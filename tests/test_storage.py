import sqlite3
import threading

import pytest

from crawler.models import Comment, Post
from crawler.storage import (COMMENTS_SCOPE, FEED_SCOPE, Storage)


@pytest.fixture
def storage(tmp_path):
    instance = Storage(tmp_path / "crawler.db")
    yield instance
    instance.close()


def make_post(post_id, comment_count=2):
    return Post(post_id=post_id, page_id="page-1", feedback_id=f"fb-{post_id}", created_at=1_700_000_000,
                text=f"text {post_id}", permalink=f"https://example.test/{post_id}",
                comment_count=comment_count, reaction_count=5, share_count="1.2K", page_name="UIT Confessions")


def make_comment(comment_id, post_id="p1", parent=None, text="xin chào", author_id="u1", author_name="Nguyễn Văn A"):
    return Comment(comment_id=comment_id, post_id=post_id, parent_comment_id=parent, author_id=author_id,
                   author_name=author_name, author_url=f"https://example.test/{author_id}", text=text,
                   reaction_count="3")


def test_database_uses_wal_journal(storage):
    mode = storage._connection().execute("PRAGMA journal_mode").fetchone()[0]

    assert mode == "wal"


def test_feed_page_saves_posts_and_cursor_together(storage):
    inserted = storage.save_feed_page("page-1", [make_post("p1"), make_post("p2")], "cursor-A")

    assert inserted == 2
    assert storage.post_exists("p1") and storage.post_exists("p2")
    assert storage.get_cursor(FEED_SCOPE, "page-1") == "cursor-A"


def test_exhausted_feed_clears_cursor(storage):
    storage.save_feed_page("page-1", [make_post("p1")], "cursor-A")

    storage.save_feed_page("page-1", [], None)

    assert storage.get_cursor(FEED_SCOPE, "page-1") is None


def test_replaying_a_feed_page_does_not_duplicate_posts(storage):
    storage.save_feed_page("page-1", [make_post("p1")], "cursor-A")

    inserted = storage.save_feed_page("page-1", [make_post("p1")], "cursor-A")

    assert inserted == 0
    assert storage.stats()["posts"] == 1


def test_comment_page_failure_rolls_back_comments_and_cursor(storage):
    storage.save_feed_page("page-1", [make_post("p1")], None)
    broken = make_comment("c2")
    object.__setattr__(broken, "text", None)

    with pytest.raises(sqlite3.IntegrityError):
        storage.save_comment_page("p1", [make_comment("c1"), broken], "cursor-1")

    assert storage.stats()["comments"] == 0
    assert storage.get_cursor(COMMENTS_SCOPE, "p1") is None


def test_replaying_a_comment_page_is_idempotent(storage):
    storage.save_feed_page("page-1", [make_post("p1")], None)
    page = [make_comment("c1"), make_comment("r1", parent="c1")]

    storage.save_comment_page("p1", page, "cursor-1")
    storage.save_comment_page("p1", page, "cursor-1")

    assert storage.stats()["comments"] == 2
    assert storage.get_cursor(COMMENTS_SCOPE, "p1") == "cursor-1"


def test_raw_author_fields_are_stored_unmodified(storage):
    storage.save_feed_page("page-1", [make_post("p1")], None)

    storage.save_comment_page("p1", [make_comment("c1", author_id="100012345", author_name="Trần Thị B")], None)

    row = storage._connection().execute("SELECT author_id, author_name, text FROM comments").fetchone()
    assert (row["author_id"], row["author_name"], row["text"]) == ("100012345", "Trần Thị B", "xin chào")


def test_finish_post_marks_done_and_clears_comment_cursor(storage):
    storage.save_feed_page("page-1", [make_post("p1")], None)
    storage.save_comment_page("p1", [make_comment("c1")], "cursor-1")

    storage.finish_post("p1")

    assert storage.stats()["posts_by_status"] == {"DONE": 1}
    assert storage.get_cursor(COMMENTS_SCOPE, "p1") is None


def test_claim_hands_each_post_to_exactly_one_thread(storage):
    storage.save_feed_page("page-1", [make_post(f"p{n}") for n in range(40)], None)
    claimed = []
    claimed_lock = threading.Lock()

    def worker():
        while (post := storage.claim_next_post()) is not None:
            with claimed_lock:
                claimed.append(post.post_id)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(claimed) == sorted(f"p{n}" for n in range(40))


def test_reset_in_progress_returns_abandoned_posts_to_queue(storage):
    storage.save_feed_page("page-1", [make_post("p1"), make_post("p2")], None)
    storage.claim_next_post()

    assert storage.reset_in_progress() == 1

    assert storage.unfinished_post_count() == 2
    assert storage.claim_next_post().post_id == "p1"


def test_failed_posts_can_be_requeued(storage):
    storage.save_feed_page("page-1", [make_post("p1")], None)
    storage.claim_next_post()
    storage.fail_post("p1", "boom")

    assert storage.unfinished_post_count() == 0
    assert storage.requeue_failed_posts() == 1
    assert storage.claim_next_post().post_id == "p1"


def test_recent_simhashes_returns_oldest_to_newest_within_limit(storage):
    storage.save_feed_page("page-1", [make_post("p1")], None)
    comments = []
    for index, value in enumerate([0xA, 0xB, 0xC]):
        comment = make_comment(f"c{index}")
        object.__setattr__(comment, "simhash", f"{value:016x}")
        comments.append(comment)
    storage.save_comment_page("p1", comments, None)

    assert storage.recent_simhashes(2) == [0xB, 0xC]


def test_export_nests_replies_under_their_comment(storage):
    storage.save_feed_page("page-1", [make_post("p1")], None)
    storage.save_comment_page("p1", [
        make_comment("c1"),
        make_comment("r1", parent="c1", text="trả lời"),
        make_comment("c2"),
    ], None)

    exported = list(storage.export_posts())

    assert [c["comment_id"] for c in exported[0]["comments"]] == ["c1", "c2"]
    assert [r["comment_id"] for r in exported[0]["comments"][0]["replies"]] == ["r1"]
