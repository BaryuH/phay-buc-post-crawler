import random

import pytest

from crawler.dedup import (BloomFilter, NearDuplicateIndex, compute_simhash,
                           hamming_distance)


def test_bloom_filter_has_no_false_negatives():
    bloom = BloomFilter(capacity=5000, error_rate=0.01)
    ids = [f"post-{n}" for n in range(5000)]

    for item in ids:
        bloom.add(item)

    assert all(item in bloom for item in ids)


def test_bloom_filter_false_positive_rate_stays_near_target():
    bloom = BloomFilter(capacity=5000, error_rate=0.01)
    for n in range(5000):
        bloom.add(f"post-{n}")

    false_positives = sum(f"other-{n}" in bloom for n in range(20000))

    assert false_positives / 20000 < 0.02


def test_bloom_filter_rejects_invalid_parameters():
    with pytest.raises(ValueError):
        BloomFilter(capacity=0, error_rate=0.01)
    with pytest.raises(ValueError):
        BloomFilter(capacity=10, error_rate=1.5)


SPAM = (
    "mua ngay hôm nay giảm giá cực sốc liên hệ zalo để nhận ưu đãi lớn "
    "cho sinh viên trường đại học công nghệ thông tin số lượng có hạn "
    "nhanh tay đặt hàng trước khi hết khuyến mãi áp dụng toàn quốc"
)
SPAM_VARIANT = SPAM + " nha"
UNRELATED = "mình đang tìm bạn cùng phòng ký túc xá khu B học kỳ này có ai muốn ghép không"


def test_simhash_of_near_identical_texts_is_within_threshold():
    first = compute_simhash(SPAM, min_tokens=8)
    second = compute_simhash(SPAM_VARIANT, min_tokens=8)

    assert hamming_distance(first, second) <= 3


def test_simhash_of_different_texts_is_far_apart():
    first = compute_simhash(SPAM, min_tokens=8)
    second = compute_simhash(UNRELATED, min_tokens=8)

    assert hamming_distance(first, second) > 3


def test_simhash_ignores_texts_shorter_than_minimum():
    assert compute_simhash("hay quá", min_tokens=8) is None


def test_simhash_is_case_and_punctuation_insensitive():
    assert compute_simhash(SPAM.upper() + "!!!", min_tokens=8) == compute_simhash(SPAM, min_tokens=8)


def test_index_flags_second_near_duplicate_only():
    index = NearDuplicateIndex(max_distance=3, window=100)
    original = compute_simhash(SPAM, min_tokens=8)

    assert index.is_near_duplicate_then_remember(original) is False
    assert index.is_near_duplicate_then_remember(compute_simhash(SPAM_VARIANT, min_tokens=8)) is True
    assert index.is_near_duplicate_then_remember(compute_simhash(UNRELATED, min_tokens=8)) is False


def test_index_matches_brute_force_hamming_search():
    rng = random.Random(7)
    max_distance = 3
    index = NearDuplicateIndex(max_distance=max_distance, window=10_000)
    stored = []
    base = rng.getrandbits(64)

    for _ in range(400):
        flips = rng.randint(0, 8)
        candidate = base
        for bit in rng.sample(range(64), flips):
            candidate ^= 1 << bit
        expected = any(hamming_distance(candidate, other) <= max_distance for other in stored)

        assert index.is_near_duplicate_then_remember(candidate) is expected
        if not expected:
            stored.append(candidate)


def test_index_forgets_fingerprints_beyond_window():
    index = NearDuplicateIndex(max_distance=3, window=2)
    first = 0
    far_a = (1 << 64) - 1
    far_b = 0x5555_5555_5555_5555

    index.is_near_duplicate_then_remember(first)
    index.is_near_duplicate_then_remember(far_a)
    index.is_near_duplicate_then_remember(far_b)

    assert index.is_near_duplicate_then_remember(first) is False


def test_remember_warms_index_for_later_checks():
    index = NearDuplicateIndex(max_distance=3, window=100)
    fingerprint = compute_simhash(SPAM, min_tokens=8)

    index.remember(fingerprint)

    assert index.is_near_duplicate_then_remember(fingerprint) is True
