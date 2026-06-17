# Job Automation — runnable starter kit

A local "co-pilot" that **searches, scores, tailors, tracks, and stores** — and
**never auto-applies**. You open the job link and apply yourself; the app does the
busywork. Runs entirely on your computer.

> **Last verified:** 2026-06-11 — `python tests/test_core.py` → **269/269 checks passing**.

---

## ✨ What it does (features)

- **Finds jobs** from 14 sources: 267 H1B-sponsor company boards (Greenhouse / Lever /
  Ashby / Workday / SmartRecruiters), aggregators (SerpApi Google Jobs · JSearch ·
  Adzuna · Careerjet · Jooble), The Muse, free remote APIs (Remotive · RemoteOK),
  Tavily site-discovery, an **AI-search paste** path, and **manual paste**.
- **Scores** every role against *your* résumé — ATS match %, résumé↔JD keyword
  coverage, and a priority rank. Filters out senior / over-experience / off-focus /
  non-US / duplicate roles.
- **Flags H1B** sponsorship from a 342-employer confidence database (a heuristic — you
  still verify).
- **Tailors** a truthful, ATS-friendly résumé PDF (an honesty validator blocks any
  fabrication) + a per-job **cover letter** that leads with your strongest project.
- **Marks** jobs ⭐ saved / 🔴 not-relevant (persist across reloads), batch **auto-tailors**
  your top N, and **tracks** applications in a local Excel file with an analytics
  dashboard (funnel · response rate · by-source).
- **Optional:** schedule an overnight pull (cron) + Slack/email notify; true résumé↔JD
  AI scoring with your own Anthropic key.
- **Private + safe:** runs locally, never auto-applies, never scrapes ToS-protected
  sites. Your résumé is session-only (re-upload each session).

## 🚀 Quick start — your first session

1. **Launch:** double-click **`run.command`** (macOS) / **`run.bat`** (Windows), or
   run the terminal commands below. Browser opens at `http://localhost:8501`.
2. **Tab 1 · Resume** → upload your résumé (PDF/DOCX/TXT/TEX). *Nothing works until you
   do this — it's résumé-gated and session-only.*
3. **Tab 2 · Find Jobs** → set filters (role focus, work mode, "Posted within", max
   years, H1B-only), tick sources (start with **209+ sponsor boards** — no setup), hit
   **🔭 Search**. Off-season (Dec–Jul)? Widen "Posted within" to 30 days + use **B) AI
   search → paste**.
4. **Tab 3 · Match & Score** → review ranked jobs. **⭐ Save** good ones, **🔴 mark** bad
   ones (colors the list, persists), filter by status, **🚀 auto-tailor your top N**.
5. **Tab 4 · Tailor & Apply** → generate a tailored résumé PDF + cover letter → **open
   the posting and apply yourself** → click **✅ I applied manually**.
6. **Tab 5 · Tracker** → daily-goal dashboard + 📊 analytics; update each application's
   status as you progress.

> ⚠️ Always **verify H1B** on MyVisaJobs / H1BGrader (the badge is a heuristic), and
> **review every tailored résumé** before sending.

## 🔑 Get more jobs (optional, 5-min free keys)

The app ships ready; these activate more sources. Copy `.env.example` → `.env` and fill in:
- **Careerjet** (free affiliate id): https://www.careerjet.com/partners/api/ → `CAREERJET_AFFID`
- **Jooble** (free key): https://jooble.org/api/about → `JOOBLE_API_KEY`
- **SerpApi / JSearch** keys → full-JD volume from LinkedIn/Indeed/Glassdoor aggregated
- **Slack webhook / SMTP** → notifications for the overnight pull
- **`pip install anthropic`** + `llm_api_key` in settings.yaml → true AI résumé↔JD scoring

Then restart. Keys can live in `.env` (git-ignored) or `config/settings.yaml`.

---

## Run it (with Claude completely closed)

This app is **100% standalone** — plain Python + Streamlit on your machine. It does
not need Claude or this chat open. You only need **Python 3** installed and an
internet connection (for the live job-board pulls).

### Easiest — double-click

- **macOS:** double-click **`run.command`** (in this folder). If macOS blocks it the
  first time: right-click → Open → Open.
- **Windows:** double-click **`run.bat`**.

First launch sets up a private environment and installs dependencies (~1 min); after
that it starts in seconds and opens your browser at http://localhost:8501.
Leave the little terminal window open while you use the app; close it (or press
`Ctrl+C`) to stop.

### Or from a terminal

```bash
cd "Job_Automation"
python3 -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m streamlit run app/app.py
```

> Does it need the internet? The **live board pulls** and the **AI-search** step do
> (they reach the web). **Manual paste**, scoring, tailoring, PDF generation, and the
> tracker all work fully offline.

### Optional: compile tailored LaTeX → PDF locally

The Tailor & Apply tab can compile an AI-returned `.tex` résumé into a PDF on your
machine if a LaTeX engine is installed. The lightest option:

```bash
brew install tectonic        # macOS; self-contained, auto-fetches packages
```

Without it, everything else still works — you just won't see the in-app compile button.

---

## The 5 tabs

1. **Resume** — upload your master résumé (**PDF / DOCX / TXT / TEX**). It parses your
   skills, projects, experience, education, and links, then uses *that* for everything
   after. The app **starts empty** and shows a warning banner until you upload — nothing
   is preloaded. Your parsed profile is saved to `config/profile.json` and **persists
   across restarts** (clear it anytime with the 🗑 button). Uploading a `.tex` file also
   stores it as your active LaTeX template for AI tailoring prompts.
2. **Find Jobs** — three ways to get roles, all sharing the search settings at the top
   (role focus, work mode, H1B-only, entry-level, max years, and a **"Posted within"**
   freshness filter):
   - **Discovery site buttons** — open pre-filled searches on LinkedIn, Indeed,
     Glassdoor, Dice, Handshake, Lensa, OPTnation, JobRight, SubmitX, MyVisaJobs,
     Built In, Wellfound, Naukri, Hired, Google/company-career search, and Workday
     ATS search. These **do not import jobs**; use them to discover leads, then import
     via **B) AI search → paste** or **Manual**.
   - **Search all sources** — one click fans out across the enabled source groups:
     direct APIs (Greenhouse / Lever / Ashby / The Muse / optional Adzuna),
     company ATS pulls (Workday / SmartRecruiters), and key-gated discovery boards
     when a search API key is configured.
   - **A) Pull target boards** — the legacy focused pull for your target H1B sponsors'
     public boards and ATS endpoints (Greenhouse / Lever / Ashby / Workday /
     SmartRecruiters), and — with the **"➕ Also pull The Muse"** toggle (on by default)
     — also from **The Muse's public jobs API** (no key, US-heavy employers). The links
     come straight from each source's API and their format is quality-checked; with the
     **"Verify application links are live"** toggle (on by default) each one is also
     **HTTP-checked** during the pull so closed/404 postings get flagged and demoted.
     Results are scored against your résumé and filtered by a **relevance floor**.
     Failed company slugs/tenants are listed so you can fix them.
   - **B) AI search → paste** *(widest reach)* — copy the built-in hardened CO-STAR
     prompt (it bakes in your full résumé, role focus, work mode, H1B setting, and
     freshness window) into any AI with web search (claude.ai, ChatGPT), then paste its
     JSON back. Pasted links are verified too.
   - **Manual** — paste a single job's description + link.
3. **Match & Score** — every role gets an **estimated ATS match**, a priority score that
   ranks which to apply to first (driven by real **skill overlap** + how much of the JD
   your résumé covers), a color band, a "you match N of M hard skills" line, matched
   skills, and an honest missing-skills list. Sort by priority / résumé-vs-JD match /
   ATS / AI fit. Senior, over-experience, off-type, and duplicate roles are filtered out.
   Each card shows an H1B confidence badge and flags closed/unreachable links.
4. **Tailor & Apply** — generate a truthful, ATS-friendly tailored résumé PDF (an
   honesty validator blocks any fabrication), or run the AI-LaTeX workflow (copy the
   strict prompt → paste the returned `.tex` → compile to PDF locally). See the tailored
   résumé's ATS keyword coverage vs the JD, upload the exact résumé you applied with,
   open the link, apply **manually**, then click **✅ I applied manually**. Duplicate
   applications are detected before recording.
5. **Tracker** — opens with a **daily-goal dashboard** (Today X/10, this week X/70,
   H1B-likely apps, follow-ups due, interview pipeline). The ✅ tick writes a row to a
   local Excel file and files the exact résumé you used; you can edit an application's
   status (Applied → Phone Screen → OA → Interview → Onsite → Offer/Rejected), download
   the tracker, and re-download any résumé you applied with. Follow-ups surface
   automatically.

---

## Configure (optional)

`config/h1b_sponsors.json` — the **structured H1B sponsor database** that drives the
confidence badges. Each entry has a name, aliases, confidence (`high` = verified /
`medium` = likely), evidence, and `last_verified` date. Matched by exact/alias name
(no loose substring matching). **This is a curated heuristic, not a guarantee — always
verify on MyVisaJobs / H1BGrader before applying.**

`config/settings.yaml`
- `h1b_sponsors` — a simpler fallback sponsor list (used only if the JSON DB is missing).
- `search_max_years` — roles asking more than this many years are dropped (default 2).
- `adzuna.app_id` / `app_key` — optional free key from https://developer.adzuna.com
- `serpapi.api_key` — optional SerpApi Google Jobs key for structured Google Jobs results.
- `jsearch.api_key` / `rapidapi_key` — optional OpenWeb Ninja and RapidAPI JSearch keys
  for structured job results. Prefer env vars (`SERPAPI_API_KEY`, `JSEARCH_API_KEY`,
  `JSEARCH_RAPIDAPI_KEY`) for shared repos.
- `job_api_fallback` — quota-safe provider order. The app uses **one** limited provider
  per run and falls through only if the current provider is missing/quota-exhausted/errors:
  SerpApi → OpenWeb Ninja JSearch → RapidAPI JSearch → Tavily discovery.
- `discovery` — optional Tavily or Google Programmable Search credentials. Leave blank
  to keep LinkedIn / Indeed / Glassdoor / Dice discovery boards off and use the site
  buttons + AI paste path. Recommended quick setup: create a Tavily key, then either
  set `TAVILY_API_KEY` in your shell or paste it into `discovery.tavily_api_key`.
  For Google Programmable Search, set both `GOOGLE_API_KEY` and `GOOGLE_CX`, or paste
  both values into `settings.yaml`.

`config/source_catalog.yaml` — source groups for the unified **Search all sources**
button: direct APIs, company/ATS discovery, and key-gated discovery boards.

`config/target_companies.txt` — companies pulled in **A)** and by **Search all
sources**. Format `ats,token,Name` (`ats` = `greenhouse` | `lever` | `ashby` |
`workday` | `smartrecruiters`). Find slugs/tenants from careers URLs:
`boards.greenhouse.io/<slug>`, `jobs.lever.co/<slug>`, `jobs.ashbyhq.com/<slug>`,
or Workday tokens like `tenant|wd5|ExternalCareerSite`. Add freely — the app
reports which slugs/tenants failed so you can prune them.

---

## Optional: scheduled overnight pull (cron)

`scripts/scheduled_pull.py` fetches across every configured source and saves the raw
jobs to `logs/overnight_jobs.json` (no scoring — your résumé stays session-only). In
the morning, open the app, upload your résumé, and click **📥 Import overnight pull**
in Find Jobs to score them in-session. Add a cron job:

```bash
0 6 * * *  cd "/path/to/Job_Automation" && .venv/bin/python scripts/scheduled_pull.py
```

Get pinged when it finishes by configuring `notify:` in `settings.yaml` (or env vars
`SLACK_WEBHOOK_URL` / `SMTP_HOST`+`EMAIL_TO`).

---

## Quality guarantees

- **No apply function exists** — the app cannot submit an application.
- The **✅ tick** is the only thing that writes an "applied" row.
- Tailoring only **reorders + rephrases your real content**; `validate_no_fabrication()`
  rejects any new skill, employer, or number.
- Jobs come **only** from sources that permit it (public APIs — Greenhouse / Lever /
  Ashby / Workday / SmartRecruiters / The Muse / optional Adzuna — search API
  discovery, your AI's search, or manual paste). No
  LinkedIn / Indeed / Glassdoor / Dice / Handshake scraping (ToS-forbidden,
  login-walled, or bot-blocked); for those, use the discovery buttons plus the
  AI-search paste or manual import path. Workday is handled as company/ATS discovery
  because each employer runs its own tenant.
- **Senior, over-experience, off-type, and duplicate roles are filtered** on every path.
- Everything stays **local**.

Run the tests anytime: `python tests/test_core.py` (**269 checks**).

> The ATS score is an **estimate to prioritize effort** — not the employer's real number.
> H1B badges are a **curated heuristic** — verify sponsorship before applying.
> Always review a tailored résumé before applying.
