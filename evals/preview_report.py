"""Build an alternate report from saved scores without rerunning evaluations.

Run: python -m evals.preview_report
Only comparison_temp.html is written. The existing report and JSON stay intact.
"""
import argparse
import json
from collections import Counter
from html import escape
from pathlib import Path

from .common import RESULTS, dumps, load_records


def badge(status):
    return f'<span class="status {escape(status.lower().replace(" ", "-"))}">{escape(status)}</span>'


def python_status(attempt):
    if attempt is None:
        return 'Not run'
    result = attempt.get('deterministic', {})
    if not result.get('assessable', True):
        return 'Unscored'
    return 'Passed' if result.get('passed') else 'Failed'


def judge_status(attempt, judges):
    if attempt is None:
        return 'Not run'
    result = judges.get(attempt['attempt_id'])
    value = result.get('semantic_pass') if result else None
    return 'Unscored' if value is None else 'Passed' if value else 'Failed'


def reasons(attempt, judges):
    if attempt is None:
        return ['No model result was recorded.']
    result = []
    output = attempt.get('output') or {}
    for check in attempt.get('deterministic', {}).get('checks', []):
        if check['passed']:
            continue
        if check['name'] == 'billing_comparisons':
            from .contract import TOKEN
            required = {'bill_total', 'bill_change', 'daily_change', 'feb_days', 'march_days'}
            missing = sorted(required - set(TOKEN.findall(output.get('explanation', ''))))
            for key in missing:
                location = ' It appears in the headline.' if key in TOKEN.findall(output.get('headline', '')) else ''
                label = {'bill_change': 'bill change', 'bill_total': 'bill total', 'daily_change': 'daily usage change',
                         'feb_days': 'February day count', 'march_days': 'March day count'}[key]
                result.append(f'The {label} is missing from the required explanation field.{location}')
        else:
            result.append(check.get('reason') or check['name'])
    judge = judges.get(attempt['attempt_id'], {})
    for criterion, finding in (judge.get('output') or {}).items():
        if finding.get('verdict') == 'fail':
            result.append(f'Judge · {criterion}: {finding["reason"]}')
    if python_status(attempt) == 'Unscored' and not result:
        result.append('The response could not be assessed; inspect the saved execution result.')
    return result


def candidate_text(attempt):
    output = attempt.get('output') or {}
    metrics = attempt.get('input', {}).get('metrics', {})
    # Resolve only supplied references. Unknown references remain visible.
    from .judge import resolve_candidate
    return resolve_candidate(output, metrics)


def build_preview(folder=RESULTS, run_id=None, *, write=True):
    folder = Path(folder)
    if run_id is None:
        candidates = [(p.parent, json.loads(p.read_text())) for p in folder.glob('*/summary.json')]
        candidates = [(p, s) for p, s in candidates if s.get('mode') == 'live']
        if not candidates:
            raise ValueError('No saved live evaluations found')
        run, summary = max(candidates, key=lambda item: item[1]['started_utc'])
    else:
        run = folder / run_id
        summary = json.loads((run / 'summary.json').read_text())
    metadata = json.loads((run / 'run.json').read_text())
    records = load_records(run / 'attempts.jsonl')
    agents = {r['attempt_id']: r for r in records if r['type'] == 'attempt' and r['kind'] == 'agent'}
    judges = {r['parent_id']: r for r in records if r['type'] == 'attempt' and r['kind'] == 'judge'}
    workflows = summary['workflows']['results']
    failed = [w for w in workflows if w['status'] == 'failed']
    counts = Counter(w['status'] for w in workflows)
    rows, issue_cards = [], []
    for index, workflow in enumerate(workflows):
        analyst = agents.get(workflow.get('analyst_attempt_id'))
        email = agents.get(workflow.get('communications_attempt_id'))
        attempts = [a for a in (analyst, email) if a]
        py = [python_status(a) for a in attempts]
        judged = [judge_status(a, judges) for a in attempts]
        py_result = 'Failed' if 'Failed' in py else 'Unscored' if 'Unscored' in py else 'Passed' if py else 'Not run'
        judge_result = 'Failed' if 'Failed' in judged else 'Unscored' if 'Unscored' in judged else 'Passed' if judged else 'Not run'
        issues = [(a, reasons(a, judges)) for a in attempts]
        messages = [message for _, messages in issues for message in messages]
        normal = workflow.get('communications_source') == 'deterministic_normal'
        if workflow['status'] == 'passed':
            explanation = 'Normal usage; calculated snapshot used.' if normal else 'Analyst and customer copy passed.'
        else:
            explanation = ' '.join(messages) or 'No complete, assessable workflow result. See the saved records.'
        anchor = f'issue-{index}'
        link = f' <a href="#{anchor}">See output ↓</a>' if messages else ''
        rows.append(f'<tr><th scope="row">Home {escape(workflow["case_id"])}</th><td>{workflow["repetition"]}</td>'
                    f'<td>{badge(workflow["status"].title())}</td><td>{badge(py_result)}</td><td>{badge(judge_result)}</td>'
                    f'<td>{escape(explanation)}{link}</td></tr>')
        if messages:
            content = []
            for attempt, attempt_reasons in issues:
                if not attempt_reasons:
                    continue
                text = candidate_text(attempt)
                fields = (('headline', 'Headline'), ('explanation', 'Explanation')) if attempt['stage'] == 'analyst' else (('intro', 'Title'), ('explanation', 'Explanation'))
                excerpt = ''.join(f'<div><dt>{label}</dt><dd>{escape(str(text.get(key, "No text recorded")))}</dd></div>'
                                  for key, label in fields)
                content.append(f'<p><strong>{"Billing analyst" if attempt["stage"] == "analyst" else "Email agent"}</strong></p>'
                               + '<ul>' + ''.join(f'<li>{escape(message)}</li>' for message in attempt_reasons) + '</ul>'
                               + '<dl class="excerpt">' + excerpt + '</dl>')
            issue_cards.append(f'<details class="issue" id="{anchor}" open><summary>Home {escape(workflow["case_id"])} · attempt {workflow["repetition"]}</summary>'
                               + ''.join(content) + '</details>')
    # Summarize each testing scope without counting isolated email tests as workflows.
    stage_labels = {'analyst/analyst': ('Billing analyst', 'Same calculated household evidence each time.'),
                    'isolated/communications': ('Email agent with a fixed example', 'Tests customer wording independently of this run’s analyst.'),
                    'workflow/communications': ('Email agent in the complete workflow', 'Uses this run’s validated analyst findings; shares the original analyst call.')}
    stage_rows = []
    for key, (label, description) in stage_labels.items():
        scope, stage = key.split('/')
        group = [a for a in agents.values() if a['scope'] == scope and a['stage'] == stage]
        passed_python = sum(python_status(a) == 'Passed' for a in group)
        assessed = [a for a in group if judge_status(a, judges) in ('Passed', 'Failed')]
        passed_judge = sum(judge_status(a, judges) == 'Passed' for a in assessed)
        stage_rows.append(f'<tr><th scope="row">{label}<small>{description}</small></th><td>{len(group)}</td>'
                          f'<td>{passed_python}/{len(group)}</td><td>{passed_judge}/{len(assessed)} reviewed</td></tr>')
    cost = summary['cost']
    intro = f'{counts["passed"]} of {summary["workflows"]["requested"]} workflows passed.'
    if failed:
        failures_by_home = Counter(w['case_id'] for w in failed)
        intro += ' ' + '; '.join(f'Home {h} failed {n} of {sum(w["case_id"] == h for w in workflows)} attempts'
                                for h, n in failures_by_home.items()) + '.'
        failures = [message for w in failed for a in [agents.get(w.get('analyst_attempt_id')), agents.get(w.get('communications_attempt_id'))]
                    if a for message in reasons(a, judges)]
        if failures and all('bill change is missing' in m for m in failures):
            intro += ' The bill change appeared in the headline instead of the required explanation field.'
    if counts['incomplete'] or counts['unscored']:
        intro += f' {counts["incomplete"]} incomplete and {counts["unscored"]} unscored.'
        if any('budget' in x.get('reason', '').lower() for x in summary.get('skips', [])):
            intro += ' Some tests were skipped because the budget could not cover the next request.'
    feedback = [(a, j) for a in agents.values() if (j := judges.get(a['attempt_id']))
                and j.get('semantic_pass') is False]
    judge_feedback = ''.join(f'<li>Home {escape(a["case_id"])} · attempt {a["repetition"]}: {escape(message)}</li>'
                             for a, j in feedback for message in reasons(a, {a['attempt_id']: j}) if message.startswith('Judge'))
    file_prefix = escape(run.name)
    details = ''.join(f'<dt>{escape(k.replace("_", " ").title())}</dt><dd>{escape(str(metadata.get(k, "Unavailable")))}</dd>'
                      for k in ('evaluation_policy_version', 'started_utc', 'dataset_hash', 'prompts_hash', 'rubric_hash'))
    page = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Evaluation results · preview</title><style>
*{box-sizing:border-box}body{margin:0;background:#f3f6fa;color:#18314a;font:16px/1.55 system-ui,sans-serif}main{max-width:1200px;margin:auto;padding:38px 25px}h1{font-size:34px;letter-spacing:-1px;margin:5px 0}h2{font-size:21px;margin:24px 0 10px}p{margin:10px 0}.eyebrow{font-size:12px;letter-spacing:1.5px;font-weight:700;color:#53677b}.muted,small{color:#53677b;font-size:13px}small{display:block;font-weight:400}.card{background:white;border:1px solid #dce5ed;border-radius:14px;margin:22px 0;padding:24px}.run-summary{cursor:pointer;font-size:20px;color:#123c66}.run-summary::marker{color:#1465ac}.run-meta{display:block;font-size:13px;color:#53677b}.overview{background:#edf4fb;border-left:4px solid #246eaa;border-radius:5px;padding:14px 18px;margin:20px 0}.numbers{display:flex;flex-wrap:wrap;gap:35px}.numbers strong{display:block;font-size:29px;color:#123c66}.numbers span{font-size:13px;color:#53677b}.status{display:inline-block;font-size:12px;font-weight:700;border-radius:5px;padding:3px 8px;white-space:nowrap;background:#edf0f4;color:#526171}.passed{background:#e5f3ea;color:#20633f}.failed{background:#fbe9e8;color:#9f2f28}.unscored,.incomplete{background:#fff2d6;color:#815e12}table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;vertical-align:top;border-bottom:1px solid #e4ebf2;padding:12px 9px}thead{background:#f6f8fb}caption{text-align:left;padding:10px 0;font-weight:700}.table-scroll{overflow-x:auto}a{color:#1465ac}details.issue{border:1px solid #e4d1cd;border-radius:8px;padding:12px 16px;margin:12px 0}summary{cursor:pointer;font-weight:650}summary:focus-visible,a:focus-visible{outline:3px solid #246eaa;outline-offset:4px}.excerpt{margin:12px 0;background:#f6f8fb;padding:12px;border-radius:5px}.excerpt div{margin:8px 0}dt{font-weight:700}dd{margin:2px 0 12px;overflow-wrap:anywhere}.note{font-size:14px;color:#53677b}.appendix{border-top:1px solid #e4ebf2;margin-top:22px;padding-top:14px}.comparison-note{border:1px solid #dcc991;background:#fff8e7;padding:12px 16px;border-radius:7px;font-size:14px}footer{font-size:13px;color:#53677b;margin-top:28px}@media(max-width:700px){main{padding:20px 12px}.card{padding:16px}.numbers{gap:18px}h1{font-size:29px}}
</style><main><span class="eyebrow">SAVED EVALUATION · REPORT PREVIEW</span><h1>What passed, what needs attention</h1>
<p class="muted">Latest saved live run. This preview presents the original scores; no model calls or rescoring.</p>'''
    page += f'<article class="card"><details open><summary class="run-summary">{escape(summary["agent_model"])} · {counts["passed"]}/{summary["workflows"]["requested"]} workflows passed'
    page += f'<span class="run-meta">{escape(summary["status"].upper())} · {escape(run.name)} · Judge: {escape(summary["judge_model"])} · ${float(cost["usage_based_estimated_usd"]):.3f} · {cost["api_calls"]} saved API calls</span></summary>'
    page += f'<div class="overview">{escape(intro)}</div><div class="numbers">'
    for value, label in ((counts['passed'], 'Passed'), (counts['failed'], 'Failed'),
                         (summary['workflows']['requested'] - len(workflows) + sum(counts[k] for k in ('incomplete', 'unscored')), 'Incomplete / unscored')):
        page += f'<div><strong>{value}</strong><span>{label}</span></div>'
    page += '</div><p class="note">Five homes × two attempts = ten workflows. A workflow passes when its required Python checks and judge assessments pass. Normal homes use a calculated snapshot and skip the email model.</p>'
    page += '<h2>Results by home and attempt</h2><div class="table-scroll"><table><thead><tr><th>Home</th><th>Attempt</th><th>Workflow</th><th>Python</th><th>Judge</th><th>What happened</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>'
    page += '<h2>Why workflows failed</h2><p class="note">Python checks enforce the output contract. The judge assesses meaning. A judge pass cannot override a failed Python requirement.</p>' + (''.join(issue_cards) or '<p>No workflow failures recorded.</p>')
    page += '<h2>Judge feedback</h2>' + ('<ul>' + judge_feedback + '</ul>' if feedback else '<p>No failing judge assessments recorded. Unscored or skipped cases were not judged.</p>')
    page += '<details class="appendix"><summary>Which parts were tested?</summary><div class="table-scroll"><table><thead><tr><th>Test</th><th>Model outputs</th><th>Python passed</th><th>Judge passed</th></tr></thead><tbody>' + ''.join(stage_rows) + '</tbody></table></div><p class="note">Fixed-example email tests are additional checks; they do not add to the ten workflows. Counts include all saved model attempts, including rewrites if enabled.</p></details>'
    standalone_failures = [a for a in agents.values() if a['scope'] == 'isolated' and (python_status(a) == 'Failed' or judge_status(a, judges) == 'Failed')]
    page += '<details class="appendix"><summary>Independent email test failures</summary>' + ('<ul>' + ''.join(f'<li>Home {escape(a["case_id"])} · attempt {a["repetition"]}: {escape(" ".join(reasons(a, judges)))}</li>' for a in standalone_failures) + '</ul>' if standalone_failures else '<p>No failures recorded among completed fixed-example tests. Skipped or unscored tests are not passes.</p>') + '</details>'
    page += '<details class="appendix"><summary>Cost, repeatability and run details</summary>'
    page += f'<p>Estimated model cost: ${float(cost["usage_based_estimated_usd"]):.4f}. Agent calls: {cost["agent_calls"]}; judge calls: {cost["judge_calls"]}. Unknown-cost attempts: {cost["unknown_cost_attempts"]}.</p>'
    page += '<p class="note">Cost includes independent tests and judging. It is not the production cost per customer email. Repeatability means matching decisions across attempts; matching decisions can still violate a required output rule.</p>'
    page += '<ul>' + ''.join(f'<li>Home {escape(r["case_id"])}: ' + ('Unscored decision agreement' if r['decision_pairwise_agreement'] is None else f'{r["decision_pairwise_agreement"]:.0%} decision agreement') + f' across {r["structured_responses"]} structured responses.</li>' for r in summary['repeatability']) + '</ul><dl>' + details + '</dl></details>'
    page += f'<p><a href="{file_prefix}/summary.json">Saved scores</a> · <a href="{file_prefix}/attempts.jsonl">Original outputs and judge feedback</a> · <a href="{file_prefix}/run.json">Exact prompts and settings</a></p></details></article>'
    page += '<p class="comparison-note"><strong>Comparing with older runs:</strong> this preview shows one run. Compare model results only when cases, prompts, judge, rules and settings match. The earlier all-pass Luna run used an older scoring policy; treat it as historical evidence rather than a controlled comparison.</p><footer>Prepared by Venu Nagineni · Five synthetic homes; results are development evidence.</footer></main></html>'
    if not write:
        return page
    target = folder / 'comparison_temp.html'
    target.write_text(page, encoding='utf-8')
    return target


def build_latest_models_preview(folder=RESULTS):
    """Combine the latest saved live run for each selected model, without rescoring."""
    folder = Path(folder)
    runs = [(p.parent.name, json.loads(p.read_text())) for p in folder.glob('*/summary.json')]
    selected = []
    for model in ('gpt-6-luna', 'gpt-5.4', 'gpt-5.4-mini'):
        matches = [(name, summary) for name, summary in runs
                   if summary.get('mode') == 'live' and summary.get('agent_model') == model]
        if matches:
            selected.append(max(matches, key=lambda item: item[1]['started_utc']))
    if not selected:
        raise ValueError('No saved live evaluations found for the selected models')
    pages = [build_preview(folder, name, write=False) for name, _ in selected]
    start = pages[0].index('<article class="card">')
    header = pages[0][:start].replace('Latest saved live run.', 'Latest saved live run for each selected model.')
    cards = ''.join(page[page.index('<article class="card">'):page.index('</article>') + len('</article>')]
                    for page in pages)
    groups = {dumps(summary['comparison_group']) for _, summary in selected}
    if len(groups) > 1:
        note = 'Runs differ in saved configuration, evaluation source or dependencies. Review run details before comparing; incomplete runs are not full model scores.'
    else:
        note = 'Runs have matching saved comparison settings. Incomplete runs are not full model scores.'
    target = folder / 'comparison_temp.html'
    target.write_text(header + cards + f'<p class="comparison-note"><strong>Comparison:</strong> {escape(note)}</p>'
                      + '<footer>Prepared by Venu Nagineni · Five synthetic homes; results are development evidence.</footer></main></html>', encoding='utf-8')
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', help='Saved run to preview; defaults to latest live run')
    args = parser.parse_args()
    if args.run_id:
        build_preview(run_id=args.run_id)
    else:
        build_latest_models_preview()
    print('Preview saved to evals/results/comparison_temp.html')
