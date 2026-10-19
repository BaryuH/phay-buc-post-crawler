"""Field extractors for Facebook Story nodes, ported from the reference scraper.

Facebook moves these fields between response versions, so each extractor tries the
known paths first and then falls back to a bounded recursive search.
"""

MAX_SEARCH_DEPTH = 14

_FEEDBACK_TARGET_PATH = ("comet_sections", "feedback", "story", "story_ufi_container", "story",
                         "feedback_context", "feedback_target_with_context")


def dig(node, *path):
    """Follow dict keys, returning None as soon as a step is missing or not a dict."""
    current = node
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _first_not_none(*values):
    return next((value for value in values if value is not None), None)


def _deep_get(obj, target_key, sub_key, depth=MAX_SEARCH_DEPTH):
    if depth < 0:
        return None
    if isinstance(obj, dict):
        target = obj.get(target_key)
        if isinstance(target, dict) and target.get(sub_key) is not None:
            return target[sub_key]
        children = obj.values()
    elif isinstance(obj, list):
        children = obj
    else:
        return None
    for child in children:
        found = _deep_get(child, target_key, sub_key, depth - 1)
        if found is not None:
            return found
    return None


def _comet_ufi_feedback(node):
    return dig(node, *_FEEDBACK_TARGET_PATH, "comet_ufi_summary_and_actions_renderer", "feedback") or {}


def extract_page_name(node):
    actors = dig(node, "comet_sections", "content", "story", "actors")
    if actors:
        return actors[0].get("name")
    owning_profile = dig(node, "feedback", "owning_profile")
    if owning_profile:
        return owning_profile.get("name") or owning_profile.get("short_name")
    return None


def extract_comment_count(node):
    comet_ufi = _comet_ufi_feedback(node)
    count = _first_not_none(
        dig(node, "feedback", "comment_rendering_instance", "comments", "total_count"),
        dig(node, *_FEEDBACK_TARGET_PATH, "comment_rendering_instance", "comments", "total_count"),
        dig(comet_ufi, "comment_rendering_instance", "comments", "total_count"),
        dig(comet_ufi, "comments_count_summary_renderer", "feedback",
            "comment_rendering_instance", "comments", "total_count"),
        dig(node, "feedback", "comments_count_summary_renderer", "feedback",
            "comment_rendering_instance", "comments", "total_count"),
    )
    return count if count is not None else 0


def extract_reaction_count(node):
    """Exact integer count when present, else the localized string (e.g. "4.1K")."""
    comet_ufi = _comet_ufi_feedback(node)
    feedback = node.get("feedback") or {}
    count = _first_not_none(
        dig(comet_ufi, "reaction_count", "count"),
        dig(feedback, "reaction_count", "count"),
        comet_ufi.get("i18n_reaction_count"),
        feedback.get("i18n_reaction_count"),
        dig(comet_ufi, "reactors", "count_reduced"),
        dig(feedback, "reactors", "count_reduced"),
        _deep_get(node, "reaction_count", "count"),
        _deep_get(node, "reactors", "count_reduced"),
    )
    return count if count is not None else 0


def extract_share_count(node):
    comet_ufi = _comet_ufi_feedback(node)
    feedback = node.get("feedback") or {}
    count = _first_not_none(
        dig(comet_ufi, "share_count", "count"),
        dig(feedback, "share_count", "count"),
        comet_ufi.get("i18n_share_count"),
        feedback.get("i18n_share_count"),
        _deep_get(node, "share_count", "count"),
    )
    return count if count is not None else 0


def _is_video_media(media):
    return media.get("__typename") == "Video" or "reel" in str(media).lower()


def is_reel_or_video_post(node):
    if not node or node.get("__typename") != "Story":
        return False
    for attachment in node.get("attachments") or []:
        media_candidates = [
            attachment.get("media") or {},
            dig(attachment, "styles", "attachment", "media") or {},
        ]
        for sub in dig(attachment, "all_subattachments", "nodes") or []:
            media_candidates.append(sub.get("media") or {})
        if any(_is_video_media(media) for media in media_candidates):
            return True
    return False
