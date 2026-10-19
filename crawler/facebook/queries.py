"""GraphQL request payload builders (shapes taken from the reference scraper)."""
import json
from dataclasses import dataclass
from typing import Optional

FEED_FRIENDLY_NAME = "ProfileCometTimelineFeedRefetchQuery"
COMMENTS_FRIENDLY_NAME = "CommentsListComponentsPaginationQuery"
REPLIES_FRIENDLY_NAME = "Depth1CommentsListPaginationQuery"

_COMMENT_RELAY_PROVIDERS = {
    "__relay_internal__pv__CometUFICommentAutoTranslationTyperelayprovider": "AUTO_TRANSLATE",
    "__relay_internal__pv__CometUFICommentAvatarStickerAnimatedImagerelayprovider": False,
    "__relay_internal__pv__CometUFICommentActionLinksRewriteEnabledrelayprovider": True,
    "__relay_internal__pv__IsWorkUserrelayprovider": False,
}

_FEED_RELAY_PROVIDERS = {
    "__relay_internal__pv__GHLShouldChangeAdIdFieldNamerelayprovider": True,
    "__relay_internal__pv__GHLShouldChangeSponsoredDataFieldNamerelayprovider": True,
    "__relay_internal__pv__CometFeedStory_enable_reactor_facepilerelayprovider": False,
    "__relay_internal__pv__CometFeedStory_enable_social_bubblesrelayprovider": False,
    "__relay_internal__pv__CometFeedStory_enable_post_permalink_white_space_clickrelayprovider": False,
    "__relay_internal__pv__CometUFICommentActionLinksRewriteEnabledrelayprovider": True,
    "__relay_internal__pv__CometUFICommentAvatarStickerAnimatedImagerelayprovider": False,
    "__relay_internal__pv__IsWorkUserrelayprovider": False,
    "__relay_internal__pv__TestPilotShouldIncludeDemoAdUseCaserelayprovider": False,
    "__relay_internal__pv__FBReels_deprecate_short_form_video_context_gkrelayprovider": True,
    "__relay_internal__pv__FBReels_enable_view_dubbed_audio_type_gkrelayprovider": True,
    "__relay_internal__pv__CometFeedShareMedia_shouldPrefetchShareImagerelayprovider": False,
    "__relay_internal__pv__CometImmersivePhotoCanUserDisable3DMotionrelayprovider": False,
    "__relay_internal__pv__WorkCometIsEmployeeGKProviderrelayprovider": False,
    "__relay_internal__pv__IsMergQAPollsrelayprovider": False,
    "__relay_internal__pv__FBReelsMediaFooter_comet_enable_reels_ads_gkrelayprovider": True,
    "__relay_internal__pv__CometUFIReactionsEnableShortNamerelayprovider": False,
    "__relay_internal__pv__CometUFICommentAutoTranslationTyperelayprovider": "AUTO_TRANSLATE",
    "__relay_internal__pv__CometUFIShareActionMigrationrelayprovider": True,
    "__relay_internal__pv__CometUFISingleLineUFIrelayprovider": True,
    "__relay_internal__pv__relay_provider_comet_ufi_ssr_seo_deferrelayprovider": True,
    "__relay_internal__pv__CometUFI_dedicated_comment_routable_dialog_gkrelayprovider": True,
    "__relay_internal__pv__ReelsIFUCard_reelsIFULikeCountrelayprovider": False,
    "__relay_internal__pv__FBReelsIFUTileContent_reelsIFUPlayOnHoverrelayprovider": True,
    "__relay_internal__pv__StoriesShouldEnablePhotosensitiveContentWarningrelayprovider": False,
    "__relay_internal__pv__ShouldEnableBakedInTextStoriesrelayprovider": False,
    "__relay_internal__pv__StoriesShouldIncludeFbNotesrelayprovider": True,
}


@dataclass(frozen=True)
class FeedRequest:
    page_id: str
    cursor: Optional[str]
    page_size: int
    after_time: Optional[int] = None
    before_time: Optional[int] = None


@dataclass(frozen=True)
class CommentsRequest:
    feedback_id: str
    cursor: Optional[str]


@dataclass(frozen=True)
class RepliesRequest:
    feedback_id: str
    expansion_token: str


def _base_payload(account, doc_id, variables):
    payload = {
        "av": account.user_id,
        "__user": account.user_id,
        "__a": "1",
        "fb_dtsg": account.fb_dtsg,
        "fb_api_caller_class": "RelayModern",
        "server_timestamps": "true",
        "doc_id": doc_id,
        "variables": json.dumps(variables),
    }
    if account.lsd:
        payload["lsd"] = account.lsd
    return payload


def build_feed_payload(account, doc_id, request):
    variables = {
        "afterTime": request.after_time,
        "beforeTime": request.before_time,
        "count": request.page_size,
        "cursor": request.cursor,
        "feedLocation": "TIMELINE",
        "renderLocation": "timeline",
        "scale": 2,
        "id": request.page_id,
        **_FEED_RELAY_PROVIDERS,
    }
    payload = _base_payload(account, doc_id, variables)
    payload.update({
        "__aaid": "0",
        "__comet_req": "15",
        "fb_api_req_friendly_name": FEED_FRIENDLY_NAME,
    })
    return payload


def build_comments_payload(account, doc_id, request):
    variables = {
        "commentsAfterCount": -1,
        "commentsAfterCursor": request.cursor,
        "commentsBeforeCount": None,
        "commentsBeforeCursor": None,
        "commentsIntentToken": None,
        "feedLocation": "POST_PERMALINK_DIALOG",
        "focusCommentID": None,
        "scale": 2,
        "useDefaultActor": False,
        "id": request.feedback_id,
        **_COMMENT_RELAY_PROVIDERS,
    }
    return _base_payload(account, doc_id, variables)


def build_replies_payload(account, doc_id, request):
    variables = {
        "clientKey": None,
        "expansionToken": request.expansion_token,
        "feedLocation": "POST_PERMALINK_DIALOG",
        "focusCommentID": None,
        "scale": 2,
        "useDefaultActor": False,
        "id": request.feedback_id,
        **_COMMENT_RELAY_PROVIDERS,
    }
    return _base_payload(account, doc_id, variables)
