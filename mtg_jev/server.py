"""Local mock-only UI. Uploads stay in request memory; request logging disabled."""

import base64
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.resources import files
from urllib.parse import parse_qs, urlsplit

from .detector import detect
from .fixtures import SCENARIOS, fixture
from .geometry import evaluate_documents
from .images import MAX_FILE, overlay, png_bytes, prepare
from .provider import MockClient, mock_script


class Handler(BaseHTTPRequestHandler):
    server_version = "MTGPoC"
    sys_version = ""

    def log_message(self, format, *args):
        pass  # Never log URLs, uploads, request bodies or user filenames.

    def respond(self, status, payload, content_type="application/json"):
        if isinstance(payload, dict):
            payload = json.dumps(payload, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data: blob:; "
                         "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                         "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(payload)

    def local_request(self):
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        return host in hosts and (origin is None or origin in {f"http://{h}" for h in hosts})

    def do_GET(self):
        if not self.local_request():
            self.respond(403, {"error": "Loopback origin required"})
            return
        url = urlsplit(self.path)
        if url.path == "/":
            self.respond(200, files("mtg_jev").joinpath("web.html").read_bytes(), "text/html; charset=utf-8")
        elif url.path.startswith("/api/demo/"):
            scenario = url.path.removeprefix("/api/demo/")
            if scenario not in SCENARIOS:
                self.respond(404, {"error": "Unknown scenario"})
                return
            image, _ = fixture(scenario)
            self.respond(200, png_bytes(image), "image/png")
        else:
            self.respond(404, {"error": "Not found"})

    def do_POST(self):
        if not self.local_request():
            self.respond(403, {"error": "Loopback origin required"})
            return
        url = urlsplit(self.path)
        if url.path not in ("/api/prepare", "/api/detect", "/api/evaluate"):
            self.respond(404, {"error": "Not found"})
            return
        try:
            if self.headers.get("Transfer-Encoding"):
                raise ValueError("Transfer encoding unsupported")
            length = int(self.headers.get("Content-Length", "0"))
            limit = 2 * 1024 * 1024 if url.path == "/api/evaluate" else MAX_FILE
            if not 0 < length <= limit:
                self.respond(413, {"error": "Request body empty or too large"})
                return
            self.connection.settimeout(10)
            data = self.rfile.read(length)
            if len(data) != length:
                raise ValueError("Incomplete body")
            if url.path == "/api/evaluate":
                value = json.loads(data)
                self.respond(200, evaluate_documents(value["prediction"], value["truth"],
                                                     value.get("iou", 0.5)))
                return
            prepared = prepare(data)
            if url.path == "/api/prepare":
                preview = prepared.original.copy()
                preview.thumbnail((1568, 1568))
                self.respond(200, {"preview_png": base64.b64encode(png_bytes(preview)).decode(),
                                   "image": {"width": prepared.original.width,
                                             "height": prepared.original.height}})
                return
            query = parse_qs(url.query)
            scenario = query.get("scenario", ["multiple"])[0]
            if scenario not in SCENARIOS:
                raise ValueError("Unknown scenario")
            _, known_geometry = fixture(scenario)
            # These predetermined positions do not depend on the uploaded pixels.
            client = MockClient(mock_script(prepared, known_geometry))
            result = detect(prepared, client)
            self.respond(200, {"result": result,
                               "overlay_png": base64.b64encode(png_bytes(overlay(prepared, result))).decode()})
        except (ValueError, OSError, TypeError, KeyError, AttributeError):
            self.respond(400, {"error": "Invalid image, annotations or request; details omitted"})


def make_server(port=8765):
    return HTTPServer(("127.0.0.1", port), Handler)


def serve(port=8765):
    with make_server(port) as server:
        print(f"Mock-only UI: http://127.0.0.1:{server.server_port} (no API calls)")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
