# Utility billing evaluations

This is a standalone evaluation project. It imports nothing from `utility_demo/`,
never runs `build_demo.py`, and never reads or writes the demo's `output/` or model
caches. Its frozen inputs, copied contracts, configuration, dependencies, budget,
and results are all under `evals/`. The original demo commands continue to work.

## 1. Create the separate environment

Run these commands from the project root. No commands in this guide rebuild the demo.

```sh
python3 -m venv evals/.venv
source evals/.venv/bin/activate
python -m pip install -r evals/requirements.txt
```

These dependencies are separate from the demo's `.venv` and `requirements.txt`.
Use Python 3.10 or newer. The pinned Agents SDK/Pydantic versions match the
versions checked when this module was built.

## 2. Preview without an API key

```sh
python -m evals.run --dry-run
python -m evals.run --mode offline
open evals/results/comparison.html
```

The dry run only prints the planned calls and, when both models are configured,
a deliberately conservative reservation estimate. It does not create result files.
The offline run exercises the Python checks with deterministic reference fixtures.
Its report is labeled **offline fixture preview**. Neither an agent model nor a
judge was called; this is not evidence of model quality. Semantic scores remain
unscored. It does not create or spend the live budget ledger.

## 3. Select the two models

Edit only these two fields in `evals/config.json`:

- `agent_model`: the OpenAI model used for both agents in this evaluation run.
- `judge_model`: the OpenAI model used for semantic review.

Both start as `null`, so paid runs cannot start accidentally. Enter exact model
IDs available to your API project. Changing these fields never changes the demo's
`config.json`. The module uses the OpenAI Agents SDK with the official OpenAI
endpoint; external providers and alternate base URLs are not used.

To compare another model, change `agent_model` and run again. Keep the judge and
other settings fixed. Results are retained in separate timestamped folders and
combined in `comparison.json`/`comparison.html`. Mismatched configurations are
flagged; there is no automatic winner or change to the demo model.

The other settings are intentionally small:

- `cases`: A–E by default.
- `repetitions`: two fresh repetitions per case by default; this is exploratory.
- `max_output_tokens`: separate analyst, communications, and judge token caps.
  Reasoning tokens consume the model's output allowance; an insufficient cap can
  produce an incomplete response, recorded as a failure.
- `reasoning_effort`: model-supported agent/judge setting, currently `low` for both.
  Unsupported settings are recorded as API errors rather than silently changed.
- `timeout_seconds`: sixty by default.
- `email_revisions`: zero by default to save money; up to two length-only repairs
  can be enabled. Each repair and its judgment are paid, recorded attempts.

## 4. Check prices, export your key, and run

`evals/prices.json` contains dated standard/default prices for seven OpenAI models,
checked against [official pricing](https://developers.openai.com/api/docs/pricing).
Unknown model IDs or prices older than thirty days block a live run. Add verified
rates for another model to this file before using it. Prices are estimates for
the standard public endpoint, not regional, priority, batch, or special account rates.
The cache-write rate conservatively covers the longer write tier where applicable.
For GPT-5.4, GPT-5.4 Mini, and GPT-5.4 Nano, `cache_write` equals the ordinary input rate because
these models have no additional cache-write charge.

Export the key in the same terminal session. It is not stored in configuration or results.

```sh
export OPENAI_API_KEY="your-openai-api-key"
python -m evals.run --dry-run
python -m evals.run --mode live
open evals/results/comparison.html
```

There is no required file-execution sequence beyond this single entry point.
An alternate evaluation config can be selected using `--config path/to/config.json`.
All configs share the same budget; there is no per-config or per-run reset.

With the current routing, five homes and two repetitions schedule **eighteen agent
calls and eighteen judge calls**, before any optional repairs. Failed analyst
outputs can prevent workflow communications, and budget limits can reduce this
count. Homes A, B, and E take the normal deterministic snapshot route; B's weather
association is not an equipment anomaly. C and D exercise the communications agent.

## What is evaluated

1. **Agent 1 alone:** every candidate receives the same frozen semantic summary.
   Python checks the schema, canonical metric references, anomaly/peer fields, review
   decision, plan selection/costs, required comparisons, and explanation length.
2. **Agent 2 alone:** C and D receive the same deterministic, approved reference
   analysis for every model. These references are fixture data, not past LLM answers.
   Python checks references, schema, and resolved caption lengths.
3. **Full workflow:** reuse that repetition's actual Agent 1 output, validate it,
   and feed it to Agent 2 when appropriate. A bad or held analysis blocks workflow
   communication. Normal homes use the copied deterministic snapshot function.
   This is a separate workflow assessment that shares the original analyst call;
   it is not an additional independent analyst trial.
4. **Semantic review:** a separate judge receives the calculated evidence,
   task context, candidate response with placeholders expanded, and, for Agent 2,
   the approved upstream analysis. It never sees model identity or previous scores.
   A versioned rubric grades groundedness, uncertainty, actionability, and clarity
   with `pass`, `fail`, or appropriate `not_applicable`, plus brief reasons/quotes.
   Groundedness and clarity must be assessed. Python checks the judge's structure.
   Semantic review cannot override failed deterministic checks.

The judge evaluates raw agent copy. The current demo's final HTML also includes
Python-generated guidance and graphics; these do not count as LLM performance.
The analyst's input already contains calculated anomaly flags and plan prices, so
this measures interpretation and faithful communication, not independent anomaly
discovery or the correctness of the upstream analytics. Five familiar synthetic
cases cannot establish production accuracy. Judge assessments are probabilistic,
not human ground truth; inspect the saved explanations when presenting results.

## Revised checks and judge feedback

The evaluation-only analyst prompt incorporates feedback from the saved Luna run:
when weather fit is weak or extrapolated, it must qualify weather's role and avoid
interpreting the residual percentage in prose. The residual is relative to the
weather model's estimate, not a percentage of total use. The judge's criteria and
the requirement to include the calculated metric in anomaly evidence are unchanged.

The current `checks-v5` policy accepts the bill change in either the analyst
headline or explanation, matching the demo; the other billing comparisons remain
required in the explanation. Original saved scores remain unchanged.
The policy keeps the LLM's original word targets. Python
allows twenty percent extra, rounded down to whole words, except that the intro
has an explicit 11-word hard limit: subject 9, intro 11,
explanation 28, action 16, and combined body 54. The analyst explanation hard
limit is 144 words while its prompt still requests 120. These private enforcement
limits are stored in run metadata and never supplied to either agent or judge.
Optional shortening retries receive only the original targets. Counts still use
expanded metric values. The original demo's word limits are unchanged.

The `checks-v2` policy accepts harmless presentation differences: dollar symbols
in USD decimal fields, numeric versus string representations, trailing decimal
zeros, and whitespace differences in copied display metrics. Every normalization
is recorded alongside the original visible JSON. It does **not** round amounts,
change a sign, remove a unit, accept another currency, or correct a wrong number.
Unknown/duplicated metrics, incorrect plans, incorrect flags, missing required
evidence, and caption-length violations still fail. Each requirement has its own
check; one wrong metric no longer also produces a duplicate broad contract failure.

Incomplete responses, refusals, API errors, and SDK errors are execution outcomes,
separate from factual checks. The public API response is captured before the
Agents SDK parses it, retaining completion status/reason, request ID, usage, and
visible output even if the SDK rejects it. No-output cases have unassessed factual
checks rather than a misleading failed-schema assertion. Unknown failures in older
runs remain unknown; a report refresh cannot recover missing API responses.

`prompt_updates.json` contains versioned, manually curated improvements from the
first live run. Agent 1 receives explicit machine-readable money formatting and
focused synthesis guidance. Agent 2 receives the judge's plain-language feedback:
explain steady electricity use and practical checks without unexplained baseline
or power-unit jargon. The judge rubric and quality standards are unchanged.
Feedback informs the next prompt version; responses are not repeatedly rewritten
until the same judge approves them. These changes apply to evaluation agent copies.
The original demo prompts remain unchanged, preserving the separation requested.

The current caps are 4,096 analyst, 1,536 communications, and 1,536 judge output
tokens, with low reasoning effort. This gives reasoning and JSON more headroom
than the first pilot. The shared $2 guard reserves for these larger caps.
Only a new live run can establish whether the prompt/settings changes improve
model performance; an offline validation recheck cannot establish that.

To recheck already-saved outputs without any model calls:

```sh
python -m evals.run --audit-run 20261106T154409Z-live-f9e7e4
```

This saves separate JSON/HTML files under `evals/results/audits/` and links them
from the comparison page. Original run JSON, responses, judge scores, workflow
outcomes, and the budget ledger are preserved. The first run's Python recheck
accepts eleven of eighteen saved agent outputs versus nine originally; seven
remain unavailable. This is a validation-policy comparison, not a new live score.

Repeatability compares structured analyst decisions, rather than exact wording,
and reports factual/semantic pass rates across repeated communications. It stays
separate from accuracy: identical wrong answers are still repeatable. A single
response has no decision-agreement score. Provider prompt-cache hits are recorded
in usage; application-level output caches are never reused as new repetitions.

## Evaluation budgets

`evals/results/budget.json` persists across **all** live runs and models. Keep this
file: deleting it would discard the local spending history. There is intentionally
no budget reset/increase command. A file lock prevents simultaneous paid runs.

Each live evaluation has a **$0.35** limit; all runs together have a **$4.00** limit.
The runner allocates at most **$3.90** to requests overall, leaving a ten-cent margin.
The authorized increase is recorded in the existing ledger; all previous spending
and unknown-request reservations continue to count. Each new run gets its own cap.
Before each agent call it reserves the agent's conservative maximum plus room
for its judge. Input reservations use UTF-8 bytes for prompts, inputs, and schemas
plus framing allowance; this overestimates normal token counts. Judge inputs have
a reserved twenty-thousand-token byte bound. Oversized inputs are left unscored.
Standard-tier output caps apply to every request. SDK/network retries are disabled.

Actual provider usage frees unused reservations. Output tokens include reasoning
tokens; cached-input tokens are priced separately, without double-counting output.
Failed or interrupted calls with unknown usage keep their entire reservation.
The runner stops or marks tests incomplete when another pair cannot fit; it never
silently raises the budget. Recordings retain known costs and unknown reservations
separately. This is a conservative local guard using dated prices, not an API
account billing limit; invoices, special pricing, or unexpected provider accounting
must be reconciled against the recorded request IDs. No paid calls are needed to
test or preview this module.

The reservation for an agent and judge is intentionally conservative, so a run can
stop below $0.35 even if its expected final cost is lower. A new command starts a
new run; it does not resume incomplete cases from an earlier run.

## Saved evidence for your demo

Every run creates `evals/results/<run-id>/`:

- `attempts.jsonl`: incrementally saved agent and judge calls, raw responses,
  inputs, IDs, repetitions, scores, validation failures, token usage, latency,
  costs/reservations, workflow links, and explicit skips. Completed records survive
  interruption; an unfinished API request still retains its budget reservation.
- `run.json`: the exact configuration, inputs, prompts, schemas, pricing snapshot,
  dependency versions, and fixture/rubric hashes used for that run.
- `summary.json`: aggregate scores, failure counts, workflow outcomes, repetition
  counts, cost breakdowns, and limitations. Incomplete and unscored work is visible.
- `report.html`: a compact local report with links to the saved evidence.

The combined `evals/results/comparison.html` and `comparison.json` can be used in
your demo later. To refresh those files without any model calls:

```sh
python -m evals.run --report
```

Do not present offline fixture scores as model evaluation results. Each report
clearly identifies its mode. Records contain full synthetic inputs and responses,
not API keys. Low-level SDK exception messages are omitted to avoid echoing headers.

## Verification

```sh
python -m unittest discover -s evals/tests -v
```

Tests exercise budget persistence and failure handling, deterministic failures,
judge failures, repeatability, workflow routing, and the real SDK using a mocked
HTTP transport. They make no paid API calls.

The frozen source hashes live in `evals/fixtures/manifest.json`. Evaluation schema
adapters in `eval_schemas.py` and prompt additions in `prompt_updates.json` are
versioned separately; their effective schemas/prompts are saved with each new run.
Fixture checks
fail if a copied contract or dataset changes without an intentional version update.
There is no automatic synchronization with the demo; later demo changes require
an explicit new evaluation snapshot.
