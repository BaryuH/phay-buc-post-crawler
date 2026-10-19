import json
import threading

import pytest

from crawler.accounts import (Account, AccountsError, PooledSession, SessionPool,
                              SessionState, load_accounts, parse_cookie_header)
from crawler.errors import (AcquireTimeoutError, PoolExhaustedError, SessionInvalidError,
                            ThrottledError)
from crawler.settings import PoolSettings


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class RecordingLimiter:
    def __init__(self):
        self.waits = self.successes = self.throttles = 0
        self.rtts = []

    def wait(self):
        self.waits += 1

    def on_success(self, rtt_seconds):
        self.successes += 1
        self.rtts.append(rtt_seconds)

    def on_throttle(self):
        self.throttles += 1


class NoTransport:
    def close(self):
        pass


def make_session(name):
    account = Account(name=name, cookies={"c_user": "1"}, fb_dtsg="t", proxy=None, impersonate=None)
    return PooledSession(account=account, limiter=RecordingLimiter(), transport=NoTransport())


def make_pool(count=2, cooldown=100.0, quota=1_000_000, quota_cooldown=100.0):
    clock = FakeClock()
    sessions = [make_session(f"clone-{n}") for n in range(count)]
    pool_settings = PoolSettings(cooldown_seconds=cooldown, quota_per_account=quota,
                                 quota_cooldown_seconds=quota_cooldown, acquire_timeout_seconds=1.0)
    return SessionPool(sessions, pool_settings, clock=clock), sessions, clock


def test_cookie_header_is_parsed_into_dict():
    cookies = parse_cookie_header("c_user=123; xs=a=b; datr=zz")

    assert cookies == {"c_user": "123", "xs": "a=b", "datr": "zz"}


def test_load_accounts_reads_valid_file(tmp_path):
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps([
        {"name": "a", "cookies": "c_user=1; xs=2", "fb_dtsg": "tok", "proxy": "http://p:1"},
        {"name": "b", "cookies": "c_user=3", "fb_dtsg": "tok2"},
    ]), encoding="utf-8")

    accounts = load_accounts(path)

    assert [a.user_id for a in accounts] == ["1", "3"]
    assert accounts[0].proxy == "http://p:1" and accounts[1].proxy is None


@pytest.mark.parametrize("entries, message", [
    ([], "non-empty"),
    ([{"name": "a", "cookies": "xs=2", "fb_dtsg": "t"}], "c_user"),
    ([{"name": "a", "cookies": "c_user=1"}], "fb_dtsg"),
    ([{"name": "a", "cookies": "c_user=1", "fb_dtsg": "t"},
      {"name": "a", "cookies": "c_user=2", "fb_dtsg": "t"}], "unique"),
])
def test_load_accounts_rejects_invalid_files(tmp_path, entries, message):
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps(entries), encoding="utf-8")

    with pytest.raises(AccountsError, match=message):
        load_accounts(path)


def test_successful_lease_paces_then_returns_session_to_ready():
    pool, sessions, _ = make_pool(count=1)

    with pool.lease(1.0) as session:
        assert session.state is SessionState.ACTIVE

    assert sessions[0].state is SessionState.READY
    assert (sessions[0].limiter.waits, sessions[0].limiter.successes) == (1, 1)


def test_throttle_puts_session_in_cooldown_and_backs_off_limiter():
    pool, sessions, _ = make_pool(count=1)

    with pytest.raises(ThrottledError):
        with pool.lease(1.0):
            raise ThrottledError("1675030")

    assert sessions[0].state is SessionState.COOLDOWN
    assert sessions[0].limiter.throttles == 1


def test_cooldown_expires_after_configured_time():
    pool, sessions, clock = make_pool(count=1, cooldown=100.0)
    with pytest.raises(ThrottledError):
        with pool.lease(1.0):
            raise ThrottledError("limit")

    clock.now += 100.0

    with pool.lease(1.0) as session:
        assert session is sessions[0]


def test_invalid_session_is_dead_and_never_reacquired():
    pool, sessions, _ = make_pool(count=2)

    with pytest.raises(SessionInvalidError):
        with pool.lease(1.0):
            raise SessionInvalidError("checkpoint")

    assert sorted(s.state.value for s in sessions) == ["DEAD", "READY"]
    for _ in range(3):
        with pool.lease(1.0) as session:
            assert session.state is SessionState.ACTIVE
            assert session is not next(s for s in sessions if s.state is SessionState.DEAD)


def test_pool_raises_when_every_account_is_dead():
    pool, _, _ = make_pool(count=1)
    with pytest.raises(SessionInvalidError):
        with pool.lease(1.0):
            raise SessionInvalidError("checkpoint")

    with pytest.raises(PoolExhaustedError):
        pool.acquire(1.0)


def test_acquire_times_out_when_all_sessions_are_cooling_down():
    pool, _, clock = make_pool(count=1, cooldown=1000.0)
    with pytest.raises(ThrottledError):
        with pool.lease(1.0):
            raise ThrottledError("limit")

    with pytest.raises(AcquireTimeoutError):
        pool.acquire(0.0)


def test_least_recently_used_session_is_chosen_first():
    pool, sessions, clock = make_pool(count=2)

    with pool.lease(1.0) as first:
        pass
    clock.now += 1
    with pool.lease(1.0) as second:
        pass

    assert first is not second


def test_unexpected_error_releases_session_without_penalty():
    pool, sessions, _ = make_pool(count=1)

    with pytest.raises(RuntimeError):
        with pool.lease(1.0):
            raise RuntimeError("bug")

    assert sessions[0].state is SessionState.READY
    assert sessions[0].limiter.throttles == 0 and sessions[0].limiter.successes == 0


def test_concurrent_leases_never_share_a_session():
    pool, sessions, _ = make_pool(count=3)
    active = set()
    overlaps = []
    guard = threading.Lock()

    def work():
        for _ in range(50):
            with pool.lease(5.0) as session:
                with guard:
                    if session.name in active:
                        overlaps.append(session.name)
                    active.add(session.name)
                with guard:
                    active.discard(session.name)

    threads = [threading.Thread(target=work) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert overlaps == []


def test_account_rests_after_its_request_quota_and_work_rotates_to_the_other():
    pool, sessions, clock = make_pool(count=2, quota=2, quota_cooldown=500.0)

    used = []
    for _ in range(4):
        with pool.lease(1.0) as session:
            used.append(session.name)
        clock.now += 1

    assert used == ["clone-0", "clone-1", "clone-0", "clone-1"]
    assert sorted(s.state.value for s in sessions) == ["COOLDOWN", "COOLDOWN"]


def test_quota_rest_ends_after_the_quota_cooldown():
    pool, sessions, clock = make_pool(count=1, quota=1, quota_cooldown=500.0)
    with pool.lease(1.0):
        pass
    assert sessions[0].state is SessionState.COOLDOWN

    clock.now += 499
    with pytest.raises(AcquireTimeoutError):
        pool.acquire(0.0)
    clock.now += 2

    with pool.lease(1.0) as session:
        assert session is sessions[0]


def test_lease_reports_round_trip_time_to_the_limiter():
    pool, sessions, clock = make_pool(count=1)

    with pool.lease(1.0):
        clock.now += 6.5

    assert sessions[0].limiter.rtts == [6.5]


def test_lsd_is_loaded_when_present(tmp_path):
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps([
        {"name": "a", "cookies": "c_user=1", "fb_dtsg": "t", "lsd": "AVq"},
        {"name": "b", "cookies": "c_user=2", "fb_dtsg": "t"},
    ]), encoding="utf-8")

    accounts = load_accounts(path)

    assert (accounts[0].lsd, accounts[1].lsd) == ("AVq", None)
