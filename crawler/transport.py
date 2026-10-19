"""HTTP/2 transport (curl_cffi) bound to one account, with Facebook error classification."""
import json
import time
from urllib.parse import urlparse

from curl_cffi import CurlHttpVersion
from curl_cffi import requests as curl_requests

from .errors import SessionInvalidError, ThrottledError, TransportError

FACEBOOK_RATE_LIMIT_CODES = frozenset({1675030})
# [INFERENCE] Not confirmed against live Facebook; unknown codes fall through to normal retry.
FACEBOOK_AUTH_ERROR_CODES = frozenset({1357001, 1357004, 1357031})

HTTP_OK = 200
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_TOO_MANY_REQUESTS = 429
HTTP_SERVER_ERROR_FLOOR = 500

JS_HIJACK_PREFIX = "for (;;);"


def facebook_error_code(response_text):
    """Return the numeric error code from a Facebook error body, or None for normal data."""
    first_line = response_text.strip().removeprefix(JS_HIJACK_PREFIX).split("\n", 1)[0].strip()
    if not first_line.startswith("{"):
        return None
    try:
        document = json.loads(first_line)
    except ValueError:
        return None
    if not isinstance(document, dict):
        return None
    top_level = document.get("error")
    if isinstance(top_level, int):
        return top_level
    for entry in document.get("errors") or []:
        if isinstance(entry, dict) and isinstance(entry.get("code"), int):
            return entry["code"]
    return None


def _classify_failure(status_code, response_text):
    if status_code in (HTTP_TOO_MANY_REQUESTS, HTTP_FORBIDDEN):
        return ThrottledError(f"HTTP {status_code}")
    if status_code == HTTP_UNAUTHORIZED:
        return SessionInvalidError("HTTP 401")
    if status_code != HTTP_OK:
        return None
    code = facebook_error_code(response_text)
    if code in FACEBOOK_RATE_LIMIT_CODES:
        return ThrottledError(f"Facebook error {code}")
    if code in FACEBOOK_AUTH_ERROR_CODES:
        return SessionInvalidError(f"Facebook error {code}")
    return None


class FacebookTransport:
    def __init__(self, account, transport_settings, graphql_url):
        self._settings = transport_settings
        self._graphql_url = graphql_url
        self._session = curl_requests.Session(
            impersonate=account.impersonate or transport_settings.impersonate,
            # h2 via ALPN on HTTPS; falls back to HTTP/1.1 for plain-HTTP test servers.
            http_version=CurlHttpVersion.V2TLS,
            proxies={"http": account.proxy, "https": account.proxy} if account.proxy else None,
            timeout=transport_settings.timeout_seconds,
        )
        host = urlparse(graphql_url).hostname
        for name, value in account.cookies.items():
            self._session.cookies.set(name, value, domain=host)

    def post_graphql(self, payload, friendly_name, referer):
        headers = {
            "content-type": "application/x-www-form-urlencoded",
            "origin": "https://www.facebook.com",
            "referer": referer,
            "x-fb-friendly-name": friendly_name,
            "sec-fetch-site": "same-origin",
            "sec-fetch-mode": "cors",
            "sec-fetch-dest": "empty",
        }
        return self._request_with_retry("POST", self._graphql_url, data=payload, headers=headers)

    def get_html(self, url):
        return self._request_with_retry("GET", url)

    def _request_with_retry(self, method, url, **kwargs):
        last_problem = "no attempt made"
        for attempt in range(1, self._settings.max_attempts + 1):
            try:
                response = self._session.request(method, url, **kwargs)
            except curl_requests.exceptions.RequestException as error:
                last_problem = f"{type(error).__name__}: {error}"
            else:
                failure = _classify_failure(response.status_code, response.text)
                if failure is not None:
                    raise failure
                if response.status_code == HTTP_OK:
                    return response.text
                last_problem = f"HTTP {response.status_code}"
                if response.status_code < HTTP_SERVER_ERROR_FLOOR:
                    break
            if attempt < self._settings.max_attempts:
                time.sleep(attempt * self._settings.retry_backoff_seconds)
        raise TransportError(f"{method} {url} failed: {last_problem}")

    def close(self):
        self._session.close()
