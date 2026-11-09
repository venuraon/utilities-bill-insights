"""Evaluation-only configuration and frozen fixture validation."""
import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from .common import ROOT


def load_config(path=None):
    config = json.loads(Path(path or ROOT / 'config.json').read_text())
    expected = {'agent_model', 'judge_model', 'cases', 'repetitions', 'max_output_tokens',
                'reasoning_effort', 'timeout_seconds', 'email_revisions'}
    if set(config) != expected:
        raise ValueError(f'Config keys must be: {sorted(expected)}')
    for key in ('agent_model', 'judge_model'):
        model = config[key]
        if model is not None and (not isinstance(model, str) or
                                  not re.fullmatch(r'(?:gpt-|o[134](?:-|$))[A-Za-z0-9._-]*', model)):
            raise ValueError(f'{key} must be an OpenAI model ID or null')
    if (not isinstance(config['cases'], list) or not config['cases'] or
            len(set(config['cases'])) != len(config['cases']) or
            not set(config['cases']) <= set('ABCDE')):
        raise ValueError('Choose unique case IDs from A, B, C, D, E')
    for key, minimum, maximum in (('repetitions', 1, 20), ('timeout_seconds', 1, 120),
                                   ('email_revisions', 0, 2)):
        if type(config[key]) is not int or not minimum <= config[key] <= maximum:
            raise ValueError(f'{key} must be an integer in {minimum}..{maximum}')
    limits = config['max_output_tokens']
    if set(limits) != {'analyst', 'communications', 'judge'} or any(
            type(v) is not int or not 128 <= v <= 8192 for v in limits.values()):
        raise ValueError('Set analyst, communications, judge output caps in 128..8192')
    efforts = config['reasoning_effort']
    if set(efforts) != {'agent', 'judge'} or any(
            v not in (None, 'none', 'minimal', 'low', 'medium', 'high') for v in efforts.values()):
        raise ValueError('Invalid agent/judge reasoning_effort')
    return config


def load_prices(models, today=None):
    prices = json.loads((ROOT / 'prices.json').read_text())
    age = ((today or date.today()) - date.fromisoformat(prices['verified_on'])).days
    if not 0 <= age <= 30:
        raise ValueError('Refresh evals/prices.json from official pricing; it is older than thirty days')
    if prices['service_tier'] != 'default' or prices['unit'] != 'per_million_tokens':
        raise ValueError('Only standard/default per-million-token pricing is supported')
    for model in models:
        if not model or model not in prices['models']:
            raise ValueError(f'Add verified pricing for {model!r} to evals/prices.json before a live run')
        rates = prices['models'][model]
        if set(rates) != {'input', 'cached_input', 'output', 'cache_write'} or any(
                not Decimal(str(v)).is_finite() or Decimal(str(v)) <= 0 for v in rates.values()):
            raise ValueError(f'Invalid prices for {model}')
        if Decimal(rates['cached_input']) > max(Decimal(rates['input']), Decimal(rates['cache_write'])):
            raise ValueError('Cached input price exceeds the conservative input reservation')
    return prices


def load_fixtures():
    import hashlib
    manifest = json.loads((ROOT / 'fixtures/manifest.json').read_text())
    for relative, expected in manifest['files'].items():
        if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != expected:
            raise ValueError(f'Frozen contract changed: {relative}; create an intentional new fixture version')
    cases = json.loads((ROOT / 'fixtures/cases.json').read_text())
    prompts = json.loads((ROOT / 'fixtures/prompts.json').read_text())
    updates = json.loads((ROOT / 'prompt_updates.json').read_text())
    prompts = {key: text + '\nEvaluation refinement:\n' + updates[key] for key, text in prompts.items()}
    return {c['case_id']: c for c in cases}, prompts, manifest
