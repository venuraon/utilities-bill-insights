# Juniper Electric · Bill clarity demo

This local demo explains monthly electricity bills for five fictional Mesa homes. It has three parts: generate the source data once, build customer insights from that saved data, and run independent evaluations.

## Project map

| Part | Files | What it does |
| --- | --- | --- |
| 1. Data generation | `generate_data.py`, `data_generation/` | Creates 90 days of five-minute readings, shared weather, peer homes, and the fictional home and plan registry in `output/data/`. Run once, or rerun only when changing the synthetic scenario. |
| 2. Insights workflow | `build_demo.py`, `utility_demo/`, `web/` | Reads saved CSVs, calculates bills and semantic summaries, runs the two LLM roles when requested, and builds the local HTML viewer. It does not regenerate source data. |
| 3. Evaluations | `evals/` | Runs isolated analyst, communications, and full-workflow checks from frozen fixtures. It does not import the demo modules or modify demo outputs. See [evals/README.md](evals/README.md). |

`config.json` defines the fictional scenario, tariffs, thresholds, and demo model. `output/` holds generated data and presentation artifacts. `tests/` covers the data and workflow. `PLAN.md`, `take-home.md`, `problem-statement.md`, and `RECORDING.md` are project and presentation notes.

## Run the demo in order

Open a terminal in the project folder:

```sh
python3 generate_data.py                  # Step 1: once, no API key
python3 build_demo.py --mode sample       # Step 2: calculated demo with sample copy
open output/demo.html
open output/slides.html
```

To use saved live agent results without model calls, run `python3 build_demo.py --mode cached` after Step 1. The workflow always recalculates the semantic summaries from the saved source data; the agents see only those concise summaries. It writes bills, evidence, standalone email previews, and the viewer under `output/`.

For new live agent output, install dependencies and export your key in the same terminal:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export OPENAI_API_KEY="YOUR_OPENAI_API_KEY"
python build_demo.py --mode live
open output/demo.html
```

On macOS, you can enter the key without showing it or saving it in shell history:

```sh
printf 'OpenAI API key: '
read -r -s OPENAI_API_KEY
export OPENAI_API_KEY
printf '\n'
```

Use `python build_demo.py --mode live --resume` to reuse matching completed agent results after an interrupted run. `sample` uses clearly labeled placeholder copy; `cached` requires matching saved live results; `live` makes paid OpenAI calls. The configured demo model is `gpt-6-luna`. Normal homes skip the communications call. A review flag withholds that home's customer email. No mode sends email or changes a rate plan.

The generated data already exists in `output/data/`, so you can begin with Step 2. Running Step 1 again replaces the source CSVs with the same seeded scenario when `config.json` is unchanged. The workflow requires `readings.csv`, `weather.csv`, and `neighborhood.csv`; if one is missing, it tells you to run Step 1.

## Run evaluations separately

Evaluation commands run from the project root and use only `evals/` fixtures and results:

```sh
python3 -m venv evals/.venv
source evals/.venv/bin/activate
python -m pip install -r evals/requirements.txt
python -m evals.run --dry-run
python -m evals.run --mode offline
open evals/results/comparison.html
```

For paid evaluation runs, set `agent_model` and `judge_model` in `evals/config.json`, export `OPENAI_API_KEY`, then run `python -m evals.run --mode live`. The evaluation module enforces its own budget and saves JSON and HTML reports under `evals/results/`. Read [evals/README.md](evals/README.md) for the budget, stages, and scoring details.

## Inspect and verify

| Artifact | Purpose |
| --- | --- |
| [output/demo.html](output/demo.html) | Interactive local viewer |
| [output/slides.html](output/slides.html) | Presentation deck |
| [output/data/semantic_summaries.json](output/data/semantic_summaries.json) | Python evidence supplied to the analyst |
| `output/analyses/` | Validated analyst output for each home |
| `output/emails/` | Standalone HTML customer previews |
| `output/review_queue.json` | Cases withheld for staff review |

```sh
.venv/bin/python -m unittest discover -s tests -v
node tests/viewer.test.cjs                 # Optional, if Node.js is installed
```

The project uses synthetic households and simplified tariffs. The generated charts and bill amounts come from deterministic calculations. Model text is validated against that evidence, but a real utility would still review customer drafts, tariff eligibility, privacy rules, and measured customer impact before deployment.
