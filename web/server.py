"""HTTP server: a small read-only JSON API plus the static single-page app."""
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from . import data

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'"),
}
POST_PATH_PREFIX = "/api/posts/"


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def _int_param(params, name, default):
    raw = params.get(name, "")
    if raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None


def _post_query(params):
    return data.PostQuery(
        text=params.get("q", ""), status=params.get("status", ""),
        min_comments=_int_param(params, "min_comments", 0), sort=params.get("sort", "newest"),
        offset=_int_param(params, "offset", 0), limit=_int_param(params, "limit", data.DEFAULT_PAGE_SIZE))


def _comment_query(params):
    return data.CommentQuery(
        text=params.get("q", ""), author=params.get("author", ""), level=params.get("level", "all"),
        near_duplicates_only=params.get("duplicates") == "1",
        offset=_int_param(params, "offset", 0), limit=_int_param(params, "limit", data.DEFAULT_PAGE_SIZE))


class Handler(BaseHTTPRequestHandler):
    server_version = "PhayBucWeb"

    def do_GET(self):
        parts = urlsplit(self.path)
        params = {key: values[-1] for key, values in parse_qs(parts.query).items()}
        try:
            if parts.path in STATIC_FILES:
                self._send_static(*STATIC_FILES[parts.path])
            elif parts.path.startswith("/api/"):
                self._send_json(200, self._api(parts.path, params))
            else:
                raise ApiError(404, "That page was not found")
        except ApiError as error:
            self._send_json(error.status, {"error": error.message})
        except data.DatabaseUnavailableError:
            self._send_json(503, {"error": "There is no data yet. Run `python -m crawler crawl` first."})
        except ValueError as error:
            self._send_json(400, {"error": str(error)})
        except Exception:
            logger.exception("request failed: %s", self.path)
            self._send_json(500, {"error": "The server hit an unexpected problem"})

    def _api(self, path, params):
        with self.server.database.connect() as connection:
            if path == "/api/stats":
                return data.stats(connection)
            if path == "/api/posts":
                return data.list_posts(connection, _post_query(params))
            if path.startswith(POST_PATH_PREFIX):
                post = data.get_post(connection, unquote(path[len(POST_PATH_PREFIX):]))
                if post is None:
                    raise ApiError(404, "That post was not found")
                return post
            if path == "/api/comments":
                return data.search_comments(connection, _comment_query(params))
        raise ApiError(404, "There is no such API endpoint")

    def _send_static(self, filename, content_type):
        body = (STATIC_DIR / filename).read_bytes()
        self._send(200, body, content_type)

    def _send_json(self, status, payload):
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _send(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        logger.debug(format, *args)


def make_server(database_path, host, port):
    server = ThreadingHTTPServer((host, port), Handler)
    server.database = data.Database(database_path)
    return server
