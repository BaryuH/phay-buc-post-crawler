"""Read-only queries over the crawler database for the web app."""
import sqlite3
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PAGE_SIZE = 30
MAX_PAGE_SIZE = 100
LIST_SNIPPET_CHARS = 240
COMMENT_POST_SNIPPET_CHARS = 120

POST_SORTS = {
    "newest": "p.created_at DESC, p.rowid DESC",
    "oldest": "p.created_at ASC, p.rowid ASC",
    "comments": "p.comment_count DESC, p.rowid DESC",
    # CAST reads "4.1K" as 4: coarse, but good enough to rank posts.
    "reactions": "CAST(p.reaction_count AS INTEGER) DESC, p.rowid DESC",
}
POST_STATUSES = ("", "DONE", "PENDING", "IN_PROGRESS", "FAILED")
COMMENT_LEVELS = ("all", "comments", "replies")


class DatabaseUnavailableError(Exception):
    pass


def fold(text):
    """Lower-case and strip Vietnamese diacritics so "toi" finds "Tôi"."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return stripped.replace("đ", "d").replace("Đ", "D").lower()


def _like_pattern(text):
    escaped = fold(text).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@dataclass(frozen=True)
class PostQuery:
    text: str = ""
    status: str = ""
    min_comments: int = 0
    sort: str = "newest"
    offset: int = 0
    limit: int = DEFAULT_PAGE_SIZE


@dataclass(frozen=True)
class CommentQuery:
    text: str = ""
    author: str = ""
    level: str = "all"
    near_duplicates_only: bool = False
    offset: int = 0
    limit: int = DEFAULT_PAGE_SIZE


class Database:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        if not self.path.is_file():
            raise DatabaseUnavailableError(f"database not found: {self.path}")
        connection = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.create_function("fold", 1, fold, deterministic=True)
        try:
            yield connection
        finally:
            connection.close()


def _validate_paging(query):
    if query.offset < 0 or not 1 <= query.limit <= MAX_PAGE_SIZE:
        raise ValueError(f"offset must be >= 0 and limit between 1 and {MAX_PAGE_SIZE}")


def stats(connection):
    by_status = {row["status"]: row["total"] for row in connection.execute(
        "SELECT status, COUNT(*) AS total FROM posts GROUP BY status")}
    totals = connection.execute(
        "SELECT COUNT(*) AS rows_total, "
        "COALESCE(SUM(parent_comment_id IS NOT NULL), 0) AS replies, "
        "COALESCE(SUM(is_near_duplicate), 0) AS flagged FROM comments").fetchone()
    return {
        "posts": sum(by_status.values()),
        "posts_by_status": by_status,
        "comments": totals["rows_total"] - totals["replies"],
        "replies": totals["replies"],
        "near_duplicate_comments": totals["flagged"],
    }


def list_posts(connection, query):
    _validate_paging(query)
    if query.sort not in POST_SORTS:
        raise ValueError(f"sort must be one of {sorted(POST_SORTS)}")
    if query.status not in POST_STATUSES:
        raise ValueError(f"status must be one of {POST_STATUSES}")

    where, params = [], []
    if query.text.strip():
        where.append("(fold(p.text) LIKE ? ESCAPE '\\' OR p.post_id = ?)")
        params += [_like_pattern(query.text.strip()), query.text.strip()]
    if query.status:
        where.append("p.status = ?")
        params.append(query.status)
    if query.min_comments > 0:
        where.append("p.comment_count >= ?")
        params.append(query.min_comments)
    clause = f"WHERE {' AND '.join(where)}" if where else ""

    total = connection.execute(f"SELECT COUNT(*) FROM posts p {clause}", params).fetchone()[0]
    rows = connection.execute(
        f"SELECT p.post_id, substr(p.text, 1, {LIST_SNIPPET_CHARS}) AS snippet, "
        "length(p.text) > ? AS truncated, p.created_at, p.comment_count, p.reaction_count, "
        "p.share_count, p.page_name, p.status, "
        "(SELECT COUNT(*) FROM comments c WHERE c.post_id = p.post_id) AS stored_comments "
        f"FROM posts p {clause} ORDER BY {POST_SORTS[query.sort]} LIMIT ? OFFSET ?",
        [LIST_SNIPPET_CHARS, *params, query.limit, query.offset]).fetchall()
    return {"total": total, "items": [dict(row, truncated=bool(row["truncated"])) for row in rows]}


def _comment_dict(row):
    entry = dict(row)
    entry["is_near_duplicate"] = bool(entry["is_near_duplicate"])
    return entry


def _nest(rows):
    top_level, by_id = [], {}
    for row in rows:
        entry = {**_comment_dict(row), "replies": []}
        by_id[entry["comment_id"]] = entry
        parent = by_id.get(entry["parent_comment_id"])
        (parent["replies"] if parent else top_level).append(entry)
    return top_level


def get_post(connection, post_id):
    post = connection.execute("SELECT * FROM posts WHERE post_id = ?", (post_id,)).fetchone()
    if post is None:
        return None
    rows = connection.execute(
        "SELECT comment_id, post_id, parent_comment_id, author_id, author_name, author_url, text, "
        "reaction_count, simhash, is_near_duplicate FROM comments WHERE post_id = ? ORDER BY rowid",
        (post_id,)).fetchall()
    return {**dict(post), "stored_comments": len(rows), "comments": _nest(rows)}


def search_comments(connection, query):
    _validate_paging(query)
    if query.level not in COMMENT_LEVELS:
        raise ValueError(f"level must be one of {COMMENT_LEVELS}")

    where, params = [], []
    if query.text.strip():
        where.append("fold(c.text) LIKE ? ESCAPE '\\'")
        params.append(_like_pattern(query.text.strip()))
    if query.author.strip():
        where.append("(fold(c.author_name) LIKE ? ESCAPE '\\' OR c.author_id = ?)")
        params += [_like_pattern(query.author.strip()), query.author.strip()]
    if query.level == "comments":
        where.append("c.parent_comment_id IS NULL")
    elif query.level == "replies":
        where.append("c.parent_comment_id IS NOT NULL")
    if query.near_duplicates_only:
        where.append("c.is_near_duplicate = 1")
    clause = f"WHERE {' AND '.join(where)}" if where else ""

    total = connection.execute(f"SELECT COUNT(*) FROM comments c {clause}", params).fetchone()[0]
    rows = connection.execute(
        "SELECT c.comment_id, c.post_id, c.parent_comment_id, c.author_id, c.author_name, c.author_url, "
        "c.text, c.reaction_count, c.is_near_duplicate, "
        f"substr(p.text, 1, {COMMENT_POST_SNIPPET_CHARS}) AS post_snippet "
        f"FROM comments c JOIN posts p ON p.post_id = c.post_id {clause} "
        "ORDER BY c.rowid DESC LIMIT ? OFFSET ?",
        [*params, query.limit, query.offset]).fetchall()
    return {"total": total, "items": [_comment_dict(row) for row in rows]}
