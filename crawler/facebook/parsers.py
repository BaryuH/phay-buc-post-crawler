"""Turn raw GraphQL response text into Post/Comment records and pagination cursors."""
import base64
import json
from dataclasses import dataclass, field
from typing import Optional

from ..errors import CrawlerError
from ..models import Comment, Post
from .extractors import (dig, extract_comment_count, extract_page_name, extract_reaction_count,
                         extract_share_count, is_reel_or_video_post)

JS_HIJACK_PREFIX = "for (;;);"


class MalformedResponseError(CrawlerError):
    """The response parsed but did not have the expected GraphQL structure."""


@dataclass(frozen=True)
class FeedPage:
    posts: list
    next_cursor: Optional[str]
    skipped_video_posts: int = 0


@dataclass(frozen=True)
class CommentsPage:
    comments: list
    next_cursor: Optional[str]


def feedback_id_for_post(post_id):
    return base64.b64encode(f"feedback:{post_id}".encode()).decode()


def _data_blocks(raw_text):
    """Every balanced `{...}` value that follows a "data" key (feed responses are multi-block)."""
    text = raw_text.replace(JS_HIJACK_PREFIX, "").strip()
    blocks = []
    cursor = 0
    while True:
        key_index = text.find('"data"', cursor)
        if key_index == -1:
            return blocks
        start = text.find("{", key_index)
        if start == -1:
            return blocks
        end = _matching_brace(text, start)
        if end is None:
            return blocks
        try:
            blocks.append(json.loads(text[start:end + 1]))
        except ValueError:
            pass
        cursor = end + 1


def _matching_brace(text, start):
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _first_line_document(response_text):
    text = response_text.strip().removeprefix(JS_HIJACK_PREFIX)
    try:
        document = json.loads(text.split("\n", 1)[0].strip())
    except ValueError as error:
        raise MalformedResponseError(f"response is not JSON: {error}") from error
    if not isinstance(document, dict):
        raise MalformedResponseError("response JSON is not an object")
    return document


def _story_nodes(blocks):
    stories = []
    timeline_block = None
    for block in blocks:
        node = block.get("node") if isinstance(block, dict) else None
        if not isinstance(node, dict):
            continue
        if "timeline_list_feed_units" in node:
            timeline_block = node["timeline_list_feed_units"]
            stories.extend(edge["node"] for edge in timeline_block.get("edges") or []
                           if (edge.get("node") or {}).get("__typename") == "Story")
        elif node.get("__typename") == "Story":
            stories.append(node)
    return stories, timeline_block


def _end_cursor(blocks, timeline_block):
    page_info = (timeline_block or {}).get("page_info")
    if not page_info:
        page_info = next((block["page_info"] for block in blocks
                          if isinstance(block, dict) and "page_info" in block), None)
    return (page_info or {}).get("end_cursor")


def _permalink(node, post_id):
    attachments = node.get("attachments") or []
    url = dig(attachments[0], "styles", "attachment", "url") if attachments else None
    return url or f"https://www.facebook.com/{post_id}"


def _to_post(node, page_id):
    post_id = node.get("post_id")
    if not post_id:
        return None
    return Post(
        post_id=str(post_id),
        page_id=page_id,
        feedback_id=dig(node, "feedback", "id"),
        created_at=node.get("creation_time"),
        text=dig(node, "comet_sections", "content", "story", "message", "text"),
        permalink=_permalink(node, post_id),
        comment_count=extract_comment_count(node),
        reaction_count=extract_reaction_count(node),
        share_count=extract_share_count(node),
        page_name=extract_page_name(node),
    )


def parse_feed_page(response_text, page_id):
    blocks = _data_blocks(response_text)
    if not blocks:
        raise MalformedResponseError("feed response has no data blocks")
    stories, timeline_block = _story_nodes(blocks)

    posts = []
    skipped_videos = 0
    for story in stories:
        if is_reel_or_video_post(story):
            skipped_videos += 1
            continue
        post = _to_post(story, page_id)
        if post is not None:
            posts.append(post)
    return FeedPage(posts=posts, next_cursor=_end_cursor(blocks, timeline_block),
                    skipped_video_posts=skipped_videos)


def _to_comment(node, post_id, parent_comment_id):
    comment_id = node.get("legacy_fbid")
    if not comment_id:
        return None
    author = node.get("author") or {}
    feedback = node.get("feedback") or {}
    return Comment(
        comment_id=str(comment_id),
        post_id=post_id,
        parent_comment_id=parent_comment_id,
        author_id=author.get("id"),
        author_name=author.get("name"),
        author_url=author.get("url"),
        text=dig(node, "body", "text") or "",
        reaction_count=dig(feedback, "reactors", "count_reduced") or "0",
        feedback_id=feedback.get("id"),
        expansion_token=dig(feedback, "expansion_info", "expansion_token"),
    )


def parse_comments_page(response_text, post_id):
    document = _first_line_document(response_text)
    node = dig(document, "data", "node")
    if not isinstance(node, dict):
        raise MalformedResponseError("comments response has no data.node")
    block = dig(node, "comment_rendering_instance_for_feed_location", "comments") or {}
    comments = [comment for edge in block.get("edges") or []
                if (comment := _to_comment(edge.get("node") or {}, post_id, None)) is not None]
    return CommentsPage(comments=comments, next_cursor=dig(block, "page_info", "end_cursor"))


def parse_replies(response_text, post_id, parent_comment_id):
    document = _first_line_document(response_text)
    node = dig(document, "data", "node")
    if not isinstance(node, dict):
        raise MalformedResponseError("replies response has no data.node")
    edges = dig(node, "replies_connection", "edges") or []
    return [reply for edge in edges
            if (reply := _to_comment(edge.get("node") or {}, post_id, parent_comment_id)) is not None]
