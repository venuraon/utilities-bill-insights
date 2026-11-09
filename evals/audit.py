"""Recheck saved responses with the new validator, preserving original scores."""
import json
from html import escape
from pathlib import Path
from .common import RESULTS, load_records, save_json, utc_now
from .normalization import POLICY_VERSION, visible_json
from .scoring import score


def audit_run(run_id, results=RESULTS):
    if not run_id or run_id != Path(run_id).name:
        raise ValueError('Specify the saved run folder name, not a path')
    folder = results / run_id
    metadata = json.loads((folder / 'run.json').read_text())
    cases = {c['case_id']: c for c in metadata['cases']}
    records = load_records(folder / 'attempts.jsonl')
    outcomes = []
    for record in records:
        if record['type'] != 'attempt' or record['kind'] != 'agent':
            continue
        candidate = record.get('candidate_raw')
        if candidate is None:
            candidate = record.get('output')
        recovered = False
        if candidate is None and record.get('execution_status') not in ('incomplete', 'refused', 'provider_error'):
            candidate = visible_json(record.get('raw_output'))
            recovered = candidate is not None
        upstream = record['input'].get('analysis', record['input']) if record['stage'] == 'communications' else None
        outcome = score(cases[record['case_id']], record['stage'], candidate, upstream,
                        record.get('execution_status') or ('sdk_error' if candidate is None else None))
        outcome.pop('normalized_output')
        outcomes.append({'attempt_id': record['attempt_id'], 'case_id': record['case_id'],
                         'repetition': record['repetition'], 'stage': record['stage'], 'scope': record['scope'],
                         'original_passed': record['deterministic']['passed'],
                         'recovered_complete_visible_json': recovered, **outcome})
    audit = {'type': 'saved_output_validation_recheck', 'source_run': run_id,
             'policy_version': POLICY_VERSION, 'created_utc': utc_now(), 'new_api_calls': 0,
             'attempts': len(outcomes), 'original_passes': sum(r['original_passed'] for r in outcomes),
             'new_passes': sum(r['passed'] for r in outcomes),
             'unavailable': sum(not r['assessable'] for r in outcomes), 'results': outcomes,
             'note': 'Rechecks identical saved responses with a different Python policy. Does not measure new prompt performance, '
                     'rerun agents, create judge scores, or change original workflow outcomes.'}
    target = results / 'audits' / (run_id + '-' + POLICY_VERSION)
    save_json(target.with_suffix('.json'), audit)
    rows = ''.join('<tr>' + ''.join(f'<td>{escape(str(value))}</td>' for value in (
        'Home ' + r['case_id'], r['repetition'], r['scope'] + '/' + r['stage'],
        'Pass' if r['original_passed'] else 'Fail',
        'Pass' if r['passed'] else 'Unavailable' if not r['assessable'] else 'Fail',
        '; '.join(n['reason'] + ': ' + str(n['original']) + ' → ' + str(n['normalized']) for n in r['normalizations'])
        or '; '.join(c['reason'] for c in r['checks'] if not c['passed']) or r['execution_status'])) + '</tr>' for r in outcomes)
    target.with_suffix('.html').write_text('''<!doctype html><html lang="en"><meta charset="utf-8"><title>Saved-response validation recheck</title>
<style>body{font:16px/1.5 system-ui;max-width:1200px;margin:40px auto;color:#18314a}table{border-collapse:collapse;width:100%}td,th{padding:10px;border-bottom:1px solid #ddd;text-align:left}</style>
<h1>Saved-response validation recheck</h1><p>Zero additional model calls. Original live scores and workflow results remain unchanged.</p>'''
        + f'<p>{audit["original_passes"]}/{audit["attempts"]} originally passed Python checks; {audit["new_passes"]}/{audit["attempts"]} accepted under {POLICY_VERSION}. '
          f'{audit["unavailable"]} outputs remain unavailable.</p>'
        + '<table><thead><tr><th>Case</th><th>Repetition</th><th>Test</th><th>Original</th><th>Recheck</th><th>Explanation</th></tr></thead><tbody>'
        + rows + '</tbody></table><p>Prompt improvements require a separate live run to assess.</p></html>', encoding='utf-8')
    return target.with_suffix('.html')
