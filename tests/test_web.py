import json
import threading
import urllib.error
import urllib.request

import pytest

from crawler.models import Comment, Post
from crawler.storage import Storage
from web.data import fold
from web.server import make_server


def post(post_id, text, created_at, comment_count=0, reaction_count=0):
    return Post(post_id=post_id, page_id="page", feedback_id=f"fb-{post_id}", created_at=created_at,
                text=text, permalink=f"https://www.facebook.com/{post_id}", comment_count=comment_count,
                reaction_count=reaction_count, share_count=0, page_name="UIT Confession")


def comment(comment_id, post_id, text, parent=None, author_id="u1", author_name="Nguyễn Văn A",
            duplicate=False):
    return Comment(comment_id=comment_id, post_id=post_id, parent_comment_id=parent, author_id=author_id,
                   author_name=author_name, author_url=f"https://www.facebook.com/{author_id}", text=text,
                   reaction_count="3", is_near_duplicate=duplicate)


@pytest.fixture
def api(tmp_path):
    database_path = tmp_path / "crawler.db"
    storage = Storage(database_path)
    storage.save_feed_page("page", [
        post("p1", "Tôi muốn tìm bạn cùng phòng", 300, comment_count=3, reaction_count=10),
        post("p2", "Giảm 100% học phí?", 200, comment_count=9, reaction_count="4.1K"),
        post("p3", "Hơn 100 người đăng ký", 100, comment_count=0, reaction_count=1),
    ], None)
    storage.save_comment_page("p1", [
        comment("c1", "p1", "Mình cũng đang tìm", author_id="u1", author_name="Nguyễn Văn A"),
        comment("r1", "p1", "Ib mình nhé", parent="c1", author_id="u2", author_name="Trần Thị B"),
        comment("c2", "p1", "Quảng cáo spam spam spam", author_id="u3", author_name="Lê Văn C", duplicate=True),
    ], None)
    storage.fail_post("p3", "boom")
    storage.close()

    server = make_server(database_path, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"

    def get(path):
        try:
            with urllib.request.urlopen(base + path) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read()

    def get_json(path):
        status, _, body = get(path)
        return status, json.loads(body)

    get_json.raw = get
    yield get_json
    server.shutdown()
    server.server_close()


def test_fold_removes_vietnamese_diacritics_and_case():
    assert fold("Đường Tôi Ơi") == "duong toi oi"


def test_stats_separate_top_level_comments_from_replies(api):
    status, stats = api("/api/stats")

    assert status == 200
    assert (stats["posts"], stats["comments"], stats["replies"], stats["near_duplicate_comments"]) == (3, 2, 1, 1)
    assert stats["posts_by_status"] == {"PENDING": 2, "FAILED": 1}


def test_post_search_ignores_diacritics_and_case(api):
    _, result = api("/api/posts?q=toi%20MUON")

    assert [p["post_id"] for p in result["items"]] == ["p1"]


def test_post_search_treats_percent_literally(api):
    _, result = api("/api/posts?q=100%25")

    assert [p["post_id"] for p in result["items"]] == ["p2"]


def test_post_search_accepts_an_exact_post_id(api):
    _, result = api("/api/posts?q=p3")

    assert [p["post_id"] for p in result["items"]] == ["p3"]


def test_posts_sort_and_filter(api):
    _, by_comments = api("/api/posts?sort=comments")
    _, oldest = api("/api/posts?sort=oldest")
    _, busy = api("/api/posts?min_comments=3")
    _, failed = api("/api/posts?status=FAILED")

    assert [p["post_id"] for p in by_comments["items"]] == ["p2", "p1", "p3"]
    assert [p["post_id"] for p in oldest["items"]] == ["p3", "p2", "p1"]
    assert [p["post_id"] for p in busy["items"]] == ["p1", "p2"]
    assert [p["post_id"] for p in failed["items"]] == ["p3"]


def test_posts_are_paged_with_a_stable_total(api):
    _, first = api("/api/posts?limit=2")
    _, second = api("/api/posts?limit=2&offset=2")

    assert (first["total"], len(first["items"]), len(second["items"])) == (3, 2, 1)
    assert first["items"][0]["stored_comments"] == 3


def test_post_detail_nests_replies_under_their_comment(api):
    status, detail = api("/api/posts/p1")

    assert status == 200
    assert [c["comment_id"] for c in detail["comments"]] == ["c1", "c2"]
    assert [r["comment_id"] for r in detail["comments"][0]["replies"]] == ["r1"]
    assert detail["comments"][1]["is_near_duplicate"] is True
    assert detail["comments"][0]["author_name"] == "Nguyễn Văn A"


def test_comment_search_filters_by_text_author_level_and_duplicates(api):
    _, by_text = api("/api/comments?q=ib%20minh")
    _, by_author = api("/api/comments?author=le%20van%20c")
    _, by_author_id = api("/api/comments?author=u2")
    _, replies = api("/api/comments?level=replies")
    _, top_level = api("/api/comments?level=comments")
    _, flagged = api("/api/comments?duplicates=1")

    assert [c["comment_id"] for c in by_text["items"]] == ["r1"]
    assert [c["comment_id"] for c in by_author["items"]] == ["c2"]
    assert [c["comment_id"] for c in by_author_id["items"]] == ["r1"]
    assert [c["comment_id"] for c in replies["items"]] == ["r1"]
    assert sorted(c["comment_id"] for c in top_level["items"]) == ["c1", "c2"]
    assert [c["comment_id"] for c in flagged["items"]] == ["c2"]
    assert by_text["items"][0]["post_snippet"].startswith("Tôi muốn tìm")


@pytest.mark.parametrize("path", ["/api/posts?sort=drop_table", "/api/posts?limit=abc", "/api/posts?limit=0",
                                  "/api/posts?status=NOPE", "/api/comments?level=x", "/api/posts?offset=-1"])
def test_invalid_parameters_are_rejected_with_400(api, path):
    status, body = api(path)

    assert status == 400 and body["error"]


def test_unknown_post_and_api_return_404(api):
    assert api("/api/posts/nope")[0] == 404
    assert api("/api/nothing")[0] == 404


def test_static_app_is_served_with_security_headers(api):
    status, headers, body = api.raw("/")

    assert status == 200 and "Phây-búc crawler".encode() in body
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert api.raw("/app.js")[0] == 200 and api.raw("/style.css")[0] == 200


@pytest.mark.parametrize("path", ["/../README.md", "/..%2fREADME.md", "/data/crawler.db", "/web/server.py"])
def test_files_outside_the_static_whitelist_are_not_served(api, path):
    assert api.raw(path)[0] == 404


def test_missing_database_returns_503_with_a_hint(tmp_path):
    server = make_server(tmp_path / "absent.db", "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/api/stats")
        assert error.value.code == 503
        assert "crawler crawl" in json.loads(error.value.read())["error"]
    finally:
        server.shutdown()
        server.server_close()
