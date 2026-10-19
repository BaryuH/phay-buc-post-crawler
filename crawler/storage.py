"""SQLite WAL persistence: posts, comments, cursors, and the post work queue."""
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

PENDING = "PENDING"
IN_PROGRESS = "IN_PROGRESS"
DONE = "DONE"
FAILED = "FAILED"

FEED_SCOPE = "feed"
COMMENTS_SCOPE = "comments"

BUSY_TIMEOUT_MILLISECONDS = 30_000

# reaction_count/share_count are left untyped on purpose: Facebook returns either
# an exact integer or a localized string such as "4.1K" and we keep the raw value.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    post_id TEXT PRIMARY KEY,
    page_id TEXT NOT NULL,
    feedback_id TEXT,
    created_at INTEGER,
    text TEXT,
    permalink TEXT,
    comment_count INTEGER,
    reaction_count,
    share_count,
    page_name TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING',
    error TEXT,
    crawled_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_posts_status ON posts (status);

CREATE TABLE IF NOT EXISTS comments (
    comment_id TEXT PRIMARY KEY,
    post_id TEXT NOT NULL,
    parent_comment_id TEXT,
    author_id TEXT,
    author_name TEXT,
    author_url TEXT,
    text TEXT NOT NULL,
    reaction_count,
    simhash TEXT,
    is_near_duplicate INTEGER NOT NULL DEFAULT 0,
    crawled_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_comments_post ON comments (post_id);

CREATE TABLE IF NOT EXISTS checkpoints (
    scope TEXT NOT NULL,
    key TEXT NOT NULL,
    cursor TEXT NOT NULL,
    PRIMARY KEY (scope, key)
);
"""


@dataclass(frozen=True)
class ClaimedPost:
    post_id: str
    feedback_id: Optional[str]


class Storage:
    def __init__(self, path, clock=time.time):
        self._clock = clock
        self._local = threading.local()
        self._connections = []
        self._connections_lock = threading.Lock()
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._path = str(path)
        connection = self._connection()
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(_SCHEMA)

    def _connection(self):
        connection = getattr(self._local, "connection", None)
        if connection is None:
            # isolation_level=None: transactions are explicit via _transaction().
            # check_same_thread=False only so close() can shut every thread's connection;
            # each connection is otherwise used by its owning thread alone.
            connection = sqlite3.connect(self._path, isolation_level=None, check_same_thread=False,
                                         timeout=BUSY_TIMEOUT_MILLISECONDS / 1000)
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MILLISECONDS}")
            connection.row_factory = sqlite3.Row
            self._local.connection = connection
            with self._connections_lock:
                self._connections.append(connection)
        return connection

    @contextmanager
    def _transaction(self):
        connection = self._connection()
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        connection.execute("COMMIT")

    def close(self):
        with self._connections_lock:
            for connection in self._connections:
                connection.close()
            self._connections.clear()
        self._local = threading.local()

    # --- existence checks -------------------------------------------------

    def post_exists(self, post_id):
        row = self._connection().execute(
            "SELECT 1 FROM posts WHERE post_id = ?", (post_id,)).fetchone()
        return row is not None

    def comment_exists(self, comment_id):
        row = self._connection().execute(
            "SELECT 1 FROM comments WHERE comment_id = ?", (comment_id,)).fetchone()
        return row is not None

    def iter_post_ids(self):
        cursor = self._connection().execute("SELECT post_id FROM posts")
        return (row["post_id"] for row in cursor)

    def iter_comment_ids(self):
        cursor = self._connection().execute("SELECT comment_id FROM comments")
        return (row["comment_id"] for row in cursor)

    def recent_simhashes(self, limit):
        rows = self._connection().execute(
            "SELECT simhash FROM comments WHERE simhash IS NOT NULL ORDER BY rowid DESC LIMIT ?",
            (limit,)).fetchall()
        return [int(row["simhash"], 16) for row in reversed(rows)]

    # --- checkpoints ------------------------------------------------------

    def get_cursor(self, scope, key):
        row = self._connection().execute(
            "SELECT cursor FROM checkpoints WHERE scope = ? AND key = ?", (scope, key)).fetchone()
        return row["cursor"] if row else None

    @staticmethod
    def _write_cursor(connection, scope, key, cursor):
        if cursor:
            connection.execute(
                "INSERT INTO checkpoints (scope, key, cursor) VALUES (?, ?, ?) "
                "ON CONFLICT (scope, key) DO UPDATE SET cursor = excluded.cursor",
                (scope, key, cursor))
        else:
            connection.execute(
                "DELETE FROM checkpoints WHERE scope = ? AND key = ?", (scope, key))

    # --- producer side ----------------------------------------------------

    def _insert_posts(self, connection, posts):
        now = self._clock()
        inserted = 0
        for post in posts:
            result = connection.execute(
                "INSERT INTO posts (post_id, page_id, feedback_id, created_at, text, "
                "permalink, comment_count, reaction_count, share_count, page_name, status, crawled_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (post_id) DO NOTHING",
                (post.post_id, post.page_id, post.feedback_id, post.created_at, post.text,
                 post.permalink, post.comment_count, post.reaction_count, post.share_count,
                 post.page_name, PENDING, now))
            inserted += result.rowcount
        return inserted

    def save_feed_page(self, page_id, posts, next_cursor):
        """Insert new posts and advance the feed cursor in one transaction.

        A falsy next_cursor means the feed is exhausted and clears the checkpoint.
        Returns the number of posts that were actually new.
        """
        with self._transaction() as connection:
            inserted = self._insert_posts(connection, posts)
            self._write_cursor(connection, FEED_SCOPE, page_id, next_cursor)
        return inserted

    def save_posts(self, posts):
        """Insert new posts without touching the feed cursor (incremental scans)."""
        with self._transaction() as connection:
            return self._insert_posts(connection, posts)

    # --- consumer side ----------------------------------------------------

    def claim_next_post(self) -> Optional[ClaimedPost]:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT post_id, feedback_id FROM posts WHERE status = ? ORDER BY rowid LIMIT 1",
                (PENDING,)).fetchone()
            if row is None:
                return None
            connection.execute("UPDATE posts SET status = ? WHERE post_id = ?",
                               (IN_PROGRESS, row["post_id"]))
        return ClaimedPost(row["post_id"], row["feedback_id"])

    def reset_in_progress(self):
        """Return posts abandoned by a crashed run to the queue."""
        with self._transaction() as connection:
            return connection.execute(
                "UPDATE posts SET status = ? WHERE status = ?", (PENDING, IN_PROGRESS)).rowcount

    def save_comment_page(self, post_id, comments, next_cursor):
        """Insert one page of comments/replies and advance the post's comment cursor atomically."""
        now = self._clock()
        with self._transaction() as connection:
            for comment in comments:
                connection.execute(
                    "INSERT INTO comments (comment_id, post_id, parent_comment_id, author_id, "
                    "author_name, author_url, text, reaction_count, simhash, is_near_duplicate, crawled_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (comment_id) DO NOTHING",
                    (comment.comment_id, comment.post_id, comment.parent_comment_id, comment.author_id,
                     comment.author_name, comment.author_url, comment.text, comment.reaction_count,
                     comment.simhash, int(comment.is_near_duplicate), now))
            self._write_cursor(connection, COMMENTS_SCOPE, post_id, next_cursor)

    def finish_post(self, post_id):
        with self._transaction() as connection:
            connection.execute("UPDATE posts SET status = ?, error = NULL WHERE post_id = ?",
                               (DONE, post_id))
            self._write_cursor(connection, COMMENTS_SCOPE, post_id, None)

    def fail_post(self, post_id, error):
        with self._transaction() as connection:
            connection.execute("UPDATE posts SET status = ?, error = ? WHERE post_id = ?",
                               (FAILED, error, post_id))

    def requeue_failed_posts(self):
        with self._transaction() as connection:
            return connection.execute(
                "UPDATE posts SET status = ?, error = NULL WHERE status = ?",
                (PENDING, FAILED)).rowcount

    def unfinished_post_count(self):
        row = self._connection().execute(
            "SELECT COUNT(*) AS total FROM posts WHERE status IN (?, ?)",
            (PENDING, IN_PROGRESS)).fetchone()
        return row["total"]

    # --- reporting --------------------------------------------------------

    def stats(self):
        connection = self._connection()
        by_status = {row["status"]: row["total"] for row in connection.execute(
            "SELECT status, COUNT(*) AS total FROM posts GROUP BY status")}
        comments = connection.execute("SELECT COUNT(*) AS total FROM comments").fetchone()["total"]
        replies = connection.execute(
            "SELECT COUNT(*) AS total FROM comments WHERE parent_comment_id IS NOT NULL").fetchone()["total"]
        flagged = connection.execute(
            "SELECT COUNT(*) AS total FROM comments WHERE is_near_duplicate = 1").fetchone()["total"]
        return {
            "posts": sum(by_status.values()),
            "posts_by_status": by_status,
            "comments": comments,
            "replies": replies,
            "near_duplicate_comments": flagged,
        }

    def export_posts(self):
        """Yield one nested dict per post: post fields plus comments with their replies."""
        connection = self._connection()
        for post in connection.execute("SELECT * FROM posts ORDER BY rowid"):
            rows = connection.execute(
                "SELECT * FROM comments WHERE post_id = ? ORDER BY rowid", (post["post_id"],)).fetchall()
            yield {**dict(post), "comments": _nest_comments(rows)}


def _nest_comments(rows):
    top_level = []
    by_id = {}
    for row in rows:
        entry = {**dict(row), "replies": []}
        by_id[entry["comment_id"]] = entry
        parent = by_id.get(entry["parent_comment_id"])
        (parent["replies"] if parent else top_level).append(entry)
    return top_level
