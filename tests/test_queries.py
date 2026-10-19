import json

from crawler.accounts import Account
from crawler.facebook import queries


def make_account(lsd=None):
    return Account(name="a", cookies={"c_user": "42"}, fb_dtsg="dtsg", proxy=None, impersonate=None, lsd=lsd)


def test_lsd_is_sent_when_the_account_has_one():
    payload = queries.build_comments_payload(make_account(lsd="AVq123"), "doc", queries.CommentsRequest("fb", None))

    assert payload["lsd"] == "AVq123"


def test_lsd_is_omitted_when_the_account_has_none():
    payload = queries.build_feed_payload(make_account(), "doc", queries.FeedRequest("1", None, 10))

    assert "lsd" not in payload


def test_comments_request_carries_cursor_and_session():
    payload = queries.build_comments_payload(make_account(), "doc", queries.CommentsRequest("fb-1", "cur-2"))

    variables = json.loads(payload["variables"])
    assert (variables["id"], variables["commentsAfterCursor"]) == ("fb-1", "cur-2")
    assert (payload["av"], payload["fb_dtsg"], payload["doc_id"]) == ("42", "dtsg", "doc")


def test_feed_request_carries_page_size_and_date_window():
    request = queries.FeedRequest("777", "cur", 10, after_time=100, before_time=200)

    variables = json.loads(queries.build_feed_payload(make_account(), "doc", request)["variables"])

    assert (variables["count"], variables["afterTime"], variables["beforeTime"]) == (10, 100, 200)
    assert (variables["id"], variables["cursor"]) == ("777", "cur")
