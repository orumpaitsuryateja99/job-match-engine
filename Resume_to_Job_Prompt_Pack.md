# Resume-to-Job Automation — Prompt Pack (COSTAR)

Five chained, reusable prompts — one per stage of your workflow. Each is self-contained: copy the block, fill the `<INPUT>` placeholders, run it. Outputs are strict JSON (or Markdown for the resume) so they pipe cleanly from one stage into the next.

**How they chain**

```text
[1] Parse Resume ──► candidate_profile JSON
        │
        ▼
[2] Search Jobs (uses profile) ──► jobs[] JSON  (real, verified links)
        │
        ▼  (pick a job, get its full JD)
[3] ATS Score (profile + JD) ──► ats_report JSON
        │
        ▼
[4] Tailor Resume (profile + JD + ats_report) ──► tailored resume + honesty check
        │
        ▼  YOU APPLY MANUALLY via the link
        ▼
[5] Track (after you confirm) ──► spreadsheet row JSON
```

**Global rules baked into every prompt**

1. **Never fabricate.** No invented skills, employers, titles, dates, metrics, or projects. Ever.
2. **Never auto-apply.** These prompts produce text/JSON only. You open links and apply yourself.
3. **Estimates, not verdicts.** The ATS score is for prioritization, not the employer's real number.
4. **H1B: verify, don't guess.** Flag confidence; never assert sponsorship you can't support.
5. **Output discipline.** When a prompt says "JSON only," return *only* the JSON — no prose, no ```` ``` ```` fences, no commentary.

> The biggest fix vs. a naive setup is in **Prompt 2**: it forbids recalling jobs from memory and *requires* a real retrieval method + link verification. That's what stops dead/hallucinated links and irrelevant results.

---

## 1 · Resume Parsing Prompt

**Use:** once per resume. **Input:** the resume text/file. **Output:** `candidate_profile` JSON that feeds every later stage.
**Upgrades vs. your draft:** JSON-only enforcement, date/metric normalization rules, evidence-based strengths/gaps, no visa inference, reading-order handling for multi-column PDFs.

```text
## C — Context
I am giving you the full text of one resume. Parse it into a clean, structured
candidate profile used downstream for job search, ATS scoring, and tailoring.
Use ONLY information present in the resume. Never guess. If a field is absent,
return "" (string) or [] (array). If the resume is multi-column, read it in
logical reading order (left column fully, then right, or by visual section).

## O — Objective
Act as a Resume Parsing and Candidate Profile Extraction Specialist.
Extract the resume into the exact JSON schema below.

## S — Steps
1. Extract contact info (name, email, phone, location, LinkedIn, GitHub, portfolio).
2. Extract education (degree, university, location, GPA, graduation date, coursework).
3. Extract work experience (title, company, location, dates, bullets, skills used).
4. Extract projects separately from experience (name, description, bullets, skills, metrics).
5. Categorize technical skills (languages, frameworks, cloud, databases, tools, ml_ai, other).
6. Extract certifications and achievements verbatim.
7. Infer target_roles from the resume's content and level (e.g., "Software Engineer (New Grad)",
   "Backend Engineer", "Full-Stack Engineer"). Base this on actual skills/projects, not wishes.
8. Infer seniority_level from titles + years (new grad / entry-level / junior / mid / senior).
   A graduating student with internships = "new grad / entry-level".
9. Set visa_or_sponsorship_notes ONLY if the resume explicitly mentions it; else "".
10. Build existing_keywords: the concrete technical terms actually present
    (languages, frameworks, tools, methodologies), lowercased and de-duplicated.
11. List strengths (max 5) and gaps (max 5), each specific and evidence-based.
    A "gap" = a skill commonly expected for the target role that is absent here.

## NORMALIZATION RULES
- Dates: "MMM YYYY" (e.g., "Jul 2023") or "YYYY"; use "Present" for current roles.
- Metrics: preserve EXACTLY as written ("97%", "17,509 images", "4 REST APIs"). Never round or invent.
- Do not merge projects into experience or vice versa.
- Skills go in their correct category; if unsure, use "other".

## T — Output format (return ONLY this JSON, nothing else)
{
  "candidate_profile": {
    "name": "",
    "contact": { "email": "", "phone": "", "location": "", "linkedin": "", "github": "", "portfolio": "" },
    "education": [ { "degree": "", "university": "", "location": "", "gpa": "", "graduation_date": "", "coursework": [] } ],
    "experience": [ { "title": "", "company": "", "location": "", "start_date": "", "end_date": "", "bullets": [], "skills_used": [] } ],
    "projects": [ { "name": "", "description": "", "bullets": [], "skills_used": [], "metrics": [] } ],
    "skills": { "languages": [], "frameworks": [], "cloud": [], "databases": [], "tools": [], "ml_ai": [], "other": [] },
    "certifications": [],
    "achievements": [],
    "target_roles": [],
    "seniority_level": "",
    "visa_or_sponsorship_notes": "",
    "strengths": [],
    "gaps": [],
    "existing_keywords": []
  }
}

## A — Audience
An automation system consumes this JSON for matching, scoring, and tailoring.

## R — Reflection (verify before returning)
- No information was added that isn't in the resume.
- Metrics preserved exactly; dates normalized.
- Projects vs. experience separated correctly; skills categorized.
- Missing fields left blank, not guessed. Output is valid JSON and nothing else.

### RESUME TEXT
<PASTE_RESUME_TEXT_HERE>
```

---

## 2 · Job Search & Link Discovery Prompt  ⭐ (the one that was failing)

**Use:** after parsing. **Input:** `candidate_profile` JSON (+ optional location/remote preference). **Output:** `jobs[]` JSON with **verified, direct** apply links.
**Why your old one failed:** it let the model "search from memory," which produces dead or invented URLs and vague matches. **This version forbids that** and forces a concrete retrieval method + link verification, so every returned link is real and current.

```text
## C — Context
I have a parsed candidate_profile (below). Find CURRENTLY OPEN, RELEVANT jobs and
return DIRECT application links. The candidate wants new-grad / entry-level /
associate / junior / early-career / 0–2 yr Software Engineering roles. H1B
sponsorship matters — flag it with a confidence level, never assert it blindly.
Application stays manual: you only return links.

## O — Objective
Act as a Job Search Strategist and Technical Recruiter for SWE roles.
Return real, open, relevant jobs with verified direct application URLs.

## HARD RULES (this is what makes the output trustworthy)
- You MUST NOT invent or recall listings from memory. Every job MUST come from a
  live source you actually retrieve in this session (see RETRIEVAL METHOD).
- Every application_url MUST resolve to a SPECIFIC, LIVE posting — not a company
  homepage, not a search-results page, not an expired/closed req. If you cannot
  verify a link is live and specific, DO NOT include that job.
- Quality over quantity. Returning 6 verified jobs is better than 30 guesses.
  If tools are unavailable and you cannot verify anything, return [] and say so
  in a single line BEFORE the JSON: "NO_VERIFIED_SOURCE_AVAILABLE".

## RETRIEVAL METHOD (do these in order)
1. Build the search inputs from the profile:
   - role_variants: 3–5 title synonyms (e.g., "Software Engineer New Grad",
     "Software Development Engineer I", "Backend Engineer", "Associate Software Engineer",
     "Software Engineer University Graduate").
   - core_skills: top 6–8 from skills (e.g., python, java, rest apis, sql, node.js, aws).
   - level_terms: "new grad" OR "entry level" OR "university graduate" OR "early career" OR "I".
2. PRIMARY SOURCE — company ATS public boards (most reliable, direct links):
   For a maintained list of H1B-sponsoring companies, query their public boards:
     - Greenhouse: https://boards-api.greenhouse.io/v1/boards/{company}/jobs?content=true
     - Lever:      https://api.lever.co/v0/postings/{company}?mode=json
     - Ashby:      https://api.ashbyhq.com/posting-api/job-board/{company}
     - Workday/SmartRecruiters: use the company careers page search.
   Keep only postings whose title/level match role_variants + level_terms and whose
   description overlaps core_skills. The returned URL (absolute_url / hostedUrl /
   jobUrl) is the verified direct apply link.
3. SECONDARY SOURCE — web search, well-formed queries, e.g.:
     ("new grad" OR "entry level" OR "university graduate") "software engineer"
       (greenhouse.io OR lever.co OR ashbyhq.com OR myworkdayjobs.com) 2026
     + rotate in core_skills and a location/remote term.
   Open each promising result and confirm it is a live, specific posting before using it.
4. NEVER use sources that prohibit scraping (e.g., LinkedIn, Indeed scraping).
   A LinkedIn/Indeed link is acceptable ONLY if it is the official live posting URL
   the user can open and apply through — do not scrape behind logins.

## FILTERING
- Drop senior / staff / principal / lead / manager / director roles.
- Drop expired, duplicate (same company+title), and clearly irrelevant roles.
- Drop anything requiring >2–3 yrs experience unless it explicitly welcomes new grads.
- Rank remaining by fit_score (skills + role + project relevance + location/visa fit).
- Return the top results (aim 8–15 if available).

## H1B FLAGGING
- h1b_sponsor_likely: true/false best estimate.
- h1b_confidence: "high" (known frequent sponsor / posting states sponsorship),
  "medium", "low", or "unknown". If unknown, say "unknown" — do not guess "true".

## T — Output format (return ONLY this JSON array)
[
  {
    "title": "",
    "company": "",
    "location": "",
    "job_type": "full-time | internship | contract",
    "seniority": "new grad | entry-level | junior | associate",
    "h1b_sponsor_likely": true,
    "h1b_confidence": "high | medium | low | unknown",
    "fit_score": 0,
    "why_relevant": "specific to THIS candidate's skills/projects",
    "possible_gaps": "honest, specific skill gap to close before applying",
    "application_url": "direct, live, specific posting URL",
    "source": "Greenhouse | Lever | Ashby | Workday | SmartRecruiters | Company Careers | Web",
    "date_checked": "YYYY-MM-DD"
  }
]

## A — Audience
A local dashboard where I review jobs, click links, and apply manually.

## R — Reflection (verify before returning)
- Did every job come from a live source retrieved now (not memory)?
- Is every application_url specific and live (not a homepage/search page)?
- Senior roles, duplicates, and irrelevant roles removed?
- Is each why_relevant tied to THIS candidate, and each possible_gaps honest?
- Is H1B confidence stated, not guessed? fit_score an integer 0–100?
- If nothing could be verified, did you return [] with NO_VERIFIED_SOURCE_AVAILABLE?

### CANDIDATE PROFILE
<PASTE_candidate_profile_JSON_HERE>

### OPTIONAL PREFERENCES
location_preference: <e.g., "Remote, US" or "any">
must_have_h1b: <true|false>
target_companies (optional seed list of H1B sponsors to pull boards for):
<e.g., stripe, databricks, snowflake, plaid, coinbase, robinhood, reddit, doordash, ...>
```

---

## 3 · ATS Score Prompt

**Use:** per job, before tailoring. **Input:** `candidate_profile` + the full job description. **Output:** `ats_report` JSON + summary.
**Upgrades vs. your draft:** strict (ATS-style exact/near matching, not generous semantics), each sub-score defined, and a clean split between *safe-to-emphasize* keywords (supported by evidence) and *unsupported* keywords (real gaps — never to be added).

```text
## C — Context
I have a candidate_profile and one job description (JD). Estimate how well the
resume matches the JD. This is an ESTIMATE for prioritization and tailoring — NOT
the employer's real ATS result. Score strictly, the way a keyword-based ATS would:
reward exact/near term matches; do not give credit for vague semantic similarity.

## O — Objective
Act as an ATS Match Analyst. Produce an honest, structured match report.

## S — Steps
1. From the JD, extract: required skills, preferred skills, tools/cloud/databases,
   the role title, the experience level, and the domain.
2. Compare against the candidate_profile.
3. Score each sub-dimension 0–100 (definitions below), then combine with the formula.
4. List matched_keywords (present in BOTH) and missing_keywords (JD-required, absent).
5. Split the missing/JD terms into:
   - safe_to_emphasize_keywords: terms the candidate genuinely has evidence for but
     hasn't featured prominently (OK to surface in tailoring).
   - unsupported_keywords_do_not_add: terms with NO evidence in the profile (these are
     gaps to learn — they must NOT be inserted into the resume).
6. Give strong_points, weak_points, a recommendation, and an ordered tailoring_priority.

## SUB-SCORE DEFINITIONS (each 0–100)
- hard_skill_match: % of JD REQUIRED skills present in the profile.
- keyword_match: % of the JD's high-signal keywords present in the profile.
- role_alignment: similarity of JD title to candidate target_roles (penalize senior titles).
- experience_level_match: 100 if JD is 0–2 yr / new-grad; lower as required years rise.
- project_relevance: how directly the candidate's projects map to the JD's domain/tasks.
- tools_cloud_database_match: % overlap of named tools/cloud/DBs.
- education_match: does the candidate meet the degree/field requirement? (100/partial/low)
- ats_formatting: is the resume parse-friendly (single column, standard headings, text)?

## SCORING FORMULA
overall_ats_score =
  0.25*hard_skill_match + 0.20*keyword_match + 0.15*role_alignment +
  0.10*experience_level_match + 0.10*project_relevance +
  0.10*tools_cloud_database_match + 0.05*education_match + 0.05*ats_formatting
(each component 0–100 → overall 0–100, rounded)

## SCORE BANDS
85–100 strong | 70–84 good | 55–69 stretch | 0–54 weak

## RECOMMENDATION LOGIC
strong → "Apply now"; good → "Tailor then apply"; stretch → "Stretch — tailor & apply
only if you can honestly close the top gaps"; weak → "Skip unless H1B options are thin".

## T — Output format (return ONLY this JSON, then a one-line summary after it)
{
  "company": "",
  "job_title": "",
  "overall_ats_score": 0,
  "score_band": "strong | good | stretch | weak",
  "breakdown": {
    "hard_skill_match": 0, "keyword_match": 0, "role_alignment": 0,
    "experience_level_match": 0, "project_relevance": 0,
    "tools_cloud_database_match": 0, "education_match": 0, "ats_formatting": 0
  },
  "matched_keywords": [],
  "missing_keywords": [],
  "safe_to_emphasize_keywords": [],
  "unsupported_keywords_do_not_add": [],
  "strong_points": [],
  "weak_points": [],
  "recommendation": "",
  "tailoring_priority": [],
  "summary": ""
}

## R — Reflection (verify before returning)
- Treated the score as an estimate, not an official ATS result.
- Scored strictly; sub-scores justified by the comparison.
- Missing vs. safe-to-emphasize vs. unsupported keywords cleanly separated.
- No suggestion to add anything the candidate can't support.

### CANDIDATE PROFILE
<PASTE_candidate_profile_JSON_HERE>

### JOB DESCRIPTION
<PASTE_FULL_JD_TEXT_HERE>
```

---

## 4 · Resume Tailoring Prompt (no faking)

**Use:** after ATS scoring. **Input:** original resume + `candidate_profile` + JD + `ats_report`. **Output:** a tailored, ATS-friendly resume + change log + **honesty check**.
**Upgrades vs. your draft:** every emphasized keyword must cite the evidence it's based on; explicit ATS-formatting rules; unsupported JD requirements are listed as gaps, never inserted; before/after estimate + filename.

```text
## C — Context
I have: (1) my original resume, (2) the parsed candidate_profile, (3) the job
description, (4) the ats_report. Produce a tailored resume for THIS job. Optimize
wording, ordering, and keyword alignment — but DO NOT fake or exaggerate anything.

## O — Objective
Act as a Senior Resume Writer and ATS Optimization Specialist. Produce a truthful,
ATS-friendly, ideally one-page tailored resume tuned to the JD.

## ABSOLUTE HONESTY RULES
- Use ONLY content supported by the original resume/profile.
- Do NOT add skills, tools, companies, titles, dates, metrics, certifications,
  projects, or responsibilities that aren't already there.
- You MAY: reorder skills, re-prioritize bullets, rephrase bullets to mirror JD
  vocabulary WHEN TRUTHFUL, sharpen impact, and surface relevant existing details.
- Only use a JD keyword if the candidate has real evidence for it. If a JD
  requirement has no evidence, list it under "Keywords Not Added" — never insert it.
- Each emphasized keyword must map to where in the profile it's supported (cite it).

## ATS-FRIENDLY FORMATTING RULES
- Single column. Standard section headings: Summary, Skills, Experience, Projects, Education.
- Plain text; no tables, text boxes, images, icons, or columns. No critical info in
  headers/footers. Real, consistent dates. Strong action verbs; quantify only with
  EXISTING numbers. Keep to one page if the content allows.

## S — Steps
1. Read original resume, profile, JD, and ats_report (esp. matched / safe_to_emphasize
   / unsupported keywords).
2. Rewrite the Summary to lead with the candidate's true, most JD-relevant strengths.
3. Reorder Skills so JD-relevant (and truthfully held) skills appear first.
4. Re-prioritize and rephrase Experience/Project bullets to mirror the JD — truthfully.
5. Pull in safe_to_emphasize keywords where evidence supports them.
6. Keep everything truthful and ATS-parse-friendly.
7. Produce the change log, keyword lists, and honesty validation.

## T — Output format (Markdown)
# Tailored Resume
<final tailored resume content, ready to format as a PDF>

## Change Log
| Section | Change Made | Evidence It's Based On |
|---|---|---|
| Summary |  |  |
| Skills |  |  |
| Experience |  |  |
| Projects |  |  |

## Keywords Emphasized (with evidence)
- <keyword> — supported by: <bullet/skill/project it came from>

## Keywords NOT Added (unsupported — these are gaps to learn)
- <keyword> — no evidence in profile; left out on purpose.

## Honesty Validation
- [ ] No fake experience, skills, employers, titles, dates, or metrics added.
- [ ] Every emphasized keyword maps to real evidence above.
- [ ] Unsupported JD requirements listed as gaps, not inserted.
- [ ] Formatting is single-column, standard-heading, ATS-parse-friendly.

## Tailoring Notes
- Estimated ATS score before: <from ats_report>
- Estimated ATS score after (your honest estimate): <n>
- Recommended file name: Resume_<Company>_<Role>_<YYYY-MM-DD>.pdf

## R — Reflection (verify before returning)
- Resume stays 100% truthful and specific to this JD.
- All added keywords are evidence-backed; unsupported ones excluded.
- Wording is natural and professional; formatting is ATS-friendly.

### ORIGINAL RESUME
<PASTE_ORIGINAL_RESUME_TEXT_HERE>

### CANDIDATE PROFILE
<PASTE_candidate_profile_JSON_HERE>

### JOB DESCRIPTION
<PASTE_FULL_JD_TEXT_HERE>

### ATS REPORT
<PASTE_ats_report_JSON_HERE>
```

---

## 5 · Application Tracking Prompt (after you manually apply)

**Use:** only after you've applied and confirmed it. **Input:** the job + ATS score + resume file used. **Output:** one spreadsheet-ready row.
**Upgrades vs. your draft:** hard gate on `manual_apply_confirmed`, ISO dates, deterministic `application_id` + follow-up rule.

```text
## C — Context
I manually opened the job link and applied. I'm now confirming it so a clean row is
added to my local tracker and the exact resume file used is recorded. Only create an
"Applied" record if I confirm — you never apply on my behalf.

## O — Objective
Act as a Local Job Application Tracker Assistant. Generate ONE spreadsheet-ready row.

## S — Steps
1. If manual_apply_confirmed is not true → return {"error":"manual_apply_not_confirmed"} and stop.
2. Generate application_id as "APP-YYYYMMDD-NNN" (NNN = next sequence for that date).
3. Set status = "Applied"; applied_date = provided date or today (YYYY-MM-DD).
4. Set follow_up_date = applied_date + 7 days (unless another rule is given).
5. Carry over company, title, location, link, source, ATS score, resume file name + path,
   and H1B flag. Return JSON only.

## T — Input
{
  "manual_apply_confirmed": true,
  "job": { "title": "", "company": "", "location": "", "application_url": "", "source": "", "h1b_sponsor_likely": true },
  "ats_report": { "overall_ats_score": 0 },
  "resume_file": { "file_name": "", "file_path": "" },
  "applied_date": ""
}

## T — Output (return ONLY this JSON)
{
  "application_id": "",
  "applied_date": "",
  "company": "",
  "job_title": "",
  "location": "",
  "job_link": "",
  "source": "",
  "ats_score": 0,
  "resume_file_name": "",
  "resume_file_path": "",
  "status": "Applied",
  "h1b_sponsor_likely": true,
  "follow_up_date": "",
  "notes": ""
}

## R — Reflection (verify before returning)
- Was manual application confirmed? If not, no record was created.
- Job link, exact resume file path, ATS score, and follow-up date all present.
- Dates are ISO (YYYY-MM-DD). Output is valid JSON only.

### INPUT
<PASTE_INPUT_JSON_HERE>
```

---

## Quick tips for getting these to work well

- **Run them where there's web access for Prompt 2.** Prompt 2 only returns real links if the assistant can actually retrieve pages/boards. In a plain chat with no browsing, it will (correctly) return `[]` rather than hallucinate — that's the safety net working, not a failure.
- **Feed Prompt 2 a seed list of H1B sponsors** (the `target_companies` field). Pulling their Greenhouse/Lever/Ashby boards is the single most reliable way to get direct, current apply links. Your `config/target_companies.txt` already has a starter list.
- **Always paste the *full* JD into Prompts 3 and 4** — partial JDs produce weak scores and thin tailoring.
- **These mirror your app's data model**, so outputs from Prompt 1/2/3 can drop straight into the Job_Automation dashboard, and Prompt 4's honesty check matches the app's `validate_no_fabrication` rule.
```

