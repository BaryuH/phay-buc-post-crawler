"""Write stored data as nested JSONL (one post per line) or flat CSV (one comment/reply per row)."""
import csv
import json
from pathlib import Path

CSV_COLUMNS = (
    "post_id", "post_created_at", "post_text", "comment_id", "parent_comment_id", "author_id",
    "author_name", "author_url", "text", "reaction_count", "is_near_duplicate", "simhash",
)


def _flatten(post):
    """Replies follow their parent; parent_comment_id keeps the tree recoverable."""
    stack = list(reversed(post["comments"]))
    while stack:
        comment = stack.pop()
        yield {
            "post_id": post["post_id"],
            "post_created_at": post["created_at"],
            "post_text": post["text"],
            "comment_id": comment["comment_id"],
            "parent_comment_id": comment["parent_comment_id"],
            "author_id": comment["author_id"],
            "author_name": comment["author_name"],
            "author_url": comment["author_url"],
            "text": comment["text"],
            "reaction_count": comment["reaction_count"],
            "is_near_duplicate": comment["is_near_duplicate"],
            "simhash": comment["simhash"],
        }
        stack.extend(reversed(comment["replies"]))


def write_jsonl(posts, out_path):
    count = 0
    with _open(out_path) as handle:
        for post in posts:
            handle.write(json.dumps(post, ensure_ascii=False) + "\n")
            count += 1
    return count


def write_csv(posts, out_path):
    count = 0
    with _open(out_path) as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for post in posts:
            for row in _flatten(post):
                writer.writerow(row)
                count += 1
    return count


def _open(out_path):
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="" is required by the csv module and harmless for JSONL.
    return open(path, "w", encoding="utf-8", newline="")


WRITERS = {"jsonl": write_jsonl, "csv": write_csv}
