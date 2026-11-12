#!/usr/bin/env python3
"""Build insights and viewer from previously generated data."""
import argparse
import asyncio
import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from utility_demo.agents import explain
from utility_demo.config import load_config
from utility_demo.data_factory import run_analytics
from utility_demo.render import render

ROOT = Path(__file__).resolve().parent


async def build(args):
    config = load_config()
    output = Path(args.output).resolve()
    demo_assets = ROOT / 'demo_assets' if output == ROOT / 'output' else output / 'demo_assets'
    data_dir = output / "data"
    started = time.perf_counter()
    print("Reading generated data and calculating semantic summaries (no model calls)…", flush=True)
    evidence_reports, invoices = run_analytics(config, data_dir)
    analytics_seconds = time.perf_counter() - started
    print(f"All semantic summaries saved: {data_dir / 'semantic_summaries.json'}", flush=True)
    semaphore = asyncio.Semaphore(config.get('max_concurrent_homes', 2))
    async def explain_home(evidence):
        async with semaphore:
            print(f"Home {evidence['home']['id']}: {args.mode} explanation", flush=True)
            result = await explain(evidence, config["model"], args.mode, output / "agent_runs", resume=getattr(args, 'resume', False))
            return {**evidence, **result}
    reports = await asyncio.gather(*(explain_home(evidence) for evidence in evidence_reports))
    # Publish only once all five reports pass validation.
    render(config, reports, invoices, output, ROOT / "frontend_templates", demo_assets)
    (data_dir / "evidence.json").write_text(json.dumps(reports, indent=2))
    analyses = output / 'analyses'
    analyses.mkdir(exist_ok=True)
    for report in reports:
        (analyses / f"{report['home']['id']}.json").write_text(json.dumps(report['analysis'], indent=2, allow_nan=False))
    review_queue = [{'home_id': r['home']['id'], 'analysis_path': f"analyses/{r['home']['id']}.json",
                     'analysis': r['analysis']} for r in reports if r['analysis']['needs_review']]
    (output / 'review_queue.json').write_text(json.dumps(review_queue, indent=2, allow_nan=False))
    (data_dir / 'run_metrics.json').write_text(json.dumps({
        'mode': args.mode, 'analytics_seconds': round(analytics_seconds, 3),
        'summary_bytes_by_home': {r['home']['id']: len(json.dumps(r['semantic_summary']).encode()) for r in reports},
        'configured_max_concurrent_homes': config.get('max_concurrent_homes', 2),
        'note': 'Byte sizes measure serialized summaries, not billed tokens. Sample/cached modes make no model calls.'
    }, indent=2))
    print(f"Ready: {demo_assets / 'demo.html'}")
    print(f"Slides: {demo_assets / 'slides.html'}")
    if review_queue:
        print(f"Completed with {len(review_queue)} home(s) requiring staff review: " + ', '.join(r['home_id'] for r in review_queue), flush=True)
        print(f"Review queue: {output / 'review_queue.json'}. Their customer emails were withheld.", flush=True)
    if args.mode == "sample":
        print("SAMPLE MODE: explanations are template placeholders, not OpenAI outputs.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("sample", "live", "cached"), default="sample")
    parser.add_argument("--output", default=str(ROOT / "output"))
    parser.add_argument("--resume", action="store_true", help="With live mode, reuse matching completed results and analyst checkpoints")
    args = parser.parse_args()
    if args.resume and args.mode != 'live':
        parser.error('--resume requires --mode live')
    if args.mode == "live" and not os.environ.get("OPENAI_API_KEY"):
        print("Live mode requires OPENAI_API_KEY. See README.md or use --mode sample.", file=sys.stderr)
        return 1
    if args.mode == "live" and importlib.util.find_spec("agents") is None:
        print("Install agent dependencies first: python -m pip install -r requirements.txt", file=sys.stderr)
        return 1
    try:
        asyncio.run(build(args))
    except (ValueError, ImportError) as exc:
        print(f"Build stopped: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Provider errors can echo credential fragments. Keep them out of console output.
        print(f"Build stopped ({type(exc).__name__}). For live mode, check API access, the configured model, and structured-output compatibility. No model substitution was attempted.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
