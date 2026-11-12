"use strict";
const demo = JSON.parse(document.getElementById('demo-data').textContent);
const byId = id => document.getElementById(id);
const currency = n => new Intl.NumberFormat('en-US', {style:'currency',currency:'USD'}).format(n);
let selectedHome = 'C';
let selectedTab = 'usage';

function selectTab(tab, focus = false) {
  selectedTab = tab;
  for (const button of document.querySelectorAll('[data-tab]')) {
    const active = button.dataset.tab === tab;
    button.setAttribute('aria-selected', String(active));
    button.tabIndex = active ? 0 : -1;
    byId(`panel-${button.dataset.tab}`).hidden = !active;
    if (active && focus) button.focus();
  }
}

function renderHome(id) {
  selectedHome = id;
  const r = demo.homes.find(h => h.home.id === id);
  const sample = r.provenance.mode === 'sample';
  const review = r.analysis.needs_review === true;
  const normal = !review && !r.analysis.is_anomaly;
  byId('mode-notice').textContent = review
    ? 'STAFF REVIEW REQUIRED · Analyst findings were saved. The customer email is withheld; calculated usage and invoices remain available.'
    : sample
    ? 'LAYOUT PREVIEW · Real synthetic readings and calculated bills. Explanations are sample templates; no OpenAI model has run yet.'
    : `GENERATED RESULTS · Saved output from ${r.provenance.model}. Synthetic readings, weather and tariffs. Emails have not been sent.`;
  byId('mode-notice').classList.toggle('live', !sample && !review);
  for (const button of document.querySelectorAll('[data-home]')) button.setAttribute('aria-pressed', String(button.dataset.home === id));
  byId('home-size').textContent = `Home ${id} · ${r.home.sqft.toLocaleString()} sq ft · ${demo.locality}`;
  byId('home-plan').textContent = r.invoice.plan_name + ' · Electric heating';
  byId('bill-total').textContent = currency(r.invoice.total);
  byId('bill-change').textContent = `${r.metrics.bill_change} compared with February`;
  byId('daily-usage').textContent = `${(r.invoice.kwh / 31).toFixed(1)} kWh`;
  byId('daily-change').textContent = `${r.metrics.daily_change} per day vs. February · 31 vs. 28 days`;
  const signal = r.signals[0];
  const kinds = {sustained:'Sustained increase',peak:'Peak-hour increase',weather:'Weather association'};
  byId('pattern-title').textContent = signal ? kinds[signal.kind] : 'Within usual pattern';
  byId('pattern-caption').textContent = signal ? (signal.kind === 'weather' ? 'Usage changes coincide with temperature changes' : `From ${r.metrics.signal_start} · ${r.metrics.signal_days} days flagged`) : 'No sustained anomaly detected by simple checks';
  byId('usage-chart').innerHTML = r.chart_html;
  byId('weather-chart').innerHTML = r.weather_chart_html;
  byId('weather-response').textContent = r.weather_response_note;
  byId('neighborhood-content').innerHTML = r.neighborhood_html;
  byId('invoice-content').innerHTML = r.invoice_html;
  byId('email-content').innerHTML = r.email_html;
  byId('finding-headline').textContent = review ? r.resolved_headline : r.customer_insight.title;
  byId('finding-body').textContent = r.resolved_explanation;
  byId('finding-source').textContent = review ? 'STAFF REVIEW REQUIRED · Analyst draft' : 'EVIDENCE-BASED GUIDANCE · Possible causes, not a device diagnosis';
  byId('plan-message').innerHTML = r.plan_insight_html;
  byId('plan-list').replaceChildren();
  for (const plan of r.plans) {
    const row = document.createElement('div'); row.className = 'plan-row';
    const name = document.createElement('span'); name.textContent = plan.plan_name;
    if (plan.plan_id === r.home.plan) { const label = document.createElement('small'); label.textContent = 'CURRENT'; name.append(label); }
    const total = document.createElement('strong'); total.textContent = currency(plan.total);
    row.append(name,total); byId('plan-list').append(row);
  }
  byId('email-link').href = `../emails/${id}-2026-03.html`;
  byId('email-link').textContent = review ? 'View review status ↗' : 'Open customer email ↗';
  byId('analysis-link').href = `../analyses/${id}.json`;
  byId('jan-link').href = `../invoices/${id}-2026-01.html`;
  byId('feb-link').href = `../invoices/${id}-2026-02.html`;
  byId('pipeline-note').textContent = review
    ? `The billing analyst requested staff review. The communications agent was skipped for this home. Open Analyst JSON to inspect its findings. Saved results remain available through --resume; the flag has not been overridden.`
    : sample
    ? `Preview mode: agent steps have not executed. Both agents are configured for ${demo.model} using the OpenAI Agents SDK. Add an API key locally and run live mode to replace sample narratives.`
    : normal ? `The analyst found no fault-like anomaly. Short guidance and plan savings are derived from the validated weather, consumption and tariff features. ${r.provenance.communications_source === 'deterministic_normal' ? 'The communications call was skipped.' : 'Existing saved model copy is retained in the analyst artifacts.'}`
    : `Both agents used ${r.provenance.model} through the OpenAI Agents SDK. The saved model draft is retained; the possible-reason/next-step sentence is rule-based guidance from validated features. This viewer makes no API calls.`;
  document.title = `Home ${id} · Bill clarity · Coruscant Electric`;
  history.replaceState(null,'',`#home=${id}&view=${selectedTab}`);
}

for (const home of demo.homes) {
  const button = document.createElement('button');
  button.textContent = `Home ${home.home.id}`;
  button.dataset.home = home.home.id;
  button.setAttribute('aria-pressed','false');
  button.addEventListener('click', () => renderHome(home.home.id));
  byId('home-buttons').append(button);
}
const tabs = [...document.querySelectorAll('[data-tab]')];
for (const [index, button] of tabs.entries()) {
  button.addEventListener('click', () => { selectTab(button.dataset.tab); renderHome(selectedHome); });
  button.addEventListener('keydown', event => {
    let next;
    if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
    if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
    if (event.key === 'Home') next = 0;
    if (event.key === 'End') next = tabs.length - 1;
    if (next !== undefined) { event.preventDefault(); selectTab(tabs[next].dataset.tab, true); renderHome(selectedHome); }
  });
}
const initial = new URLSearchParams(location.hash.slice(1));
if (['usage','invoice','email'].includes(initial.get('view'))) selectTab(initial.get('view'));
renderHome(demo.homes.some(h => h.home.id === initial.get('home')) ? initial.get('home') : 'C');
