"""Validated home/plan registry. config.json remains the editable source of truth."""
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import math

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Home:
    id: str
    sqft: int
    plan: str
    heating: str
    locality: str


@dataclass(frozen=True)
class UtilityPlan:
    id: str
    name: str
    off_peak_rate: Decimal
    peak_rate: Decimal
    monthly_fee: Decimal


def registries(config):
    homes = {h['id']: Home(h['id'], h['sqft'], h['plan'], h['heating'], config['locality']) for h in config['homes']}
    plans = {p['id']: UtilityPlan(p['id'], p['name'], Decimal(str(p['off_peak_rate'])),
                                Decimal(str(p['peak_rate'])), Decimal(str(p['monthly_fee']))) for p in config['plans']}
    if len(homes) != len(config['homes']) or len(plans) != len(config['plans']):
        raise ValueError('Home and plan IDs must be unique')
    for home in homes.values():
        if home.sqft <= 0 or home.plan not in plans:
            raise ValueError('Every home needs a positive size and a registered plan')
    for plan in plans.values():
        if any(not v.is_finite() or v < 0 for v in (plan.off_peak_rate, plan.peak_rate, plan.monthly_fee)):
            raise ValueError('Tariff rates and fees must be finite and nonnegative')
    ZoneInfo(config['timezone'])
    if not 0 <= config['peak_start_hour'] < config['peak_end_hour'] <= 24:
        raise ValueError('Invalid local peak-hour window')
    if type(config.get('max_concurrent_homes', 2)) is not int or not 1 <= config.get('max_concurrent_homes', 2) <= 5:
        raise ValueError('Concurrent home count must be between one and five')
    for key, value in config.get('analytics', {}).items():
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f'Invalid analytics threshold: {key}')
    return homes, plans


def load_config(path=ROOT / 'config.json'):
    config = json.loads(Path(path).read_text())
    registries(config)
    return config
