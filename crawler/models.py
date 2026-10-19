"""Plain data records moved between parser, orchestrator, and storage."""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Post:
    post_id: str
    page_id: str
    feedback_id: Optional[str]
    created_at: Optional[int]
    text: Optional[str]
    permalink: Optional[str]
    comment_count: int
    reaction_count: object
    share_count: object
    page_name: Optional[str]


@dataclass(frozen=True)
class Comment:
    comment_id: str
    post_id: str
    parent_comment_id: Optional[str]
    author_id: Optional[str]
    author_name: Optional[str]
    author_url: Optional[str]
    text: str
    reaction_count: object
    feedback_id: Optional[str] = None
    expansion_token: Optional[str] = None
    simhash: Optional[str] = None
    is_near_duplicate: bool = False
