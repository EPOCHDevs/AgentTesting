"""The relay needs upstream HTTP health, not the API image's Redis configuration."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import subprocess
import sys
from threading import Thread

from tools.local_stack import relay_health_probe, up_compose_override


def test_relay_probe_uses_forwarded_health_and_refuses_upstream_failure():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        status = 200

        def do_GET(self):
            seen.append(self.path)
            self.send_response(self.status)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        command = [sys.executable, "-c", relay_health_probe(server.server_port)]
        assert subprocess.run(command, capture_output=True).returncode == 0
        Handler.status = 503
        assert subprocess.run(command, capture_output=True).returncode == 1
        assert seen == ["/health", "/health"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_only_relay_gets_the_component_specific_probe():
    composed = up_compose_override([], [], "epoch-ai-up:test", 9000)
    api, relay = composed.split("  backend-loopback:", 1)
    assert "healthcheck" not in api
    assert "HTTPConnection" in relay and "/health" in relay
    assert "/api/healthcheck.py" not in relay
    assert "REDIS_URI" not in relay
    assert "healthcheck" not in up_compose_override([], [], "test", None)
