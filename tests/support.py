"""Shared builders for integration tests."""
import dataclasses
from pathlib import Path

from crawler.accounts import Account
from crawler.bootstrap import build_pool
from crawler.orchestrator import Crawler
from crawler.settings import load_settings
from crawler.storage import Storage

SETTINGS_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"


def make_settings(url, database_path, **dedup_overrides):
    shipped = load_settings(SETTINGS_PATH)
    return dataclasses.replace(
        shipped,
        graphql_url=url,
        database_path=str(database_path),
        pacing=dataclasses.replace(shipped.pacing, base_delay_seconds=0.0, min_delay_seconds=0.0,
                                   max_delay_seconds=0.0, min_sleep_seconds=0.0,
                                   macro_break_every_min_requests=1_000_000,
                                   macro_break_every_max_requests=1_000_000,
                                   macro_break_min_seconds=0.0, macro_break_max_seconds=0.0),
        pool=dataclasses.replace(shipped.pool, cooldown_seconds=1000.0, quota_per_account=1_000_000,
                                 acquire_timeout_seconds=0.3),
        crawl=dataclasses.replace(shipped.crawl, consumer_threads=2, feed_page_size=3),
        dedup=dataclasses.replace(shipped.dedup, **dedup_overrides),
    )


def make_accounts(count):
    return [Account(name=f"clone-{n}", cookies={"c_user": str(n)}, fb_dtsg=f"dtsg-{n}",
                    proxy=None, impersonate=None)
            for n in range(1, count + 1)]


class Harness:
    """A crawler wired to a database, a pool, and a fake server; reusable across restarts."""

    def __init__(self, settings, account_count):
        self.settings = settings
        self.storage = Storage(settings.database_path)
        self.pool = build_pool(make_accounts(account_count), settings)
        self.crawler = Crawler(settings, self.storage, self.pool)

    def close(self):
        self.pool.close()
        self.storage.close()
