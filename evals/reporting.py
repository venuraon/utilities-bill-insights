"""Offline summaries and an HTML comparison report built entirely from saved files."""
from collections import Counter, defaultdict
from decimal import Decimal
from html import escape
from statistics import median
from .common import RESULTS, digest, dumps, load_records, save_json, utc_now
from .scoring import decision_signature
from .budget import TOTAL_LIMIT

STAGE_LABELS = {'analyst/analyst': 'Bill analyst · alone',
                'isolated/communications': 'Insights/email agent · alone (fixed analysis)',
                'workflow/communications': 'Insights/email agent · workflow (live analysis)'}


def is_structured(record):
    result = record['deterministic']
    checks = result['checks']
    return result.get('structured', bool(checks and checks[0]['passed']))


def failure_detail(record):
    result = record['deterministic']
    if result['passed']:
        return None
    error = record.get('error') or {}
    category = record.get('execution_status') or result.get('execution_status')
    if record.get('output') is None and not category:
        category = 'sdk_error' if not record.get('raw_output') else 'schema_error'
    if category == 'incomplete':
        reason = 'Response incomplete: ' + str(record.get('incomplete_reason') or 'reason unavailable')
    elif category in ('sdk_error', 'no_usable_output'):
        reason = f"{error.get('type', 'No usable output')}; provider response details were not retained in this older run."
    elif category in ('api_error', 'timeout', 'refused', 'provider_error'):
        reason = f"{category}: {error.get('type', '')}"
    else:
        failed = [c for c in result['checks'] if not c['passed']]
        if category == 'schema_error' and record.get('raw_output') and 'policy_version' not in result:
            from .normalization import visible_json
            from .schemas import BillingAnalysis, EmailCopy
            value = visible_json(record['raw_output'])
            try:
                (BillingAnalysis if record['stage'] == 'analyst' else EmailCopy).model_validate(value)
            except Exception as exc:
                if hasattr(exc, 'errors'):
                    failed = [{'reason': '.'.join(map(str, e['loc'])) + ': ' + e['msg']}
                              for e in exc.errors(include_url=False, include_input=False, include_context=False)]
        reason = '; '.join(dict.fromkeys(c['reason'] for c in failed if c.get('reason'))) or 'Schema validation failed'
        category = 'schema_error' if category == 'schema_error' else ', '.join(sorted(
            {c.get('category', 'legacy_contract') for c in result['checks'] if not c['passed']}))
    return {'case_id': record['case_id'], 'repetition': record['repetition'],
            'stage': record['stage'], 'scope': record['scope'], 'category': category,
            'reason': reason, 'attempt_id': record['attempt_id']}


def ratio(numerator, denominator):
    return round(numerator / denominator, 4) if denominator else None


def summarize(folder, budget=None, interrupted=False):
    import json
    metadata = json.loads((folder / 'run.json').read_text())
    rows = load_records(folder / 'attempts.jsonl')
    agents = [r for r in rows if r['type'] == 'attempt' and r['kind'] == 'agent']
    judges = [r for r in rows if r['type'] == 'attempt' and r['kind'] == 'judge']
    judged = {r['parent_id']: r for r in judges}
    workflows = [r for r in rows if r['type'] == 'workflow']
    stages = {}
    for stage, scope in (('analyst', 'analyst'), ('communications', 'isolated'), ('communications', 'workflow')):
        attempts = [r for r in agents if r['stage'] == stage and r['scope'] == scope]
        first = [r for r in attempts if r['attempt_index'] == 0]
        successful = [r for r in attempts if r['deterministic']['passed']]
        semantic = [judged[r['attempt_id']]['semantic_pass'] for r in attempts
                    if r['attempt_id'] in judged and judged[r['attempt_id']]['semantic_pass'] is not None]
        final = {}
        for attempt in attempts:
            final[attempt['case_id'], attempt['repetition']] = attempt
        latencies = [r['latency_seconds'] for r in attempts if r['latency_seconds'] is not None]
        stages[f'{scope}/{stage}'] = {
            'attempts': len(attempts), 'first_attempts': len(first),
            'first_attempt_deterministic_pass_rate': ratio(sum(r['deterministic']['passed'] for r in first), len(first)),
            'final_deterministic_pass_rate': ratio(sum(r['deterministic']['passed'] for r in final.values()), len(final)),
            'semantic_assessments': len(semantic), 'semantic_pass_rate': ratio(sum(semantic), len(semantic)),
            'unscored_attempts': len(attempts) - len(semantic),
            'api_or_schema_errors': sum(r['error'] is not None for r in attempts),
            'median_latency_seconds': median(latencies) if latencies else None,
            'deterministically_valid_attempts': len(successful),
            'failed_checks': dict(Counter(c['name'] for r in attempts for c in r['deterministic']['checks'] if not c['passed']))}
        stages[f'{scope}/{stage}']['execution_failures'] = dict(Counter(
            r.get('execution_status') or r['deterministic'].get('execution_status', 'legacy_sdk_error')
            for r in attempts if not r['deterministic']['checks']))
        stages[f'{scope}/{stage}']['normalizations'] = sum(len(r['deterministic'].get('normalizations', [])) for r in attempts)
    repeatability = []
    by_case = defaultdict(list)
    for attempt in agents:
        if attempt['stage'] == 'analyst' and attempt['attempt_index'] == 0:
            by_case[attempt['case_id']].append(attempt)
    for case in metadata['config']['cases']:
        group = by_case[case]
        structured = [r for r in group if is_structured(r)]
        counts = Counter(dumps(decision_signature('analyst', r['output'])) for r in structured)
        pairs = len(structured) * (len(structured) - 1) // 2
        agreeing_pairs = sum(n * (n - 1) // 2 for n in counts.values())
        repeatability.append({'case_id': case, 'requested_repetitions': metadata['config']['repetitions'],
                              'observed_attempts': len(group), 'structured_responses': len(structured),
                              'decision_pairwise_agreement': ratio(agreeing_pairs, pairs),
                              'deterministic_pass_rate': ratio(sum(r['deterministic']['passed'] for r in group), len(group)),
                              'note': 'Agreement is not correctness; fewer than two structured responses cannot establish repeatability.'})
    repeated_checks = []
    for stage, scope in (('communications', 'isolated'), ('communications', 'workflow')):
        for case in metadata['config']['cases']:
            group = [r for r in agents if r['stage'] == stage and r['scope'] == scope
                     and r['case_id'] == case and r['attempt_index'] == 0]
            if not group:
                continue
            assessed = [judged[r['attempt_id']]['semantic_pass'] for r in group
                        if r['attempt_id'] in judged and judged[r['attempt_id']]['semantic_pass'] is not None]
            repeated_checks.append({'case_id': case, 'scope': scope, 'observed_attempts': len(group),
                                    'deterministic_pass_rate': ratio(sum(r['deterministic']['passed'] for r in group), len(group)),
                                    'semantic_assessments': len(assessed), 'semantic_pass_rate': ratio(sum(assessed), len(assessed)),
                                    'note': 'Caption wording may vary; factual compliance and semantic pass rates are compared.'})
    calls = agents + judges
    live = [r for r in calls if r['source'] == 'openai']
    def cost(group):
        return str(sum((Decimal(r['estimated_cost_usd']) for r in group if r['estimated_cost_usd'] is not None), Decimal(0)))
    total_cost = Decimal(cost(live))
    passed = sum(r['status'] == 'passed' for r in workflows)
    expected_workflows = len(metadata['config']['cases']) * metadata['config']['repetitions']
    skip_rows = [r for r in rows if r['type'] == 'skip']
    incomplete = interrupted or len(workflows) != expected_workflows or any(
        r['status'] in ('unscored', 'incomplete') for r in workflows) or any(
            'budget' in r['reason'].lower() or 'reserved cap' in r['reason'] for r in skip_rows)
    summary = {'schema_version': 'evaluation-v2' if metadata.get('evaluation_policy_version') else 'evaluation-v1', 'run_id': metadata['run_id'],
               'evaluation_policy_version': metadata.get('evaluation_policy_version', 'checks-v1'),
               'mode': metadata['mode'], 'started_utc': metadata['started_utc'], 'updated_utc': utc_now(),
               'agent_model': metadata['config']['agent_model'], 'judge_model': metadata['config']['judge_model'],
               'comparison_group': {'dataset_hash': metadata['dataset_hash'], 'prompts_hash': metadata['prompts_hash'],
                                    'contract_hash': digest(metadata['fixture_manifest']),
                                    'evaluation_source_hash': metadata.get('evaluation_source_hash'),
                                    'rubric_hash': metadata['rubric_hash'], 'judge_model': metadata['config']['judge_model'],
                                    'max_output_tokens': metadata['config']['max_output_tokens'],
                                    'reasoning_effort': metadata['config']['reasoning_effort'],
                                    'cases': metadata['config']['cases'], 'repetitions': metadata['config']['repetitions'],
                                    'packages': metadata['packages'], 'email_revisions': metadata['config']['email_revisions'],
                                    'timeout_seconds': metadata['config']['timeout_seconds']},
               'status': 'offline_fixture_preview' if metadata['mode'] == 'offline' else 'partial' if incomplete else 'complete',
               'interrupted': interrupted, 'stages': stages, 'repeatability': repeatability,
               'communications_repeatability': repeated_checks,
               'workflows': {'requested': expected_workflows, 'recorded': len(workflows), 'passed': passed,
                             'statuses': dict(Counter(r['status'] for r in workflows)),
                             'pass_rate_of_requested': ratio(passed, expected_workflows), 'results': workflows},
               'cost': {'api_calls': len(live), 'agent_calls': sum(r['kind'] == 'agent' for r in live),
                        'judge_calls': sum(r['kind'] == 'judge' for r in live),
                        'usage_based_estimated_usd': str(total_cost), 'agent_estimated_usd': cost([r for r in live if r['kind'] == 'agent']),
                        'judge_estimated_usd': cost([r for r in live if r['kind'] == 'judge']),
                        'unknown_cost_attempts': sum(r['estimated_cost_usd'] is None for r in live),
                        'estimated_usd_per_passed_workflow': str(total_cost / passed) if passed and
                            not any(r['estimated_cost_usd'] is None for r in live) else None,
                        'per_passed_workflow_note': 'Includes isolated-agent tests and judging overhead for this evaluation run.',
                        'budget': budget},
               'skips': skip_rows, 'limitations': metadata['limitations'],
               'failure_details': [detail for r in agents if (detail := failure_detail(r))],
               'artifacts': {'attempts': 'attempts.jsonl', 'configuration_and_inputs': 'run.json'}}
    save_json(folder / 'summary.json', summary)
    write_report(folder / 'report.html', [(folder.name, summary)])
    return summary


def percent(value):
    return 'Unscored' if value is None else f'{value * 100:.0f}%'


def write_report(path, summaries):
    cards = []
    for relative, summary in summaries:
        model = summary['agent_model'] or 'Model not selected'
        label = 'OFFLINE FIXTURE PREVIEW — no model or judge was called' if summary['mode'] == 'offline' else summary['status'].upper()
        stages = ''.join('<tr>' + ''.join(f'<td>{escape(str(v))}</td>' for v in (
            STAGE_LABELS.get(name, name), data['attempts'], percent(data['first_attempt_deterministic_pass_rate']),
            f"{percent(data['semantic_pass_rate'])} ({data['semantic_assessments']} reviewed)", data['unscored_attempts'])) + '</tr>'
                         for name, data in summary['stages'].items())
        results = ''.join(f"<tr><td>Home {escape(r['case_id'])}</td><td>{r['observed_attempts']}</td>"
                          f"<td>{r['structured_responses']}</td><td>{percent(r['decision_pairwise_agreement'])}</td>"
                          f"<td>{percent(r['deterministic_pass_rate'])}</td></tr>" for r in summary['repeatability'])
        target = '' if path.parent.name == summary['run_id'] else relative + '/'
        run_folder = path.parent if path.parent.name == summary['run_id'] else path.parent / relative
        records = load_records(run_folder / 'attempts.jsonl') if (run_folder / 'attempts.jsonl').exists() else []
        failures = summary.get('failure_details')
        if failures is None:
            failures = [detail for r in records if r['type'] == 'attempt' and r['kind'] == 'agent' and (detail := failure_detail(r))]
        failed = ''.join('<tr>' + ''.join(f'<td>{escape(str(v))}</td>' for v in (
            f"Home {r['case_id']}, repetition {r['repetition']}", STAGE_LABELS.get(f"{r['scope']}/{r['stage']}", r['stage']),
            r['category'], r['reason'])) + '</tr>' for r in failures)
        feedback = ''.join('<li>' + escape(f"Home {r['case_id']}, repetition {r['repetition']}: {key} — {value['reason']}") + '</li>'
                           for r in records if r['type'] == 'attempt' and r['kind'] == 'judge' and isinstance(r.get('output'), dict)
                           for key, value in r['output'].items() if value.get('verdict') == 'fail')
        email_repeats = ''.join('<tr>' + ''.join(f'<td>{escape(str(v))}</td>' for v in (
            'Home ' + r['case_id'], 'Alone' if r['scope'] == 'isolated' else 'Workflow', r['observed_attempts'],
            percent(r['deterministic_pass_rate']), f"{percent(r['semantic_pass_rate'])} ({r['semantic_assessments']} reviewed)")) + '</tr>'
                               for r in summary.get('communications_repeatability', []))
        audit_base = path.parent.parent / 'audits' if path.parent.name == summary['run_id'] else path.parent / 'audits'
        audit_files = sorted(audit_base.glob(summary['run_id'] + '-checks-*.json'), key=lambda p: p.stat().st_mtime)
        audit_file = audit_files[-1] if audit_files else None
        audit_note = ''
        if audit_file is not None:
            import json
            audit = json.loads(audit_file.read_text())
            href = ('../audits/' if path.parent.name == summary['run_id'] else 'audits/') + audit_file.stem + '.html'
            audit_note = f'<p><strong>Saved-output validation recheck:</strong> {audit["new_passes"]}/{audit["attempts"]} accepted under {escape(audit["policy_version"])}; {audit["unavailable"]} unavailable. <a href="{escape(href)}">See recheck details</a>. No new model calls or judge assessments; original scores below are preserved.</p>'
        is_comparison = path.name == 'comparison.html'
        if is_comparison:
            heading = (f'<details class="evaluation-toggle" open><summary>'
                       f'<span class="eval-heading"><strong>{escape(model)}</strong> '
                       f'<span class="badge">{escape(label)}</span><br>'
                       f'<span class="eval-meta">{escape(summary["run_id"])} · '
                       f'{summary["workflows"]["passed"]}/{summary["workflows"]["requested"]} workflows passed</span>'
                       f'</span></summary>')
        else:
            heading = f'<p class="badge">{escape(label)}</p><h2>{escape(model)}</h2>'
        closing = '</details>' if is_comparison else ''
        cards.append(f'''<article>{heading}
<p>Judge: {escape(summary['judge_model'] or 'Not selected')} · {escape(summary['run_id'])}</p>
<p>Scoring policy: {escape(summary.get('evaluation_policy_version', 'checks-v1'))}. Complete means the run finished; individual tests may have failed.</p>{audit_note}
<div class="metrics"><div><strong>${escape(summary['cost']['usage_based_estimated_usd'])}</strong>Usage-based estimated cost</div>
<div><strong>{summary['cost']['api_calls']}</strong>API calls, including judge calls</div>
<div><strong>{summary['workflows']['passed']}/{summary['workflows']['requested']}</strong>Workflows passed</div></div>
<p>Unknown-cost attempts: {summary['cost']['unknown_cost_attempts']}. Incomplete and unscored tests remain visible.</p>
<p>The email agent is tested alone with fixed approved findings and again with the analyst's live findings. The workflow reuses that repetition's analyst call.</p>
<table><caption>Agent checks</caption><thead><tr><th>Stage</th><th>Attempts</th><th>Valid on first try</th><th>Judge pass</th><th>Unscored</th></tr></thead><tbody>{stages}</tbody></table>
<table><caption>Analyst repeatability</caption><thead><tr><th>Case</th><th>Requests</th><th>Structured responses</th><th>Decision agreement</th><th>Python pass</th></tr></thead><tbody>{results}</tbody></table>
<p>Unscored agreement means fewer than two structured responses were available. Agreement measures consistency, including consistently wrong decisions.</p>
<table><caption>Insights/email agent repeated-run checks</caption><thead><tr><th>Case</th><th>Test</th><th>Requests</th><th>Python pass</th><th>Judge pass</th></tr></thead><tbody>{email_repeats or '<tr><td colspan="5">No email-agent requests recorded.</td></tr>'}</tbody></table>
<p>Email repetitions measure factual compliance and semantic quality on each draft, not direct agreement in meaning between drafts.</p>
<details open><summary>Failed attempts and causes</summary><table><thead><tr><th>Case</th><th>Test</th><th>Category</th><th>Reason</th></tr></thead><tbody>{failed or '<tr><td colspan="4">None recorded</td></tr>'}</tbody></table></details>
<details open><summary>Judge feedback</summary><ul>{feedback or '<li>No failing semantic assessments recorded. Unscored responses were not judged.</li>'}</ul></details>
<p><a href="{escape(target)}summary.json">Summary JSON</a> · <a href="{escape(target)}attempts.jsonl">All attempts and responses</a> · <a href="{escape(target)}run.json">Frozen inputs and configuration</a></p>{closing}</article>''')
    groups = {dumps(s['comparison_group']) for _, s in summaries if s['mode'] == 'live'}
    note = 'Runs have different inputs, judging, settings, or dependency versions; compare within matching groups.' if len(groups) > 1 else 'Compare models on matching datasets, prompts, judge, and settings. Judge assessments are separate from Python checks.'
    path.write_text('''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Utility agent evaluations</title><style>
body{font:16px/1.55 system-ui,sans-serif;background:#f3f6fa;color:#18314a;margin:0;padding:32px}main{max-width:1100px;margin:auto}
h1{font-size:36px;letter-spacing:-1px}h2{margin:4px 0}.badge{font-size:12px;font-weight:700;color:#665000}
article{background:white;border:1px solid #dce5ed;border-radius:16px;padding:24px;margin:24px 0;overflow:auto}
.evaluation-toggle > summary{cursor:pointer;color:#123c66;font-size:20px;line-height:1.4}.evaluation-toggle > summary::marker{color:#1465ac}.evaluation-toggle > summary:focus-visible{outline:3px solid #1465ac;outline-offset:4px}.eval-heading{display:inline-block;vertical-align:top}.eval-heading .badge{margin-left:10px}.eval-meta{font-size:13px;font-weight:400;color:#53677b}
.metrics{display:flex;flex-wrap:wrap;gap:32px;margin:24px 0}.metrics div{color:#53677b;font-size:14px}.metrics strong{display:block;color:#123c66;font-size:28px}
table{width:100%;border-collapse:collapse;margin:24px 0}th,td{text-align:left;padding:10px;border-bottom:1px solid #e4ebf2}
caption{text-align:left;font-weight:700;padding-bottom:8px}a{color:#1465ac}footer{color:#53677b;margin:24px 0}
</style><main><h1>Utility agent evaluations</h1><p>Separate evaluation workspace · $'''
                    + str(TOTAL_LIMIT) + ''' shared API budget</p><p>'''
                    + escape(note) + '</p>' + ''.join(cards) + '<footer>Prepared by Venu Nagineni</footer></main></html>', encoding='utf-8')


def comparison(folder=RESULTS):
    import json
    summaries = [(path.parent.name, json.loads(path.read_text()))
                 for path in sorted(folder.glob('*/summary.json'))]
    folder.mkdir(parents=True, exist_ok=True)
    write_report(folder / 'comparison.html', summaries)
    save_json(folder / 'comparison.json', {'runs': [s for _, s in summaries]})
    return folder / 'comparison.html'


def rebuild_reports(folder=RESULTS):
    """Recover orphaned/interrupted run journals without importing any model SDK."""
    import json
    for metadata in sorted(folder.glob('*/run.json')):
        summary_path = metadata.parent / 'summary.json'
        old = json.loads(summary_path.read_text()) if summary_path.exists() else None
        newer_records = (metadata.parent / 'attempts.jsonl').stat().st_mtime > summary_path.stat().st_mtime if old else True
        if old and not newer_records:
            write_report(metadata.parent / 'report.html', [(metadata.parent.name, old)])
        else:
            summarize(metadata.parent, old['cost']['budget'] if old else None,
                      interrupted=bool(old and old['interrupted']) or newer_records)
    return comparison(folder)
