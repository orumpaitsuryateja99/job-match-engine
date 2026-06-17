"""
prompts.py — builds the hardened job-search prompt the user pastes into any AI
that has web search (claude.ai, ChatGPT, etc.). Mirrors the command center's
hardened prompt: no recalling from memory, hard seniority/years exclusion,
role-focus targeting, work-mode + H1B preferences, board-first sources, link
verification, no duplicates.

The requested JSON schema matches what sources.normalize_pasted_jobs() ingests,
so results paste straight back into the app.
"""

# The ONLY job boards the AI is allowed to search on (user's canonical list).
_ALLOWED_SOURCES = [
    "LinkedIn (linkedin.com/jobs)",
    "Indeed (indeed.com)",
    "Glassdoor (glassdoor.com/Job)",
    "Dice (dice.com)",
    "Wellfound (wellfound.com/jobs)",
    "Handshake (joinhandshake.com)",
    "Jobright AI (jobright.ai)",
    "MyVisaJobs (myvisajobs.com)",
    "OPTnation (optnation.com)",
    "Interstride (interstride.com/jobs)",
    "RippleMatch (ripplematch.com)",
    "Lensa (lensa.com)",
    "Built In (builtin.com)",
    "Y Combinator Jobs (ycombinator.com/jobs)",
    "Startup Jobs (startup.jobs)",
]

_ALLOWED_SOURCES_STR = "\n   ".join(f"• {s}" for s in _ALLOWED_SOURCES)


def build_job_search_prompt(profile: dict, prefs: str = "", location: str = "any",
                            max_years: int = 2, role_labels=None, target_roles=None,
                            core_skills=None, work_mode: str = "Any",
                            h1b_only: bool = False, count: int = 25,
                            resume_text: str = "", freshness_label: str = "the last 24 hours") -> str:
    skills = ", ".join((profile.get("skills", []) + profile.get("tools", []))[:14]) or "(see resume)"
    roles = ", ".join(target_roles or profile.get("target_roles", ["software engineer"]))
    focus = ", ".join(role_labels) if role_labels else "Software Engineer (general)"
    summary = profile.get("summary", "")
    priority_skills = ", ".join(core_skills) if core_skills else skills

    rt = (resume_text or profile.get("raw_text") or "").strip()
    resume_block = rt[:6000] if rt else "(only the structured profile above is available)"

    wm = (work_mode or "Any").strip()
    if wm and wm.lower() != "any":
        work_line = (f"- WORK ARRANGEMENT: return ONLY **{wm}** roles. Confirm the posting "
                     f"says {wm.lower()} (or 0-2 days in office for hybrid). Drop roles that "
                     f"don't match. Fill the work_mode field accordingly.")
    else:
        work_line = ("- WORK ARRANGEMENT: any (remote, hybrid, or onsite all fine). Still fill "
                     "the work_mode field from the posting.")

    if h1b_only:
        h1b_line = ("- H1B IS MANDATORY: return ONLY employers that have a recent, verifiable "
                    "H1B sponsorship history (check MyVisaJobs / H1BGrader). Drop any company "
                    "that does not sponsor. In h1b_note, state the evidence and the word verify.")
    else:
        h1b_line = ("- H1B: STRONGLY PREFER employers with H1B sponsorship history; rank them "
                    "first. In h1b_note say whether sponsorship looks likely and include verify.")

    return f"""# Job Search Assistant (CO-STAR)

## C - Context
You are my job-search assistant WITH LIVE WEB SEARCH. I am an ENTRY-LEVEL / NEW-GRAD
software engineer (graduating May 2026, MS Computer Science). Use my profile below to
find roles that genuinely match my REAL background. Application stays manual — you
only return links; you never apply.
- ROLE FOCUS (what to search for): {focus}
- Target job titles to match: {roles}
- Top skills: {skills}
- Skills to PRIORITIZE for this focus: {priority_skills}
- Location preference: {location}
- Other preferences: {prefs or "(none)"}
- Summary: {summary}

### MY FULL RÉSUMÉ (match roles against ALL of this — projects, bullets, metrics, tools)
{resume_block}

## O - Objective
Return a JSON array of up to {count} CURRENTLY OPEN, ENTRY-LEVEL / NEW-GRAD jobs that
match the ROLE FOCUS above — each with the EXACT live posting link and the FULL job
description copied from that posting. Relevance, link accuracy, 24-hour freshness, and
level-fit matter most. Quality over quantity — fewer truly-matching roles beats a padded list.

## ALLOWED SOURCES — SEARCH ONLY THESE SITES (no exceptions)
You MUST search exclusively on the following job boards. Do NOT return results from any
other website, random job board, or company career page unless the posting was FOUND via
one of these boards first.
   {_ALLOWED_SOURCES_STR}

For each lead found on these boards, navigate to the direct company/ATS posting URL
(Greenhouse, Lever, Workday, Ashby, etc.) to get the full JD and the canonical link.
The source field must name which board above you found it on.

## HARD RULES (follow ALL exactly — no exceptions)

### RULE 1 — 24-HOUR FRESHNESS (STRICT)
ONLY include roles posted within {freshness_label}.
- OPEN each posting and read the "posted" or "date posted" field.
- If posted_date is NOT visible OR is older than 24 hours → DROP the role immediately.
- "3 days ago", "5 days ago", "1 week ago" → DROP. These are too old.
- "Today", "Just now", "X hours ago", a date matching today ({freshness_label}) → KEEP.
- If the board shows "Posted 30+ days ago" or "Easy Apply" with no visible date → DROP.
- Evergreen / rolling new-grad pipelines (no close date, perpetually open) are allowed
  ONLY if they explicitly say "applications accepted year-round" — mark posted_date as
  "evergreen / rolling" in that case.

### RULE 2 — LINK MUST BE A SINGLE LIVE JOB PAGE
- OPEN each apply_link before including it.
- The URL must load ONE specific job description (not a search results page, not a company
  homepage, not a generic /careers page, not a board search filtered page).
- If the page 404s, redirects to a general page, or shows "Job no longer available" → DROP.
- Never return a LinkedIn /jobs/search/ or Indeed /jobs?q= URL — only a direct posting URL.
- Example of VALID: boards.greenhouse.io/company/jobs/12345678
- Example of INVALID: linkedin.com/jobs/search/?keywords=software+engineer

### RULE 3 — NEW GRAD / ENTRY LEVEL ONLY
- The title OR the first paragraph of the JD MUST contain at least one of:
  "new grad", "new college grad", "entry level", "entry-level", "university graduate",
  "associate", "2025", "2026", "0-2 years", "0–2 years", "recent graduate".
- EXCLUDE any title/JD with: Senior, Sr., Staff, Principal, Lead, Manager, Director,
  Architect, II, III, IV, L3, L4, L5, SWE 2, SWE 3.
- EXCLUDE roles requiring MORE than {max_years} years of experience unless the JD
  explicitly says "new grads welcome" or "0-{max_years} yrs".

### RULE 4 — NO MEMORY / NO GUESSING
Do NOT recall or guess jobs from memory or training data. EVERY job MUST come from
a live page you open in this session. If you cannot verify it live → drop it.

### RULE 5 — RÉSUMÉ RELEVANCE MANDATORY
Only include a role if my actual skills/projects plausibly match it. Reject off-domain
SWE specializations with no evidence in my résumé (wireless/RF, firmware/embedded,
kernel, robotics, hardware). Generic title alone is NOT enough — the JD's real
requirements must overlap mine.

### RULE 6 — EXCLUDE NON-ROLES
Drop: contract/C2C/1099, unpaid/"for-equity-only", internships (unless asked),
commission-only, staffing-agency reposts hiding real employer, "W2 only no sponsorship".

{work_line}
{h1b_line}

### RULE 7 — NO DUPLICATES / NO PADDING
If fewer than {count} pass all rules, RETURN FEWER. If none pass, return [].
Never pad with guesses. No duplicate companies unless roles are clearly different.

## S - Steps
1. Build 3-5 title variants: ROLE FOCUS + entry-level keyword + my top skills.
   Example: "New Grad Software Engineer Python", "Entry Level SWE Flask REST API",
   "University Graduate Software Engineer 2025 2026".
2. Search EACH of the allowed boards above for those title variants + "new grad" OR
   "entry level" + {location}. Filter by "last 24 hours" or "Today" date filter where
   available (LinkedIn: "Past 24 hours"; Indeed: "Last 24 hours"; Dice: "Today").
3. For each lead, open the direct posting URL. Verify: (a) loads a single job page,
   (b) posted within 24 hours, (c) matches new-grad rule, (d) matches role focus.
   Drop immediately if any check fails.
4. Navigate to the canonical company/ATS link (Greenhouse/Lever/Workday/Ashby) when
   available — prefer it over the board URL as the apply_link.
5. Apply ALL HARD RULES (freshness → link validity → level → focus → résumé → H1B).
6. Copy the FULL job description verbatim into job_description.
7. Score and rank: set fit_score by overlap with MY RÉSUMÉ specifically (not generic
   desirability). In fit_reason name the concrete overlap (e.g. "uses Python/Flask +
   REST APIs matching my ClimateAI project"). In matched_skills, list ONLY skills from
   my résumé. In gaps, list real requirements I lack.
   80+ = Apply immediately; 65-79 = Tailor first; below 65 = Skip.

## T - Output (return ONLY this JSON array — no markdown, no commentary)
[
  {{
    "company": "",
    "role": "",
    "location": "",
    "work_mode": "Remote / Hybrid / Onsite",
    "source": "LinkedIn / Indeed / Glassdoor / Dice / Wellfound / Handshake / Jobright AI / MyVisaJobs / OPTnation / Interstride / RippleMatch / Lensa / Built In / Y Combinator Jobs / Startup Jobs",
    "apply_link": "exact live single-posting URL (company ATS or board direct link)",
    "job_description": "full text copied verbatim from the posting",
    "fit_score": 0,
    "fit_reason": "one sentence specific to my background and this role focus",
    "matched_skills": ["skills from my resume that match"],
    "gaps": ["requirements I should NOT fake"],
    "experience_required": "",
    "posted_date": "exact date or 'X hours ago' or 'evergreen / rolling'",
    "salary": "",
    "h1b_note": "short note, always include the word verify",
    "priority": "Apply immediately / Tailor first / Skip"
  }}
]

## A - Audience
Me — international student / new grad SWE needing H1B sponsorship.
Results feed a local application tracker that parses this JSON, so schema + link accuracy must be exact.

## R - Reflection (run through ALL before returning)
1. Did EVERY job come from a live page opened in this session — zero from memory?
2. Is every job from one of the {len(_ALLOWED_SOURCES)} allowed boards above — NO other sites?
3. Is every apply_link a single live job page (opens one JD, no 404, no redirect to search/homepage)?
4. Is every posted_date within 24 hours or explicitly "evergreen / rolling"?
   → If ANY date says "X days ago" or "X weeks ago" → REMOVE that job before returning.
5. Does every role match the ROLE FOCUS ({focus})? Drop off-focus roles.
6. Did you drop all Senior/Staff/Lead/II+/{max_years}yr+ roles?
7. Does every role satisfy work-mode and H1B rules?
8. No duplicates? Return fewer, not padded guesses.

### THEN: paste the JSON array back into the app's "Paste AI results" box."""
