"""In-process fake of the three GraphQL queries the crawler uses."""
import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from tests import fb_fixtures as fx

COMMENTS_PAGE_SIZE = 2
BASE_TIME = 1_700_000_000
POST_SPACING_SECONDS = 100
RATE_LIMIT_BODY = 'for (;;);{"error":1675030,"errorSummary":"rate limited"}'
LOGGED_OUT_BODY = 'for (;;);{"error":1357001,"errorSummary":"not logged in"}'


@dataclass
class FakeComment:
    comment_id: str
    text: str = "một bình luận"
    author_id: str = "1001"
    replies: list = field(default_factory=list)


@dataclass
class FakePost:
    post_id: str
    comments: list = field(default_factory=list)
    text: str = "nội dung confession"


def build_catalog(post_count, comments_per_post=5, replied_comment_indexes=(0, 3), replies_per_comment=2):
    posts = []
    for p in range(post_count):
        comments = []
        for c in range(comments_per_post):
            comment_id = f"c{p}-{c}"
            replies = []
            if c in replied_comment_indexes:
                replies = [FakeComment(f"{comment_id}-r{r}", text=f"trả lời {p}-{c}-{r}")
                           for r in range(replies_per_comment)]
            comments.append(FakeComment(comment_id, text=f"bình luận {p}-{c}", replies=replies))
        posts.append(FakePost(f"p{p}", comments, text=f"confession số {p}"))
    return posts


def expected_counts(posts):
    comments = sum(len(post.comments) for post in posts)
    replies = sum(len(c.replies) for post in posts for c in post.comments)
    return {"posts": len(posts), "comments": comments + replies, "replies": replies}


class FakeFacebook:
    def __init__(self, posts, doc_ids):
        self.posts = posts
        self.doc_ids = doc_ids
        self.requests = []
        self.always_throttle_users = set()
        self.logged_out_users = set()
        self.stop_serving_after = None  # after N total requests, answer everything with a rate limit
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                form = parse_qs(self.rfile.read(length).decode())
                cookie = self.headers.get("Cookie", "")
                user = next((part.split("=")[1] for part in cookie.split("; ") if part.startswith("c_user=")), "?")
                body = outer.respond(form, user)
                payload = body.encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}/api/graphql/"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self):
        self._server.shutdown()
        self._server.server_close()

    def requests_for(self, kind):
        return [r for r in self.requests if r["kind"] == kind]

    def respond(self, form, user):
        doc_id = form["doc_id"][0]
        variables = json.loads(form["variables"][0])
        kind = next(name for name, value in self.doc_ids.items() if value == doc_id)
        with self._lock:
            self.requests.append({"kind": kind, "user": user, "variables": variables})
            total = len(self.requests)
        if user in self.logged_out_users:
            return LOGGED_OUT_BODY
        if user in self.always_throttle_users:
            return RATE_LIMIT_BODY
        if self.stop_serving_after is not None and total > self.stop_serving_after:
            return RATE_LIMIT_BODY
        return getattr(self, f"_serve_{kind}")(variables)

    def _serve_feed(self, variables):
        offset = _offset(variables["cursor"])
        count = variables["count"]
        chunk = self.posts[offset:offset + count]
        stories = [fx.story(post.post_id, text=post.text,
                            created_at=BASE_TIME - (offset + index) * POST_SPACING_SECONDS,
                            comments=len(post.comments))
                   for index, post in enumerate(chunk)]
        more = offset + count < len(self.posts)
        return fx.feed_response(stories, f"o{offset + count}" if more else None)

    def _serve_comments(self, variables):
        post = next(p for p in self.posts if f"feedback-{p.post_id}" == variables["id"])
        offset = _offset(variables["commentsAfterCursor"])
        chunk = post.comments[offset:offset + COMMENTS_PAGE_SIZE]
        nodes = [fx.comment(c.comment_id, text=c.text, author_id=c.author_id,
                            replies_token=f"token-{c.comment_id}" if c.replies else None)
                 for c in chunk]
        more = offset + COMMENTS_PAGE_SIZE < len(post.comments)
        return fx.comments_response(nodes, f"o{offset + COMMENTS_PAGE_SIZE}" if more else None)

    def _serve_replies(self, variables):
        comment_id = variables["id"].removeprefix("feedback-")
        parent = next(c for p in self.posts for c in p.comments if c.comment_id == comment_id)
        return fx.replies_response([fx.comment(r.comment_id, text=r.text, author_id=r.author_id)
                                    for r in parent.replies])


def _offset(cursor):
    return int(cursor[1:]) if cursor else 0
