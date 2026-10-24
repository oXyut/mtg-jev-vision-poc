"""Private, durable accounting for the one Issue #25 comparison. No network.

All cooperating invocations must use the SAME ledger. This is a local spending
gate, not an account limit or protection against deletion/tampering by its owner.
Money is integer nanodollars; provider charges are bounded from token usage,
not misrepresented as an observed invoice.
"""
import copy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading

CAP_NANOS = 500_000_000
CONTEXT_TOKENS = 1_050_000
OUTPUT_TOKENS = 128_000
LONG_CONTEXT_THRESHOLD = 272_000
PRICING_DATE = '2026-10-24'
PRICING_ID = 'decisions-luna-2026-10-24-standard-no-cache'
REPO = Path(__file__).resolve().parents[1]


class BudgetError(ValueError):
    pass


def private_path(path):
    """Resolve only an explicitly supplied path, never search for private files."""
    p = Path(path)
    if not p.is_absolute() or p.is_symlink() or p.resolve().is_relative_to(REPO):
        raise BudgetError('Private files must use absolute paths outside the checkout')
    return p.resolve()


def nanos(value):
    try:
        if not isinstance(value, str):
            raise ValueError()
        d = Decimal(value)
        if not d.is_finite() or not 0 <= d <= Decimal('.50'):
            raise ValueError()
        return int((d * 1_000_000_000).to_integral_value(rounding=ROUND_CEILING))
    except (InvalidOperation, ValueError, OverflowError):
        raise BudgetError('Explicit known prior comparison spend in USD is required') from None


def usd(value):
    return format(Decimal(value) / 1_000_000_000, '.9f')


def check_pricing(pricing_id):
    # A later task must review official prices/limits and update this snapshot.
    if (pricing_id != PRICING_ID
            or datetime.now(timezone.utc).date().isoformat() != PRICING_DATE):
        raise BudgetError('Pricing/limit snapshot is unconfirmed or stale; live disabled')


def reservation(questions):
    if type(questions) is not int or questions not in (1, 2):
        raise BudgetError('Only one or two predicate questions are supported')
    # $0.10/M = 100 nanos/token. Reserve full context FOR EACH question,
    # even if shared input is counted repeatedly; 2x long context, 1.1x region.
    # Explicit caching without breakpoints is mandatory. Output costs $0.
    return questions * CONTEXT_TOKENS * 220


def usage_cost(usage, questions):
    if (set(usage) != {'input_tokens', 'output_tokens', 'cached_tokens', 'cache_write_tokens'}
            or any(type(v) is not int or v < 0 for v in usage.values())
            or usage['cached_tokens'] or usage['cache_write_tokens']
            or usage['input_tokens'] + usage['output_tokens'] > questions * CONTEXT_TOKENS
            or usage['output_tokens'] > questions * OUTPUT_TOKENS):
        raise BudgetError('Usage violates the verified cost bound; halt without retry')
    rate = 220 if usage['input_tokens'] > LONG_CONTEXT_THRESHOLD else 110
    cost = usage['input_tokens'] * rate
    if cost > reservation(questions):
        raise BudgetError('Usage exceeds reservation; halt without retry')
    return cost


class ComparisonBudget:
    """Single writer across threads/processes; lock stays held for the full run.

    Reserved/pending is fsynced before HTTP. An interrupted process leaves a
    pending entry that blocks restart. There is deliberately no reset, retry,
    or automatic uncertain-request reconciliation operation.
    """
    def __init__(self, path, manifest_sha256, questions, prior_spend_usd, *, create=False):
        self.path = private_path(path)
        self.mutex = threading.RLock()
        self.broken = False
        self.lock_file = None
        if (not isinstance(manifest_sha256, str) or not re.fullmatch('[0-9a-f]{64}', manifest_sha256)
                or not isinstance(questions, list) or not 1 <= len(questions) <= 2):
            raise BudgetError('Invalid frozen comparison or approved request count')
        self.questions = list(questions)
        self.bounds = [reservation(q) for q in questions]
        prior = nanos(prior_spend_usd)
        if create and prior + sum(self.bounds) > CAP_NANOS:
            raise BudgetError('Entire plan exceeds USD 0.50; no ledger or request created')
        try:
            lock = self.path.with_name(self.path.name + '.lock')
            fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            self.lock_file = os.fdopen(fd, 'a')
            self._secure(self.lock_file.fileno())
            fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if create:
                # Exclusive creation never replaces an existing comparison book.
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                os.close(fd)
                self.data = {'schema': 1, 'comparison': 'issue-25', 'cap_nanos': CAP_NANOS,
                             'manifest_sha256': manifest_sha256, 'pricing_id': PRICING_ID,
                             'prior_nanos': prior, 'halted': False,
                             'entries': [{'state': 'planned', 'questions': q, 'usage': None,
                                          'accounted_nanos': None, 'answers': None, 'seconds': None}
                                         for q in questions]}
            else:
                fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(fd) as handle:
                    self._secure(handle.fileno())
                    if os.fstat(handle.fileno()).st_size > 1024 * 1024:
                        raise BudgetError('Invalid private budget ledger')
                    self.data = json.load(handle)
                self._validate(manifest_sha256)
                if prior < self.data['prior_nanos']:
                    raise BudgetError('Prior comparison spend cannot be reduced')
                self.data['prior_nanos'] = prior
            self.preflight()
            self._write()
        except BaseException as exc:
            self.close()
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise BudgetError('Budget ledger unavailable, locked, halted, over cap or invalid') from None

    @staticmethod
    def _secure(fd):
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or s.st_mode & 0o077 or s.st_nlink != 1:
            raise BudgetError('Budget ledger must be a private regular file')

    def _validate(self, manifest_sha256):
        d = self.data
        if (set(d) != {'schema', 'comparison', 'cap_nanos', 'manifest_sha256', 'pricing_id',
                       'prior_nanos', 'halted', 'entries'}
                or type(d['schema']) is not int or d['schema'] != 1
                or d['comparison'] != 'issue-25' or d['cap_nanos'] != CAP_NANOS
                or d['manifest_sha256'] != manifest_sha256 or d['pricing_id'] != PRICING_ID
                or type(d['prior_nanos']) is not int or not 0 <= d['prior_nanos'] <= CAP_NANOS
                or type(d['halted']) is not bool or len(d['entries']) != len(self.questions)):
            raise BudgetError('Frozen budget differs')
        saw_unfinished = False
        for entry, q in zip(d['entries'], self.questions):
            if (set(entry) != {'state', 'questions', 'usage', 'accounted_nanos', 'answers', 'seconds'}
                    or type(entry['questions']) is not int or entry['questions'] != q
                    or entry['state'] not in ('planned', 'pending', 'complete', 'answer_error', 'uncertain', 'cancelled')):
                raise BudgetError('Invalid budget entry')
            if entry['state'] in ('complete', 'answer_error'):
                if (saw_unfinished or type(entry['accounted_nanos']) is not int
                        or entry['accounted_nanos'] != usage_cost(entry['usage'], q)):
                    raise BudgetError('Usage reconciliation differs')
                self._answers(entry['answers'], q, entry['state'] == 'complete')
                self._seconds(entry['seconds'])
            else:
                saw_unfinished = True
                if any(entry[k] is not None for k in ('usage', 'accounted_nanos', 'answers', 'seconds')):
                    raise BudgetError('Unresolved usage must remain unknown')

    @staticmethod
    def _answers(answers, questions, required=True):
        if not required and answers is None:
            return
        names = ['present'] if questions == 1 else ['present', 'single']
        if not isinstance(answers, dict) or list(answers) != names:
            raise BudgetError('Invalid normalized predicates')
        for answer in answers.values():
            if not isinstance(answer, dict) or set(answer) != {'status', 'probability'}:
                raise BudgetError('Invalid normalized predicates')
            p = answer['probability']
            if not ((answer['status'] == 'refused' and p is None)
                    or (answer['status'] == 'answered' and type(p) in (int, float) and 0 <= p <= 1)):
                raise BudgetError('Invalid normalized predicates')

    @staticmethod
    def _seconds(seconds):
        import math
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
            raise BudgetError('Invalid elapsed time')

    def _write(self):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=self.path.parent, delete=False) as handle:
                temporary = handle.name
                json.dump(self.data, handle, allow_nan=False, separators=(',', ':'))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except BaseException:
            self.broken = True
            raise BudgetError('Budget persistence failed; halt without retry') from None
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def preflight(self):
        with self.mutex:
            if self.broken or not self.lock_file or self.lock_file.closed or self.data['halted']:
                raise BudgetError('Budget halted or closed; no automatic retry')
            if any(e['state'] not in ('planned', 'complete') for e in self.data['entries']):
                raise BudgetError('Prior request unresolved or failed; no automatic retry')
            upper = self.data['prior_nanos'] + sum(
                e['accounted_nanos'] if e['state'] == 'complete' else bound
                for e, bound in zip(self.data['entries'], self.bounds))
            if upper > CAP_NANOS:
                raise BudgetError('Entire remaining plan exceeds USD 0.50; no requests sent')
            return {'cap_usd': usd(CAP_NANOS), 'plan_cost_upper_bound_usd': usd(upper),
                    'completed_requests': sum(e['state'] == 'complete' for e in self.data['entries']),
                    'planned_requests': len(self.questions)}

    def reserve(self, index):
        with self.mutex:
            self.preflight()
            expected = next((i for i, e in enumerate(self.data['entries']) if e['state'] == 'planned'), None)
            if type(index) is not int or index != expected:
                raise BudgetError('No repeats, omissions or concurrent requests')
            self.data['entries'][index]['state'] = 'pending'
            self._write()  # Must succeed durably BEFORE entering the HTTP send.

    def finish(self, index, usage, answers, seconds):
        with self.mutex:
            entry = self.data['entries'][index]
            if entry['state'] != 'pending' or self.broken:
                raise BudgetError('No pending reservation')
            cost = usage_cost(usage, self.questions[index])
            self._answers(answers, self.questions[index], answers is not None)
            self._seconds(seconds)
            entry.update(state='complete' if answers is not None else 'answer_error',
                         usage=copy.deepcopy(usage), accounted_nanos=cost,
                         answers=copy.deepcopy(answers), seconds=seconds)
            self.data['halted'] = answers is None
            self._write()

    def stop(self, index, *, sent):
        with self.mutex:
            if self.broken:
                return  # The on-disk pending reservation remains authoritative.
            entry = self.data['entries'][index]
            if entry['state'] == 'pending':
                entry['state'] = 'uncertain' if sent else 'cancelled'
            self.data['halted'] = True
            self._write()

    def results(self):
        with self.mutex:
            return [{'index': i, 'status': e['state'], 'usage': copy.deepcopy(e['usage']),
                     'answers': copy.deepcopy(e['answers']), 'seconds': e['seconds'],
                     'cost_upper_bound_usd': (usd(e['accounted_nanos'])
                                              if e['accounted_nanos'] is not None else
                                              usd(0) if e['state'] == 'cancelled' else None),
                     'reserved_upper_bound_usd': usd(reservation(e['questions']))
                         if e['state'] in ('pending', 'uncertain') else usd(0),
                     'http_attempted_upper_bound': int(e['state'] not in ('planned', 'cancelled'))}
                    for i, e in enumerate(self.data['entries'])]

    def close(self):
        with self.mutex:
            if self.lock_file and not self.lock_file.closed:
                self.lock_file.close()

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()
