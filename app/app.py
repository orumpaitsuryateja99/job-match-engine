"""
app.py — Job Automation dashboard (Streamlit).

Run from the Job_Automation/ folder:
    streamlit run app/app.py

5 tabs: Resume · Find Jobs · Match & Score · Tailor & Apply · Tracker
Human-in-the-loop: the system never applies. You click "✅ I Applied".
"""
import os
import re
import sys
import json
import time
import html as _html
import hashlib
from datetime import date, timedelta
from urllib.parse import quote, urlparse

import streamlit as st
import streamlit.components.v1 as components
import yaml

# We deliberately drive the Find Jobs filter widgets from st.session_state so they
# survive tab switches (Streamlit garbage-collects the state of widgets not rendered
# in a run). That supported pattern makes Streamlit log a benign "created with a
# default value but also had its value set via the Session State API" warning for
# every such widget, every rerun — pure terminal spam. Silence ONLY that one message
# on its specific (non-propagating) logger; all other warnings still come through.
import logging as _logging
_logging.getLogger("streamlit.elements.lib.policies").addFilter(
    lambda _r: "had its value set via the Session State API" not in _r.getMessage())

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)            # Job_Automation/
sys.path.insert(0, HERE)


def _load_dotenv():
    """Load Job_Automation/.env (KEY=VALUE lines) into os.environ — lets you keep API
    keys out of settings.yaml entirely. No dependency; existing env vars win; the
    source modules already read SERPAPI_API_KEY / JSEARCH_API_KEY / TAVILY_API_KEY /
    ADZUNA_* etc. .env is git-ignored."""
    path = os.path.join(BASE, ".env")
    try:
        if not os.path.exists(path):
            return
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass
    # On Streamlit Community Cloud there's no .env file — keys live in the app's
    # Secrets manager instead, exposed via st.secrets. Mirror them into os.environ
    # so the rest of the codebase (which only calls os.getenv) keeps working unchanged.
    try:
        for k, v in st.secrets.items():
            if k and k not in os.environ:
                os.environ[k] = str(v)
    except Exception:
        pass


_load_dotenv()

import resume_parser as rp
import ats as ats_mod
import tailor as tailor_mod
import pdf_gen
import tracker as trk
import sources as src
import aggregator as agg
import prompts
import roles
import latex_resume as ltx
import h1b as h1b_mod
import llm_score
from textutils import detect_work_mode, detect_sponsorship_block, detect_us_location


# ----------------------------- helpers --------------------------------
def P(rel):  # resolve a path relative to Job_Automation/
    return os.path.join(BASE, rel)


def _log_exc(where: str):
    """Best-effort: append the current exception (with traceback) to
    logs/app_errors.log so otherwise-silent failures (corrupt profile/jobs cache,
    bad résumé) are debuggable. Never raises — logging must not break the app."""
    try:
        import traceback
        from datetime import datetime as _dt
        logp = P("logs/app_errors.log")
        os.makedirs(os.path.dirname(logp), exist_ok=True)
        with open(logp, "a", encoding="utf-8") as f:
            f.write(f"\n[{_dt.now().isoformat(timespec='seconds')}] {where}\n")
            f.write(traceback.format_exc())
    except Exception:
        pass


@st.cache_data
def load_settings():
    with open(P("config/settings.yaml"), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@st.cache_data
def load_source_catalog():
    return agg.load_catalog(P("config/source_catalog.yaml"))


@st.cache_data(ttl=600, show_spinner=False)
def serpapi_quota_cached(api_key: str, _bucket: int):
    """SerpApi remaining-searches, cached ~10 min (keyed by a time bucket) so the
    account endpoint isn't hit on every rerun. getattr-guarded so a stale/partial
    sources module can't hard-crash the Find Jobs page."""
    fn = getattr(src, "serpapi_account", None)
    return fn(api_key) if fn else {}


def empty_profile():
    """Start the UI empty. A profile becomes active only after resume upload."""
    return {
        "name": "",
        "email": "",
        "phone": "",
        "links": {},
        "summary": "",
        "skill_categories": [],
        "skill_phrases": [],
        "skills": [],
        "tools": [],
        "domains": [],
        "projects": [],
        "experience": [],
        "education": [],
        "additional": [],
        "target_roles": ["software engineer", "backend engineer", "full stack engineer"],
        "experience_years": 0,
        "raw_text": "",
        "latex_template": "",
        "resume_file": "",
    }


def persist_jobs(cfg):
    """No-op BY DESIGN. Jobs are SESSION-ONLY: nothing is written to disk and every
    page load starts empty (see startup). Previously this wrote logs/session_jobs.json
    on every search/clear, but that file was never read back — so it only leaked the
    job list to disk against the session-only model. Kept as a stable call site so the
    search/clear handlers don't special-case persistence. (To keep a set deliberately,
    use the user-initiated Save batch — save_batch/load_batch below.)"""
    return None


_SCORE_KEYS = ("score", "band", "matched_skills", "missing_skills", "missing_tools", "components")


def _valid_job_item(it):
    """Guard against a stale/partial saved batch breaking the Match & Score render."""
    return (isinstance(it, dict)
            and isinstance(it.get("job"), dict)
            and it["job"].get("title") and it["job"].get("company")
            and isinstance(it.get("score"), dict)
            and all(k in it["score"] for k in _SCORE_KEYS))


# ---- Persistent job marks (🔴 not-relevant) — survive reload, keyed by job identity ----
def _marks_path(cfg):
    return P(os.path.join(cfg["paths"].get("logs", "logs"), "job_marks.json"))


def load_marks(cfg) -> dict:
    """Restore the user's 🔴/🟢 marks {job_ui_key: 'rejected'} from disk."""
    try:
        p = _marks_path(cfg)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
    except Exception:
        _log_exc("load_marks: could not read job_marks.json")
    return {}


def save_marks(cfg):
    """Persist the current marks so 'not relevant' / 'applied' decisions stick."""
    try:
        p = _marks_path(cfg)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(st.session_state.get("job_marks", {}), f)
    except Exception:
        _log_exc("save_marks: could not write job_marks.json")


# ---- Named batches: save a Match & Score list to apply to later ----
def _batches_dir(cfg):
    return P(os.path.join(cfg["paths"].get("logs", "logs"), "batches"))


def list_batches(cfg):
    d = _batches_dir(cfg)
    if not os.path.isdir(d):
        return []
    return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json"))


def save_batch(cfg, name, jobs):
    """Save the current scored job list under a name → logs/batches/<name>.json."""
    safe = pdf_gen.safe_filename(name) or "batch"
    try:
        d = _batches_dir(cfg)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, safe + ".json"), "w", encoding="utf-8") as f:
            json.dump(jobs, f)
        return safe
    except Exception:
        _log_exc("save_batch")
        return ""


def load_batch(cfg, name):
    """Load a saved batch back into the session (schema-validated)."""
    path = os.path.join(_batches_dir(cfg), pdf_gen.safe_filename(name) + ".json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return [it for it in data if _valid_job_item(it)]
        except Exception:
            _log_exc("load_batch")
    return []


def delete_batch(cfg, name):
    path = os.path.join(_batches_dir(cfg), pdf_gen.safe_filename(name) + ".json")
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        _log_exc("delete_batch")


def clear_resume_state(cfg):
    # Truly remove the résumé: delete the uploaded file too, so "remove" means gone
    # (not just hidden) — the loaded résumé tracks the file 1:1.
    prof = st.session_state.get("profile") or {}
    rel = prof.get("resume_file", "")
    in_dir = os.path.abspath(P(cfg["paths"]["input_resume"]))
    if rel:
        rf = P(rel) if not os.path.isabs(rel) else rel
        # only delete files inside input_resume/ (never the sample/test fixtures elsewhere)
        try:
            if os.path.exists(rf) and os.path.abspath(rf).startswith(in_dir):
                os.remove(rf)
        except Exception:
            _log_exc("clear_resume_state: could not delete uploaded résumé file")
    # Also sweep any leftover UPLOADED files (named '<stem>_<8hex>.<ext>') so a stale
    # sibling can't be picked up later. Non-hashed files (e.g. a reference résumé you
    # placed there yourself) are preserved.
    try:
        if os.path.isdir(in_dir):
            for fn in os.listdir(in_dir):
                if re.search(r"_[0-9a-f]{8}\.(pdf|docx|txt|md|tex)$", fn, re.I):
                    os.remove(os.path.join(in_dir, fn))
    except Exception:
        _log_exc("clear_resume_state: could not sweep leftover uploads")
    st.session_state["profile"] = empty_profile()
    st.session_state["resume_ready"] = False
    st.session_state["resume_just_uploaded"] = False
    st.session_state["uploaded_resume_name"] = ""
    st.session_state["uploaded_resume_fingerprint"] = ""
    # Removing the résumé invalidates every matched job — clear them so Tailor &
    # Apply / Match & Score don't show jobs tied to a résumé that's now gone.
    st.session_state["jobs"] = []
    st.session_state["has_run_find_jobs"] = False
    reset_match_source_filter()
    st.session_state.pop("active_tailor_job", None)
    # NB: job_marks are NOT cleared here — they persist across résumé changes/reloads
    # (a "not relevant" decision about a specific job stands regardless of résumé).
    pj = P(cfg["paths"]["profile_json"])
    if os.path.exists(pj):
        os.remove(pj)


def session_resume_ready() -> bool:
    """True only if a résumé was uploaded IN THIS SESSION (and parsed to skills).
    Gates finding/scoring/displaying jobs — nothing happens without a current résumé."""
    return bool(st.session_state.get("resume_ready")
                and (st.session_state.get("profile") or {}).get("skills"))


# Shown wherever an action is blocked for lack of a résumé.
RESUME_REQUIRED_MSG = "Upload your resume first so jobs can be matched to your profile."


def band_color(b):
    return {"Strong": "🟢", "Good": "🟡", "Stretch": "🟠", "Weak": "🔴"}.get(b, "⚪")


# Freshness windows shared by Path A (real date filter) and Path B (prompt rule).
# Values are MAX AGE IN HOURS; None = no limit.
POSTED_WINDOWS = {
    "Any time": None,
    "Past 1 hour": 1,
    "Past 3 hours": 3,
    "Past 12 hours": 12,
    "Past 24 hours (1 day)": 24,
    "Past 3 days": 72,
    "Past 7 days (1 week)": 168,
    "Past 14 days (2 weeks)": 336,
    "Past 30 days (1 month)": 720,
    "Past 60 days (2 months)": 1440,
    "Past 90 days (3 months)": 2160,
}


def freshness_label_for_prompt(hours):
    """Human phrase for the AI search prompt's freshness rule."""
    return {
        None: "any time (most recent first)",
        1: "the last 1 hour", 3: "the last 3 hours", 12: "the last 12 hours",
        24: "the last 24 hours", 72: "the last 3 days", 168: "the last 7 days",
        336: "the last 14 days", 720: "the last 30 days",
        1440: "the last 60 days", 2160: "the last 90 days",
    }.get(hours, "the last 30 days")


def _skill_match_strength(sc):
    """0–100 signal for 'how well does THIS job's required skills match MY résumé'.
    Blends coverage (% of the JD's detected hard skills I actually have) with
    breadth (how many distinct skills overlap, capped). A job whose JD lists real
    skills I have NONE of scores 0 — the core fix for irrelevant roles ranking high.
    """
    matched = sc.get("matched_skills", []) or []
    missing = sc.get("missing_skills", []) or []
    req = len(matched) + len(missing)
    breadth = 100.0 * min(len(matched), 6) / 6.0            # absolute overlap, capped at 6
    if req == 0:
        return breadth * 0.5          # JD listed no detectable hard skills → weak signal
    coverage = 100.0 * len(matched) / req                  # share of the JD's skills I have
    return 0.6 * coverage + 0.4 * breadth


def relevance_score(item):
    """Priority score that orders which jobs to apply to first.

    Path B (AI search) keeps the command-center blend 0.5*ATS + 0.5*fit. Path A
    (board pulls, no AI fit) is driven by REAL résumé↔JD fit: skill overlap first,
    then how much of the job description my résumé covers, then the structured ATS
    title/level score — so a generic 'Software Engineer' I share no skills with no
    longer outranks a full-stack role that matches 5 of my skills.
    """
    sc = item["score"]
    ats = sc["score"]
    fit = item["job"].get("fit_score", item["job"].get("fitScore"))
    try:
        fit = float(fit)
    except (TypeError, ValueError):
        fit = None
    j = item["job"]
    if fit is not None and 0 <= fit <= 100:
        base = round(0.5 * ats + 0.5 * fit)
    else:
        ms = _skill_match_strength(sc)
        jd = item.get("jd_ats")
        if jd is not None:
            base = round(0.60 * ms + 0.25 * jd + 0.15 * ats)
        else:
            base = round(0.70 * ms + 0.30 * ats)
    # Ranking quality, not filtering: float high-trust/new-grad/full-JD roles,
    # demote snippet-only third-party leads until the exact posting is verified.
    desc_len = len(j.get("description") or "")
    adj = 0
    if j.get("is_new_grad"):
        adj += 9
    if item.get("h1b"):
        adj += 3
    if src.url_rank(j.get("job_link", "")) == 3:
        adj += 4
    if j.get("jd_source") == "ats" or desc_len >= 900:
        adj += 4
    if j.get("needs_verification") or j.get("jd_source") == "snippet":
        adj -= 8
    if j.get("link_warning"):
        adj -= 4
    if j.get("us_location") == "foreign":
        adj -= 10
    base = max(0, min(100, base + adj))
    # A JD that won't sponsor (or needs citizenship/clearance) is a hard skip for an
    # international student — sink it to the bottom regardless of fit.
    if j.get("no_sponsorship"):
        return min(base, 3)
    # A dead/closed posting is useless no matter how relevant — keep it off the top.
    if j.get("link_live") is False:
        return min(base, 20)
    return base


@st.cache_data
def _read_text_cached(path, _mtime):
    return rp.read_resume_text(path)


def get_resume_text(cfg, profile):
    """Full résumé text for the AI prompt. Uses the in-session parse if present,
    else re-reads the EXACT file this profile was built from (profile.json drops
    raw_text across restarts).

    Deliberately does NOT scan input_resume/ for a 'newest file' fallback: that
    resurrected a leftover/cleared résumé and silently scored against it even when
    nothing was uploaded. The recorded resume_file is the single source of truth —
    no recorded résumé ⇒ no résumé text."""
    rt = profile.get("raw_text")
    if rt:
        return rt
    rel = profile.get("resume_file", "")
    if rel:
        path = P(rel) if not os.path.isabs(rel) else rel
        if os.path.exists(path) and path.lower().endswith((".pdf", ".docx", ".txt", ".md", ".tex")):
            try:
                return _read_text_cached(path, os.path.getmtime(path))
            except Exception:
                pass
    return ""


def copy_button(text, label="📋 Copy prompt to clipboard"):
    """A real one-click clipboard button.

    The text is held in a hidden <textarea> (HTML-escaped) and copied via a
    <script> listener — never injected into an attribute. This avoids the bug
    where an apostrophe in the prompt broke an inline onclick='...' and spilled
    raw HTML onto the page.
    """
    uid = hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:10]
    safe = _html.escape(text or "")
    components.html(
        f"""
        <textarea id="src_{uid}" readonly
            style="position:absolute;left:-9999px;top:0;height:1px;width:1px;">{safe}</textarea>
        <button id="btn_{uid}"
            style="padding:9px 18px;border-radius:8px;border:1px solid #4a4a4a;
            background:#262730;color:#fafafa;cursor:pointer;font-size:14px;font-weight:600;">
            {_html.escape(label)}</button>
        <script>
        (function() {{
          var b = document.getElementById("btn_{uid}");
          var src = document.getElementById("src_{uid}");
          b.addEventListener("click", function() {{
            var t = src.value;
            function done() {{ b.innerText = "✅ Copied!"; setTimeout(function(){{b.innerText="{_html.escape(label)}";}}, 1800); }}
            if (navigator.clipboard && navigator.clipboard.writeText) {{
              navigator.clipboard.writeText(t).then(done, function() {{ src.select(); document.execCommand("copy"); done(); }});
            }} else {{ src.select(); document.execCommand("copy"); done(); }}
          }});
        }})();
        </script>
        """,
        height=52,
    )


ATS_CHECKERS = [
    ("Jobscan", "https://www.jobscan.co/"),
    ("Resume Worded", "https://resumeworded.com/"),
    ("Enhancv ATS", "https://enhancv.com/resources/resume-checker/"),
    ("Teal", "https://www.tealhq.com/tools/resume-checker"),
]


def ats_checker_links(note=True):
    """Link out to real ATS checkers — our score is only a local estimate."""
    if note:
        st.caption("ℹ️ The scores here are a **local estimate** to prioritize effort — not the "
                   "employer's real ATS result. For an accurate report, paste your resume + this "
                   "exact job description into a dedicated checker:")
    cols = st.columns(len(ATS_CHECKERS))
    for c, (label, url) in zip(cols, ATS_CHECKERS):
        c.link_button(label, url, width="stretch")


def source_tag(j: dict) -> str:
    """Short visible tag for where a job came from."""
    source = (j.get("source") or "").strip()
    if source and source.lower() not in ("ai search", "manual"):
        return source
    link = j.get("job_link") or ""
    host = urlparse(link).netloc.lower().replace("www.", "")
    if "greenhouse" in host:
        return "Greenhouse"
    if "lever.co" in host:
        return "Lever"
    if "ashbyhq" in host:
        return "Ashby"
    if "workdayjobs" in host or "myworkdayjobs" in host:
        return "Workday"
    if "glassdoor" in host:
        return "Glassdoor"
    if "dice.com" in host:
        return "Dice"
    if "smartrecruiters" in host:
        return "SmartRecruiters"
    if "icims" in host:
        return "iCIMS"
    if "linkedin" in host:
        return "LinkedIn"
    if "indeed" in host:
        return "Indeed"
    if host:
        return host.split(".")[0].title()
    return source or "Manual"


ALL_SOURCES_FILTER = "All Sources"


def reset_match_source_filter():
    """Return Match & Score to the full current job list."""
    st.session_state["match_source_filter"] = ALL_SOURCES_FILTER


def job_source_label(item_or_job: dict) -> str:
    """Source label used by the Match & Score source filter."""
    if not isinstance(item_or_job, dict):
        return "Other"
    j = item_or_job.get("job") if isinstance(item_or_job.get("job"), dict) else item_or_job
    label = (j.get("source_tag") or source_tag(j)).strip()
    return label or "Other"


def source_counts_for_jobs(items: list) -> dict:
    counts = {}
    for it in items or []:
        label = job_source_label(it)
        counts[label] = counts.get(label, 0) + 1
    return counts


@st.cache_data(show_spinner=False)
def pdf_preview_pages(path: str, mtime: float, max_pages: int = 2) -> list:
    """Render PDF pages to PNG bytes for reliable in-app preview."""
    import io
    import fitz
    images = []
    with fitz.open(path) as doc:
        for i in range(min(max_pages, doc.page_count)):
            page = doc[i]
            pix = page.get_pixmap(matrix=fitz.Matrix(3.0, 3.0), colorspace=fitz.csRGB, alpha=False)
            png = pix.tobytes("png")
            try:
                from PIL import Image, ImageEnhance
                img = Image.open(io.BytesIO(png)).convert("RGB")
                img = ImageEnhance.Contrast(img).enhance(1.28)
                img = ImageEnhance.Sharpness(img).enhance(1.25)
                out = io.BytesIO()
                img.save(out, format="PNG", optimize=True)
                png = out.getvalue()
            except Exception:
                pass
            images.append(png)
    return images


def display_uploaded_resume(profile: dict, preview_width: int = 700):
    """Preview the uploaded resume instead of dumping parsed text on the main page."""
    rel = profile.get("resume_file", "")
    if not rel:
        st.info("Upload your master resume once; the app will preview it here and use it for matching/tailoring.")
        return
    path = P(rel) if not os.path.isabs(rel) else rel
    if not os.path.exists(path):
        st.warning("Saved resume file was not found. Upload it again to refresh the preview.")
        return
    ext = os.path.splitext(path)[1].lower()
    st.markdown("**Uploaded resume preview**")
    st.caption(f"Previewing {os.path.basename(path)}")
    if ext == ".pdf":
        for idx, image in enumerate(pdf_preview_pages(path, os.path.getmtime(path))):
            st.image(image, caption=f"Page {idx + 1}", width=preview_width)
    elif ext in (".txt", ".md", ".tex"):
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            st.code(f.read(), language="latex" if ext == ".tex" else "text")
    else:
        st.caption(f"Uploaded resume saved at {rel}")


def _link_url(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if value.startswith(("http://", "https://", "mailto:")):
        return value
    return "https://" + value


def _markdown_links(links: dict, labels: dict = None) -> str:
    labels = labels or {}
    pieces = []
    for key, value in (links or {}).items():
        url = _link_url(value)
        if url:
            pieces.append(f"[{labels.get(key, key.title())}]({url})")
    return " · ".join(pieces)


def _render_section_bullets(items: list):
    for b in items or []:
        st.markdown(f"- {b}")


def search_links(query: str, location: str) -> list:
    """Discovery links that open each site's search with the KEYWORD and LOCATION
    in their correct, separate boxes (Indeed/Glassdoor/LinkedIn/Dice take a distinct
    location field — jamming the location into the keyword box returns nothing)."""
    kw = (query or "software engineer new grad").strip()
    q = quote(kw)
    loc = quote((location or "").strip())
    careers_q = quote(
        f'{kw} "Software Engineer" '
        '(site:myworkdayjobs.com OR site:greenhouse.io OR site:lever.co '
        'OR site:ashbyhq.com OR site:smartrecruiters.com OR site:icims.com)'
    )
    return [
        ("Google postings", f"https://www.google.com/search?q={q}+site%3Agreenhouse.io+OR+site%3Alever.co+OR+site%3Aashbyhq.com+OR+site%3Amyworkdayjobs.com"),
        ("Company careers", f"https://www.google.com/search?q={careers_q}"),
        ("Workday ATS", f"https://www.google.com/search?q={q}+site%3Amyworkdayjobs.com+OR+site%3Aworkdayjobs.com"),
        ("LinkedIn Jobs", f"https://www.linkedin.com/jobs/search/?keywords={q}&location={loc}"),
        ("Indeed", f"https://www.indeed.com/jobs?q={q}&l={loc}"),
        ("Glassdoor", f"https://www.glassdoor.com/Job/jobs.htm?sc.keyword={q}&locKeyword={loc}"),
        ("Handshake", f"https://www.google.com/search?q={q}+site%3Ajoinhandshake.com%2Fstu%2Fpostings"),
        ("Jobright AI", f"https://jobright.ai/jobs?keyword={q}"),
        ("SubmitX AI", "https://submitx.ai/"),
        ("MyVisaJobs", f"https://www.myvisajobs.com/Search_Visa_Sponsor.aspx?N={q}"),
        ("OPTnation", f"https://www.optnation.com/jobs-search?keyword={q}"),
        ("Interstride", "https://interstride.com/"),
        ("Lensa", f"https://lensa.com/jobs/search?q={q}&location={loc}"),
        ("Dice", f"https://www.dice.com/jobs?q={q}&location={loc}"),
        ("Built In", f"https://builtin.com/jobs?search={q}"),
        ("Wellfound", "https://wellfound.com/jobs"),
        ("Naukri", f"https://www.naukri.com/{q.replace('%20', '-')}-jobs"),
        ("Hired", f"https://www.google.com/search?q={q}+site%3Ahired.com%2Fjobs"),
        ("Hire Tech", f"https://www.google.com/search?q={q}+%22hire+technologies%22+jobs"),
    ]


def _add_jobs(jobs, cfg, min_relevance=0, max_age_hours=None):
    """Score incoming jobs against the active profile and stash in session.
    De-duplicates against jobs already in the session (so repeated pulls don't
    pile up copies).

    `min_relevance` (used by board pulls) drops jobs whose résumé ATS score is
    below the floor AND that share no matched skill with the résumé — so Path A
    becomes résumé-aware instead of "every entry-level role at these companies".
    Skipped only when a résumé is actually loaded.
    """
    prof = st.session_state["profile"]
    sponsors = cfg.get("h1b_sponsors", [])
    focus_keys = st.session_state.get("focus_keys") or ["general"]
    target_roles = roles.target_roles_for(focus_keys)
    has_resume = bool(prof.get("skills"))
    # Full résumé text once, for the tailored-résumé ATS keyword coverage per job
    # (honest tailoring never adds keywords, so this is the tailored score too).
    resume_text = get_resume_text(cfg, prof) or ltx.profile_plain_text(prof)
    # Precompute the résumé-derived structures ONCE per batch (they're identical for
    # every job) instead of rebuilding them inside ats_score / compute_text_ats_match
    # per job — ~89% of ats_score time was this rebuild. See ats.profile_keyword_pool.
    _prof_keywords = ats_mod.profile_keyword_pool(prof)
    _resume_tokens = ltx.resume_token_set(resume_text) if resume_text else None
    existing = {src.job_key(it["job"]) for it in st.session_state["jobs"]}
    strict_us_only = st.session_state.get("strict_us_only", True)
    added, dropped, dropped_stale, dropped_location = 0, 0, 0, 0
    for j in jobs:
        if "_error" in j:
            continue
        if not src.job_within_freshness(j, max_age_hours):
            dropped_stale += 1
            continue
        key = src.job_key(j)
        if key in existing:
            continue
        existing.add(key)
        lq = src.link_quality(j.get("job_link", ""))
        j.setdefault("link_ok", lq["ok"])
        j.setdefault("link_warning", lq["warning"])
        j["source_tag"] = source_tag(j)
        j["source"] = (j.get("source") or j["source_tag"] or "Other").strip()
        # Tag genuine new-grad / early-career roles (title or JD) so Match & Score can
        # float them to the top and offer a 'New-grad only' filter — without hiding the
        # rest (new-grad-titled reqs are seasonal and scarce off-season).
        j["is_new_grad"] = roles.is_new_grad_role(j.get("title", ""), j.get("description", ""))
        # US-location class ('us' | 'foreign' | 'unknown') for the strict-US filter.
        j["us_location"] = detect_us_location(
            f"{j.get('title','')} {j.get('company','')} {j.get('location','')} "
            f"{j.get('description','')[:700]}")
        # Hard-drop only CLEARLY-foreign roles at search time. 'unknown' (discovery
        # snippets with no location signal — often actually US) is kept and handled by
        # the toggleable 'US locations only' filter in Match & Score, so we don't
        # permanently lose real US jobs that just had a vague snippet.
        if strict_us_only and j["us_location"] == "foreign":
            dropped_location += 1
            continue
        # make sure every job carries a work_mode (manual adds don't set one)
        if not j.get("work_mode"):
            j["work_mode"] = detect_work_mode(
                f"{j.get('location','')} {j.get('description','')}")
        parsed = ats_mod.parse_job(j.get("description", ""), j.get("title", ""))
        sc = ats_mod.ats_score(prof, parsed, my_years=cfg.get("my_years_experience", 0),
                               target_roles=target_roles, profile_keywords=_prof_keywords)
        # Résumé-relevance gate (board pulls only). Drop a role when:
        #  (a) it scores below the floor AND shares no skill with the résumé, OR
        #  (b) the JD clearly lists hard skills (>=4) and the résumé matches NONE of
        #      them — i.e. genuinely off-target however generic the title looks.
        # Discovery leads carry only a search snippet (no full JD), so skill-overlap
        # scoring is meaningless for them — exempt them from the relevance floor.
        if min_relevance and has_resume and not j.get("from_discovery"):
            req = len(sc["matched_skills"]) + len(sc["missing_skills"])
            no_overlap = not sc["matched_skills"]
            if (sc["score"] < min_relevance and no_overlap) or (no_overlap and req >= 4):
                dropped += 1
                continue
        jd_ats = (ltx.compute_text_ats_match(resume_text, j.get("description", ""),
                                             resume_tokens=_resume_tokens)
                  if resume_text else None)
        # H1B confidence (structured DB, alias/exact match) + anti-sponsorship scan
        h1b_st = h1b_mod.status(j.get("company", ""), P("config/h1b_sponsors.json"),
                                fallback_list=sponsors)
        j["no_sponsorship"] = detect_sponsorship_block(j.get("description", ""))
        st.session_state["jobs"].append({
            "job": j, "score": sc,
            "h1b": h1b_st["sponsor"],
            "h1b_status": h1b_st,
            "jd_ats": jd_ats["score"] if jd_ats else None,
            "years_required": parsed.get("years_required", 0),   # for the Risk line
            "fetched_at": date.today().isoformat(),   # for stale-job clarity on restore
        })
        added += 1
    st.session_state["_last_dropped_relevance"] = dropped
    st.session_state["_last_dropped_stale"] = dropped_stale
    st.session_state["_last_dropped_location"] = dropped_location
    st.session_state["jobs_restored"] = False     # these are fresh this session
    persist_jobs(cfg)
    return added


def _job_ui_key(j: dict) -> str:
    # Unique, stable per-job widget key. Truncating the sanitized key to a fixed
    # length collided when two postings shared a long prefix (e.g. two LinkedIn
    # URLs ending '…-new-college-grad-…'), which crashed Streamlit with a duplicate
    # element key. Appending a short hash of the FULL key disambiguates them while
    # staying deterministic across reruns.
    raw = src.job_key(j) or f"{j.get('company','')}_{j.get('title','')}"
    safe = pdf_gen.safe_filename(raw).replace(".", "_")[:70] or "job"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    return f"{safe}_{digest}"


def render_tailor_apply_panel(item, cfg, key_suffix: str):
    """Per-job Tailor & Apply workflow as a clear 4-step flow:
    1 Review the job · 2 Your résumé · 3 Generate materials · 4 Apply.
    Shared by tab4 and the inline Match & Score panel."""
    j, sc = item["job"], item["score"]
    prof = st.session_state["profile"]
    pdf_key = "pdf_" + key_suffix
    latex_code_key = "latex_code_" + key_suffix
    latex_path_key = "latex_path_" + key_suffix
    parsed = ats_mod.parse_job(j.get("description", ""), j.get("title", ""))

    # ─────────────────── STEP 1 · Review the job ───────────────────
    st.markdown("#### 1 · Review the job")
    st.markdown(f"### {j.get('company', '')} — {j.get('title', '')}")
    _meta = " · ".join(x for x in [
        (j.get("location", "").strip() or "Location —"), j.get("work_mode", ""),
        (j.get("source_tag") or j.get("source", "")),
        (f"posted {j.get('posted_date')}" if j.get("posted_date") else "")] if x)
    if _meta:
        st.caption(_meta)
    hb = item.get("h1b_status") or {}
    rm1, rm2, rm3 = st.columns(3)
    rm1.metric("Fit / priority", relevance_score(item))
    rm2.metric("ATS match", f"{sc['score']}%", sc.get("band", ""))
    rm3.markdown("**H1B**")
    rm3.markdown(h1b_mod.badge(hb) + (f" · {hb.get('label')}" if hb.get("label") else ""))
    if j.get("no_sponsorship"):
        rm3.caption("⚠️ JD hints it may NOT sponsor — verify.")
    why1, why2 = st.columns(2)
    with why1:
        st.markdown("**✅ Why it matches**")
        st.write(", ".join(sc.get("matched_skills", [])[:10]) or "_no overlapping hard skills detected_")
    with why2:
        st.markdown("**🚫 Gaps — don't fake these**")
        st.write(", ".join(sc.get("missing_skills", [])[:10]) or "_none_")
    if j.get("link_warning"):
        st.warning(f"{j['link_warning']} Verify the exact posting URL before applying.")
    lc1, lc2 = st.columns([1, 2])
    with lc1:
        if j.get("job_link") and st.button("🔄 Re-check link", key=f"recheck_{key_suffix}",
                                            help="HTTP-check this posting now (it may have closed since the pull)."):
            with st.spinner("Checking…"):
                src.verify_links([j])
            persist_jobs(cfg)
    with lc2:
        if j.get("link_live") is False:
            st.error(f"❌ Posting closed/unreachable (HTTP {j.get('link_status', '?')}). Find the live posting.")
        elif j.get("link_live") is True:
            st.caption(f"✅ Link checked live (HTTP {j.get('link_status', '200')}).")
    # Application link — always visible (prominent button), never silently hidden.
    if j.get("job_link"):
        st.link_button("🔗 Apply — open job posting", j["job_link"], type="primary")
        st.caption(j["job_link"])
    else:
        st.warning("No application link available.")

    st.divider()

    # ─────────────────── STEP 2 · Your résumé ───────────────────
    st.markdown("#### 2 · Your résumé")
    if prof.get("skills"):
        _rn = os.path.basename(prof.get("resume_file", "")) or "your uploaded résumé"
        st.caption(f"Tailoring is based on **{_rn}** (from tab 1 · Resume). "
                   "To use a different base résumé, swap it there.")
    else:
        st.warning("No résumé loaded — upload one in **tab 1 · Resume** so tailoring and scoring work.")

    st.divider()

    # ─────────────────── STEP 3 · Generate materials ───────────────────
    st.markdown("#### 3 · Generate tailored materials")
    _has_resume = bool(prof.get("skills"))
    if not _has_resume:
        st.info("⬆️ **Upload a résumé in tab 1 · Resume** to tailor a PDF or a cover letter. "
                "Nothing is generated from a past/saved résumé. You can still review the job "
                "and record an application below.")
    tailored = tailor_mod.tailor_resume(prof, parsed)
    _lead = (tailored.get("projects") or [{}])[0].get("name", "")
    st.caption(f"🧭 Angle: **{tailored.get('tailoring_angle_label', 'Software Engineer')}** — "
               f"leads with **{_lead or 'your strongest project'}**, surfaces this role's skills first. "
               "Only reorders/selects your real content — never invents anything.")
    with st.expander("🔍 What tailoring changed"):
        jd_terms = parsed["skills"] | parsed["tools"]
        _front = [s for s in tailored.get("skills", []) if s in jd_terms]
        st.markdown("**Project order:** " + " → ".join(p.get("name", "") for p in tailored.get("projects", [])))
        st.markdown("**Skills surfaced first:** "
                    + (", ".join(_front[:10]) or "_none matched — original order kept_"))
        for _label, _secs in (("Experience", tailored.get("experience", [])),
                              ("Projects", tailored.get("projects", []))):
            for _s in _secs:
                _nm = _s.get("name") or _s.get("company") or _s.get("role") or _label
                _bl = _s.get("bullets", [])
                if _bl:
                    st.markdown(f"**{_nm}**")
                    for _b in _bl[:3]:
                        st.markdown(f"- {_b}")

    ok, violations = tailor_mod.validate_no_fabrication(tailored, prof)
    if not ok:
        st.error("Honesty validator blocked the draft:")
        for v in violations:
            st.write("•", v)
    else:
        if st.button("📄 Generate tailored résumé PDF", key=f"gen_pdf_{key_suffix}", type="primary",
                     disabled=not _has_resume):
            fname = pdf_gen.safe_filename(
                f"Resume_{j['company']}_{j['title']}_{date.today().isoformat()}.pdf")
            out = P(os.path.join(cfg["paths"]["tailored_resumes"], fname))
            pdf_gen.generate_resume_pdf(tailored, out)
            st.session_state[pdf_key] = out
        saved = st.session_state.get(pdf_key)
        if saved and os.path.exists(saved) and saved.endswith(".pdf"):
            with open(saved, "rb") as f:
                st.download_button("⬇️ Download tailored résumé PDF", f,
                                   file_name=os.path.basename(saved), mime="application/pdf",
                                   key=f"download_pdf_{key_suffix}")
            st.caption(f"Saved to tailored_resumes/{os.path.basename(saved)}")
            try:
                res_text = rp.read_resume_text(saved)
            except Exception:
                res_text = ltx.profile_plain_text(tailored)
            ats_pdf = ltx.compute_text_ats_match(res_text, j.get("description", ""))
            if ats_pdf:
                st.metric("Tailored résumé ATS match vs this JD", f"{ats_pdf['score']}%")
                if ats_pdf["missing"]:
                    st.caption("Missing keywords (add only if genuinely true): "
                               + ", ".join(ats_pdf["missing"][:20]))

    # Cover letter — GENERATED (truthful, ClimateAI-led) + the copyable AI prompt.
    with st.expander("✉️ Cover letter"):
      if not _has_resume:
        st.info("Upload a résumé in tab 1 · Resume first — the cover letter is built from your résumé.")
      else:
        # Merge the raw job (company/title/location) with its parsed skills/tools so the
        # letter has both the employer name AND the JD-matched skills.
        _parsed_cover = ats_mod.parse_job(j.get("description", ""), j.get("title", ""))
        _cl = tailor_mod.cover_letter(prof, {**j, **_parsed_cover})
        _cl_ok, _cl_viol = tailor_mod.validate_cover_letter(_cl, prof)
        if not _cl_ok:
            st.error("Honesty validator flagged the draft (not shown):")
            for _v in _cl_viol:
                st.write("•", _v)
        else:
            st.markdown("**Generated cover letter** — truthful (real résumé facts only), "
                        "leads with your strongest project. Review + edit before sending.")
            st.text_area("Cover letter", _cl, height=320, key=f"coverletter_{key_suffix}")
            cd1, cd2 = st.columns(2)
            with cd1:
                copy_button(_cl, "📋 Copy letter")
            with cd2:
                st.download_button("⬇️ Download .txt", _cl,
                                   file_name=pdf_gen.safe_filename(
                                       f"CoverLetter_{j.get('company','')}_{j.get('title','')}.txt"),
                                   mime="text/plain", key=f"cl_dl_{key_suffix}")
        st.divider()
        st.caption("Prefer an AI to write it? Copy this prompt instead:")
        _name = prof.get("name") or "Suryateja Orumpati"
        _match = ", ".join(sc.get("matched_skills", [])[:6]) or "Python, Flask, REST APIs"
        cover_prompt = (
            f"Write a concise (~250-word) cover letter for {_name}, an MS CS new grad "
            f"(University of Georgia, May 2026) applying to **{j.get('title','')}** at "
            f"**{j.get('company','')}**. Lead with the ClimateAI project (full-stack Python/Flask "
            f"app, 4 live REST APIs + Google Gemini, deployed). Highlight these matching skills: "
            f"{_match}. Note H1B sponsorship is needed. Tone: direct, specific, no fluff. "
            f"Output only the letter.")
        copy_button(cover_prompt, "📋 Copy cover-letter prompt")
        st.caption("Paste into Claude/ChatGPT, then review and edit before sending.")

    # Advanced LaTeX résumé workflow (kept, but out of the way)
    with st.expander("🧩 Advanced: AI LaTeX résumé (copy prompt → paste .tex → compile)"):
        tailor_prompt = ltx.build_tailor_prompt(prof, j, sc)
        if st.checkbox("Show LaTeX tailoring prompt", key=f"show_latex_prompt_{key_suffix}"):
            st.code(tailor_prompt, language="markdown")
        st.download_button(
            "⬇️ Download base LaTeX template", ltx.active_latex_template(prof),
            file_name="suryateja_base_resume_template.tex", mime="application/x-tex",
            key=f"download_base_tex_{key_suffix}")
        pasted_latex = st.text_area(
            "Paste AI-returned LaTeX résumé here", value=st.session_state.get(latex_code_key, ""),
            height=200, key=f"latex_area_{key_suffix}",
            placeholder="Paste the complete .tex returned by Claude/ChatGPT…")
        c_save_latex, c_score_latex = st.columns([1, 2])
        with c_save_latex:
            if st.button("💾 Save .tex", key=f"save_tex_{key_suffix}"):
                code = ltx.strip_latex_fences(pasted_latex)
                if len(code) < 120 or "\\documentclass" not in code:
                    st.error("Paste a complete LaTeX résumé with \\documentclass first.")
                else:
                    fname = pdf_gen.safe_filename(
                        f"suryateja_{j['company']}_{j['title']}_{date.today().isoformat()}.tex")
                    out = P(os.path.join(cfg["paths"]["tailored_resumes"], fname))
                    os.makedirs(os.path.dirname(out), exist_ok=True)
                    with open(out, "w", encoding="utf-8") as f:
                        f.write(code)
                    st.session_state[latex_code_key] = code
                    st.session_state[latex_path_key] = out
                    st.success(f"Saved tailored_resumes/{fname}")
        saved_latex = st.session_state.get(latex_code_key) or ltx.strip_latex_fences(pasted_latex)
        if saved_latex and "\\documentclass" in saved_latex:
            with c_score_latex:
                ats_latex = ltx.compute_latex_ats_match(saved_latex, j.get("description", ""))
                if ats_latex:
                    st.metric("LaTeX ATS keyword coverage", f"{ats_latex['score']}%")
                    if ats_latex["missing"]:
                        st.caption("Missing: " + ", ".join(ats_latex["missing"][:20]))
            st.download_button(
                "⬇️ Download tailored .tex", saved_latex,
                file_name=pdf_gen.safe_filename(f"suryateja_{j['company']}_{j['title']}.tex"),
                mime="application/x-tex", key=f"download_tex_{key_suffix}")
            if ltx.latex_engine():
                if st.button("🛠 Compile LaTeX → PDF", key=f"compile_tex_{key_suffix}"):
                    fname = pdf_gen.safe_filename(
                        f"Resume_{j['company']}_{j['title']}_{date.today().isoformat()}.pdf")
                    out = P(os.path.join(cfg["paths"]["tailored_resumes"], fname))
                    try:
                        with st.spinner("Compiling with tectonic…"):
                            ltx.compile_latex_to_pdf(saved_latex, out)
                        st.session_state[pdf_key] = out   # so '✅ I applied' can attach it
                        st.success(f"Compiled tailored_resumes/{fname}")
                    except Exception as e:
                        msg = str(e)
                        if "No LaTeX engine" in msg:
                            st.error("No LaTeX engine found:  `brew install tectonic`")
                        else:
                            st.error("LaTeX compile failed (a .tex syntax error). Paste this back "
                                     "to the AI to fix, then re-paste:")
                            st.code(msg[-1200:])
                compiled = st.session_state.get(pdf_key)
                if compiled and os.path.exists(compiled) and compiled.endswith(".pdf"):
                    for img in pdf_preview_pages(compiled, os.path.getmtime(compiled)):
                        st.image(img, width=520)
                    with open(compiled, "rb") as f:
                        st.download_button("⬇️ Download compiled PDF", f.read(),
                                           file_name=os.path.basename(compiled), mime="application/pdf",
                                           key=f"download_compiled_{key_suffix}")
            else:
                st.caption("💡 Install a LaTeX engine to compile here:  `brew install tectonic`")
        st.markdown("**Verify on a real ATS checker:**")
        ats_checker_links()

    st.divider()

    # ─────────────────── STEP 4 · Apply ───────────────────
    st.markdown("#### 4 · Apply")
    if j.get("job_link"):
        st.link_button("🔗 Apply — open posting in a new tab", j["job_link"], type="primary")
        st.caption("Apply on the site, then record it below.")
    else:
        st.warning("No application link available.")
    applied_file = st.file_uploader(
        "📎 Upload the EXACT résumé you applied with (filed & downloadable later)",
        type=["pdf", "docx", "tex", "txt"], key=f"applied_upload_{key_suffix}",
        help="The final résumé you actually submitted (e.g. the compiled PDF). Filed with this application.")
    notes = st.text_input("Notes (optional)", key=f"notes_{key_suffix}")
    xlsx_path = P(cfg["paths"]["tracker_xlsx"])
    dup = trk.find_duplicate(xlsx_path, j["company"], j["title"], j.get("job_link", ""))
    if dup:
        st.info(f"📌 Already in your tracker: **{dup.get('app_id','')}** — status "
                f"'{dup.get('status','')}' on {dup.get('applied_date','')}. "
                "Update it in the Tracker tab instead of re-adding.")
    dupok_key = f"dupok_{key_suffix}"
    if st.button("✅ I applied manually — record it", type="primary", key=f"applied_{key_suffix}"):
        if dup and not st.session_state.get(dupok_key):
            st.session_state[dupok_key] = True
            st.warning("⚠️ Looks like a **duplicate** (same company+title or link). "
                       "Click '✅ I applied manually' once more to record it anyway, or update the "
                       "existing row in the Tracker tab.")
            st.stop()
        st.session_state.pop(dupok_key, None)
        import shutil
        pdf_used = st.session_state.get(pdf_key, "")
        latex_used = st.session_state.get(latex_path_key, "")
        folder = P(os.path.join(cfg["paths"]["applied_jobs"],
                   pdf_gen.safe_filename(f"{j['company']}_{j['title']}_{date.today().isoformat()}")))
        saved_rel = ""
        # 1) the uploaded final resume wins; else fall back to generated pdf/latex
        if applied_file is not None:
            os.makedirs(folder, exist_ok=True)
            ext = os.path.splitext(applied_file.name)[1] or ".pdf"
            dest = os.path.join(folder, "applied_resume" + ext)
            with open(dest, "wb") as f:
                f.write(applied_file.getvalue())
            saved_rel = os.path.relpath(dest, BASE)
        else:
            resume_used = pdf_used or latex_used
            if resume_used and os.path.exists(resume_used):
                os.makedirs(folder, exist_ok=True)
                ext = os.path.splitext(resume_used)[1] or ".pdf"
                dest = os.path.join(folder, "applied_resume" + ext)
                shutil.copy(resume_used, dest)
                saved_rel = os.path.relpath(dest, BASE)
        record = {
            "company": j["company"], "job_title": j["title"],
            "location": j.get("location", ""), "job_link": j.get("job_link", ""),
            "source": j.get("source", "Manual"), "ats_score": sc["score"],
            "h1b_sponsor": item.get("h1b", False),
            "resume_file": saved_rel,   # relative path → downloadable from Tracker
            "applied": True, "status": "Applied", "notes": notes,
        }
        app_id = trk.append_application(P(cfg["paths"]["tracker_xlsx"]), record)
        if saved_rel:
            st.success(f"Recorded {app_id} and filed your applied resume. See it in 'Tracker'.")
        else:
            st.success(f"Recorded {app_id} in the tracker. "
                       "(No resume file attached — upload one above to keep a copy.)")


# ----------------------------- app ------------------------------------
st.set_page_config(page_title="Job Automation", page_icon="🎯", layout="wide")


def inject_css():
    """iOS-like polish: SF Pro system font, soft layered shadows, spring easing,
    press states, frosted cards. Pure CSS over Streamlit's stable data-testid
    selectors (Tailwind can't attach to Streamlit's compiled React components).
    Scoped to structure only — never styles generic label/span (so checkbox text
    stays plain, no highlight). Accent for widgets comes from .streamlit/config.toml."""
    st.markdown("""
    <style>
      :root { --accent:#2563eb; --accent-dark:#1d4ed8; --ring:rgba(37,99,235,.18);
              --card:#ffffff; --line:#e6e9ef; --ink:#0f172a; --muted:#64748b;
              --ease:cubic-bezier(.32,.72,0,1); }

      html, body, [class*="css"], [data-testid="stAppViewContainer"] {
          font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'SF Pro Text',
                       'Segoe UI', Inter, system-ui, sans-serif;
          -webkit-font-smoothing: antialiased; }
      .block-container { padding-top: 2.2rem; padding-bottom: 3.5rem; max-width: 1280px; }

      h1 { font-weight: 700; letter-spacing: -.03em; }
      h2, h3 { font-weight: 650; letter-spacing: -.015em; }

      /* Section nav → smooth segmented controls */
      .stTabs [data-baseweb="tab-list"] { gap: 4px; border-bottom: 1px solid var(--line); }
      .stTabs [data-baseweb="tab"] { height: 44px; padding: 0 18px; border-radius: 10px 10px 0 0;
          font-weight: 600; color: var(--muted); transition: all .25s var(--ease); }
      .stTabs [data-baseweb="tab"]:hover { color: var(--ink); background: rgba(37,99,235,.05); }
      .stTabs [aria-selected="true"] { color: var(--accent); background: rgba(37,99,235,.10); }
      [data-testid="stSegmentedControl"] { border-bottom: 1px solid var(--line); padding-bottom: 0; }
      [data-testid="stSegmentedControl"] [role="group"] { gap: 4px; }
      [data-testid="stSegmentedControl"] button {
          min-height: 44px; padding: 0 18px; border-radius: 10px 10px 0 0;
          font-weight: 600; transition: all .25s var(--ease); }

      /* Buttons → pill-ish, soft shadow, springy press (iOS feel) */
      .stButton > button, .stFormSubmitButton > button, .stDownloadButton > button {
          border-radius: 12px; font-weight: 600; padding: .55rem 1.15rem;
          border: 1px solid var(--line); background: var(--card); color: var(--ink);
          box-shadow: 0 1px 2px rgba(15,23,42,.05);
          transition: transform .18s var(--ease), box-shadow .18s var(--ease),
                      background .18s var(--ease), border-color .18s var(--ease); }
      .stButton > button:hover, .stDownloadButton > button:hover {
          border-color: var(--accent); color: var(--accent);
          box-shadow: 0 4px 14px rgba(15,23,42,.08); transform: translateY(-1px); }
      .stButton > button:active, .stFormSubmitButton > button:active,
      .stDownloadButton > button:active { transform: scale(.975); }
      .stButton > button[kind="primary"], .stFormSubmitButton > button {
          background: var(--accent); color: #fff; border: none;
          box-shadow: 0 4px 14px rgba(37,99,235,.32); }
      .stButton > button[kind="primary"]:hover, .stFormSubmitButton > button:hover {
          background: var(--accent-dark); color: #fff; transform: translateY(-1px);
          box-shadow: 0 6px 20px rgba(37,99,235,.4); }

      /* Path A form + expanders → crisp frosted cards (clearly visible border + depth) */
      [data-testid="stForm"] { background: var(--card); border: 1px solid var(--line);
          border-radius: 18px; padding: 1.4rem 1.6rem;
          box-shadow: 0 1px 3px rgba(15,23,42,.06), 0 12px 32px rgba(15,23,42,.05); }
      [data-testid="stExpander"] { border-radius: 14px; border: 1px solid var(--line);
          background: var(--card); box-shadow: 0 1px 2px rgba(15,23,42,.04);
          transition: box-shadow .2s var(--ease); }
      [data-testid="stExpander"]:hover { box-shadow: 0 4px 14px rgba(15,23,42,.07); }
      [data-testid="stExpander"] summary { font-weight: 600; }

      /* Inputs → rounded with a soft focus ring */
      [data-baseweb="input"], [data-baseweb="select"], [data-baseweb="textarea"] {
          border-radius: 11px; transition: box-shadow .18s var(--ease); }
      [data-baseweb="input"]:focus-within, [data-baseweb="select"]:focus-within,
      [data-baseweb="textarea"]:focus-within { box-shadow: 0 0 0 4px var(--ring); }

      /* Alerts → rounded, gentle */
      [data-testid="stAlert"] { border-radius: 14px; border: none;
          box-shadow: 0 1px 2px rgba(15,23,42,.05); }
    </style>
    """, unsafe_allow_html=True)


inject_css()
cfg = load_settings()
# Reuse raw board/ATS responses across searches in this session (15-min TTL) so a
# repeat search doesn't re-pull ~200 boards. Filtering still runs fresh every search.
src.enable_board_cache(900)
# Jobs are SESSION-ONLY and SEARCH-DRIVEN: a new page load starts with an empty
# list — we never restore previous-session / cached jobs from disk. Match & Score
# and Tailor & Apply only show jobs after you upload a résumé AND run Find Jobs
# this session (gated on `has_run_find_jobs`).
if "jobs" not in st.session_state:
    st.session_state["jobs"] = []
    st.session_state["jobs_restored"] = False
    st.session_state["has_run_find_jobs"] = False
st.session_state.setdefault("jobs_restored", False)
st.session_state.setdefault("has_run_find_jobs", False)
st.session_state.setdefault("match_source_filter", ALL_SOURCES_FILTER)
# 🔴/🟢 marks persist across reloads (they're your decisions, not session jobs).
st.session_state.setdefault("job_marks", load_marks(cfg))
# Résumé is SESSION-ONLY by design: a new page load NEVER auto-loads a saved résumé.
# Each browser session starts with no résumé until you upload one now — so a stale
# résumé is never reused for matching, scoring, tailoring, or cover letters. We also
# delete any leftover profile.json (older versions persisted it) to clear old data.
if "profile" not in st.session_state:
    st.session_state["profile"] = empty_profile()
    st.session_state["resume_ready"] = False
    st.session_state["uploaded_resume_name"] = ""
    _stale_pj = P(cfg["paths"]["profile_json"])
    if os.path.exists(_stale_pj):
        try:
            os.remove(_stale_pj)
        except Exception:
            _log_exc("startup: could not remove stale profile.json")
st.session_state.setdefault("resume_ready", False)
st.session_state.setdefault("uploaded_resume_name", "")
st.session_state.setdefault("uploaded_resume_fingerprint", "")

# Keep Find Jobs filter/source selections alive across tab navigation. Streamlit
# garbage-collects the state of any widget NOT rendered in a run, so the moment you
# switch to another tab (Find Jobs isn't drawn), these keys would be dropped and the
# filters reset to defaults on return. Re-assigning each key to itself every run —
# BEFORE any widget is instantiated — prevents that GC without changing the value.
# (active_tab is always rendered, so it doesn't need this and is left out.)
for _wk in list(st.session_state.keys()):
    if (_wk.startswith(("fj_", "cb_", "disc_", "pa_")) or
            _wk in ("loc_pref", "job_prefs")):
        st.session_state[_wk] = st.session_state[_wk]

st.title("🎯 Resume-to-Job Automation")
st.caption("Searches · scores · tailors · tracks — **never auto-applies.** You press the buttons.")
if not st.session_state.get("resume_ready"):
    st.warning("⚠️ **No resume uploaded.** Nothing is loaded automatically — upload your résumé in "
               "**tab 1 · Resume** to use it *this session* for matching, scoring, tailoring, and "
               "cover letters. (It is never saved or reused on a future page load.)")

TAB_LABELS = ["1 · Resume", "2 · Find Jobs", "3 · Match & Score", "4 · Tailor & Apply", "5 · Tracker"]
active_tab = st.segmented_control(
    "Section", TAB_LABELS, default=TAB_LABELS[0], key="active_tab",
    label_visibility="collapsed", width="stretch")
active_tab = active_tab or TAB_LABELS[0]

# ====================== TAB 1 · RESUME ================================
if active_tab == TAB_LABELS[0]:
    st.subheader("Your profile")
    up = st.file_uploader(
        "Upload your master resume (PDF, DOCX, TXT, or TEX)",
        type=["pdf", "docx", "txt", "tex"],
        key="resume_upload",
    )
    if up is not None:
        file_bytes = up.getvalue()
        fingerprint = hashlib.sha1(file_bytes).hexdigest()
        if fingerprint != st.session_state.get("uploaded_resume_fingerprint"):
            # Safe filename: sanitize the stem + add a short content hash so weird
            # characters can't break paths and re-uploads never silently overwrite.
            stem, ext = os.path.splitext(up.name)
            safe_name = f"{pdf_gen.safe_filename(stem) or 'resume'}_{fingerprint[:8]}{ext.lower()}"
            rel = os.path.join(cfg["paths"]["input_resume"], safe_name)
            dest = P(rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(file_bytes)
            try:
                prof = rp.parse_resume(dest)
                prof["resume_file"] = rel
                # Held in this SESSION only — deliberately NOT written to profile.json,
                # so it is never auto-reused on a future page load.
                st.session_state["profile"] = prof
                st.session_state["resume_ready"] = True
                st.session_state["resume_just_uploaded"] = True
                st.session_state["uploaded_resume_name"] = up.name
                st.session_state["uploaded_resume_fingerprint"] = fingerprint
                # A new/changed résumé invalidates any earlier matches — start clean
                # so jobs only ever reflect THIS résumé + a fresh Find Jobs run.
                st.session_state["jobs"] = []
                st.session_state["has_run_find_jobs"] = False
                reset_match_source_filter()
                st.session_state.pop("active_tailor_job", None)
                st.success(
                    f"Parsed {up.name} for this session "
                    f"({len(prof.get('skills', []))} skills, "
                    f"{len(prof.get('projects', []))} projects, "
                    f"{len(prof.get('experience', []))} experience item)."
                )
                if prof.get("latex_template"):
                    st.info("Saved this .tex resume as the active LaTeX template for AI tailoring prompts.")
            except Exception as e:
                _log_exc(f"parse_resume failed for {up.name}")
                st.error(
                    f"Couldn't parse **{up.name}** ({ext.lower() or 'unknown type'}). "
                    "Upload a **text-based** PDF, DOCX, TXT, or TEX résumé — a scanned-image "
                    "or photo PDF has no selectable text and can't be read. "
                    f"\n\nDetails: {e}")

    prof = st.session_state["profile"]
    if not st.session_state.get("resume_ready"):
        st.info("**No resume uploaded.** Upload one to use it this session — nothing is preloaded "
                "and no past résumé is ever auto-loaded.")
    else:
        cs1, cs2 = st.columns([4, 1])
        with cs1:
            _rn = (st.session_state.get("uploaded_resume_name")
                   or os.path.basename(prof.get("resume_file", "")) or "your résumé")
            st.success(f"✅ Using **{_rn}** for this session — matching, scoring, tailoring, cover "
                       "letters. Not saved; reload the page (or 🗑 Clear) and you'll start fresh.")
        with cs2:
            if st.button("🗑 Clear résumé"):
                clear_resume_state(cfg)
                st.rerun()
        display_uploaded_resume(prof)

        # ---- Parse-confidence warnings: tell the user when the parse looks thin ----
        # raw_text is dropped from the saved profile, so use raw_text_len when restored.
        _txt_len = prof.get("raw_text_len")
        if _txt_len is None:
            _txt_len = len(prof.get("raw_text", ""))
        _pwarn = []
        if _txt_len < 350 and str(prof.get("resume_file", "")).lower().endswith(".pdf"):
            _pwarn.append("Very little text was extracted — this may be a **scanned / image-only "
                          "PDF**. Re-export a **text-based** PDF (or upload DOCX/TXT) so matching works.")
        if not prof.get("skills"):
            _pwarn.append("**No skills were parsed** — ATS scoring and matching can't work. Check "
                          "that your résumé has a clear Skills section.")
        if not prof.get("experience") and not prof.get("projects"):
            _pwarn.append("**No work experience or projects were parsed** — tailoring will be thin. "
                          "Check the section headings (e.g. 'Work Experience', 'Projects').")
        if _pwarn:
            st.warning("⚠️ **Parse check — review before relying on matches:**\n\n"
                       + "\n".join(f"- {w}" for w in _pwarn))
        else:
            st.caption(f"✅ Parse check passed: {len(prof.get('skills', []))} skills · "
                       f"{len(prof.get('experience', []))} experience · "
                       f"{len(prof.get('projects', []))} projects parsed.")

        details_open = st.session_state.pop("resume_just_uploaded", False)
        with st.expander("Parsed profile details used for matching", expanded=details_open):
            st.markdown(f"**{prof.get('name','')}**")
            contact_bits = []
            if prof.get("email"):
                contact_bits.append(f"[{prof['email']}](mailto:{prof['email']})")
            if prof.get("phone"):
                contact_bits.append(prof["phone"])
            profile_links = _markdown_links(
                prof.get("links", {}),
                {"linkedin": "LinkedIn", "github": "GitHub", "portfolio": "Portfolio"},
            )
            if profile_links:
                contact_bits.append(profile_links)
            st.markdown(" · ".join(contact_bits) or "_No contact details parsed_")

            if prof.get("summary"):
                st.markdown("**Professional summary**")
                st.write(prof["summary"])

            if prof.get("skill_categories"):
                st.markdown("**Technical skills from resume**")
                for cat in prof.get("skill_categories", []):
                    st.markdown(f"- **{cat.get('category','')}**: {', '.join(cat.get('items', []))}")

            st.markdown("**Normalized skills used for ATS matching**")
            st.write(", ".join(prof.get("skills", [])) or "_none_")
            st.markdown("**Tools used for ATS matching**")
            st.write(", ".join(prof.get("tools", [])) or "_none_")
            st.markdown("**Domains**")
            st.write(", ".join(prof.get("domains", [])) or "_none_")
            if prof.get("latex_template"):
                st.markdown("**LaTeX template**")
                st.write("Active uploaded .tex template")

            st.markdown("**Work experience**")
            if prof.get("experience"):
                for e in prof.get("experience", []):
                    title = e.get("role") or "Experience"
                    company = e.get("company", "")
                    meta = " · ".join(x for x in [e.get("date", ""), e.get("location", "")] if x)
                    st.markdown(f"**{title}**" + (f" — *{company}*" if company else ""))
                    if meta:
                        st.caption(meta)
                    links = _markdown_links(e.get("links", {}), {"live": "Live", "github": "GitHub"})
                    if links:
                        st.markdown(links)
                    _render_section_bullets(e.get("bullets", []))
            else:
                st.write("_none_")

            st.markdown("**Projects**")
            if prof.get("projects"):
                for p in prof.get("projects", []):
                    title = p.get("name", "Project")
                    subtitle = p.get("subtitle", "")
                    meta = " · ".join(x for x in [p.get("date", ""), p.get("tech", "")] if x)
                    st.markdown(f"**{title}**" + (f" — *{subtitle}*" if subtitle else ""))
                    if meta:
                        st.caption(meta)
                    links = _markdown_links(p.get("links", {}), {"live": "Live", "github": "GitHub"})
                    if links:
                        st.markdown(links)
                    _render_section_bullets(p.get("bullets", []))
            else:
                st.write("_none_")

            st.markdown("**Education**")
            if prof.get("education"):
                for edu in prof.get("education", []):
                    st.markdown(f"**{edu.get('school','')}** — {edu.get('location','')}")
                    details = " · ".join(x for x in [edu.get("degree", ""), edu.get("detail", ""), edu.get("dates", "")] if x)
                    if details:
                        st.write(details)
            else:
                st.write("_none_")

            st.markdown("**Additional**")
            if prof.get("additional"):
                _render_section_bullets(prof.get("additional", []))
            else:
                st.write("_none_")

# ====================== TAB 2 · FIND JOBS =============================
if active_tab == TAB_LABELS[1]:
    st.subheader("🔍 Find Jobs")

    # GATE: no résumé this session → no fetching, no scoring, no jobs. The search
    # controls render but are DISABLED, and the run handlers below are gated too.
    _resume_ok = session_resume_ready()
    if not _resume_ok:
        st.warning(f"⚠️ {RESUME_REQUIRED_MSG}")
        st.caption("Upload it in **tab 1 · Resume**. Jobs are matched/scored against the "
                   "résumé you upload this session — nothing is fetched without one.")

    catalog = load_source_catalog()
    _disc_ready = src.discovery_available(cfg)
    adz_cfg = cfg.get("adzuna", {}) or {}
    _adzuna_ready = bool(adz_cfg.get("app_id") and adz_cfg.get("app_key"))
    _job_api_ready = agg.job_api_fallback_available(cfg)

    # ═══════════════════════════════════════════════════════════════════
    # PATH A — Auto Search
    # ═══════════════════════════════════════════════════════════════════
    st.markdown("""<div style="background:#1a3a5c;padding:10px 16px;border-radius:8px;margin:8px 0">
<span style="color:white;font-size:17px;font-weight:700">🅐 Path A — Search / Pull Jobs Automatically</span><br>
<span style="color:#aad4ff;font-size:12px">Sponsor boards + Job API (SerpApi → JSearch → Careerjet → Jooble) + Discovery</span>
</div>""", unsafe_allow_html=True)

    # Default focus = new-grad level + the user's resume specializations
    # (backend / full-stack), so 'Backend Engineer' / 'Full Stack Developer'
    # titles are KEPT by the focus gate, not rejected as un-selected specializations.
    # NOTE: never coerce st.session_state filter keys here — Streamlit reruns this
    # script on every interaction, so any 'migration' that rewrites those keys
    # silently undoes the user's choices on every rerun (this previously wiped the
    # source checkboxes and snapped freshness/max-years back, starving every search).
    default_focus = [k for k in ("newgrad", "backend", "fullstack")
                     if k in roles.ROLE_FOCUS]
    focus_opts = [k for k in roles.ROLE_FOCUS if k != "general"] + ["general"]

    # Pre-compute disc sources so the toggle callback (outside the form) can reference them.
    _disc_sources_all = [(v["label"], k) for k, v in catalog.items()
                        if v.get("mode") == "discovery"]

    def _disc_toggle_changed():
        _val = st.session_state.get("disc_toggle_all", True)
        for _, _dk in _disc_sources_all:
            st.session_state[f"disc_{_dk}"] = (_val and src.discovery_available(cfg))

    _dtc1, _dtc2 = st.columns([3, 1])
    with _dtc2:
        st.toggle(
            "🌐 Discovery: all on/off",
            key="disc_toggle_all",
            value=True,
            disabled=not src.discovery_available(cfg),
            on_change=_disc_toggle_changed,
            help="Turn all discovery site checkboxes on or off instantly.")

    # The filters + sources live in a FORM: nothing reruns until "Search selected
    # sources" is pressed. That keeps editing the role-focus chips / checkboxes from
    # triggering a rerun (which was bouncing the active tab) — and it's much smoother.
    with st.form("path_a_form", border=True):
        pa_left, pa_right = st.columns([1, 1], gap="large")

        # ── Panel 1 · Search Filters ────────────────────────────────────
        with pa_left:
            st.markdown("**🎛️ Search Filters**")
            focus_keys = st.multiselect(
                "Role focus", options=focus_opts, default=default_focus, key="fj_focus",
                format_func=lambda k: roles.ROLE_FOCUS[k]["label"],
                help="What you're hunting. Pick several to widen. Empty = general SWE.")

            pf1, pf2 = st.columns(2)
            with pf1:
                work_mode = st.selectbox("Work mode", ["Any", "Remote", "Hybrid", "Onsite"],
                                         key="fj_work_mode")
            with pf2:
                posted_label = st.selectbox(
                    "Posted within", list(POSTED_WINDOWS.keys()), key="fj_posted",
                    index=list(POSTED_WINDOWS.keys()).index("Past 7 days (1 week)"))

            pf3, pf4 = st.columns(2)
            with pf3:
                loc_pref = st.text_input("Location", "Remote, US", key="loc_pref")
            with pf4:
                prefs = st.text_input("Extra keywords", "", key="job_prefs",
                                      placeholder="Java, fintech, AWS…")

            pf5, pf6, pf7 = st.columns(3)
            with pf5:
                h1b_only = st.checkbox("H1B only", value=False, key="fj_h1b",
                                       help="⚠️ Restricts to companies in the H1B DB. Verify before applying.")
            with pf6:
                new_grad_only = st.checkbox("Entry-level only", value=True, key="fj_entry",
                                            help="Drops senior/staff/lead titles and over-experience roles.")
            with pf7:
                max_years = st.slider("Max yrs exp", 0, 5,
                                      int(cfg.get("search_max_years", 2)),
                                      key="fj_maxyears")

            with st.expander("⚙️ Pull options"):
                adv1, adv2, adv3 = st.columns(3)
                with adv1:
                    pa_floor = st.slider("Relevance floor", 0, 80, 45, step=5, key="pa_floor",
                                         help="Drop roles below this résumé-match score (0 = keep all).")
                with adv2:
                    pa_verify = st.checkbox("Verify links", value=True, key="pa_verify",
                                            help="HTTP-checks each link and flags closed/404 postings.")
                with adv3:
                    pa_fresh = st.checkbox("Fresh (replace)", value=True, key="pa_fresh",
                                           help="Replace the current job list instead of appending.")

            st.markdown("**📊 API Quotas**")
            _sq2 = serpapi_quota_cached(cfg.get("serpapi",{}).get("api_key",""), int(time.time()//600))
            _sl2 = _sq2.get("left", "?")
            _dd = src._discovery_config(cfg)
            _rows = [
                ("SerpApi",   f"{_sl2}/250" if isinstance(_sl2,int) else "?", "⚠️" if isinstance(_sl2,int) and _sl2<20 else "✅"),
                ("JSearch",   "~500/mo (free tier)" if os.getenv("JSEARCH_RAPIDAPI_KEY") else "—", "✅" if os.getenv("JSEARCH_RAPIDAPI_KEY") else "❌"),
                ("Careerjet", "unlimited (affiliate)" if os.getenv("CAREERJET_AFFID") else "—",  "✅" if os.getenv("CAREERJET_AFFID") else "❌"),
                ("Jooble",    "500 lifetime" if os.getenv("JOOBLE_API_KEY") else "—", "✅" if os.getenv("JOOBLE_API_KEY") else "❌"),
                ("Adzuna",    "~250/mo" if os.getenv("ADZUNA_APP_ID") else "—", "✅" if os.getenv("ADZUNA_APP_ID") else "❌"),
                ("Discovery", _dd.get("provider","none"), "✅" if src.discovery_available(cfg) else "❌"),
            ]
            for _name, _val, _icon in _rows:
                st.caption(f"{_icon} **{_name}**: {_val}")

        # ── Panel 2 · Job Sources ───────────────────────────────────────
        with pa_right:
            st.markdown("**📦 Job Sources**")

            st.markdown("🏢 **Core sponsor boards**")
            st.caption("Greenhouse · Lever · Ashby boards from config/target_companies.txt")
            src1, src2, src3, src4 = st.columns(4)
            with src1:
                cb_boards = st.checkbox("217 sponsor boards", value=True, key="cb_boards")
            with src2:
                cb_muse = st.checkbox("The Muse", value=False, key="cb_muse")
            with src3:
                cb_adzuna = st.checkbox(
                    "Adzuna" + (" ✅" if _adzuna_ready else " 🔒"), value=False,
                    key="cb_adzuna", disabled=not _adzuna_ready,
                    help="Free aggregator API. Add app_id/app_key in settings.yaml to enable.")
            with src4:
                cb_jsearch = st.checkbox(
                    "Job API fallback" + (" ✅" if _job_api_ready else " 🔒"), value=False,
                    key="cb_jsearch", disabled=not _job_api_ready,
                    help=("Uses one limited provider per run, in priority order: "
                          "SerpApi → OpenWeb JSearch → RapidAPI JSearch → Tavily fallback."))
            _serp_cfg = cfg.get("serpapi", {}) or {}
            if _serp_cfg.get("api_key"):
                _q = serpapi_quota_cached(_serp_cfg["api_key"], int(time.time() // 600))
                if _q.get("left") is not None:
                    _left = _q["left"]
                    st.caption(("⚠️ " if _left < 15 else "") +
                               f"SerpApi: **{_left}** searches left this month"
                               + (f" of {_q['total']}" if _q.get("total") else ""))
            if src.discovery_available(cfg) and src._discovery_config(cfg).get("provider") == "google_pse":
                _gpse = src.google_pse_usage_today()
                _grem = _gpse["remaining"]
                st.caption(("⚠️ " if _grem < 20 else "") +
                           f"Google PSE: **{_grem}** / 100 free queries left today"
                           + (" — resets midnight UTC" if _grem < 20 else ""))
            srcr1, _srcr2 = st.columns([2, 2])
            with srcr1:
                cb_remote = st.checkbox(
                    "🌐 Remote APIs ✅", value=False, key="cb_remote",
                    help="Free, no-key remote SWE jobs with FULL JDs (Remotive + RemoteOK). "
                         "Remote-skewed — fewer big H1B sponsors, but real entry-level volume.")

            st.markdown("🏭 **Workday / SmartRecruiters ATS**")
            st.caption("NVIDIA · Adobe · Salesforce · Mastercard · Visa · Bosch…")
            src4, src5 = st.columns(2)
            with src4:
                cb_workday = st.checkbox("Workday", value=False, key="cb_workday")
            with src5:
                cb_sr = st.checkbox("SmartRecruiters", value=False, key="cb_sr")

            st.markdown("🌐 **Discovery websites**" + (" ✅" if _disc_ready else " 🔒"))
            _disc_prov = src._discovery_config(cfg).get("provider", "")
            _disc_prov_label = {"google_pse": "Google PSE (100 free queries/day)",
                                "brave": "Brave Search", "bing": "Bing",
                                "tavily": "Tavily", "serpapi": "SerpApi"}.get(_disc_prov,
                                                                              _disc_prov or "a search API")
            st.caption(f"Uses **{_disc_prov_label}** to find leads on each site. Grouped labels "
                       "(Startup boards · H1B/OPT boards · New-grad aggregators) bundle many niche "
                       "sites into ONE query slot. Each ticked label costs ~6 queries per run — "
                       "fewer ticked = more runs per day. "
                       "🔒 = login-walled, manual only (reach via AI-paste)." if _disc_ready
                       else "🔒 Add a search key (Google PSE / Brave / Tavily) to settings.yaml → "
                            "discovery: to enable.")
            _supported_disc = set(src.supported_discovery_labels())
            _disc_sources = _disc_sources_all
            _disc_col1, _disc_col2 = st.columns(2)
            _disc_selected = {}
            _disc_toggle_cur = st.session_state.get("disc_toggle_all", True)
            for i, (label, key) in enumerate(_disc_sources):
                col = _disc_col1 if i % 2 == 0 else _disc_col2
                if label in _supported_disc:
                    _disc_selected[label] = col.checkbox(
                        label, value=(_disc_ready and _disc_toggle_cur),
                        key=f"disc_{key}", disabled=not _disc_ready)
                else:
                    continue

            st.markdown("**🔒 Manual-only discovery links**")
            _manual_query = " ".join(x for x in ["software engineer new grad", prefs] if x).strip()
            _manual_url_map = dict(search_links(_manual_query, loc_pref))
            _locked_links = [
                ("Handshake", _manual_url_map.get("Handshake", "https://joinhandshake.com/")),
                ("SubmitX AI", _manual_url_map.get("SubmitX AI", "https://submitx.ai/")),
                ("Hired", _manual_url_map.get("Hired", "https://hired.com/jobs")),
                ("Hire Tech", _manual_url_map.get("Hire Tech", "https://www.google.com/search?q=software+engineer+new+grad+%22hire+technologies%22+jobs")),
            ]
            _locked_cols = st.columns(2)
            for i, (label, url) in enumerate(_locked_links):
                _locked_cols[i % 2].markdown(f"[🔒 {label}]({url})")

        _btn_label = ("🔭 Search selected sources" if _resume_ok
                      else "🔒 Upload a résumé first to search")
        do_path_a = st.form_submit_button(_btn_label, type="primary",
                                          use_container_width=True, disabled=not _resume_ok)
        if not _resume_ok:
            st.caption("⬆️ This button is locked because **no résumé is loaded this session**. "
                       "Go to **tab 1 · Resume**, upload yours, then come back. (Résumés aren't "
                       "saved between page loads — re-upload after a refresh.)")

    focus_keys = focus_keys or ["general"]
    posted_hours = POSTED_WINDOWS[posted_label]
    st.session_state["focus_keys"] = focus_keys
    st.session_state["work_mode"] = work_mode
    st.session_state["h1b_only"] = h1b_only
    st.session_state["strict_us_only"] = True

    cap1, cap2 = st.columns([4, 1])
    with cap1:
        st.caption(f"**{', '.join(roles.labels_for(focus_keys))}** · {work_mode} · "
                   f"{'H1B only' if h1b_only else 'H1B preferred'} · "
                   f"≤{max_years} yrs · {posted_label.lower()}")
    with cap2:
        if st.button("🗑 Clear list", use_container_width=True):
            st.session_state["jobs"] = []
            st.session_state["has_run_find_jobs"] = False
            st.session_state.pop("last_search_summary", None)
            reset_match_source_filter()
            st.session_state.pop("active_tailor_job", None)
            persist_jobs(cfg)
            st.success("Cleared.")

    if do_path_a and _resume_ok:
        reset_match_source_filter()
        st.session_state["has_run_find_jobs"] = True   # a current-session search ran
        disc_labels = [lbl for lbl, on in _disc_selected.items() if on]
        selected = {
            "sponsor_boards": cb_boards,
            "themuse": cb_muse,
            "adzuna": cb_adzuna and _adzuna_ready,
            "jsearch": cb_jsearch and _job_api_ready,
            "remote_apis": cb_remote,
            "workday": cb_workday,
            "smartrecruiters": cb_sr,
            "discovery_labels": disc_labels,
        }
        if not any([selected["sponsor_boards"], selected["themuse"], selected["adzuna"],
                    selected["jsearch"], selected["remote_apis"], selected["workday"],
                    selected["smartrecruiters"], disc_labels]):
            st.warning("⚠️ Select at least one job source.")
        else:
            filters_pa = {
                "focus_keys": focus_keys, "new_grad_only": new_grad_only,
                "max_years": max_years, "max_age_hours": posted_hours,
                "location": loc_pref, "h1b_only": h1b_only,
            }
            with st.spinner("Searching selected sources…"):
                result = agg.search_selected_sources(cfg, filters_pa, selected)
            if pa_fresh:
                st.session_state["jobs"] = []
                reset_match_source_filter()
                st.session_state.pop("active_tailor_job", None)
            n = _add_jobs(result["jobs"], cfg, min_relevance=pa_floor,
                          max_age_hours=posted_hours)
            dropped = st.session_state.get("_last_dropped_relevance", 0)
            dropped_loc = st.session_state.get("_last_dropped_location", 0)
            dead = 0
            if pa_verify and st.session_state["jobs"]:
                with st.spinner("Verifying links…"):
                    src.verify_links([it["job"] for it in st.session_state["jobs"]], max_workers=24)
                dead = sum(1 for it in st.session_state["jobs"]
                           if it["job"].get("link_live") is False)
                persist_jobs(cfg)
            c = result["counts"]
            h1b_likely = sum(1 for it in st.session_state["jobs"] if it.get("h1b"))
            # Build per-source summary
            summary_lines = []
            for key, lbl in (("boards", "Sponsor boards"), ("the_muse", "The Muse"),
                             ("adzuna", "Adzuna"), ("jsearch", "Job API fallback"),
                             ("remote_apis", "Remote APIs (Remotive · RemoteOK)"),
                             ("workday", "Workday"),
                             ("smartrecruiters", "SmartRecruiters"),
                             ("discovery_leads", "Discovery leads")):
                if key in c:
                    summary_lines.append(f"- {lbl}: **{c[key]}**")
            if c.get("job_api_provider"):
                summary_lines.append(f"- Job API provider used: **{c['job_api_provider']}**")
            if c.get("job_api_attempted") and c.get("job_api_attempted") != c.get("job_api_provider"):
                summary_lines.append(f"- Job API attempted: **{c['job_api_attempted']}**")
            # Discovery V2 diagnostics: how many leads became full-JD jobs vs stayed
            # third-party leads that need you to verify the exact posting.
            if "discovery_leads" in c:
                summary_lines.append(
                    f"  - ↳ official ATS: **{c.get('discovery_official', 0)}** "
                    f"(full JD: **{c.get('discovery_enriched', 0)}**) · "
                    f"third-party (verify link): **{c.get('discovery_third_party', 0)}**")
            summary_lines += [
                f"- Fetched: **{c.get('fetched', 0)}**",
                f"- After dedupe: **{c.get('after_merge', 0)}**",
                f"- Added after filters/scoring: **{n}**",
                f"- 🛂 H1B-likely: **{h1b_likely}**",
            ]
            if dropped:
                summary_lines.append(f"- Below relevance floor: **{dropped}**")
            if dropped_loc:
                summary_lines.append(f"- Clearly non-US location dropped: **{dropped_loc}**")
            if dead:
                summary_lines.append(f"- Dead links demoted: **{dead}**")
            _summary_md = "**Searched:**\n" + "\n".join(summary_lines)
            # Persist so returning to this tab re-shows the outcome (it isn't wiped).
            st.session_state["last_search_summary"] = _summary_md
            # Toast = instant confirmation that survives the rerun's scroll-to-top,
            # so a search never feels like it "jumped to another tab".
            st.toast(f"✅ Found {n} job(s) → open Match & Score", icon="🎯")
            st.success("✅ Search complete → open **Match & Score**\n\n" + _summary_md)
            # Discovery selected but nothing came back. With the new resolver the usual
            # cause is a too-tight freshness window or strict role filters — not rate
            # limits (the throttle+backoff keeps the burst under the free tier).
            if disc_labels and not selected.get("jsearch") and c.get("discovery_leads", 0) == 0:
                st.info("🌐 Discovery returned **0 leads** this run. Most likely the "
                        "**'Posted within' window is too tight** (try Past 3–7 days) or the "
                        "role focus is narrow. If it persists, the search provider's free "
                        "quota may be exhausted for today (Google PSE resets at midnight PT) "
                        "— check the expander above. Direct APIs + Workday are unaffected.")
            if set(focus_keys) & {"newgrad", "newgrad_swe", "entry_swe", "junior_dev",
                                  "associate_swe", "swe_i", "new_college_grad_swe"}:
                _ng = sum(1 for it in st.session_state["jobs"] if it["job"].get("is_new_grad"))
                if _ng < 5:
                    st.info(f"🎓 Only {_ng} genuine new-grad-titled role(s) found — these peak "
                            "Aug–Nov. Try widening 'Posted within' to 'Any time' for more.")
            if result["errors"]:
                with st.expander(f"⚠️ {len(result['errors'])} source(s) failed"):
                    for e in result["errors"][:50]:
                        st.caption(e)
    elif _resume_ok and st.session_state.get("has_run_find_jobs") and \
            st.session_state.get("last_search_summary"):
        # Returning to Find Jobs after a search (no new run this rerun) → re-show the
        # outcome so navigating between tabs never looks like the results were wiped.
        # The jobs live in Match & Score until you run a NEW search or reload the page.
        _added = len(st.session_state.get("jobs", []))
        st.success(f"✅ Your last search is still loaded — **{_added} job(s)** in "
                   f"**Match & Score**.\n\n" + st.session_state["last_search_summary"])
        st.caption("These stay until you run a **new** search (Fresh = replace) or **reload "
                   "the page**. Re-running Find Jobs updates Match & Score automatically.")

    # A clean, short keyword for the browser searches (the full target-role list is
    # repetitive and overflows the keyword box). Use the first role + 'new grad' + prefs.
    _base_role = (roles.target_roles_for(focus_keys)[:1] or ["software engineer"])[0]
    _base_role = re.sub(r"\b(new grad|entry[- ]level|new college grad)\b", "", _base_role,
                        flags=re.I).strip() or "software engineer"
    link_query = " ".join(x for x in [_base_role, "new grad", prefs] if x).strip()

    # ═══════════════════════════════════════════════════════════════════
    # PATH B — AI Search → Paste JSON
    # ═══════════════════════════════════════════════════════════════════
    st.markdown("""<div style="background:#3a1a5c;padding:10px 16px;border-radius:8px;margin:8px 0">
<span style="color:white;font-size:17px;font-weight:700">🅑 Path B — AI Search → Paste JSON</span><br>
<span style="color:#ddbfff;font-size:12px">Use Claude.ai / ChatGPT with web search → paste results back (widest reach)</span>
</div>""", unsafe_allow_html=True)
    st.caption("Use when you want Claude.ai / ChatGPT (with web search) to find jobs across "
               "LinkedIn, Indeed, Glassdoor, Dice, Handshake, Wellfound, OPTnation, and more. "
               "Copy the prompt, run it in any AI with web search, paste the JSON back here.")

    prompt = prompts.build_job_search_prompt(
        st.session_state["profile"], prefs=prefs, location=loc_pref, max_years=max_years,
        role_labels=roles.labels_for(focus_keys),
        target_roles=roles.target_roles_for(focus_keys),
        core_skills=roles.core_skills_for(focus_keys),
        work_mode=work_mode, h1b_only=h1b_only,
        resume_text=get_resume_text(cfg, st.session_state["profile"]),
        freshness_label=freshness_label_for_prompt(posted_hours))

    pb1, pb2 = st.columns([2, 3])
    with pb1:
        copy_button(prompt)
        with st.expander("📋 Show the prompt"):
            st.code(prompt, language="markdown")
        with st.expander("🔗 Open discovery sites in browser"):
            st.caption("Open a pre-filled search on each site → find a role → paste its link above.")
            _bcols = st.columns(4)
            for i, (label, url) in enumerate(search_links(link_query, loc_pref)):
                _bcols[i % 4].link_button(label, url, width="stretch")
    with pb2:
        pasted = st.text_area("Paste the AI's JSON here", height=200, key="ai_json")
        if st.button("📥 Import pasted jobs", type="primary", disabled=not _resume_ok):
            jobs, stats = src.normalize_pasted_jobs(
                pasted, new_grad_only=new_grad_only, max_years=max_years, focus_keys=focus_keys)
            if stats.get("error"):
                st.error(stats["error"])
            elif stats.get("empty"):
                st.info("The AI returned an **empty list** — no jobs passed its rules this run. "
                        "That usually means the freshness window was too strict; re-run the "
                        "prompt with a wider 'Posted within' or broader role focus.")
            else:
                with st.spinner(f"Verifying {len(jobs)} links…"):
                    src.verify_links(jobs)
                reset_match_source_filter()
                n = _add_jobs(jobs, cfg)
                st.session_state["has_run_find_jobs"] = True
                bits = []
                if stats.get("filtered_senior"):
                    bits.append(f"{stats['filtered_senior']} senior/mid filtered")
                if stats.get("filtered_focus"):
                    bits.append(f"{stats['filtered_focus']} off-focus filtered")
                dead = sum(1 for j in jobs if j.get("link_live") is False)
                st.success(f"Imported **{n} roles**"
                           + (f" · {', '.join(bits)}" if bits else "") + " → Match & Score")
                if dead:
                    st.warning(f"{dead} links look closed/404 — flagged in Match & Score.")
                if stats.get("link_warnings"):
                    st.warning(f"{stats['link_warnings']} role(s) have search-like links — verify before applying.")

    st.divider()

    # ═══════════════════════════════════════════════════════════════════
    # PATH C — Import Overnight Pull (cron / scheduled)
    # ═══════════════════════════════════════════════════════════════════
    st.markdown("""<div style="background:#1a3a2a;padding:10px 16px;border-radius:8px;margin:8px 0">
<span style="color:white;font-size:17px;font-weight:700">🅒 Path C — Import Overnight Pull</span><br>
<span style="color:#aaffcc;font-size:12px">Jobs fetched by cron script overnight → score against your résumé now</span>
</div>""", unsafe_allow_html=True)
    # 📥 Import an overnight pull (scripts/scheduled_pull.py via cron). The script fetches
    # RAW jobs to logs/overnight_jobs.json; we score them HERE against your session résumé
    # (so nothing is scored/stored without a résumé this session).
    _ov_path = P(os.path.join(cfg["paths"].get("logs", "logs"), "overnight_jobs.json"))
    if os.path.exists(_ov_path):
        try:
            with open(_ov_path, encoding="utf-8") as _ovf:
                _ov = json.load(_ovf)
            _ov_jobs = _ov.get("jobs", []) if isinstance(_ov, dict) else []
        except Exception:
            _ov_jobs = []
        if _ov_jobs:
            _ov_when = (_ov.get("fetched_at", "") or "")[:16].replace("T", " ")
            if st.button(f"📥 Import overnight pull ({len(_ov_jobs)} jobs from {_ov_when})",
                         disabled=not _resume_ok,
                         help="Scores the cron-fetched jobs against your résumé this session."
                              if _resume_ok else "Upload a résumé first."):
                reset_match_source_filter()
                st.session_state["has_run_find_jobs"] = True
                n_imp = _add_jobs(_ov_jobs, cfg, min_relevance=0, max_age_hours=None)
                st.session_state["last_search_summary"] = f"**Imported overnight pull:** {n_imp} job(s) added."
                st.toast(f"✅ Imported {n_imp} overnight job(s) → Match & Score", icon="🌙")
                st.success(f"✅ Imported **{n_imp}** overnight job(s) → open **Match & Score**.")

    st.markdown("""<div style="background:#3a2a1a;padding:10px 16px;border-radius:8px;margin:8px 0">
<span style="color:white;font-size:17px;font-weight:700">🅓 Path D — Manual Paste</span><br>
<span style="color:#ffd9aa;font-size:12px">Found a job while browsing? Paste description + link → scored instantly</span>
</div>""", unsafe_allow_html=True)
    with st.expander("➕ Add one job manually"):
        with st.form("manual_job"):
            m1, m2 = st.columns(2)
            with m1:
                m_title = st.text_input("Job title")
                m_company = st.text_input("Company")
            with m2:
                m_loc = st.text_input("Location")
                m_link = st.text_input("Job link (URL)")
            m_desc = st.text_area("Paste the job description", height=120)
            if st.form_submit_button("➕ Add this job", type="primary", disabled=not _resume_ok):
                j = src.manual(m_title, m_company, m_loc, m_link, m_desc)
                if m_link:
                    with st.spinner("Checking link…"):
                        src.verify_links([j])
                reset_match_source_filter()
                _add_jobs([j], cfg)
                st.session_state["has_run_find_jobs"] = True
                if j.get("link_live") is False:
                    st.warning("Added, but the link looks closed or unreachable. Verify it.")
                else:
                        st.success("Added → Match & Score")

# ====================== TAB 3 · MATCH & SCORE ========================
if active_tab == TAB_LABELS[2]:
    hc1, hc2 = st.columns([3, 1])
    with hc1:
        st.subheader("Match & score")
    with hc2:
        if st.button("🔄 Refresh", width="stretch",
                     help="Re-render the jobs from your current Find Jobs run."):
            st.rerun()   # session-only: never reloads cached/previous-session jobs from disk
    jobs = st.session_state["jobs"]
    _resume_ok = session_resume_ready()
    _searched = bool(st.session_state.get("has_run_find_jobs"))
    if _resume_ok and _searched and jobs:
        st.caption(f"**{len(jobs)}** job(s) from your current Find Jobs run.")

    # ---- Save / load named batches (apply to a saved set later) ----
    with st.expander("💾 Save / load a batch (keep this set to apply to later)"):
        bc1, bc2 = st.columns(2)
        with bc1:
            _bname = st.text_input("Batch name", placeholder="e.g. Atlanta backend, week of Jun 2",
                                   key="batch_name")
            if st.button("💾 Save current list as a batch", disabled=not jobs):
                saved = save_batch(cfg, _bname or f"batch_{date.today().isoformat()}", jobs)
                if saved:
                    st.success(f"Saved batch **{saved}** ({len(jobs)} jobs). Load it anytime below.")
                else:
                    st.error("Could not save the batch (see logs/app_errors.log).")
        with bc2:
            _batches = list_batches(cfg)
            if _batches:
                _pick = st.selectbox("Saved batches", _batches, key="batch_pick")
                lb1, lb2 = st.columns(2)
                if lb1.button("📂 Load (replace list)", disabled=not _resume_ok):
                    loaded = load_batch(cfg, _pick)
                    if loaded:
                        st.session_state["jobs"] = loaded
                        st.session_state["jobs_restored"] = True
                        st.session_state["has_run_find_jobs"] = True   # explicit load this session
                        reset_match_source_filter()
                        st.session_state.pop("active_tailor_job", None)
                        persist_jobs(cfg)
                        st.success(f"Loaded **{_pick}** ({len(loaded)} jobs).")
                        st.rerun()
                    else:
                        st.error("That batch was empty or unreadable.")
                if lb2.button("🗑 Delete batch"):
                    delete_batch(cfg, _pick)
                    st.rerun()
            else:
                st.caption("No saved batches yet. Save the current list on the left.")

    if not _resume_ok:
        # No résumé this session → never display matched jobs (even restored ones).
        st.warning(f"⚠️ {RESUME_REQUIRED_MSG}")
        st.caption("Upload it in **tab 1 · Resume**, then search in **Find Jobs**. "
                   "Jobs are scored against the résumé you upload this session.")
    elif not _searched:
        # No current-session search yet → show empty state, never cached/old jobs.
        st.info("**No matched jobs yet. Upload your resume and run Find Jobs first.**")
    elif not jobs:
        # A search DID run this session but everything was filtered out — say so,
        # so it never looks like the click didn't register.
        _ds = st.session_state.get("_last_dropped_stale", 0)
        _dr = st.session_state.get("_last_dropped_relevance", 0)
        st.warning("**Your Find Jobs search ran, but every result was filtered out.** "
                   f"Dropped: **{_ds}** as too old (freshness window), **{_dr}** below the "
                   "résumé-relevance floor.")
        st.caption("Try widening **'Posted within'** (e.g. Past 7 days) or **'Max yrs exp'**, "
                   "lower the relevance floor in Find Jobs → Pull options, or select more sources, "
                   "then search again.")
    else:
        if st.session_state.get("jobs_restored"):
            _fetched = [it.get("fetched_at") for it in jobs if it.get("fetched_at")]
            _when = max(_fetched)[:10] if _fetched else "a previous session"
            _age = ""
            try:
                _days = (date.today() - date.fromisoformat(max(_fetched)[:10])).days
                _age = (" (today)" if _days == 0 else f" ({_days} day{'s' if _days != 1 else ''} ago)")
            except Exception:
                pass
            st.warning(f"↩️ These results were **pulled {_when}{_age}** and restored from your last "
                       "session — postings and links may now be stale. **Re-pull** in 'Find Jobs' or "
                       "click **'Verify all links'** below before applying.")
        # ---------------- Filters + sort (instant, no re-pull needed) ----------------
        _ng_total = sum(1 for it in jobs if it["job"].get("is_new_grad"))
        _source_counts = source_counts_for_jobs(jobs)
        _source_options = [ALL_SOURCES_FILTER] + sorted(_source_counts, key=str.lower)
        if st.session_state.get("match_source_filter") not in _source_options:
            reset_match_source_filter()

        def _source_option_label(label):
            count = len(jobs) if label == ALL_SOURCES_FILTER else _source_counts.get(label, 0)
            return f"{label} ({count})"

        fc1, fc2, fc3, fc4, fc5, fc6 = st.columns([1.35, 1.1, 1, 1, 0.9, 1])
        SORT_OPTIONS = ["Priority (recommended)", "Résumé ↔ JD keyword match",
                        "Estimated ATS match (résumé ↔ role)", "AI fit score"]
        with fc1:
            sort_by = st.selectbox("Sort by", SORT_OPTIONS, index=0)
        with fc2:
            source_filter = st.selectbox(
                "Source", _source_options, key="match_source_filter",
                format_func=_source_option_label,
                help="Filter the jobs already fetched in this Find Jobs run. This does not refetch.")
        with fc3:
            wm_filter = st.selectbox("Work mode", ["Any", "Remote", "Hybrid", "Onsite"],
                                     index=0)
        with fc4:
            min_score = st.slider("Min priority score", 0, 100, 0, step=5)
        with fc5:
            h1b_filter = st.checkbox("Likely H1B only",
                                     value=False,
                                     help="Show only roles at companies in the local H1B "
                                          "sponsor database. Always verify before applying.")
        with fc6:
            ng_only = st.checkbox(f"🎓 New-grad only ({_ng_total})",
                                  value=False,
                                  help="Show only roles with a genuine new-grad / early-career "
                                       "signal (title like 'Software Engineer I' / 'Associate' / "
                                       "'New Grad', or JD language). These are seasonal (peak "
                                       "Aug–Nov), so this can be near-empty off-season.")
        # Clearly-foreign roles are already dropped at search; what's left to optionally
        # hide is 'unknown' (no clear location signal — often US). Default OFF so you see
        # more jobs; tick it for a strict US-only view.
        _n_unknown = sum(1 for it in jobs if it["job"].get("us_location") == "unknown")
        us_only = st.checkbox(f"🇺🇸 Strict US only (hides {_n_unknown} unknown-location)",
                              value=False,
                              help="OFF: show US + unknown-location roles (foreign already "
                                   "removed). ON: only roles with a clear US signal (US "
                                   "city/state, United States, USA, Remote US) — H1B-strict.")
        # Unified job-status filter: one persisted store (job_marks: ⭐ saved / 🔴 rejected)
        # + applied (from the Tracker, authoritative). One control to slice by status.
        _jm0 = st.session_state.get("job_marks", {})
        _n_rej = sum(1 for it in jobs if _jm0.get(_job_ui_key(it["job"])) == "rejected")
        _n_sav = sum(1 for it in jobs if _jm0.get(_job_ui_key(it["job"])) == "saved")
        # Stable option VALUES (no counts) + format_func for the count labels — embedding
        # the live count in the value would reset the selection every time you save/reject
        # a job (the stored value would no longer match an option).
        _status_label = {
            "All": "All",
            "Active": "Active (hide applied + 🔴)",
            "Saved": f"⭐ Saved only ({_n_sav})",
            "Applied": "Applied only",
            "HideRej": f"🔴 Hide not-relevant ({_n_rej})",
        }
        status_filter = st.selectbox(
            "Status", ["All", "Active", "Saved", "Applied", "HideRej"],
            index=0, key="match_status_filter", format_func=lambda o: _status_label[o],
            help="⭐ Save or 🔴 mark jobs on each card; this slices the list by that status. "
                 "Applied comes from your Tracker.")
        if (_n_rej or _n_sav) and st.button(f"🧹 Clear all marks ({_n_sav}⭐ · {_n_rej}🔴)",
                                            help="Remove every ⭐/🔴 mark (persisted)."):
            st.session_state["job_marks"] = {}
            save_marks(cfg)
            st.rerun()

        # Per-render memo: relevance_score is pure per item, but it's hit ~3x each
        # (filter, sort, card). Cache once per render so a big unfiltered list isn't
        # recomputed hundreds of times. (id() is stable within a single render.)
        _rel_cache = {}

        def _rel(it):
            k = id(it)
            if k not in _rel_cache:
                _rel_cache[k] = relevance_score(it)
            return _rel_cache[k]

        def _ai_fit(it):
            f = it["job"].get("fit_score", it["job"].get("fitScore"))
            try:
                return float(f)
            except (TypeError, ValueError):
                return -1

        def _sort_key(it):
            if sort_by == "Résumé ↔ JD keyword match":
                v = it.get("jd_ats")
                return v if v is not None else -1
            if sort_by == "Estimated ATS match (résumé ↔ role)":
                return it["score"]["score"]
            if sort_by == "AI fit score":
                return _ai_fit(it)
            # Priority: genuine new-grad roles float to the top, then by priority score.
            return (1 if it["job"].get("is_new_grad") else 0, _rel(it))

        st.caption("**Priority score** decides the order to apply: for board pulls it is driven "
                   "by how many of the job's required skills your résumé matches; for AI-search "
                   "roles it blends the estimated ATS match with the AI's fit score. 🟢 ≥85 · 🟡 ≥70 · 🟠 ≥55.")
        vc1, vc2 = st.columns([1, 3])
        with vc1:
            if st.button("🔗 Verify all links", help="HTTP-check every job link and flag closed/unreachable ones (404/closed)."):
                with st.spinner(f"Checking {len(jobs)} links…"):
                    src.verify_links([it["job"] for it in jobs], max_workers=16)
                dead = sum(1 for it in jobs if it["job"].get("link_live") is False)
                persist_jobs(cfg)
                st.success(f"Checked {len(jobs)} links · {dead} closed/unreachable flagged below.")
        n_dead = sum(1 for it in jobs if it["job"].get("link_live") is False)
        with vc2:
            if n_dead:
                st.error(f"❌ {n_dead} job(s) have a closed or unreachable link — flagged below "
                         "with a 'Find real posting' fallback.")
        with st.expander("ℹ️ About these scores / check on a real ATS tool"):
            ats_checker_links()

        # Which jobs are already in the tracker? Read the workbook ONCE, then O(1) lookups.
        _applied_links, _applied_ct = set(), set()
        try:
            for _r in trk.read_all(P(cfg["paths"]["tracker_xlsx"])):
                _lk = str(_r.get("job_link", "")).strip().lower()
                if _lk:
                    _applied_links.add(_lk)
                _c = str(_r.get("company", "")).strip().lower()
                _t = str(_r.get("job_title", "")).strip().lower()
                if _c and _t:
                    _applied_ct.add((_c, _t))
        except Exception:
            _log_exc("Match&Score: could not read tracker for applied markers")

        def _is_applied(j):
            lk = (j.get("job_link", "") or "").strip().lower()
            if lk and lk in _applied_links:
                return True
            return (str(j.get("company", "")).strip().lower(),
                    str(j.get("title", "")).strip().lower()) in _applied_ct

        def _passes_filters(item):
            _mk = _jm0.get(_job_ui_key(item["job"]))
            _app = _is_applied(item["job"])
            if status_filter == "Active" and (_app or _mk == "rejected"):
                return False
            if status_filter == "Saved" and _mk != "saved":
                return False
            if status_filter == "Applied" and not _app:
                return False
            if status_filter == "HideRej" and _mk == "rejected":
                return False
            if source_filter != ALL_SOURCES_FILTER and job_source_label(item) != source_filter:
                return False
            if _rel(item) < min_score:
                return False
            if h1b_filter and (not item.get("h1b") or item["job"].get("no_sponsorship")):
                return False
            if wm_filter != "Any" and item["job"].get("work_mode", "") != wm_filter:
                return False
            if ng_only and not item["job"].get("is_new_grad"):
                return False
            if us_only and item["job"].get("us_location") != "us":
                return False
            return True

        ranked = sorted([it for it in jobs if _passes_filters(it)],
                        key=_sort_key, reverse=True)
        # User "🔴 Not relevant" marks (session-only) sink to the bottom — still
        # visible, shown in red — so the top of the list stays the jobs you care about.
        _marks = st.session_state.setdefault("job_marks", {})
        ranked = ([it for it in ranked if _marks.get(_job_ui_key(it["job"])) != "rejected"]
                  + [it for it in ranked if _marks.get(_job_ui_key(it["job"])) == "rejected"])
        source_total = (len(jobs) if source_filter == ALL_SOURCES_FILTER
                        else _source_counts.get(source_filter, 0))

        # ---- Optional: true résumé↔JD relevance via Claude (your own API key) ----
        _llm_key = cfg.get("llm_api_key", "")
        _N_AI = 25
        if llm_score.available(_llm_key):
            ac1, ac2 = st.columns([1, 3])
            with ac1:
                if st.button(f"🤖 AI-score top {min(_N_AI, len(ranked))} with Claude",
                             help="Reads each job's full description + your résumé and scores REAL "
                                  "fit (uses your Anthropic API key; costs a few cents per run). "
                                  "The AI fit blends into the priority ranking."):
                    rt = get_resume_text(cfg, st.session_state["profile"]) \
                        or ltx.profile_plain_text(st.session_state["profile"])
                    try:
                        with st.spinner(f"Claude is reading {min(_N_AI, len(ranked))} job descriptions…"):
                            res = llm_score.score_jobs(rt, [it["job"] for it in ranked[:_N_AI]],
                                                       _llm_key, model=cfg.get("llm_model"),
                                                       max_jobs=_N_AI)
                            merged = llm_score.merge_scores(ranked[:_N_AI], res)
                        persist_jobs(cfg)
                        st.success(f"Claude scored {merged} job(s) against your résumé. "
                                   "Sort by **AI fit score** or **Priority** to use it.")
                    except Exception as e:
                        st.error(f"AI scoring failed: {str(e)[:300]}")
            with ac2:
                st.caption("💡 This is the most accurate relevance — Claude reads the full JD and "
                           "your résumé, instead of keyword matching.")
        else:
            st.caption("💡 Want true résumé↔JD relevance? Add an Anthropic API key as `llm_api_key` "
                       "in config/settings.yaml and `pip install anthropic` — an **🤖 AI-score with "
                       "Claude** button appears here that reads each full JD and scores real fit.")

        _ng_msg = (f" · 🎓 **{_ng_total}** genuine new-grad role(s) floated to the top"
                   if (_ng_total and not ng_only) else "")
        _source_msg = (f" from **{source_filter}**" if source_filter != ALL_SOURCES_FILTER else "")
        st.caption(f"Showing **{len(ranked)}** of {source_total} job(s){_source_msg} "
                   f"(current run: {len(jobs)}), sorted by **{sort_by}**.{_ng_msg}")
        if source_filter != ALL_SOURCES_FILTER and source_total == 0:
            st.info("No jobs found from this source.")
        elif not ranked and source_filter != ALL_SOURCES_FILTER:
            st.info("No jobs from this source match the active filters. Loosen them above.")
        elif not ranked and ng_only:
            st.info("No genuine new-grad-titled roles in your current list — they're seasonal "
                    "(peak Aug–Nov). Uncheck **🎓 New-grad only** to see all entry-level roles, "
                    "or use **Path B (AI search)** for evergreen early-career pipelines.")
        elif not ranked:
            st.info("No jobs match these filters. Loosen them above.")

        # ── Auto-tailor batch: generate a tailored, honesty-validated PDF for the top
        #    N ranked jobs in one click. Reuses the exact single-job tailoring pipeline
        #    (tailor_resume → validate_no_fabrication → generate_resume_pdf) unchanged.
        if ranked and session_resume_ready():
            with st.expander("🚀 Auto-tailor the top jobs (batch → PDFs)"):
                _prof_bt = st.session_state["profile"]
                _bc1, _bc2 = st.columns([1, 2])
                with _bc1:
                    _n_bt = st.number_input("How many (from the top)", 1,
                                            min(25, len(ranked)), min(5, len(ranked)),
                                            key="autotailor_n")
                with _bc2:
                    st.caption("Tailors a truthful, ATS-friendly résumé PDF per job — reorders "
                               "your REAL content only (the honesty validator blocks any "
                               "fabrication). Skips jobs the validator can't pass.")
                if st.button("🚀 Generate tailored PDFs", type="primary", key="autotailor_go"):
                    done, blocked = [], []
                    prog = st.progress(0.0)
                    _targets = ranked[:int(_n_bt)]
                    for _bi, _it in enumerate(_targets):
                        _bj = _it["job"]
                        try:
                            _bparsed = ats_mod.parse_job(_bj.get("description", ""), _bj.get("title", ""))
                            _bt = tailor_mod.tailor_resume(_prof_bt, _bparsed)
                            _bok, _ = tailor_mod.validate_no_fabrication(_bt, _prof_bt)
                            if not _bok:
                                blocked.append(f"{_bj.get('company','')} — {_bj.get('title','')}")
                            else:
                                _bfn = pdf_gen.safe_filename(
                                    f"Resume_{_bj.get('company','')}_{_bj.get('title','')}_{date.today().isoformat()}.pdf")
                                _bout = P(os.path.join(cfg["paths"]["tailored_resumes"], _bfn))
                                pdf_gen.generate_resume_pdf(_bt, _bout)
                                done.append((_bj, _bout))
                        except Exception:
                            _log_exc(f"auto-tailor failed for {_bj.get('company','')}")
                            blocked.append(f"{_bj.get('company','')} — {_bj.get('title','')} (error)")
                        prog.progress((_bi + 1) / len(_targets))
                    st.session_state["autotailor_done"] = [(jb.get("company", ""), jb.get("title", ""), str(p))
                                                           for jb, p in done]
                    st.success(f"✅ Tailored **{len(done)}** résumé PDF(s)"
                               + (f" · {len(blocked)} blocked by the honesty validator" if blocked else ""))
                    if blocked:
                        with st.expander(f"⚠️ {len(blocked)} blocked"):
                            for b in blocked:
                                st.caption(b)
                # Download buttons for the last batch (survive the rerun).
                for _di, (_dc, _dt, _dp) in enumerate(st.session_state.get("autotailor_done", [])):
                    if os.path.exists(_dp):
                        with open(_dp, "rb") as _df:
                            st.download_button(f"⬇️ {_dc} — {_dt[:40]}", _df,
                                               file_name=os.path.basename(_dp),
                                               mime="application/pdf", key=f"autodl_{_di}")

        for i, item in enumerate(ranked):
            j, sc = item["job"], item["score"]
            rel = _rel(item)
            ui_key = _job_ui_key(j)
            is_tailor_open = st.session_state.get("active_tailor_job") == ui_key
            h1b = ("🚫 no sponsorship" if j.get("no_sponsorship")
                   else h1b_mod.badge(item.get("h1b_status") or {}))
            wm = j.get("work_mode", "")
            site = j.get("source_tag") or source_tag(j)
            wm_tag = f"  ·  {wm}" if wm else ""
            jd_ats = item.get("jd_ats")
            ats_tag = f"  ·  📄 résumé↔JD {jd_ats}%" if jd_ats is not None else ""
            # JD-depth trust signal: full JD → scores are accurate; snippet → approximate.
            _jds = j.get("jd_source")
            if _jds in ("ats", "api") or len(j.get("description") or "") >= 900:
                jd_tag = "  ·  📄 full JD"
            elif _jds == "snippet" or j.get("needs_verification"):
                jd_tag = "  ·  ✂️ snippet — verify"
            else:
                jd_tag = ""
            link_tag = "  ·  ❌ closed/unreachable link" if j.get("link_live") is False else ""
            ng_tag = "🎓 " if j.get("is_new_grad") else ""
            applied = _is_applied(j)
            _mk = _marks.get(ui_key)
            rejected = _mk == "rejected"
            saved = _mk == "saved"
            # One status, colored across the whole title so you see decisions at a glance:
            # 🟢 applied (Tracker) > ⭐ saved > 🔴 not-relevant.
            _ttl = f"{j['title']} — {j['company']}"
            if applied:
                mark_prefix, mark_tag, _ttl = "🟢 ", "  ·  🟢 :green[**APPLIED**]", f":green[{_ttl}]"
            elif saved:
                mark_prefix, mark_tag, _ttl = "⭐ ", "  ·  ⭐ :violet[**SAVED**]", f":violet[{_ttl}]"
            elif rejected:
                mark_prefix, mark_tag, _ttl = "🔴 ", "  ·  🔴 :red[**NOT RELEVANT**]", f":red[{_ttl}]"
            else:
                mark_prefix, mark_tag = "", ""
            with st.expander(
                f"{mark_prefix}**#{i + 1}**  ·  {ng_tag}{band_color(ats_mod.band(rel))} **{rel}** · {_ttl}  ·  🏷 {site}  ·  {h1b}{wm_tag}{ats_tag}{jd_tag}{link_tag}{mark_tag}",
                expanded=(i == 0 or is_tailor_open)):
                if applied:
                    st.success("🟢 **Applied** — recorded in your Tracker.")
                elif saved:
                    st.info("⭐ **Saved** — in your shortlist.")
                elif rejected:
                    st.error("🔴 **Marked NOT relevant** — sunk to the bottom of the list.")
                # --- Three transparent sub-scores (not one ambiguous "ATS score") ---
                m1, m2, m3 = st.columns(3)
                ai_fit_val = j.get("fit_score", j.get("fitScore"))
                m1.metric("Résumé ↔ JD keywords",
                          f"{jd_ats}%" if jd_ats is not None else "—",
                          help="How much of THIS posting's keywords your résumé already contains.")
                m2.metric("Role relevance (ATS)", f"{sc['score']}",
                          help="Estimated match between your résumé and this role/title — a local "
                               "heuristic, not the employer's real ATS number.")
                m3.metric("Application priority", f"{rel}",
                          help="What to apply to first: skill overlap (board pulls) or the "
                               "ATS↔AI-fit blend (AI search). 🟢≥85 · 🟡≥70 · 🟠≥55.")
                n_match = len(sc["matched_skills"])
                n_req = n_match + len(sc["missing_skills"])
                if n_req:
                    st.caption(f"🎯 You match **{n_match} of {n_req}** hard skills this JD asks for"
                               + (f"  ·  AI fit {ai_fit_val}" if ai_fit_val not in (None, "") else "")
                               + ".")
                # --- Risk line: the honest "why this might be a waste" ---
                _risks = []
                if j.get("no_sponsorship"):
                    _risks.append(f"🚫 likely no sponsorship (\"{j['no_sponsorship']}\")")
                _yr = item.get("years_required", 0)
                if _yr and _yr > int(cfg.get("search_max_years", 2)):
                    _risks.append(f"⏳ JD asks {_yr}+ yrs")
                if j.get("link_live") is False:
                    _risks.append("🔗 posting closed/unreachable")
                elif j.get("link_warning"):
                    _risks.append("🔗 link is a board/search page, not an exact posting")
                if _risks:
                    st.warning("**Risk:** " + "  ·  ".join(_risks))
                action_cols = st.columns([3, 2, 2, 2])
                with action_cols[0]:
                    if st.button("Tailor & Apply this job", key=f"open_tailor_{ui_key}",
                                 type="primary" if is_tailor_open else "secondary",
                                 width="stretch"):
                        st.session_state["active_tailor_job"] = ui_key
                        is_tailor_open = True
                with action_cols[1]:
                    if j.get("job_link"):
                        st.link_button("Open posting", j["job_link"], width="stretch")
                with action_cols[2]:
                    if st.button("⭐ Unsave" if saved else "⭐ Save", key=f"save_{ui_key}",
                                 width="stretch", help="Shortlist this job (persists across reloads)."):
                        if saved:
                            _marks.pop(ui_key, None)
                        else:
                            _marks[ui_key] = "saved"
                        save_marks(cfg)
                        st.rerun()
                with action_cols[3]:
                    if rejected:
                        if st.button("↩️ Un-mark", key=f"unmark_{ui_key}", width="stretch",
                                     help="Restore this job to its normal position."):
                            _marks.pop(ui_key, None)
                            save_marks(cfg)
                            st.rerun()
                    elif st.button("🔴 Not relevant", key=f"mark_{ui_key}", width="stretch",
                                   help="Mark red + sink to the bottom (persists across reloads)."):
                        _marks[ui_key] = "rejected"
                        save_marks(cfg)
                        st.rerun()
                st.caption(f"📍 {j.get('location','—')}  ·  🏷 {site}  ·  source: {j.get('source','')}"
                           + (f"  ·  {wm}" if wm else ""))
                if j.get("fit_reason"):
                    extra = f" (AI fit {j['fit_score']})" if j.get("fit_score") else ""
                    st.caption(f"💡 {j['fit_reason']}{extra}")
                if j.get("priority") or j.get("posted_date") or j.get("salary"):
                    meta = []
                    if j.get("priority"):
                        meta.append(f"priority: {j['priority']}")
                    if j.get("posted_date"):
                        meta.append(f"posted: {j['posted_date']}")
                    if j.get("salary"):
                        meta.append(f"salary: {j['salary']}")
                    st.caption(" · ".join(meta))
                if j.get("no_sponsorship"):
                    st.error(f"🚫 **Skip — likely no sponsorship.** The JD says: "
                             f"*\"{j['no_sponsorship']}\"*. A high score is wasted if they won't "
                             "sponsor — verify before spending time here.")
                hs = item.get("h1b_status") or {}
                if hs.get("sponsor"):
                    st.caption(f"H1B: **{hs.get('label','Likely')} sponsor** "
                               f"({hs.get('confidence','medium')} confidence) based on a local "
                               "database — not a guarantee. Sponsorship can be **role / team / "
                               "budget-dependent** even at sponsoring companies. ⚠️ Verify this "
                               "specific role on MyVisaJobs / H1BGrader (or with the recruiter) "
                               "before applying.")
                else:
                    st.caption("H1B: **Unknown** — not in the local sponsor database. ⚠️ Verify "
                               "sponsorship on MyVisaJobs / H1BGrader before applying.")
                if j.get("h1b_note"):
                    st.caption(f"AI H1B note: {j['h1b_note']}")
                dead = j.get("link_live") is False
                if dead or j.get("link_warning"):
                    if dead:
                        st.error(f"❌ This posting looks closed or unreachable (HTTP {j.get('link_status','?')}). "
                                 "Don't trust it — find the real posting below.")
                    else:
                        st.warning(f"{j['link_warning']} Find the exact live posting before applying.")
                    find_q = (
                        f'{j.get("company","")} "{j.get("title","")}" apply '
                        "(site:greenhouse.io OR site:lever.co OR site:ashbyhq.com "
                        "OR site:myworkdayjobs.com OR site:smartrecruiters.com OR site:icims.com)"
                    )
                    st.markdown(
                        f"[🔎 Find real posting](https://www.google.com/search?q={quote(find_q)})"
                    )
                elif j.get("link_live") is True:
                    if j.get("link_class") == "live_third_party":
                        st.caption("✅ Link live — but it's a **third-party aggregator**. Confirm it "
                                   "opens the real company/ATS posting before you apply.")
                    else:
                        st.caption(f"✅ Link verified live & official (HTTP {j.get('link_status','200')}).")
                st.progress(min(sc["score"], 100) / 100)
                cc1, cc2 = st.columns(2)
                with cc1:
                    st.markdown("**Matched skills**")
                    st.write(", ".join(sc["matched_skills"]) or "_none_")
                    if j.get("matched_skills_ai"):
                        st.caption("AI matched: " + ", ".join(j["matched_skills_ai"][:10]))
                with cc2:
                    st.markdown("**Missing — gaps to address (honestly)**")
                    gaps = sc["missing_skills"] + sc["missing_tools"]
                    if j.get("gaps_ai"):
                        gaps += j["gaps_ai"]
                    st.write(", ".join(dict.fromkeys(gaps)) or "_none_")
                with st.popover("Score breakdown"):
                    st.json(sc["components"])
                if st.session_state.get("active_tailor_job") == ui_key:
                    st.divider()
                    render_tailor_apply_panel(item, cfg, ui_key)

# ====================== TAB 4 · TAILOR & APPLY =======================
if active_tab == TAB_LABELS[3]:
    st.subheader("✍️ Tailor & Apply")
    jobs = st.session_state["jobs"]
    # GATE: only show jobs from the CURRENT résumé-driven Find Jobs run — never
    # cached / default / demo / previous-session jobs.
    if not (session_resume_ready() and st.session_state.get("has_run_find_jobs") and jobs):
        st.info("**No matched jobs yet. Upload your resume and run Find Jobs first.**")
    else:
        st.markdown("#### Select a job")
        rt1, rt2 = st.columns([4, 1])
        with rt2:
            if st.button("🔄 Refresh", key="tab4_refresh", width="stretch",
                         help="Re-render the jobs from your current Find Jobs run."):
                st.session_state.pop("tab4_job_pick", None)
                st.rerun()   # session-only: never reloads cached jobs from disk
        ranked = sorted(jobs, key=relevance_score, reverse=True)
        # Reset the picker whenever the job set changes (e.g. after a fresh pull), so a
        # stale/out-of-range index can't keep showing the wrong job — it snaps to the top.
        _sig = "|".join(_job_ui_key(it["job"]) for it in ranked)
        if st.session_state.get("_tab4_sig") != _sig:
            st.session_state["_tab4_sig"] = _sig
            st.session_state.pop("tab4_job_pick", None)

        def _tab4_label(i, it):
            j = it["job"]
            head = f"#{i + 1} · [{relevance_score(it)}] " \
                   f"{'🎓 ' if j.get('is_new_grad') else ''}{j.get('company','')} — {j.get('title','')}"
            detail = "  ·  ".join(x for x in [
                (j.get("location", "").strip() or "loc —"),
                j.get("work_mode", ""),
                (j.get("source_tag") or j.get("source", "")),
                (f"posted {j.get('posted_date')}" if j.get("posted_date") else ""),
                ("❌ dead link" if j.get("link_live") is False else ""),
            ] if x)
            return f"{head}  ·  {detail}"
        labels = [_tab4_label(i, it) for i, it in enumerate(ranked)]
        with rt1:
            idx = st.selectbox(f"{len(ranked)} job(s) — rows show company · location · source · posted",
                               range(len(ranked)), format_func=lambda i: labels[i],
                               key="tab4_job_pick", label_visibility="visible")
        item = ranked[idx]
        st.divider()
        render_tailor_apply_panel(item, cfg, "tab4_" + _job_ui_key(item["job"]))

# ====================== TAB 5 · TRACKER ==============================
if active_tab == TAB_LABELS[4]:
    st.subheader("Application tracker")
    xlsx = P(cfg["paths"]["tracker_xlsx"])
    rows = trk.read_all(xlsx)                       # read the workbook ONCE
    due = trk.follow_ups_due_from_rows(rows)

    # ---------------- Daily-goal dashboard (10 apps/day target) ----------------
    def _d(s):
        try:
            return date.fromisoformat(str(s)[:10])
        except Exception:
            return None
    _today = date.today()
    _week_start = _today - timedelta(days=6)
    applied_today = sum(1 for r in rows if _d(r.get("applied_date")) == _today)
    applied_week = sum(1 for r in rows if (_d(r.get("applied_date")) or date(1900, 1, 1)) >= _week_start)
    h1b_apps = sum(1 for r in rows if str(r.get("h1b_sponsor")).lower() in ("true", "1", "yes"))
    interviews = sum(1 for r in rows
                     if any(k in str(r.get("status", "")).lower()
                            for k in ("interview", "onsite", "offer")))
    st.markdown("#### 📅 Daily goal")
    g1, g2, g3, g4, g5 = st.columns(5)
    g1.metric("Today", f"{applied_today}/10")
    g2.metric("This week", f"{applied_week}/70")
    g3.metric("H1B-likely apps", h1b_apps)
    g4.metric("Follow-ups due", len(due))
    g5.metric("Interview pipeline", interviews)
    st.progress(min(applied_today / 10.0, 1.0),
                text=f"{applied_today} of 10 applications today")
    st.divider()

    counts = trk.summary_counts_from_rows(rows)
    if counts.get("_total"):
        cols = st.columns(len(counts))
        for c, (k, v) in zip(cols, counts.items()):
            c.metric("Total" if k == "_total" else k, v)

    # ---------------- 📊 Analytics: funnel · response rate · sources ----------------
    if rows:
        with st.expander("📊 Analytics — funnel · response rate · sources"):
            from collections import Counter as _AC
            _stages = ["Applied", "Phone Screen", "OA", "Interview", "Onsite", "Offer", "Rejected"]
            _sc = {s: 0 for s in _stages}
            for _r in rows:
                _s = str(_r.get("status", "") or "").strip()
                if _s in _sc:
                    _sc[_s] += 1
            _apps = sum(1 for _r in rows if _r.get("applied"))
            _responded = sum(v for k, v in _sc.items() if k != "Applied")        # any movement (incl. rejected)
            _positive = sum(v for k, v in _sc.items()
                            if k in ("Phone Screen", "OA", "Interview", "Onsite", "Offer"))
            _h1b_n = sum(1 for _r in rows if _r.get("h1b_sponsor"))
            am1, am2, am3, am4 = st.columns(4)
            am1.metric("Applications", _apps)
            am2.metric("Response rate", f"{round(100 * _responded / _apps) if _apps else 0}%",
                       help="Share that moved past 'Applied' (any reply, including rejections).")
            am3.metric("Positive", _positive, help="Phone screen or further.")
            am4.metric("🛂 H1B apps", _h1b_n)
            try:
                import pandas as _pd
                _funnel = {k: v for k, v in _sc.items() if v}
                if _funnel:
                    st.markdown("**Funnel**")
                    st.bar_chart(_pd.Series(_funnel, name="count"))
                _bysrc = _AC(str(_r.get("source", "") or "Unknown") for _r in rows if _r.get("applied"))
                if _bysrc:
                    st.markdown("**Applications by source**")
                    st.bar_chart(_pd.Series(dict(_bysrc), name="apps"))
            except Exception:
                # pandas/altair missing → fall back to plain text (never crash the tab)
                st.write({k: v for k, v in _sc.items() if v})

    if due:
        st.warning(f"⏰ {len(due)} follow-up(s) due:")
        for r in due:
            st.write(f"- {r['company']} — {r['job_title']} (since {r['applied_date']})")

    # ---------------- Data hygiene: flag duplicate company+title rows ----------------
    from collections import Counter as _Counter
    _dupe_keys = _Counter((str(r.get("company", "")).strip().lower(),
                           str(r.get("job_title", "")).strip().lower())
                          for r in rows if str(r.get("company", "")).strip())
    _dupes = [(co, ti) for (co, ti), c in _dupe_keys.items() if c > 1]
    if _dupes:
        st.warning("🧹 **Possible duplicate rows** (same company + title) — clean these up to keep "
                   "your numbers honest:\n\n"
                   + "\n".join(f"- {co.title()} — {ti.title()} ({_dupe_keys[(co, ti)]}×)"
                               for co, ti in _dupes[:10]))

    if rows:
        st.dataframe(rows, width="stretch")

        # ---------------- Update an application (status / notes) ----------------
        with st.expander("✏️ Update an application's status"):
            id_rows = [r for r in rows if r.get("app_id")]
            if id_rows:
                lbl = {r["app_id"]: f"{r['app_id']} · {r.get('company','')} — {r.get('job_title','')}"
                       for r in id_rows}
                sel = st.selectbox("Application", list(lbl.keys()),
                                   format_func=lambda x: lbl[x], key="trk_sel")
                STATUSES = ["Applied", "Phone Screen", "OA / Assessment", "Interview",
                            "Onsite", "Offer", "Rejected", "Withdrawn"]
                cur = next((r.get("status", "Applied") for r in id_rows if r["app_id"] == sel), "Applied")
                ns = st.selectbox("New status", STATUSES,
                                  index=STATUSES.index(cur) if cur in STATUSES else 0, key="trk_status")
                nn = st.text_input("Replace notes (optional)", key="trk_notes")
                if st.button("Update record"):
                    trk.update_status(xlsx, sel, status=ns, notes=(nn or None))
                    st.success(f"Updated {sel} → {ns}.")
                    st.rerun()
        with open(xlsx, "rb") as _xf:
            _xlsx_bytes = _xf.read()
        st.download_button(
            "⬇️ Download tracker (Excel)", data=_xlsx_bytes,
            file_name="job_applications.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        downloadable = [r for r in rows if r.get("resume_file")
                        and os.path.exists(P(str(r["resume_file"])))]
        if downloadable:
            st.markdown("#### 📎 Resumes you applied with")
            for r in downloadable:
                path = P(str(r["resume_file"]))
                c1, c2 = st.columns([3, 1])
                with c1:
                    st.write(f"**{r.get('company','')}** — {r.get('job_title','')} "
                             f"({r.get('applied_date','')})")
                with c2:
                    with open(path, "rb") as f:
                        st.download_button(
                            "⬇️ Resume", f.read(),
                            file_name=f"{pdf_gen.safe_filename(str(r.get('company','')) + '_' + str(r.get('job_title','')))}{os.path.splitext(path)[1]}",
                            mime="application/octet-stream",
                            key=f"dl_{r.get('app_id','')}")
    else:
        st.info("No applications recorded yet. Apply to a job and click ✅ in 'Tailor & Apply'.")
