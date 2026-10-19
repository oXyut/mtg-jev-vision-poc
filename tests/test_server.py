import http.client
import json
import threading
import unittest
from unittest.mock import patch

from mtg_jev.fixtures import fixture
from mtg_jev.geometry import COORDINATES
from mtg_jev.images import png_bytes
from mtg_jev.server import make_server


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server(0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        status = response.status
        connection.close()
        return status, data

    def test_prepare_mock_and_manual_evaluation_no_outbound_network(self):
        with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('External API forbidden')):
            image, boxes = fixture('overlap')
            data = png_bytes(image)
            status, raw = self.request('POST', '/api/prepare', data)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(raw)['image'], {'width': 800, 'height': 600})
            status, raw = self.request('POST', '/api/detect?scenario=overlap', data)
            self.assertEqual(status, 200)
            result = json.loads(raw)['result']
            truth = {'coordinate_system': COORDINATES, 'image': result['image'],
                     'boxes': [b.json() for b in boxes]}
            status, metrics = self.request('POST', '/api/evaluate',
                                           json.dumps({'prediction': result, 'truth': truth}))
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(metrics)['tp'], 2)
            self.assertEqual(result['api_calls'], 0)

    def test_local_origin_and_host_required(self):
        for headers in ({'Origin': 'https://attacker.example'}, {'Host': 'attacker.example'}):
            status, _ = self.request('GET', '/', headers=headers)
            self.assertEqual(status, 403)

    def test_bad_image_and_oversized_body(self):
        status, _ = self.request('POST', '/api/prepare', b'private-invalid-image')
        self.assertEqual(status, 400)
        status, _ = self.request('POST', '/api/prepare', b'', {'Content-Length': '20000000'})
        self.assertEqual(status, 413)

    def test_ui_and_unknown_paths(self):
        status, page = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn('MOCK'.encode(), page)
        for path in ('/.env', '/api/live', '/../../etc/passwd'):
            self.assertEqual(self.request('GET', path)[0], 404)


if __name__ == '__main__':
    unittest.main()
