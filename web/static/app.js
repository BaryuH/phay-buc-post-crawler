"use strict";

const PAGE_SIZE = 30;
const SEARCH_DEBOUNCE_MS = 250;
const SKELETON_ROWS = 5;
const TOP_COMMENT_MIN_COMMENTS = 3;
const TOP_COMMENT_MIN_REACTIONS = 2;
const STATUS_LABELS = { PENDING: "Waiting", IN_PROGRESS: "Collecting", FAILED: "Failed" };
const SEARCH_PLACEHOLDERS = {
  posts: "Search posts by text or post ID",
  comments: "Search the text of every comment",
};

const $ = (selector) => document.querySelector(selector);

const state = {
  view: "posts",
  selectedId: null,
  queries: { posts: "", comments: "" },
  posts: { items: [], total: 0, loading: false, error: null, ticket: 0 },
  comments: { items: [], total: 0, loading: false, error: null, ticket: 0, loadedOnce: false },
  detail: null,
};

/* ---------- helpers ---------- */

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

// Builds DOM with textContent only, so crawled text can never inject markup.
function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child !== null && child !== undefined && child !== false) node.append(child);
  }
  return node;
}

async function api(path, params = {}) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === "" || value === null || value === undefined || value === false) continue;
    query.set(key, value === true ? "1" : value);
  }
  const suffix = query.toString() ? `?${query}` : "";
  const response = await fetch(`${path}${suffix}`);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(body.error || `Error ${response.status}`, response.status);
  return body;
}

function debounce(fn, delay) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}

const formatNumber = (value) => Number(value || 0).toLocaleString("en-US");
const plural = (count, one, many) => `${formatNumber(count)} ${count === 1 ? one : many}`;

function formatDate(seconds) {
  if (!seconds) return "an unknown date";
  return new Date(seconds * 1000).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" });
}

// Facebook gives either an exact integer or a localized string such as "4.1K".
function reactionValue(raw) {
  const text = String(raw ?? "0").trim().toUpperCase().replace(",", ".");
  const number = parseFloat(text);
  if (Number.isNaN(number)) return 0;
  if (text.endsWith("K")) return number * 1000;
  if (text.endsWith("M")) return number * 1000000;
  return number;
}

const reactionLabel = (raw) => plural(reactionValue(raw), "reaction", "reactions");

// Only https links become anchors; anything else (javascript:, data:) stays plain text.
const safeUrl = (url) => (typeof url === "string" && /^https:\/\//i.test(url) ? url : null);

/* Accent-insensitive matching, same folding as the server: "toi" finds "Tôi". */
function foldChar(char) {
  const base = char.normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/[đĐ]/g, "d").toLowerCase();
  return base.length === 1 ? base : char;
}

const foldText = (text) => Array.from((text || "").normalize("NFC"), foldChar).join("");

function matches(text, query) {
  return Boolean(query) && foldText(text).includes(foldText(query.trim()));
}

function highlight(text, query) {
  const needle = Array.from((query || "").trim().normalize("NFC"), foldChar);
  if (!needle.length) return [text];
  const chars = Array.from((text || "").normalize("NFC"));
  const folded = chars.map(foldChar);
  const parts = [];
  let last = 0;
  let index = 0;
  while (index <= folded.length - needle.length) {
    if (needle.every((char, offset) => folded[index + offset] === char)) {
      if (index > last) parts.push(chars.slice(last, index).join(""));
      parts.push(el("mark", { text: chars.slice(index, index + needle.length).join("") }));
      index += needle.length;
      last = index;
    } else {
      index += 1;
    }
  }
  if (last < chars.length) parts.push(chars.slice(last).join(""));
  return parts.length ? parts : [text];
}

// First letter of the given name (last word), which suits Vietnamese names.
function initial(name) {
  const word = (name || "").trim().split(/\s+/).pop() || "?";
  return Array.from(word)[0].toUpperCase();
}

function navigate(params) {
  const hash = new URLSearchParams(Object.entries(params).filter(([, value]) => value));
  const next = `#${hash}`;
  if (location.hash === next) applyRoute();
  else location.hash = next;
}

/* ---------- shared pieces ---------- */

const skeletonRows = () =>
  Array.from({ length: SKELETON_ROWS }, () => el("li", {}, el("div", { class: "skeleton row" })));

function emptyNote(title, hint) {
  return el("li", {}, el("div", { class: "note" }, el("strong", { text: title }), hint ? el("span", { text: hint }) : null));
}

function errorNote(message, onRetry) {
  return el("li", {}, el("div", { class: "note error", role: "alert" },
    el("strong", { text: "Oops, something went wrong" }),
    el("span", { text: message }),
    el("div", {}, el("button", { class: "btn", type: "button", text: "Try again", onclick: onRetry }))));
}

function statusBadge(status) {
  if (!STATUS_LABELS[status]) return null;
  return el("span", { class: `badge ${status === "FAILED" ? "error" : ""}`, text: STATUS_LABELS[status] });
}

function authorNode(entry, query) {
  const name = highlight(entry.author_name || "Unknown author", query);
  const link = safeUrl(entry.author_url);
  return link
    ? el("a", { class: "author", href: link, target: "_blank", rel: "noopener noreferrer" }, name)
    : el("span", { class: "author" }, name);
}

function commentBody(entry, query) {
  if (!entry.text) {
    return el("p", { class: "comment-text no-text", text: "(No text, probably a photo or a sticker.)" });
  }
  return el("p", { class: "comment-text" }, highlight(entry.text, query));
}

/* ---------- stats ---------- */

async function loadStats() {
  try {
    const stats = await api("/api/stats");
    const parts = [[stats.posts, "posts"], [stats.comments, "comments"],
      [stats.replies, "replies"], [stats.near_duplicate_comments, "flagged"]];
    const nodes = parts.flatMap(([value, label], index) => [
      index ? " \u00b7 " : "", el("strong", { text: formatNumber(value) }), ` ${label}`]);
    $("#stats").replaceChildren(...nodes);
    return true;
  } catch (error) {
    showDatabaseProblem(error);
    return false;
  }
}

function showDatabaseProblem(error) {
  $("#view-posts").hidden = true;
  $("#view-comments").hidden = true;
  const notice = $("#empty-database");
  notice.hidden = false;
  notice.replaceChildren(
    el("h2", { text: error.status === 503 ? "Nothing to show yet" : "Can't reach the server" }),
    el("p", { text: error.message }),
    el("p", {}, "Collect a few posts with ", el("code", { text: "python -m crawler crawl --limit 5" }),
      ", then refresh this page."));
}

/* ---------- posts list ---------- */

const postFilters = () => ({
  q: state.queries.posts.trim(),
  sort: $("#post-sort").value,
  status: $("#post-status").value,
  min_comments: Number($("#post-min-comments").value) || 0,
});

async function loadPosts({ append = false } = {}) {
  const posts = state.posts;
  const ticket = ++posts.ticket;
  posts.loading = true;
  posts.error = null;
  if (!append) Object.assign(posts, { items: [], total: 0 });
  renderPostList();
  try {
    const result = await api("/api/posts", {
      ...postFilters(), offset: append ? posts.items.length : 0, limit: PAGE_SIZE });
    if (ticket !== posts.ticket) return;
    posts.items = append ? posts.items.concat(result.items) : result.items;
    posts.total = result.total;
  } catch (error) {
    if (ticket !== posts.ticket) return;
    posts.error = error.message;
  }
  posts.loading = false;
  renderPostList();
}

function postRow(post, query) {
  const selected = post.post_id === state.selectedId;
  const snippet = post.snippet + (post.truncated ? "..." : "");
  return el("li", {}, el("button", {
    class: "post-row", type: "button", "aria-current": selected ? "true" : null,
    onclick: () => navigate({ post: post.post_id }),
  },
  el("div", { class: "post-title" }, highlight(snippet, query)),
  el("div", { class: "meta" },
    el("span", { text: formatDate(post.created_at) }),
    el("span", { text: `${formatNumber(post.stored_comments)} of ${plural(post.comment_count, "comment", "comments")}` }),
    el("span", { text: reactionLabel(post.reaction_count) }),
    statusBadge(post.status))));
}

function renderPostList() {
  const { items, total, loading, error } = state.posts;
  const filters = postFilters();
  const list = $("#post-list");
  const nodes = [];

  if (loading && !items.length) nodes.push(...skeletonRows());
  else if (error && !items.length) nodes.push(errorNote(error, () => loadPosts()));
  else if (!items.length) {
    const filtering = filters.q || filters.status || filters.min_comments > 0;
    nodes.push(filtering
      ? emptyNote("No posts match", "Try different words or loosen the filters.")
      : emptyNote("Nothing here yet", "Run a crawl and this page will fill right up."));
  } else {
    nodes.push(...items.map((post) => postRow(post, filters.q)));
    if (items.length < total) {
      nodes.push(el("li", {}, el("button", {
        class: "btn more", type: "button", disabled: loading ? true : null,
        text: loading ? "Loading..." : `Show more (${formatNumber(total - items.length)} left)`,
        onclick: () => loadPosts({ append: true }),
      })));
    }
  }
  list.replaceChildren(...nodes);
  $("#post-count").textContent = items.length ? plural(total, "post", "posts") : "";
  list.querySelector('[aria-current="true"]')?.scrollIntoView({ block: "nearest" });
}

function stepPost(delta) {
  const { items } = state.posts;
  if (!items.length) return;
  const current = items.findIndex((post) => post.post_id === state.selectedId);
  const next = Math.min(items.length - 1, Math.max(0, current === -1 ? 0 : current + delta));
  navigate({ post: items[next].post_id });
}

/* ---------- post detail ---------- */

const newDetail = () => ({ post: null, loading: true, error: null, query: "", hideDuplicates: false,
  sort: "collected", expanded: new Set(), topCommentId: null });

function findTopComment(post) {
  if (post.comments.length < TOP_COMMENT_MIN_COMMENTS) return null;
  let best = null;
  for (const comment of post.comments) {
    if (!best || reactionValue(comment.reaction_count) > reactionValue(best.reaction_count)) best = comment;
  }
  return best && reactionValue(best.reaction_count) >= TOP_COMMENT_MIN_REACTIONS ? best.comment_id : null;
}

async function openPost(postId, commentId) {
  state.selectedId = postId;
  state.detail = newDetail();
  renderPostList();
  renderDetail();
  try {
    const post = await api(`/api/posts/${encodeURIComponent(postId)}`);
    if (state.selectedId !== postId) return;
    state.detail.post = post;
    state.detail.topCommentId = findTopComment(post);
  } catch (error) {
    if (state.selectedId !== postId) return;
    state.detail.error = error.message;
  }
  state.detail.loading = false;
  renderDetail();
  if (commentId && state.detail.post) revealComment(commentId);
}

function closePost() {
  state.selectedId = null;
  state.detail = null;
  renderPostList();
  renderDetail();
}

function counter(value, label) {
  return el("span", { class: "counter" }, el("b", { text: formatNumber(value) }), ` ${label}`);
}

function renderDetail() {
  const pane = $("#post-detail");
  const detail = state.detail;
  $("#view-posts").classList.toggle("has-selection", Boolean(state.selectedId));

  if (!state.selectedId) {
    pane.replaceChildren(el("div", { class: "note" },
      el("strong", { text: "Pick a post" }),
      el("span", { text: "Choose one from the list to read what people said about it." })));
    return;
  }
  if (detail.loading) {
    pane.replaceChildren(el("div", { class: "detail-inner" },
      el("div", { class: "skeleton line" }), el("div", { class: "skeleton block" }),
      el("div", { class: "skeleton line" }), el("div", { class: "skeleton line" })));
    return;
  }
  if (detail.error) {
    pane.replaceChildren(el("ol", { class: "post-list" },
      errorNote(detail.error, () => openPost(state.selectedId))));
    return;
  }

  const post = detail.post;
  const link = safeUrl(post.permalink);
  const search = el("input", {
    type: "search", id: "comment-search", placeholder: "Search this post's comments",
    "aria-label": "Search this post's comments", value: detail.query,
    oninput: (event) => { detail.query = event.target.value; renderComments(); },
  });
  const sort = el("select", { "aria-label": "Sort comments",
    onchange: (event) => { detail.sort = event.target.value; renderComments(); } },
  el("option", { value: "collected", text: "Collection order" }),
  el("option", { value: "reactions", text: "Most reactions" }));
  sort.value = detail.sort;
  const hideDuplicates = el("input", { type: "checkbox",
    onchange: (event) => { detail.hideDuplicates = event.target.checked; renderComments(); } });
  hideDuplicates.checked = detail.hideDuplicates;

  pane.replaceChildren(el("div", { class: "detail-inner" },
    el("button", { class: "btn back", type: "button", text: "Back to posts", onclick: () => navigate({}) }),
    el("div", { class: "module post-box" },
      el("div", { class: "module-head" },
        el("h2", { text: post.page_name || "Post" }),
        el("span", { text: `Post ID ${post.post_id}` })),
      el("div", { class: "post-body" },
        el("div", { class: "post-byline" }, "Posted by ", el("strong", { text: post.page_name || "an unknown page" }),
          ` on ${formatDate(post.created_at)}`),
        el("div", { class: "post-text", text: post.text || "(This post has no text.)" }),
        el("div", { class: "counters" },
          counter(reactionValue(post.reaction_count), "reactions"),
          counter(reactionValue(post.share_count), "shares"),
          el("span", { class: "counter" }, el("b", { text: formatNumber(post.stored_comments) }),
            ` of ${plural(post.comment_count, "comment", "comments")} collected`),
          statusBadge(post.status)),
        link ? el("div", { class: "post-links" },
          el("a", { href: link, target: "_blank", rel: "noopener noreferrer", text: "View the original on Facebook" })) : null)),
    el("div", { class: "module" },
      el("div", { class: "module-head" },
        el("h2", { text: "Comments" }),
        el("span", { text: `${plural(post.comments.length, "thread", "threads")}, ${plural(post.stored_comments, "comment", "comments")} in all` })),
      el("div", { class: "comment-tools" }, search, sort,
        el("label", {}, hideDuplicates, " Hide near-duplicates"),
        el("button", { class: "btn", type: "button", id: "toggle-all" })),
      el("p", { class: "comment-summary", id: "comment-summary", "aria-live": "polite" }),
      el("div", { id: "comment-tree" }))));
  renderComments();
}

function commentView(top, number) {
  const { query, hideDuplicates } = state.detail;
  const shown = top.replies.filter((reply) => !(hideDuplicates && reply.is_near_duplicate));
  const replyMatches = (reply) => matches(reply.text, query) || matches(reply.author_name, query);
  const matchedReplies = query ? shown.filter(replyMatches) : shown;
  const selfMatches = !query || matches(top.text, query) || matches(top.author_name, query);
  if (!selfMatches && !matchedReplies.length) return null;
  return { top, number, replies: selfMatches ? shown : matchedReplies };
}

function visibleComments() {
  const { post, hideDuplicates, sort } = state.detail;
  let views = post.comments
    .map((top, index) => (hideDuplicates && top.is_near_duplicate ? null : commentView(top, index + 1)))
    .filter(Boolean);
  if (sort === "reactions") {
    views = [...views].sort((a, b) => reactionValue(b.top.reaction_count) - reactionValue(a.top.reaction_count));
  }
  return views;
}

function isOpen(entry, replies) {
  const { expanded, query } = state.detail;
  if (expanded.has(entry.comment_id)) return true;
  return Boolean(query) && replies.some((reply) =>
    matches(reply.text, query) || matches(reply.author_name, query));
}

function replyNode(reply, query) {
  return el("li", { class: "reply", "data-comment": reply.comment_id },
    el("div", { class: "avatar", "aria-hidden": "true", text: initial(reply.author_name) }),
    el("div", { class: "comment-main" },
      el("div", { class: "comment-head" },
        authorNode(reply, query),
        reply.author_id ? el("span", { class: "author-id", text: reply.author_id }) : null,
        reply.is_near_duplicate ? el("span", { class: "badge", text: "Near-duplicate" }) : null),
      commentBody(reply, query),
      el("div", { class: "comment-foot" }, el("span", { text: reactionLabel(reply.reaction_count) }))));
}

function commentCard(view, query) {
  const { top, number, replies } = view;
  const open = replies.length > 0 && isOpen(top, replies);
  const isBest = top.comment_id === state.detail.topCommentId;
  return el("article", { class: `comment-card ${isBest ? "is-top" : ""}`, "data-comment": top.comment_id },
    el("div", { class: "avatar", "aria-hidden": "true", text: initial(top.author_name) }),
    el("div", { class: "comment-main" },
      el("div", { class: "comment-head" },
        authorNode(top, query),
        top.author_id ? el("span", { class: "author-id", text: top.author_id }) : null,
        isBest ? el("span", { class: "badge top", text: "Top comment" }) : null,
        top.is_near_duplicate ? el("span", { class: "badge", text: "Near-duplicate" }) : null,
        el("span", { class: "comment-no", text: `#${number}` })),
      commentBody(top, query),
      el("div", { class: "comment-foot" },
        el("span", { text: reactionLabel(top.reaction_count) }),
        replies.length ? el("button", {
          class: "link-button", type: "button", "aria-expanded": String(open),
          text: `${open ? "Hide" : "Show"} ${plural(replies.length, "reply", "replies")}`,
          onclick: () => toggleReplies(top.comment_id),
        }) : null),
      open ? el("ol", { class: "replies" }, replies.map((reply) => replyNode(reply, query))) : null));
}

function toggleReplies(commentId) {
  const { expanded } = state.detail;
  if (expanded.has(commentId)) expanded.delete(commentId);
  else expanded.add(commentId);
  renderComments();
}

function renderComments() {
  const detail = state.detail;
  const tree = $("#comment-tree");
  if (!detail || !detail.post || !tree) return;
  const views = visibleComments();
  const query = detail.query.trim();
  const total = detail.post.comments.length;
  const withReplies = views.filter((view) => view.replies.length);
  const allOpen = withReplies.length > 0 && withReplies.every((view) => isOpen(view.top, view.replies));

  tree.replaceChildren(...(views.length
    ? views.map((view) => commentCard(view, query))
    : [el("div", { class: "note" },
      el("strong", { text: total ? "No comments match" : "No comments collected for this post" }),
      total ? el("span", { text: "Try different words, or show the near-duplicates again." }) : null)]));

  $("#comment-summary").textContent = total
    ? `Showing ${formatNumber(views.length)} of ${plural(total, "thread", "threads")}` : "";
  const toggleAll = $("#toggle-all");
  toggleAll.textContent = allOpen ? "Collapse all replies" : "Expand all replies";
  toggleAll.disabled = withReplies.length === 0;
  toggleAll.onclick = () => {
    for (const view of withReplies) {
      if (allOpen) detail.expanded.delete(view.top.comment_id);
      else detail.expanded.add(view.top.comment_id);
    }
    renderComments();
  };
}

function revealComment(commentId) {
  const detail = state.detail;
  Object.assign(detail, { query: "", hideDuplicates: false });
  for (const top of detail.post.comments) {
    if (top.replies.some((reply) => reply.comment_id === commentId)) detail.expanded.add(top.comment_id);
  }
  renderDetail();
  const target = document.querySelector(`[data-comment="${CSS.escape(commentId)}"]`);
  if (!target) return;
  target.scrollIntoView({ block: "center" });
  target.classList.add("flash");
}

/* ---------- comments search view ---------- */

const commentFilters = () => ({
  q: state.queries.comments.trim(),
  author: $("#comment-author").value.trim(),
  level: $("#comment-level").value,
  duplicates: $("#comment-duplicates").checked,
});

async function loadComments({ append = false } = {}) {
  const comments = state.comments;
  const ticket = ++comments.ticket;
  comments.loading = true;
  comments.error = null;
  comments.loadedOnce = true;
  if (!append) Object.assign(comments, { items: [], total: 0 });
  renderCommentResults();
  try {
    const result = await api("/api/comments", {
      ...commentFilters(), offset: append ? comments.items.length : 0, limit: PAGE_SIZE });
    if (ticket !== comments.ticket) return;
    comments.items = append ? comments.items.concat(result.items) : result.items;
    comments.total = result.total;
  } catch (error) {
    if (ticket !== comments.ticket) return;
    comments.error = error.message;
  }
  comments.loading = false;
  renderCommentResults();
}

function resultCard(entry, filters) {
  return el("li", { class: "result" },
    el("div", { class: "comment-head" },
      authorNode(entry, filters.author),
      entry.author_id ? el("span", { class: "author-id", text: entry.author_id }) : null,
      entry.parent_comment_id ? el("span", { class: "badge", text: "Reply" }) : null,
      entry.is_near_duplicate ? el("span", { class: "badge", text: "Near-duplicate" }) : null,
      el("span", { class: "comment-no", text: reactionLabel(entry.reaction_count) })),
    commentBody(entry, filters.q),
    el("div", { class: "source" },
      el("span", { text: `From post: ${entry.post_snippet}` }),
      el("button", { class: "btn", type: "button", text: "View in post",
        onclick: () => navigate({ post: entry.post_id, comment: entry.comment_id }) })));
}

function renderCommentResults() {
  const { items, total, loading, error } = state.comments;
  const filters = commentFilters();
  const list = $("#comment-results");
  const nodes = [];

  if (loading && !items.length) nodes.push(...skeletonRows());
  else if (error && !items.length) nodes.push(errorNote(error, () => loadComments()));
  else if (!items.length) nodes.push(emptyNote("No comments match", "Try different words or loosen the filters."));
  else {
    nodes.push(...items.map((entry) => resultCard(entry, filters)));
    if (items.length < total) {
      nodes.push(el("li", {}, el("button", {
        class: "btn more", type: "button", disabled: loading ? true : null,
        text: loading ? "Loading..." : `Show more (${formatNumber(total - items.length)} left)`,
        onclick: () => loadComments({ append: true }),
      })));
    }
  }
  list.replaceChildren(...nodes);
  $("#comment-count").textContent = items.length ? plural(total, "comment", "comments") : "";
}

/* ---------- routing, tabs, search, keyboard ---------- */

function showView(view) {
  state.view = view;
  $("#view-posts").hidden = view !== "posts";
  $("#view-comments").hidden = view !== "comments";
  for (const name of ["posts", "comments"]) {
    $(`#tab-${name}`).setAttribute("aria-selected", String(name === view));
  }
  const search = $("#search");
  search.value = state.queries[view];
  search.placeholder = SEARCH_PLACEHOLDERS[view];
  if (view === "comments" && !state.comments.loadedOnce) loadComments();
}

function applyRoute() {
  const route = new URLSearchParams(location.hash.slice(1));
  const view = route.get("view") === "comments" ? "comments" : "posts";
  showView(view);
  if (view !== "posts") return;
  const postId = route.get("post");
  const commentId = route.get("comment");
  if (postId && (postId !== state.selectedId || commentId)) openPost(postId, commentId);
  else if (!postId && state.selectedId) closePost();
}

document.addEventListener("keydown", (event) => {
  const typing = ["INPUT", "SELECT", "TEXTAREA"].includes(event.target.tagName);
  if (event.key === "Escape" && typing) {
    event.target.blur();
    return;
  }
  if (typing || event.metaKey || event.ctrlKey || event.altKey) return;
  if (event.key === "/") {
    event.preventDefault();
    $("#search").focus();
    $("#search").select();
  } else if ((event.key === "j" || event.key === "k") && state.view === "posts") {
    stepPost(event.key === "j" ? 1 : -1);
  }
});

function wireControls() {
  const reloadActive = () => (state.view === "comments" ? loadComments() : loadPosts());
  const reloadSoon = debounce(reloadActive, SEARCH_DEBOUNCE_MS);
  $("#search").addEventListener("input", (event) => {
    state.queries[state.view] = event.target.value;
    reloadSoon();
  });
  $("#search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    reloadActive();
  });
  for (const form of [$("#post-filters"), $("#comment-filters")]) {
    form.addEventListener("submit", (event) => event.preventDefault());
  }
  $("#post-min-comments").addEventListener("input", debounce(() => loadPosts(), SEARCH_DEBOUNCE_MS));
  for (const id of ["#post-sort", "#post-status"]) $(id).addEventListener("change", () => loadPosts());
  $("#comment-author").addEventListener("input", debounce(() => loadComments(), SEARCH_DEBOUNCE_MS));
  $("#comment-level").addEventListener("change", () => loadComments());
  $("#comment-duplicates").addEventListener("change", () => loadComments());
  $("#tab-posts").addEventListener("click", () => navigate(state.selectedId ? { post: state.selectedId } : {}));
  $("#tab-comments").addEventListener("click", () => navigate({ view: "comments" }));
  window.addEventListener("hashchange", applyRoute);
}

async function init() {
  wireControls();
  if (!(await loadStats())) return;
  renderDetail();
  loadPosts();
  applyRoute();
}

init();
