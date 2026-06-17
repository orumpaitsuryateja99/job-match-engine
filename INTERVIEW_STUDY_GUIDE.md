# Job Automation Interview Study Guide

Updated: 2026-06-10

Use this as your one-hour crash course before explaining this project in an interview.
It is written in simple language first, then gives the technical details you should be
able to say out loud.

Important: do not claim this app auto-applies, guarantees H1B sponsorship, or gives an
employer's real ATS score. The app is a local, human-in-the-loop job-search assistant.

## 1. Resume-Ready Project Description

Job Automation Platform | Python, Streamlit, REST APIs, ReportLab, OpenPyXL, PyMuPDF,
Anthropic API optional

- Built a local Streamlit job-search command center that parses my resume, searches
  public ATS/job APIs, filters new-grad SWE roles, checks H1B sponsor confidence,
  scores resume-to-JD fit, generates honesty-validated tailored resumes/cover letters,
  and tracks applications in Excel.
- Integrated Greenhouse, Lever, Ashby, Workday, SmartRecruiters, The Muse, Adzuna,
  SerpApi Google Jobs, JSearch, Careerjet, Jooble, Remotive, RemoteOK, AI-pasted JSON,
  scheduled overnight pulls, and manual JD input into one normalized job schema.
- Implemented ATS-style scoring, priority ranking, role-focus filtering, seniority and
  years-of-experience rejection, US-location filtering, duplicate detection, link
  verification, H1B sponsor lookup, and an honesty validator that blocks fabricated
  skills, employers, and metrics.
- Wrote an offline core test suite covering resume parsing, scoring, filtering, H1B
  lookup, source normalization, discovery enrichment, tailoring validation, PDF
  generation, Excel tracking, and optional Claude score parsing.

Short resume bullet:

Built a local Python/Streamlit job application automation platform that searches public
ATS/job APIs, filters new-grad and H1B-likely roles, scores resume-to-JD fit, generates
honesty-validated tailored resumes and cover letters, and tracks applications in Excel.

## 2. 60-Second Interview Pitch

"This project is a local job application command center for a new-grad software
engineer who needs H1B sponsorship. I upload my resume, and the app parses it into a
structured profile. Then it searches multiple public job sources and configured
company ATS boards, normalizes every posting into one schema, filters out senior,
off-focus, non-US, duplicate, stale, or no-sponsorship roles, and scores each job
against my actual resume.

The app separates local ATS match from application priority. Priority considers skill
overlap, resume-JD keyword coverage, new-grad signal, H1B sponsor confidence, link
quality, and whether the job description is a full JD or only a snippet. For selected
jobs, it generates a truthful tailored resume PDF and a cover letter using only facts
already present in my resume. It never auto-applies. I apply manually and then record
the application in a local Excel tracker."

## 3. Explain It Like I Am 15

Think of the app as a five-station assembly line:

1. Resume station: read my resume and turn it into structured data.
2. Job-finding station: pull jobs from APIs, company boards, AI-pasted JSON, overnight
   pulls, or manual paste.
3. Matching station: compare each job's requirements to my resume.
4. Tailoring station: reorder and polish my real resume content for one job.
5. Tracker station: after I apply manually, save the application in Excel.

The app does not "magically know" fit. It breaks text into signals:

- What skills does the JD ask for?
- What tools does it mention?
- Is the title new-grad or senior?
- How many years does it ask for?
- Is the company in the H1B sponsor database?
- Is the link a real single job posting?
- Does the JD say the company will not sponsor?

Then it ranks the jobs I should apply to first.

## 4. Problem It Solves

For my job search, the hard parts are:

- I need new-grad/entry-level SWE roles.
- I need companies likely to sponsor H1B.
- I need to apply consistently, around 10 applications/day.
- I need to tailor without lying.
- I need to avoid wasting time on stale, senior, non-US, or no-sponsorship roles.

This app automates the busywork while keeping human judgment in the loop.

H1B-critical line to say:

"The H1B badge is a confidence signal, not a guarantee. Even at known sponsors, a
specific role or team may not sponsor. The app flags likely sponsors and tells the user
to verify on MyVisaJobs, H1BGrader, or with the recruiter."

## 5. Tech Stack

UI:

- Streamlit.
- Five-tab dashboard: Resume, Find Jobs, Match & Score, Tailor & Apply, Tracker.
- Streamlit session state for current resume, current jobs, filters, active job,
  generated files, saved/rejected marks, and batch state.

Core backend:

- Python modules under `app/`.
- `requests` for REST APIs.
- `ThreadPoolExecutor` for parallel board pulls and link verification.
- `PyYAML` for configuration.
- `markitdown`, `PyMuPDF`, and `python-docx` for resume parsing.
- `ReportLab` for generated resume PDFs.
- `OpenPyXL` for the Excel tracker.
- Optional `anthropic` SDK for Claude-based resume-to-JD scoring.

Storage:

- `input_resume/`: uploaded resume file for parsing.
- `tailored_resumes/`: generated tailored PDF/TEX outputs.
- `applied_jobs/`: exact resume used for each manual application.
- `tracker/job_applications.xlsx`: application tracker.
- `logs/job_marks.json`: saved/not-relevant marks.
- `logs/batches/`: saved job batches.
- `logs/overnight_jobs.json`: scheduled raw job pull output.
- `config/h1b_sponsors.json`: 342-employer H1B confidence database.
- `config/target_companies.txt`: 267 configured target company ATS boards.
- `config/source_catalog.yaml`: source groups and discovery labels.

Secrets:

- The app can load keys from local `.env`, environment variables, or local settings.
- Do not show actual keys in an interview.

## 6. Main Files And Responsibilities

`app/app.py`

- Main Streamlit app.
- Loads config and `.env`.
- Defines the five tabs.
- Keeps resume matching and job display session-gated.
- Orchestrates search, scoring, filtering, tailoring, tracking, marks, and batches.

`app/resume_parser.py`

- Reads PDF, DOCX, TXT, MD, and TEX resumes.
- Extracts name, email, phone, links, summary, skill categories, normalized skills,
  tools, domains, projects, experience, education, and additional achievements.

`app/sources.py`

- Source connectors and normalization layer.
- Handles public APIs, search discovery, pasted jobs, manual jobs, date parsing,
  entry-level filters, link quality, link verification, dedupe, Workday/Ashby/Lever/
  Greenhouse parsing, and discovery enrichment.

`app/aggregator.py`

- Unified source fan-out engine.
- Fetches from selected sources, merges and dedupes jobs, and returns counts/errors.
- Does not score jobs. Scoring is centralized in `app.py`.

`app/ats.py`

- Local ATS-style scoring.
- Parses JDs into skills, tools, domains, keywords, years required, and title.
- Returns score, band, component breakdown, matched skills, missing skills, missing
  tools, and missing keywords.

`app/textutils.py`

- Lightweight text utilities.
- Detects skills, tools, domains, years required, senior titles, sponsorship blocks,
  US/foreign/unknown location, and remote/hybrid/onsite work mode.

`app/skills_db.py`

- Controlled vocabulary and aliases for technical matching.
- Example: `js` -> `javascript`, `node js` -> `node.js`, `restful api` ->
  `rest apis`, `gemini api` -> `google gemini api`.

`app/roles.py`

- Role-focus model.
- Supports exact entry-level chips, general SWE, backend, frontend, full-stack, ML/AI,
  data, mobile, and DevOps/platform.
- Rejects non-SWE families like QA, support, business analyst, sales engineer,
  product manager, field technician, mechanical engineer, and senior/staff/lead roles.

`app/h1b.py`

- Exact/alias H1B sponsor lookup.
- Loads `config/h1b_sponsors.json`.
- Returns sponsor true/false plus confidence: high, medium, unknown.

`app/tailor.py`

- Deterministic honest resume and cover-letter tailoring.
- Reorders real skills, skill categories, projects, and bullets.
- Creates a truthful role-focused summary.
- Blocks fabricated skills, employers, and metrics.

`app/pdf_gen.py`

- Generates ATS-friendly PDFs with ReportLab.
- Uses selectable text, clickable contact links, clear section headings, compact
  bullets, and no image-only resume content.

`app/latex_resume.py`

- Optional LaTeX workflow.
- Converts TEX to plain text, builds tailoring prompts, computes keyword coverage,
  and compiles with a local LaTeX engine if available.

`app/tracker.py`

- Excel tracker with OpenPyXL.
- Creates workbook, appends application rows, detects duplicates, updates status,
  calculates follow-ups, and summarizes status counts.

`app/prompts.py`

- Builds the strict AI-search prompt for Claude/ChatGPT with web search.
- Forces live web search, allowed sources only, entry-level rules, freshness rules,
  exact link rules, H1B preference, JSON schema, and no guessing from memory.

`app/llm_score.py`

- Optional Claude scoring.
- Reads resume plus full JDs only when an Anthropic key exists and the user clicks
  the AI-score button.
- Parses structured JSON results and merges fit scores back onto jobs.

`scripts/scheduled_pull.py`

- Headless overnight pull.
- Fetches raw jobs into `logs/overnight_jobs.json`.
- Does not score because scoring depends on the current session resume.

`tests/test_core.py`

- Offline tests for the non-UI logic.
- Current README claim: 268 checks.

## 7. End-To-End Data Flow

Use this answer for "walk me through the project":

1. User uploads resume in Tab 1.
2. `resume_parser.parse_resume()` extracts text and builds a structured profile.
3. The profile is stored in Streamlit session state.
4. User selects filters and sources in Find Jobs.
5. `aggregator.search_selected_sources()` calls the selected connectors.
6. `sources.py` normalizes every result to a common job dictionary:
   `title`, `company`, `location`, `job_link`, `source`, `description`, plus metadata.
7. `sources.merge_duplicates()` collapses duplicates and prefers official ATS links.
8. `app._add_jobs()` performs final scoring and filters:
   freshness, duplicate check, link quality, new-grad tag, US-location tag, work mode,
   ATS score, relevance floor, H1B lookup, and sponsorship-block detection.
9. Match & Score displays ranked jobs with matched skills, gaps, risks, H1B status,
   link status, and score breakdown.
10. Tailor & Apply generates a truthful tailored resume PDF and cover letter.
11. User manually opens the posting and applies.
12. User clicks "I applied manually".
13. `tracker.append_application()` writes a row to Excel and stores the applied resume.

## 8. Important Data Structures

Profile:

```python
{
    "name": "...",
    "email": "...",
    "phone": "...",
    "links": {"linkedin": "...", "github": "...", "portfolio": "..."},
    "summary": "...",
    "skill_categories": [{"category": "Languages", "items": [...]}],
    "skills": ["python", "java", "flask"],
    "tools": ["git", "github", "postman"],
    "domains": ["ai/ml", "full-stack"],
    "projects": [{"name": "ClimateAI", "bullets": [...]}],
    "experience": [{"company": "VIVA FIT", "role": "...", "bullets": [...]}],
    "education": [...],
    "additional": [...],
    "target_roles": [...],
    "raw_text": "..."
}
```

Job:

```python
{
    "title": "Software Engineer, New Grad",
    "company": "Stripe",
    "location": "Remote, US",
    "job_link": "https://...",
    "source": "Greenhouse",
    "description": "Full JD text...",
    "posted_date": "2026-06-10",
    "work_mode": "Remote",
    "jd_source": "api"
}
```

Scored session item:

```python
{
    "job": job,
    "score": ats_score_result,
    "h1b": True,
    "h1b_status": {"sponsor": True, "confidence": "high", "label": "Verified"},
    "jd_ats": 82,
    "years_required": 1,
    "fetched_at": "2026-06-10"
}
```

## 9. The Five Tabs

Tab 1: Resume

- Upload PDF/DOCX/TXT/TEX.
- Parse into structured profile.
- Preview parsed resume and extracted sections.
- Warn if parse is thin, skills are missing, or no projects/experience parsed.
- Clearing resume also clears matched jobs because old jobs were scored against old
  resume state.

Tab 2: Find Jobs

- Path A: automatic search/pull jobs.
- Path B: AI search -> paste JSON.
- Path C: import overnight pull.
- Path D: add one manual job.
- All paths end up in the same `_add_jobs()` scoring pipeline.

Tab 3: Match & Score

- Sort by priority, resume-JD keyword match, local ATS match, or AI fit score.
- Filter by source, work mode, min priority, H1B-likely, new-grad only, strict US,
  and status.
- Save jobs, mark jobs not relevant, verify links, save/load batches, auto-tailor
  top jobs.

Tab 4: Tailor & Apply

- Pick one ranked job.
- Review job, resume, tailored changes, cover letter, LaTeX workflow, and apply step.
- Record only after manual application.

Tab 5: Tracker

- Daily goal: today X/10 and week X/70.
- Metrics: H1B-likely apps, follow-ups due, interview pipeline.
- Analytics: funnel, response rate, applications by source.
- Duplicate company/title row warning.
- Status/notes update.
- Download Excel tracker and applied resumes.

## 10. Job Sources

Automatic/direct API sources:

- Greenhouse.
- Lever.
- Ashby.
- The Muse.
- Adzuna.
- JSearch.
- SerpApi Google Jobs.
- Careerjet.
- Jooble.
- Remotive.
- RemoteOK.

Company ATS sources:

- Workday.
- SmartRecruiters.

Discovery/search-lead sources:

- Company careers.
- Google postings.
- LinkedIn Jobs.
- Indeed.
- Glassdoor.
- Dice.
- Built In.
- Wellfound.
- Jobright AI.
- Startup boards: Startup Jobs, TopStartups, Y Combinator Jobs, Work at a Startup.
- H1B/OPT boards: MyVisaJobs, OPTnation, Interstride, SkillHire, ApplyRyt,
  ScoutBetter, TickBig, SubmitX.
- New-grad aggregators: newgrad-jobs.com, jobsfornewgrad.com, RippleMatch, Lensa.

Disabled or manual-only examples:

- Handshake, SubmitX, Hired, Hire Tech are login-walled or low-yield for public search.
- Naukri is disabled because it is India-focused and not useful for a US H1B hunt.

Key design:

The app does not scrape login-walled/ToS-protected boards directly. For those, it uses
site buttons, search API discovery, AI-search paste, or manual paste.

## 11. Aggregator Design

`aggregator.py` follows this boundary:

- Fetch.
- Normalize.
- Resolve.
- Dedupe.
- Return jobs plus counts/errors.

It does not score jobs. That is intentional. Scoring lives in `_add_jobs()` so every
source path uses one shared scoring/filtering pipeline.

Job API fallback:

- Provider order can be SerpApi -> OpenWeb JSearch -> RapidAPI JSearch -> Careerjet
  -> Jooble -> Tavily discovery.
- It uses one successful limited provider per run.
- It falls through only when a provider is missing, quota-exhausted, or errors.
- This avoids burning every API quota in one search.

## 12. Source Normalization And Dedupe

Every provider has different field names. The app normalizes them into one schema.

Examples:

- Greenhouse returns board jobs and `absolute_url`.
- Lever returns company postings.
- Ashby uses board names.
- Workday requires list + detail requests and tenant/site tokens.
- SmartRecruiters uses company identifiers.
- JSearch/SerpApi expose apply options and aggregator links.
- Discovery may return only snippets, so the app tries to upgrade official ATS links
  to full job descriptions.

Dedupe:

- `job_key()` removes exact duplicates.
- `merge_duplicates()` merges same company/title across sources.
- `url_rank()` prefers official ATS/employer links over third-party links, board roots,
  search pages, or missing links.

## 13. Filtering Logic

Entry-level filter:

- Rejects Senior, Sr., Staff, Principal, Lead, Manager, Director, Architect.
- Rejects Software Engineer II/III/IV, SWE 2/3, L3/L4/L5, IC2+ style levels.
- Rejects jobs requiring more than selected max years.
- Keeps New Grad, Entry Level, Junior, Associate, Software Engineer I, New College
  Grad, University Graduate.

Role focus filter:

- Supports exact entry-level roles and specializations like backend, frontend,
  full-stack, ML/AI, data, mobile, DevOps, and general SWE.
- Rejects non-SWE roles even under general search.

Freshness filter:

- Parses ISO dates, dates, timestamps, and relative phrases like "2 hours ago".
- Drops jobs outside selected "Posted within" window.

Location filter:

- Classifies location as `us`, `foreign`, or `unknown`.
- Clearly foreign roles are hard-dropped.
- Unknown-location roles can be hidden with the strict US filter.

Sponsorship-block filter:

- Detects phrases like "will not sponsor", "no visa sponsorship", "US citizens only",
  or "security clearance required".
- Such jobs are sunk to near-zero priority because they are bad for an H1B-required
  candidate.

Link filter:

- Detects search-like links, board roots, third-party links, official ATS links, and
  dead/closed links.
- Link verification runs in parallel.

## 14. ATS Scoring

`ats.py` computes a local estimate:

```text
ATS score =
  0.33 * hard_skill
+ 0.27 * keyword
+ 0.15 * title
+ 0.10 * tools
+ 0.05 * experience
+ 0.05 * domain
+ 0.05 * formatting
```

Components:

- Hard skill: overlap between resume skills and JD skills.
- Keyword: JD technical keyword coverage in the resume.
- Title: similarity between job title and selected target roles.
- Tools: overlap with tools like Git, GitHub, Postman.
- Experience: whether required years fit new-grad level.
- Domain: overlap with AI/ML, cloud, full-stack, etc.
- Formatting: rough ATS parse-readiness of the resume.

Bands:

- 85+ = Strong.
- 70-84 = Good.
- 55-69 = Stretch.
- Below 55 = Weak.

Say this:

"The score is a local prioritization estimate, not the employer's real ATS score. I
show matched skills, missing skills, missing tools, missing keywords, and component
breakdown so the user can inspect why a job scored high or low."

## 15. Priority Ranking

The app separates local ATS match from application priority.

For AI-pasted jobs with `fit_score`:

```text
priority_base = 0.5 * local_ATS + 0.5 * AI_fit
```

For board/API jobs with resume-JD keyword coverage:

```text
priority_base =
  0.60 * skill_match_strength
+ 0.25 * resume_JD_keyword_coverage
+ 0.15 * local_ATS
```

When keyword coverage is unavailable:

```text
priority_base =
  0.70 * skill_match_strength
+ 0.30 * local_ATS
```

Adjustments:

- +9 for genuine new-grad signal.
- +3 for H1B-likely sponsor.
- +4 for official ATS/employer link.
- +4 for full JD or ATS-sourced JD.
- -8 for snippet-only or needs-verification leads.
- -4 for link warning.
- -10 for foreign location, though foreign roles are usually dropped earlier.
- No-sponsorship jobs are capped near zero.
- Dead links are capped low.

Strong answer:

"I learned that pure keyword score can rank generic bad jobs too high, so I built a
separate priority score that better models where I should spend application time."

## 16. Skill Detection

The app uses a controlled vocabulary instead of heavy NLP.

Why:

- Resume/JD matching depends on technical aliases.
- A curated dictionary is more predictable than generic keyword extraction.

Examples:

- `js` -> `javascript`.
- `node js` -> `node.js`.
- `restful` -> `rest apis`.
- `gemini api` -> `google gemini api`.
- `cnn` -> `computer vision`.
- `sam` -> `sam`.

Careful detail:

The detector avoids false positives for the C language. It only credits bare `C` in
safe contexts like `C/C++`, `C programming`, `embedded C`, or `C language`, not in
phrases like "C Corp" or "C-suite".

## 17. H1B System

`h1b.py` loads `config/h1b_sponsors.json`.

Current database:

- 342 sponsor entries.
- Version: 2026-05-31.
- Each entry has name, aliases, confidence, evidence, and last verified date.

Matching:

- Normalize company names.
- Match exact names or aliases.
- No loose substring matching.

Why no substring matching:

If substring matching were allowed, "Goog" could accidentally match "Google". H1B is
high-stakes, so the app uses exact/alias matching.

Confidence labels:

- `high` -> Verified.
- `medium` -> Likely.
- unknown -> Unknown.

Interview line:

"This is intentionally a confidence database, not a legal guarantee. The app helps
prioritize likely sponsors but still requires manual verification."

## 18. Honest Resume Tailoring

`tailor.py` is deterministic by default.

It can:

- Reorder skills so JD-relevant skills appear first.
- Reorder skill categories while keeping every real item.
- Reorder projects based on role angle.
- Pick the most relevant real bullets.
- Polish weak phrasing.
- Build a role-focused summary from true resume facts.
- Generate a cover letter from true resume facts.

It cannot:

- Add skills I do not have.
- Invent employers.
- Invent dates.
- Invent metrics.
- Pretend I know missing JD skills.

Role angle:

- ML/AI roles lead with the CNN/weed classification project.
- Backend/API and full-stack roles lead with ClimateAI.
- Frontend roles emphasize ClimateAI and VIVA FIT UI work.

Honesty validator:

`validate_no_fabrication()` blocks:

- New skills/tools not in original resume.
- Summary skills not in profile.
- New numbers/metrics in tailored bullets.
- New employers.

Cover-letter validator:

- Blocks fake skills/tools.
- Blocks fake metric-like numbers.

Best line:

"The tailoring system is conservative by design. Missing skills become gaps to learn,
not fake resume content."

## 19. PDF And LaTeX Output

ReportLab PDF:

- Generates clean, ATS-friendly resume PDFs.
- Uses selectable text, not images.
- Includes contact links, sections, grouped skills, experience, projects, education,
  and additional achievements.

LaTeX workflow:

- Supports uploaded `.tex` resumes.
- Builds a strict AI prompt for LaTeX tailoring.
- User pastes returned LaTeX.
- App computes keyword coverage and can compile locally if `tectonic`, `latexmk`,
  `pdflatex`, or `xelatex` exists.

## 20. Tracker

The tracker is an Excel workbook using OpenPyXL.

Columns:

- app_id.
- date_added.
- company.
- job_title.
- location.
- job_link.
- source.
- ats_score.
- h1b_sponsor.
- resume_file.
- applied.
- applied_date.
- status.
- follow_up_date.
- notes.

Important behavior:

- No auto-apply function exists.
- The user must click "I applied manually" to record an application.
- Duplicate detection checks same link or same company+title.
- Follow-up date defaults to 7 days after applying.
- The exact submitted resume can be stored and re-downloaded.
- Dashboard tracks daily goal, weekly goal, H1B apps, follow-ups, interview pipeline,
  funnel, response rate, and source counts.

## 21. Privacy And Safety

Safety choices:

- Runs locally.
- Resume matching is session-gated.
- Job display requires current resume plus current search/load.
- Jobs are not blindly restored across page loads.
- Saved/not-relevant marks persist because they are user decisions.
- Batches can be saved intentionally by the user.
- No auto-apply.
- No direct scraping of login-walled boards.
- H1B badge is a heuristic.
- Tailoring validator blocks fabrication.

Important nuance:

The app may save uploaded/generated files locally because it needs them for parsing,
download, and tracker attachments. But it does not automatically reuse old resumes for
new scoring after a fresh page load.

## 22. Performance Engineering

Pooled HTTP session:

- `sources.py` uses one `requests.Session`.
- Connection pooling and retries improve reliability for many board/API calls.

Parallel board pulls:

- `pull_targets_verbose()` uses `ThreadPoolExecutor`.
- One bad company board returns an error instead of killing the whole search.

Board cache:

- `src.enable_board_cache(900)` enables 15-minute in-session caching.
- Raw API responses are cached.
- Filtering still runs fresh when filters change.

Parallel link verification:

- `verify_links()` checks links concurrently and annotates each job.

Precomputed keyword pools:

- `_add_jobs()` builds resume keyword/token sets once per batch.
- Each job score reuses them, avoiding repeated resume tokenization.

Discovery throttling:

- Discovery calls pause between requests to avoid provider throttling and zero-result
  failures.

Google PSE quota tracking:

- The app tracks daily Google Programmable Search usage in `logs/google_pse_usage.json`.

## 23. Testing

Run:

```bash
cd "Job_Automation"
python tests/test_core.py
```

The tests cover:

- Resume parsing.
- Real resume extraction.
- Skill/tool/domain detection.
- ATS scoring.
- Seniority and years filters.
- Role focus filters.
- US/foreign/unknown location detection.
- AI-paste JSON normalization.
- Link quality checks.
- Prompt builder.
- LaTeX helper functions.
- H1B database exact/alias matching.
- Tailoring and cover-letter honesty validators.
- PDF generation.
- Excel tracker writes and follow-up dates.
- The Muse, Adzuna, JSearch, SerpApi, Remotive, RemoteOK, Careerjet, Jooble.
- Discovery query building, stale/closed lead filtering, lead resolution, and ATS
  enrichment.
- Workday and SmartRecruiters routing.
- Aggregator selected-source behavior and fallback provider logic.
- Optional Claude score parsing and merge.

Good interview line:

"Most core logic is outside Streamlit, so I can test it without launching the UI. For
network sources, the tests stub API responses and verify normalized output."

## 24. What To Highlight

Technical strengths:

- Multi-source API integration.
- Normalizing inconsistent provider schemas.
- Resume parsing.
- Deterministic ATS scoring.
- Priority ranking beyond raw keyword match.
- H1B-specific filtering.
- Safety-first resume tailoring.
- Excel tracking and analytics.
- Parallel I/O and caching.
- Offline regression tests.

Personal strengths:

- Built for a real job-search problem.
- New-grad SWE focus.
- H1B-aware design.
- Combines full-stack, APIs, automation, document generation, AI-assisted workflow,
  and data processing.

## 25. Limitations And Strong Answers

Q: Is this a real ATS score?

A: "No. It is a local estimate for prioritization. The app clearly labels it as an
estimate and shows the component breakdown."

Q: Can it guarantee H1B sponsorship?

A: "No. It uses a curated confidence database and warns the user to verify the exact
role."

Q: Does it scrape LinkedIn/Indeed?

A: "No direct scraping. For login-walled or ToS-sensitive boards, the app uses search
discovery, AI-search paste, site buttons, or manual paste."

Q: Can tailoring fabricate experience?

A: "No. The default tailoring is deterministic and only reorders or rephrases real
resume content. The validator blocks new skills, new employers, and new metrics."

Q: What would you improve next?

A: "I would move secrets fully to environment variables/secret manager, add SQLite or
Postgres for durable state, add background jobs with a queue, improve observability for
source failures, add embeddings for semantic matching, and keep the honesty validator."

## 26. Interview Q&A

Q: Why did you build this?

A: "I needed a practical system to apply consistently to new-grad SWE roles while
handling H1B constraints. Existing job boards return too much noise: senior roles,
duplicates, stale links, unknown sponsors, and roles that do not match my resume."

Q: What is the architecture?

A: "Streamlit is the UI. `app.py` orchestrates session state and tabs. `sources.py` and
`aggregator.py` fetch and normalize jobs. `resume_parser.py` builds the candidate
profile. `ats.py`, `roles.py`, and `textutils.py` score and filter jobs. `h1b.py`
handles sponsor confidence. `tailor.py` and `pdf_gen.py` generate truthful application
materials. `tracker.py` writes the Excel tracker."

Q: How do you normalize different job APIs?

A: "Each connector converts provider-specific fields into the same internal schema:
title, company, location, link, source, description, and metadata. Then the aggregator
dedupes and prefers official ATS links."

Q: How does scoring work?

A: "I parse the JD into skills, tools, domains, keywords, title, and years required.
Then I compare those against the parsed resume using weighted components: hard skills,
keywords, title alignment, tools, experience, domain, and formatting."

Q: What was hardest?

A: "The hardest part was making messy, inconsistent job sources behave like one clean
system. Each provider has different fields, date formats, link quality, and JD depth.
I solved that with source-specific connectors, a normalized schema, URL ranking,
dedupe, and one shared scoring pipeline."

Q: Where is AI used?

A: "The core app works without AI. AI is optional in two places: AI-search paste, where
the user asks a web-search AI to return strict JSON, and Claude scoring, where Claude
can score full JDs against the resume if the user provides an Anthropic key."

Q: How do you keep the resume truthful?

A: "The tailoring layer can reorder and polish only real resume facts. It validates
against the original parsed profile and blocks invented skills, employers, or metrics."

Q: Why Streamlit?

A: "It let me quickly build a useful local dashboard with file upload, forms, metrics,
dataframes, download buttons, and session state, while keeping most business logic in
plain Python modules that are testable."

## 27. Things Not To Claim

Do not claim:

- It auto-applies.
- It guarantees sponsorship.
- It gives the employer's real ATS score.
- It directly scrapes login-walled job boards.
- It fabricates custom achievements.
- It is a production SaaS.

Claim instead:

- It automates job-search busywork.
- It is human-in-the-loop.
- It prioritizes likely-fit jobs.
- It flags H1B confidence.
- It tailors honestly.
- It runs locally.
- It is production-minded but still a personal tool.

## 28. One-Hour Study Plan

0-5 minutes:

- Memorize the 60-second pitch.
- Memorize the short resume bullet.

5-15 minutes:

- Study sections 5, 6, and 7.
- Be able to draw the architecture from memory.

15-25 minutes:

- Study sections 10, 11, 12, and 13.
- Be ready to explain source integration, normalization, filtering, and dedupe.

25-35 minutes:

- Study sections 14, 15, 16, and 17.
- Be ready to explain scoring, priority ranking, skills, and H1B.

35-45 minutes:

- Study sections 18, 19, 20, and 21.
- Be ready to explain tailoring, PDF generation, tracker, and safety.

45-60 minutes:

- Practice section 26 out loud.

## 29. Fast Cheat Sheet

Project:

- Local Streamlit job application automation platform.

Core value:

- Search, filter, score, tailor, and track new-grad SWE applications with H1B awareness.

Main modules:

- UI: `app.py`.
- Resume parsing: `resume_parser.py`.
- Sources: `sources.py`.
- Unified search: `aggregator.py`.
- Scoring: `ats.py`.
- Text/skills: `textutils.py`, `skills_db.py`.
- Roles: `roles.py`.
- H1B: `h1b.py`.
- Tailoring: `tailor.py`.
- PDF: `pdf_gen.py`.
- Tracker: `tracker.py`.
- AI prompt/search: `prompts.py`.
- Optional Claude scoring: `llm_score.py`.

Best architecture phrase:

"Fetch and normalize in the source/aggregator layer, then score and filter through one
shared pipeline so every import path behaves consistently."

Best safety phrase:

"The app automates busywork, not judgment. It never auto-applies, never guarantees H1B,
and never fabricates resume content."

Best technical phrase:

"I separate ATS-style match from application priority so a generic keyword match does
not outrank a truly relevant new-grad/H1B-friendly role."

## 30. Two-Minute Deep-Dive Script

"The app starts with resume parsing. I support PDF, DOCX, TXT, and LaTeX. The parser
extracts structured fields like contact info, links, skills, tools, domains, projects,
experience, education, and achievements. That structured profile becomes the source of
truth for matching and tailoring.

For job discovery, I built a source layer. Each connector knows how to call one
provider, like Greenhouse, Lever, Ashby, Workday, SmartRecruiters, The Muse, JSearch,
SerpApi, or RemoteOK. Since each provider returns different JSON, I normalize all
results into one job schema. The aggregator merges sources, resolves discovery leads,
dedupes jobs, and prefers official ATS links.

Then the scoring pipeline parses every JD and compares it to my resume. The ATS score
uses hard-skill overlap, keyword coverage, title alignment, tool overlap, experience
fit, domain fit, and resume formatting readiness. The application priority score adds
real-world signals like new-grad tag, H1B confidence, official link quality, full JD
availability, and no-sponsorship risk.

For tailoring, I intentionally kept the default system deterministic and conservative.
It detects the role angle and reorders my real projects and skills. Backend and
full-stack roles lead with ClimateAI; ML roles lead with my CNN project. The honesty
validator blocks any new skill, employer, or metric. Finally, after I manually apply,
the tracker writes the application to Excel and stores the exact resume I used."

