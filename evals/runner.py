"""Frozen evidence -> agent -> Python checks -> blinded judge -> persisted results."""
import copy
import hashlib
import importlib.metadata
from pathlib import Path
from uuid import uuid4
from .budget import (BudgetExceeded, JUDGE_INPUT_CAP, input_bound, maximum_cost,
                     usage_cost)
from .common import ROOT, append_record, digest, dumps, save_json, utc_now
import json
from .contract import normal_email_copy, validate_email
from .judge import (JUDGE_PROMPT, RUBRIC_VERSION, SemanticReview, judge_input,
                    semantic_pass)
from .eval_schemas import BillingAnalysis, EmailCopy
from .normalization import POLICY_VERSION
from .word_limits import (ANALYST_HARD_LIMIT, EMAIL_HARD_LIMITS, EMAIL_TARGETS,
                          email_word_counts, revision_feedback)
from .scoring import score


class Harness:
    def __init__(self, config, cases, prompts, manifest, folder, *, mode,
                 backend=None, budget=None, prices=None):
        self.config, self.cases, self.prompts = config, cases, prompts
        self.folder, self.mode, self.backend, self.budget = Path(folder), mode, backend, budget
        self.prices = prices
        self.folder.mkdir(parents=True, exist_ok=False)
        self.journal = self.folder / 'attempts.jsonl'
        self.journal.touch()
        self.run_id = self.folder.name
        versions = {}
        for package in ('openai-agents', 'openai', 'pydantic'):
            try:
                versions[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                versions[package] = 'not installed'
        self.metadata = {'run_id': self.run_id, 'started_utc': utc_now(), 'mode': mode,
                         'evaluation_policy_version': POLICY_VERSION,
                         'word_limits': {'email_targets': EMAIL_TARGETS, 'email_hard_limits': EMAIL_HARD_LIMITS,
                                         'analyst_target': 120, 'analyst_hard_limit': ANALYST_HARD_LIMIT},
                         'prompt_updates': json.loads((ROOT / 'prompt_updates.json').read_text()),
                         'config': config, 'fixture_manifest': manifest,
                         'evaluation_source_hash': digest({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                                           for p in sorted(ROOT.glob('*.py'))}),
                         'dataset_hash': digest(cases), 'prompts_hash': digest(prompts),
                         'rubric_version': RUBRIC_VERSION, 'rubric_hash': digest(JUDGE_PROMPT),
                         'prices': prices, 'packages': versions,
                         'transport': {'provider': 'OpenAI', 'service_tier': 'default',
                                       'sdk_retries': 0, 'concurrency': 1, 'max_turns': 1},
                         'limitations': [
                             'Five synthetic homes; small exploratory sample.',
                             'Anomaly flags and plan costs are already calculated inputs.',
                             'Judge assessments are probabilistic, not human ground truth.',
                             'Model aliases can change; record response IDs and package versions.',
                             'Final demo HTML includes deterministic guidance; this evaluates raw agent outputs.',
                             'Offline fixtures are not live model evaluations.' if mode == 'offline'
                             else 'Each repetition uses fresh API requests; provider prompt caching may apply.'
                         ]}
        save_json(self.folder / 'run.json', {**self.metadata, 'prompts': prompts,
                                           'judge_prompt': JUDGE_PROMPT,
                                           'cases': [cases[c] for c in config['cases']],
                                           'schemas': {'analyst': BillingAnalysis.model_json_schema(),
                                                       'communications': EmailCopy.model_json_schema(),
                                                       'judge': SemanticReview.model_json_schema()}})

    def record(self, value):
        record = {'run_id': self.run_id, 'timestamp_utc': utc_now(), **value}
        append_record(self.journal, record)
        return record

    def skip(self, case, stage, scope, repetition, reason):
        return self.record({'type': 'skip', 'case_id': case['case_id'], 'stage': stage,
                            'scope': scope, 'repetition': repetition, 'reason': reason})

    async def call(self, *, case, stage, scope, repetition, payload, schema, prompt,
                   kind='agent', attempt_index=0, parent_id=None, reservation=None, upstream=None):
        role = 'judge' if kind == 'judge' else stage
        model = self.config['judge_model' if kind == 'judge' else 'agent_model']
        cap = self.config['max_output_tokens'][role]
        effort = self.config['reasoning_effort']['judge' if kind == 'judge' else 'agent']
        attempt_id = uuid4().hex
        maximum = None
        if self.mode == 'offline':
            output = copy.deepcopy(case['reference_analysis' if stage == 'analyst' else 'reference_email'])
            response = {'output': output, 'raw_output': None, 'error': None, 'usage': None,
                        'latency_seconds': None, 'request_id': None, 'response_id': None, 'source': 'fixture'}
        else:
            try:
                response = await self.backend.call(model, prompt, payload, schema, cap, effort)
            except Exception as exc:
                response = {'output': None, 'raw_output': None, 'error': {'type': type(exc).__name__},
                            'usage': None, 'latency_seconds': None, 'source': 'openai',
                            'request_id': None, 'response_id': None}
            maximum = self.budget.state['entries'][reservation]['maximum_usd']
        actual = usage_cost(self.prices['models'][model], response['usage']) if self.mode == 'live' else None
        record = {'type': 'attempt', 'kind': kind, 'attempt_id': attempt_id, 'parent_id': parent_id,
                  'case_id': case['case_id'], 'stage': stage, 'scope': scope,
                  'repetition': repetition, 'attempt_index': attempt_index, 'model': model,
                  'input': payload, 'input_hash': digest(payload), 'prompt_hash': digest(prompt),
                  'schema_hash': digest(schema.model_json_schema()), 'max_output_tokens': cap,
                  'reasoning_effort': effort, 'reservation_id': reservation,
                  'maximum_reserved_usd': maximum,
                  'estimated_cost_usd': str(actual) if actual is not None else '0' if self.mode == 'offline' else None,
                  'cost_status': 'fixture_no_charge' if self.mode == 'offline' else
                                 'usage_based_estimate' if actual is not None else 'unknown_full_reservation_retained',
                  **response}
        if kind == 'agent':
            candidate = response.get('candidate_raw')
            if candidate is None:
                candidate = response['output']
            # Incomplete/refused responses are never recovered as approved results.
            if response.get('execution_status') in ('incomplete', 'refused', 'provider_error'):
                candidate = None
            assessment = score(case, stage, candidate, upstream, response.get('execution_status'))
            record['output'] = assessment.pop('normalized_output')
            record['candidate_raw'] = candidate
            record['deterministic'] = assessment
        else:
            try:
                record['semantic_pass'] = semantic_pass(response['output'])
                record['judge_validation_error'] = None
            except (ValueError, TypeError) as exc:
                record['semantic_pass'] = None
                record['judge_validation_error'] = str(exc)[:500]
        saved = self.record(record)  # Save output before settlement, further calls, or parsing elsewhere.
        if self.mode == 'live':
            self.budget.settle(reservation, actual, sent=response.get('request_started', True))
            # Token estimates are conservative, but stop if the provider reports otherwise.
            observed = response['usage']
            bound = JUDGE_INPUT_CAP if kind == 'judge' else input_bound(prompt, payload, schema.model_json_schema())
            if observed and (observed.get('input_tokens', 0) > bound or observed.get('output_tokens', 0) > cap):
                raise BudgetExceeded('Provider token usage exceeded the reserved bound; stopped')
        return saved

    async def evaluate(self, case, stage, scope, repetition, payload, upstream=None):
        schema = BillingAnalysis if stage == 'analyst' else EmailCopy
        prompt = self.prompts[stage]
        current = payload
        attempts = 1 + (self.config['email_revisions'] if stage == 'communications' else 0)
        final = None
        for attempt_index in range(attempts):
            reservations = [None, None]
            if self.mode == 'live':
                agent_price = self.prices['models'][self.config['agent_model']]
                judge_price = self.prices['models'][self.config['judge_model']]
                amounts = [maximum_cost(agent_price, input_bound(prompt, current, schema.model_json_schema()),
                                        self.config['max_output_tokens'][stage]),
                           maximum_cost(judge_price, JUDGE_INPUT_CAP, self.config['max_output_tokens']['judge'])]
                try:
                    reservations = self.budget.reserve(self.run_id, amounts)
                except BudgetExceeded as exc:
                    self.skip(case, stage, scope, repetition, str(exc))
                    return final
            print(f"Home {case['case_id']} · {scope}/{stage} · repetition {repetition} · attempt {attempt_index + 1}", flush=True)
            agent = await self.call(case=case, stage=stage, scope=scope, repetition=repetition,
                                    payload=current, schema=schema, prompt=prompt,
                                    attempt_index=attempt_index, reservation=reservations[0], upstream=upstream)
            judge = None
            if self.mode == 'live':
                if agent['output'] is None or not agent['deterministic']['structured']:
                    self.budget.settle(reservations[1], None, sent=False)
                    self.skip(case, 'judge', scope, repetition, f"No structured candidate to judge: {agent['attempt_id']}")
                else:
                    judge_payload = judge_input(case, stage, agent['output'], upstream)
                    if input_bound(JUDGE_PROMPT, judge_payload, SemanticReview.model_json_schema()) > JUDGE_INPUT_CAP:
                        self.budget.settle(reservations[1], None, sent=False)
                        self.skip(case, 'judge', scope, repetition, f"Judge input exceeds reserved cap: {agent['attempt_id']}")
                    else:
                        judge = await self.call(case=case, stage=stage, scope=scope, repetition=repetition,
                                                payload=judge_payload, schema=SemanticReview, prompt=JUDGE_PROMPT,
                                                kind='judge', parent_id=agent['attempt_id'],
                                                attempt_index=attempt_index, reservation=reservations[1])
            final = {'agent': agent, 'judge': judge}
            if stage != 'communications' or agent['deterministic']['passed'] or agent['output'] is None:
                return final
            # Retry length-only failures. Private hard limits must never cross this boundary.
            failed = [c['name'] for c in agent['deterministic']['checks'] if not c['passed']]
            if failed == ['email_word_limits']:
                counts = email_word_counts(agent['output'], case['summary']['metrics'])
                current = {'analysis': upstream, 'previous_draft': agent['output'],
                           'word_limit_violations': revision_feedback(counts),
                           'resolved_word_counts': counts,
                           'revision_instruction': 'Shorten the captions, preserving all validated facts and uncertainty. '
                                                   'Aim for subject six words, intro five, explanation sixteen, action ten, '
                                                   'body thirty-five. Return the same EmailCopy schema.'}
            else:
                return final
        return final

    @staticmethod
    def outcome(results):
        if any(r is not None and (not r['agent']['deterministic']['passed'] or
                                  (r['judge'] is not None and r['judge']['semantic_pass'] is False)) for r in results):
            return 'failed'
        if any(r is None for r in results):
            return 'incomplete'
        if any(r['judge'] is None or r['judge']['semantic_pass'] is None for r in results):
            return 'unscored'
        return 'passed'

    async def run(self):
        for repetition in range(1, self.config['repetitions'] + 1):
            for case_id in self.config['cases']:
                case = self.cases[case_id]
                analyst = await self.evaluate(case, 'analyst', 'analyst', repetition, case['summary'])
                # Agent 2 sees the same frozen, approved analysis in isolated tests.
                if case['summary']['anomalies']['is_anomaly']:
                    await self.evaluate(case, 'communications', 'isolated', repetition,
                                        case['reference_analysis'], case['reference_analysis'])
                else:
                    self.skip(case, 'communications', 'isolated', repetition,
                              'Normal case: production routing uses a deterministic snapshot')
                communications = None
                normal = None
                if analyst is not None and analyst['agent']['deterministic']['passed']:
                    analysis = analyst['agent']['output']
                    if analysis['is_anomaly']:
                        communications = await self.evaluate(case, 'communications', 'workflow', repetition,
                                                            analysis, analysis)
                    else:
                        normal = normal_email_copy()
                        validate_email(normal, {'metrics': case['summary']['metrics']}, analysis)
                elif analyst is not None:
                    self.skip(case, 'communications', 'workflow', repetition, 'Analyst validation failed or review hold requested')
                results = [analyst] + ([communications] if case['summary']['anomalies']['is_anomaly'] else [])
                self.record({'type': 'workflow', 'case_id': case_id, 'repetition': repetition,
                             'status': self.outcome(results),
                             'analyst_attempt_id': analyst['agent']['attempt_id'] if analyst else None,
                             'communications_attempt_id': communications['agent']['attempt_id'] if communications else None,
                             'communications_source': 'deterministic_normal' if normal else 'model' if communications else 'withheld_or_incomplete',
                             'normal_email': normal})
