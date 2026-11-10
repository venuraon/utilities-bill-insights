"""Versioned, nonduplicating factual checks; execution failures are not fact errors."""
from decimal import Decimal
from .contract import (TOKEN, approved_metrics, best_alternative,
                       resolve, validate_texts)
from .eval_schemas import BillingAnalysis, EmailCopy
from .normalization import POLICY_VERSION, decimal_text, normalize_candidate
from .word_limits import ANALYST_HARD_LIMIT, EMAIL_HARD_LIMITS, email_word_counts, hard_violations


def check(name, function, category='facts'):
    try:
        function()
        return {'name': name, 'category': category, 'passed': True, 'reason': None}
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, ArithmeticError) as exc:
        return {'name': name, 'category': category, 'passed': False, 'reason': str(exc)[:500]}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def score(case, stage, output, upstream=None, execution_status=None):
    base = {'policy_version': POLICY_VERSION, 'normalizations': [], 'structured': False}
    if output is None:
        return {**base, 'passed': False, 'assessable': False, 'checks': [],
                'execution_status': execution_status or 'no_usable_output', 'normalized_output': None}
    normalized, notices = normalize_candidate(case, stage, output)
    schema = BillingAnalysis if stage == 'analyst' else EmailCopy
    checks = [check('structured_schema', lambda: schema.model_validate(normalized), 'structure')]
    if not checks[0]['passed']:
        return {**base, 'passed': False, 'assessable': True, 'checks': checks,
                'normalizations': notices, 'execution_status': 'schema_error', 'normalized_output': None}
    normalized = schema.model_validate(normalized).model_dump(mode='json')
    output = normalized
    summary, metrics = case['summary'], case['summary']['metrics']
    if stage == 'analyst':
        flags = summary['anomalies']
        checks.extend([
            check('anomaly_flags', lambda: require(
                output['is_anomaly'] == flags['is_anomaly'] and output['anomaly_type'] == flags['primary_type'],
                'Does not match the calculated anomaly flags')),
            check('peer_status', lambda: require(output['peer_comparison_status'] == summary['peer_group']['status'],
                                                 'Incorrect peer comparison')),
            check('review_decision', lambda: require(output['needs_review'] == case['expected']['needs_review'],
                                                     'Incorrect staff-review decision for this labeled case'), 'routing'),
        ])

        def plan():
            best = best_alternative(summary)
            recommendation = output['recommended_plan']
            if best is None:
                require(recommendation is None, 'No materially cheaper registered plan')
            else:
                require(recommendation is not None and recommendation['plan_id'] == best['plan_id']
                        and Decimal(recommendation['estimated_bill']) == Decimal(str(best['total']))
                        and Decimal(recommendation['savings']) == Decimal(str(best['savings'])),
                        'Plan/cost/savings do not match the lowest-cost calculated option')

        def references():
            refs = output['mathematical_justification']
            approved = approved_metrics(output)
            require(len(approved) == len(refs) and all(metrics.get(k) == v for k, v in approved.items()),
                    'Changed, duplicated, or unknown evidence value; amount, sign, and unit must match')

        def required_references():
            required = {'bill_total', 'bill_change', 'daily_change', 'feb_days', 'march_days'}
            if flags['is_anomaly']:
                required.add('weather_residual')
            if flags['primary_type'] in ('constant_draw', 'baseline_step'):
                required.update({'minimum_draw', 'baseline_step'})
            require(required <= approved_metrics(output).keys(), 'Required mathematical justification is missing')

        texts = [output['headline'], output['explanation'], *output['actions']]
        if output['recommended_plan']:
            texts.append(output['recommended_plan']['reason'])
        checks.extend([
            check('optimal_plan_and_exact_costs', plan),
            check('evidence_values', references),
            check('required_evidence', required_references, 'contract'),
            check('evidence_reference_syntax', lambda: validate_texts(texts, approved_metrics(output)), 'contract'),
            check('billing_comparisons', lambda: require(
                {'bill_total', 'daily_change', 'feb_days', 'march_days'} <= set(TOKEN.findall(output['explanation']))
                and 'bill_change' in TOKEN.findall(output['headline'] + ' ' + output['explanation']),
                'Explanation needs bill total, daily change, and both day counts; bill change must appear in headline or explanation'), 'contract'),
            check('analyst_word_limit', lambda: require(
                len(resolve(output['explanation'], metrics).split()) <= ANALYST_HARD_LIMIT,
                f'Resolved analyst explanation exceeds {ANALYST_HARD_LIMIT} words'), 'presentation'),
        ])
    else:
        checks.append(check('approved_upstream', lambda: require(
            isinstance(upstream, dict) and not upstream.get('needs_review', True),
            'Customer email needs an approved upstream analysis'), 'routing'))
        allowed = approved_metrics(upstream) if isinstance(upstream, dict) else {}
        texts = [output['subject'], output['intro'], output['explanation'], *output['actions']]
        checks.append(check('email_evidence_reference_syntax', lambda: validate_texts(texts, allowed), 'contract'))

        def length():
            counts = email_word_counts(output, metrics)
            violations = [f'{k}: {n}/{EMAIL_HARD_LIMITS[k]} words' for k, n in hard_violations(counts).items()]
            require(not violations, '; '.join(violations))
        checks.append(check('email_word_limits', length, 'presentation'))
    return {**base, 'passed': all(c['passed'] for c in checks), 'assessable': True,
            'structured': True, 'checks': checks, 'normalizations': notices,
            'execution_status': execution_status or 'completed', 'normalized_output': normalized}


def decision_signature(stage, output):
    if output is None:
        return None
    if stage == 'analyst':
        plan = output.get('recommended_plan')
        return {'is_anomaly': output.get('is_anomaly'), 'anomaly_type': output.get('anomaly_type'),
                'peer_status': output.get('peer_comparison_status'), 'needs_review': output.get('needs_review'),
                'plan': {'plan_id': plan.get('plan_id'),
                         **{k: decimal_text(Decimal(str(plan[k]))) for k in ('estimated_bill', 'savings')}}
                if isinstance(plan, dict) else None}
    return None
