import pytest

from crawler.resolver import PageIdNotFoundError, resolve_page_id


def never_called(url):
    raise AssertionError("HTML must not be fetched when the id is in the target")


@pytest.mark.parametrize("target, expected", [
    ("123456789", "123456789"),
    ("https://www.facebook.com/profile.php?id=100064", "100064"),
    ("https://www.facebook.com/profile/555", "555"),
    ("https://www.facebook.com/page?foo=1&id=777", "777"),
])
def test_id_in_target_needs_no_network(target, expected):
    assert resolve_page_id(target, never_called) == expected


def test_falls_back_to_page_html():
    html = '<script>{"profile_owner":"424242"}</script>'

    assert resolve_page_id("https://www.facebook.com/uit.confessions", lambda url: html) == "424242"


def test_unresolvable_page_raises_with_hint():
    with pytest.raises(PageIdNotFoundError, match="numeric id"):
        resolve_page_id("https://www.facebook.com/private", lambda url: "<html>login</html>")
