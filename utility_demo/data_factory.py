"""Deterministic telemetry -> semantic features. No LLM/SDK dependency.

Standard Python computes all numbers once. Raw intervals and daily vectors stay
in this layer; only the scalar semantic summary crosses the agent boundary.
"""
import csv
import json
import math
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from .billing import money
from .billing import calculate
from .data import write_csv


def correlation(x, y):
    mx, my = statistics.mean(x), statistics.mean(y)
    xx = sum((v - mx) ** 2 for v in x)
    yy = sum((v - my) ** 2 for v in y)
    if xx < 1e-12 or yy < 1e-12:
        return None
    return max(-1, min(1, sum((a - mx) * (b - my) for a, b in zip(x, y)) / math.sqrt(xx * yy)))


def degree_fit(rows, key='kwh', base=64.4):
    """Small nonnegative heating/cooling regression; no training on the target month."""
    h = [max(0, base - r['temp_f']) for r in rows]
    c = [max(0, r['temp_f'] - base) for r in rows]
    y = [r[key] for r in rows]
    mh, mc, my = statistics.mean(h), statistics.mean(c), statistics.mean(y)
    hh = sum((v - mh) ** 2 for v in h)
    cc = sum((v - mc) ** 2 for v in c)
    hc = sum((a - mh) * (b - mc) for a, b in zip(h, c))
    hy = sum((a - mh) * (b - my) for a, b in zip(h, y))
    cy = sum((a - mc) * (b - my) for a, b in zip(c, y))
    slopes = [(0.0, 0.0)]
    if hh > 1e-10:
        slopes.append((max(0, hy / hh), 0.0))
    if cc > 1e-10:
        slopes.append((0.0, max(0, cy / cc)))
    determinant = hh * cc - hc * hc
    if determinant > 1e-10:
        sh, sc = (hy * cc - cy * hc) / determinant, (cy * hh - hy * hc) / determinant
        if sh >= 0 and sc >= 0:
            slopes.append((sh, sc))
    def error(pair):
        sh, sc = pair
        intercept = my - sh * mh - sc * mc
        return sum((actual - (intercept + sh * a + sc * b)) ** 2 for a, b, actual in zip(h, c, y))
    sh, sc = min(slopes, key=error)
    variance = sum((v - my) ** 2 for v in y)
    return {'intercept': my - sh * mh - sc * mc, 'heating_slope': sh, 'cooling_slope': sc,
            'r_squared': max(0, 1 - error((sh, sc)) / variance) if variance > 1e-10 else None, 'base_f': base}


def predict(fit, row):
    return max(0, fit['intercept'] + fit['heating_slope'] * max(0, fit['base_f'] - row['temp_f'])
               + fit['cooling_slope'] * max(0, row['temp_f'] - fit['base_f']))


def weather_features(history, march, config):
    base = config.get('analytics', {}).get('degree_day_base_f', 64.4)
    fit = degree_fit(history, base=base)
    expected = sum(predict(fit, r) for r in march)
    actual = sum(r['kwh'] for r in march)
    residual_pct = (actual / expected - 1) * 100 if expected > 0 else 0
    feb = [r for r in history if r['date'].startswith('2026-02')]
    shift = statistics.mean(predict(fit, r) for r in march) - statistics.mean(predict(fit, r) for r in feb)
    all_rows = history + march
    hdd = [max(0, base - r['temp_f']) for r in all_rows]
    cdd = [max(0, r['temp_f'] - base) for r in all_rows]
    values = [r['kwh'] for r in all_rows]
    material = config.get('analytics', {}).get('material_residual_pct', 20)
    if residual_pct > material:
        association = 'decoupled'
    elif (fit['r_squared'] or 0) >= .5 and abs(shift) > avg(feb, 'kwh') * .08:
        association = 'heating' if fit['heating_slope'] >= fit['cooling_slope'] else 'cooling'
    else:
        association = 'not_evident'
    def rounded(value):
        return round(value, 4) if value is not None else None
    features = {
        'base_temperature_f': base, 'degree_day_unit': '°F·days', 'heating_degree_days': round(sum(max(0, base - r['temp_f']) for r in march), 3),
        'cooling_degree_days': round(sum(max(0, r['temp_f'] - base) for r in march), 3),
        'temperature_usage_correlation': rounded(correlation([r['temp_f'] for r in all_rows], values)),
        'heating_usage_correlation': rounded(correlation(hdd, values)),
        'cooling_usage_correlation': rounded(correlation(cdd, values)),
        'historical_fit_r_squared': rounded(fit['r_squared']), 'historical_fit_days': len(history),
        'expected_kwh': round(expected, 6), 'unexplained_kwh': round(actual - expected, 6),
        'unexplained_pct': round(residual_pct, 3), 'association': association,
        'temperature_outside_training_range': any(r['temp_f'] < min(h['temp_f'] for h in history)
                                                  or r['temp_f'] > max(h['temp_f'] for h in history) for r in march),
    }
    return features, fit


def anomaly_features(history, march, config):
    settings = config.get('analytics', {})
    base = settings.get('degree_day_base_f', 64.4)
    p10_fit = degree_fit(history, 'p10_kw', base)
    floor_fit = degree_fit(history, 'min_kw', base)
    residuals = [r['p10_kw'] - predict(p10_fit, r) for r in history]
    sigma = statistics.pstdev(residuals)
    step_threshold = max(settings.get('step_increase_kw', .4), 4 * sigma)
    minimum_days = int(settings.get('minimum_signal_days', 3))
    current_residuals = [r['p10_kw'] - predict(p10_fit, r) for r in march]
    constant = [r for r in march if r['min_kw'] - predict(floor_fit, r) >= settings.get('constant_draw_increase_kw', 1.2)]
    step = None
    for index in range(len(march) - minimum_days + 1):
        before = (residuals + current_residuals[:index])[-7:]
        after = current_residuals[index:index + minimum_days]
        if (all(v - statistics.median(before) > step_threshold for v in after)
                and sum(v > step_threshold for v in current_residuals[index:]) / len(current_residuals[index:]) >= .8):
            step = {'start': march[index]['date'], 'increase_kw': round(statistics.median(after) - statistics.median(before), 4)}
            break
    baseline_peak = avg([r for r in history if date.fromisoformat(r['date']).weekday() < 5], 'peak_kwh')
    usage_fit = degree_fit(history, base=base)
    peak = [r for r in march if date.fromisoformat(r['date']).weekday() < 5 and r['peak_kwh'] > baseline_peak * 1.45
            and r['kwh'] - predict(usage_fit, r) > max(4, predict(usage_fit, r) * .18)]
    high_constant = len(constant) >= minimum_days
    primary = 'constant_draw' if high_constant else 'baseline_step' if step else 'peak_shift' if len(peak) >= minimum_days else 'none'
    start = constant[0]['date'] if high_constant else step['start'] if step else peak[0]['date'] if primary == 'peak_shift' else None
    return {'is_anomaly': primary != 'none', 'primary_type': primary, 'high_constant_minimum_draw': high_constant,
            'constant_draw_days': len(constant), 'historical_minimum_kw': round(avg(history, 'min_kw'), 4),
            'current_minimum_kw': round(avg(march, 'min_kw'), 4),
            'baseline_step': step is not None, 'step_date': step['start'] if step else None,
            'step_increase_kw': step['increase_kw'] if step else None,
            'step_threshold_kw': round(step_threshold, 4), 'weekday_peak_shift': len(peak) >= minimum_days,
            'peak_shift_days': len(peak), 'signal_start': start,
            'signal_days': len(constant) if high_constant else len(march) - next(i for i, r in enumerate(march) if r['date'] == start) if step else len(peak) if primary == 'peak_shift' else 0}


def avg(rows, key):
    return statistics.mean(r[key] for r in rows)


def compare_neighborhood(home, kwh, neighborhood, config):
    settings = config["neighborhood"]
    tolerance = settings["size_tolerance_pct"] / 100
    minimum, maximum = home["sqft"] * (1 - tolerance), home["sqft"] * (1 + tolerance)
    peers = [p for p in neighborhood
             if minimum <= p["sqft"] <= maximum and p["month"] == "2026-03"
             and p["locality"] == config["locality"] and p["heating"] == home["heating"]]
    comparison = {"available": len(peers) >= settings["minimum_peers"], "count": len(peers),
                  "min_sqft": round(minimum), "max_sqft": round(maximum),
                  "size_tolerance_pct": settings["size_tolerance_pct"], "home_kwh": kwh,
                  "note": "Synthetic neighborhood benchmark. Homes share the locality, billing month, and electric heating. Occupancy and equipment differ; this is context, not an efficiency rating."}
    if not comparison["available"]:
        comparison["insight"] = "Not enough neighborhood homes of similar size are available for a reliable demo comparison."
        return comparison
    mean = statistics.mean(p["kwh"] for p in peers)
    if mean <= 0:
        comparison.update(available=False, insight="Neighborhood consumption data is unavailable for comparison.")
        return comparison
    difference = (kwh / mean - 1) * 100
    deviation = statistics.pstdev(p['kwh'] for p in peers)
    direction = "higher" if difference >= 0 else "lower"
    comparison.update(peer_kwh=round(mean, 3), difference_pct=round(difference, 1), direction=direction,
                      peer_intensity=statistics.mean(p["kwh"] / p["sqft"] for p in peers))
    comparison.update(stddev_kwh=round(deviation, 6),
                      z_score=round((kwh - mean) / deviation, 4) if deviation > 1e-10 else None,
                      status='above' if difference > 10 else 'below' if difference < -10 else 'typical')
    comparison["insight"] = f"Your March electricity consumption was {abs(difference):.1f}% {direction} than the average for {len(peers)} neighborhood homes of similar size ({minimum:,.0f}–{maximum:,.0f} sq ft)."
    return comparison


def build_evidence(home, daily, invoices, alternatives, all_daily, config, neighborhood=None):
    history = [d for d in daily if d["date"] < "2026-03-01"]
    feb = [d for d in daily if d["date"].startswith("2026-02")]
    march = [d for d in daily if d["date"].startswith("2026-03")]
    current = next(i for i in invoices if i["home_id"] == home["id"] and i["month"] == "2026-03")
    previous = next(i for i in invoices if i["home_id"] == home["id"] and i["month"] == "2026-02")
    weather, fit = weather_features(history, march, config)
    anomalies = anomaly_features(history, march, config)
    baseline_night = avg(history, "night_kw")
    chart = [{**row, "expected_kwh": round(predict(fit, row), 6)} for row in daily]
    signals = []
    if anomalies['is_anomaly']:
        kind = 'peak' if anomalies['primary_type'] == 'peak_shift' else 'sustained'
        signals.append({'kind': kind, 'start': anomalies['signal_start'], 'days': anomalies['signal_days'],
                        'summary': anomalies['primary_type'].replace('_', ' ')})
    elif weather['association'] in ('heating', 'cooling'):
        threshold = avg(history, 'temp_f')
        unusual = [r for r in march if (r['temp_f'] < threshold - 3.6 if weather['association'] == 'heating' else r['temp_f'] > threshold + 3.6)]
        runs = []
        for row in unusual:
            if not runs or (date.fromisoformat(row['date']) - date.fromisoformat(runs[-1][-1]['date'])).days != 1:
                runs.append([])
            runs[-1].append(row)
        spell = max(runs, key=len) if runs else march
        signals.append({'kind': 'weather', 'start': spell[0]['date'], 'days': len(spell), 'driver': weather['association'],
                        'summary': 'Usage changes coincide with temperature changes'})
    daily_change = (avg(march, "kwh") / avg(feb, "kwh") - 1) * 100
    plan_comparison = sorted([
        {"plan_id": p["id"], "plan_name": p["name"],
         "total": alternatives[(home["id"], "2026-03", p["id"])],
         "savings": money(current["total"] - alternatives[(home["id"], "2026-03", p["id"])])}
        for p in config["plans"]
    ], key=lambda p: (p["total"], p["plan_id"]))
    comparison = compare_neighborhood(home, current["kwh"], neighborhood or [], config)
    delta = money(current["total"] - previous["total"])
    best = plan_comparison[0]
    metrics = {
        "bill_total": f"${current['total']:,.2f}", "previous_bill": f"${previous['total']:,.2f}",
        "bill_change": f"{'+' if delta >= 0 else '−'}${abs(delta):,.2f}",
        "march_kwh": f"{current['kwh']:,.1f} kWh", "daily_change": f"{daily_change:+.1f}%",
        "baseline_night": f"{baseline_night:.2f} kW",
        "march_night": f"{avg(march, 'night_kw'):.2f} kW",
        "feb_days": str(len(feb)), "march_days": str(len(march)),
        "feb_temp": f"{avg(feb, 'temp_f'):.1f} °F", "march_temp": f"{avg(march, 'temp_f'):.1f} °F",
        "best_plan": best["plan_name"], "best_plan_total": f"${best['total']:,.2f}",
        "plan_savings": f"${max(0, best['savings']):,.2f}",
        "home_intensity": f"{current['kwh'] / home['sqft']:.3f} kWh/sq ft",
        "minimum_draw": f"{anomalies['current_minimum_kw']:.2f} kW",
        "baseline_step": f"{anomalies['step_increase_kw'] or 0:.2f} kW",
        "weather_residual": f"{weather['unexplained_pct']:+.1f}%",
    }
    if comparison["available"]:
        metrics.update({"peer_intensity": f"{comparison['peer_intensity']:.3f} kWh/sq ft",
                        "peer_count": str(comparison["count"]),
                        "peer_size_range": f"{comparison['min_sqft']:,}–{comparison['max_sqft']:,} sq ft",
                        "peer_kwh": f"{comparison['peer_kwh']:,.1f} kWh",
                        "peer_difference": f"{abs(comparison['difference_pct']):.1f}%",
                        "peer_direction": comparison["direction"]})
        if comparison['z_score'] is not None:
            metrics['peer_z_score'] = f"{comparison['z_score']:.2f}"
    anomaly = next((s for s in signals if s["kind"] in ("sustained", "peak")), None)
    if anomaly:
        metrics["signal_start"] = date.fromisoformat(anomaly["start"]).strftime("%b %d").replace(" 0", " ")
        metrics["signal_days"] = str(anomaly["days"])
    summary = {
        'schema_version': 'hybrid-v2-fahrenheit',
        'home': {k: home[k] for k in ('id', 'sqft', 'heating', 'plan')},
        'locality': config['locality'], 'period': '2026-03',
        'billing': {'total_kwh': current.get('exact_kwh', f"{current['kwh']:.6f}"),
                    'bill_total': f"{current['total']:.2f}", 'current_plan': current['plan_id'],
                    'previous_bill': f"{previous['total']:.2f}", 'bill_change': f"{delta:.2f}",
                    'daily_usage_change_pct': round(daily_change, 4), 'current_days': len(march), 'previous_days': len(feb)},
        'peer_group': {'count': comparison['count'], 'size_band_sqft': [comparison['min_sqft'], comparison['max_sqft']],
                       'average_monthly_kwh': comparison.get('peer_kwh'), 'stddev_kwh': comparison.get('stddev_kwh'),
                       'relative_variance_pct': comparison.get('difference_pct'), 'z_score': comparison.get('z_score'),
                       'status': comparison.get('status', 'unavailable')},
        'weather': weather, 'anomalies': anomalies, 'plan_options': plan_comparison,
        'metrics': metrics,
        'limits': ['Synthetic data and tariffs; demo plans assumed eligible.',
                   'Peer matching does not control for occupancy or equipment.',
                   'Weather association is not proof of cause; extrapolation is explicitly flagged.',
                   'Meter patterns cannot identify an appliance, leak or stuck HVAC valve.'],
    }
    return {
        "home": home, "billing_period": "March 2026", "invoice": current, "previous_invoice": previous,
        "daily": chart, "signals": signals, "metrics": metrics, "daily_change_pct": round(daily_change, 2),
        "bill_delta": delta, "plans": plan_comparison, "neighborhood": comparison,
        "semantic_summary": summary,
        "method": "Historical heating/cooling degree-day fit; weather-adjusted minimum-draw and baseline-step checks. Exact integer energy aggregation and Decimal billing.",
        "limitations": "Synthetic weather, tariffs and neighborhood benchmark. Similar size means within twenty percent of floor area; occupancy and equipment are not matched. Correlation does not establish cause. No appliance identification.",
    }


def agent_input(evidence):
    """Exclude raw other-home records, generation parameters, and evaluation truth."""
    return evidence['semantic_summary']


def validate_intervals(readings, weather, config):
    """Fail closed on missing, duplicate, off-grid, or incomplete local telemetry."""
    tz = ZoneInfo(config['timezone'])
    start = datetime.combine(date.fromisoformat(config['start_date']), datetime.min.time(), tz).astimezone(timezone.utc)
    end_day = date.fromisoformat(config['start_date']) + timedelta(days=config['days'])
    end = datetime.combine(end_day, datetime.min.time(), tz).astimezone(timezone.utc)
    expected = int((end - start).total_seconds() / 300)
    seen = {h['id']: set() for h in config['homes']}
    for row in readings:
        if row['home_id'] not in seen:
            raise ValueError('Unknown home in telemetry')
        stamp = datetime.fromisoformat(row['timestamp_utc'])
        if stamp.utcoffset() is None:
            raise ValueError('Naive telemetry timestamp')
        offset = (stamp - start).total_seconds()
        if stamp < start or stamp >= end or offset % 300:
            raise ValueError('Out-of-period or off-grid telemetry timestamp')
        if stamp in seen[row['home_id']]:
            raise ValueError('Duplicate telemetry interval')
        seen[row['home_id']].add(stamp)
    if any(len(stamps) != expected for stamps in seen.values()):
        raise ValueError('Incomplete telemetry; semantic summaries cannot be published')
    expected_days = {(date.fromisoformat(config['start_date']) + timedelta(days=i)).isoformat() for i in range(config['days'])}
    if len(weather) != config['days'] or {r['date'] for r in weather} != expected_days:
        raise ValueError('Missing or duplicate daily weather')
    if any(not math.isfinite(r['mean_temp_f']) for r in weather):
        raise ValueError('Weather temperatures must be finite')


def calculate_features(readings, weather, neighborhood, config):
    """Public deterministic API for supplied interval data, not just generated data."""
    validate_intervals(readings, weather, config)
    invoices, daily, alternatives = calculate(readings, weather, config)
    reports = [build_evidence(h, daily[h['id']], invoices, alternatives, daily, config, neighborhood) for h in config['homes']]
    return reports, invoices


def run_analytics(config, output):
    """Analyze previously generated input CSVs; never replace the source readings."""
    output = Path(output)
    required = ('readings.csv', 'weather.csv', 'neighborhood.csv')
    missing = [name for name in required if not (output / name).is_file()]
    if missing:
        raise ValueError('Missing generated data: ' + ', '.join(missing) + '. Run python generate_data.py first.')
    def read_csv(name):
        with (output / name).open(newline='') as stream:
            return list(csv.DictReader(stream))
    readings = read_csv('readings.csv')
    weather = [{**row, 'mean_temp_f': float(row['mean_temp_f'])} for row in read_csv('weather.csv')]
    neighborhood = [{**row, 'sqft': int(row['sqft']), 'kwh': float(row['kwh'])}
                    for row in read_csv('neighborhood.csv')]
    reports, invoices = calculate_features(readings, weather, neighborhood, config)
    write_csv(output / 'invoices.csv', invoices)
    (output / 'semantic_summaries.json').write_text(json.dumps([r['semantic_summary'] for r in reports], indent=2, allow_nan=False))
    return reports, invoices
