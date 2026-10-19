"""Builders for response bodies shaped like the ones the parsers read."""
import json


def story(post_id, text="bài viết", created_at=1_700_000_000, comments=2, video=False):
    node = {
        "__typename": "Story",
        "post_id": post_id,
        "creation_time": created_at,
        "feedback": {
            "id": f"feedback-{post_id}",
            "comment_rendering_instance": {"comments": {"total_count": comments}},
            "reaction_count": {"count": 7},
            "share_count": {"count": 1},
        },
        "comet_sections": {
            "content": {"story": {"message": {"text": text},
                                  "actors": [{"name": "UIT Confessions"}]}},
        },
    }
    if video:
        node["attachments"] = [{"media": {"__typename": "Video"}}]
    return node


def feed_response(stories, end_cursor):
    block = {"node": {"__typename": "User", "timeline_list_feed_units": {
        "edges": [{"node": node} for node in stories],
        "page_info": {"end_cursor": end_cursor, "has_next_page": bool(end_cursor)},
    }}}
    return "for (;;);" + json.dumps({"data": block})


def comment(comment_id, text="bình luận", author_id="1001", author_name="Nguyễn Văn A",
            reactions="2", replies_token=None):
    return {
        "legacy_fbid": comment_id,
        "author": {"id": author_id, "name": author_name, "url": f"https://www.facebook.com/{author_id}"},
        "body": {"text": text},
        "feedback": {
            "id": f"feedback-{comment_id}",
            "reactors": {"count_reduced": reactions},
            "expansion_info": {"expansion_token": replies_token},
        },
    }


def comments_response(comments, end_cursor):
    block = {"edges": [{"node": node} for node in comments],
             "page_info": {"end_cursor": end_cursor}}
    document = {"data": {"node": {"comment_rendering_instance_for_feed_location": {"comments": block}}}}
    return "for (;;);" + json.dumps(document)


def replies_response(replies):
    document = {"data": {"node": {"replies_connection": {"edges": [{"node": node} for node in replies]}}}}
    return "for (;;);" + json.dumps(document)
