"""Opt-in Decisions HTTPS transport. No network during import or preflight.

Only pre-approved full images/fixed crops and the existing predicate profiles
are accepted. This never locates boxes, generates names, or calls another API.
"""
import hashlib
import io
import json
import os
import time
import urllib.error
import urllib.request
import warnings

from PIL import Image

from .comparison import digest
from .decisions import ENDPOINT, verification_document, read_usage, read_verification
from .decisions_budget import (BudgetError, ComparisonBudget, PRICING_ID, check_pricing,
                               private_path, reservation, usage_cost, usd)

MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_RESPONSE_BYTES = 64 * 1024


class DecisionsError(Exception):
    """Only fixed, redacted messages cross the transport boundary."""


def _read(path, maximum):
    with private_path(path).open('rb') as handle:
        data = handle.read(maximum + 1)
    if not 0 < len(data) <= maximum:
        raise ValueError()
    return data


def _json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs,
                      parse_constant=lambda unused: (_ for _ in ()).throw(ValueError()))


def _image(record, *, original=False):
    if not isinstance(record, dict) or set(record) != {'path', 'sha256'}:
        raise ValueError()
    data = _read(record['path'], MAX_IMAGE_BYTES)
    if hashlib.sha256(data).hexdigest() != record['sha256']:
        raise ValueError()
    with warnings.catch_warnings():
        warnings.simplefilter('error')  # Decoder warnings must not leak input metadata.
        with Image.open(io.BytesIO(data)) as image:
            if (image.format not in ('JPEG', 'PNG') or image.getexif()
                    or getattr(image, 'is_animated', False)
                    or not (0 < image.width <= 1280 and 0 < image.height <= 960)
                    or (original and (image.format != 'JPEG' or image.size != (1280, 960)))):
                raise ValueError()
            image.verify()
    return data


class FrozenVerification:
    """Read only explicit private paths. Retain approved bytes in memory.

    Approval is an operator attestation bound to exact hashes, not a claim that
    code can infer consent. An optional retained historical manifest establishes
    byte identity; absent that artifact, this is explicitly a fresh evaluation.
    No truth or coordinates are accepted by the inference protocol.
    """
    def __init__(self, manifest_path):
        try:
            manifest = _json(_read(manifest_path, 64 * 1024))
            required = {'schema', 'images_approved', 'request_count_approved', 'pricing_id',
                        'standard_pricing_and_funding_reviewed', 'originals', 'runs'}
            optional = {'historical_manifest', 'fixed_crop_set_approved'}
            if (not isinstance(manifest, dict) or not required <= set(manifest)
                    or set(manifest) - required - optional
                    or type(manifest['schema']) is not int or manifest['schema'] != 1
                    or manifest['images_approved'] is not True
                    or manifest['standard_pricing_and_funding_reviewed'] is not True
                    or manifest['pricing_id'] != PRICING_ID
                    or not isinstance(manifest['originals'], list) or len(manifest['originals']) != 2
                    or not isinstance(manifest['runs'], list) or not 1 <= len(manifest['runs']) <= 2
                    or type(manifest['request_count_approved']) is not int
                    or manifest['request_count_approved'] != len(manifest['runs'])):
                raise ValueError()
            originals = [_image(record, original=True) for record in manifest['originals']]
            hashes = [hashlib.sha256(data).hexdigest() for data in originals]
            if len(set(hashes)) != 2:
                raise ValueError()
            self.basis = 'fresh_evaluation_historical_match_unverified'
            historical_hashes = None
            if 'historical_manifest' in manifest:
                historical = _json(_read(manifest['historical_manifest'], 64 * 1024))
                historical_hashes = [historical['inputs'][scene]['sha256'] for scene in ('scene-A', 'scene-B')]
                if historical_hashes != hashes:
                    raise ValueError()
                self.basis = 'original_bytes_match_retained_manifest'
            self._documents = []
            self.questions = []
            self._runs = []
            for run in manifest['runs']:
                if (not isinstance(run, dict) or not {'source', 'task', 'condition'} <= set(run)
                        or type(run['source']) is not int or run['source'] not in (0, 1)):
                    raise ValueError()
                if run['task'] == 'presence':
                    if set(run) != {'source', 'task', 'condition'}:
                        raise ValueError()
                    data = originals[run['source']]
                elif run['task'] == 'crop_verification':
                    if (set(run) != {'source', 'task', 'condition', 'crop', 'crop_origin'}
                            or manifest.get('fixed_crop_set_approved') is not True
                            or run['crop_origin'] not in ('independent', 'jev', 'truth_oracle')):
                        raise ValueError()
                    data = _image(run['crop'])
                else:
                    raise ValueError()
                document = verification_document(data, run['condition'], run['task'])
                encoded = json.dumps(document, allow_nan=False, separators=(',', ':')).encode()
                if len(encoded) > 24 * 1024 * 1024:
                    raise ValueError()
                self._documents.append(encoded)
                self.questions.append(len(document['questions']))
                self._runs.append({key: run[key] for key in ('task', 'condition', 'crop_origin') if key in run})
            # Bind the private approval, original experiment hashes, and exact
            # request bytes. These digests never appear in console summaries.
            self.sha256 = digest({'approval': manifest, 'historical_hashes': historical_hashes,
                                  'documents': [hashlib.sha256(d).hexdigest() for d in self._documents]})
        except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError,
                Image.DecompressionBombError, Warning):
            raise DecisionsError('Approved private input manifest invalid or unavailable; contents omitted') from None

    def preflight(self, *, require_current=True):
        if require_current:
            check_pricing(PRICING_ID)
        upper = sum(reservation(q) for q in self.questions)
        return {'evidence': 'preflight_only', 'evaluation_basis': self.basis,
                'planned_http': len(self.questions), 'plan_reservation_usd': usd(upper),
                'api_calls_this_command': 0, 'native_bbox': False,
                'native_open_identity': False}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise DecisionsError('Decisions redirect refused; no retry')


class DecisionsClient:
    """A single attempt per frozen slot. The budget owns the process-wide lock."""
    def __init__(self, protocol, budget, *, approve_live=False, timeout_seconds=30):
        # Gate before inspecting credentials or constructing an opener.
        if approve_live is not True or os.environ.get('MTG_DECISIONS_ENABLE_LIVE') != '1':
            raise DecisionsError('Live disabled: explicit flag and MTG_DECISIONS_ENABLE_LIVE=1 required')
        if type(protocol) is not FrozenVerification or type(budget) is not ComparisonBudget:
            raise DecisionsError('Frozen approved inputs and shared comparison budget required')
        protocol.preflight()
        budget.preflight()
        if budget.data['manifest_sha256'] != protocol.sha256 or budget.questions != protocol.questions:
            raise DecisionsError('Budget is not bound to these approved inputs')
        if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 30:
            raise DecisionsError('A socket timeout in (0, 30] seconds is required')
        key = os.environ.get('OPENAI_API_KEY', '')
        if not key or key == 'YOUR_OPENAI_API_KEY' or any(c.isspace() for c in key):
            raise DecisionsError('A securely supplied OPENAI_API_KEY is required')
        self._key = key  # Opaque cloud placeholder; never transform, log or save it.
        self.protocol, self.budget, self.timeout = protocol, budget, timeout_seconds
        # Default proxy handling and TLS verification remain enabled.
        try:
            self._opener = urllib.request.build_opener(NoRedirect())
        except Exception:
            raise DecisionsError('Decisions proxy/opener unavailable; no request attempted') from None

    def run(self, *, cancelled=lambda: False):
        with self.budget.mutex:
            return self._run(cancelled)

    def _run(self, cancelled):
        try:
            self.protocol.preflight()
            before = self.budget.preflight()['completed_requests']
            for index, (body, run) in enumerate(zip(self.protocol._documents, self.protocol._runs)):
                if self.budget.data['entries'][index]['state'] == 'complete':
                    continue  # Resume the unexecuted prefix, never replay a charged slot.
                if cancelled():
                    raise DecisionsError('Cancelled before sending; no request attempted')
                self.protocol.preflight()  # Recheck the dated bound before every send.
                request = urllib.request.Request(ENDPOINT, data=body, method='POST', headers={
                    'Authorization': 'Bearer ' + self._key,
                    'Content-Type': 'application/json', 'Accept': 'application/json'})
                self.budget.reserve(index)
                sent = False
                start = time.monotonic()
                try:
                    if cancelled():
                        raise DecisionsError('Cancelled before sending; no request attempted')
                    sent = True  # Any interruption from this point may have been charged.
                    with self._opener.open(request, timeout=self.timeout) as response:
                        # Bounded response; never read or expose error bodies/headers.
                        raw = response.read(MAX_RESPONSE_BYTES + 1)
                        if len(raw) > MAX_RESPONSE_BYTES:
                            raise ValueError()
                    value = _json(raw)
                    usage = read_usage(value, strict=True)
                    usage_cost(usage, self.protocol.questions[index])
                    try:
                        answers = read_verification(value, run['task'])['answers']
                    except ValueError:
                        # Charge known usage even when the prediction is unusable.
                        self.budget.finish(index, usage, None, time.monotonic() - start)
                        raise DecisionsError('Invalid Decisions answers; usage reconciled; no retry') from None
                    self.budget.finish(index, usage, answers, time.monotonic() - start)
                    if cancelled():
                        self.budget.stop(index, sent=True)
                        raise DecisionsError('Cancelled after response; usage reconciled; no retry')
                except BaseException as exc:
                    if isinstance(exc, urllib.error.HTTPError):
                        exc.close()
                    self.budget.stop(index, sent=sent)
                    raise DecisionsError('Decisions request stopped; inspect private ledger; no retry') from None
            return {'evidence': 'live', 'evaluation_basis': self.protocol.basis,
                    'native_bbox': False, 'native_open_identity': False,
                    'api_calls_this_command': len(self.protocol.questions) - before,
                    'results': [{**entry, **run} for entry, run in zip(self.budget.results(), self.protocol._runs)]}
        except (BudgetError, OSError, ValueError):
            raise DecisionsError('Decisions preflight/accounting failed; no retry') from None
