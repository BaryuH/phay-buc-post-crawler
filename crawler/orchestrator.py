"""Dual-queue orchestration: a feed producer fills the post queue, comment consumers drain it."""
import logging
import threading
from dataclasses import dataclass, replace
from functools import partial
from typing import Optional

from .dedup import NearDuplicateIndex, compute_simhash
from .errors import AcquireTimeoutError, CrawlerError, PoolExhaustedError
from .facebook import queries
from .facebook.parsers import (feedback_id_for_post, parse_comments_page, parse_feed_page,
                               parse_replies)
from .requester import LogicalRequest, Requester
from .seen import SeenIndex
from .storage import COMMENTS_SCOPE, FEED_SCOPE

logger = logging.getLogger(__name__)

WORKER_POLL_SECONDS = 0.2
SIMHASH_HEX_WIDTH = 16


@dataclass(frozen=True)
class CrawlOptions:
    page_id: str
    limit: Optional[int] = None
    start_time: Optional[int] = None
    end_time: Optional[int] = None
    stop_after_existing: Optional[int] = None


class Crawler:
    def __init__(self, settings, storage, pool):
        self._settings = settings
        self._storage = storage
        self._pool = pool
        self._requester = Requester(pool, settings.pool, settings.crawl.request_attempts)
        self._seen = SeenIndex(storage, settings.dedup)
        self._near_duplicates = NearDuplicateIndex(settings.dedup.simhash_max_distance,
                                                   settings.dedup.near_duplicate_window)
        self._stop = threading.Event()
        self._feed_finished = threading.Event()
        self._failures = []
        self._failures_lock = threading.Lock()

    # --- entry point ------------------------------------------------------

    def run(self, options):
        """Crawl until the feed is exhausted and every queued post is done. Returns storage stats."""
        return self._execute(partial(self._produce_feed, options))

    def drain_comments(self):
        """Collect comments for queued posts only; no feed requests."""
        self._feed_finished.set()
        return self._execute(None)

    def _execute(self, feed_work):
        self._storage.reset_in_progress()
        self._seen.warm_from_storage()
        for fingerprint in self._storage.recent_simhashes(self._settings.dedup.near_duplicate_window):
            self._near_duplicates.remember(fingerprint)

        threads = []
        if feed_work is not None:
            threads.append(threading.Thread(target=self._guarded, args=(feed_work,), name="feed-producer"))
        threads += [threading.Thread(target=self._guarded, args=(self._consume_posts,),
                                     name=f"comment-consumer-{n}")
                    for n in range(self._settings.crawl.consumer_threads)]
        for thread in threads:
            thread.start()
        try:
            for thread in threads:
                thread.join()
        except KeyboardInterrupt:
            self._stop.set()
            for thread in threads:
                thread.join()
            raise
        if self._failures:
            raise self._failures[0]
        return self._storage.stats()

    def _guarded(self, work):
        try:
            work()
        except BaseException as error:
            with self._failures_lock:
                self._failures.append(error)
            self._stop.set()

    # --- producer: feed -> post queue -------------------------------------

    def _produce_feed(self, options):
        try:
            self._scan_feed(options)
        finally:
            self._feed_finished.set()

    def _scan_feed(self, options):
        if options.stop_after_existing is None:
            self._scan_full(options)
        else:
            self._scan_incremental(options)

    def _scan_incremental(self, options):
        """Newest-first scan that stops after N consecutive already-stored posts.

        Never reads or writes the feed cursor, so it cannot disturb an interrupted full crawl.
        """
        cursor = None
        stored_this_run = 0
        consecutive_existing = 0
        while not self._stop.is_set():
            page = self._requester.fetch(self._feed_request(options, cursor))
            candidates, passed_start = self._within_date_window(page.posts, options)
            fresh = []
            caught_up = False
            for post in candidates:
                if self._seen.has_post(post.post_id):
                    consecutive_existing += 1
                    if consecutive_existing >= options.stop_after_existing:
                        caught_up = True
                        break
                else:
                    consecutive_existing = 0
                    fresh.append(post)

            limit_hit = False
            if options.limit is not None:
                room = options.limit - stored_this_run
                limit_hit = len(fresh) >= room
                fresh = fresh[:room]
            self._storage.save_posts(fresh)
            for post in fresh:
                self._seen.remember_post(post.post_id)
            stored_this_run += len(fresh)
            logger.info("incremental feed: %d new posts (%d this run)", len(fresh), stored_this_run)

            if caught_up or limit_hit or passed_start or not page.next_cursor:
                return
            cursor = page.next_cursor

    def _scan_full(self, options):
        page_id = options.page_id
        cursor = self._storage.get_cursor(FEED_SCOPE, page_id)
        stored_this_run = 0
        while not self._stop.is_set():
            page = self._requester.fetch(self._feed_request(options, cursor))
            candidates, passed_start = self._within_date_window(page.posts, options)
            fresh = [post for post in candidates if not self._seen.has_post(post.post_id)]
            limit_hit = truncated = False
            if options.limit is not None:
                room = options.limit - stored_this_run
                truncated = len(fresh) > room
                limit_hit = len(fresh) >= room
                fresh = fresh[:room]

            exhausted = passed_start or not page.next_cursor
            if truncated:
                resume_cursor = cursor  # posts we did not store are still on this page
            elif exhausted and not limit_hit:
                resume_cursor = None  # next run starts again from the newest post
            else:
                resume_cursor = page.next_cursor
            self._storage.save_feed_page(page_id, fresh, resume_cursor)
            for post in fresh:
                self._seen.remember_post(post.post_id)
            stored_this_run += len(fresh)
            logger.info("feed: %d new posts (%d this run)", len(fresh), stored_this_run)

            if limit_hit or exhausted:
                return
            cursor = page.next_cursor

    def _feed_request(self, options, cursor):
        feed_request = queries.FeedRequest(page_id=options.page_id, cursor=cursor,
                                           page_size=self._settings.crawl.feed_page_size,
                                           after_time=options.start_time, before_time=options.end_time)
        return LogicalRequest(
            build_payload=lambda account: queries.build_feed_payload(
                account, self._settings.doc_ids.feed, feed_request),
            friendly_name=queries.FEED_FRIENDLY_NAME,
            referer=f"https://www.facebook.com/profile.php?id={options.page_id}",
            parse=lambda text: parse_feed_page(text, options.page_id),
        )

    @staticmethod
    def _within_date_window(posts, options):
        """Feed is newest-first: skip posts newer than the window, stop at the first older one."""
        kept = []
        for post in posts:
            created = post.created_at
            if options.end_time and created and created > options.end_time:
                continue
            if options.start_time and created and created < options.start_time:
                return kept, True
            kept.append(post)
        return kept, False

    # --- consumers: post queue -> comment tree ----------------------------

    def _consume_posts(self):
        while not self._stop.is_set():
            claimed = self._storage.claim_next_post()
            if claimed is None:
                if self._feed_finished.is_set():
                    return
                self._feed_finished.wait(WORKER_POLL_SECONDS)
                continue
            try:
                self._crawl_post(claimed)
            except (PoolExhaustedError, AcquireTimeoutError):
                # Affects every post; leave the claim IN_PROGRESS so the next run re-queues it.
                raise
            except CrawlerError as error:
                self._storage.fail_post(claimed.post_id, str(error))
                logger.warning("post %s failed: %s", claimed.post_id, error)

    def _crawl_post(self, claimed):
        post_id = claimed.post_id
        feedback_id = claimed.feedback_id or feedback_id_for_post(post_id)
        cursor = self._storage.get_cursor(COMMENTS_SCOPE, post_id)
        while True:
            page = self._requester.fetch(self._comments_request(post_id, feedback_id, cursor))
            rows = self._comment_rows(post_id, page.comments)
            self._storage.save_comment_page(post_id, rows, page.next_cursor)
            for row in rows:
                self._seen.remember_comment(row.comment_id)
            if not page.next_cursor:
                break
            cursor = page.next_cursor
        self._storage.finish_post(post_id)
        logger.info("post %s done", post_id)

    def _comments_request(self, post_id, feedback_id, cursor):
        comments_request = queries.CommentsRequest(feedback_id=feedback_id, cursor=cursor)
        return LogicalRequest(
            build_payload=lambda account: queries.build_comments_payload(
                account, self._settings.doc_ids.comments, comments_request),
            friendly_name=queries.COMMENTS_FRIENDLY_NAME,
            referer=f"https://www.facebook.com/{post_id}",
            parse=lambda text: parse_comments_page(text, post_id),
        )

    def _replies_request(self, comment):
        replies_request = queries.RepliesRequest(feedback_id=comment.feedback_id,
                                                 expansion_token=comment.expansion_token)
        return LogicalRequest(
            build_payload=lambda account: queries.build_replies_payload(
                account, self._settings.doc_ids.replies, replies_request),
            friendly_name=queries.REPLIES_FRIENDLY_NAME,
            referer=f"https://www.facebook.com/{comment.post_id}",
            parse=lambda text: parse_replies(text, comment.post_id, comment.comment_id),
        )

    def _comment_rows(self, post_id, top_level_comments):
        rows = []
        for comment in top_level_comments:
            # A stored top-level comment was committed together with all its replies.
            if self._seen.has_comment(comment.comment_id):
                continue
            replies = []
            if comment.expansion_token and comment.feedback_id:
                replies = self._requester.fetch(self._replies_request(comment))
            rows.extend(self._annotate(candidate) for candidate in [comment, *replies])
        if self._settings.dedup.near_duplicate_action == "drop":
            rows = [row for row in rows if not row.is_near_duplicate]
        return rows

    def _annotate(self, comment):
        fingerprint = compute_simhash(comment.text, self._settings.dedup.simhash_min_tokens)
        if fingerprint is None:
            return comment
        is_duplicate = self._near_duplicates.is_near_duplicate_then_remember(fingerprint)
        return replace(comment, simhash=f"{fingerprint:0{SIMHASH_HEX_WIDTH}x}", is_near_duplicate=is_duplicate)
