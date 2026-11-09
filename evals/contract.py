"""Frozen demo validation contract. No imports from, or writes to, the demo.

Snapshot source hashes and provenance are in fixtures/manifest.json.
"""
import re
from decimal import Decimal

TOKEN = re.compile(r"\{\{([a-z_]+)\}\}")
EMAIL_WORD_LIMITS = {"subject": 8, "intro": 7, "explanation": 24, "action": 14, "body": 45}

def agent_input(evidence):
    return evidence["semantic_summary"]


class EmailLengthError(ValueError):
    """Recoverable copy-length issue, distinct from an unsupported factual claim."""
    def __init__(self, counts):
        self.counts = counts
        self.violations = {key: {'words': value, 'limit': EMAIL_WORD_LIMITS[key]}
                           for key, value in counts.items() if value > EMAIL_WORD_LIMITS[key]}
        details = '; '.join(f"{key}: {item['words']}/{item['limit']} words" for key, item in self.violations.items())
        super().__init__(f'Email captions exceed their word limits ({details})')

def resolve(text, metrics):
    return TOKEN.sub(lambda m: metrics[m.group(1)], text)

def validate_texts(texts, metrics):
    for text in texts:
        if not isinstance(text, str):
            raise ValueError('Expected text fields')
        if not set(TOKEN.findall(text)).issubset(metrics):
            raise ValueError('Unknown evidence reference')
        stripped = TOKEN.sub('', text)
        if '{{' in stripped or '}}' in stripped or re.search(r'\d', stripped):
            raise ValueError('Unvalidated number or malformed evidence reference in model output')

def best_alternative(summary):
    choices = sorted(summary['plan_options'], key=lambda p: (p['total'], p['plan_id']))
    best = choices[0]
    return best if best['savings'] > .5 else None

def approved_metrics(analysis):
    return {r['metric_id']: r['calculated_value'] for r in analysis['mathematical_justification']}

def validate_analysis(analysis, evidence, *, allow_review=False):
    summary = agent_input(evidence)
    required_keys = {'is_anomaly', 'anomaly_type', 'mathematical_justification', 'peer_comparison_status',
                     'recommended_plan', 'headline', 'explanation', 'actions', 'needs_review'}
    if set(analysis) != required_keys:
        raise ValueError('Analysis does not match the strict billing schema')
    if type(analysis['is_anomaly']) is not bool or type(analysis['needs_review']) is not bool:
        raise ValueError('Analysis flags must be booleans')
    flags = summary['anomalies']
    if analysis['is_anomaly'] != flags['is_anomaly'] or analysis['anomaly_type'] != flags['primary_type']:
        raise ValueError('Analysis contradicts deterministic anomaly flags')
    if analysis['peer_comparison_status'] != summary['peer_group']['status']:
        raise ValueError('Analysis contradicts deterministic peer comparison')
    if not isinstance(analysis['actions'], list) or not 1 <= len(analysis['actions']) <= 2:
        raise ValueError('Analysis needs one or two actions')
    refs = analysis['mathematical_justification']
    if not isinstance(refs, list) or not refs:
        raise ValueError('Mathematical justification is required')
    for ref in refs:
        if not isinstance(ref, dict) or set(ref) != {'metric_id', 'calculated_value'}:
            raise ValueError('Invalid mathematical justification')
        if (not isinstance(ref['metric_id'], str) or not isinstance(ref['calculated_value'], str)
                or ref['metric_id'] not in summary['metrics']
                or summary['metrics'][ref['metric_id']] != ref['calculated_value']):
            raise ValueError('Mathematical justification changed a calculated value')
    metrics = approved_metrics(analysis)
    if len(metrics) != len(refs):
        raise ValueError('Duplicate mathematical justification')
    texts = [analysis['headline'], analysis['explanation'], *analysis['actions']]
    best = best_alternative(summary)
    recommended = analysis['recommended_plan']
    if best is None and recommended is not None:
        raise ValueError('No lower-cost registered plan supports this recommendation')
    if best is not None:
        if not isinstance(recommended, dict) or set(recommended) != {'plan_id', 'estimated_bill', 'savings', 'reason'}:
            raise ValueError('Expected a structured plan recommendation')
        if (recommended['plan_id'] != best['plan_id'] or Decimal(str(recommended['estimated_bill'])) != Decimal(str(best['total']))
                or Decimal(str(recommended['savings'])) != Decimal(str(best['savings']))):
            raise ValueError('Recommendation must match the optimal calculated plan and savings')
        texts.append(recommended['reason'])
    validate_texts(texts, metrics)
    required = {'bill_total', 'bill_change', 'daily_change', 'feb_days', 'march_days'}
    if flags['is_anomaly']:
        required.add('weather_residual')
    if flags['primary_type'] in ('constant_draw', 'baseline_step'):
        required.update({'minimum_draw', 'baseline_step'})
    if not required.issubset(metrics):
        raise ValueError('Analysis omitted required mathematical justification')
    if not {'bill_total', 'bill_change', 'daily_change', 'feb_days', 'march_days'}.issubset(TOKEN.findall(analysis['explanation'])):
        raise ValueError('Analysis omitted required billing comparisons')
    if analysis['needs_review'] and not allow_review:
        raise ValueError('Analyst flagged findings for staff review; email generation stopped')

def validate_email(email, evidence, analysis):
    if analysis['needs_review']:
        raise ValueError('Review-required findings cannot be used for a customer email')
    if not isinstance(email, dict):
        raise ValueError('Approved findings require a validated customer email')
    if set(email) != {'subject', 'intro', 'explanation', 'actions'}:
        raise ValueError('Email does not match the strict communications schema')
    if not isinstance(email['actions'], list) or len(email['actions']) != 1:
        raise ValueError('Visual email must have exactly one action')
    texts = [email['subject'], email['intro'], email['explanation'], *email['actions']]
    validate_texts(texts, approved_metrics(analysis))
    counts = {key: len(resolve(value, evidence['metrics']).split()) for key, value in
              [('subject', email['subject']), ('intro', email['intro']),
               ('explanation', email['explanation']), ('action', email['actions'][0])]}
    counts['body'] = counts['intro'] + counts['explanation'] + counts['action']
    if any(value > EMAIL_WORD_LIMITS[key] for key, value in counts.items()):
        raise EmailLengthError(counts)

def normal_email_copy():
    """Normal bills need only measured facts; no model-written causal narrative."""
    return {'subject': 'Your March electricity snapshot', 'intro': 'Your monthly usage snapshot',
            'explanation': 'Average daily consumption changed {{daily_change}} compared with February.',
            'actions': ['Keep monitoring your usual usage.']}
