"""Stubbed HTTP and synthetic fixtures ONLY. No measured recognition/charges."""
import contextlib
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from email.message import Message
from unittest.mock import patch
from urllib.response import addinfourl

from PIL import Image

from mtg_jev.decisions import ENDPOINT, MODEL, read_usage
from mtg_jev.decisions_budget import (BudgetError, CAP_NANOS, ComparisonBudget, CONTEXT_TOKENS,
                                     PRICING_ID, check_pricing, nanos, reservation, usage_cost)
from mtg_jev.decisions_transport import (DecisionsClient, DecisionsError, FrozenVerification,
                                        MAX_RESPONSE_BYTES)

MARKER = '__artificial_decisions_marker_not_a_credential__'
PROXY = 'http://proxy.test.invalid:3128'


def response(questions=1, tokens=100):
    return {'model': MODEL, 'answers': [{'name': name, 'type': 'predicate', 'probability': .9}
             for name in (['present'] if questions == 1 else ['present', 'single'])],
            'usage': {'input_tokens': tokens, 'output_tokens': 0,
                      'input_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0},
                      'output_tokens_details': {'reasoning_tokens': 0}, 'total_tokens': tokens}}


class FakeHTTPS(urllib.request.HTTPSHandler):
    def __init__(self, *, body=None, code=200, location=None, failure=None, read_failure=None):
        super().__init__()
        self.body = json.dumps(response()).encode() if body is None else body
        self.code, self.location = code, location
        self.failure, self.read_failure = failure, read_failure
        self.requests = []

    def https_open(self, request):
        self.requests.append(request)
        if self.failure:
            raise self.failure
        headers = Message()
        if self.location:
            headers['Location'] = self.location
        body = io.BytesIO(self.body)
        if self.read_failure:
            body.read = lambda *unused: (_ for _ in ()).throw(self.read_failure)
        result = addinfourl(body, headers, request.full_url, self.code)
        result.msg = 'Artificial response'
        return result


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.manifest = self.folder / 'approval.json'
        self.ledger = self.folder / 'comparison-budget.json'
        originals = []
        for index, color in enumerate(('white', 'black')):
            p = self.folder / f'synthetic-{index}.jpg'
            out = io.BytesIO()
            Image.new('RGB', (1280, 960), color).save(out, 'JPEG')
            p.write_bytes(out.getvalue())
            originals.append({'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()})
        self.approval = {'schema': 1, 'images_approved': True, 'request_count_approved': 1,
                         'pricing_id': PRICING_ID, 'standard_pricing_and_funding_reviewed': True,
                         'originals': originals,
                         'runs': [{'source': 0, 'task': 'presence', 'condition': 'mtg_plain'}]}
        self.write_manifest()
        p = patch('mtg_jev.decisions_budget.datetime')
        p.start().now.return_value = datetime(2026, 10, 24, tzinfo=timezone.utc)
        self.addCleanup(p.stop)
        # All transport tests use artificial env values. Never read real keys.
        for target in ('socket.create_connection', 'socket.socket.connect', 'socket.getaddrinfo'):
            p = patch(target, side_effect=AssertionError('Network forbidden'))
            p.start()
            self.addCleanup(p.stop)
        p = patch.dict(os.environ, {'MTG_DECISIONS_ENABLE_LIVE': '1', 'OPENAI_API_KEY': MARKER,
                                   'HTTPS_PROXY': PROXY}, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def write_manifest(self):
        self.manifest.write_text(json.dumps(self.approval))

    def protocol(self):
        self.write_manifest()
        return FrozenVerification(str(self.manifest))

    def budget(self, protocol, *, path=None, create=True, prior='0'):
        budget = ComparisonBudget(str(path or self.ledger), protocol.sha256, protocol.questions,
                                  prior, create=create)
        self.addCleanup(budget.close)
        return budget

    @contextlib.contextmanager
    def fake(self, fake):
        build = urllib.request.build_opener
        with patch('urllib.request.build_opener', side_effect=lambda *h: build(*h, fake)):
            yield

    def test_exact_wire_schema_proxy_placeholder_and_private_results(self):
        protocol = self.protocol()
        budget = self.budget(protocol)
        fake = FakeHTTPS(body=json.dumps({**response(), 'ignored_private': 'RAW_SENTINEL'}).encode())
        out, err = io.StringIO(), io.StringIO()
        with self.fake(fake), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = DecisionsClient(protocol, budget, approve_live=True).run()
        self.assertEqual((out.getvalue(), err.getvalue()), ('', ''))
        self.assertEqual(len(fake.requests), 1)
        req = fake.requests[0]
        self.assertEqual((req.full_url, req.get_method()), (ENDPOINT, 'POST'))
        self.assertEqual(req._tunnel_host, 'api.openai.com')
        self.assertEqual(req.host, 'proxy.test.invalid:3128')
        self.assertEqual(req.get_header('Authorization'), 'Bearer ' + MARKER)
        document = json.loads(req.data)
        self.assertEqual(set(document), {'model', 'input', 'questions', 'prompt_cache_options'})
        self.assertEqual(document['prompt_cache_options'], {'mode': 'explicit'})
        self.assertEqual(document['model'], MODEL)
        self.assertEqual(document['questions'][0]['type'], 'predicate')
        self.assertEqual(result['api_calls_this_command'], 1)
        self.assertFalse(result['native_bbox'])
        self.assertEqual(result['results'][0]['cost_upper_bound_usd'], '0.000011000')
        saved = self.ledger.read_text()
        for private in (MARKER, 'RAW_SENTINEL', 'base64', 'image_url', 'Authorization', 'Bearer',
                        str(self.folder), 'left', 'right'):
            self.assertNotIn(private, saved)
        self.assertEqual(self.ledger.stat().st_mode & 0o777, 0o600)

    def test_opt_in_and_key_gates_before_opener(self):
        protocol = self.protocol()
        budget = self.budget(protocol)
        cases = [(False, {'OPENAI_API_KEY': MARKER}),
                 (True, {'OPENAI_API_KEY': MARKER}),
                 (True, {'MTG_DECISIONS_ENABLE_LIVE': '1'}),
                 (True, {'MTG_DECISIONS_ENABLE_LIVE': '1', 'OPENAI_API_KEY': 'YOUR_OPENAI_API_KEY'})]
        for flag, env in cases:
            with patch.dict(os.environ, env, clear=True), patch('urllib.request.build_opener') as opener:
                with self.assertRaises(DecisionsError):
                    DecisionsClient(protocol, budget, approve_live=flag)
                opener.assert_not_called()
        self.assertEqual(budget.results()[0]['http_attempted_upper_bound'], 0)

    def test_schema_approval_hash_original_and_historical_validation(self):
        variants = [dict(self.approval, images_approved=False),
                    dict(self.approval, schema=True),
                    dict(self.approval, request_count_approved=2),
                    dict(self.approval, standard_pricing_and_funding_reviewed=False),
                    dict(self.approval, coordinates=[]),
                    dict(self.approval, runs=[{'source': 0, 'task': 'bbox', 'condition': 'mtg_plain'}])]
        for changed in variants:
            self.manifest.write_text(json.dumps(changed))
            with self.assertRaises(DecisionsError): FrozenVerification(str(self.manifest))
        self.write_manifest()
        original = Path(self.approval['originals'][0]['path'])
        original.write_bytes(original.read_bytes() + b'changed')
        with self.assertRaises(DecisionsError): self.protocol()

    def test_crop_is_separate_two_predicates_refusal_and_oracle_label(self):
        self.approval['fixed_crop_set_approved'] = True
        self.approval['runs'] = [{'source': 0, 'task': 'crop_verification', 'condition': 'generic_explicit',
                                 'crop': self.approval['originals'][0], 'crop_origin': 'truth_oracle'}]
        protocol = self.protocol()
        self.assertEqual(protocol.questions, [2])
        self.assertEqual(protocol.preflight()['plan_reservation_usd'], '0.462000000')
        data = response(2)
        data['answers'][1] = {'name': 'single', 'type': 'refusal'}
        fake = FakeHTTPS(body=json.dumps(data).encode())
        with self.fake(fake):
            result = DecisionsClient(protocol, self.budget(protocol), approve_live=True).run()
        self.assertEqual(result['results'][0]['crop_origin'], 'truth_oracle')
        self.assertEqual(result['results'][0]['answers']['single'], {'status': 'refused', 'probability': None})
        self.approval['fixed_crop_set_approved'] = False
        with self.assertRaises(DecisionsError): self.protocol()

    def test_retained_original_manifest_only_proves_byte_match(self):
        self.assertIn('unverified', self.protocol().basis)
        historic = self.folder / 'historical.json'
        historic.write_text(json.dumps({'inputs': {scene: self.approval['originals'][i]
                              for i, scene in enumerate(('scene-A', 'scene-B'))}}))
        self.approval['historical_manifest'] = str(historic)
        self.assertEqual(self.protocol().basis, 'original_bytes_match_retained_manifest')
        historic.write_text('{"inputs": {}}')
        with self.assertRaises(DecisionsError): self.protocol()

    def test_two_crop_plan_is_rejected_before_credential_lookup_or_opener(self):
        self.approval['fixed_crop_set_approved'] = True
        self.approval['runs'] = [{'source': i, 'task': 'crop_verification', 'condition': 'mtg_plain',
                                 'crop': record, 'crop_origin': 'independent'}
                                for i, record in enumerate(self.approval['originals'])]
        self.approval['request_count_approved'] = 2
        protocol = self.protocol()
        with patch('os.environ.get', side_effect=AssertionError('No credential lookup')), \
             patch('urllib.request.build_opener') as opener:
            with self.assertRaises(BudgetError): self.budget(protocol)
            opener.assert_not_called()
        self.assertFalse(self.ledger.exists())

    def test_original_exif_dimensions_and_decoder_warnings_are_rejected_quietly(self):
        record = self.approval['originals'][0]
        for size, exif in (((1280, 960), True), ((1280, 959), False)):
            data = io.BytesIO()
            metadata = Image.Exif()
            if exif: metadata[274] = 1
            Image.new('RGB', size).save(data, 'JPEG', exif=metadata)
            Path(record['path']).write_bytes(data.getvalue())
            record['sha256'] = hashlib.sha256(data.getvalue()).hexdigest()
            with self.assertRaises(DecisionsError): self.protocol()
        out, err = io.StringIO(), io.StringIO()
        with patch('PIL.Image.open', side_effect=Image.DecompressionBombWarning('PRIVATE_METADATA')), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with self.assertRaises(DecisionsError) as caught: self.protocol()
        self.assertNotIn('PRIVATE_METADATA', str(caught.exception))
        self.assertEqual(out.getvalue() + err.getvalue(), '')

    def test_no_redirect_auth_or_rate_error_retries_and_redaction(self):
        cases = [(code, dest) for code in (301, 302, 303, 307, 308)
                 for dest in ('https://other.test.invalid/', 'http://api.openai.com/',
                              'https://api.openai.com:8443/', '/other')]
        cases += [(code, None) for code in (401, 429, 500)]
        protocol = self.protocol()
        for number, (code, dest) in enumerate(cases):
            budget = self.budget(protocol, path=self.folder / f'error-{number}.json')
            fake = FakeHTTPS(code=code, location=dest, body=MARKER.encode())
            with self.fake(fake):
                client = DecisionsClient(protocol, budget, approve_live=True)
                with self.assertRaises(DecisionsError) as error: client.run()
                with self.assertRaises(DecisionsError): client.run()
            self.assertNotIn(MARKER, str(error.exception))
            self.assertEqual(len(fake.requests), 1)
            self.assertEqual(budget.results()[0]['status'], 'uncertain')
            self.assertIsNone(budget.results()[0]['cost_upper_bound_usd'])
            self.assertEqual(budget.results()[0]['reserved_upper_bound_usd'], '0.231000000')

    def test_timeout_connection_and_interruption_remain_uncertain_across_restart(self):
        protocol = self.protocol()
        failures = [TimeoutError(MARKER), urllib.error.URLError(MARKER), ConnectionResetError(MARKER),
                    KeyboardInterrupt(MARKER), SystemExit(MARKER)]
        for number, failure in enumerate(failures):
            path = self.folder / f'interrupted-{number}.json'
            budget = self.budget(protocol, path=path)
            fake = FakeHTTPS(failure=failure)
            with self.fake(fake):
                client = DecisionsClient(protocol, budget, approve_live=True)
                with self.assertRaises(DecisionsError): client.run()
            self.assertEqual(len(fake.requests), 1)
            self.assertEqual(budget.results()[0]['status'], 'uncertain')
            budget.close()
            with self.assertRaises(BudgetError): self.budget(protocol, path=path, create=False)

    def test_invalid_usage_body_model_and_schema_never_release_reservation(self):
        protocol = self.protocol()
        bad_model = response(); bad_model['model'] = 'other'
        bad_total = response(); bad_total['usage']['total_tokens'] = 99
        cache = response(); cache['usage']['input_tokens_details']['cache_write_tokens'] = 1
        over = response(tokens=CONTEXT_TOKENS + 1)
        data = [b'not-json-' + MARKER.encode(), b'\xff', b'{"model":1,"model":2}', b'{"usage":NaN}',
                b'x' * (MAX_RESPONSE_BYTES + 1)]
        data += [json.dumps(value).encode() for value in ({}, bad_model, bad_total, cache, over)]
        for number, body in enumerate(data):
            budget = self.budget(protocol, path=self.folder / f'invalid-{number}.json')
            fake = FakeHTTPS(body=body)
            with self.fake(fake):
                with self.assertRaises(DecisionsError):
                    DecisionsClient(protocol, budget, approve_live=True).run()
            self.assertEqual(budget.results()[0]['status'], 'uncertain')
            self.assertIsNone(budget.results()[0]['usage'])
            self.assertNotIn(MARKER, budget.path.read_text())

    def test_bad_answers_reconcile_known_usage_then_halt(self):
        protocol = self.protocol()
        value = response(); value['answers'][0]['probability'] = True
        budget = self.budget(protocol)
        fake = FakeHTTPS(body=json.dumps(value).encode())
        with self.fake(fake):
            with self.assertRaises(DecisionsError): DecisionsClient(protocol, budget, approve_live=True).run()
        self.assertEqual(budget.results()[0]['status'], 'answer_error')
        self.assertEqual(budget.results()[0]['cost_upper_bound_usd'], '0.000011000')
        with self.assertRaises(BudgetError): budget.preflight()

    def test_cancel_before_send_and_after_response_and_read_timeout(self):
        protocol = self.protocol()
        for number, (cancelled, fake, expected) in enumerate((
                (lambda: True, FakeHTTPS(), 'planned'),
                (iter([False, True]).__next__, FakeHTTPS(), 'cancelled'),
                (iter([False, False, True]).__next__, FakeHTTPS(), 'complete'),
                (lambda: False, FakeHTTPS(read_failure=TimeoutError(MARKER)), 'uncertain'))):
            budget = self.budget(protocol, path=self.folder / f'cancel-{number}.json')
            with self.fake(fake):
                with self.assertRaises(DecisionsError):
                    DecisionsClient(protocol, budget, approve_live=True).run(cancelled=cancelled)
            self.assertEqual(budget.results()[0]['status'], expected)
            self.assertEqual(len(fake.requests), int(expected in ('complete', 'uncertain')))
            if expected == 'cancelled':
                self.assertEqual(budget.results()[0]['cost_upper_bound_usd'], '0.000000000')

    def test_completed_prefix_resumes_without_replay_or_extra_http(self):
        self.approval['runs'].append({'source': 1, 'task': 'presence', 'condition': 'mtg_plain'})
        self.approval['request_count_approved'] = 2
        protocol = self.protocol()
        budget = self.budget(protocol)
        self.assertEqual(budget.preflight()['plan_cost_upper_bound_usd'], '0.462000000')
        budget.reserve(0)
        budget.finish(0, read_usage(response(), strict=True),
                      {'present': {'status': 'answered', 'probability': .9}}, .1)
        budget.close()
        budget = self.budget(protocol, create=False)
        fake = FakeHTTPS()
        with self.fake(fake):
            client = DecisionsClient(protocol, budget, approve_live=True)
            self.assertEqual(client.run()['api_calls_this_command'], 1)
            self.assertEqual(client.run()['api_calls_this_command'], 0)
        self.assertEqual(len(fake.requests), 1)

    def test_fsync_failure_before_send_and_after_send_stops(self):
        protocol = self.protocol()
        for number, fail_at in enumerate((1, 2)):
            budget = self.budget(protocol, path=self.folder / f'write-{number}.json')
            fake = FakeHTTPS()
            original = os.fsync
            count = 0
            def failing_fsync(fd):
                nonlocal count
                count += 1
                # _write fsyncs both file and directory.
                if count == (1 if fail_at == 1 else 3): raise OSError(MARKER)
                return original(fd)
            with self.fake(fake), patch('os.fsync', side_effect=failing_fsync):
                with self.assertRaises(DecisionsError):
                    DecisionsClient(protocol, budget, approve_live=True).run()
            self.assertEqual(len(fake.requests), fail_at - 1)
            with self.assertRaises(BudgetError): budget.preflight()
            if fail_at == 2:
                self.assertEqual(json.loads(budget.path.read_text())['entries'][0]['state'], 'pending')

    def test_budget_cap_prior_spend_unknown_usage_and_no_reset(self):
        protocol = self.protocol()
        with self.assertRaises(BudgetError): self.budget(protocol, prior='0.27')
        self.assertFalse(self.ledger.exists())
        budget = self.budget(protocol, prior='0.1')
        with self.assertRaises(BudgetError): self.budget(protocol)  # Concurrent process book lock.
        budget.close()
        with self.assertRaises(BudgetError): self.budget(protocol, create=False, prior='0')
        with self.assertRaises(BudgetError): self.budget(protocol, create=True, prior='0.1')
        budget = self.budget(protocol, create=False, prior='0.1')
        budget.reserve(0)
        budget.close()  # A process crash after reservation is never treated as free.
        with self.assertRaises(BudgetError): self.budget(protocol, create=False, prior='0.1')

    def test_cross_process_lock_and_thread_reservations(self):
        protocol = self.protocol()
        budget = self.budget(protocol)
        code = ('from mtg_jev.decisions_budget import ComparisonBudget, BudgetError\n'
                'import sys\ntry:\n ComparisonBudget(sys.argv[1], sys.argv[2], [1], "0")\n'
                'except BudgetError:\n sys.exit(7)\n')
        child = subprocess.run([sys.executable, '-c', code, str(self.ledger), protocol.sha256],
                               env={}, capture_output=True)
        self.assertEqual(child.returncode, 7)
        self.assertEqual(child.stdout + child.stderr, b'')
        barrier = threading.Barrier(2)
        outcomes = []
        def reserve():
            barrier.wait()
            try:
                budget.reserve(0)
                outcomes.append('reserved')
            except BudgetError:
                outcomes.append('blocked')
        threads = [threading.Thread(target=reserve) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertCountEqual(outcomes, ['reserved', 'blocked'])

    def test_budget_and_manifest_drift_symlinks_permissions_and_tampering_fail(self):
        protocol = self.protocol()
        budget = self.budget(protocol)
        budget.close()
        self.ledger.chmod(0o644)
        with self.assertRaises(BudgetError): self.budget(protocol, create=False)
        self.ledger.chmod(0o600)
        changed = json.loads(self.ledger.read_text()); changed['prior_nanos'] = -1
        self.ledger.write_text(json.dumps(changed))
        with self.assertRaises(BudgetError): self.budget(protocol, create=False)
        alias = self.folder / 'alias.json'; alias.symlink_to(self.ledger)
        with self.assertRaises(BudgetError): self.budget(protocol, path=alias, create=False)

    def test_pricing_staleness_unknown_priors_and_exact_nano_accounting(self):
        for value in ('NaN', 'Infinity', '-0.1', '0.500000001', '', None, False):
            with self.assertRaises(BudgetError): nanos(value)
        self.assertEqual(nanos('0.0000000001'), 1)
        self.assertEqual(reservation(1), 231_000_000)
        self.assertLessEqual(2 * reservation(1), CAP_NANOS)
        self.assertGreater(2 * reservation(2), CAP_NANOS)
        for tokens, rate in ((272_000, 110), (272_001, 220), (CONTEXT_TOKENS, 220)):
            self.assertEqual(usage_cost(read_usage(response(tokens=tokens)), 1), tokens * rate)
        with self.assertRaises(BudgetError): check_pricing('unconfirmed')
        with patch('mtg_jev.decisions_budget.PRICING_DATE', '2000-01-01'):
            with self.assertRaises(BudgetError): self.protocol().preflight()

    def test_cli_defaults_to_no_network_and_safe_errors(self):
        script = Path(__file__).resolve().parents[1] / 'experiments/run_decisions_verification.py'
        args = [sys.executable, str(script), 'init-budget', '--manifest', str(self.manifest),
                '--ledger', str(self.ledger), '--prior-spend-usd', '0']
        env = {'PATH': os.defpath}  # No inherited live flag or credentials.
        result = subprocess.run(args, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['api_calls_this_command'], 0)
        args[2] = 'run'
        result = subprocess.run(args, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(str(self.folder), result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertEqual(json.loads(self.ledger.read_text())['entries'][0]['state'], 'planned')


if __name__ == '__main__':
    unittest.main()
