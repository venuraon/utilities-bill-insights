"""Render portable HTML and SVG, with escaped agent text and no remote assets."""
import html
import json
import math
import os
import statistics
from pathlib import Path
from urllib.parse import quote
from .agents import resolve
from .email_layout import inline_email_styles
from .customer_insights import customer_insight


def esc(value):
    return html.escape(str(value), quote=True)


def chart_svg(daily):
    width, height = 1000, 280
    left, top, bottom, right = 44, 22, 242, 982
    maximum = max(max(d["kwh"], d["expected_kwh"]) for d in daily) * 1.15
    maximum = max(10, ((int(maximum) // 10) + 1) * 10)
    step = (right - left) / len(daily)
    def y(value):
        return bottom - value / maximum * (bottom - top)
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Daily electricity consumption and estimated electricity consumption in kWh per day" xmlns="http://www.w3.org/2000/svg">',
             '<title>Daily electricity usage, January–March 2026</title>',
             f'<rect x="{left + 59 * step:.1f}" y="{top}" width="{31 * step:.1f}" height="{bottom - top}" fill="#edf1f5" rx="5"/>']
    for i in range(5):
        value = maximum * i / 4
        parts.append(f'<line x1="{left}" x2="{right}" y1="{y(value):.1f}" y2="{y(value):.1f}" stroke="#e4e8e2"/><text x="{left-9}" y="{y(value)+4:.1f}" text-anchor="end" fill="#757a80" font-size="11">{value:.0f}</text>')
    baseline = []
    for i, row in enumerate(daily):
        x = left + i * step
        high = row["date"] >= "2026-03-01" and row["kwh"] > row["expected_kwh"] * 1.25 + 4
        color = "#cc7945" if high else "#345677"
        parts.append(f'<rect x="{x + 1:.1f}" y="{y(row["kwh"]):.1f}" width="{step - 2:.1f}" height="{bottom - y(row["kwh"]):.1f}" rx="2" fill="{color}"><title>{row["date"]}: {row["kwh"]:.1f} kWh; expected {row["expected_kwh"]:.1f} kWh; {row["temp_f"]:.1f} °F</title></rect>')
        baseline.append(f'{x+step/2:.1f},{y(row["expected_kwh"]):.1f}')
    parts.append(f'<polyline points="{" ".join(baseline)}" fill="none" stroke="#19242f" stroke-width="2" stroke-dasharray="5 4"/>')
    for index, label in [(0, "JAN 01"), (31, "FEB 01"), (59, "MAR 01"), (89, "MAR 31")]:
        anchor = 'end' if index == 89 else 'start'
        parts.append(f'<text x="{left+index*step:.1f}" y="267" fill="#757a80" font-size="11" text-anchor="{anchor}">{label}</text>')
    parts.append('<text x="4" y="12" fill="#757a80" font-size="11">kWh / day</text></svg>')
    return "".join(parts)


def shared_weather_svg(daily, locality):
    """One locality temperature series, with exactly the same scale for every home."""
    left, right, top, bottom = 44, 982, 30, 146
    temperatures = [r['temp_f'] for r in daily]
    low = math.floor((min(temperatures) - 5) / 10) * 10
    high = math.ceil((max(temperatures) + 5) / 10) * 10
    def x(index):
        return left + (right-left) * index / max(1, len(daily)-1)
    def y(value):
        return bottom - (value-low) / (high-low) * (bottom-top)
    label = f'{locality}: shared daily mean temperature in degrees Fahrenheit for all five homes'
    parts = [f'<svg viewBox="0 0 1000 180" role="img" aria-label="{esc(label)}" xmlns="http://www.w3.org/2000/svg"><title>{esc(label)}</title>']
    for i in range(4):
        value = low + (high-low) * i / 3
        parts.append(f'<line x1="{left}" x2="{right}" y1="{y(value):.1f}" y2="{y(value):.1f}" stroke="#e4e8e2"/><text x="{left-8}" y="{y(value)+4:.1f}" text-anchor="end" fill="#657e86" font-size="11">{value:.0f}°</text>')
    history = [r['temp_f'] for r in daily if r['date'] < '2026-03-01']
    threshold = statistics.mean(history) - 3.6 if history else min(temperatures)
    cold = [i for i, r in enumerate(daily) if r['date'] >= '2026-03-01' and r['temp_f'] < threshold]
    if cold:
        # Highlight the longest continuous cold period, independently of any household's usage.
        runs = []
        for index in cold:
            if not runs or index != runs[-1][-1] + 1:
                runs.append([])
            runs[-1].append(index)
        spell = max(runs, key=len)
        start, end = x(spell[0]), x(min(spell[-1]+1, len(daily)-1))
        parts.append(f'<rect x="{start:.1f}" y="{top}" width="{end-start:.1f}" height="{bottom-top}" fill="#dcebf3"/><text x="{start+5:.1f}" y="20" fill="#426b82" font-size="12">Shared cold spell</text>')
    points = ' '.join(f'{x(i):.1f},{y(r["temp_f"]):.1f}' for i, r in enumerate(daily))
    parts.append(f'<polyline points="{points}" fill="none" stroke="#397da2" stroke-width="2.5" stroke-linejoin="round"/>')
    for index, row in enumerate(daily):
        if row['date'].endswith('-01') or index == len(daily)-1:
            label = row['date'][5:].replace('-', '/')
            parts.append(f'<text x="{x(index):.1f}" y="170" text-anchor="middle" fill="#757a80" font-size="11">{label}</text>')
    parts.append('<text x="4" y="16" fill="#426b82" font-size="11">°F</text></svg>')
    return ''.join(parts)


def weather_response_note(home, config):
    sensitivities = [h['heat_sensitivity_kw'] for h in config['homes']]
    stronger = home['heat_sensitivity_kw'] > 3 * statistics.median(sensitivities)
    response = 'a stronger' if stronger else 'a lower'
    return (f"Same Coruscant weather as every other home. Demo assumption: this home has {response} "
            'electric-heating response to cold temperatures. Building characteristics are simulated, not measured.')


def invoice_html(invoice, config):
    p = next(p for p in config["plans"] if p["id"] == invoice["plan_id"])
    rows = [("Off-peak energy", f"{invoice['off_peak_kwh']:,.1f} kWh × ${p['off_peak_rate']:.3f}", invoice["off_peak_charge"]),
            ("Peak-period energy", f"{invoice['peak_kwh']:,.1f} kWh × ${p['peak_rate']:.3f}", invoice["peak_charge"]),
            ("Monthly service fee", "Fixed fee", invoice["monthly_fee"])]
    body = "".join(f'<tr><td>{esc(label)}<small>{esc(detail)}</small></td><td>${amount:,.2f}</td></tr>' for label, detail, amount in rows)
    return f'''<article class="invoice-sheet"><div class="document-head"><div><span class="eyebrow">CORUSCANT ELECTRIC</span><h2>Electricity statement</h2></div><span class="document-pill">DEMO INVOICE</span></div>
    <div class="invoice-meta"><div>Account<strong>Home {esc(invoice['home_id'])}</strong></div><div>Billing period<strong>{esc(invoice['month'])}</strong></div><div>Electricity plan<strong>{esc(invoice['plan_name'])}</strong></div></div>
    <table class="charges"><thead><tr><th scope="col">Description</th><th scope="col">Amount</th></tr></thead><tbody>{body}</tbody><tfoot><tr><th scope="row">Total bill</th><td>${invoice['total']:,.2f}</td></tr></tfoot></table>
    <p class="fine-print">Fictional tariffs · weekday peak hours: {config['peak_start_hour']}:00–{config['peak_end_hour']}:00, Arizona local time. Taxes and tariff exceptions omitted. Peak/off-peak labels describe timing even on flat plans.</p></article>'''


def neighborhood_html(comparison):
    heading = '<span class="eyebrow">NEIGHBORHOOD COMPARISON</span><h2>How you compare nearby</h2>'
    insight = f'<p class="neighborhood-insight">{esc(comparison["insight"])}</p>'
    if not comparison["available"]:
        return f'<section class="panel neighborhood-panel">{heading}{insight}</section>'
    maximum = max(comparison["home_kwh"], comparison["peer_kwh"], 1)
    bars = ''
    for label, value, kind in [('Your home', comparison['home_kwh'], 'your-home'),
                               ('Similar-sized homes', comparison['peer_kwh'], 'nearby-homes')]:
        bars += f'<div class="benchmark-row"><span>{label}</span><div class="benchmark-track" aria-hidden="true"><div class="benchmark-fill {kind}" style="width:{value / maximum * 100:.2f}%"></div></div><strong>{value:,.1f} kWh</strong></div>'
    return f'<section class="panel neighborhood-panel">{heading}{insight}<div class="benchmark-bars">{bars}</div><p class="fine-print">{esc(comparison["note"])}</p></section>'


def pair_bars_svg(rows, label):
    """Two quantities on a shared zero-based scale, with direct value labels."""
    maximum = max(*(row[1] for row in rows), 1)
    parts = [f'<svg class="pair-bars" viewBox="0 0 580 94" role="img" aria-label="{esc(label)}" xmlns="http://www.w3.org/2000/svg"><title>{esc(label)}</title>']
    for index, (name, value, value_label, color) in enumerate(rows):
        y = 12 + index * 43
        parts.append(f'<text x="0" y="{y + 17}" fill="#5e666e" font-size="13">{esc(name)}</text><rect x="123" y="{y}" width="335" height="24" rx="4" fill="#e7ecf1"/><rect x="123" y="{y}" width="{value / maximum * 335:.2f}" height="24" rx="4" fill="{color}"/><text x="577" y="{y + 17}" text-anchor="end" fill="#212d39" font-size="15" font-weight="600">{esc(value_label)}</text>')
    return ''.join(parts) + '</svg>'


def email_usage_svg(report):
    """A focused March plot with the detected change labeled on the chart."""
    days = [d for d in report['daily'] if d['date'].startswith('2026-03')]
    left, right, top, bottom = 35, 565, 34, 150
    maximum = max(max(d['kwh'], d['expected_kwh']) for d in days) * 1.18
    def x(index):
        return left + (right - left) * index / max(len(days) - 1, 1)
    def y(value):
        return bottom - value / maximum * (bottom - top)
    signal = next(iter(report['signals']), None)
    label = 'March daily usage with weather-adjusted estimate'
    parts = [f'<svg class="email-usage-plot" viewBox="0 0 580 190" role="img" aria-label="{label}" xmlns="http://www.w3.org/2000/svg"><title>{label}</title>']
    for value in (0, maximum / 2, maximum):
        parts.append(f'<line x1="{left}" x2="{right}" y1="{y(value):.1f}" y2="{y(value):.1f}" stroke="#dce2e9"/><text x="{left - 7}" y="{y(value) + 4:.1f}" text-anchor="end" fill="#6c7681" font-size="10">{value:.0f}</text>')
    if signal:
        index = next((i for i, d in enumerate(days) if d['date'] == signal['start']), 0)
        start_x = x(index)
        if signal['kind'] == 'weather':
            end_x = x(min(index + signal['days'], len(days) - 1))
            annotation = 'Warm spell' if signal.get('driver') == 'cooling' else 'Cold spell'
        else:
            end_x = right
            annotation = f"Mar {int(signal['start'][-2:])} · pattern change"
        parts.append(f'<rect x="{start_x:.1f}" y="{top}" width="{end_x-start_x:.1f}" height="{bottom-top}" fill="#c47b42" opacity="0.09"/><line x1="{start_x:.1f}" x2="{start_x:.1f}" y1="{top}" y2="{bottom}" stroke="#b57645" stroke-dasharray="3 3"/><text x="{min(start_x + 5, right - 150):.1f}" y="20" fill="#986039" font-size="12">{esc(annotation)}</text>')
    for key, color, dash in [('expected_kwh', '#7b8692', '5 4'), ('kwh', '#345677', '')]:
        points = ' '.join(f'{x(i):.1f},{y(d[key]):.1f}' for i, d in enumerate(days))
        parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="{2 if dash else 3}" stroke-dasharray="{dash}" stroke-linejoin="round"/>')
    for index in (0, 7, 14, 21, 30):
        parts.append(f'<text x="{x(index):.1f}" y="172" text-anchor="middle" fill="#6c7681" font-size="10">Mar {index+1}</text>')
    parts.append('<text x="0" y="20" fill="#6c7681" font-size="10">kWh/day</text></svg>')
    return ''.join(parts)


def email_neighborhood_html(comparison, *, normal=False):
    if not comparison['available']:
        return '<section class="visual-neighborhood"><h3>How you compare nearby</h3><p>Not enough similar-sized homes to compare.</p></section>'
    bars = pair_bars_svg([
        ('Your home', comparison['home_kwh'], f"{comparison['home_kwh']:,.0f} kWh", '#345677'),
        ('Similar homes', comparison['peer_kwh'], f"{comparison['peer_kwh']:,.0f} kWh", '#92a3b4'),
    ], 'March consumption: your home versus the average for similar-sized neighborhood homes')
    badge = 'In line with neighbors' if normal and comparison.get('status') == 'typical' else f"{abs(comparison['difference_pct']):.1f}% {esc(comparison['direction'])}"
    return f'''<section class="visual-neighborhood"><div class="visual-section-head"><h3>How you compare nearby</h3><span class="comparison-badge">{badge}</span></div>{bars}<p class="micro-note">{comparison['count']} neighborhood homes of similar size · {comparison['min_sqft']:,}–{comparison['max_sqft']:,} sq ft · Electric heating</p></section>'''


def normal_snapshot_text(report):
    change = report['daily_change_pct']
    usage = f"Average daily consumption {'increased' if change >= 0 else 'decreased'} {abs(change):.1f}% compared with February."
    peer = report['neighborhood']
    if peer.get('status') == 'typical':
        return usage + ' Consumption is in line with similar-sized homes nearby.'
    if peer['available']:
        return usage + f" March consumption was {abs(peer['difference_pct']):.1f}% {peer['direction']} than similar-sized homes nearby."
    return usage + ' A similar-home comparison is unavailable.'


def plan_insight_html(report):
    """Every cleared home gets a plan outcome, independently of anomaly status."""
    recommendation = report['analysis']['recommended_plan']
    if recommendation is None:
        cheapest = min(report['plans'], key=lambda p: (p['total'], p['plan_id']))
        if cheapest['savings'] > .5:
            raise ValueError('A supported plan saving is missing from the validated recommendation')
        if cheapest['savings'] > 0:
            return f'<div class="visual-plan"><span>Your current plan costs about the same.<small>Best alternative is ${cheapest["savings"]:.2f} less for identical March usage.</small></span></div>'
        return '<div class="visual-plan"><span>Your current plan is already lowest cost.<small>Compared with registered demo plans for the same March usage.</small></span></div>'
    plan_id = recommendation['plan_id']
    calculated = next(p for p in report['plans'] if p['plan_id'] == plan_id)
    return f'''<div class="visual-plan"><strong>${calculated['savings']:.2f} less</strong><span>Consider {esc(calculated['plan_name'])}<small>March bill: ${report['invoice']['total']:.2f} → ${calculated['total']:.2f}</small><small>Same usage · Check eligibility · Future savings may differ</small></span></div>'''


def guidance_html(report):
    insight = customer_insight(report)
    return f'<section class="visual-action"><span class="action-mark" aria-hidden="true">↗</span><div><span class="eyebrow">POSSIBLE REASON &amp; NEXT STEP</span><p>{esc(insight["sentence"])}</p></div></section>'


def email_html(report, config, chart):
    if report['analysis']['needs_review']:
        return inline_email_styles('''<article class="email-sheet visual-email"><div class="visual-email-body"><span class="eyebrow">STAFF REVIEW REQUIRED</span><h2>Customer email withheld</h2><p>The analyst requested staff review. Its findings are saved in the analyst JSON and review queue.</p><p>No customer email was generated for this home. Usage charts and invoices remain available.</p></div></article>''')
    email = report["email"]
    def text(value):
        return esc(resolve(value, report["metrics"]))
    sample = '<div class="sample-note">SAMPLE EXPLANATION · No model calls.</div>' if report["provenance"]["mode"] == "sample" else ''
    current, previous = report['invoice']['total'], report['previous_invoice']['total']
    bill_bars = pair_bars_svg([('February', previous, f'${previous:,.2f}', '#92a3b4'),
                               ('March', current, f'${current:,.2f}', '#345677')], 'February and March electricity bills in dollars')
    insight = customer_insight(report)
    guidance = guidance_html(report)
    plan_note = plan_insight_html(report)
    if not report['analysis']['is_anomaly']:
        return inline_email_styles(f'''{sample}<article class="email-sheet visual-email"><div class="mail-meta"><span>{esc(config['utility'])} → home-{esc(report['home']['id'].lower())}@example.com</span><strong>Your March electricity snapshot</strong></div><div class="visual-email-body"><header class="visual-email-header"><span class="eyebrow">MARCH 2026</span><h2>Your March snapshot.</h2><p class="visual-caption">{esc(insight['title'])}.</p></header><section class="visual-bill"><div class="visual-section-head"><h3>Your electricity bill</h3><span>{esc(report['metrics']['bill_change'])} vs February</span></div>{bill_bars}<div class="visual-stats"><span><strong>{esc(report['metrics']['daily_change'])}</strong> daily consumption</span><span><strong>{esc(report['metrics']['feb_days'])} → {esc(report['metrics']['march_days'])}</strong> billing days</span></div></section>{guidance}{email_neighborhood_html(report['neighborhood'], normal=True)}{plan_note}<footer class="micro-note">Synthetic demo · Similar-size comparison · Occupancy and equipment may differ.</footer></div></article>''')
    return inline_email_styles(f'''{sample}<article class="email-sheet visual-email"><div class="mail-meta"><span>{esc(config['utility'])} → home-{esc(report['home']['id'].lower())}@example.com</span><strong>{text(email['subject'])}</strong></div>
    <div class="visual-email-body"><header class="visual-email-header"><span class="eyebrow">CORUSCANT ELECTRIC · MARCH 2026</span><h2>Your bill, explained.</h2></header>
    <section class="visual-bill"><div class="visual-section-head"><h3>Your electricity bill</h3><span>{esc(report['metrics']['bill_change'])} vs February</span></div>{bill_bars}
    <div class="visual-stats"><span><strong>{esc(report['metrics']['daily_change'])}</strong> daily usage</span><span><strong>{esc(report['metrics']['feb_days'])} → {esc(report['metrics']['march_days'])}</strong> billing days</span></div></section>
    <section class="visual-pattern"><h3>{text(email['intro'])}</h3>{email_usage_svg(report)}<div class="visual-legend"><span><i class="actual-key"></i> Your usage</span><span><i class="estimate-key"></i> Estimated usage (kWh/day)</span></div></section>
    {email_neighborhood_html(report['neighborhood'])}
    {guidance}{plan_note}
    <footer class="micro-note">Synthetic demo · Preview only · Occupancy and equipment not matched.</footer></div></article>''')


def document(title, body, style):
    return f'<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title><style>{style}</style><body class="standalone">{body}</body></html>'


def render(config, reports, invoices, output, web, demo_assets=None):
    output = Path(output)
    reference = [(r['date'], r['temp_f']) for r in reports[0]['daily']]
    if any([(r['date'], r['temp_f']) for r in report['daily']] != reference for report in reports[1:]):
        raise ValueError('Homes in the same locality must use identical daily weather')
    weather_chart = shared_weather_svg(reports[0]['daily'], config['locality'])
    (output / "invoices").mkdir(parents=True, exist_ok=True)
    (output / "emails").mkdir(exist_ok=True)
    style = (web / "style.css").read_text() + (web / "neighborhood.css").read_text() + (web / "email.css").read_text() + (web / 'weather.css').read_text()
    for invoice in invoices:
        body = invoice_html(invoice, config)
        (output / "invoices" / f"{invoice['home_id']}-{invoice['month']}.html").write_text(document("Demo electricity invoice", body, style))
    display = []
    for report in reports:
        chart = chart_svg(report["daily"])
        email = email_html(report, config, chart)
        (output / "emails" / f"{report['home']['id']}-2026-03.html").write_text(document("Your March bill explained", email, style))
        insight = customer_insight(report)
        display.append({**report, "chart_html": chart, "neighborhood_html": neighborhood_html(report["neighborhood"]), "invoice_html": invoice_html(report["invoice"], config),
                        "weather_chart_html": weather_chart, "weather_response_note": weather_response_note(report['home'], config),
                        "customer_insight": insight,
                        "plan_insight_html": '<p class="fine-print">Review pending; calculated plan costs are shown below.</p>' if report['analysis']['needs_review'] else plan_insight_html(report),
                        "email_html": email, "resolved_headline": resolve(report["analysis"]["headline"], report["metrics"]),
                        "resolved_explanation": resolve(report["analysis"]["explanation"], report["metrics"]) if report['analysis']['needs_review'] else insight['sentence']})
    data = json.dumps({"utility": config["utility"], "model": config["model"], "locality": config["locality"], "homes": display})
    data = data.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    demo_assets = Path(demo_assets) if demo_assets is not None else output / "demo_assets"
    demo_assets.mkdir(parents=True, exist_ok=True)
    output_link = quote(Path(os.path.relpath(output, demo_assets)).as_posix(), safe='/')
    def link_output(text):
        for folder in ('data', 'analyses', 'emails', 'invoices'):
            text = text.replace(f'../{folder}/', f'{output_link}/{folder}/')
        return text
    template = link_output((web / "viewer.html").read_text())
    script = link_output((web / "viewer.js").read_text())
    page = template.replace("__STYLE__", style).replace("__DATA__", data).replace("__SCRIPT__", script)
    (demo_assets / "demo.html").write_text(page)
    (demo_assets / "slides.html").write_text(link_output((web / "slides.html").read_text()))
