"""Short, qualified customer guidance from validated features, not scenario labels.

Keep the observed pattern, a possible reason and a practical action together.
These rules supplement the saved agent findings and never identify an appliance.
"""


def customer_insight(report):
    summary = report['semantic_summary']
    kind = summary['anomalies']['primary_type']
    weather = summary['weather']['association']
    change = summary['billing']['daily_usage_change_pct']
    if kind == 'constant_draw':
        title = 'Persistent extra demand'
        reason = 'Equipment may be running continuously'
        action = 'check timers and devices left on overnight'
        category = 'anomaly'
    elif kind == 'baseline_step':
        title = 'A mid-month usage increase'
        reason = 'A schedule or equipment change may explain the higher baseline'
        action = 'check recent settings and devices left running'
        category = 'anomaly'
    elif kind == 'peak_shift':
        title = 'More use during peak hours'
        reason = 'More consumption during expensive peak hours may be raising costs'
        action = 'move flexible equipment use to off-peak hours'
        category = 'anomaly'
    elif weather == 'heating' and change > 5:
        title = 'Weather-related usage increase'
        reason = 'Colder days may have increased heating use'
        action = 'review thermostat schedules and avoid heating empty rooms'
        category = 'weather'
    elif weather == 'cooling' and change > 5:
        title = 'Weather-related usage increase'
        reason = 'Hotter days may have increased cooling use'
        action = 'review thermostat schedules and avoid cooling empty rooms'
        category = 'weather'
    elif change > 5:
        title = 'Daily consumption increased'
        reason = 'Changed routines or equipment use may explain the increase'
        action = 'review recent schedules and devices left running'
        category = 'usage'
    elif change < -5:
        title = 'Daily consumption decreased'
        reason = 'Shorter equipment run times may explain the lower consumption'
        action = 'keep helpful schedules and monitor daily use'
        category = 'usage'
    elif summary['billing']['current_days'] > summary['billing']['previous_days'] and report['bill_delta'] > 0:
        title = 'Daily usage is broadly steady'
        reason = 'More billing days contributed to the higher total'
        action = 'compare daily averages when reviewing monthly bills'
        category = 'steady'
    else:
        title = 'Daily usage is broadly steady'
        reason = 'No unusual consumption pattern was detected'
        action = 'keep your routine and monitor daily averages'
        category = 'steady'
    return {'title': title, 'reason': reason, 'action': action,
            'sentence': f'{reason}; {action}.', 'category': category,
            'source': 'Calculated evidence with qualified, rule-based suggestions'}
