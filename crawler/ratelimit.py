"""Per-session pacing: log-normal human jitter, AIMD base delay, randomized macro-breaks."""
import random
import threading
import time


class AimdLimiter:
    """AIMD applied to the delay: fast responses shave a constant off, slow or throttled
    ones multiply it, so backoff is quick and recovery is gradual."""

    def __init__(self, pacing, rng=None, sleep=time.sleep):
        self._pacing = pacing
        self._rng = rng or random.Random()
        self._sleep = sleep
        self._delay = pacing.base_delay_seconds
        self._requests_made = 0
        self._next_macro_break_at = self._schedule_macro_break()
        self._lock = threading.Lock()

    @property
    def current_delay(self):
        return self._delay

    def _schedule_macro_break(self):
        pacing = self._pacing
        return self._requests_made + self._rng.randint(pacing.macro_break_every_min_requests,
                                                       pacing.macro_break_every_max_requests)

    def _jittered_delay(self):
        sigma = self._pacing.jitter_sigma
        # mu = -sigma^2/2 keeps the multiplier's mean at 1, so the mean delay equals the AIMD base.
        multiplier = self._rng.lognormvariate(-sigma ** 2 / 2, sigma)
        delay = self._delay * multiplier
        return min(max(delay, self._pacing.min_sleep_seconds), self._pacing.max_delay_seconds)

    def wait(self):
        """Block before the next request: macro-break when due, then jittered delay."""
        macro_break = 0.0
        with self._lock:
            self._requests_made += 1
            if self._requests_made >= self._next_macro_break_at:
                macro_break = self._rng.uniform(self._pacing.macro_break_min_seconds,
                                                self._pacing.macro_break_max_seconds)
                self._next_macro_break_at = self._schedule_macro_break()
            delay = self._jittered_delay()
        if macro_break:
            self._sleep(macro_break)
        self._sleep(delay)

    def _increase(self):
        self._delay = min(self._pacing.max_delay_seconds,
                          self._delay * self._pacing.throttle_multiplier)

    def on_success(self, rtt_seconds):
        """A fast reply lowers the delay, a slow one is an early congestion signal."""
        with self._lock:
            if rtt_seconds < self._pacing.fast_rtt_seconds:
                self._delay = max(self._pacing.min_delay_seconds,
                                  self._delay - self._pacing.additive_decrease_seconds)
            elif rtt_seconds > self._pacing.slow_rtt_seconds:
                self._increase()

    def on_throttle(self):
        with self._lock:
            self._increase()
