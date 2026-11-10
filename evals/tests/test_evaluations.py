"""Meaningful offline checks. HTTP mocks never leave this process."""
import asyncio
import copy
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from evals.budget import Budget, BudgetExceeded, input_bound, usage_cost
from evals.common import dumps, load_records, save_json
from evals.judge import JUDGE_PROMPT, SemanticReview, judge_input, semantic_pass
from evals.reporting import comparison, rebuild_reports, summarize
from evals.runner import Harness
from evals.eval_schemas import BillingAnalysis, EmailCopy
from evals.scoring import score
from evals.settings import load_config, load_fixtures, load_prices
from evals.normalization import money_value
from evals.audit import audit_run
from evals.word_limits import EMAIL_TARGETS, EMAIL_HARD_LIMITS, email_word_counts, hard_violations


def review(verdict='pass'):
    return {key: {'verdict': verdict, 'reason': 'Fits the evidence and the stage.', 'quote': ''}
            for key in ('groundedness', 'uncertainty', 'actionability', 'clarity')}


class FakeBackend:
    def __init__(self, cases, *, wrong_review=False, judge_failure=False, length_repair=False):
        self.cases, self.calls = cases, []
        self.wrong_review, self.judge_failure, self.length_repair = wrong_review, judge_failure, length_repair

    async def call(self, model, prompt, payload, schema, cap, effort):
        self.calls.append((model, payload, schema.__name__))
        if schema is SemanticReview:
            output = review()
            if self.judge_failure:
                output['groundedness']['verdict'] = 'fail'
        elif schema is BillingAnalysis:
            case = self.cases[payload['home']['id']]
            output = copy.deepcopy(case['reference_analysis'])
            if self.wrong_review:
                output['needs_review'] = True
        else:
            analysis = payload.get('analysis', payload)
            case = next(c for c in self.cases.values()
                        if c['reference_analysis']['anomaly_type'] == analysis['anomaly_type'])
            output = copy.deepcopy(case['reference_email'])
            if self.length_repair and 'previous_draft' not in payload:
                output['explanation'] = 'Usage ' * 50
        return {'output': output, 'raw_output': [{'text': dumps(output)}], 'error': None,
                'usage': {'input_tokens': 100, 'output_tokens': 50,
                          'input_tokens_details': {'cached_tokens': 20},
                          'output_tokens_details': {'reasoning_tokens': 10}},
                'latency_seconds': 0.01, 'request_id': 'mock-request',
                'response_id': 'mock-response', 'source': 'openai'}


class FixtureAndScoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases, cls.prompts, cls.manifest = load_fixtures()

    def test_every_reference_passes_contract_and_numeric_checks(self):
        for case in self.cases.values():
            self.assertTrue(score(case, 'analyst', case['reference_analysis'])['passed'])
            self.assertTrue(score(case, 'communications', case['reference_email'], case['reference_analysis'])['passed'])

    def test_wrong_cost_and_wrong_anomaly_fail_independently(self):
        case = self.cases['D']
        output = copy.deepcopy(case['reference_analysis'])
        output['recommended_plan']['savings'] = '999.00'
        output['anomaly_type'] = 'none'
        result = score(case, 'analyst', output)
        failed = {c['name'] for c in result['checks'] if not c['passed']}
        self.assertIn('optimal_plan_and_exact_costs', failed)
        self.assertIn('anomaly_flags', failed)

    def test_invented_metric_and_overlong_caption_fail(self):
        case = self.cases['C']
        output = copy.deepcopy(case['reference_analysis'])
        output['mathematical_justification'][0]['calculated_value'] = '$999.00'
        self.assertFalse(score(case, 'analyst', output)['passed'])
        email = copy.deepcopy(case['reference_email'])
        email['explanation'] = 'Too long ' * 30
        self.assertFalse(score(case, 'communications', email, case['reference_analysis'])['passed'])

    def test_currency_and_whitespace_are_normalized_with_an_audit_trail(self):
        case = self.cases['A']
        output = copy.deepcopy(case['reference_analysis'])
        output['recommended_plan']['estimated_bill'] = '$85.94'
        output['recommended_plan']['savings'] = '$5.37'
        changed = next(r for r in output['mathematical_justification'] if r['metric_id'] == 'bill_change')
        changed['calculated_value'] = '+ $10.88'
        result = score(case, 'analyst', output)
        self.assertTrue(result['passed'])
        self.assertEqual(len(result['normalizations']), 3)
        self.assertEqual(output['recommended_plan']['estimated_bill'], '$85.94')  # Original is preserved.
        self.assertEqual(result['normalized_output']['recommended_plan']['estimated_bill'], '85.94')

    def test_normalization_never_changes_wrong_amounts_signs_or_units(self):
        case = self.cases['D']
        for wrong in ('$191.95', '-$191.94', '€191.94', '191.94000000000000000000000000001'):
            output = copy.deepcopy(case['reference_analysis'])
            output['recommended_plan']['estimated_bill'] = wrong
            self.assertFalse(score(case, 'analyst', output)['passed'], wrong)
        for wrong in ('-47.5%', '+47.5', '+47.5 kWh'):
            output = copy.deepcopy(case['reference_analysis'])
            next(r for r in output['mathematical_justification'] if r['metric_id'] == 'daily_change')['calculated_value'] = wrong
            failed = [c for c in score(case, 'analyst', output)['checks'] if not c['passed']]
            self.assertEqual([c['name'] for c in failed], ['evidence_values'])

    def test_money_rejects_invalid_types_and_nonfinite_values(self):
        for value in (True, None, [], {}, 'NaN', float('inf')):
            with self.assertRaises(ValueError):
                money_value(value)

    def test_no_output_is_execution_failure_without_a_fake_schema_failure(self):
        result = score(self.cases['A'], 'analyst', None, execution_status='incomplete')
        self.assertFalse(result['passed'])
        self.assertFalse(result['assessable'])
        self.assertEqual(result['checks'], [])
        self.assertEqual(result['execution_status'], 'incomplete')

    def test_email_hard_boundaries_include_eleven_word_intro(self):
        self.assertEqual(EMAIL_HARD_LIMITS, {'subject': 9, 'intro': 11, 'explanation': 28, 'action': 16, 'body': 54})
        self.assertEqual(EMAIL_TARGETS['intro'], 7)
        case = self.cases['C']
        for field in ('subject', 'intro', 'explanation', 'action'):
            email = {'subject': 'Words', 'intro': 'Words', 'explanation': 'Words', 'actions': ['Words']}
            limit = EMAIL_HARD_LIMITS[field]
            if field == 'action':
                email['actions'][0] = 'word ' * limit
            else:
                email[field] = 'word ' * limit
            self.assertTrue(score(case, 'communications', email, case['reference_analysis'])['passed'], field)
            if field == 'action':
                email['actions'][0] += 'word'
            else:
                email[field] += 'word'
            self.assertFalse(score(case, 'communications', email, case['reference_analysis'])['passed'], field)
        self.assertNotIn('body', hard_violations({'body': 54}))
        self.assertIn('body', hard_violations({'body': 55}))

    def test_analyst_hard_boundary_is_one_hundred_forty_four_words(self):
        case = self.cases['A']
        analysis = copy.deepcopy(case['reference_analysis'])
        analysis['explanation'] = '{{bill_total}} {{bill_change}} {{daily_change}} {{feb_days}} {{march_days}} ' + 'word ' * 139
        self.assertTrue(score(case, 'analyst', analysis)['passed'])
        analysis['explanation'] += 'word'
        self.assertFalse(score(case, 'analyst', analysis)['passed'])

    def test_agent_prompts_keep_original_word_targets(self):
        self.assertIn('at most twenty-four words', self.prompts['communications'])
        self.assertIn('one hundred and twenty words', self.prompts['analyst'])
        for prompt in self.prompts.values():
            self.assertNotIn('20%', prompt)
            self.assertNotIn('twenty-eight words', prompt)
            self.assertNotIn('hard_limits', prompt)

    def test_prompt_refinements_include_feedback_without_weakening_judge(self):
        self.assertIn('without currency symbols', self.prompts['analyst'])
        self.assertIn('February had {{feb_days}} days and March had {{march_days}} days', self.prompts['analyst'])
        self.assertIn('do not put peer_comparison_status there', self.prompts['analyst'])
        self.assertIn('Include {{bill_change}} in either the headline or the explanation', self.prompts['analyst'])
        self.assertIn('weather evidence is insufficient to determine its role', self.prompts['analyst'])
        self.assertIn('not the percentage of total use that is unexplained', self.prompts['analyst'])
        self.assertIn('do not interpret or attribute that percentage in prose', self.prompts['analyst'])
        self.assertIn('steady electricity use', self.prompts['communications'])
        self.assertIn('clarity:', JUDGE_PROMPT)

    def test_analyst_rejects_unknown_evidence_and_missing_explanation_comparison(self):
        case = self.cases['E']
        output = copy.deepcopy(case['reference_analysis'])
        output['mathematical_justification'].append({'metric_id': 'peer_comparison_status', 'calculated_value': 'typical'})
        self.assertIn('evidence_values', [c['name'] for c in score(case, 'analyst', output)['checks'] if not c['passed']])
        output = copy.deepcopy(case['reference_analysis'])
        output['explanation'] = output['explanation'].replace('{{bill_change}}', 'higher')
        output['headline'] = output['headline'].replace('{{bill_change}}', 'higher')
        self.assertIn('billing_comparisons', [c['name'] for c in score(case, 'analyst', output)['checks'] if not c['passed']])

    def test_bill_change_can_appear_in_headline_but_other_comparisons_remain_required(self):
        case = self.cases['A']
        output = copy.deepcopy(case['reference_analysis'])
        output['explanation'] = output['explanation'].replace('{{bill_change}}', 'higher')
        output['headline'] = 'Bill change: {{bill_change}}'
        self.assertTrue(score(case, 'analyst', output)['passed'])
        output['explanation'] = output['explanation'].replace('{{feb_days}}', 'fewer')
        self.assertIn('billing_comparisons', [c['name'] for c in score(case, 'analyst', output)['checks'] if not c['passed']])

    def test_judge_is_blinded_and_has_resolved_placeholders(self):
        case = self.cases['D']
        payload = judge_input(case, 'analyst', case['reference_analysis'])
        self.assertNotIn('agent_model', dumps(payload))
        self.assertNotIn('{{bill_total}}', dumps(payload['candidate']))
        self.assertIn('$208.60', dumps(payload['candidate']))
        self.assertNotIn('reference_analysis', payload)
        self.assertLess(input_bound(JUDGE_PROMPT, payload, SemanticReview.model_json_schema()), 20_000)

    def test_judge_requires_meaningful_assessment(self):
        result = review()
        self.assertTrue(semantic_pass(result))
        result['groundedness']['verdict'] = 'not_applicable'
        with self.assertRaises(ValueError):
            semantic_pass(result)
        result['groundedness']['verdict'] = 'fail'
        self.assertFalse(semantic_pass(result))

    def test_configuration_rejects_foreign_provider_and_zero_repeats(self):
        config = load_config()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'config.json'
            config['agent_model'] = 'other-provider/model'
            save_json(path, config)
            with self.assertRaises(ValueError):
                load_config(path)
            config['agent_model'] = 'gpt-6-luna'
            config['repetitions'] = 0
            save_json(path, config)
            with self.assertRaises(ValueError):
                load_config(path)


class BudgetTests(unittest.TestCase):
    def test_authorized_limits_preserve_the_existing_ledger(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            entries = {'old': {'run_id': 'old-run', 'maximum_usd': '.2',
                                'charged_or_reserved_usd': '.2', 'status': 'unknown'}}
            save_json(folder / 'budget.json', {'limit_usd': '1.00', 'request_allowance_usd': '.90',
                                              'entries': entries})
            with Budget(folder) as budget:
                self.assertEqual(budget.state['limit_usd'], '4.00')
                self.assertEqual(budget.state['request_allowance_usd'], '3.90')
                self.assertEqual(budget.state['run_limit_usd'], '0.35')
                self.assertEqual(budget.state['entries'], entries)
                self.assertEqual(budget.committed, Decimal('.2'))
                self.assertEqual(len(budget.state['limit_changes']), 1)
            with Budget(folder) as budget:
                self.assertEqual(len(budget.state['limit_changes']), 1)

    def test_budget_persists_across_runs_and_unknown_requests(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            with Budget(folder) as budget:
                a, b = budget.reserve('first', [Decimal('.2'), Decimal('.1')])
                budget.settle(a, Decimal('.03'))
                budget.settle(b, None)  # Network result unknown; retain the whole allocation.
                budget.reserve('interrupted', [Decimal('.1')])
            with Budget(folder) as budget:
                self.assertEqual(budget.committed, Decimal('.23'))
                self.assertEqual(budget.snapshot()['unknown_or_interrupted_requests'], 2)
                with self.assertRaises(BudgetExceeded):
                    budget.reserve('second', [Decimal('1.7')])

    def test_unsent_judge_releases_reservation(self):
        with tempfile.TemporaryDirectory() as temporary:
            with Budget(Path(temporary)) as budget:
                key, = budget.reserve('run', [Decimal('.1')])
                budget.settle(key, None, sent=False)
                self.assertEqual(budget.committed, 0)

    def test_per_run_limit_is_separate_from_shared_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            with Budget(Path(temporary)) as budget:
                key, = budget.reserve('first', [Decimal('.30')])
                budget.settle(key, Decimal('.25'))
                with self.assertRaisesRegex(BudgetExceeded, 'per-evaluation'):
                    budget.reserve('first', [Decimal('.11')])
                budget.reserve('second', [Decimal('.11')])
                self.assertEqual(budget.snapshot('first')['run_committed_usd'], '0.25')

    def test_observed_cost_overrun_stops_without_losing_accounting(self):
        with tempfile.TemporaryDirectory() as temporary:
            with Budget(Path(temporary)) as budget:
                key, = budget.reserve('run', [Decimal('.1')])
                with self.assertRaises(BudgetExceeded):
                    budget.settle(key, Decimal('.11'))
                self.assertEqual(budget.committed, Decimal('.11'))

    def test_lock_prevents_parallel_paid_runs(self):
        with tempfile.TemporaryDirectory() as temporary:
            with Budget(Path(temporary)):
                with self.assertRaisesRegex(ValueError, 'lock'):
                    with Budget(Path(temporary)):
                        pass

    def test_corrupt_or_negative_ledger_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / 'budget.json').write_text('{')
            with self.assertRaises(ValueError):
                with Budget(folder):
                    pass
            save_json(folder / 'budget.json', {'limit_usd': '1.00', 'entries': {
                'bad': {'charged_or_reserved_usd': '-1', 'maximum_usd': '.1', 'status': 'estimated'}}})
            with self.assertRaises(ValueError):
                with Budget(folder):
                    pass

    def test_cost_includes_cached_input_and_reasoning_without_double_count(self):
        prices = {'input': '2', 'cached_input': '.2', 'output': '10', 'cache_write': '5'}
        usage = {'input_tokens': 1000, 'output_tokens': 100,
                 'input_tokens_details': {'cached_tokens': 200, 'cache_write_tokens': 100},
                 'output_tokens_details': {'reasoning_tokens': 70}}
        self.assertEqual(usage_cost(prices, usage), Decimal('.00294'))
        self.assertIsNone(usage_cost(prices, None))


class PipelineTests(unittest.TestCase):
    def run_harness(self, temporary, *, config=None, backend_options=None, pre_spend=None):
        cases, prompts, manifest = load_fixtures()
        config = config or load_config()
        config.update(agent_model='gpt-6-luna', judge_model='gpt-6-luna')
        backend = FakeBackend(cases, **(backend_options or {}))
        folder = Path(temporary)
        with Budget(folder) as budget:
            if pre_spend:
                remaining = pre_spend
                index = 0
                while remaining:
                    amount = min(remaining, Decimal('.30'))
                    key, = budget.reserve(f'prior-{index}', [amount])
                    budget.settle(key, amount)
                    remaining -= amount
                    index += 1
            harness = Harness(config, cases, prompts, manifest, folder / 'run', mode='live',
                              backend=backend, budget=budget, prices=load_prices(['gpt-6-luna']))
            asyncio.run(harness.run())
            summary = summarize(harness.folder, budget.snapshot())
            return summary, load_records(harness.journal), backend.calls

    def test_both_agents_and_workflow_share_analyst_calls(self):
        with tempfile.TemporaryDirectory() as temporary:
            summary, rows, calls = self.run_harness(temporary)
            self.assertEqual(len(calls), 36)
            self.assertEqual(summary['workflows']['passed'], 10)
            self.assertEqual(summary['status'], 'complete')
            analysts = [r for r in rows if r['type'] == 'attempt' and r['kind'] == 'agent' and r['stage'] == 'analyst']
            workflows = summary['workflows']['results']
            self.assertEqual({r['attempt_id'] for r in analysts}, {r['analyst_attempt_id'] for r in workflows})
            self.assertEqual(sum(r['communications_source'] == 'deterministic_normal' for r in workflows), 6)
            self.assertTrue(all(r['decision_pairwise_agreement'] == 1 for r in summary['repeatability']))
            self.assertTrue((Path(temporary) / 'run/report.html').exists())

    def test_judge_pass_cannot_override_wrong_review_hold(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = load_config()
            config.update(cases=['C'], repetitions=1)
            summary, rows, calls = self.run_harness(temporary, config=config, backend_options={'wrong_review': True})
            self.assertEqual(summary['workflows']['passed'], 0)
            self.assertEqual(summary['workflows']['statuses'], {'failed': 1})
            self.assertFalse(any(r.get('scope') == 'workflow' and r['type'] == 'attempt' for r in rows))
            self.assertTrue(any(r.get('scope') == 'isolated' and r['type'] == 'attempt' for r in rows))

    def test_semantic_failure_is_separate_from_python_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = load_config()
            config.update(cases=['A'], repetitions=1)
            summary, _, _ = self.run_harness(temporary, config=config, backend_options={'judge_failure': True})
            stage = summary['stages']['analyst/analyst']
            self.assertEqual(stage['first_attempt_deterministic_pass_rate'], 1)
            self.assertEqual(stage['semantic_pass_rate'], 0)
            self.assertEqual(summary['workflows']['passed'], 0)
            self.assertIsNone(summary['repeatability'][0]['decision_pairwise_agreement'])

    def test_no_calls_when_pair_does_not_fit_shared_budget(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = load_config()
            config.update(cases=['A'], repetitions=1)
            summary, _, calls = self.run_harness(temporary, config=config, pre_spend=Decimal('3.899'))
            self.assertEqual(calls, [])
            self.assertEqual(summary['status'], 'partial')
            self.assertEqual(summary['workflows']['passed'], 0)

    def test_length_revisions_keep_first_pass_failure_and_extra_cost(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = load_config()
            config.update(cases=['C'], repetitions=1, email_revisions=1)
            summary, rows, calls = self.run_harness(temporary, config=config, backend_options={'length_repair': True})
            self.assertEqual(len(calls), 10)
            for scope in ('isolated', 'workflow'):
                stage = summary['stages'][f'{scope}/communications']
                self.assertEqual(stage['first_attempt_deterministic_pass_rate'], 0)
                self.assertEqual(stage['final_deterministic_pass_rate'], 1)
            self.assertEqual(summary['workflows']['passed'], 1)
            repairs = [payload for _, payload, _ in calls if 'previous_draft' in payload]
            self.assertEqual(len(repairs), 2)
            for payload in repairs:
                for key, violation in payload['word_limit_violations'].items():
                    self.assertEqual(violation['limit'], EMAIL_TARGETS[key])
                self.assertNotIn('hard_limits', dumps(payload))
                self.assertNotIn('20%', dumps(payload))

    def test_offline_records_never_claim_a_live_evaluation(self):
        cases, prompts, manifest = load_fixtures()
        with tempfile.TemporaryDirectory() as temporary:
            harness = Harness(load_config(), cases, prompts, manifest, Path(temporary) / 'offline', mode='offline')
            asyncio.run(harness.run())
            summary = summarize(harness.folder)
            self.assertEqual(summary['cost']['api_calls'], 0)
            self.assertEqual(summary['cost']['judge_calls'], 0)
            self.assertEqual(summary['status'], 'offline_fixture_preview')
            self.assertEqual(summary['stages']['analyst/analyst']['semantic_assessments'], 0)
            self.assertIn('no model or judge was called', (harness.folder / 'report.html').read_text())
            comparison(Path(temporary))
            self.assertTrue((Path(temporary) / 'comparison.json').exists())

    def test_report_recovers_interrupted_journal_without_model_calls(self):
        cases, prompts, manifest = load_fixtures()
        config = load_config()
        config.update(cases=['A'], repetitions=1)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            harness = Harness(config, cases, prompts, manifest, root / 'interrupted', mode='offline')
            asyncio.run(harness.run())
            with harness.journal.open('a') as stream:
                stream.write('{incomplete last line')
            rebuild_reports(root)
            summary = json.loads((harness.folder / 'summary.json').read_text())
            self.assertTrue(summary['interrupted'])
            self.assertEqual(summary['cost']['api_calls'], 0)
            self.assertEqual(summary['workflows']['recorded'], 1)

    def test_recheck_is_separate_from_original_scores_and_does_not_call_models(self):
        cases, prompts, manifest = load_fixtures()
        config = load_config()
        config.update(cases=['A'], repetitions=1)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            harness = Harness(config, cases, prompts, manifest, root / 'saved-run', mode='offline')
            asyncio.run(harness.run())
            summarize(harness.folder)
            original = (harness.folder / 'summary.json').read_bytes()
            path = audit_run('saved-run', root)
            self.assertTrue(path.exists())
            self.assertEqual((harness.folder / 'summary.json').read_bytes(), original)
            result = json.loads(path.with_suffix('.json').read_text())
            self.assertEqual(result['new_api_calls'], 0)
            self.assertEqual(result['new_passes'], 1)
            rebuild_reports(root)
            self.assertIn('accepted under checks-v5', (root / 'comparison.html').read_text())


class SDKTransportTests(unittest.IsolatedAsyncioTestCase):
    async def backend(self, handler):
        import httpx2 as httpx
        from openai import AsyncOpenAI
        from evals.backend import OpenAIBackend
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'fake-local-test-key',
                                       'OPENAI_BASE_URL': 'https://other-provider.invalid'}):
            backend = OpenAIBackend(timeout=1)
        self.assertEqual(str(backend.client.base_url), 'https://api.openai.com/v1/')
        await backend.close()
        backend.client = AsyncOpenAI(api_key='fake-local-test-key', max_retries=0,
                                     http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        self.addAsyncCleanup(backend.close)
        return backend

    async def test_real_sdk_preserves_usage_even_when_structured_parsing_fails(self):
        import httpx2 as httpx
        calls = []

        def handler(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200, headers={'x-request-id': 'mock-request'}, json={
                'id': 'resp_mock', 'object': 'response', 'created_at': 1, 'status': 'completed',
                'model': 'gpt-6-luna', 'output': [{'type': 'message', 'id': 'msg_mock',
                    'role': 'assistant', 'status': 'completed',
                    'content': [{'type': 'output_text', 'text': 'invalid JSON', 'annotations': []}]}],
                'usage': {'input_tokens': 100, 'output_tokens': 20, 'total_tokens': 120,
                          'input_tokens_details': {'cached_tokens': 0},
                          'output_tokens_details': {'reasoning_tokens': 5}}})
        backend = await self.backend(handler)
        result = await backend.call('gpt-6-luna', 'Test prompt', {'data': 'test'}, SemanticReview, 768)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['max_output_tokens'], 768)
        self.assertEqual(calls[0]['service_tier'], 'default')
        self.assertFalse(calls[0]['store'])
        self.assertIsNotNone(result['error'])
        self.assertEqual(result['usage']['output_tokens'], 20)
        self.assertEqual(result['raw_output'][0]['content'][0]['text'], 'invalid JSON')

    async def test_http_failure_is_not_retried_and_error_text_is_not_saved(self):
        import httpx2 as httpx
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(503, json={'error': {'message': 'secret-text-must-not-be-saved',
                                                      'type': 'server_error', 'code': 'server_error'}})
        backend = await self.backend(handler)
        result = await backend.call('gpt-6-luna', 'Test prompt', {}, SemanticReview, 768)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['error']['status_code'], 503)
        self.assertNotIn('secret-text', dumps(result))
        self.assertIsNone(result['usage'])

    async def test_real_sdk_returns_valid_structured_judgment(self):
        import httpx2 as httpx

        def handler(request):
            return httpx.Response(200, headers={'x-request-id': 'mock-success'}, json={
                'id': 'resp_success', 'object': 'response', 'created_at': 1, 'status': 'completed',
                'model': 'gpt-6-luna', 'output': [{'type': 'message', 'id': 'msg_success',
                    'role': 'assistant', 'status': 'completed',
                    'content': [{'type': 'output_text', 'text': dumps(review()), 'annotations': []}]}],
                'usage': {'input_tokens': 100, 'output_tokens': 50, 'total_tokens': 150,
                          'input_tokens_details': {'cached_tokens': 20},
                          'output_tokens_details': {'reasoning_tokens': 0}}})
        backend = await self.backend(handler)
        result = await backend.call('gpt-6-luna', 'Test prompt', {}, SemanticReview, 768)
        self.assertIsNone(result['error'])
        self.assertEqual(result['output'], review())
        self.assertEqual(result['request_id'], 'mock-success')
        self.assertEqual(result['usage']['input_tokens_details']['cached_tokens'], 20)

    async def test_incomplete_response_retains_usage_and_the_real_failure_reason(self):
        import httpx2 as httpx

        def handler(request):
            return httpx.Response(200, headers={'x-request-id': 'mock-incomplete'}, json={
                'id': 'resp_incomplete', 'object': 'response', 'created_at': 1, 'status': 'incomplete',
                'model': 'gpt-6-luna', 'incomplete_details': {'reason': 'max_output_tokens'},
                'output': [], 'usage': {'input_tokens': 100, 'output_tokens': 512, 'total_tokens': 612,
                                      'input_tokens_details': {'cached_tokens': 0},
                                      'output_tokens_details': {'reasoning_tokens': 512}}})
        backend = await self.backend(handler)
        result = await backend.call('gpt-6-luna', 'Test prompt', {}, SemanticReview, 512)
        self.assertEqual(result['execution_status'], 'incomplete')
        self.assertEqual(result['incomplete_reason'], 'max_output_tokens')
        self.assertEqual(result['usage']['output_tokens'], 512)
        self.assertEqual(result['request_id'], 'mock-incomplete')
        self.assertIsNotNone(usage_cost({'input': '.1', 'cached_input': '.01', 'output': '.5', 'cache_write': '.25'}, result['usage']))

    async def test_refusal_is_classified_without_a_schema_claim(self):
        import httpx2 as httpx

        def handler(request):
            return httpx.Response(200, json={
                'id': 'resp_refused', 'object': 'response', 'created_at': 1, 'status': 'completed',
                'model': 'gpt-6-luna', 'output': [{'type': 'message', 'id': 'msg_refused',
                    'role': 'assistant', 'status': 'completed',
                    'content': [{'type': 'refusal', 'refusal': 'Cannot provide a draft.'}]}],
                'usage': {'input_tokens': 100, 'output_tokens': 10, 'total_tokens': 110}})
        backend = await self.backend(handler)
        result = await backend.call('gpt-6-luna', 'Test prompt', {}, SemanticReview, 512)
        self.assertEqual(result['execution_status'], 'refused')
        self.assertEqual(result['usage']['output_tokens'], 10)


if __name__ == '__main__':
    unittest.main()
