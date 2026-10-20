"""Metadata-only local accounting; never stores images, answers or credentials."""
import fcntl
import json
import math
import os
from pathlib import Path
import threading
from datetime import datetime, timezone

from .provider import PRICE_PER_MILLION_INPUT

LEDGER_MINIMUM_PRIOR_USD = .025210836  # Original schema-1 books remain readable.
POC_SPENT_BEFORE_CAMERA = .049285530  # Known Cloud only; preserves local usage separately.
POC_LIMIT_USD = 5.0


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


class UsageLedger:
    def __init__(self, path, prior_spend=POC_SPENT_BEFORE_CAMERA):
        if not math.isfinite(prior_spend) or not POC_SPENT_BEFORE_CAMERA <= prior_spend < POC_LIMIT_USD:
            raise ValueError('Invalid prior PoC spend')
        self.path = Path(path)
        self.mutex = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_file = self.path.with_name(self.path.name+'.lock').open('a')
        try:
            fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self.path.exists():
                if self.path.stat().st_size > 4*1024*1024:
                    raise ValueError('Usage ledger too large')
                self.data = json.loads(self.path.read_text())
                self.validate()
                self.data['prior_spend_usd'] = max(prior_spend, self.data['prior_spend_usd'])
            else:
                self.data = {'schema_version': 1, 'prior_spend_usd': prior_spend,
                             'input_tokens': 0, 'output_tokens': 0, 'requests_attempted': 0,
                             'pending': None, 'halt_reason': None, 'history': []}
            self.write()
        except Exception:
            self.close()
            raise ValueError('Usage ledger unavailable, locked or invalid') from None

    def validate(self):
        d = self.data
        if d.get('schema_version') != 1:
            raise ValueError('Unknown usage schema')
        if not isinstance(d.get('prior_spend_usd'), (int, float)) or not math.isfinite(d['prior_spend_usd']) or not LEDGER_MINIMUM_PRIOR_USD <= d['prior_spend_usd'] < POC_LIMIT_USD:
            raise ValueError('Invalid prior cost')
        for k in ('input_tokens', 'output_tokens', 'requests_attempted'):
            if type(d.get(k)) is not int or d[k] < 0:
                raise ValueError('Invalid usage counter')
        history = d.get('history')
        if not isinstance(history, list) or len(history) > 10000:
            raise ValueError('Invalid usage history')
        # Validate totals: an edited or truncated ledger must not silently reset cost.
        if len(history) != d['requests_attempted']:
            raise ValueError('Usage history count mismatch')
        for h in history:
            if h.get('status') not in ('completed', 'usage_unknown'):
                raise ValueError('Invalid usage status')
            u = h.get('usage', {})
            if h['status'] == 'completed' and any(type(u.get(k)) is not int or u[k] < 0 for k in ('input_tokens', 'output_tokens')):
                raise ValueError('Invalid recorded usage')
        for k in ('input_tokens', 'output_tokens'):
            if sum(h.get('usage', {}).get(k, 0) for h in history) != d[k]:
                raise ValueError('Usage total mismatch')

    @property
    def spent(self):
        with self.mutex:
            return self.data['prior_spend_usd'] + self.data['input_tokens']*PRICE_PER_MILLION_INPUT/1e6

    def write(self):
        temporary = self.path.with_name(self.path.name+'.tmp')
        fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w') as handle:
            json.dump(self.data, handle, allow_nan=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    def begin(self, questions):
        with self.mutex:
            if self.data['pending'] or self.data['halt_reason']:
                raise ValueError('Unresolved prior request; accounting must be reconciled first')
            self.data['pending'] = {'started_at': utc_now(), 'questions': questions}
            self.write()  # Durable BEFORE sending; a crash cannot erase an uncertain bill.

    def finish(self, usage):
        with self.mutex:
            pending = self.data['pending']
            if not pending:
                raise ValueError('Missing pending request')
            self.data['history'].append({**pending, 'completed_at': utc_now(),
                                         'status': 'completed', 'usage': usage})
            self.data['requests_attempted'] += 1
            for k in ('input_tokens', 'output_tokens'):
                self.data[k] += usage[k]
            self.data['pending'] = None
            self.write()

    def failed(self, sent):
        with self.mutex:
            if sent:
                self.data['history'].append({**self.data['pending'], 'completed_at': utc_now(),
                                             'status': 'usage_unknown'})
                self.data['requests_attempted'] += 1
                self.data['halt_reason'] = 'usage_unknown_no_retry'
                # Preserve pending to make the uncertainty visible across restarts.
            else:
                self.data['pending'] = None
            self.write()

    def halt(self, reason):
        with self.mutex:
            self.data['halt_reason'] = reason
            self.write()

    def close(self):
        handle = getattr(self, 'lock_file', None)
        if handle and not handle.closed:
            handle.close()
