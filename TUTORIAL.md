# Phây-búc crawler: Setup Tutorial

This guide takes you from a fresh checkout to a browsable dataset: install, get your login values, find the page ID, crawl, and open the web app. Commands are for Windows; the macOS/Linux differences are noted.

Time needed: about 15 minutes for setup, plus however long the crawl takes.

---

## 1. Install

You need Python 3.10 or newer (the project is tested on 3.14).

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

On macOS/Linux activate with `source .venv/bin/activate` instead, and use `/` in paths.

Optional check that everything works:

```
python -m pytest -q
```

---

## 2. Try it first with no account (demo)

Before touching any real account, run the crawler against a simulated Facebook. Save this as `demo.py` in the repository root and run `python demo.py`:

```python
import json, subprocess, sys, tempfile, pathlib, yaml
sys.path.insert(0, ".")
from tests.fake_facebook import FakeFacebook, build_catalog

work = pathlib.Path(tempfile.mkdtemp())
settings = yaml.safe_load(open("config/settings.yaml", encoding="utf-8"))
server = FakeFacebook(build_catalog(post_count=5), settings["doc_ids"])
settings.update(graphql_url=server.url, database_path=str(work / "demo.db"), log_path=str(work / "demo.log"))
settings["pacing"].update(base_delay_seconds=0, min_delay_seconds=0, max_delay_seconds=0, min_sleep_seconds=0,
                          macro_break_min_seconds=0, macro_break_max_seconds=0)
settings["transport"].update(max_attempts=1, retry_backoff_seconds=0)
settings["pool"]["acquire_timeout_seconds"] = 10
(work / "settings.yaml").write_text(yaml.safe_dump(settings), encoding="utf-8")
(work / "accounts.json").write_text(json.dumps([
    {"name": "demo-1", "cookies": "c_user=1; xs=x", "fb_dtsg": "t"},
    {"name": "demo-2", "cookies": "c_user=2; xs=y", "fb_dtsg": "t"}]), encoding="utf-8")

run = lambda *a: subprocess.run([sys.executable, "-m", "crawler", *a, "--config", str(work / "settings.yaml")], check=True)
run("crawl", "--page", "777", "--accounts", str(work / "accounts.json"))
run("export", "--format", "csv", "--out", str(work / "corpus.csv"))
print("CSV:", work / "corpus.csv"); server.stop()
```

It finishes in a few seconds and prints the totals (5 posts, 45 comments, 20 replies) and the path of a CSV file. If that works, the install is good.

---

## 3. Prepare a secondary account

The crawler logs in as a real Facebook account, using the cookies your browser already has.

- Use a **secondary account**, never your main one. Automated collection can break Facebook's terms and the account may get locked.
- Use a **separate Chrome profile** for each account (Chrome: click your avatar, then *Add*). This keeps their cookies apart.
- Log in to Facebook in that profile and open the page you want to crawl, for example `https://www.facebook.com/UITconfess`.
- When you are done grabbing values, **close the window. Do not log out**: logging out invalidates the cookies.

You can start with one account. More accounts share the work and keep each one's traffic lighter.

---

## 4. Get the cookies and `fb_dtsg`

These two values are what let the crawler act as your logged-in account. Treat them like a password.

1. In the Chrome profile from step 3, press **F12** and open the **Network** tab.
2. Type `graphql` in the **Filter** box. Choose **Fetch/XHR** if you see it.
3. Make Facebook send a request: **scroll the page down** a little. Rows named `graphql` appear. If the list stays empty, keep DevTools open and press **F5**, then scroll again. Check that the record button (top left of the Network tab) is red.
4. Click one `graphql` row. A panel with tabs (**Headers**, **Payload**, **Response**...) opens beside or below the list. If you cannot see the tabs, drag the divider wider or click the `>>` arrow. You can also dock DevTools at the bottom (menu with three dots, then *Dock side*).

**Cookies:** in **Headers**, scroll to **Request Headers**, find the line `cookie:`, right-click it and choose **Copy value**. It looks like `sb=...; datr=...; c_user=1000...; xs=...`. It must contain both `c_user` and `xs`.

**fb_dtsg:** open **Payload**, find `fb_dtsg`, and copy its value. It starts with `NA`. If you see `%3A` in it, click **view decoded** first, or replace every `%3A` with `:` yourself.

**lsd (optional):** in the same **Payload** tab, copy the value of `lsd`. It is only sent if you add it to `accounts.json`.

**Shortcut with Copy as cURL:** right-click a `graphql` row, choose **Copy, then Copy as cURL (bash)**, and paste it into a text editor. The cookies are inside `-b '...'` and `fb_dtsg=` is inside `--data-raw '...'` (up to the next `&`). Pick the *bash* variant: the *cmd* variant adds `^` characters that you would have to remove. Keep the `%3A` inside the cookie's `xs`, but decode it to `:` in `fb_dtsg`.

Never paste a full cURL command into a chat, a ticket, or a public repo. It contains your live login.

---

## 5. Write `config/accounts.json`

```
copy config\accounts.example.json config\accounts.json
```

Fill it in, one entry per account:

```json
[
  {
    "name": "clone-1",
    "cookies": "datr=...; sb=...; c_user=1000...; xs=39%3A...",
    "fb_dtsg": "NAf...:39:1792395281",
    "lsd": "RRMAfrjSV...",
    "impersonate": "chrome",
    "proxy": null
  }
]
```

| Field | Meaning |
| :--- | :--- |
| `name` | Any label. Must be unique. Shows up in logs. |
| `cookies` | The full `cookie:` header value from step 4. |
| `fb_dtsg` | The token from step 4 (decoded, with `:`). |
| `lsd` | Optional. Leave it out if you did not copy it. |
| `impersonate` | Browser fingerprint to mimic. `chrome` is fine. |
| `proxy` | Optional, like `http://user:pass@host:port`. Use `null` for none. |

`config/accounts.json` is git-ignored, so it will not be committed.

---

## 6. Find the page ID

Facebook identifies a page by a number. You can enter a page URL and let the tool look it up, but giving it the number is more reliable.

**Easiest (same request as step 4):** scroll the page until you see a `graphql` request whose **Headers** show `x-fb-friendly-name: ProfileCometTimelineFeedRefetchQuery`. In its **Payload**, open `variables` and read `"id"`. For UIT Confessions that is `100088809545601`.

**Alternative:** open `view-source:https://www.facebook.com/<page>` and search (Ctrl+F) for `"userID"` or `fb://profile/`. This works on many pages but is not guaranteed.

**Or just use the URL:** the crawler tries to find the ID itself. This is untested against the live site and can fail on pages that need a login to view.

---

## 7. Choose the target page

Create a `.env` file in the repository root:

```
TARGET_PAGE=100088809545601
```

`TARGET_PAGE` can be a numeric ID or a page URL. The crawler picks the target in this order: `--page` on the command line, then the `TARGET_PAGE` environment variable, then `.env`. To crawl a different page for one run, pass `--page`. `.env.example` shows the format; the real `.env` is git-ignored.

---

## 8. Check the query IDs (`doc_id`)

Facebook identifies each kind of request by a `doc_id`, and it changes from time to time. They live in `config/settings.yaml`:

```yaml
doc_ids:
  feed: "..."      # ProfileCometTimelineFeedRefetchQuery
  comments: "..."  # CommentsListComponentsPaginationQuery
  replies: "..."   # Depth1CommentsListPaginationQuery
```

If a crawl returns nothing, or posts end up `FAILED`, refresh them. In DevTools, trigger the action, click the request, read the `x-fb-friendly-name` header to see which one it is, and copy `doc_id` from **Payload**:

| To get this ID | Do this on the page |
| :--- | :--- |
| `feed` | Scroll down the page feed |
| `comments` | Open a post and click *View more comments* |
| `replies` | Click *View replies* under a comment |

---

## 9. Run your first crawl

Start small:

```
python -m crawler crawl --limit 1
python -m crawler stats
```

Each request waits about six seconds on purpose, so one post takes a minute or so. `stats` prints how many posts, comments and replies are stored. Data goes to `data/crawler.db` and the log to `logs/crawler_audit.log`.

Then go bigger:

```
python -m crawler crawl --limit 50
python -m crawler crawl
```

Everyday options:

| Command | What it does |
| :--- | :--- |
| `crawl --limit N` | Stop after N new posts. The next run continues from there. |
| `crawl --start-date 2026-01-01 --end-date 2026-03-31` | Only posts in that date range (UTC). |
| `crawl --stop-on-existing 20` | Only new posts: scans from the newest and stops after 20 already-saved posts in a row. |
| `comments` | Only fetch comments for posts already queued, no feed requests. |
| `crawl --retry-failed` | Re-queue posts that failed, then crawl. |
| `crawl --page <id or URL>` | Crawl a different page than `.env`. |

You can stop with Ctrl+C at any time. The next run resumes from the last saved point, with nothing lost and nothing duplicated.

---

## 10. Browse the results in the web app

```
python -m web
```

Open the address it prints (default `http://127.0.0.1:8000`). In the app you can:

- **Posts:** search, sort, filter by status or comment count, and open a post to read the whole thread.
- **Comments:** search every comment by text or author, or show only replies or near-duplicates.
- **In a post:** search its comments, expand or collapse replies, and hide near-duplicates.
- **Keyboard:** `/` focuses search, `j` and `k` move between posts.

Options: `--port 9000`, `--database path\to\other.db`. The app only reads the database and listens on your own computer. If you bind it to another address with `--host`, remember that the data holds real names and IDs.

---

## 11. Export

```
python -m crawler export --out data\export.jsonl
python -m crawler export --out data\corpus.csv --format csv
```

- **JSONL:** one post per line, with its comments and replies nested inside.
- **CSV:** one row per comment or reply, with the post text next to it. Replies come right after their parent and carry `parent_comment_id`.

---

## 12. Good habits

- One secondary account per browser profile. Never share cookies.
- Don't log out of the account in the browser, or its cookies stop working.
- After about 100 requests an account rests for 30 minutes on purpose. With one account, big crawls therefore pause now and then. More accounts keep things moving.
- Cookies and `fb_dtsg` expire eventually. When an account is marked dead, repeat steps 4 and 5 for it.
- Collected data contains real names and IDs. Keep it private and follow your institution's rules before sharing it.

---

## 13. Troubleshooting

| What you see | What to do |
| :--- | :--- |
| `error: no target page: pass --page or set TARGET_PAGE...` | Create `.env` (step 7) or pass `--page`. |
| `error: [Errno 2] No such file or directory: '...accounts.json'` | Create `config/accounts.json` (step 5). |
| `no page id found for '...'` | Use the numeric ID (step 6). |
| Log says an account is `DEAD` or `session rejected` | The cookies expired or you logged out. Redo steps 4 and 5. |
| Log says an account is `resting` | Normal: it hit its request quota or a rate limit. The others carry on. |
| Posts come back with 0 comments though Facebook shows many, or end up `FAILED` | The `comments` or `replies` `doc_id` is out of date. Refresh it (step 8), then `crawl --retry-failed`. |
| Nothing is collected, or errors mention empty data | The `feed` `doc_id` is out of date. Refresh it (step 8). |
| `Facebook error 1675030` in the log | Rate limit. The crawler slows down and rotates accounts by itself. |
| Web app says "Nothing to show yet" | No database yet. Run a crawl first, or pass `--database`. |
| A crawl stops with "no session became available" | Every account is resting or dead. Wait, add accounts, or refresh cookies. |
| `crawl` seems slow | By design: about six seconds between requests. Lower values in `config/settings.yaml` under `pacing` at your own risk. |

The comment count Facebook reports is often higher than what you can collect, because it includes hidden, filtered, or spam comments. That difference is expected.
