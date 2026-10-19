import random
import statistics

from crawler.ratelimit import AimdLimiter
from crawler.settings import PacingSettings


def _pacing(**overrides):
    values = dict(
        base_delay_seconds=2.0,
        min_delay_seconds=0.5,
        max_delay_seconds=30.0,
        min_sleep_seconds=0.0,
        jitter_sigma=0.35,
        additive_decrease_seconds=0.25,
        throttle_multiplier=2.0,
        fast_rtt_seconds=2.0,
        slow_rtt_seconds=5.0,
        macro_break_every_min_requests=5,
        macro_break_every_max_requests=5,
        macro_break_min_seconds=100.0,
        macro_break_max_seconds=100.0,
    )
    values.update(overrides)
    return PacingSettings(**values)


NO_MACRO_BREAK = dict(macro_break_every_min_requests=100_000, macro_break_every_max_requests=100_000)


def _limiter(pacing=None, seed=1):
    slept = []
    limiter = AimdLimiter(pacing or _pacing(), rng=random.Random(seed), sleep=slept.append)
    return limiter, slept


def test_throttle_doubles_delay_up_to_the_cap():
    limiter, _ = _limiter(_pacing(base_delay_seconds=10.0))

    limiter.on_throttle()
    assert limiter.current_delay == 20.0

    limiter.on_throttle()
    assert limiter.current_delay == 30.0


def test_fast_reply_decreases_delay_down_to_the_floor():
    limiter, _ = _limiter(_pacing(base_delay_seconds=1.0))

    limiter.on_success(rtt_seconds=0.3)
    assert limiter.current_delay == 0.75

    for _ in range(10):
        limiter.on_success(rtt_seconds=0.3)
    assert limiter.current_delay == 0.5


def test_slow_reply_raises_delay_like_a_throttle():
    limiter, _ = _limiter(_pacing(base_delay_seconds=4.0))

    limiter.on_success(rtt_seconds=6.0)

    assert limiter.current_delay == 8.0


def test_reply_between_thresholds_leaves_delay_unchanged():
    limiter, _ = _limiter(_pacing(base_delay_seconds=4.0))

    limiter.on_success(rtt_seconds=3.0)

    assert limiter.current_delay == 4.0


def test_waits_are_jittered_not_constant():
    limiter, slept = _limiter(_pacing(**NO_MACRO_BREAK))

    for _ in range(50):
        limiter.wait()

    assert len(set(slept)) == 50
    assert all(delay > 0 for delay in slept)


def test_mean_wait_tracks_the_aimd_base_delay():
    limiter, slept = _limiter(_pacing(max_delay_seconds=1000.0, **NO_MACRO_BREAK))

    for _ in range(5000):
        limiter.wait()

    assert abs(statistics.mean(slept) - 2.0) < 0.1


def test_wait_is_clamped_between_min_sleep_and_max_delay():
    limiter, slept = _limiter(_pacing(base_delay_seconds=30.0, min_sleep_seconds=25.0, **NO_MACRO_BREAK))

    for _ in range(200):
        limiter.wait()

    assert 25.0 <= min(slept) and max(slept) <= 30.0


def test_macro_break_happens_every_k_requests():
    limiter, slept = _limiter(_pacing())

    for _ in range(10):
        limiter.wait()

    assert len(slept) == 12
    assert [index for index, value in enumerate(slept) if value == 100.0] == [4, 10]


def test_macro_break_interval_and_length_are_randomized_within_bounds():
    pacing = _pacing(macro_break_every_min_requests=3, macro_break_every_max_requests=9,
                     macro_break_min_seconds=45.0, macro_break_max_seconds=90.0,
                     base_delay_seconds=0.01, max_delay_seconds=0.02)
    limiter, slept = _limiter(pacing, seed=3)

    for _ in range(300):
        limiter.wait()

    breaks = [value for value in slept if value >= 45.0]
    request_numbers = []
    count = 0
    for value in slept:
        if value < 45.0:
            count += 1
        else:
            request_numbers.append(count + 1)
    gaps = [b - a for a, b in zip(request_numbers, request_numbers[1:])]
    assert all(45.0 <= value <= 90.0 for value in breaks)
    assert len(set(round(value, 3) for value in breaks)) > 5
    assert all(3 <= gap <= 9 for gap in gaps) and len(set(gaps)) > 2
