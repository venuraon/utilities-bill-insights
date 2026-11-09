# Electricity bill insights demo

Build a small, local demo that explains a household’s monthly electricity bill before the customer needs to call support. Each explanation should show what changed, the evidence behind it, and one or two practical ways to save.

Sources: `take-home.pages` defines the assignment; `problem-statement.pages` defines the chosen industry, problem, and demo scope.

## Assignment goal and customer context

Prepare a five-minute demo video supported by 3–5 concise slides and a local HTML demo viewer. Demonstrate customer understanding, product judgment, and business value within roughly five hours of preparation. Code and local artifacts support the presentation.

Use a fictional regional electricity utility serving residential customers. The customer-service director sponsors the project, billing operations owns the workflow, and households receive the explanations. The utility issues monthly bills and handles questions about unexpected changes; reducing avoidable billing contacts and improving customer understanding are the primary goals.

Selected product surface: **OpenAI Platform and APIs**, implemented using the OpenAI Agents SDK for Python. Keep the presentation focused on this single surface.

## Current and improved workflow

- Current: invoice arrives → customer sees an unexpected increase → customer calls support → representative investigates usage and charges → customer receives an explanation.
- Improved: billing completes → usage and charges are analyzed → findings are validated → a clear explanation accompanies the invoice → unresolved questions reach support with supporting evidence.

The model connects evidence into a useful explanation and adapts it for the customer. Ordinary code calculates charges, comparisons, and savings. The two agents separate evidence interpretation from customer communication, with validation between them.

## Business-value hypothesis

Lead with fewer bill-explanation calls and clearer customer communication. As an illustrative assumption, avoiding 10% of 10,000 monthly billing calls at eight minutes each would save about 133 support hours per month. These are hypothetical inputs, not measured results or promised savings. The five-home demo demonstrates the workflow, not actual call reduction.

A future pilot should measure billing-contact rate against a baseline or comparison group, explanation accuracy, customer helpfulness ratings, and model cost per explanation. Assess net value after model costs and staff review effort.

## Scope and assumptions

- Five fictional homes of different sizes in Mesa, Arizona, using `America/Phoenix` local time (no daylight-saving transitions during the demo period).
- January 1–March 31, 2026: exactly 90 days. Store readings in UTC to avoid daylight-saving gaps; use local time for pricing and charts.
- One reading every five minutes: 25,920 readings per home, 129,600 total. Store interval consumption in kWh.
- Synthetic weather and electricity prices, clearly labeled as demo data. No live integrations required.
- Publish temperatures in °F throughout the data, semantic summaries and charts. Use a 64.4°F degree-day baseline and label degree-day totals as °F·days. Unit conversion must preserve electricity readings, invoices and anomaly classifications.
- Three fictional plans: two flat-rate plans and one time-of-use plan. Some homes share a plan. Keep plans unchanged during the demo.
- Generate invoices for all three months; explain the latest month using the earlier months as context.
- Two sequential agents: bill analysis, then email composition. Save email previews locally; no delivery integration.
- Show usage, invoices, and generated insights together in a simple local HTML viewer designed for screen recording.
- Use OpenAI models only for both agents, unless the user explicitly changes this requirement later. Start with `gpt-6-luna` for both agents and keep the model name configurable.
- Use the OpenAI Agents SDK for Python with a simple sequential runner.
- The user will provide an API key later through `OPENAI_API_KEY` in the local environment. Do not put credentials in source files or generated HTML. Build data, billing, and the viewer without API access; defer live agent execution and model-access verification until the key is available. Any temporary sample explanations must be labeled as placeholders and replaced with real agent outputs before recording.

## Household scenarios

| Home | Size | Plan | Intended story |
| --- | --- | --- | --- |
| A | 900 sq ft | Flat A | Stable usage and a largely unchanged bill |
| B | 1,300 sq ft | Flat A | Higher heating usage during a cold spell |
| C | 1,800 sq ft | Time-of-use | New, sustained excess consumption consistent with equipment running all day |
| D | 2,400 sq ft | Time-of-use | A mid-cycle baseline step from March 15, alongside increased evening peak usage |
| E | 3,000 sq ft | Flat B | Higher total consumption consistent with its size; no anomaly |

Inject C and D’s anomalies in March. Include daily routines, weekday/weekend differences, weather effects, and modest random variation. Use a fixed random seed so the demo is repeatable. Keep injected scenario labels separate from the analysis inputs so the explanations must follow the readings.

## Implementation plan

### 1. Generate the demo data

Use Python with a small set of dependencies. Write CSV files for homes, plans, interval readings, and daily weather. Keep generation parameters in one configuration file.

Check that every home has the expected reading count, timestamps are unique, and consumption is nonnegative.

### 2. Calculate invoices and supporting evidence

Use ordinary code for billing and numerical analysis. Apply interval rates plus a fixed monthly fee; omit taxes, demand charges, and tariff exceptions.

Generate 15 simple HTML invoices showing billing period, consumption, energy charges, fixed fee, and total. Also save the invoice totals as CSV.

For each home’s March bill, calculate:

- Bill and consumption changes from February, accounting for different month lengths.
- Daily consumption and peak/off-peak consumption where relevant.
- Sustained increases in overnight/base consumption and unusual peak-period usage.
- Historical heating/cooling degree-day fits, correlations, unexplained consumption and extrapolation flags; no raw weather vectors go to the agents.
- Compare March consumption with the average for synthetic neighborhood homes within ±20% of the customer's floor area, sharing the locality, electric heating, and billing period. Generate 60 benchmark neighbors with monthly summaries. Calculate the mean, population standard deviation, relative variance and Z-score; show customer-friendly percentage and bars. Require at least three peers; report null Z-score for zero variance. Home D's additional baseline step intentionally changes its readings and invoice during this hybrid refactor.
- What March would have cost under each of the other demo plans, using the same readings. Recommend a switch only when the calculated bill is lower.

Save all per-home Semantic Feature Summaries in `output/data/semantic_summaries.json` before any model call. Include exact decimal energy/billing values, peer statistics, weather features, minimum-draw/step-change flags, and registered plan costs. Raw intervals, daily vectors and chart data remain in the deterministic layer and renderer. Keep likely causes distinct from confirmed facts.

### 3. Agent 1: analyze the bill

Give the analyst only the Semantic Feature Summary. Use the strict Pydantic `BillingAnalysis` schema in `models.py`, including `is_anomaly`, `anomaly_type`, `mathematical_justification`, `peer_comparison_status`, `recommended_plan`, and explanation/actions. Validate classifications and mathematical values against the summary and verify that any recommendation is the lowest-cost registered plan with supported savings. Return structured findings containing:

- The main reasons for the bill change, with supporting figures.
- Any anomalies, their timing, and plausible explanations.
- Uncertainty where the readings cannot establish the cause.
- One or two relevant actions and any calculated plan savings.

The agent interprets evidence; billing code supplies all totals and savings figures. Avoid claiming that meter readings identify a particular appliance.

### 4. Agent 2: compose the email

Give the second agent the validated findings. Have it write a short subject, headline, one-sentence explanation, and exactly one action. Limit headline, explanation, and action to 45 words combined and enforce those limits in code. Render a visual email rather than a long narrative:

- Current bill and change from last month.
- A two-bar comparison of February and March bills, with daily usage change and billing-day counts displayed separately.
- A focused March usage plot with any detected change labeled directly on the chart. Keep the full 90-day chart in the evidence view.
- One short explanation of the likely driver, preserving uncertainty.
- Neighborhood consumption bars with the percentage difference, matched group size, and size band. Keep methodology notes brief.
- Exactly one practical next step and a compact plan-saving card only when supported. Avoid repeating chart values in paragraphs.

Generate charts in code and embed them in the HTML so previews open without an image server. Save one March email per home.

Use the OpenAI Agents SDK for Python with strict Structured Outputs on both agents. Pass only validated analyst JSON to the communications agent. Apply trusted inline styles and calculated plots in code alongside its short text. Target one model call per agent per home; permit two homes concurrently while preserving sequential validation within each home. Cache results using summary, prompt, schema and model fingerprints. No memory system or autonomous orchestration is needed. Both agents must use OpenAI models unless the user changes that requirement.

### 5. Build the local demo viewer

Generate a local `demo.html` using plain HTML, CSS, and minimal JavaScript. Open it directly in a browser; embed the display data and charts so no backend, deployment, or external assets are required. This viewer presents the workflow and its outputs; the selected OpenAI product surface remains OpenAI Platform and APIs.

Include a home selector, defaulting to Home C, and three clearly labeled sections with simple navigation:

- **Usage:** 90-day daily consumption chart, the latest month's consumption change, and relevant anomaly evidence.
- **Invoice:** March charge breakdown and comparison with February; links to the other monthly invoices.
- **Insights email:** the rendered customer email with its explanation, visual, and suggested actions.

Use large, readable text and charts suitable for video. Switching homes updates all three sections. Keep household evidence in the presenter view; the customer email contains only its intended customer-facing information.

Run the actual agent pipeline before recording and show its saved outputs. Label these as generated results. Regeneration happens through the Python command, not a browser button; the recording does not depend on waiting for model calls.

### 6. Package and verify

Provide one command to generate data, calculate bills, and produce the reports, plus a short README describing setup and the synthetic assumptions. Save agent outputs so the finished demo can be reviewed without another model call.

Keep verification focused:

- Check a known flat-rate calculation and a time-of-use calculation, including local peak-hour boundaries.
- Reconcile invoice totals with readings and rates.
- Confirm C and D’s injected anomalies appear in the analysis, while stable homes are not described as equipment failures.
- Check that each email’s bill, changes, and savings agree with the calculated evidence.
- Open the HTML outputs and check chart rendering and readability.
- Open `demo.html` directly, switch between homes, and confirm the usage, invoice, and email all belong to the selected home and are readable at the recording resolution.

### 7. Prepare the customer story and slides

Create five concise slides covering the customer and pain, improved workflow and selected surface, demo flow, expected value, and assumptions with next steps. Use one workflow diagram and the actual demo artifacts. Make the model's contribution and the responsibility of each agent clear without spending presentation time on implementation details.

### 8. Rehearse and record the demo video

Make Home C the main story: unexpected bill → sustained usage increase → supporting evidence → clear customer email and suggested equipment check. Briefly show Home A to demonstrate that normal usage does not produce a manufactured problem. Keep Homes B and D available for questions.

Rehearse the complete presentation to five minutes, moving from the opening slides to the local viewer and back to value and next steps. Record a narrated screen capture and review the exported video for duration, readable text, and clear audio. Prepare screenshots as a fallback. Clearly distinguish a live run from saved results; the default recording uses real saved agent outputs.

## Governance, limitations, and next steps

- Use synthetic data only for this assignment and label it throughout the artifacts.
- Give each agent only the current household's evidence and aggregate peer comparisons; do not include other households' individual records in its input or customer email.
- Validate numerical claims against calculated evidence before rendering the final explanation. Flag unsupported or inconsistent findings for review.
- Describe equipment causes as possibilities and weather relationships as associations. The meter readings cannot identify a specific appliance.
- Treat the synthetic neighborhood benchmark as illustrative. Match on size, locality, billing period, and electric heating; occupancy and equipment remain unknown. Do not describe the comparison as an efficiency rating.
- Explain that a real pilot would start with staff-reviewed drafts and route unsupported explanations to review. Email delivery remains outside this demo.
- Before a production rollout, confirm customer-data access controls and retention requirements, real tariff eligibility, and the pilot's quality and business results.

## Demo walkthrough

| Time | Presentation content |
| --- | --- |
| 45 seconds | Customer, business problem, and current workflow |
| 45 seconds | Improved workflow and OpenAI Platform and APIs |
| 2 minutes | Home C: invoice → evidence → email; brief Home A comparison |
| 45 seconds | Expected value and pilot measurements |
| 45 seconds | Assumptions, limitations, and next steps |

Keep all five homes in the data, but focus presentation time on one trustworthy explanation. Additional scenarios support questions afterward.

## Deliverables

- Five concise slides covering the assignment's customer, workflow, product surface, demo flow, expected value, assumptions, and next steps.
- A narrated five-minute demo video with saved outputs or screenshots as a fallback.
- A local `demo.html` viewer showing usage, invoices, and generated insights emails for all five homes.
- Repeatable synthetic data generator and sample CSV data.
- Billing and evidence calculation code.
- Two agent prompts and a simple sequential runner.
- Fifteen monthly invoices and five March email previews.
- README with setup, demo command, and limitations.

## Preparation budget and priorities

Target roughly five hours: 30 minutes for customer framing and scenario design, 60 minutes for data and billing, 60 minutes for agents and email rendering, 30 minutes for the local viewer, 30 minutes for focused verification, and 90 minutes for slides, rehearsal, and recording.

Prioritize a complete Home C explanation, accurate figures, a readable visual, and the business story. Keep invoice styling minimal and peer comparisons simple. If time is tight, reduce presentation polish and secondary analysis detail before adding features; preserve time for the deck and rehearsal.

## Keep out of this version

No full dashboard application, accounts, database, cloud deployment, live weather, real utility tariffs, email sending, machine-learning anomaly models, or additional orchestration frameworks. The local HTML viewer is limited to presenting the generated demo artifacts.

No clarification is required to start with these assumptions. The initial model is `gpt-6-luna`; using another provider requires the user to change the OpenAI-only requirement. Live API calls will wait until the user supplies their API key.

## Implementation status

- Built: hybrid standard-Python analytics in `data_factory.py`, validated registries in `config.py`, strict schemas in `models.py`, and the summary → analyst JSON → communications pipeline in `agents.py`. Integer energy aggregation and monthly Decimal pricing replace repeated per-interval tariff arithmetic. The viewer, 15 invoices, inline-styled visual emails, slides and recording guide remain available.
- Generated: all five homes' local artifacts using sample mode. Explanation copy is explicitly labeled as template placeholders; no API calls have been made.
- Verification: all 41 tests pass in `.venv`, including real Pydantic/SDK schema checks, numerical and data-quality tests, mocked agent boundaries, and email rewrite/resume regression cases. JavaScript checks cover all five homes, tabs and links. Tests make no API calls.
- Live-run repair: short-copy violations trigger at most two communications-only rewrites with measured word-count feedback. Validated analyst checkpoints are saved before the email call. `--mode live --resume` reuses completed homes and matching checkpoints; normal live mode still requests fresh outputs. Additional rewrite calls may be billed. One existing completed household cache was verified as reusable.
- Review handling: the revised analyst prompt permits qualified explanations of measured anomalies even when the appliance cause is unknown or the weather fit is weak/extrapolated. Review is reserved for missing required facts, conflicting calculations, or unsupported necessary claims. Any returned review flag is still respected. Previous-policy approved caches are revalidated and migrated with provenance; previous-policy held drafts get fresh analysis, never an edited flag. Current-policy holds remain held on resume. This lets the C/D anomaly scenarios proceed to the communications agent when the model returns a supported qualified draft, without household-specific exceptions.
- Customer usefulness: every cleared home gets a brief possible-reason/next-step sentence and a plan-cost outcome. Stable A/E keep billing-day context; B's weather-associated rise gets thermostat guidance; C/D get qualified equipment or schedule checks. Display validated savings for any home, independently of anomaly status (currently A/B/D); show the already-lowest-cost outcome for C/E. Suggestions are rule-based and labeled as guidance, while original model drafts stay in analyst artifacts. Normal homes still skip the communications call, and held drafts remain withheld. “In line with neighbors” requires the calculated typical peer band.
- Shared-weather display: all five homes use the same daily Mesa temperatures. Display that shared series in its own °F chart with an identical scale for every home. Label the dashed curve as estimated electricity consumption (kWh/day), and identify Home B's greater heating sensitivity as a simulation assumption. Assert locality weather consistency and preserve the underlying data and cached insights.
- Pending: successful completion of the user's full live run after the word-limit fix; review of real model explanations; browser visual review; rehearsal and recording of the narrated five-minute video. A previous headless Chrome launch could not start in this environment.
