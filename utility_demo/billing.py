"""One telemetry pass; exact integer micro-kWh aggregation and Decimal tariffs."""
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo
from .config import registries

MICRO_KWH = 1_000_000


def money(value):
    return float(Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def is_peak(local, config):
    return local.weekday() < 5 and config['peak_start_hour'] <= local.hour < config['peak_end_hour']


def percentile(values, fraction):
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def calculate(readings, weather, config):
    tz = ZoneInfo(config['timezone'])
    homes, plans = registries(config)
    daily = defaultdict(lambda: {'units': 0, 'peak_units': 0, 'night_units': 0, 'night_count': 0, 'kw': []})
    monthly = defaultdict(lambda: {'peak': 0, 'off_peak': 0})
    stamps = {}
    for row in readings:
        home_id = row['home_id']
        if home_id not in homes:
            raise ValueError('Unknown home in telemetry')
        stamp = row['timestamp_utc']
        if stamp not in stamps:
            parsed = datetime.fromisoformat(stamp)
            if parsed.utcoffset() is None:
                raise ValueError('Telemetry timestamps must be timezone-aware')
            local = parsed.astimezone(tz)
            day = local.date().isoformat()
            stamps[stamp] = (day, day[:7], local.hour, is_peak(local, config))
        day, month, hour, peak = stamps[stamp]
        quantity = Decimal(str(row['kwh'])) * MICRO_KWH
        if not quantity.is_finite() or quantity < 0 or quantity != quantity.to_integral_value():
            raise ValueError('Consumption must be nonnegative, finite, and have at most six decimal places')
        units = int(quantity)
        d = daily[(home_id, day)]
        d['units'] += units
        d['peak_units'] += units if peak else 0
        d['kw'].append(units * 12 / MICRO_KWH)
        if hour < 5:
            d['night_units'] += units
            d['night_count'] += 1
        monthly[(home_id, month)]['peak' if peak else 'off_peak'] += units
    temperatures = {w['date']: w['mean_temp_f'] for w in weather}
    daily_by_home = defaultdict(list)
    for (home_id, day), values in sorted(daily.items()):
        daily_by_home[home_id].append({
            'date': day, 'kwh': values['units'] / MICRO_KWH,
            'peak_kwh': values['peak_units'] / MICRO_KWH,
            'night_kw': round(values['night_units'] * 12 / (MICRO_KWH * values['night_count']), 6) if values['night_count'] else 0,
            'min_kw': min(values['kw']), 'p10_kw': percentile(values['kw'], 0.1),
            'interval_count': len(values['kw']), 'temp_f': temperatures[day],
        })
    invoices, alternatives = [], {}
    for (home_id, month), quantities in sorted(monthly.items()):
        for plan in plans.values():
            peak_charge = money(Decimal(quantities['peak']) / MICRO_KWH * plan.peak_rate)
            off_charge = money(Decimal(quantities['off_peak']) / MICRO_KWH * plan.off_peak_rate)
            total = money(Decimal(str(peak_charge)) + Decimal(str(off_charge)) + plan.monthly_fee)
            alternatives[(home_id, month, plan.id)] = total
            if plan.id == homes[home_id].plan:
                invoices.append({
                    'home_id': home_id, 'month': month, 'plan_id': plan.id, 'plan_name': plan.name,
                    'kwh': (quantities['peak'] + quantities['off_peak']) / MICRO_KWH,
                    'exact_kwh': format(Decimal(quantities['peak'] + quantities['off_peak']) / MICRO_KWH, '.6f'),
                    'peak_kwh': quantities['peak'] / MICRO_KWH,
                    'off_peak_kwh': quantities['off_peak'] / MICRO_KWH,
                    'peak_charge': peak_charge, 'off_peak_charge': off_charge,
                    'monthly_fee': float(plan.monthly_fee), 'total': total,
                })
    return invoices, dict(daily_by_home), alternatives
