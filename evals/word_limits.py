"""Private Python enforcement margins. Never include these limits in model inputs."""
from .contract import EMAIL_WORD_LIMITS, resolve

EMAIL_TARGETS = dict(EMAIL_WORD_LIMITS)
ANALYST_TARGET = 120
# Integer division rounds down: never allow more than twenty percent extra.
EMAIL_HARD_LIMITS = {key: value * 120 // 100 for key, value in EMAIL_TARGETS.items()}
ANALYST_HARD_LIMIT = ANALYST_TARGET * 120 // 100


def email_word_counts(email, metrics):
    counts = {key: len(resolve(value, metrics).split()) for key, value in
              [('subject', email['subject']), ('intro', email['intro']),
               ('explanation', email['explanation']), ('action', email['actions'][0])]}
    counts['body'] = counts['intro'] + counts['explanation'] + counts['action']
    return counts


def hard_violations(counts):
    return {key: count for key, count in counts.items() if count > EMAIL_HARD_LIMITS[key]}


def revision_feedback(counts):
    # The model gets its original targets, never the private enforcement thresholds.
    return {key: {'words': count, 'limit': EMAIL_TARGETS[key]}
            for key, count in hard_violations(counts).items()}
