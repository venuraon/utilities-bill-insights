"""Persistent, fail-closed local spending guard shared by every live evaluation.

Reservations are written before requests. Interrupted/unknown requests retain the
full reservation. Actual invoice enforcement still belongs to the API account.
"""
import fcntl
import json
from decimal import Decimal
from uuid import uuid4
from .common import RESULTS, dumps, save_json, utc_now

TOTAL_LIMIT = Decimal('4.00')
REQUEST_LIMIT = Decimal('3.90')  # Keep ten cents outside the overall request allowance.
RUN_LIMIT = Decimal('0.35')
JUDGE_INPUT_CAP = 20_000
WRAPPER_ALLOWANCE = 4096


class BudgetExceeded(ValueError):
    pass


def input_bound(prompt, payload, schema):
    # A UTF-8 byte bound, including the structured-output schema, plus framing.
    # Deliberately conservative; this is not an exact tokenizer/counting API.
    return len((prompt + dumps(payload) + dumps(schema)).encode('utf-8')) + WRAPPER_ALLOWANCE


def maximum_cost(rates, tokens_in, tokens_out):
    return (max(Decimal(rates['input']), Decimal(rates['cache_write'])) * tokens_in
            + Decimal(rates['output']) * tokens_out) / Decimal(1_000_000)


def usage_cost(rates, usage):
    if usage is None or not {'input_tokens', 'output_tokens'} <= usage.keys():
        return None
    tokens_in, tokens_out = usage['input_tokens'], usage['output_tokens']
    details = usage.get('input_tokens_details') or {}
    cached = details.get('cached_tokens', 0)
    written = details.get('cache_write_tokens', 0)
    if any(type(v) is not int or v < 0 for v in (tokens_in, tokens_out, cached, written)) or cached + written > tokens_in:
        return None
    return ((tokens_in - cached - written) * Decimal(rates['input'])
            + cached * Decimal(rates['cached_input']) + written * Decimal(rates['cache_write'])
            + tokens_out * Decimal(rates['output'])) / Decimal(1_000_000)


class Budget:
    def __init__(self, folder=RESULTS):
        self.folder = folder
        self.path = folder / 'budget.json'
        self.lock = None

    def __enter__(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        self.lock = (self.folder / '.budget.lock').open('a')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise ValueError('Another live evaluation holds the shared budget lock') from None
        try:
            if self.path.exists():
                self.state = json.loads(self.path.read_text())
                if self.state.get('limit_usd') not in ('1.00', '2.00', str(TOTAL_LIMIT)) or not isinstance(self.state.get('entries'), dict):
                    raise ValueError('Invalid budget ledger; refusing paid requests')
                for entry in self.state['entries'].values():
                    charged, maximum = Decimal(entry['charged_or_reserved_usd']), Decimal(entry['maximum_usd'])
                    if not charged.is_finite() or not maximum.is_finite() or min(charged, maximum) < 0 or entry['status'] not in (
                            'reserved', 'estimated', 'unknown', 'not_sent'):
                        raise ValueError('Invalid budget entry; refusing paid requests')
                if self.state['limit_usd'] != str(TOTAL_LIMIT):
                    # Preserve all past charges and unresolved reservations.
                    self.state.setdefault('limit_changes', []).append({
                        'previous_limit_usd': self.state['limit_usd'], 'new_limit_usd': str(TOTAL_LIMIT),
                        'changed_utc': utc_now(), 'reason': 'User authorized a $4 overall limit and $0.35 per evaluation'})
                    self.state.update(limit_usd=str(TOTAL_LIMIT), request_allowance_usd=str(REQUEST_LIMIT), run_limit_usd=str(RUN_LIMIT))
                    save_json(self.path, self.state)
            else:
                self.state = {'limit_usd': str(TOTAL_LIMIT), 'request_allowance_usd': str(REQUEST_LIMIT),
                              'run_limit_usd': str(RUN_LIMIT), 'entries': {}}
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *args):
        if self.lock and not self.lock.closed:
            fcntl.flock(self.lock, fcntl.LOCK_UN)
            self.lock.close()

    @property
    def committed(self):
        return sum((Decimal(e['charged_or_reserved_usd']) for e in self.state['entries'].values()), Decimal(0))

    def reserve(self, run_id, amounts):
        if any(amount < 0 or not amount.is_finite() for amount in amounts):
            raise ValueError('Invalid cost reservation')
        if self.committed + sum(amounts) > REQUEST_LIMIT:
            raise BudgetExceeded('Remaining shared budget cannot cover the agent and its judge')
        run_committed = sum((Decimal(e['charged_or_reserved_usd']) for e in self.state['entries'].values()
                             if e['run_id'] == run_id), Decimal(0))
        if run_committed + sum(amounts) > RUN_LIMIT:
            raise BudgetExceeded('Remaining per-evaluation budget cannot cover the agent and its judge')
        ids = []
        for amount in amounts:
            key = uuid4().hex
            ids.append(key)
            self.state['entries'][key] = {'run_id': run_id, 'reserved_utc': utc_now(),
                                         'status': 'reserved', 'maximum_usd': str(amount),
                                         'charged_or_reserved_usd': str(amount)}
        save_json(self.path, self.state)
        return ids

    def settle(self, key, actual, *, sent=True):
        entry = self.state['entries'][key]
        if entry['status'] != 'reserved':
            raise ValueError('Reservation already settled')
        reserved = Decimal(entry['maximum_usd'])
        charged = actual if actual is not None else reserved
        if not sent:
            charged = Decimal(0)
        entry.update(status='estimated' if actual is not None and sent else 'unknown' if sent else 'not_sent',
                     charged_or_reserved_usd=str(charged), settled_utc=utc_now())
        save_json(self.path, self.state)
        if charged > reserved:
            raise BudgetExceeded('Observed cost exceeded its reservation; stopping all remaining paid requests')

    def snapshot(self, run_id=None):
        run_committed = sum((Decimal(e['charged_or_reserved_usd']) for e in self.state['entries'].values()
                             if e['run_id'] == run_id), Decimal(0)) if run_id else None
        return {'limit_usd': str(TOTAL_LIMIT), 'request_allowance_usd': str(REQUEST_LIMIT),
                'run_limit_usd': str(RUN_LIMIT), 'run_committed_usd': str(run_committed) if run_id else None,
                'shared_committed_usd': str(self.committed),
                'remaining_request_allowance_usd': str(max(Decimal(0), REQUEST_LIMIT - self.committed)),
                'unknown_or_interrupted_requests': sum(e['status'] in ('unknown', 'reserved')
                                                       for e in self.state['entries'].values())}
