import dataclasses

import pytest

from crawler.dedup import BloomFilter
from crawler.errors import AcquireTimeoutError
from crawler.models import Post
from crawler.orchestrator import CrawlOptions
from crawler.seen import SeenIndex
from crawler.settings import DedupSettings
from crawler.storage import Storage
from tests.fake_facebook import (BASE_TIME, POST_SPACING_SECONDS, FakeComment, FakeFacebook,
                                 FakePost, build_catalog, expected_counts)
from tests.support import Harness, make_settings

PAGE_ID = "777"


@pytest.fixture
def world(tmp_path):
    created = {"servers": [], "harnesses": []}

    def start(posts, accounts=2, **dedup_overrides):
        settings = make_settings("http://placeholder/", tmp_path / "crawler.db", **dedup_overrides)
        server = FakeFacebook(posts, dataclasses.asdict(settings.doc_ids))
        settings = dataclasses.replace(settings, graphql_url=server.url)
        created["servers"].append(server)
        return server, settings, accounts

    def harness(settings, accounts):
        instance = Harness(settings, accounts)
        created["harnesses"].append(instance)
        return instance

    yield start, harness
    for instance in created["harnesses"]:
        instance.close()
    for server in created["servers"]:
        server.stop()


def stored(harness):
    return harness.storage.stats()


def test_full_crawl_stores_posts_comments_and_replies(world):
    start, harness = world
    catalog = build_catalog(post_count=7)
    server, settings, accounts = start(catalog)
    run = harness(settings, accounts)

    stats = run.crawler.run(CrawlOptions(page_id=PAGE_ID))

    expected = expected_counts(catalog)
    assert stats["posts"] == expected["posts"]
    assert stats["comments"] == expected["comments"]
    assert stats["replies"] == expected["replies"]
    assert stats["posts_by_status"] == {"DONE": 7}


def test_stored_authors_are_raw_and_replies_nest_in_export(world):
    start, harness = world
    catalog = [FakePost("p0", [FakeComment("c0", author_id="100777", replies=[FakeComment("c0-r0")])])]
    server, settings, accounts = start(catalog)
    run = harness(settings, accounts)

    run.crawler.run(CrawlOptions(page_id=PAGE_ID))

    exported = list(run.storage.export_posts())
    top = exported[0]["comments"][0]
    assert top["author_id"] == "100777" and top["author_name"] == "Nguyễn Văn A"
    assert [r["comment_id"] for r in top["replies"]] == ["c0-r0"]


def test_second_run_skips_everything_already_stored(world):
    start, harness = world
    server, settings, accounts = start(build_catalog(post_count=7))
    run = harness(settings, accounts)
    run.crawler.run(CrawlOptions(page_id=PAGE_ID))
    requests_after_first = len(server.requests)
    before = stored(run)

    again = harness(settings, accounts)
    again.crawler.run(CrawlOptions(page_id=PAGE_ID))

    assert stored(again) == before
    assert [r["kind"] for r in server.requests[requests_after_first:]] == ["feed"] * 3


def test_limit_stops_the_feed_and_next_run_continues_from_the_cursor(world):
    start, harness = world
    server, settings, accounts = start(build_catalog(post_count=7))
    first = harness(settings, accounts)

    first.crawler.run(CrawlOptions(page_id=PAGE_ID, limit=4))
    assert stored(first)["posts"] == 4

    second = harness(settings, accounts)
    second.crawler.run(CrawlOptions(page_id=PAGE_ID, limit=4))

    assert stored(second)["posts"] == 7
    continuing_feed_cursors = [r["variables"]["cursor"] for r in server.requests_for("feed")]
    assert continuing_feed_cursors.count(None) == 1


def test_date_window_stops_at_first_post_older_than_start(world):
    start, harness = world
    server, settings, accounts = start(build_catalog(post_count=7))
    run = harness(settings, accounts)
    start_time = BASE_TIME - 2 * POST_SPACING_SECONDS - 50

    run.crawler.run(CrawlOptions(page_id=PAGE_ID, start_time=start_time))

    assert stored(run)["posts"] == 3


def test_throttled_account_is_cooled_down_and_work_moves_to_the_other(world):
    start, harness = world
    catalog = build_catalog(post_count=5)
    server, settings, accounts = start(catalog, accounts=2)
    server.always_throttle_users.add("1")
    run = harness(settings, accounts)

    stats = run.crawler.run(CrawlOptions(page_id=PAGE_ID))

    expected = expected_counts(catalog)
    assert (stats["posts"], stats["comments"]) == (expected["posts"], expected["comments"])
    assert run.pool.states()["clone-1"].value == "COOLDOWN"
    assert run.pool.states()["clone-2"].value == "READY"


def test_logged_out_account_is_marked_dead_and_crawl_completes(world):
    start, harness = world
    catalog = build_catalog(post_count=4)
    server, settings, accounts = start(catalog, accounts=2)
    server.logged_out_users.add("2")
    run = harness(settings, accounts)

    stats = run.crawler.run(CrawlOptions(page_id=PAGE_ID))

    assert stats["comments"] == expected_counts(catalog)["comments"]
    assert run.pool.states()["clone-2"].value == "DEAD"


def test_interrupted_crawl_resumes_without_duplicates_or_loss(world):
    start, harness = world
    catalog = build_catalog(post_count=7)
    server, settings, accounts = start(catalog, accounts=1)
    server.stop_serving_after = 12
    interrupted = harness(settings, accounts)

    with pytest.raises(AcquireTimeoutError):
        interrupted.crawler.run(CrawlOptions(page_id=PAGE_ID))
    partial = stored(interrupted)
    requests_before_resume = len(server.requests)
    finished_before = {p["post_id"] for p in interrupted.storage.export_posts() if p["status"] == "DONE"}

    server.stop_serving_after = None
    resumed = harness(settings, accounts)
    stats = resumed.crawler.run(CrawlOptions(page_id=PAGE_ID))

    expected = expected_counts(catalog)
    assert partial["comments"] < expected["comments"]
    assert (stats["posts"], stats["comments"], stats["replies"]) == (
        expected["posts"], expected["comments"], expected["replies"])
    assert stats["posts_by_status"] == {"DONE": 7}
    refetched = [r for r in server.requests[requests_before_resume:]
                 if r["kind"] != "feed" and r["variables"].get("id") in {f"feedback-{p}" for p in finished_before}]
    assert refetched == []


def test_comments_are_paged_through_end_cursors(world):
    start, harness = world
    catalog = [FakePost("p0", [FakeComment(f"c{n}") for n in range(4)])]
    server, settings, accounts = start(catalog, accounts=1)
    run = harness(settings, accounts)
    run.crawler.run(CrawlOptions(page_id=PAGE_ID))

    assert [r["variables"]["commentsAfterCursor"] for r in server.requests_for("comments")] == [None, "o2"]


def test_near_duplicate_comments_are_flagged_but_kept_raw(world):
    start, harness = world
    spam = ("mua ngay hôm nay giảm giá cực sốc liên hệ zalo để nhận ưu đãi lớn cho sinh viên "
            "trường đại học công nghệ thông tin số lượng có hạn nhanh tay đặt hàng trước khi hết")
    catalog = [FakePost("p0", [FakeComment("c0", text=spam, author_id="11"),
                               FakeComment("c1", text=spam + " nha", author_id="22")])]
    server, settings, accounts = start(catalog, accounts=1)
    run = harness(settings, accounts)

    stats = run.crawler.run(CrawlOptions(page_id=PAGE_ID))

    assert stats["comments"] == 2
    assert stats["near_duplicate_comments"] == 1


def test_near_duplicate_drop_mode_skips_the_repeat(world):
    start, harness = world
    spam = ("mua ngay hôm nay giảm giá cực sốc liên hệ zalo để nhận ưu đãi lớn cho sinh viên "
            "trường đại học công nghệ thông tin số lượng có hạn nhanh tay đặt hàng trước khi hết")
    catalog = [FakePost("p0", [FakeComment("c0", text=spam), FakeComment("c1", text=spam + " nha")])]
    server, settings, accounts = start(catalog, accounts=1, near_duplicate_action="drop")
    run = harness(settings, accounts)

    stats = run.crawler.run(CrawlOptions(page_id=PAGE_ID))

    assert stats["comments"] == 1


def test_bloom_false_positive_never_hides_an_unseen_post(tmp_path):
    storage = Storage(tmp_path / "bloom.db")
    saturated = DedupSettings(bloom_capacity=1, bloom_error_rate=0.9, simhash_max_distance=3,
                              simhash_min_tokens=8, near_duplicate_window=10, near_duplicate_action="flag")
    seen = SeenIndex(storage, saturated)
    for n in range(200):
        seen.remember_post(f"other-{n}")
    assert "never-stored" in seen._posts

    assert seen.has_post("never-stored") is False
    storage.close()


def test_incremental_scan_stores_only_new_posts_and_stops_after_n_existing(world):
    start, harness = world
    catalog = build_catalog(post_count=8)
    server, settings, accounts = start(catalog, accounts=1)
    first = harness(settings, accounts)
    first.crawler.run(CrawlOptions(page_id=PAGE_ID))
    feed_requests_before = len(server.requests_for("feed"))
    server.posts[0:0] = [FakePost("new1"), FakePost("new0")]

    again = harness(settings, accounts)
    stats = again.crawler.run(CrawlOptions(page_id=PAGE_ID, stop_after_existing=2))

    assert stats["posts"] == 10
    assert stats["posts_by_status"] == {"DONE": 10}
    assert len(server.requests_for("feed")) - feed_requests_before == 2


def test_incremental_scan_leaves_an_interrupted_feed_cursor_alone(world):
    start, harness = world
    server, settings, accounts = start(build_catalog(post_count=8), accounts=1)
    run = harness(settings, accounts)
    run.storage.save_feed_page(PAGE_ID, [], "o6")

    run.crawler.run(CrawlOptions(page_id=PAGE_ID, stop_after_existing=2))

    assert run.storage.get_cursor("feed", PAGE_ID) == "o6"
    assert server.requests_for("feed")[0]["variables"]["cursor"] is None


def test_comments_only_drains_queued_posts_without_touching_the_feed(world):
    start, harness = world
    catalog = build_catalog(post_count=2)
    server, settings, accounts = start(catalog, accounts=1)
    run = harness(settings, accounts)
    run.storage.save_feed_page(PAGE_ID, [
        Post(post_id="p0", page_id=PAGE_ID, feedback_id="feedback-p0", created_at=1, text="t",
             permalink=None, comment_count=5, reaction_count=0, share_count=0, page_name="n"),
    ], None)

    stats = run.crawler.drain_comments()

    assert server.requests_for("feed") == []
    assert stats["posts_by_status"] == {"DONE": 1}
    assert stats["comments"] == expected_counts(catalog[:1])["comments"]
