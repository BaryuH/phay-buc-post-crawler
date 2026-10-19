"""Typed runtime settings loaded from YAML."""
import dataclasses
import typing
from dataclasses import dataclass
from pathlib import Path

import yaml


class SettingsError(ValueError):
    pass


@dataclass(frozen=True)
class DocIds:
    feed: str
    comments: str
    replies: str


@dataclass(frozen=True)
class TransportSettings:
    impersonate: str
    timeout_seconds: float
    max_attempts: int
    retry_backoff_seconds: float


@dataclass(frozen=True)
class PacingSettings:
    base_delay_seconds: float
    min_delay_seconds: float
    max_delay_seconds: float
    min_sleep_seconds: float
    jitter_sigma: float
    additive_decrease_seconds: float
    throttle_multiplier: float
    fast_rtt_seconds: float
    slow_rtt_seconds: float
    macro_break_every_min_requests: int
    macro_break_every_max_requests: int
    macro_break_min_seconds: float
    macro_break_max_seconds: float


@dataclass(frozen=True)
class PoolSettings:
    cooldown_seconds: float
    quota_per_account: int
    quota_cooldown_seconds: float
    acquire_timeout_seconds: float


@dataclass(frozen=True)
class CrawlSettings:
    consumer_threads: int
    feed_page_size: int
    request_attempts: int


@dataclass(frozen=True)
class DedupSettings:
    bloom_capacity: int
    bloom_error_rate: float
    simhash_max_distance: int
    simhash_min_tokens: int
    near_duplicate_window: int
    near_duplicate_action: str


@dataclass(frozen=True)
class Settings:
    graphql_url: str
    database_path: str
    log_path: str
    doc_ids: DocIds
    transport: TransportSettings
    pacing: PacingSettings
    pool: PoolSettings
    crawl: CrawlSettings
    dedup: DedupSettings


NEAR_DUPLICATE_ACTIONS = ("flag", "drop")


def _build(cls, mapping, path):
    if not isinstance(mapping, dict):
        raise SettingsError(f"'{path}' must be a mapping")
    hints = typing.get_type_hints(cls)
    names = {field.name for field in dataclasses.fields(cls)}
    unknown = set(mapping) - names
    if unknown:
        raise SettingsError(f"unknown keys under '{path}': {sorted(unknown)}")
    missing = names - set(mapping)
    if missing:
        raise SettingsError(f"missing keys under '{path}': {sorted(missing)}")

    values = {}
    for name in names:
        expected = hints[name]
        raw = mapping[name]
        child_path = f"{path}.{name}" if path else name
        if dataclasses.is_dataclass(expected):
            values[name] = _build(expected, raw, child_path)
        elif expected is float and isinstance(raw, int) and not isinstance(raw, bool):
            values[name] = float(raw)
        elif isinstance(raw, expected) and not (expected is int and isinstance(raw, bool)):
            values[name] = raw
        else:
            raise SettingsError(f"'{child_path}' must be {expected.__name__}, got {type(raw).__name__}")
    return cls(**values)


def _validate(settings):
    if settings.dedup.near_duplicate_action not in NEAR_DUPLICATE_ACTIONS:
        raise SettingsError(f"dedup.near_duplicate_action must be one of {NEAR_DUPLICATE_ACTIONS}")
    if settings.crawl.consumer_threads < 1:
        raise SettingsError("crawl.consumer_threads must be >= 1")
    if settings.pacing.min_delay_seconds > settings.pacing.max_delay_seconds:
        raise SettingsError("pacing.min_delay_seconds must be <= pacing.max_delay_seconds")
    pacing = settings.pacing
    if pacing.macro_break_every_min_requests > pacing.macro_break_every_max_requests:
        raise SettingsError("pacing.macro_break_every_min_requests must be <= ..._max_requests")
    if pacing.macro_break_min_seconds > pacing.macro_break_max_seconds:
        raise SettingsError("pacing.macro_break_min_seconds must be <= ..._max_seconds")
    if pacing.fast_rtt_seconds > pacing.slow_rtt_seconds:
        raise SettingsError("pacing.fast_rtt_seconds must be <= pacing.slow_rtt_seconds")
    if settings.pool.quota_per_account < 1:
        raise SettingsError("pool.quota_per_account must be >= 1")


def load_settings(path):
    with open(Path(path), encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    settings = _build(Settings, document, "")
    _validate(settings)
    return settings
