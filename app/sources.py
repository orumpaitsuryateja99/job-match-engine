"""
sources.py — fetch job postings from sources that PERMIT it.

  * Greenhouse Job Board API — public, no auth, not rate-limited.
  * Lever Postings API       — public, no auth.
  * Ashby Posting API        — public, no auth.
  * The Muse public jobs API — public, no auth, no key (US-heavy employers).
  * Adzuna API               — free tier, needs app_id/app_key (optional).
  * JSearch / OpenWeb Ninja  — optional structured jobs API, needs api_key.
  * Manual paste             — universal fallback (you paste a JD + link).

We deliberately DO NOT scrape LinkedIn/Indeed/Dice/Handshake/etc. (ToS prohibits
it; most are login-walled or block bots). For those, use the AI-search paste path.

Each function returns a list of normalized job dicts:
  {title, company, location, job_link, source, description}
"""
import html
import json
import math
import os
import re
import time
import concurrent.futures as _cf
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter

try:                                    # urllib3 ships with requests
    from urllib3.util.retry import Retry
except Exception:                       # very old urllib3 fallback
    Retry = None

from textutils import (is_senior_title, extract_years_required, detect_work_mode,
                       detect_us_location)
import roles

HEADERS = {"User-Agent": "JobAutomation/1.0 (personal job-search assistant)"}
TIMEOUT = 12            # per-request seconds (board pulls run in parallel, so keep it tight)

# A shared, pooled, auto-retrying session. Connection reuse + retries on transient
# errors make large board/ATS pulls much faster and more reliable than one-off GETs.
_SESSION = requests.Session()
_SESSION.headers.update(HEADERS)
_adapter_kwargs = {"pool_connections": 32, "pool_maxsize": 32}
if Retry is not None:
    _adapter_kwargs["max_retries"] = Retry(
        total=2, backoff_factor=0.3,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]))
_SESSION.mount("https://", HTTPAdapter(**_adapter_kwargs))
_SESSION.mount("http://", HTTPAdapter(**_adapter_kwargs))

# --- Optional in-session board cache (OFF by default; the Streamlit app turns it on) ---
# A search re-pulls every target board, but board/ATS JSON changes slowly. With caching
# on, repeat searches in a session reuse the raw network response (TTL-bounded) instead
# of re-fetching ~200 boards. FILTERING still runs fresh on every call, so changing the
# freshness window or role focus is applied correctly to the cached raw data. The cache
# is OFF unless enable_board_cache() is called — tests never call it, so stubbed
# _SESSION requests behave exactly as before (no hidden cache hits between cases).
_board_cache_enabled = False
_BOARD_CACHE_TTL = 900.0                 # 15 min
_BOARD_CACHE = {}                        # (url, repr(params), repr(post_json)) -> (expiry, json)


def enable_board_cache(ttl: float = 900.0):
    """Turn on the in-session raw-board cache (the app calls this once at startup)."""
    global _board_cache_enabled, _BOARD_CACHE_TTL
    _board_cache_enabled = True
    _BOARD_CACHE_TTL = float(ttl)


def clear_board_cache():
    """Drop all cached board responses (e.g. to force a truly fresh pull)."""
    _BOARD_CACHE.clear()


def _board_json(url, params=None, post_json=None, headers=None):
    """GET (or POST when post_json is given) a board endpoint → parsed JSON, raising on
    HTTP error. When the cache is enabled, an identical, fresh-enough request is served
    from memory instead of hitting the network. Only the raw NETWORK result is cached;
    callers still apply their own filtering afterward."""
    key = (url, repr(params), repr(post_json))
    now = time.time()
    if _board_cache_enabled:
        hit = _BOARD_CACHE.get(key)
        if hit and hit[0] > now:
            return hit[1]
    if post_json is not None:
        r = _SESSION.post(url, json=post_json, timeout=TIMEOUT,
                          headers=headers or {"Accept": "application/json"})
    else:
        r = _SESSION.get(url, params=params, timeout=TIMEOUT, headers=headers)
    r.raise_for_status()
    data = r.json()
    if _board_cache_enabled:
        _BOARD_CACHE[key] = (now + _BOARD_CACHE_TTL, data)
    return data


# Max years of experience a JD may require and still count as new-grad-friendly.
MAX_YEARS_FOR_NEWGRAD = 2

SWE_HINTS = ("software engineer", "software developer", "sde", "backend",
             "full stack", "full-stack", "software development", "frontend",
             "front end", "front-end")


def _relative_posted_datetime(text: str, now=None):
    """Best-effort parse for posting-age phrases from search snippets."""
    if not text:
        return None
    now = now or datetime.now(timezone.utc)
    t = str(text).lower()
    if re.search(r"\b(just posted|posted just now|just now|today)\b", t):
        return now
    if re.search(r"\byesterday\b", t):
        return now - timedelta(days=1)
    m = re.search(
        r"\b(?:posted\s+)?(\d+)\s*(minute|minutes|min|mins|hour|hours|hr|hrs|day|days|week|weeks|month|months)\s+ago\b",
        t,
    )
    if not m:
        m = re.search(r"\b(\d+)\s*([mhdw])\s+ago\b", t)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2)
    if unit in ("minute", "minutes", "min", "mins", "m"):
        return now - timedelta(minutes=n)
    if unit in ("hour", "hours", "hr", "hrs", "h"):
        return now - timedelta(hours=n)
    if unit in ("day", "days", "d"):
        return now - timedelta(days=n)
    if unit in ("week", "weeks", "w"):
        return now - timedelta(weeks=n)
    if unit in ("month", "months"):
        return now - timedelta(days=30 * n)
    return None


def _absolute_posted_datetime(text: str, now=None):
    """Parse common absolute dates found in search snippets."""
    if not text:
        return None
    now = now or datetime.now(timezone.utc)
    s = str(text).strip()
    m = re.search(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                            tzinfo=timezone.utc)
        except Exception:
            pass
    m = re.search(
        r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|"
        r"dec(?:ember)?)\s+(\d{1,2}),?\s+(20\d{2})\b",
        s,
        re.I,
    )
    if m:
        mon = _MONTHS.get(m.group(1).lower()[:3], 0)
        try:
            return datetime(int(m.group(3)), mon, int(m.group(2)), tzinfo=timezone.utc)
        except Exception:
            return None
    return None


def posted_datetime(ts, now=None):
    """Normalize a posting timestamp/date/relative age into a timezone-aware datetime."""
    now = now or datetime.now(timezone.utc)
    if ts is None or ts == "":
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    rel = _relative_posted_datetime(str(ts), now=now)
    if rel:
        return rel
    if isinstance(ts, (int, float)):
        try:
            sec = ts / 1000.0 if ts > 1e12 else float(ts)
            return datetime.fromtimestamp(sec, tz=timezone.utc)
        except Exception:
            return None
    s = str(ts).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return _absolute_posted_datetime(s, now=now)


def infer_posted_datetime(title: str = "", snippet: str = "", published_date: str = ""):
    """Infer the actual job-posted time from result text, preferring job snippets.

    Search providers may expose their own index/published date, which is not always
    the job posting date. A snippet phrase like "7 days ago" is therefore trusted
    before the provider timestamp.
    """
    now = datetime.now(timezone.utc)
    text_dt = _relative_posted_datetime(f"{title or ''} {snippet or ''}", now=now)
    if text_dt:
        return text_dt
    date_dt = _absolute_posted_datetime(f"{title or ''} {snippet or ''}", now=now)
    if date_dt:
        return date_dt
    return posted_datetime(published_date, now=now)


def _posted_at_str(ts) -> str:
    dt = posted_datetime(ts)
    return dt.isoformat() if dt else ""


def posted_age_hours(ts) -> float:
    """Hours since a posting was published.

    Accepts ISO strings, epoch ms/seconds, date strings, and relative phrases such
    as "3 hours ago", "today", or "7 days ago". Returns None if it can't be parsed.
    """
    if ts is None or ts == "":
        return None
    dt = posted_datetime(ts)
    if not dt:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0


def _posted_date_str(ts) -> str:
    """Human-readable posted date (YYYY-MM-DD) from a timestamp, or ''."""
    dt = posted_datetime(ts)
    return dt.date().isoformat() if dt else ""


def _too_old(ts, max_age_hours) -> bool:
    """True if the posting should be dropped for the chosen freshness window.
    When a window is set and the age can't be determined, we drop it (the user
    explicitly asked for recent postings)."""
    if not max_age_hours:
        return False
    age = posted_age_hours(ts)
    if age is None:
        return True
    return age > max_age_hours


def job_within_freshness(job: dict, max_age_hours) -> bool:
    """Final guard before a discovery job is displayed under a freshness filter."""
    if not max_age_hours:
        return True
    if not isinstance(job, dict):
        return False
    # Direct API sources are already filtered using exact API timestamps before
    # they reach the app. Discovery jobs are search leads, so they need this
    # stricter final validation.
    if not job.get("from_discovery"):
        return True
    posted = (job.get("posted_at") or job.get("posted_date")
              or job.get("published_date") or "")
    if not posted:
        posted_dt = infer_posted_datetime(job.get("title", ""), job.get("description", ""),
                                          job.get("published_date", ""))
        posted = _posted_at_str(posted_dt)
    # Consistent with search_discovery: a discovery job with NO parseable date is
    # KEPT (the search provider's time_range already bounded freshness). Only drop
    # when we have a concrete posting date that is genuinely outside the window —
    # otherwise good leads (Tavily rarely returns dates) silently vanished between
    # "fetched" and "added".
    if not posted:
        return True
    return not _too_old(posted, max_age_hours)


def _strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def safe_url(value: str) -> str:
    """Return a normalized http(s) URL, or empty string for unusable links."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.lower().startswith("www."):
        raw = "https://" + raw
    try:
        parsed = urlparse(urljoin("https://example.com", raw))
    except Exception:
        return ""
    if parsed.scheme not in ("http", "https"):
        return ""
    return parsed.geturl()


def is_search_like_link(url: str) -> bool:
    """True when a URL looks like a board/search/list page, not one posting.

    Ported from the older browser command center. We keep these jobs, but mark
    them so the user knows to find the exact posting before tailoring/applying.
    """
    u = safe_url(url).lower()
    if not u:
        return False
    if re.search(r"[?&]error=", u) or re.search(r"(no[-_]longer|expired|closed)", u):
        return True
    if re.search(r"[?&](q|query|keyword|keywords|search)=", u):
        return True
    if re.search(r"/(search|job-search|position/list|jobsearch)(/|\?|$)", u):
        return True
    if re.search(r"/(careers|jobs|openings|opportunities)/?(\?|#|$)", u):
        return True
    if re.search(r"(boards|job-boards)\.greenhouse\.io/[^/]+/?(\?|#|$)", u):
        return True
    if "greenhouse.io/embed/job_board" in u and "gh_jid=" not in u:
        return True
    if re.search(r"jobs\.lever\.co/[^/]+/?(\?|#|$)", u):
        return True
    if re.search(r"jobs\.ashbyhq\.com/[^/]+/?(\?|#|$)", u):
        return True
    if re.search(r"\.wd\d+\.myworkdayjobs\.com/[^/]*/?(\?|#|$)", u) and "/job/" not in u:
        return True
    if re.search(r"smartrecruiters\.com/[^/]+/?(\?|#|$)", u):
        return True
    return False


def link_quality(job_link: str) -> dict:
    """Small UI-friendly quality report for an application URL."""
    url = safe_url(job_link)
    if not url:
        return {"ok": False, "warning": "Missing or invalid application link."}
    if is_search_like_link(url):
        return {"ok": False, "warning": "Looks like a board/search page, not an exact posting."}
    return {"ok": True, "warning": ""}


# Hosts that are the EMPLOYER's own ATS (an "official" apply link) vs third-party
# aggregators (a real posting may sit one redirect away — verify before applying).
_ATS_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "myworkdayjobs.com", "workday",
              "smartrecruiters.com", "icims.com", "jobvite.com", "workable.com", "bamboohr.com",
              "breezy.hr", "gem.com", "ashbyhq", "eightfold.ai", "oraclecloud.com/hcm")
_THIRD_PARTY_HOSTS = ("linkedin.", "indeed.", "glassdoor.", "ziprecruiter.", "monster.",
                      "dice.com", "lensa.", "jobright.", "builtin.com", "wellfound.",
                      "simplyhired.", "naukri.", "optnation.", "submitx.", "talent.com",
                      "jora.", "getro.", "joinhandshake.")


def classify_link(url: str, live) -> str:
    """Categorize a verified link so the UI can be honest about it:
    live_official | live_third_party | closed | unreachable | needs_check.
    Only 'live_official' / 'live_third_party' are safe to apply to."""
    if live is False:
        return "closed"
    host = ""
    try:
        host = urlparse(safe_url(url)).netloc.lower().replace("www.", "")
    except Exception:
        host = ""
    if not host:
        return "needs_check"
    if live is None:
        return "unreachable"
    if any(h in host for h in _THIRD_PARTY_HOSTS):
        return "live_third_party"
    # an employer ATS or the company's own domain counts as official
    return "live_official"


# Browser-like UA so career sites don't bot-block our verification with a 403.
_BROWSER_UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/122.0 Safari/537.36")}
# Phrases that mean the posting is closed even when the page returns HTTP 200.
_CLOSED_MARKERS = ("no longer accepting", "no longer available", "position has been filled",
                   "this job is closed", "posting is closed", "job is no longer",
                   "this role is no longer", "not found", "404", "page you are looking for")


def verify_link(url: str, timeout: int = 12) -> dict:
    """Actually fetch the URL and judge whether it's a live posting.

    Returns {live, status}: live is True (reachable, not closed),
    False (404/410/400 or closed-marker on page), or None (couldn't confirm —
    bot-blocked 403/429/999, timeout, server error). We only hard-flag clear
    failures so we don't falsely kill links that just block scrapers.
    """
    url = safe_url(url)
    if not url or not url.startswith("http"):
        return {"live": False, "status": "no link"}
    try:
        # stream so we can read just enough body to spot closed-posting markers
        # without downloading multi-MB career pages.
        with _SESSION.get(url, headers=_BROWSER_UA, timeout=timeout,
                          allow_redirects=True, stream=True) as r:
            code = r.status_code
            if code in (400, 404, 410):
                return {"live": False, "status": str(code)}
            if code < 400:
                try:
                    chunk = next(r.iter_content(20000), b"") or b""
                except Exception:
                    chunk = b""
                body = chunk.decode("utf-8", "ignore").lower()
                if any(m in body for m in _CLOSED_MARKERS):
                    return {"live": False, "status": "closed"}
                return {"live": True, "status": str(code)}
        return {"live": None, "status": str(code)}      # 403/429/5xx/999 — unknown
    except Exception:
        return {"live": None, "status": "unreachable"}


def verify_links(jobs: list, max_workers: int = 10) -> list:
    """HTTP-verify each job's link in parallel; annotate link_live + link_status.
    Use for AI-pasted / manual jobs (board pulls already come from live APIs)."""
    real = [j for j in jobs if isinstance(j, dict) and "_error" not in j]
    with _cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
        results = list(ex.map(lambda j: verify_link(j.get("job_link", "")), real))
    for j, res in zip(real, results):
        j["link_live"] = res["live"]
        j["link_status"] = res["status"]
        j["link_class"] = classify_link(j.get("job_link", ""), res["live"])
    return jobs


def passes_entry_level(title: str, content: str = "",
                       max_years: int = MAX_YEARS_FOR_NEWGRAD) -> bool:
    """True if the role is NOT senior-titled and the JD does not demand more than
    `max_years` years. (Does not require a SWE title — used for AI-pasted results
    where the model was already asked for SWE roles.)"""
    if is_senior_title(title):
        return False
    if extract_years_required(content) > max_years:
        return False
    return True


def _is_entry_level_swe(title: str, content: str = "",
                        max_years: int = MAX_YEARS_FOR_NEWGRAD,
                        focus_keys=None) -> bool:
    """Strict gate for BOARD pulls (which return every role): the title must fit
    the chosen role focus (default = broad SWE) AND pass the entry-level check.
    Stops senior/3+yr/non-SWE/off-focus roles leaking in.

    Note: the 'New Grad' focus is a SOFT signal — genuine new-grad roles are tagged
    (`is_new_grad`) and floated to the top in Match & Score, NOT hard-dropped here,
    so the list isn't empty off-season. Use the 'New-grad only' filter to narrow."""
    if not roles.title_matches_focus(title, focus_keys):
        return False
    return passes_entry_level(title, content, max_years)


def job_key(j: dict) -> str:
    """Stable identity for de-duplication: prefer the direct link, else company+title."""
    link = safe_url(j.get("job_link") or "").lower()
    if link:
        return link
    return f"{j.get('company','')}|{j.get('title','')}|{j.get('location','')}".strip().lower()


def dedupe(jobs: list) -> list:
    """Remove duplicate postings (same link, or same company+title+location)."""
    seen, out = set(), []
    for j in jobs:
        if "_error" in j:
            out.append(j)
            continue
        k = job_key(j)
        if k in seen:
            continue
        seen.add(k)
        out.append(j)
    return out


def url_rank(url: str) -> int:
    """Rank an application URL by how 'real' it is, for cross-source dedupe.
    3 = employer ATS / company domain (best apply link)
    2 = third-party board (LinkedIn/Indeed/Glassdoor/Dice/… — a redirect away)
    1 = search/list-like page (not a single posting)
    0 = missing/invalid."""
    u = safe_url(url)
    if not u:
        return 0
    host = ""
    try:
        host = urlparse(u).netloc.lower().replace("www.", "")
    except Exception:
        host = ""
    if any(h in host for h in _THIRD_PARTY_HOSTS):
        return 1 if is_search_like_link(u) else 2
    if is_search_like_link(u):
        return 1
    return 3                       # employer ATS or a company's own careers domain


def _merge_key(j: dict) -> str:
    """Identity for a ROLE across sources (so the same job found on LinkedIn +
    Workday + Google collapses to one): normalized company + title. Location is
    intentionally excluded — the SAME posting carries different location strings
    on different sources ('SF' vs 'San Francisco, CA'), which would defeat the
    merge. The kept entry retains its own location."""
    def norm(s):
        return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()
    return f"{norm(j.get('company'))}|{norm(j.get('title'))}"


def merge_duplicates(jobs: list) -> list:
    """Cross-source de-dupe: collapse the same role found on several sources into
    ONE entry, keeping the best apply link (employer/ATS URL > board URL > search
    URL). Errors pass through untouched. Order is preserved by first appearance."""
    best, order = {}, []
    passthrough = []
    for j in jobs:
        if "_error" in j:
            passthrough.append(j)
            continue
        k = _merge_key(j)
        if k not in best:
            best[k] = j
            order.append(k)
            continue
        # keep whichever has the stronger application link; prefer one WITH a description
        cur = best[k]
        cand_rank = (url_rank(j.get("job_link")), len(j.get("description") or ""))
        cur_rank = (url_rank(cur.get("job_link")), len(cur.get("description") or ""))
        if cand_rank > cur_rank:
            best[k] = j
    return [best[k] for k in order] + passthrough


# ----------------------------- GREENHOUSE -----------------------------
def greenhouse(board_token: str, new_grad_only: bool = True,
               max_years: int = MAX_YEARS_FOR_NEWGRAD, focus_keys=None,
               max_age_hours=None, display_name=None) -> list:
    """board_token is the company's Greenhouse slug, e.g. 'stripe', 'databricks'."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true"
    company = display_name or board_token.replace("-", " ").title()
    out = []
    try:
        for j in _board_json(url).get("jobs", []):
            title = j.get("title", "")
            content = _strip_html(j.get("content", ""))
            if new_grad_only and not _is_entry_level_swe(title, content, max_years, focus_keys):
                continue
            posted = j.get("first_published") or j.get("updated_at")
            if _too_old(posted, max_age_hours):
                continue
            loc = (j.get("location") or {}).get("name", "")
            out.append({
                "title": title,
                "company": company,
                "board_token": board_token,
                "location": loc,
                "job_link": j.get("absolute_url", ""),
                "source": "Greenhouse",
                "description": content,
                "work_mode": detect_work_mode(loc + " " + content),
                "posted_date": _posted_date_str(posted),
            })
    except Exception as e:
        out.append({"_error": f"Greenhouse {board_token}: {e}"})
    return out


# ------------------------------- LEVER --------------------------------
def lever(company: str, new_grad_only: bool = True,
          max_years: int = MAX_YEARS_FOR_NEWGRAD, focus_keys=None,
          max_age_hours=None, display_name=None) -> list:
    """company is the Lever slug, e.g. 'leverdemo', 'plaid'."""
    url = f"https://api.lever.co/v0/postings/{company}?mode=json"
    company_name = display_name or company.replace("-", " ").title()
    out = []
    try:
        for j in _board_json(url):
            title = j.get("text", "")
            content = _strip_html(j.get("descriptionPlain") or j.get("description", ""))
            if new_grad_only and not _is_entry_level_swe(title, content, max_years, focus_keys):
                continue
            posted = j.get("createdAt")
            if _too_old(posted, max_age_hours):
                continue
            cats = j.get("categories", {}) or {}
            loc = cats.get("location", "")
            wp = cats.get("workplaceType", "")  # Lever exposes remote/hybrid/on-site
            out.append({
                "title": title,
                "company": company_name,
                "board_token": company,
                "location": loc,
                "job_link": j.get("hostedUrl", ""),
                "source": "Lever",
                "description": content,
                "work_mode": detect_work_mode(wp + " " + loc + " " + content),
                "posted_date": _posted_date_str(posted),
            })
    except Exception as e:
        out.append({"_error": f"Lever {company}: {e}"})
    return out


# ------------------------------- ASHBY --------------------------------
def ashby(company: str, new_grad_only: bool = True,
          max_years: int = MAX_YEARS_FOR_NEWGRAD, focus_keys=None,
          max_age_hours=None, display_name=None) -> list:
    """company is the Ashby job-board name, e.g. 'ramp', 'openai', 'notion'."""
    url = f"https://api.ashbyhq.com/posting-api/job-board/{company}?includeCompensation=true"
    company_name = display_name or company.replace("-", " ").title()
    out = []
    try:
        for j in _board_json(url).get("jobs", []):
            title = j.get("title", "")
            content = _strip_html(j.get("descriptionPlain") or j.get("descriptionHtml") or "")
            if new_grad_only and not _is_entry_level_swe(title, content, max_years, focus_keys):
                continue
            posted = j.get("publishedAt") or j.get("updatedAt")
            if _too_old(posted, max_age_hours):
                continue
            loc = j.get("location") or ""
            if isinstance(loc, dict):
                loc = loc.get("locationName") or loc.get("name") or ""
            remote = "remote" if j.get("isRemote") else ""
            out.append({
                "title": title,
                "company": company_name,
                "board_token": company,
                "location": loc,
                "job_link": j.get("jobUrl") or j.get("applyUrl", ""),
                "source": "Ashby",
                "description": content,
                "work_mode": detect_work_mode(remote + " " + str(loc) + " " + content),
                "posted_date": _posted_date_str(posted),
            })
    except Exception as e:
        out.append({"_error": f"Ashby {company}: {e}"})
    return out


# ------------------------------- ADZUNA -------------------------------
def adzuna(query: str, app_id: str, app_key: str, country: str = "us",
           results: int = 50, pages: int = 1, new_grad_only: bool = True,
           max_years: int = MAX_YEARS_FOR_NEWGRAD, focus_keys=None,
           max_age_hours=None) -> list:
    """Optional. Free tier requires app_id + app_key from developer.adzuna.com."""
    if not app_id or not app_key:
        return [{"_error": "Adzuna: missing app_id/app_key in settings.yaml"}]
    out, seen = [], set()
    try:
        for page in range(1, max(1, int(pages)) + 1):
            url = (f"https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"
                   f"?app_id={app_id}&app_key={app_key}&results_per_page={results}"
                   f"&what={requests.utils.quote(query)}&content-type=application/json")
            rows = _board_json(url).get("results", [])   # cached when app enables it
            if not rows:
                break
            for j in rows:
                title = j.get("title", "")
                desc = _strip_html(j.get("description", ""))
                if new_grad_only and not _is_entry_level_swe(
                        title, desc, max_years=max_years, focus_keys=focus_keys):
                    continue
                posted = j.get("created")
                if _too_old(posted, max_age_hours):
                    continue
                link = safe_url(j.get("redirect_url", ""))
                key = link.lower() or f"{j.get('company')}|{title}|{j.get('location')}"
                if key in seen:
                    continue
                seen.add(key)
                loc = (j.get("location") or {}).get("display_name", "")
                out.append({
                    "title": title,
                    "company": (j.get("company") or {}).get("display_name", ""),
                    "location": loc,
                    "job_link": link,
                    "source": "Adzuna",
                    "description": desc,
                    "work_mode": detect_work_mode(loc + " " + desc),
                    "posted_date": _posted_date_str(posted),
                })
            if len(rows) < results:
                break
    except Exception as e:
        out.append({"_error": f"Adzuna: {e}"})
    return out


# ------------------------------ JSEARCH -------------------------------
def jsearch_available(cfg: dict, provider: str = "") -> bool:
    """True when JSearch is enabled and an API key is configured.

    Supports OpenWeb Ninja direct keys (`JSEARCH_API_KEY`) and, later, RapidAPI
    keys (`JSEARCH_RAPIDAPI_KEY`) without changing the app surface.
    """
    jcfg = (cfg or {}).get("jsearch", {}) or {}
    if jcfg.get("enabled", True) is False:
        return False
    provider = (provider or jcfg.get("provider") or os.getenv("JSEARCH_PROVIDER")
                or "").lower()
    openweb_key = bool(jcfg.get("api_key") or os.getenv("JSEARCH_API_KEY")
                       or os.getenv("OPENWEBNINJA_API_KEY"))
    rapid_key = bool(jcfg.get("rapidapi_key") or os.getenv("JSEARCH_RAPIDAPI_KEY")
                     or os.getenv("RAPIDAPI_KEY"))
    if provider == "rapidapi":
        return rapid_key
    if provider in ("openweb", "openwebninja", "open_web_ninja"):
        return openweb_key
    return openweb_key or rapid_key


def _jsearch_best_apply_link(row: dict) -> str:
    """Prefer direct employer apply links, then any apply link, then Google link."""
    opts = row.get("apply_options") or row.get("job_apply_options") or []
    if isinstance(opts, list):
        for opt in opts:
            if not isinstance(opt, dict):
                continue
            if opt.get("is_direct") is True:
                link = safe_url(opt.get("apply_link") or opt.get("link") or "")
                if link:
                    return link
        for opt in opts:
            if not isinstance(opt, dict):
                continue
            link = safe_url(opt.get("apply_link") or opt.get("link") or "")
            if link:
                return link
    return safe_url(row.get("job_apply_link") or row.get("job_google_link") or "")


def jsearch(query: str, api_key: str = "", provider: str = "openwebninja",
            country: str = "us", language: str = "en", date_posted: str = "today",
            pages: int = 1, new_grad_only: bool = True,
            max_years: int = MAX_YEARS_FOR_NEWGRAD, focus_keys=None,
            max_age_hours=None, rapidapi_key: str = "") -> list:
    """Pull structured jobs from JSearch and normalize into the app schema.

    OpenWeb Ninja direct endpoint uses `X-API-Key`. RapidAPI can be enabled by
    setting provider="rapidapi" and a RapidAPI key; response shapes are treated
    the same after JSON parsing.
    """
    provider = (provider or "openwebninja").lower()
    api_key = (api_key or os.getenv("JSEARCH_API_KEY")
               or os.getenv("OPENWEBNINJA_API_KEY") or "")
    rapidapi_key = (rapidapi_key or os.getenv("JSEARCH_RAPIDAPI_KEY")
                    or os.getenv("RAPIDAPI_KEY") or "")
    if provider == "rapidapi":
        if not rapidapi_key:
            return [{"_error": "JSearch: missing RapidAPI key"}]
        url = "https://jsearch.p.rapidapi.com/search-v2"
        headers = {
            "X-RapidAPI-Key": rapidapi_key,
            "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
        }
    else:
        if not api_key:
            return [{"_error": "JSearch: missing api_key"}]
        url = "https://api.openwebninja.com/jsearch/search-v2"
        headers = {"X-API-Key": api_key}

    out, seen = [], set()
    cursor = None
    try:
        for _ in range(max(1, int(pages))):
            params = {
                "query": query,
                "country": country or "us",
                "language": language or "en",
            }
            if date_posted:
                params["date_posted"] = date_posted
            if cursor:
                params["cursor"] = cursor
            payload = _board_json(url, params=params, headers=headers) or {}   # cached when app enables it (saves quota on repeat searches)
            # OpenWeb Ninja nests the list under data={"jobs":[...], "cursor": "..."};
            # RapidAPI returns data=[...]. Handle both, and grab the nested cursor.
            _data = payload.get("data")
            _nested_cursor = None
            if isinstance(_data, dict):
                rows = _data.get("jobs") or _data.get("data") or _data.get("results") or []
                _nested_cursor = _data.get("cursor") or _data.get("next_cursor")
            else:
                rows = _data or payload.get("jobs") or payload.get("results") or []
            if not isinstance(rows, list) or not rows:
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                title = row.get("job_title") or row.get("title") or ""
                desc = _strip_html(row.get("job_description") or row.get("description") or "")
                if new_grad_only and not _is_entry_level_swe(
                        title, desc, max_years=max_years, focus_keys=focus_keys):
                    continue
                posted_raw = (row.get("job_posted_at_datetime_utc")
                              or row.get("job_posted_at_timestamp")
                              or row.get("job_posted_at")
                              or row.get("posted_at"))
                if _too_old(posted_raw, max_age_hours):
                    continue
                country_code = (row.get("job_country") or "").strip().upper()
                if country_code and country_code not in ("US", "USA", "UNITED STATES"):
                    continue
                loc = row.get("job_location") or row.get("location") or ""
                link = _jsearch_best_apply_link(row)
                key = link.lower() or f"{row.get('employer_name')}|{title}|{loc}".lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append({
                    "title": title,
                    "company": row.get("employer_name") or row.get("company_name") or "",
                    "location": loc,
                    "job_link": link,
                    "source": "JSearch",
                    "description": desc,
                    "work_mode": detect_work_mode(
                        f"{loc} {row.get('job_is_remote')} {desc}"),
                    "posted_at": _posted_at_str(posted_raw),
                    "posted_date": _posted_date_str(posted_raw),
                    "jd_source": "api",
                })
            cursor = (_nested_cursor or payload.get("cursor") or payload.get("next_cursor")
                      or (payload.get("pagination") or {}).get("next_cursor"))
            if not cursor:
                break
    except Exception as e:
        out.append({"_error": f"JSearch: {e}"})
    return out


# ------------------------------ SERPAPI -------------------------------
def _serpapi_date_chips(max_age_hours) -> str:
    """Google-Jobs `chips=date_posted:…` value for the freshness window, so SerpApi
    filters server-side instead of us paying quota to fetch stale results then drop
    them. Empty when no window is set."""
    if not max_age_hours:
        return ""
    try:
        h = float(max_age_hours)
    except Exception:
        return ""
    if h <= 24:
        return "today"
    if h <= 72:
        return "3days"
    if h <= 24 * 7:
        return "week"
    if h <= 24 * 31:
        return "month"
    return ""                                 # > month: no chip (Google Jobs has no good value)


def serpapi_account(api_key: str) -> dict:
    """SerpApi remaining-quota check via the account endpoint. Returns
    {left, total, used} or {} on any error. Lets the UI warn before exhaustion."""
    if not api_key:
        return {}
    try:
        r = _SESSION.get("https://serpapi.com/account", params={"api_key": api_key}, timeout=TIMEOUT)
        if not r.ok:
            return {}
        d = r.json() or {}
        return {
            "left": d.get("total_searches_left", d.get("plan_searches_left")),
            "total": d.get("searches_per_month", d.get("plan_searches_per_month")),
            "used": d.get("this_month_usage"),
        }
    except Exception:
        return {}


def serpapi_available(cfg: dict) -> bool:
    """True when SerpApi Google Jobs is enabled and an API key is configured."""
    scfg = (cfg or {}).get("serpapi", {}) or {}
    if scfg.get("enabled", True) is False:
        return False
    return bool(scfg.get("api_key") or os.getenv("SERPAPI_API_KEY"))


def _serpapi_best_apply_link(row: dict) -> str:
    """Prefer direct-looking apply options, then any apply option, then share link."""
    opts = row.get("apply_options") or []
    if isinstance(opts, list):
        for opt in opts:
            if not isinstance(opt, dict):
                continue
            title = (opt.get("title") or opt.get("publisher") or "").lower()
            link = safe_url(opt.get("link") or opt.get("apply_link") or "")
            if link and not any(x in title for x in ("linkedin", "indeed", "glassdoor")):
                return link
        for opt in opts:
            if not isinstance(opt, dict):
                continue
            link = safe_url(opt.get("link") or opt.get("apply_link") or "")
            if link:
                return link
    return safe_url(row.get("share_link") or "")


def _serpapi_posted_raw(row: dict):
    det = row.get("detected_extensions") or {}
    if isinstance(det, dict) and det.get("posted_at"):
        return det.get("posted_at")
    for item in row.get("extensions") or []:
        if re.search(r"\b(today|yesterday|\d+\s+(?:minute|hour|day|week|month)s?\s+ago)\b",
                     str(item), re.I):
            return item
    return ""


def serpapi_google_jobs(query: str, api_key: str = "", location: str = "United States",
                        gl: str = "us", hl: str = "en", pages: int = 1,
                        new_grad_only: bool = True,
                        max_years: int = MAX_YEARS_FOR_NEWGRAD, focus_keys=None,
                        max_age_hours=None) -> list:
    """Pull structured Google Jobs results through SerpApi and normalize them."""
    api_key = api_key or os.getenv("SERPAPI_API_KEY") or ""
    if not api_key:
        return [{"_error": "SerpApi: missing api_key"}]
    out, seen = [], set()
    next_page_token = ""
    try:
        for _ in range(max(1, int(pages))):
            params = {
                "engine": "google_jobs",
                "api_key": api_key,
                "q": query,
                "location": location or "United States",
                "gl": gl or "us",
                "hl": hl or "en",
                "google_domain": "google.com",
            }
            _chips = _serpapi_date_chips(max_age_hours)
            if _chips:
                params["chips"] = f"date_posted:{_chips}"   # server-side freshness → saves quota
            if next_page_token:
                params["next_page_token"] = next_page_token
            payload = _board_json("https://serpapi.com/search.json", params=params) or {}  # cached when app enables it (saves quota)
            if payload.get("error"):
                return [{"_error": f"SerpApi: {payload.get('error')}"}]
            rows = payload.get("jobs_results") or []
            if not isinstance(rows, list) or not rows:
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                title = row.get("title") or ""
                desc = _strip_html(row.get("description") or "")
                if new_grad_only and not _is_entry_level_swe(
                        title, desc, max_years=max_years, focus_keys=focus_keys):
                    continue
                posted_raw = _serpapi_posted_raw(row)
                if _too_old(posted_raw, max_age_hours):
                    continue
                loc = row.get("location") or ""
                if detect_us_location(f"{title} {loc} {desc[:500]}") == "foreign":
                    continue
                link = _serpapi_best_apply_link(row)
                key = (row.get("job_id") or link or
                       f"{row.get('company_name')}|{title}|{loc}").lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append({
                    "title": title,
                    "company": row.get("company_name") or "",
                    "location": loc,
                    "job_link": link,
                    "source": "SerpApi Google Jobs",
                    "description": desc,
                    "work_mode": detect_work_mode(f"{loc} {desc}"),
                    "posted_at": _posted_at_str(posted_raw),
                    "posted_date": _posted_date_str(posted_raw),
                    "jd_source": "api",
                })
            next_page_token = (payload.get("serpapi_pagination") or {}).get(
                "next_page_token", "")
            if not next_page_token:
                break
    except Exception as e:
        out.append({"_error": f"SerpApi: {e}"})
    return out


# ------------------------ CAREERJET / JOOBLE (free aggregators) ------------------------
# Both dormant until a key is configured (settings.yaml or env). Same normalization +
# entry-level/US gate as the other providers; jd_source tagged 'api'.
def careerjet_available(cfg: dict) -> bool:
    c = (cfg or {}).get("careerjet", {}) or {}
    return bool(c.get("affid") or os.getenv("CAREERJET_AFFID"))


def careerjet(query: str, affid: str = "", location: str = "United States",
              pages: int = 1, new_grad_only: bool = True,
              max_years: int = MAX_YEARS_FOR_NEWGRAD, focus_keys=None,
              max_age_hours=None) -> list:
    """Careerjet public API (needs a free affiliate id). Aggregates many US boards."""
    affid = affid or os.getenv("CAREERJET_AFFID") or ""
    if not affid:
        return [{"_error": "Careerjet: missing affid"}]
    out, seen = [], set()
    try:
        for page in range(1, max(1, int(pages)) + 1):
            data = _board_json("http://public.api.careerjet.net/search", params={
                "keywords": query, "location": location or "United States", "affid": affid,
                "user_ip": "11.22.33.44", "user_agent": HEADERS["User-Agent"],
                "pagesize": 50, "page": page, "contenttype": "application/json"})
            rows = data.get("jobs") or []
            if not isinstance(rows, list) or not rows:
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                title = row.get("title", "")
                desc = _strip_html(row.get("description", ""))
                if new_grad_only and not _is_entry_level_swe(title, desc, max_years=max_years, focus_keys=focus_keys):
                    continue
                posted = row.get("date")
                if _too_old(posted, max_age_hours):
                    continue
                loc = row.get("locations", "") or location
                if detect_us_location(f"{loc} {desc[:400]}") == "foreign":
                    continue
                link = safe_url(row.get("url", ""))
                key = link.lower() or f"{row.get('company')}|{title}|{loc}".lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append({"title": title, "company": row.get("company", ""), "location": loc,
                            "job_link": link, "source": "Careerjet", "description": desc,
                            "work_mode": detect_work_mode(f"{loc} {desc}"),
                            "posted_date": _posted_date_str(posted), "jd_source": "api"})
            if len(rows) < 50:
                break
    except Exception as e:
        out.append({"_error": f"Careerjet: {e}"})
    return out


def jooble_available(cfg: dict) -> bool:
    j = (cfg or {}).get("jooble", {}) or {}
    return bool(j.get("api_key") or os.getenv("JOOBLE_API_KEY"))


def jooble(query: str, api_key: str = "", location: str = "United States",
           pages: int = 1, new_grad_only: bool = True,
           max_years: int = MAX_YEARS_FOR_NEWGRAD, focus_keys=None,
           max_age_hours=None) -> list:
    """Jooble API (free key). POST keywords+location → aggregated US jobs."""
    api_key = api_key or os.getenv("JOOBLE_API_KEY") or ""
    if not api_key:
        return [{"_error": "Jooble: missing api_key"}]
    out, seen = [], set()
    try:
        for page in range(1, max(1, int(pages)) + 1):
            data = _board_json(f"https://jooble.org/api/{api_key}",
                               post_json={"keywords": query, "location": location or "United States",
                                          "page": page})
            rows = data.get("jobs") or []
            if not isinstance(rows, list) or not rows:
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                title = row.get("title", "")
                desc = _strip_html(row.get("snippet", "") or row.get("description", ""))
                if new_grad_only and not _is_entry_level_swe(title, desc, max_years=max_years, focus_keys=focus_keys):
                    continue
                posted = row.get("updated")
                if _too_old(posted, max_age_hours):
                    continue
                loc = row.get("location", "") or location
                if detect_us_location(f"{loc} {desc[:400]}") == "foreign":
                    continue
                link = safe_url(row.get("link", ""))
                key = link.lower() or f"{row.get('company')}|{title}|{loc}".lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append({"title": title, "company": row.get("company", ""), "location": loc,
                            "job_link": link, "source": "Jooble", "description": desc,
                            "work_mode": detect_work_mode(f"{loc} {desc}"),
                            "posted_date": _posted_date_str(posted), "jd_source": "api"})
    except Exception as e:
        out.append({"_error": f"Jooble: {e}"})
    return out


# --------------------- FREE REMOTE-JOB APIS (no key) ------------------
# Remotive + RemoteOK are public, no-key JSON APIs with FULL job descriptions.
# Remote-skewed (smaller/startup-heavy → fewer big H1B sponsors) but real entry-level
# SWE volume. Every role still passes the SAME entry-level/focus/US gate as boards.
def remotive(new_grad_only: bool = True, max_years: int = MAX_YEARS_FOR_NEWGRAD,
             focus_keys=None, max_age_hours=None) -> list:
    """Remotive public API (https://remotive.com/api/remote-jobs) — no key, full JD."""
    out = []
    try:
        data = _board_json("https://remotive.com/api/remote-jobs",
                           params={"category": "software-dev"})
        for j in data.get("jobs", []) or []:
            title = j.get("title", "")
            desc = _strip_html(j.get("description", ""))
            if new_grad_only and not _is_entry_level_swe(
                    title, desc, max_years=max_years, focus_keys=focus_keys):
                continue
            posted = j.get("publication_date")
            if _too_old(posted, max_age_hours):
                continue
            loc = j.get("candidate_required_location", "") or "Remote"
            if detect_us_location(loc) == "foreign":     # drop EU/India-only remote roles
                continue
            out.append({
                "title": title,
                "company": j.get("company_name", ""),
                "location": loc,
                "job_link": safe_url(j.get("url", "")),
                "source": "Remotive",
                "description": desc,
                "work_mode": "Remote",
                "posted_date": _posted_date_str(posted),
                "jd_source": "api",
            })
    except Exception as e:
        out.append({"_error": f"Remotive: {e}"})
    return out


def remoteok(new_grad_only: bool = True, max_years: int = MAX_YEARS_FOR_NEWGRAD,
             focus_keys=None, max_age_hours=None) -> list:
    """RemoteOK public API (https://remoteok.com/api) — no key, full JD. The first
    list element is a legal/metadata object (no 'position'); the focus gate keeps
    only entry-level SWE out of the broad all-tech feed."""
    out = []
    try:
        data = _board_json("https://remoteok.com/api")
        for j in (data if isinstance(data, list) else []):
            if not isinstance(j, dict) or not j.get("position"):
                continue                                 # skip the legal-notice header item
            title = j.get("position", "")
            desc = _strip_html(j.get("description", ""))
            if new_grad_only and not _is_entry_level_swe(
                    title, desc, max_years=max_years, focus_keys=focus_keys):
                continue
            posted = j.get("date")
            if _too_old(posted, max_age_hours):
                continue
            loc = j.get("location", "") or "Remote"
            if detect_us_location(loc) == "foreign":
                continue
            out.append({
                "title": title,
                "company": j.get("company", ""),
                "location": loc,
                "job_link": safe_url(j.get("url") or j.get("apply_url", "")),
                "source": "RemoteOK",
                "description": desc,
                "work_mode": "Remote",
                "posted_date": _posted_date_str(posted),
                "jd_source": "api",
            })
    except Exception as e:
        out.append({"_error": f"RemoteOK: {e}"})
    return out


# ------------------------------ THE MUSE ------------------------------
# The Muse exposes a PUBLIC jobs API (no key, programmatic use permitted) of
# postings from US-heavy employers — a legal way to reach jobs beyond the three
# ATS boards. Its own "level" tags are unreliable (the Software Engineering
# category leaks non-SWE roles like "Car Wash Attendant"), so we DON'T trust
# them: we pull the category broadly and gate every role through the SAME
# focus + entry-level + freshness filters used for the board pulls, so the rest
# of the app (US filter, H1B DB, scoring) treats Muse roles identically.
_MUSE_URL = "https://www.themuse.com/api/public/jobs"
# Pull several adjacent categories (The Muse files SWE roles under more than just
# "Software Engineering" — e.g. data / platform roles). Every role is still gated
# through the SAME focus + entry-level filters, so non-SWE leakage is dropped.
_MUSE_CATEGORIES = ("Software Engineering", "Data Science", "Data and Analytics",
                    "Engineering")


def themuse(new_grad_only: bool = True, max_years: int = MAX_YEARS_FOR_NEWGRAD,
            focus_keys=None, max_age_hours=None, max_pages: int = 6) -> list:
    """Pull entry-level Software Engineering roles from The Muse's public API across
    a few adjacent categories. No API key required. Returns the standard normalized
    job dicts. Stops early on the first empty/failed page."""
    out = []
    try:
        _muse_params = [("category", c) for c in _MUSE_CATEGORIES]
        for page in range(max(1, max_pages)):
            try:
                data = _board_json(_MUSE_URL, params=_muse_params + [("page", page)])
            except Exception:
                break                         # non-200 / network error → stop paging gracefully
            results = data.get("results", [])
            if not results:
                break
            for j in results:
                title = j.get("name", "").strip()
                content = _strip_html(j.get("contents", ""))
                if new_grad_only and not _is_entry_level_swe(title, content, max_years, focus_keys):
                    continue
                posted = j.get("publication_date")
                if _too_old(posted, max_age_hours):
                    continue
                locs = [l.get("name", "") for l in (j.get("locations") or []) if l.get("name")]
                loc = "; ".join(locs)
                out.append({
                    "title": title,
                    "company": (j.get("company") or {}).get("name", ""),
                    "location": loc,
                    "job_link": (j.get("refs") or {}).get("landing_page", ""),
                    "source": "The Muse",
                    "description": content,
                    "work_mode": detect_work_mode(loc + " " + content),
                    "posted_date": _posted_date_str(posted),
                })
    except Exception as e:
        out.append({"_error": f"The Muse: {e}"})
    return out


# ------------------------------- WORKDAY ------------------------------
# Workday is not one board — each employer runs its own tenant
# (company.wdN.myworkdayjobs.com). We use the PUBLIC CXS JSON endpoints
# (no auth, NOT scraping): POST .../jobs to list, then GET .../{externalPath}
# for the real title / location / startDate / full description / canonical apply
# URL. Configure tenants in config/target_companies.txt as:
#     workday,tenant|wdN|site,Display Name
# e.g. workday,nvidia|wd5|NVIDIAExternalCareerSite,NVIDIA
_WORKDAY_LIST_LIMIT = 20     # Workday CXS HARD-CAPS limit at 20 (400 above). To sample
_WORKDAY_LIST_PAGES = 5      # more of a big tenant we PAGINATE via offset (0,20,40…).
_WORKDAY_DETAIL_CAP = 40     # only fetch full JD for this many title-survivors / tenant


def _focus_search_text(focus_keys) -> str:
    """A short query for ATS search endpoints, derived from the role focus."""
    labels = roles.target_roles_for(focus_keys) if focus_keys else []
    return labels[0] if labels else "software engineer"


def workday(token: str, new_grad_only: bool = True,
            max_years: int = MAX_YEARS_FOR_NEWGRAD, focus_keys=None,
            max_age_hours=None, display_name=None) -> list:
    """token = 'tenant|wdN|site' (e.g. 'nvidia|wd5|NVIDIAExternalCareerSite')."""
    parts = [p.strip() for p in str(token).split("|")]
    if len(parts) != 3 or not all(parts):
        return [{"_error": f"Workday {token}: token must be 'tenant|wdN|site'"}]
    tenant, wd, site = parts
    base = f"https://{tenant}.{wd}.myworkdayjobs.com"
    company = display_name or tenant.title()
    out = []
    try:
        # List postings across a few pages (Workday caps limit at 20, so we page via
        # offset). Stop on the first empty / short / failed page.
        postings = []
        for page in range(_WORKDAY_LIST_PAGES):
            try:
                listing = _board_json(
                    f"{base}/wday/cxs/{tenant}/{site}/jobs",
                    post_json={"appliedFacets": {}, "limit": _WORKDAY_LIST_LIMIT,
                               "offset": page * _WORKDAY_LIST_LIMIT,
                               "searchText": _focus_search_text(focus_keys)})
            except Exception:
                break
            batch = listing.get("jobPostings", []) or []
            postings += batch
            if len(batch) < _WORKDAY_LIST_LIMIT:
                break
        # cheap title pre-filter so we only fetch full JDs for plausible roles
        survivors, seen_ep = [], set()
        for p in postings:
            title = (p.get("title") or "").strip()
            ep = p.get("externalPath") or ""
            if not ep or ep in seen_ep:        # de-dupe overlap across pages
                continue
            seen_ep.add(ep)
            if new_grad_only and (is_senior_title(title)
                                  or not roles.title_matches_focus(title, focus_keys)):
                continue
            survivors.append((title, ep))
            if len(survivors) >= _WORKDAY_DETAIL_CAP:
                break
        # Fetch the survivors' full JDs IN PARALLEL (and board-cached). This used to be
        # a serial loop, so one big tenant's 25 detail GETs bottlenecked the whole pull.
        def _wd_detail(item):
            title, ep = item
            try:
                info = _board_json(f"{base}/wday/cxs/{tenant}/{site}{ep}",
                                   headers={"Accept": "application/json"})
            except Exception:
                return None
            return (title, ep, info.get("jobPostingInfo", {}) or {})

        details = []
        if survivors:
            with _cf.ThreadPoolExecutor(max_workers=min(6, len(survivors))) as ex:
                details = [r for r in ex.map(_wd_detail, survivors) if r]
        for title, ep, info in details:
            rtitle = info.get("title") or title
            content = _strip_html(info.get("jobDescription", ""))
            if new_grad_only and not _is_entry_level_swe(rtitle, content, max_years, focus_keys):
                continue
            posted = info.get("startDate")
            if _too_old(posted, max_age_hours):
                continue
            loc = info.get("location", "")
            out.append({
                "title": rtitle,
                "company": company,
                "location": loc,
                "job_link": info.get("externalUrl") or f"{base}/{site}{ep}",
                "source": "Workday",
                "description": content,
                "work_mode": detect_work_mode(loc + " " + content),
                "posted_date": _posted_date_str(posted),
            })
    except Exception as e:
        out.append({"_error": f"Workday {tenant}: {e}"})
    return out


# --------------------------- SMARTRECRUITERS --------------------------
# Public Posting API (no auth): api.smartrecruiters.com/v1/companies/{id}/postings
# Configure in config/target_companies.txt as:  smartrecruiters,companyId,Display Name
# e.g. smartrecruiters,Visa,Visa
def smartrecruiters(token: str, new_grad_only: bool = True,
                    max_years: int = MAX_YEARS_FOR_NEWGRAD, focus_keys=None,
                    max_age_hours=None, display_name=None) -> list:
    company = display_name or str(token).title()
    base = f"https://api.smartrecruiters.com/v1/companies/{token}/postings"
    out = []
    try:
        listing = _board_json(base, params={"limit": 100, "q": _focus_search_text(focus_keys)})
        survivors = []
        for j in listing.get("content", []) or []:
            title = (j.get("name") or "").strip()
            loc = j.get("location") or {}
            loc_str = ", ".join(x for x in [loc.get("city"), loc.get("region"),
                                            loc.get("country")] if x)
            if new_grad_only and (is_senior_title(title)
                                  or not roles.title_matches_focus(title, focus_keys)):
                continue
            posted = j.get("releasedDate")
            if _too_old(posted, max_age_hours):
                continue
            survivors.append((j.get("id", ""), title, loc_str, posted))
            if len(survivors) >= _WORKDAY_DETAIL_CAP:
                break
        # Fetch survivors' full JDs IN PARALLEL (board-cached) instead of one-by-one.
        def _sr_detail(item):
            jid, title, loc_str, posted = item
            content = ""
            try:                                  # fetch full JD for accurate scoring
                d = _board_json(f"{base}/{jid}")
                secs = ((d.get("jobAd") or {}).get("sections") or {})
                content = _strip_html(" ".join(
                    (secs.get(k) or {}).get("text", "")
                    for k in ("jobDescription", "qualifications", "additionalInformation")))
            except Exception:
                pass
            return (jid, title, loc_str, posted, content)

        detailed = []
        if survivors:
            with _cf.ThreadPoolExecutor(max_workers=min(6, len(survivors))) as ex:
                detailed = list(ex.map(_sr_detail, survivors))
        for jid, title, loc_str, posted, content in detailed:
            if new_grad_only and extract_years_required(content) > max_years:
                continue
            out.append({
                "title": title,
                "company": company,
                "location": loc_str,
                "job_link": f"https://jobs.smartrecruiters.com/{token}/{jid}",
                "source": "SmartRecruiters",
                "description": content,
                "work_mode": detect_work_mode(loc_str + " " + content),
                "posted_date": _posted_date_str(posted),
            })
    except Exception as e:
        out.append({"_error": f"SmartRecruiters {token}: {e}"})
    return out


# ---------------------------- MANUAL PASTE ----------------------------
def manual(title: str, company: str, location: str, job_link: str,
           description: str) -> dict:
    link = safe_url(job_link)
    lq = link_quality(link)
    return {
        "title": title, "company": company, "location": location,
        "job_link": link, "source": "Manual", "description": description,
        "link_warning": lq["warning"], "link_ok": lq["ok"],
    }


def load_targets(path: str) -> list:
    """Parse config/target_companies.txt -> [{'ats','token','name'}, ...]."""
    targets = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 2:
                    targets.append({"ats": parts[0], "token": parts[1],
                                    "name": parts[2] if len(parts) > 2 else parts[1]})
    except FileNotFoundError:
        pass
    return targets


_ATS_FN = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby,
           "workday": workday, "smartrecruiters": smartrecruiters}


def _fetch_one(t, new_grad_only, max_years, focus_keys, max_age_hours):
    """Fetch a single target board (used by the parallel pull). Always returns a
    list; never raises (board funcs already wrap errors as {'_error': ...})."""
    fn = _ATS_FN.get((t.get("ats") or "").lower())
    token = t.get("token")
    if not fn or not token:
        return []
    display = t.get("name") or None
    try:
        return fn(token, new_grad_only, max_years, focus_keys, max_age_hours, display)
    except Exception as e:                       # defensive: keep one bad board from killing the pull
        return [{"_error": f"{t.get('ats')} {token}: {e}"}]


def pull_targets_verbose(target_list, new_grad_only=True,
                         max_years=MAX_YEARS_FOR_NEWGRAD, focus_keys=None,
                         max_age_hours=None, max_workers=24):
    """Fetch all target boards IN PARALLEL. Returns (jobs, errors) so the UI can
    show which company slugs/tenants failed. Parallelism turns a large board pull
    from tens of seconds (sequential) into a few seconds."""
    jobs, errors = [], []
    if not target_list:
        return jobs, errors
    workers = max(1, min(max_workers, len(target_list)))
    with _cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_fetch_one, t, new_grad_only, max_years, focus_keys, max_age_hours)
                   for t in target_list]
        for fut in _cf.as_completed(futures):
            for j in fut.result():
                (errors if "_error" in j else jobs).append(j)
    return dedupe(jobs), [e["_error"] for e in errors]


def pull_targets(target_list: list, new_grad_only: bool = True,
                 max_years: int = MAX_YEARS_FOR_NEWGRAD, focus_keys=None,
                 max_age_hours=None) -> list:
    """Combined, de-duplicated, error-free jobs from all target boards (parallel)."""
    jobs, _ = pull_targets_verbose(target_list, new_grad_only, max_years,
                                   focus_keys, max_age_hours)
    return jobs


# ----------------------- TRACK C: SEARCH DISCOVERY --------------------
# ToS-restricted boards (LinkedIn/Indeed/Glassdoor/Dice/Handshake/…) can't be
# fetched directly. With a SEARCH API key (Tavily or Google Programmable Search,
# set in settings.yaml -> discovery) we run site-targeted queries to find LEADS,
# then prefer the real employer/ATS posting via url_rank during dedupe. With no
# key this stays dormant and those sites are reached via the buttons + AI paste.
# Each query targets the site's INDIVIDUAL-POSTING URL pattern (not its search/
# category pages), so leads are real jobs with a parseable company — not
# "2,000+ jobs in United States" landing pages. The ATS-domain queries (Company
# careers / Google postings) are the highest-yield: they return real Greenhouse/
# Lever/Ashby/Workday postings with the employer baked into the URL.
_DISCOVERY_SITES = {
    "Company careers": ("(site:job-boards.greenhouse.io OR site:greenhouse.io OR site:jobs.lever.co "
                        "OR site:jobs.ashbyhq.com OR site:myworkdayjobs.com OR site:jobs.smartrecruiters.com)"),
    "Google postings": ("(site:greenhouse.io OR site:jobs.lever.co OR site:jobs.ashbyhq.com "
                        "OR site:myworkdayjobs.com)"),
    "LinkedIn Jobs": "site:linkedin.com/jobs/view",
    "Indeed": "site:indeed.com/viewjob",
    "Glassdoor": "site:glassdoor.com/job-listing",
    "Dice": "site:dice.com/job-detail",
    "Built In": "site:builtin.com/job",
    "Wellfound": "site:wellfound.com/jobs",
    "Naukri": "site:naukri.com/job-listings",
    "Jobright AI": "site:jobright.ai/jobs",
    # GROUPED boards: several niche sites OR'd into ONE label each, so adding them
    # costs one site-slot of query budget (6 queries/run) instead of 8+ slots.
    # Yield varies — some of these index few individual postings publicly; the
    # group still surfaces whatever the search engine has at no extra query cost.
    "Startup boards": ("(site:startup.jobs OR site:topstartups.io OR "
                       "site:ycombinator.com/jobs OR site:workatastartup.com)"),
    "H1B/OPT boards": ("(site:myvisajobs.com OR site:optnation.com OR "
                       "site:interstride.com OR site:skillhire.com OR "
                       "site:applyryt.com OR site:scoutbetter.com OR "
                       "site:tickbig.com OR site:submitx.com)"),
    "New-grad aggregators": ("(site:newgrad-jobs.com OR site:jobsfornewgrad.com OR "
                             "site:ripplematch.com OR site:lensa.com)"),
    # Handshake / Hired are login-walled (no public per-posting index) — omitted;
    # reach them via the site buttons + AI-paste path.
}


# Pause between discovery calls so a many-site selection doesn't trip the search
# API's per-second rate limit (which silently returned 0 results before).
_DISCOVERY_THROTTLE_S = 0.85

_SOURCES_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GOOGLE_PSE_USAGE_FILE = os.path.join(_SOURCES_ROOT, "logs", "google_pse_usage.json")
_GOOGLE_PSE_DAILY_LIMIT = 100


def _google_pse_increment():
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        os.makedirs(os.path.dirname(_GOOGLE_PSE_USAGE_FILE), exist_ok=True)
        data = {}
        if os.path.exists(_GOOGLE_PSE_USAGE_FILE):
            with open(_GOOGLE_PSE_USAGE_FILE, encoding="utf-8") as f:
                data = json.load(f)
        if data.get("date") != today:
            data = {"date": today, "used": 0}
        data["used"] = data.get("used", 0) + 1
        with open(_GOOGLE_PSE_USAGE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def google_pse_usage_today() -> dict:
    """Return {"date", "used", "remaining"} for the Google PSE daily quota."""
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        if os.path.exists(_GOOGLE_PSE_USAGE_FILE):
            with open(_GOOGLE_PSE_USAGE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("date") == today:
                used = int(data.get("used", 0))
                return {"date": today, "used": used,
                        "remaining": max(0, _GOOGLE_PSE_DAILY_LIMIT - used)}
    except Exception:
        pass
    return {"date": today, "used": 0, "remaining": _GOOGLE_PSE_DAILY_LIMIT}


def supported_discovery_labels() -> list:
    """Discovery sources we can actually query (have an individual-posting URL
    pattern). The UI should only offer these — login-walled sites like Handshake
    have no public index and would silently return nothing."""
    return list(_DISCOVERY_SITES.keys())


def _discovery_config(cfg: dict) -> dict:
    """Discovery credentials from settings.yaml or local environment variables.

    Env vars:
      SERPAPI_API_KEY (discovery reuses job-api key)
      BRAVE_API_KEY, BING_API_KEY, TAVILY_API_KEY, GOOGLE_API_KEY + GOOGLE_CX
    Provider auto-detected: serpapi → brave → bing → tavily → google_pse
    """
    d = (cfg or {}).get("discovery", {}) or {}
    serpapi_key = (d.get("serpapi_api_key") or os.getenv("SERPAPI_API_KEY")
                   or (cfg or {}).get("serpapi", {}).get("api_key") or "")
    brave_key  = (d.get("brave_api_key")   or os.getenv("BRAVE_API_KEY")   or "")
    bing_key   = (d.get("bing_api_key")    or os.getenv("BING_API_KEY")    or "")
    tavily_key = (d.get("tavily_api_key")  or os.getenv("TAVILY_API_KEY")
                  or os.getenv("JOB_DISCOVERY_TAVILY_API_KEY") or "")
    google_key = (d.get("google_api_key")  or os.getenv("GOOGLE_API_KEY")
                  or os.getenv("JOB_DISCOVERY_GOOGLE_API_KEY") or "")
    google_cx  = (d.get("google_cx")       or os.getenv("GOOGLE_CX")
                  or os.getenv("GOOGLE_PSE_CX") or os.getenv("JOB_DISCOVERY_GOOGLE_CX") or "")
    prov = (d.get("provider") or os.getenv("JOB_DISCOVERY_PROVIDER") or "").lower()
    if not prov:
        if serpapi_key:             prov = "serpapi"
        elif brave_key:             prov = "brave"
        elif bing_key:              prov = "bing"
        elif tavily_key:            prov = "tavily"
        elif google_key and google_cx: prov = "google_pse"
    return {
        "provider": prov,
        "serpapi_api_key": serpapi_key,
        "brave_api_key": brave_key,
        "bing_api_key": bing_key,
        "tavily_api_key": tavily_key,
        "google_api_key": google_key,
        "google_cx": google_cx,
    }


def discovery_available(cfg: dict) -> bool:
    """True only if a search provider + its key are configured."""
    d = _discovery_config(cfg)
    prov = d["provider"]
    if prov == "serpapi":    return bool(d.get("serpapi_api_key"))
    if prov == "brave":      return bool(d.get("brave_api_key"))
    if prov == "bing":       return bool(d.get("bing_api_key"))
    if prov == "tavily":     return bool(d.get("tavily_api_key"))
    if prov in ("google", "google_pse"):
        return bool(d.get("google_api_key") and d.get("google_cx"))
    return False


# Two complementary entry-level "tails". Running BOTH per site (as separate queries)
# surfaces roles the other phrasing ranks lower — different sites/postings use different
# new-grad wording. Tavily handles a quoted OR *tail* fine (unlike a quoted OR on the
# role side, which collapses recall). Year-agnostic on purpose (no stale hardcoded year).
_DISCOVERY_TAILS = (
    '("new grad" OR "new college grad" OR "junior" OR "software engineer I")',
    '("entry level" OR "early career" OR "associate" OR "university graduate" OR "recent graduate")',
    '("software engineer 1" OR "early talent" OR "rotational program" OR "graduate program" OR "campus hire")',
)

_NEW_GRAD_TITLE_QUERIES = (
    "new grad software engineer",
    "entry level software engineer",
    "junior software developer",
    "associate software engineer",
    "software engineer I",
    "new college grad software engineer",
    "university graduate software engineer",
    "entry level software developer",
)

_FOCUS_TITLE_QUERIES = {
    "backend": ("entry level backend software engineer", "junior backend developer"),
    "frontend": ("entry level frontend software engineer", "junior frontend developer"),
    "fullstack": ("entry level full stack software engineer", "junior full stack developer"),
    "mlai": ("entry level machine learning engineer", "junior ai engineer"),
    "data": ("entry level data engineer", "junior data engineer"),
    "mobile": ("entry level mobile software engineer", "junior mobile developer"),
    "devops": ("entry level platform engineer", "junior cloud engineer"),
}

_EXACT_ENTRY_FOCUS_KEYS = {
    "newgrad_swe", "entry_swe", "junior_dev", "associate_swe",
    "swe_i", "new_college_grad_swe",
}


def _clean_title_query(q: str) -> str:
    """Keep discovery title phrases compact and non-senior."""
    q = re.sub(r"\b(intern|internship|senior|staff|principal|lead)\b", " ", q or "",
               flags=re.I)
    return re.sub(r"\s+", " ", q).strip().lower()


def entry_level_title_queries(focus_keys=None, limit: int = 6) -> list:
    """Ordered title phrases for broad, entry-level SWE discovery.

    These are used by both search discovery and aggregators like Adzuna. The first
    phrases are intentionally the user's target lane (new grad / entry level /
    junior developer), then selected focus phrases are added, then role-focus
    target roles fill any remaining room.
    """
    keys = [k for k in (focus_keys or []) if k in roles.ROLE_FOCUS] or ["newgrad"]
    out, seen = [], set()

    def add(q):
        q = _clean_title_query(q)
        if q and q not in seen:
            seen.add(q)
            out.append(q)

    for k in keys:
        if k in _EXACT_ENTRY_FOCUS_KEYS:
            for q in roles.ROLE_FOCUS[k]["target_roles"]:
                add(q)
    if "newgrad" in keys or "general" in keys or (set(keys) & _EXACT_ENTRY_FOCUS_KEYS):
        for q in _NEW_GRAD_TITLE_QUERIES:
            add(q)
    else:
        # Even when a specialization is selected, keep the new-grad lane in front.
        for q in _NEW_GRAD_TITLE_QUERIES[:4]:
            add(q)
    for k in keys:
        for q in _FOCUS_TITLE_QUERIES.get(k, ()):
            add(q)
    for q in roles.target_roles_for(keys):
        add(q)
    add("software engineer")
    add("software developer")
    return out[:max(1, int(limit))]


def build_discovery_queries(focus_keys=None, location: str = "", h1b_only: bool = False,
                            source_labels=None, max_role_variants: int = 3,
                            variants: int = 2) -> list:
    """Return [(source_label, query), ...] of site-targeted discovery searches.

    Discovery V3: instead of one crowded role clause such as
    "software engineer backend frontend full stack", emit several compact
    entry-level title phrases. This improves recall on LinkedIn/Indeed-style
    indexes because each search has one clear intent: "new grad SWE",
    "entry level SWE", "junior software developer", etc. Role phrases stay
    unquoted and non-OR because Tavily's recall is better with plain terms.
    Location is NOT baked in (ATS postings rarely contain 'United States'
    literally — US filtering happens downstream)."""
    role_clauses = entry_level_title_queries(focus_keys, limit=max_role_variants)
    # NB: we deliberately do NOT add an ("H1B" OR "visa sponsorship") clause — almost
    # no posting contains those words verbatim, so it collapsed each site to ~0 results.
    # H1B is enforced downstream against the sponsor DB (h1b_only kept for signature compat).
    tails = _DISCOVERY_TAILS[:max(1, variants)]
    labels = source_labels or list(_DISCOVERY_SITES.keys())
    out = []
    for label in labels:
        site = _DISCOVERY_SITES.get(label)
        if site:
            for role_clause in role_clauses:
                for tail in tails:
                    out.append((label, f'{site} {role_clause} {tail}'))
    return out


def _tavily_recency_params(max_age_hours) -> dict:
    """Best-effort recency hint for Tavily. IMPORTANT: Tavily rejects (HTTP 400)
    a request that sets BOTH time_range and start_date ("When time_range is set,
    start_date or end_date cannot be set"), so we send time_range ONLY. Search
    engines still return stale indexed pages, so we post-filter closed/stale
    snippets below."""
    if not max_age_hours:
        return {}
    try:
        hours = float(max_age_hours)
    except Exception:
        return {}
    if hours <= 24:
        return {"time_range": "day"}
    if hours <= 24 * 7:
        return {"time_range": "week"}
    if hours <= 24 * 31:
        return {"time_range": "month"}
    return {"time_range": "year"}


def _google_date_restrict(max_age_hours) -> str:
    """Google Programmable Search dateRestrict value for a freshness window."""
    if not max_age_hours:
        return ""
    try:
        days = max(1, int(math.ceil(float(max_age_hours) / 24.0)))
    except Exception:
        return ""
    if days <= 31:
        return f"d{days}"
    months = max(1, int(math.ceil(days / 31.0)))
    if months <= 12:
        return f"m{months}"
    return f"y{max(1, int(math.ceil(months / 12.0)))}"


_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def _lead_text_closed_or_stale(title: str, snippet: str = "", published_date: str = "",
                               max_age_hours=None) -> bool:
    """Drop discovery leads that are visibly closed or stale in the search result.

    When max_age_hours is set we trust the search engine's own time_range filter
    (already sent in the Tavily/Google request). We only drop a lead if its date
    IS parseable AND is clearly outside the window — leads with no parseable date
    are kept, because Tavily almost never returns published_date and job-description
    snippets rarely contain "posted X hours ago" text. Dropping unknown-date leads
    would silently collapse discovery to 0 results on any freshness window.
    """
    text = f"{title or ''} {snippet or ''}".lower()
    closed_markers = _CLOSED_MARKERS + (
        "applications are closed", "application closed", "applications closed",
        "expired", "no longer hiring", "closed on", "application deadline passed",
    )
    if any(m in text for m in closed_markers):
        return True
    if max_age_hours:
        posted = infer_posted_datetime(title, snippet, published_date)
        # Only reject when we have a concrete date that's clearly too old.
        # Unknown-date leads pass through — the search engine's time_range already
        # filtered them; our post-filter should not add a second unknown-date veto.
        if posted is not None:
            return (datetime.now(timezone.utc) - posted).total_seconds() / 3600.0 > max_age_hours
        return False
    # Without a freshness filter, only drop visibly old month+year snippets.
    now = datetime.now(timezone.utc)
    for m in re.finditer(
            r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
            r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|"
            r"dec(?:ember)?)\s+(?:\d{1,2},?\s+)?(20\d{2})\b",
            text, re.I):
        mon = _MONTHS.get(m.group(1).lower()[:3], 0)
        year = int(m.group(2))
        if year < now.year or (year == now.year and mon and mon < now.month):
            return True
    return False


def search_discovery(queries: list, cfg: dict, max_results: int = 20,
                     max_age_hours=None) -> list:
    """Run discovery queries through the configured provider. Returns LEAD dicts
    (not full jobs). Returns [] if no provider/key is set. max_results=20 is Tavily's
    'basic' ceiling; search_depth='advanced' returns more relevant individual postings."""
    if not queries or not discovery_available(cfg):
        return []
    # Opt-in cache (same flag the app enables for boards): a repeat discovery search
    # with identical queries+window reuses leads instead of re-spending the search
    # API's quota and re-waiting the throttled multi-query burst. Off in tests.
    _ckey = ("discovery", tuple(q for _, q in queries), int(max_results), max_age_hours)
    if _board_cache_enabled:
        _hit = _BOARD_CACHE.get(_ckey)
        if _hit and _hit[0] > time.time():
            return _hit[1]
    d = _discovery_config(cfg)
    prov = d["provider"]
    leads = []
    seen_links = set()
    _http_err = None     # capture a quota/auth failure so the UI can explain a 0-yield

    def _request(payload_or_params):
        """One provider call with exponential backoff on rate-limits."""
        backoffs = (2.0, 4.0, 7.0)
        r = None
        for i in range(len(backoffs) + 1):
            if prov == "serpapi":
                r = _SESSION.get("https://serpapi.com/search", timeout=TIMEOUT,
                                 params=payload_or_params)
            elif prov == "tavily":
                r = _SESSION.post("https://api.tavily.com/search", timeout=TIMEOUT,
                                  json=payload_or_params)
            elif prov == "brave":
                r = _SESSION.get("https://api.search.brave.com/res/v1/web/search",
                                 timeout=TIMEOUT, params=payload_or_params,
                                 headers={"Accept": "application/json",
                                          "Accept-Encoding": "gzip",
                                          "X-Subscription-Token": d["brave_api_key"]})
            elif prov == "bing":
                r = _SESSION.get("https://api.bing.microsoft.com/v7.0/search",
                                 timeout=TIMEOUT, params=payload_or_params,
                                 headers={"Ocp-Apim-Subscription-Key": d["bing_api_key"]})
            else:
                r = _SESSION.get("https://www.googleapis.com/customsearch/v1",
                                 timeout=TIMEOUT, params=payload_or_params)
            if r.status_code in (429, 503) and i < len(backoffs):
                time.sleep(backoffs[i])
                continue
            return r
        return r

    for idx, (label, q) in enumerate(queries):
        if idx:
            time.sleep(_DISCOVERY_THROTTLE_S)    # throttle so the free tier doesn't 429 the burst
        try:
            if prov == "serpapi":
                params = {"api_key": d["serpapi_api_key"], "engine": "google",
                          "q": q, "num": min(max_results, 10), "gl": "us", "hl": "en"}
                if max_age_hours and max_age_hours <= 24:
                    params["tbs"] = "qdr:d"
                elif max_age_hours and max_age_hours <= 168:
                    params["tbs"] = "qdr:w"
                elif max_age_hours and max_age_hours <= 720:
                    params["tbs"] = "qdr:m"
                r = _request(params)
                if not r.ok and r.status_code in (401, 403, 429):
                    _http_err = {401: "invalid SerpApi key (HTTP 401).",
                                 403: "SerpApi forbidden (HTTP 403).",
                                 429: "SerpApi rate-limited (HTTP 429)."}[r.status_code]
                items = [(it.get("title", ""), it.get("link", ""),
                          it.get("snippet", ""), "")
                         for it in (r.json().get("organic_results", []) if r.ok else [])]
            elif prov == "tavily":
                payload = {
                    "api_key": d["tavily_api_key"], "query": q,
                    "max_results": max_results, "search_depth": "advanced",
                }
                payload.update(_tavily_recency_params(max_age_hours))
                r = _request(payload)
                if not r.ok and r.status_code in (432, 401, 403, 429):
                    _http_err = {432: "out of credits / usage limit (HTTP 432) — your "
                                      "Tavily key is exhausted; reset monthly, upgrade, or "
                                      "use a new key.",
                                 429: "rate-limited (HTTP 429) — too many searches; wait a "
                                      "minute or uncheck some discovery sites.",
                                 401: "invalid API key (HTTP 401).",
                                 403: "forbidden (HTTP 403) — key lacks access."}[r.status_code]
                items = [(it.get("title", ""), it.get("url", ""), it.get("content", ""),
                          it.get("published_date") or it.get("publishedDate") or "")
                         for it in (r.json().get("results", []) if r.ok else [])]
            elif prov == "brave":
                params = {"q": q, "count": min(max_results, 20), "safesearch": "off"}
                if max_age_hours and max_age_hours <= 24:
                    params["freshness"] = "pd"
                elif max_age_hours and max_age_hours <= 168:
                    params["freshness"] = "pw"
                elif max_age_hours and max_age_hours <= 720:
                    params["freshness"] = "pm"
                r = _request(params)
                if not r.ok and r.status_code in (401, 403, 429):
                    _http_err = {401: "invalid Brave API key (HTTP 401).",
                                 403: "Brave key forbidden (HTTP 403).",
                                 429: "Brave rate-limited (HTTP 429) — wait a minute."}[r.status_code]
                items = [(it.get("title", ""), it.get("url", ""),
                          (it.get("description") or ""), "")
                         for it in (r.json().get("web", {}).get("results", []) if r.ok else [])]
            elif prov == "bing":
                params = {"q": q, "count": min(max_results, 50), "mkt": "en-US"}
                if max_age_hours and max_age_hours <= 24:
                    params["freshness"] = "Day"
                elif max_age_hours and max_age_hours <= 168:
                    params["freshness"] = "Week"
                elif max_age_hours and max_age_hours <= 720:
                    params["freshness"] = "Month"
                r = _request(params)
                if not r.ok and r.status_code in (401, 403, 429):
                    _http_err = {401: "invalid Bing API key (HTTP 401).",
                                 403: "Bing key forbidden (HTTP 403).",
                                 429: "Bing rate-limited (HTTP 429)."}[r.status_code]
                items = [(it.get("name", ""), it.get("url", ""),
                          it.get("snippet", ""), "")
                         for it in (r.json().get("webPages", {}).get("value", []) if r.ok else [])]
            else:                                   # google programmable search
                # PSE caps at 10 results/request. discovery.pages (settings.yaml,
                # default 1) paginates via `start` for deeper per-site yield —
                # each extra page costs +1 query of the 100/day budget.
                _pse_pages = 1
                try:
                    _pse_pages = max(1, min(3, int(((cfg or {}).get("discovery", {})
                                                    or {}).get("pages", 1))))
                except Exception:
                    pass
                items = []
                for _pg in range(_pse_pages):
                    params = {"key": d["google_api_key"], "cx": d["google_cx"],
                              "q": q, "num": min(max_results, 10),
                              "start": 1 + 10 * _pg}
                    dr = _google_date_restrict(max_age_hours)
                    if dr:
                        params["dateRestrict"] = dr
                    r = _request(params)
                    _google_pse_increment()
                    page_items = [(it.get("title", ""), it.get("link", ""),
                                   it.get("snippet", ""), "")
                                  for it in (r.json().get("items", []) if r.ok else [])]
                    if not r.ok and r.status_code in (403, 429):
                        _http_err = {403: "Google PSE quota exhausted or key invalid (HTTP 403) — "
                                          "free tier is 100 queries/day, resets midnight PT.",
                                     429: "Google PSE rate-limited (HTTP 429)."}[r.status_code]
                    items.extend(page_items)
                    if len(page_items) < 10:        # no further pages exist
                        break
        except Exception:
            items = []
        for title, link, snippet, published in items:
            link = safe_url(link)
            if link and link.lower() not in seen_links and not _lead_text_closed_or_stale(
                    title, snippet, published, max_age_hours):
                seen_links.add(link.lower())
                posted = infer_posted_datetime(title, snippet, published)
                leads.append({"source": label, "title_guess": title,
                              "company_guess": "", "discovery_url": link,
                              "snippet": snippet, "published_date": published,
                              "posted_at": _posted_at_str(posted),
                              "posted_date": _posted_date_str(posted),
                              "confidence": "lead_only"})
    # Surface a quota/auth failure so a 0-yield isn't silently mistaken for "no jobs".
    if not leads and _http_err:
        return [{"_error": f"Discovery ({prov}): {_http_err}"}]
    # Cache ONLY a non-empty result. A throttled/empty Tavily burst must not be cached,
    # or the next 15 min of searches keep returning 0 even after the throttle clears.
    if _board_cache_enabled and leads:
        _BOARD_CACHE[_ckey] = (time.time() + _BOARD_CACHE_TTL, leads)
    return leads


# A search/category page, not a single posting.
def _is_aggregation_title(t: str) -> bool:
    """True for listing/search-result titles like '37 SWE jobs in United States',
    'Entry Level Software Engineer jobs in New York', 'Best … Jobs in CA',
    '… Jobs, Employment' — these are not a single posting."""
    t = (t or "").strip()
    if re.match(r"^\s*[\d,]+\+?\s+.+\bjobs\b", t, re.I):        # "37 … jobs"
        return True
    if re.search(r"\bjobs\s+(in|near|by|for)\b", t, re.I):     # "… jobs in New York"
        return True
    if re.search(r"\bjobs,\s*employment\b", t, re.I):          # "… Jobs, Employment"
        return True
    if re.match(r"^\s*(best|top)\b.+\bjobs\b", t, re.I):        # "Best … Jobs in CA"
        return True
    return False
# Trailing " - LinkedIn" / " | Greenhouse" / " - Lever" etc. on a result title.
_SITE_SUFFIX_RE = re.compile(
    r"\s*[-|–—]\s*(LinkedIn|Indeed|Glassdoor|Greenhouse|Lever|Ashby(?:HQ)?|SmartRecruiters|"
    r"Dice|Built\s?In|Wellfound|Lensa|Naukri|Handshake|Jobright)\b.*$", re.I)
# Employer slug embedded in an ATS URL.
_ATS_SLUG_RE = re.compile(
    r"greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9][a-z0-9-]+)"
    r"|jobs\.lever\.co/([a-z0-9][a-z0-9-]+)"
    r"|jobs\.ashbyhq\.com/([a-z0-9][a-z0-9-]+)"
    r"|jobs\.smartrecruiters\.com/([a-z0-9][a-z0-9-]+)"
    r"|([a-z0-9][a-z0-9-]+)\.[a-z0-9]+\.myworkdayjobs\.com", re.I)


def _clean_discovery_title(title: str) -> str:
    """Strip site suffixes / 'Job Application for' boilerplate from a result title."""
    t = (title or "").strip()
    t = re.sub(r"^Job Application for\s+", "", t, flags=re.I)
    t = _SITE_SUFFIX_RE.sub("", t)
    return t.strip(" -|–—").strip()


def _looks_like_location(s: str) -> bool:
    """True if a candidate company string is really a location/role fragment."""
    s = s.strip()
    if re.search(r",\s*[A-Za-z]{2}\b", s):              # "San Francisco, CA"
        return True
    if re.search(r"\b(united states|remote|hybrid|onsite)\b", s, re.I):
        return True
    if re.search(r"engineer|developer|intern|new grad|college grad|20\d\d", s, re.I):
        return True
    return False


def lead_company(title: str, url: str) -> str:
    """Best-effort employer name from a discovery result's title or URL.
    Handles 'Company hiring Title', '... at Company', 'Title - Company', and
    falls back to the employer slug in a Greenhouse/Lever/Ashby/Workday URL."""
    t = (title or "").strip()
    m = re.match(r"(.+?)\s+hiring\s+", t, re.I)          # "Notion hiring Software Engineer…"
    if m:
        c = m.group(1).strip(" -–—|")
        if c and not _looks_like_location(c):
            return c
    base = _SITE_SUFFIX_RE.sub("", t).strip()
    m = re.search(r"\bat\s+([A-Z][\w.&'+ ]{1,40}?)\s*$", base)   # "…Engineer at Sigma Computing"
    if m:
        c = m.group(1).strip(" -–—|")
        if c and not _looks_like_location(c):
            return c
    parts = [p.strip() for p in re.split(r"\s[-–—]\s", base) if p.strip()]
    if len(parts) >= 2:                                  # "Peak - Software Engineer (New Grad)"
        for seg in (parts[-1], parts[0]):               # company is the short non-role segment
            if 1 <= len(seg.split()) <= 4 and not _looks_like_location(seg):
                return seg.strip(" -–—|")
    m = _ATS_SLUG_RE.search(url or "")                   # employer slug in the ATS URL
    if m:
        slug = next((g for g in m.groups() if g), "")
        if slug and slug not in ("embed", "job-boards"):
            return slug.replace("-", " ").title()
    return ""


def resolve_lead(lead: dict, new_grad_only: bool = True,
                 max_years: int = MAX_YEARS_FOR_NEWGRAD, focus_keys=None,
                 max_age_hours=None) -> dict:
    """Turn a discovery LEAD into a normalized job dict, preferring the real
    employer/ATS link and extracting the company. Search/category landing pages
    ('N jobs in …') are dropped. Returns None if not usable."""
    url = safe_url(lead.get("discovery_url"))
    raw_title = (lead.get("title_guess") or "").strip()
    if not url or not raw_title:
        return None
    if _lead_text_closed_or_stale(raw_title, lead.get("snippet", ""),
                                  lead.get("published_date", ""), max_age_hours):
        return None
    if _is_aggregation_title(raw_title):     # a list/search page, not a single posting
        return None
    title = _clean_discovery_title(raw_title) or raw_title
    # STRICT role-family gate (was missing for discovery — the leak that let
    # "Semiconductor Quality Assurance Engineer" through). Title must fit the focus.
    if not roles.title_matches_focus(title, focus_keys):
        return None
    if new_grad_only and is_senior_title(title):
        return None
    company = (lead.get("company_guess") or lead_company(raw_title, url)).strip()
    rank = url_rank(url)
    posted = infer_posted_datetime(raw_title, lead.get("snippet", ""),
                                   lead.get("published_date", ""))
    return {
        "title": title,
        "company": company,
        "location": "",
        "job_link": url,
        "source": lead.get("source") or "Discovery",
        "description": lead.get("snippet") or "",
        "work_mode": detect_work_mode(title + " " + (lead.get("snippet") or "")),
        "posted_at": lead.get("posted_at") or _posted_at_str(posted),
        "posted_date": lead.get("posted_date") or _posted_date_str(posted),
        "link_warning": rank < 3,          # not an employer/ATS link → verify exact posting
        "from_discovery": True,
    }


# ------------------- DISCOVERY V2: FULL-JD ENRICHMENT -----------------
# A discovery lead is only a search snippet. When its link points at an official
# Greenhouse / Lever / Ashby board we can fetch the REAL posting via the same
# public API Path A uses — upgrading the snippet to a full JD and the link to the
# canonical apply URL. Leads that only have a third-party (LinkedIn/Indeed/…) link
# are flagged `needs_verification` and keep their snippet (no scraping).
_GH_EMBED_RE = re.compile(r"greenhouse\.io/embed/job_board\?for=([a-z0-9][a-z0-9_-]+)", re.I)
_GH_API_RE   = re.compile(r"boards-api\.greenhouse\.io/v1/boards/([a-z0-9][a-z0-9_-]+)", re.I)
_GH_SLUG_RE  = re.compile(r"(?:job-boards|boards)\.greenhouse\.io/([a-z0-9][a-z0-9_-]+)", re.I)
_LEVER_SLUG_RE = re.compile(r"jobs\.lever\.co/([a-z0-9][a-z0-9_-]+)", re.I)
_ASHBY_SLUG_RE = re.compile(r"jobs\.ashbyhq\.com/([a-z0-9][a-z0-9_-]+)", re.I)
_SLUG_STOPWORDS = {"embed", "job-boards", "boards", "v1", "jobs"}


# Workday: tenant.wdN.myworkday{jobs,site}.com/[lang/]{site}/job/...  → token tenant|wdN|site
_WORKDAY_URL_RE = re.compile(
    r"https?://([a-z0-9][a-z0-9-]*)\.(wd\d+)\.myworkday(?:jobs|site)\.com/"
    r"(?:[a-z]{2}-[a-z]{2}/)?([^/?#]+)/job/", re.I)
# SmartRecruiters: jobs.smartrecruiters.com/{companyId}/{posting}  → companyId
_SR_URL_RE = re.compile(r"jobs\.smartrecruiters\.com/([^/?#]+)/", re.I)


def ats_ref_from_url(url: str):
    """(ats_type, slug) if the URL is an official ATS posting we can fetch through the
    public API (Greenhouse / Lever / Ashby / Workday / SmartRecruiters), else None.
    Used to upgrade a discovery lead's snippet to the real full job description (legal
    — the same APIs as Path A). For Workday the 'slug' is the 'tenant|wdN|site' token;
    for SmartRecruiters it's the company id — both accepted by their board fetchers."""
    u = safe_url(url)
    if not u:
        return None
    for rx, ats in ((_GH_EMBED_RE, "greenhouse"), (_GH_API_RE, "greenhouse"),
                    (_GH_SLUG_RE, "greenhouse"), (_LEVER_SLUG_RE, "lever"),
                    (_ASHBY_SLUG_RE, "ashby")):
        m = rx.search(u)
        if m:
            slug = m.group(1)
            if slug and slug.lower() not in _SLUG_STOPWORDS:
                return (ats, slug)
    m = _WORKDAY_URL_RE.search(u)
    if m:
        return ("workday", f"{m.group(1)}|{m.group(2)}|{m.group(3)}")
    m = _SR_URL_RE.search(u)
    if m and m.group(1).lower() not in _SLUG_STOPWORDS:
        return ("smartrecruiters", m.group(1))
    return None


def _title_key(s: str) -> str:
    """Loose normalized title for matching a lead to a board posting."""
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def enrich_discovery_jobs(jobs: list, new_grad_only: bool = True,
                          max_years: int = MAX_YEARS_FOR_NEWGRAD,
                          focus_keys=None, max_age_hours=None):
    """Discovery V2 resolver. For every discovery job (`from_discovery`), if its
    link is an official Greenhouse/Lever/Ashby board, fetch that board ONCE via the
    public API and title-match to replace the snippet with the FULL JD + canonical
    apply URL + location + posted date. Third-party-only leads are flagged
    `needs_verification`. Non-discovery jobs pass through untouched.

    Returns (jobs, counts) where counts has official / third_party / enriched /
    boards_fetched. Batches by board so N leads on one slug cost ONE API call."""
    fetchers = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby,
                "workday": workday, "smartrecruiters": smartrecruiters}
    counts = {"official": 0, "third_party": 0, "enriched": 0, "boards_fetched": 0}
    by_board = {}                                   # (ats, slug) -> [job, ...]
    for j in jobs:
        if not j.get("from_discovery"):
            continue
        ref = ats_ref_from_url(j.get("job_link"))
        if ref:
            counts["official"] += 1
            by_board.setdefault(ref, []).append(j)
        else:
            counts["third_party"] += 1
            j["needs_verification"] = True
            j.setdefault("jd_source", "snippet")

    for (ats, slug), group in by_board.items():
        fetch = fetchers.get(ats)
        if not fetch:
            continue
        try:                                        # fetch the WHOLE board once, unfiltered,
            board = fetch(slug, new_grad_only=False, # so the lead's specific posting survives
                          max_age_hours=None)
        except Exception:
            board = []
        board = [b for b in board if "_error" not in b]
        counts["boards_fetched"] += 1
        index = {}
        for b in board:
            index.setdefault(_title_key(b.get("title")), b)
        for j in group:
            jt = _title_key(j.get("title"))
            match = index.get(jt)
            if not match and jt:                    # looser contains match
                for bt, b in index.items():
                    if bt and (jt in bt or bt in jt):
                        match = b
                        break
            if match and match.get("description"):
                j["description"] = match["description"]
                j["job_link"]    = match.get("job_link") or j.get("job_link")
                j["location"]    = match.get("location") or j.get("location", "")
                j["posted_date"] = match.get("posted_date") or j.get("posted_date", "")
                j["work_mode"]   = match.get("work_mode") or j.get("work_mode", "")
                if match.get("company"):
                    j["company"] = match["company"]
                j["link_warning"] = False
                j["needs_verification"] = False
                j["jd_source"] = "ats"
                counts["enriched"] += 1
            else:                                   # official link, but no JD match
                j["needs_verification"] = False
                j.setdefault("jd_source", "snippet")
    return jobs, counts


# --------------------------- AI-PASTE IMPORT --------------------------
def _extract_json(text: str):
    """Pull a JSON array/object out of pasted AI text (handles ``` fences)."""
    import json
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t).strip()
    for chunk in (t,):
        try:
            return json.loads(chunk)
        except Exception:
            pass
    a, b = t.find("["), t.rfind("]")
    if a != -1 and b > a:
        try:
            return json.loads(t[a:b + 1])
        except Exception:
            pass
    a, b = t.find("{"), t.rfind("}")
    if a != -1 and b > a:
        try:
            return json.loads(t[a:b + 1])
        except Exception:
            pass
    return None


def normalize_pasted_jobs(text: str, new_grad_only: bool = True,
                          max_years: int = MAX_YEARS_FOR_NEWGRAD, focus_keys=None):
    """Parse a JSON array pasted from an AI job search (command-center schema or
    similar) into internal job dicts. Applies the seniority/years filter, an
    optional role-focus filter, and dedupe. Returns (jobs, stats)."""
    data = _extract_json(text)
    arr = data if isinstance(data, list) else (
        data.get("jobs") if isinstance(data, dict) and isinstance(data.get("jobs"), list) else None)
    if arr is None:
        return [], {"error": ("No JSON array found. Paste the AI's output as a JSON array — e.g. "
                              '[{"company":"...","role":"...","apply_link":"...",'
                              '"job_description":"..."}]. Code fences (```json) are OK.')}
    if not arr:
        # A legitimately EMPTY array — the prompt explicitly tells the AI to return []
        # when no jobs pass its rules. That's a valid result, not a paste error.
        return [], {"kept": 0, "filtered_senior": 0, "filtered_focus": 0,
                    "filtered_dupes": 0, "link_warnings": 0, "empty": True}
    # Always enforce the role-family filter — even 'general' now rejects non-SWE
    # families (QA, semiconductor, sales, support, …) via title_matches_focus.
    apply_focus = True
    kept, senior, off_focus, link_warnings = [], 0, 0, 0
    for j in arr:
        if not isinstance(j, dict):
            continue
        title = str(j.get("role") or j.get("title") or j.get("job_title") or "").strip()
        company = str(j.get("company") or "").strip()
        if not title or not company:
            continue
        desc = str(j.get("job_description") or j.get("description") or j.get("jd") or "").strip()
        exp = str(j.get("experience_required") or j.get("experienceRequired") or "")
        if new_grad_only and not passes_entry_level(title, desc + " " + exp, max_years):
            senior += 1
            continue
        if apply_focus and not roles.title_matches_focus(title, focus_keys):
            off_focus += 1
            continue
        fit = j.get("fit_score", j.get("fitScore", j.get("fit")))
        loc = str(j.get("location") or "").strip()
        wm = str(j.get("work_mode") or j.get("workMode") or j.get("remote") or "")
        link = safe_url(j.get("apply_link") or j.get("applyLink") or j.get("application_url")
                        or j.get("url") or j.get("link") or "")
        lq = link_quality(link)
        if lq["warning"]:
            link_warnings += 1
        matched = j.get("matched_skills") or j.get("matchedSkills") or []
        gaps = j.get("gaps") or j.get("missing_skills") or []
        if not isinstance(matched, list):
            matched = []
        if not isinstance(gaps, list):
            gaps = []
        kept.append({
            "title": title,
            "company": company,
            "location": loc,
            "job_link": link,
            "source": str(j.get("source") or "AI search").strip(),
            "description": desc,
            "fit_score": fit,
            "fit_reason": str(j.get("fit_reason") or j.get("fitReason") or j.get("why_relevant") or "").strip(),
            "h1b_note": str(j.get("h1b_note") or j.get("h1bNote") or "").strip(),
            "matched_skills_ai": [str(x).strip() for x in matched if str(x).strip()],
            "gaps_ai": [str(x).strip() for x in gaps if str(x).strip()],
            "experience_required": exp,
            "posted_date": str(j.get("posted_date") or j.get("postedDate") or j.get("job_freshness") or "").strip(),
            "salary": str(j.get("salary") or "").strip(),
            "priority": str(j.get("priority") or "").strip(),
            "link_warning": lq["warning"],
            "link_ok": lq["ok"],
            "work_mode": detect_work_mode(wm + " " + loc + " " + desc),
        })
    before = len(kept)
    kept = dedupe(kept)
    return kept, {"kept": len(kept), "filtered_senior": senior,
                  "filtered_focus": off_focus, "filtered_dupes": before - len(kept),
                  "link_warnings": link_warnings}
