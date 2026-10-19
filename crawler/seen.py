"""Skip-exists index: Bloom filter fast path, SQLite as the exact arbiter."""
from .dedup import BloomFilter


class SeenIndex:
    """A Bloom miss proves the id is new; a Bloom hit is confirmed against SQLite.

    This keeps O(1) rejection of unseen ids while guaranteeing a Bloom false positive
    can never cause a new post or comment to be skipped.
    """

    def __init__(self, storage, dedup_settings):
        self._storage = storage
        self._posts = BloomFilter(dedup_settings.bloom_capacity, dedup_settings.bloom_error_rate)
        self._comments = BloomFilter(dedup_settings.bloom_capacity, dedup_settings.bloom_error_rate)

    def warm_from_storage(self):
        for post_id in self._storage.iter_post_ids():
            self._posts.add(post_id)
        for comment_id in self._storage.iter_comment_ids():
            self._comments.add(comment_id)

    def has_post(self, post_id):
        return post_id in self._posts and self._storage.post_exists(post_id)

    def has_comment(self, comment_id):
        return comment_id in self._comments and self._storage.comment_exists(comment_id)

    def remember_post(self, post_id):
        self._posts.add(post_id)

    def remember_comment(self, comment_id):
        self._comments.add(comment_id)
