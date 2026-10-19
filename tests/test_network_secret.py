"""Real urllib proxy/redirect handlers, fake HTTPS transport, no socket traffic.

The artificial marker is not a real secret or a claimed cloud placeholder format.
"""

import contextlib
import io
import json
import os
import unittest
import urllib.error
import urllib.request
from email.message import Message
from unittest.mock import patch
from urllib.parse import urlsplit
from urllib.response import addinfourl

from PIL import Image

from mtg_jev.detector import Settings, detect
from mtg_jev.images import png_bytes, prepare
from mtg_jev.provider import ENDPOINT, JevClient, LiveApproval

ARTIFICIAL_PLACEHOLDER = '__test_network_placeholder_not_a_credential__'
FAKE_PROXY = 'http://proxy.test.invalid:3128'


class FakeHTTPS(urllib.request.HTTPSHandler):
    def __init__(self, code=200, location=None, fail=None, body=None):
        super().__init__()
        self.requests = []
        self.code, self.location, self.fail = code, location, fail
        self.body = body if body is not None else json.dumps({
            'model': 'jev-1.14.0', 'usage': {'input_tokens': 30, 'output_tokens': 10},
            'answers': {'remaining': {'type': 'noul', 'noul': .01}},
        }).encode()

    def https_open(self, request):
        self.requests.append(request)
        if self.fail:
            raise self.fail
        headers = Message()
        if self.location:
            headers['Location'] = self.location
        response = addinfourl(io.BytesIO(self.body), headers, request.full_url, self.code)
        response.msg = 'Artificial response'
        return response


class NetworkSecretCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.prepared = prepare(png_bytes(Image.new('RGB', (280, 280), 'white')))

    @contextlib.contextmanager
    def transport(self, fake, **env):
        # Clear inherited proxy/credential settings. Never inspect real ones.
        values = {'MTG_JEV_ENABLE_LIVE': '1', 'TYPESAFE_API_KEY': ARTIFICIAL_PLACEHOLDER, **env}
        build_opener = urllib.request.build_opener
        with patch.dict(os.environ, values, clear=True), \
             patch('socket.create_connection', side_effect=AssertionError('Socket traffic forbidden')), \
             patch('socket.socket.connect', side_effect=AssertionError('Socket traffic forbidden')), \
             patch('socket.getaddrinfo', side_effect=AssertionError('DNS forbidden')), \
             patch('urllib.request.build_opener', side_effect=lambda *handlers: build_opener(*handlers, fake)):
            yield

    def test_placeholder_bearer_fixed_https_443_and_environment_proxy(self):
        for proxy_key in ('HTTPS_PROXY', 'https_proxy'):
            with self.subTest(proxy_key=proxy_key):
                fake = FakeHTTPS()
                with self.transport(fake, **{proxy_key: FAKE_PROXY}):
                    client = JevClient(LiveApproval(True, 1, 1))
                    self.assertTrue(any(isinstance(h, urllib.request.ProxyHandler) for h in client.opener.handlers))
                    result = detect(self.prepared, client, Settings(max_requests=1))
                self.assertEqual(result['status'], 'complete')
                self.assertEqual(len(fake.requests), 1)
                request = fake.requests[0]
                destination = urlsplit(request.full_url)
                self.assertEqual(request.full_url, ENDPOINT)
                self.assertEqual((destination.scheme, destination.hostname, destination.port or 443),
                                 ('https', 'api.typesafe.ai', 443))
                self.assertEqual(request.host, 'proxy.test.invalid:3128')
                self.assertEqual(request._tunnel_host, 'api.typesafe.ai')
                self.assertEqual(request.get_header('Authorization'), 'Bearer ' + ARTIFICIAL_PLACEHOLDER)
                self.assertNotIn(ARTIFICIAL_PLACEHOLDER.encode(), request.data)
                self.assertNotIn(ARTIFICIAL_PLACEHOLDER, json.dumps(result))

    def test_redirect_statuses_never_send_credentials_to_second_destination(self):
        for code in (301, 302, 303, 307, 308):
            for location in ('https://other.test.invalid/collect',
                             'http://api.typesafe.ai/collect',
                             'https://api.typesafe.ai:8443/collect', '/other-path'):
                with self.subTest(code=code, destination_kind=location.split(':')[0]):
                    fake = FakeHTTPS(code, location)
                    with self.transport(fake, HTTPS_PROXY=FAKE_PROXY):
                        result = detect(self.prepared, JevClient(LiveApproval(True, 2, 1)))
                    self.assertEqual(result['status'], 'error')
                    self.assertEqual(result['stop_reason'], 'Jev redirect refused')
                    self.assertEqual(len(fake.requests), 1)
                    self.assertEqual(fake.requests[0].full_url, ENDPOINT)

    def test_proxy_failure_has_no_direct_or_unauthenticated_retry(self):
        fake = FakeHTTPS(fail=urllib.error.URLError('Artificial private proxy detail'))
        with self.transport(fake, HTTPS_PROXY=FAKE_PROXY):
            result = detect(self.prepared, JevClient(LiveApproval(True, 3, 1)))
        self.assertEqual(result['status'], 'error')
        self.assertEqual(len(fake.requests), 1)
        self.assertEqual(fake.requests[0].host, 'proxy.test.invalid:3128')
        self.assertNotIn('Artificial private proxy detail', json.dumps(result))

    def test_missing_variable_or_approval_stops_before_opener(self):
        for values in ({'MTG_JEV_ENABLE_LIVE': '1'},
                       {'TYPESAFE_API_KEY': ARTIFICIAL_PLACEHOLDER}):
            with self.subTest(keys=sorted(values)), patch.dict(os.environ, values, clear=True), \
                 patch('urllib.request.build_opener', side_effect=AssertionError('Opener must not be created')):
                with self.assertRaises(ValueError):
                    JevClient(LiveApproval(True, 1, 1))

    def test_error_and_invalid_response_do_not_log_or_save_authentication(self):
        for fake in (FakeHTTPS(401, body=ARTIFICIAL_PLACEHOLDER.encode()),
                     FakeHTTPS(200, body=ARTIFICIAL_PLACEHOLDER.encode())):
            stdout, stderr = io.StringIO(), io.StringIO()
            with self.transport(fake, HTTPS_PROXY=FAKE_PROXY), \
                 contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = detect(self.prepared, JevClient(LiveApproval(True, 1, 1)))
            self.assertEqual(result['status'], 'error')
            self.assertEqual(stdout.getvalue(), '')
            self.assertEqual(stderr.getvalue(), '')
            saved = json.dumps(result)
            self.assertNotIn(ARTIFICIAL_PLACEHOLDER, saved)
            self.assertNotIn('Authorization', saved)
            self.assertNotIn('Bearer', saved)
            self.assertEqual(len(fake.requests), 1)


if __name__ == '__main__':
    unittest.main()
