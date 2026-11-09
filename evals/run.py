"""Run from the project root: python -m evals.run --dry-run."""
import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from uuid import uuid4
from .budget import Budget, JUDGE_INPUT_CAP, input_bound, maximum_cost
from .common import RESULTS
from .judge import JUDGE_PROMPT, SemanticReview
from .reporting import comparison, rebuild_reports, summarize
from .runner import Harness
from .eval_schemas import BillingAnalysis, EmailCopy
from .settings import load_config, load_fixtures, load_prices


def plan(config, cases, prompts, prices=None):
    total, bounds = 0, []
    for case_id in config['cases']:
        case = cases[case_id]
        jobs = [('analyst', case['summary'])]
        if case['summary']['anomalies']['is_anomaly']:
            jobs.extend([('communications', case['reference_analysis'])] * 2)
        for stage, payload in jobs:
            total += config['repetitions']
            if prices:
                schema = BillingAnalysis if stage == 'analyst' else EmailCopy
                bound = maximum_cost(prices['models'][config['agent_model']],
                                     input_bound(prompts[stage], payload, schema.model_json_schema()),
                                     config['max_output_tokens'][stage])
                judge = maximum_cost(prices['models'][config['judge_model']], JUDGE_INPUT_CAP,
                                     config['max_output_tokens']['judge'])
                bounds.append((bound + judge) * config['repetitions'])
    return {'agent_model': config['agent_model'], 'judge_model': config['judge_model'],
            'planned_agent_attempts_before_revisions': total,
            'planned_judge_attempts_before_revisions': total,
            'planned_api_calls_before_revisions': total * 2,
            'rough_full_plan_reservation_usd': str(sum(bounds)) if bounds else None,
            'note': 'Reservation sum is deliberately conservative, not expected billing. Workflow communication inputs vary. '
                    'Actual costs free unused reservations; each call pair is checked against the shared ledger. '
                    'Insufficient funds produce partial results, never an automatic budget increase.',
            'paid_calls_made_by_dry_run': 0}


async def execute(config, cases, prompts, manifest, mode, prices):
    backend = None
    budget = None
    harness = None
    interrupted = False
    try:
        if mode == 'live':
            if not os.environ.get('OPENAI_API_KEY'):
                raise ValueError('Export OPENAI_API_KEY in your terminal before live evaluation')
            # Validate all schemas before opening the client or reserving any funds.
            from agents import AgentOutputSchema
            for schema in (BillingAnalysis, EmailCopy, SemanticReview):
                AgentOutputSchema(schema)
            budget = Budget()
            budget.__enter__()
            from .backend import OpenAIBackend
            backend = OpenAIBackend(config['timeout_seconds'])
        run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + mode + '-' + uuid4().hex[:6]
        harness = Harness(config, cases, prompts, manifest, RESULTS / run_id,
                          mode=mode, backend=backend, budget=budget, prices=prices)
        await harness.run()
    except BaseException:
        interrupted = True
        raise
    finally:
        try:
            if harness:
                summary = summarize(harness.folder, budget.snapshot() if budget else None, interrupted)
                print(f"Saved: {harness.folder / 'summary.json'}", flush=True)
                print(f"Report: {comparison()}", flush=True)
                print(f"Status: {summary['status']} · estimated API cost ${summary['cost']['usage_based_estimated_usd']}", flush=True)
        finally:
            if backend:
                await backend.close()
            if budget:
                budget.__exit__(None, None, None)


def main():
    parser = argparse.ArgumentParser(description='Isolated utility-agent evaluations; never runs the demo builder.')
    parser.add_argument('--config', help='Evaluation config file; default evals/config.json')
    parser.add_argument('--mode', choices=('offline', 'live'), default='offline')
    parser.add_argument('--dry-run', action='store_true', help='Print a plan; no model calls or result files')
    parser.add_argument('--report', action='store_true', help='Rebuild comparison from saved JSON; no model calls')
    parser.add_argument('--audit-run', metavar='RUN_ID', help='Recheck saved outputs with current Python rules; no model calls')
    args = parser.parse_args()
    try:
        if args.audit_run:
            from .audit import audit_run
            print(audit_run(args.audit_run))
            print(rebuild_reports())
            return
        if args.report:
            print(rebuild_reports())
            return
        config = load_config(args.config)
        cases, prompts, manifest = load_fixtures()
        prices = None
        if config['agent_model'] and config['judge_model'] and (args.mode == 'live' or args.dry_run):
            prices = load_prices([config['agent_model'], config['judge_model']])
        elif args.mode == 'live':
            raise ValueError('Set agent_model and judge_model in evals/config.json before live evaluation')
        if args.dry_run:
            print(json.dumps(plan(config, cases, prompts, prices), indent=2))
            return
        asyncio.run(execute(config, cases, prompts, manifest, args.mode, prices))
    except (ValueError, OSError) as exc:
        parser.exit(1, f'Evaluation stopped: {exc}\n')
    except KeyboardInterrupt:
        parser.exit(130, 'Evaluation interrupted; completed records and budget reservations are retained.\n')


if __name__ == '__main__':
    main()
