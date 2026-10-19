import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from crawler.accounts import Account
from crawler.errors import SessionInvalidError, ThrottledError, TransportError
from crawler.settings import TransportSettings
from crawler.transport import FacebookTransport, facebook_error_code


class ScriptedServer:
    """Replies with queued (status, body) pairs and records every request."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                outer.requests.append({
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": self.rfile.read(length).decode(),
                })
                status, body = outer.replies.pop(0) if outer.replies else (200, "{}")
                payload = body.encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}/api/graphql/"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self):
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def scripted():
    servers = []

    def start(*replies):
        server = ScriptedServer(replies)
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.stop()


def make_transport(server, max_attempts=3):
    account = Account(name="clone-1", cookies={"c_user": "42", "xs": "secret"}, fb_dtsg="tok",
                      proxy=None, impersonate=None)
    settings = TransportSettings(impersonate="chrome", timeout_seconds=5.0, max_attempts=max_attempts,
                                 retry_backoff_seconds=0.0)
    return FacebookTransport(account, settings, server.url)


def test_post_sends_form_payload_and_account_cookies(scripted):
    server = scripted((200, 'for (;;);{"data":{}}'))
    transport = make_transport(server)

    text = transport.post_graphql({"doc_id": "1", "fb_dtsg": "tok"}, "SomeQuery", "https://www.facebook.com/")

    request = server.requests[0]
    assert text == 'for (;;);{"data":{}}'
    assert "doc_id=1" in request["body"] and "fb_dtsg=tok" in request["body"]
    assert "c_user=42" in request["headers"]["cookie"] and "xs=secret" in request["headers"]["cookie"]
    assert request["headers"]["x-fb-friendly-name"] == "SomeQuery"
    assert request["headers"]["content-type"] == "application/x-www-form-urlencoded"
    transport.close()


def test_rate_limit_error_code_raises_throttled(scripted):
    server = scripted((200, 'for (;;);{"error":1675030,"errorSummary":"Error performing query."}'))
    transport = make_transport(server)

    with pytest.raises(ThrottledError):
        transport.post_graphql({}, "Q", "r")
    transport.close()


def test_http_429_raises_throttled_without_retrying(scripted):
    server = scripted((429, "slow down"), (200, "{}"))
    transport = make_transport(server)

    with pytest.raises(ThrottledError):
        transport.post_graphql({}, "Q", "r")

    assert len(server.requests) == 1
    transport.close()


def test_auth_error_code_raises_session_invalid(scripted):
    server = scripted((200, 'for (;;);{"error":1357001}'))
    transport = make_transport(server)

    with pytest.raises(SessionInvalidError):
        transport.post_graphql({}, "Q", "r")
    transport.close()


def test_server_errors_are_retried_then_succeed(scripted):
    server = scripted((500, "oops"), (502, "oops"), (200, '{"data":{}}'))
    transport = make_transport(server, max_attempts=3)

    assert transport.post_graphql({}, "Q", "r") == '{"data":{}}'
    assert len(server.requests) == 3
    transport.close()


def test_persistent_server_errors_raise_transport_error(scripted):
    server = scripted((500, "x"), (500, "x"))
    transport = make_transport(server, max_attempts=2)

    with pytest.raises(TransportError, match="HTTP 500"):
        transport.post_graphql({}, "Q", "r")
    assert len(server.requests) == 2
    transport.close()


def test_client_errors_are_not_retried(scripted):
    server = scripted((404, "missing"), (200, "{}"))
    transport = make_transport(server, max_attempts=3)

    with pytest.raises(TransportError, match="HTTP 404"):
        transport.post_graphql({}, "Q", "r")
    assert len(server.requests) == 1
    transport.close()


def test_connection_failure_raises_transport_error():
    class Dead:
        url = "http://127.0.0.1:9/api/graphql/"

    transport = make_transport(Dead(), max_attempts=2)

    with pytest.raises(TransportError):
        transport.post_graphql({}, "Q", "r")
    transport.close()


@pytest.mark.parametrize("body, expected", [
    ('for (;;);{"error":1675030}', 1675030),
    ('{"errors":[{"code":1357004,"message":"x"}]}', 1357004),
    ('for (;;);{"data":{"node":{}}}', None),
    ("not json", None),
    ("[1,2]", None),
])
def test_error_code_detection(body, expected):
    assert facebook_error_code(body) == expected
