"""Account configuration, per-account session state machine, and the shared pool."""
import json
import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from .errors import (AcquireTimeoutError, PoolExhaustedError, SessionInvalidError,
                     ThrottledError)

logger = logging.getLogger(__name__)


class AccountsError(ValueError):
    pass


@dataclass(frozen=True)
class Account:
    name: str
    cookies: dict
    fb_dtsg: str
    proxy: Optional[str]
    impersonate: Optional[str]
    lsd: Optional[str] = None

    @property
    def user_id(self):
        return self.cookies["c_user"]


def parse_cookie_header(cookie_header):
    cookies = {}
    for part in cookie_header.split(";"):
        key, separator, value = part.strip().partition("=")
        if separator:
            cookies[key.strip()] = value.strip()
    return cookies


def load_accounts(path):
    with open(Path(path), encoding="utf-8") as handle:
        entries = json.load(handle)
    if not isinstance(entries, list) or not entries:
        raise AccountsError("accounts file must be a non-empty JSON list")

    accounts = []
    for position, entry in enumerate(entries):
        label = f"accounts[{position}]"
        for required in ("name", "cookies", "fb_dtsg"):
            if not entry.get(required):
                raise AccountsError(f"{label}.{required} is required")
        cookies = parse_cookie_header(entry["cookies"])
        if "c_user" not in cookies:
            raise AccountsError(f"{label}.cookies must contain c_user")
        accounts.append(Account(name=entry["name"], cookies=cookies, fb_dtsg=entry["fb_dtsg"],
                                proxy=entry.get("proxy"), impersonate=entry.get("impersonate"),
                                lsd=entry.get("lsd")))

    names = [account.name for account in accounts]
    if len(set(names)) != len(names):
        raise AccountsError("account names must be unique")
    return accounts


class SessionState(Enum):
    READY = "READY"
    ACTIVE = "ACTIVE"
    COOLDOWN = "COOLDOWN"
    DEAD = "DEAD"


@dataclass
class PooledSession:
    """One account bound to its own transport and pacing state."""
    account: Account
    limiter: Any
    transport: Any
    state: SessionState = SessionState.READY
    cooldown_until: float = 0.0
    last_released_at: float = 0.0
    requests_in_shift: int = 0

    @property
    def name(self):
        return self.account.name


class SessionPool:
    """Thread-safe pool: READY -> ACTIVE -> READY | COOLDOWN -> READY | DEAD.

    An account also rests after `quota_per_account` requests so one account never
    carries the whole crawl (quota-based rotation).
    """

    def __init__(self, sessions, pool_settings, clock=time.monotonic):
        self._sessions = list(sessions)
        self._settings = pool_settings
        self._clock = clock
        self._condition = threading.Condition()

    def _promote_expired_cooldowns(self, now):
        for session in self._sessions:
            if session.state is SessionState.COOLDOWN and session.cooldown_until <= now:
                session.state = SessionState.READY
                logger.info("account %s is READY again", session.name)

    def _next_ready(self):
        ready = [s for s in self._sessions if s.state is SessionState.READY]
        # Least recently used first spreads requests evenly over accounts.
        return min(ready, key=lambda s: s.last_released_at) if ready else None

    def _seconds_until_next_cooldown_ends(self, now):
        ends = [s.cooldown_until - now for s in self._sessions if s.state is SessionState.COOLDOWN]
        return min(ends) if ends else None

    def acquire(self, timeout_seconds):
        deadline = self._clock() + timeout_seconds
        with self._condition:
            while True:
                now = self._clock()
                self._promote_expired_cooldowns(now)
                session = self._next_ready()
                if session is not None:
                    session.state = SessionState.ACTIVE
                    return session
                if all(s.state is SessionState.DEAD for s in self._sessions):
                    raise PoolExhaustedError("all accounts are dead; refresh cookies/fb_dtsg")
                remaining = deadline - now
                if remaining <= 0:
                    raise AcquireTimeoutError("no session became available in time")
                next_cooldown = self._seconds_until_next_cooldown_ends(now)
                wait_for = remaining if next_cooldown is None else min(remaining, max(next_cooldown, 0.01))
                self._condition.wait(wait_for)

    def _release(self, session, outcome):
        with self._condition:
            now = self._clock()
            session.last_released_at = now
            session.requests_in_shift += 1
            session.state = self._state_after(session, outcome)
            if session.state is SessionState.COOLDOWN:
                rest = (self._settings.cooldown_seconds if outcome is SessionState.COOLDOWN
                        else self._settings.quota_cooldown_seconds)
                session.cooldown_until = now + rest
                session.requests_in_shift = 0
                logger.warning("account %s resting for %.0fs", session.name, rest)
            elif session.state is SessionState.DEAD:
                logger.error("account %s marked DEAD (session rejected); refresh its cookies", session.name)
            self._condition.notify_all()

    def _state_after(self, session, outcome):
        if outcome is SessionState.READY and session.requests_in_shift >= self._settings.quota_per_account:
            return SessionState.COOLDOWN
        return outcome

    @contextmanager
    def lease(self, acquire_timeout_seconds):
        """Hold one paced session for a single request and settle its state afterwards."""
        session = self.acquire(acquire_timeout_seconds)
        try:
            session.limiter.wait()
            started = self._clock()
            yield session
        except ThrottledError:
            session.limiter.on_throttle()
            self._release(session, SessionState.COOLDOWN)
            raise
        except SessionInvalidError:
            self._release(session, SessionState.DEAD)
            raise
        except BaseException:
            self._release(session, SessionState.READY)
            raise
        else:
            session.limiter.on_success(self._clock() - started)
            self._release(session, SessionState.READY)

    def states(self):
        with self._condition:
            return {s.name: s.state for s in self._sessions}

    def close(self):
        for session in self._sessions:
            session.transport.close()
