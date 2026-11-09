"""Allow representation changes while preserving every amount, sign, and unit."""
import copy
import json
import re
from decimal import Decimal

POLICY_VERSION = 'checks-v3'


def money_value(value):
    """USD symbol/whitespace is presentation; currencies, signs and digits are facts."""
    if isinstance(value, bool):
        raise ValueError('Money must be a decimal, not a boolean')
    if isinstance(value, str):
        match = re.fullmatch(r'([+-]?)\s*\$?\s*((?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?)', value.strip())
        if not match:
            raise ValueError('Expected a USD decimal amount')
        value = match[1] + match[2].replace(',', '')
    if not isinstance(value, (str, int, float, Decimal)):
        raise ValueError('Expected a USD decimal amount')
    try:
        result = Decimal(str(value))
    except ArithmeticError as exc:
        raise ValueError('Expected a USD decimal amount') from exc
    if not result.is_finite():
        raise ValueError('Money must be finite')
    return result


def decimal_text(value):
    text = format(value, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def normalize_candidate(case, stage, candidate):
    normalized = copy.deepcopy(candidate)
    notices = []
    if not isinstance(normalized, dict) or stage != 'analyst':
        return normalized, notices
    plan = normalized.get('recommended_plan')
    if isinstance(plan, dict):
        for key in ('estimated_bill', 'savings'):
            if key in plan:
                try:
                    value = decimal_text(money_value(plan[key]))
                except (ValueError, TypeError, ArithmeticError):
                    continue
                if plan[key] != value:
                    notices.append({'path': f'recommended_plan.{key}', 'original': plan[key],
                                    'normalized': value, 'reason': 'Equivalent USD decimal representation'})
                    plan[key] = value
    refs = normalized.get('mathematical_justification')
    if isinstance(refs, list):
        for index, ref in enumerate(refs):
            if not isinstance(ref, dict):
                continue
            key, observed = ref.get('metric_id'), ref.get('calculated_value')
            expected = case['summary']['metrics'].get(key) if isinstance(key, str) else None
            if isinstance(observed, str) and expected is not None and observed != expected:
                if re.sub(r'\s+', '', observed) == re.sub(r'\s+', '', expected):
                    notices.append({'path': f'mathematical_justification[{index}].calculated_value',
                                    'original': observed, 'normalized': expected,
                                    'reason': 'Whitespace-only difference from the supplied metric'})
                    ref['calculated_value'] = expected
    return normalized, notices


def visible_json(raw_output):
    """Recover complete visible JSON; never repair truncated JSON or infer facts."""
    text = ''.join(content.get('text', '') for item in (raw_output or [])
                   if item.get('type') == 'message' for content in item.get('content', [])
                   if content.get('type') == 'output_text')
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
