"""One logical GraphQL request, retried across pooled sessions."""
from dataclasses import dataclass
from typing import Callable

from .errors import (CrawlerError, SessionInvalidError, ThrottledError, TransportError)
from .facebook.parsers import MalformedResponseError


class RequestFailedError(CrawlerError):
    pass


@dataclass(frozen=True)
class LogicalRequest:
    build_payload: Callable   # (account) -> dict
    friendly_name: str
    referer: str
    parse: Callable           # (response_text) -> parsed value


class Requester:
    """Leases a session per attempt; throttled or dead sessions are replaced transparently.

    The pool already moved a throttled session to COOLDOWN (and a rejected one to DEAD)
    by the time the exception reaches us, so retrying simply draws another session.
    """

    def __init__(self, pool, pool_settings, max_attempts):
        self._pool = pool
        self._acquire_timeout = pool_settings.acquire_timeout_seconds
        self._max_attempts = max_attempts

    def fetch(self, request):
        last_problem = None
        for _ in range(self._max_attempts):
            try:
                with self._pool.lease(self._acquire_timeout) as session:
                    payload = request.build_payload(session.account)
                    text = session.transport.post_graphql(payload, request.friendly_name, request.referer)
                return request.parse(text)
            except (ThrottledError, SessionInvalidError, TransportError, MalformedResponseError) as error:
                last_problem = error
        raise RequestFailedError(
            f"{request.friendly_name} failed after {self._max_attempts} attempts: {last_problem}")
