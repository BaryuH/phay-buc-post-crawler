import csv
import json

from crawler.exporter import CSV_COLUMNS, write_csv, write_jsonl


def nested_post():
    def comment(comment_id, parent, replies=()):
        return {"comment_id": comment_id, "parent_comment_id": parent, "author_id": "u1",
                "author_name": "Nguyễn Văn A", "author_url": "https://x/u1", "text": f"nội dung {comment_id}",
                "reaction_count": "3", "is_near_duplicate": 0, "simhash": None, "replies": list(replies)}

    return {"post_id": "p1", "created_at": 1700000000, "text": "bài viết, có dấu phẩy",
            "comments": [comment("c1", None, [comment("r1", "c1"), comment("r2", "c1")]), comment("c2", None)]}


def test_csv_has_one_row_per_comment_with_replies_after_their_parent(tmp_path):
    out = tmp_path / "nested" / "corpus.csv"

    written = write_csv([nested_post()], out)

    with open(out, encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert written == 4
    assert tuple(rows[0].keys()) == CSV_COLUMNS
    assert [r["comment_id"] for r in rows] == ["c1", "r1", "r2", "c2"]
    assert [r["parent_comment_id"] for r in rows] == ["", "c1", "c1", ""]
    assert rows[0]["post_text"] == "bài viết, có dấu phẩy" and rows[0]["author_name"] == "Nguyễn Văn A"


def test_jsonl_keeps_the_nested_tree_and_unicode(tmp_path):
    out = tmp_path / "corpus.jsonl"

    written = write_jsonl([nested_post()], out)

    line = out.read_text(encoding="utf-8").splitlines()[0]
    assert written == 1 and "Nguyễn Văn A" in line
    assert [r["comment_id"] for r in json.loads(line)["comments"][0]["replies"]] == ["r1", "r2"]
