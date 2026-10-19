"""Duplicate detection: Bloom filter for ids, SimHash for near-duplicate text."""
import math
import re
import threading
from collections import deque
from typing import Optional

import mmh3

SIMHASH_BITS = 64
_TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)


class BloomFilter:
    """Probabilistic membership set. False positives possible, false negatives not."""

    def __init__(self, capacity, error_rate):
        if capacity < 1 or not 0 < error_rate < 1:
            raise ValueError("capacity must be >= 1 and error_rate in (0, 1)")
        self.bit_count = max(8, math.ceil(-capacity * math.log(error_rate) / math.log(2) ** 2))
        self.hash_count = max(1, round(self.bit_count / capacity * math.log(2)))
        self._bits = bytearray((self.bit_count + 7) // 8)
        self._lock = threading.Lock()

    def _positions(self, key):
        first, second = mmh3.hash64(key, signed=False)
        # Kirsch-Mitzenmacher: k indexes from two hashes keep the false-positive rate.
        second |= 1
        return [(first + i * second) % self.bit_count for i in range(self.hash_count)]

    def add(self, key):
        positions = self._positions(key)
        with self._lock:
            for position in positions:
                self._bits[position >> 3] |= 1 << (position & 7)

    def __contains__(self, key):
        positions = self._positions(key)
        with self._lock:
            return all(self._bits[position >> 3] & (1 << (position & 7)) for position in positions)


def _tokens(text):
    return _TOKEN_PATTERN.findall(text.lower())


def compute_simhash(text, min_tokens) -> Optional[int]:
    """64-bit SimHash over unigrams and bigrams; None when the text is too short to compare."""
    tokens = _tokens(text)
    if len(tokens) < min_tokens:
        return None
    features = tokens + [f"{a} {b}" for a, b in zip(tokens, tokens[1:])]
    weights = [0] * SIMHASH_BITS
    for feature in features:
        feature_hash = mmh3.hash64(feature, signed=False)[0]
        for bit in range(SIMHASH_BITS):
            weights[bit] += 1 if (feature_hash >> bit) & 1 else -1
    fingerprint = 0
    for bit, weight in enumerate(weights):
        if weight > 0:
            fingerprint |= 1 << bit
    return fingerprint


def hamming_distance(left, right):
    return (left ^ right).bit_count()


class NearDuplicateIndex:
    """Sliding window of fingerprints answering "is there one within max_distance?".

    Pigeonhole: two 64-bit hashes within distance d agree exactly on at least one of
    d+1 disjoint blocks, so lookups only compare hashes sharing a block value.
    """

    def __init__(self, max_distance, window):
        self._max_distance = max_distance
        self._window = window
        block_count = max_distance + 1
        self._block_ranges = [
            (index * SIMHASH_BITS // block_count, (index + 1) * SIMHASH_BITS // block_count)
            for index in range(block_count)
        ]
        self._buckets = [dict() for _ in self._block_ranges]
        self._order = deque()
        self._known = set()
        self._lock = threading.Lock()

    def _block_values(self, fingerprint):
        return [
            (fingerprint >> low) & ((1 << (high - low)) - 1)
            for low, high in self._block_ranges
        ]

    def _contains_neighbor(self, fingerprint, block_values):
        for bucket, value in zip(self._buckets, block_values):
            for candidate in bucket.get(value, ()):
                if hamming_distance(fingerprint, candidate) <= self._max_distance:
                    return True
        return False

    def _insert(self, fingerprint, block_values):
        for bucket, value in zip(self._buckets, block_values):
            bucket.setdefault(value, set()).add(fingerprint)
        self._known.add(fingerprint)
        self._order.append(fingerprint)

    def _evict_oldest(self):
        oldest = self._order.popleft()
        self._known.discard(oldest)
        for bucket, value in zip(self._buckets, self._block_values(oldest)):
            members = bucket[value]
            members.discard(oldest)
            if not members:
                del bucket[value]

    def remember(self, fingerprint):
        """Add a fingerprint without checking; used to warm the index from storage."""
        with self._lock:
            if fingerprint in self._known:
                return
            self._insert(fingerprint, self._block_values(fingerprint))
            if len(self._order) > self._window:
                self._evict_oldest()

    def is_near_duplicate_then_remember(self, fingerprint):
        """True when a stored fingerprint is within max_distance; otherwise stores this one."""
        block_values = self._block_values(fingerprint)
        with self._lock:
            if fingerprint in self._known or self._contains_neighbor(fingerprint, block_values):
                return True
            self._insert(fingerprint, block_values)
            if len(self._order) > self._window:
                self._evict_oldest()
            return False
