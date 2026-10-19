"""Resolve a Facebook page URL (or numeric id) into the numeric id the GraphQL feed needs."""
import re

from .errors import CrawlerError

_URL_ID_PATTERNS = (r"profile\.php\?id=(\d+)", r"/profile/(\d+)", r"[?&]id=(\d+)")
_HTML_ID_PATTERNS = (r"fb://profile/(\d+)", r'"profile_owner":"(\d+)"', r'"userID":"(\d+)"', r"owner_id=(\d+)")


class PageIdNotFoundError(CrawlerError):
    pass


def _search(patterns, text):
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def resolve_page_id(target, fetch_html):
    """`fetch_html(url)` is only called when the id is not already in the target string."""
    target = target.strip()
    if target.isdigit():
        return target
    found = _search(_URL_ID_PATTERNS, target)
    if found:
        return found
    found = _search(_HTML_ID_PATTERNS, fetch_html(target))
    if found:
        return found
    raise PageIdNotFoundError(
        f"no page id found for {target!r}; pass the numeric id (page may be login-walled)")
