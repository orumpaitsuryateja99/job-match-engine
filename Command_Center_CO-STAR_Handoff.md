# Command Center CO-STAR Handoff

> **📌 Developer context only (2026-06-01):** This maps the *original* browser command
> center to the Python app. For how the app behaves **today**, see `README.md`; for the
> original design intent, see `Resume_to_Job_Automation_Blueprint.md`.

This document captures the exact workflow from the older `swe_application_command_center.html` project and maps it to this new `Job_Automation` Python app. Use it as Claude context when improving or rebuilding the app.

## C - Context

The old command center was a local browser app. It did not call Claude/OpenAI directly. It worked as a human-in-the-loop workflow:

1. Upload or paste a resume locally.
2. Parse resume text in the browser.
3. Build a CO-STAR job-search prompt.
4. User pastes that prompt into Claude/ChatGPT with web search.
5. User pastes the returned job JSON back into the app.
6. For each job, build a CO-STAR resume-tailoring prompt.
7. User pastes that prompt into Claude/ChatGPT.
8. User pastes the returned LaTeX resume back into the app.
9. App computes a local ATS-style keyword match.
10. User manually applies and records the submitted resume.

The new app is a Streamlit/Python version of the same idea:

- `app/resume_parser.py` parses resumes into a structured profile.
- `app/prompts.py` builds the AI job-search prompt.
- `app/sources.py` imports and normalizes AI job JSON and public ATS board jobs.
- `app/ats.py` computes ATS match scores locally.
- `app/tailor.py` deterministically tailors resumes without fabrication.
- `app/pdf_gen.py` generates ATS-friendly PDF resumes.
- `app/tracker.py` records applied jobs in Excel.

Important design rule: the app searches, scores, tailors, tracks, and stores. It never auto-applies.

## O - Objective

When extending this project, preserve the old command-center strengths:

- Local-first privacy.
- Exact live job links, not generic boards.
- Full job descriptions, because they power ATS scoring and tailoring.
- CO-STAR prompts that are strict about JSON schemas.
- Truth-first tailoring: reorder, select, and rephrase only real resume content.
- ATS scoring as an estimate for prioritization, not an official employer score.
- Human review before applying.

## S - Steps

### 1. Resume Upload

Old command center:

- Accepted `.txt`, `.docx`, searchable `.pdf`, and `.tex`.
- `.tex` was special: the app saved it as the active LaTeX template and also converted it to plain text.
- Stored resume state in browser local storage under `free_swe_resume`.

Core old methods:

```js
handleFile(file)
docxToText(file)
pdfToText(file)
texToText(raw)
cleanText(s)
syncResumeFromEditor(silent)
```

New app equivalent:

```py
app/resume_parser.py
read_resume_text(path)
parse_resume(path)
build_profile_from_text(raw)
```

Current new app supports `.pdf`, `.docx`, `.doc`, `.txt`, `.md`, **and `.tex`** — old parity is achieved. `resume_parser.read_resume_text()` routes `.tex` through `latex_resume.tex_to_text()`, and uploading a `.tex` also stores it as the active LaTeX template for tailoring prompts.

### 2. Resume Parsing

Old parsing was local and heuristic:

- DOCX: read `word/document.xml` via JSZip.
- PDF: use PDF.js, group text by Y-position, preserve visual lines.
- TEX: strip LaTeX commands, convert sections and items to readable text.

New parsing:

- PDF: PyMuPDF (`fitz`).
- DOCX: `python-docx`.
- TXT/MD: direct text read.
- TEX: converted via `latex_resume.tex_to_text()` and saved as the active LaTeX template when uploaded.
- Skill/tool/domain extraction comes from `textutils.py` and `skills_db.py`.
- Output profile shape:

```json
{
  "name": "",
  "email": "",
  "phone": "",
  "links": {},
  "summary": "",
  "skills": [],
  "tools": [],
  "domains": [],
  "projects": [{"name": "", "bullets": []}],
  "experience": [{"company": "", "role": "", "bullets": []}],
  "target_roles": [],
  "experience_years": 0,
  "raw_text": ""
}
```

### 3. Job Search Prompt

Old function:

```js
buildJobSearchPrompt()
```

New function:

```py
app/prompts.py
build_job_search_prompt(profile, prefs="", location="any", max_years=2)
```

Use this CO-STAR prompt pattern:

```text
# Job Search Assistant (CO-STAR)

## C - Context
You are my job-search assistant WITH LIVE WEB SEARCH. I am an ENTRY-LEVEL / NEW-GRAD software engineer. Use my profile below to find roles that genuinely match my REAL background. Application stays manual — you only return links; you never apply.
- Target roles: {roles}
- Top skills: {skills}
- Location preference: {location}
- Other preferences: {prefs}
- Summary: {summary}

## O - Objective
Return a JSON array of up to 12 CURRENTLY OPEN, ENTRY-LEVEL software engineering jobs that match my profile, each with the EXACT live posting link and the FULL job description copied from that posting.

## HARD RULES
- Do not recall jobs from memory or training data.
- Every job must be opened and verified live.
- Exclude Senior, Sr., Staff, Principal, Lead, Manager, Director, Architect, II/III/IV, L3/L4/SWE 3, and roles requiring more than {max_years} years unless they explicitly welcome new grads.
- Prefer H1B-sponsoring employers, but always mark sponsorship as verify.
- Return fewer jobs rather than padding with guesses.

## S - Steps
1. Build title variants from my target roles and skills.
2. Search direct company/ATS sources first: Greenhouse, Lever, Ashby, Workday, SmartRecruiters, iCIMS, company careers.
3. Use Handshake, Built In, Wellfound, Jobright, MyVisaJobs, OPTnation, Interstride, LinkedIn, and Indeed as discovery only.
4. Open every posting and verify it is live.
5. Drop expired, closed, redirected, generic board, search, or careers-home links.
6. Copy the full job description into `job_description`.
7. Score fit honestly against my real profile.

## T - Output
Return ONLY a valid JSON array:
[
  {
    "company": "",
    "role": "",
    "location": "",
    "source": "Greenhouse / Lever / Ashby / Workday / Company Careers",
    "apply_link": "exact live single-posting URL with its id",
    "job_description": "full text copied verbatim from the posting",
    "fit_score": 0,
    "fit_reason": "one sentence specific to my background",
    "matched_skills": [],
    "gaps": ["requirements I should NOT fake"],
    "experience_required": "",
    "posted_date": "",
    "salary": "",
    "h1b_note": "short note, always include the word verify",
    "priority": "Apply immediately / Tailor first / Skip"
  }
]

## A - Audience
An international-student / new-grad software engineer. The JSON feeds a local tracker, so schema and link accuracy must be exact.

## R - Reflection
Before returning, re-check that every job came from a live posting, every link is exact, every JD is real, and no role violates the seniority/years filter.
```

### 4. Importing AI Job JSON

Old methods:

```js
extractJSON(text)
normalizeJob(j)
```

New method:

```py
app/sources.py
normalize_pasted_jobs(pasted, new_grad_only=True, max_years=2)
```

Expected fields:

- `company`
- `role` or `title`
- `location`
- `source`
- `apply_link`
- `job_description`
- `fit_score`
- `fit_reason`
- `matched_skills`
- `gaps`
- `experience_required`
- `posted_date`
- `salary`
- `h1b_note`

The full `job_description` must be preserved because `app/ats.py` and `app/tailor.py` depend on it.

### 5. Resume Tailoring Prompt

Old function:

```js
buildTailorPrompt(job)
```

The new app currently performs deterministic Tier-A tailoring in code instead of copying an LLM prompt. Keep that as the default because it is safer. If adding an optional Claude/Gemini polish step, use this CO-STAR prompt:

```text
# Tailored Resume Builder (CO-STAR)

## C - Context
I am applying to the target job below. I am giving you my real resume/profile, the job description, and the ATS report. Tailor my resume to this job WITHOUT inventing anything.

- Company: {company}
- Role: {role}
- Location: {location}
- Apply link: {apply_link}
- Fit reason: {fit_reason}
- Matched skills I genuinely have: {matched_skills}
- Known gaps I must NOT fake: {gaps}

## O - Objective
Produce one truthful, ATS-friendly, recruiter-readable tailored resume for this job. Use only facts from my original resume/profile. Tailoring means selecting, reordering, and rephrasing real content.

## S - Steps
1. Use the original resume/profile as the source of truth.
2. Do not invent skills, employers, dates, metrics, awards, links, education, certifications, or projects.
3. Surface job-relevant real skills first.
4. Reorder bullets by relevance to the JD.
5. Rephrase bullets only when the same factual claim remains true.
6. Preserve real company names, roles, dates, locations, project names, degrees, GPA, and links.
7. List unsupported JD keywords as gaps; do not insert them.
8. Keep formatting ATS-friendly: single column, standard headings, plain text, no images/tables.

## T - Output
Return structured JSON only:
{
  "tailored_resume": {
    "name": "",
    "contact": {},
    "summary": "",
    "skills": [],
    "experience": [],
    "projects": [],
    "education": [],
    "additional": []
  },
  "change_log": [],
  "honesty_check": {
    "invented_content_found": false,
    "unsupported_keywords_not_added": [],
    "evidence_for_emphasized_keywords": []
  },
  "estimated_ats_after": 0
}

## A - Audience
An ATS keyword parser and a human recruiter for this exact role.

## R - Reflection
Before returning, verify that every skill, number, employer, date, project, and claim exists in the original resume/profile. If not, remove it.
```

If adding this to code, wire it into `tailor.llm_polish()` and always run:

```py
validate_no_fabrication(tailored, profile)
```

after the LLM response.

### 6. Building The New Resume

Old command center:

- Preferred a `.tex` template.
- If the user uploaded `.tex`, it reused that exact formatting.
- Otherwise, it used a built-in base64 LaTeX template.
- AI returned final LaTeX.
- User pasted LaTeX back and downloaded `.tex`.

New app:

- Uses structured profile data.
- `tailor.tailor_resume(profile, job)` produces a truthful tailored profile.
- `tailor.validate_no_fabrication(tailored, profile)` blocks unsafe output.
- `pdf_gen.generate_resume_pdf(tailored, out_path)` creates a PDF with ReportLab.

Recommended direction:

- Keep deterministic PDF generation as default.
- Add optional LLM polish only as a rephrasing step.
- Never let the LLM directly bypass the validator.

### 7. ATS Scoring Method

Old command center scoring:

```js
computeAtsMatch(latex, job)
```

Old formula:

```text
score = matched_JD_keywords / total_extracted_JD_keywords * 100
```

Old keyword extraction:

- Known skills dictionary.
- Capitalized or tech-looking tokens from the JD.
- Stopword removal.
- Max 40 keywords.
- Check which keywords appear in the LaTeX resume converted to text.

New app scoring:

```py
app/ats.py
parse_job(jd_text, title="")
ats_score(profile, job, my_years=0)
score_resume_vs_jd(profile, jd_text, title="", my_years=0)
```

New weighted formula:

```text
ATS_score =
  0.30 * hard_skill
+ 0.25 * keyword
+ 0.15 * title
+ 0.10 * tools
+ 0.10 * experience
+ 0.05 * domain
+ 0.05 * formatting
```

Each component is 0-100.

Returned report:

```json
{
  "score": 0,
  "band": "Strong / Good / Stretch / Weak",
  "components": {},
  "missing_skills": [],
  "missing_tools": [],
  "missing_keywords": [],
  "matched_skills": []
}
```

Interpretation:

- 85-100: Strong
- 70-84: Good
- 55-69: Stretch
- Below 55: Weak

Always label this as an estimate for prioritization, not the employer's real ATS score.

### 8. Job Ranking

Old command center ranking:

```js
jobRelevance(entry)
```

If AI fit score and ATS score both exist:

```text
relevance = 0.5 * ai_fit_score + 0.5 * ats_score
```

If only one exists, use that score.

New app currently ranks the Match & Score tab by local ATS score:

```py
ranked = sorted(jobs, key=lambda x: x["score"]["score"], reverse=True)
```

Optional enhancement:

```py
combined = 0.5 * local_ats_score + 0.5 * ai_fit_score
```

only when `fit_score` exists in imported AI JSON.

## T - Tools / Data

Core files in this project:

- `app/app.py`: Streamlit UI, five tabs.
- `app/prompts.py`: AI job-search prompt builder.
- `app/resume_parser.py`: PDF/DOCX/TXT/MD parsing.
- `app/ats.py`: ATS scoring.
- `app/tailor.py`: deterministic tailoring and honesty validator.
- `app/pdf_gen.py`: PDF rendering.
- `app/sources.py`: public board pulls and AI JSON import.
- `app/tracker.py`: Excel application tracker.
- `Resume_to_Job_Prompt_Pack.md`: reusable prompt pack.
- `Resume_to_Job_Automation_Blueprint.md`: system architecture.

## A - Audience

This app is for an international-student / new-grad software engineer applying to SWE/backend/full-stack roles. The user needs:

- H1B-aware discovery.
- New-grad filtering.
- Exact posting links.
- Honest ATS gaps.
- Fast PDF tailoring.
- Manual final review and application.

## R - Reflection

When Claude modifies this app, it should preserve these guardrails:

- Never auto-apply.
- Never fabricate resume content.
- Never treat ATS score as official.
- Never include generic board/search/expired links as final jobs.
- Never add missing JD keywords unless the profile proves the candidate has them.
- Always keep the full JD attached to the job record.
- Always validate LLM-polished resume output before PDF generation.

