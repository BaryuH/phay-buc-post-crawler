"""Command line entrypoint: crawl, comments, stats, export."""
import argparse
import json
import logging
import sys
from datetime import datetime, time, timezone
from pathlib import Path

from .accounts import load_accounts
from .bootstrap import build_pool
from .errors import CrawlerError
from .exporter import WRITERS
from .orchestrator import CrawlOptions, Crawler
from .resolver import resolve_page_id
from .settings import load_settings
from .storage import Storage

DEFAULT_SETTINGS_PATH = "config/settings.yaml"
DEFAULT_ACCOUNTS_PATH = "config/accounts.json"
DATE_FORMAT = "%Y-%m-%d"
LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"


def _parse_day(text, boundary):
    day = datetime.strptime(text, DATE_FORMAT).date()
    return int(datetime.combine(day, boundary, tzinfo=timezone.utc).timestamp())


def _add_common(parser):
    parser.add_argument("--config", default=DEFAULT_SETTINGS_PATH, help="settings YAML")
    parser.add_argument("--database", help="override database_path from settings")


def _add_accounts(parser):
    parser.add_argument("--accounts", default=DEFAULT_ACCOUNTS_PATH, help="accounts JSON")


def build_parser():
    parser = argparse.ArgumentParser(prog="crawler", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    crawl = commands.add_parser("crawl", help="collect posts and comment trees from a page")
    _add_common(crawl)
    _add_accounts(crawl)
    crawl.add_argument("--page", required=True, help="page URL or numeric id")
    crawl.add_argument("--limit", type=int, help="max new posts this run")
    crawl.add_argument("--start-date", help="oldest post date, YYYY-MM-DD (UTC, inclusive)")
    crawl.add_argument("--end-date", help="newest post date, YYYY-MM-DD (UTC, inclusive)")
    crawl.add_argument("--stop-on-existing", type=int, metavar="N",
                       help="incremental mode: scan newest-first and stop after N consecutive stored posts")
    crawl.add_argument("--retry-failed", action="store_true", help="re-queue posts marked FAILED first")

    comments = commands.add_parser("comments", help="collect comments for queued posts only")
    _add_common(comments)
    _add_accounts(comments)
    comments.add_argument("--retry-failed", action="store_true", help="re-queue posts marked FAILED first")

    stats = commands.add_parser("stats", help="print stored row counts")
    _add_common(stats)

    export = commands.add_parser("export", help="write stored data as JSONL or CSV")
    _add_common(export)
    export.add_argument("--out", required=True, help="output path")
    export.add_argument("--format", choices=sorted(WRITERS), default="jsonl")
    return parser


def _configure_logging(log_path):
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT,
                        handlers=[logging.StreamHandler(), logging.FileHandler(path, encoding="utf-8")])


def _open_storage(args, settings):
    return Storage(args.database or settings.database_path)


def _crawl_options(args, resolve):
    return CrawlOptions(
        page_id=resolve(args.page),
        limit=args.limit,
        start_time=_parse_day(args.start_date, time.min) if args.start_date else None,
        end_time=_parse_day(args.end_date, time.max.replace(microsecond=0)) if args.end_date else None,
        stop_after_existing=args.stop_on_existing,
    )


def _run_crawl(args, settings):
    accounts = load_accounts(args.accounts)
    storage = _open_storage(args, settings)
    pool = build_pool(accounts, settings)
    try:
        def fetch_html(url):
            with pool.lease(settings.pool.acquire_timeout_seconds) as session:
                return session.transport.get_html(url)

        if args.retry_failed:
            storage.requeue_failed_posts()
        options = _crawl_options(args, lambda target: resolve_page_id(target, fetch_html))
        stats = Crawler(settings, storage, pool).run(options)
    finally:
        pool.close()
        storage.close()
    print(json.dumps(stats, ensure_ascii=False, indent=2))


def _run_comments(args, settings):
    accounts = load_accounts(args.accounts)
    storage = _open_storage(args, settings)
    pool = build_pool(accounts, settings)
    try:
        if args.retry_failed:
            storage.requeue_failed_posts()
        stats = Crawler(settings, storage, pool).drain_comments()
    finally:
        pool.close()
        storage.close()
    print(json.dumps(stats, ensure_ascii=False, indent=2))


def _run_stats(args, settings):
    storage = _open_storage(args, settings)
    try:
        print(json.dumps(storage.stats(), ensure_ascii=False, indent=2))
    finally:
        storage.close()


def _run_export(args, settings):
    storage = _open_storage(args, settings)
    try:
        written = WRITERS[args.format](storage.export_posts(), args.out)
        print(f"exported {written} {'posts' if args.format == 'jsonl' else 'comment rows'} to {args.out}")
    finally:
        storage.close()


COMMANDS = {"crawl": _run_crawl, "comments": _run_comments, "stats": _run_stats, "export": _run_export}


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings(args.config)
        _configure_logging(settings.log_path)
        COMMANDS[args.command](args, settings)
    except (CrawlerError, ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0
