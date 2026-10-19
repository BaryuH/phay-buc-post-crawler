import json

import pytest

from crawler.facebook.parsers import (MalformedResponseError, feedback_id_for_post,
                                      parse_comments_page, parse_feed_page, parse_replies)
from tests import fb_fixtures as fx


def test_feed_page_yields_posts_with_counts_and_cursor():
    body = fx.feed_response([fx.story("p1", comments=5), fx.story("p2")], end_cursor="next-1")

    page = parse_feed_page(body, page_id="page-9")

    assert [p.post_id for p in page.posts] == ["p1", "p2"]
    first = page.posts[0]
    assert (first.page_id, first.feedback_id, first.comment_count) == ("page-9", "feedback-p1", 5)
    assert (first.reaction_count, first.share_count, first.page_name) == (7, 1, "UIT Confessions")
    assert page.next_cursor == "next-1"


def test_feed_page_skips_video_posts_and_counts_them():
    body = fx.feed_response([fx.story("p1"), fx.story("p2", video=True)], end_cursor=None)

    page = parse_feed_page(body, page_id="page-9")

    assert [p.post_id for p in page.posts] == ["p1"]
    assert page.skipped_video_posts == 1
    assert page.next_cursor is None


def test_feed_page_survives_braces_inside_post_text():
    tricky = 'mã {code} và dấu " nháy } lạ {'
    body = fx.feed_response([fx.story("p1", text=tricky)], end_cursor="c")

    page = parse_feed_page(body, page_id="page-9")

    assert page.posts[0].text == tricky


def test_feed_page_reads_multiple_ndjson_data_blocks():
    first = json.dumps({"data": {"node": fx.story("p1")}})
    second = json.dumps({"data": {"node": fx.story("p2")}})

    page = parse_feed_page(first + "\n" + second, page_id="page-9")

    assert [p.post_id for p in page.posts] == ["p1", "p2"]


def test_feed_page_without_data_blocks_is_malformed():
    with pytest.raises(MalformedResponseError):
        parse_feed_page('for (;;);{"error":1}', page_id="page-9")


def test_feed_page_falls_back_to_deep_reaction_count():
    node = fx.story("p1")
    node["feedback"].pop("reaction_count")
    node["somewhere"] = {"deep": {"reactors": {"count_reduced": "4.1K"}}}

    page = parse_feed_page(fx.feed_response([node], None), page_id="p")

    assert page.posts[0].reaction_count == "4.1K"


def test_comments_page_keeps_raw_author_fields_and_cursor():
    body = fx.comments_response([
        fx.comment("c1", text="hay quá", author_id="100099", author_name="Lê Văn C", replies_token="tok-1"),
        fx.comment("c2"),
    ], end_cursor="cc-1")

    page = parse_comments_page(body, post_id="p1")

    assert [c.comment_id for c in page.comments] == ["c1", "c2"]
    first = page.comments[0]
    assert (first.author_id, first.author_name, first.text) == ("100099", "Lê Văn C", "hay quá")
    assert (first.post_id, first.parent_comment_id, first.expansion_token) == ("p1", None, "tok-1")
    assert page.next_cursor == "cc-1"


def test_comments_page_with_no_edges_is_the_last_page():
    page = parse_comments_page(fx.comments_response([], end_cursor=None), post_id="p1")

    assert page.comments == [] and page.next_cursor is None


@pytest.mark.parametrize("body", ["", "<html>login</html>", 'for (;;);{"error":1357001}', "[]"])
def test_comments_page_rejects_unexpected_bodies(body):
    with pytest.raises(MalformedResponseError):
        parse_comments_page(body, post_id="p1")


def test_replies_are_linked_to_parent_comment():
    body = fx.replies_response([fx.comment("r1", text="đồng ý"), fx.comment("r2")])

    replies = parse_replies(body, post_id="p1", parent_comment_id="c1")

    assert [(r.comment_id, r.parent_comment_id, r.text) for r in replies] == [
        ("r1", "c1", "đồng ý"), ("r2", "c1", "bình luận")]


def test_comment_without_id_is_dropped():
    body = fx.comments_response([fx.comment(None), fx.comment("c2")], end_cursor=None)

    page = parse_comments_page(body, post_id="p1")

    assert [c.comment_id for c in page.comments] == ["c2"]


def test_feedback_id_matches_reference_encoding():
    assert feedback_id_for_post("123") == "ZmVlZGJhY2s6MTIz"
