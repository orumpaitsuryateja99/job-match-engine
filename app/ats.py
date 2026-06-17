"""
ats.py — ATS match scoring. Implements the formula from the blueprint (§5).

ATS_score = 0.30*hard_skill + 0.25*keyword + 0.15*title + 0.10*tools
          + 0.10*experience + 0.05*domain + 0.05*formatting

Each component is 0–100. Returns the overall score, the per-dimension breakdown,
and — most useful of all — the list of missing skills/keywords to address.

This is an ESTIMATE to prioritize effort, not the employer's real ATS number.
"""
import re

from textutils import (detect_skills, detect_tools, detect_domains,
                       content_tokens, extract_years_required,
                       is_senior_title, normalize)
import latex_resume as ltx        # for extract_jd_keywords (tech-focused JD keywords)

# Weights sum to 1.0. Note: jobs reaching this scorer already passed the entry-level
# (≤ max_years) gate, so years_required is almost always 0-2 → the experience
# component is near-constant and barely discriminates. We give it a small 0.05 and
# put the freed weight on the dimensions that actually separate a good match from a
# bad one — hard-skill and keyword overlap with the résumé.
WEIGHTS = {
    "hard_skill": 0.33,
    "keyword": 0.27,
    "title": 0.15,
    "tools": 0.10,
    "experience": 0.05,
    "domain": 0.05,
    "formatting": 0.05,
}


def _pct(have: set, need: set) -> float:
    if not need:
        return 100.0
    return 100.0 * len(have & need) / len(need)


def parse_job(jd_text: str, title: str = "") -> dict:
    """Extract the structured signals we score against from a raw JD.

    `keywords` uses the tech-focused extractor (skills/tools + tech-looking tokens),
    the SAME one the résumé↔JD coverage metric uses — so the keyword dimension
    reflects real technical requirements, not the most-frequent English words."""
    return {
        "title": title or _first_line(jd_text),
        "skills": detect_skills(jd_text),
        "tools": detect_tools(jd_text),
        "domains": detect_domains(jd_text),
        "keywords": {k.lower() for k in ltx.extract_jd_keywords(jd_text)},
        "years_required": extract_years_required(jd_text),
        "raw": jd_text,
    }


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _title_alignment(jd_title: str, target_roles) -> float:
    """Token-overlap similarity between the JD title and your target roles.
    Tokenizes on words so punctuation ('Engineer,' / '(Backend)') doesn't break
    the match."""
    jd_tokens = set(re.findall(r"[a-z]+", normalize(jd_title)))
    if not jd_tokens:
        return 50.0
    best = 0.0
    for role in target_roles:
        rt = set(re.findall(r"[a-z]+", normalize(role)))
        if not rt:
            continue
        overlap = len(jd_tokens & rt) / len(rt)
        best = max(best, overlap)
    # senior/staff/lead titles (incl. "Sr.", "II", "L3") are a poor fit for a new grad
    if is_senior_title(jd_title):
        best *= 0.3
    return round(best * 100, 1)


def _experience_fit(years_required: int) -> float:
    if years_required <= 0:
        return 100.0
    if years_required <= 1:
        return 100.0
    if years_required == 2:
        return 90.0
    if years_required == 3:
        return 60.0
    if years_required == 4:
        return 35.0
    return 20.0


def _formatting_readiness(profile: dict) -> float:
    """Heuristic ATS-parse-ability score for the resume itself."""
    raw = (profile.get("raw_text") or "").lower()
    if not raw:
        return 90.0  # no raw text (e.g. a restored profile / our generated PDF): assume clean
    score = 100.0
    has_sections = sum(h in raw for h in ("experience", "education", "skills", "projects"))
    if has_sections < 2:
        score -= 30
    # crude multi-column / weird-char penalty
    if raw.count("\t") > 40:
        score -= 15
    return max(0.0, min(100.0, score))


def _profile_text(profile: dict) -> str:
    """Concatenate everything the candidate has written, for keyword coverage."""
    parts = [profile.get("summary", "")]
    for sec in profile.get("projects", []) + profile.get("experience", []):
        parts.append(sec.get("name", ""))
        parts.extend(sec.get("bullets", []))
    parts.extend(profile.get("skills", []))
    parts.extend(profile.get("tools", []))
    if profile.get("raw_text"):
        parts.append(profile["raw_text"])
    return " ".join(p for p in parts if p)


def profile_keyword_pool(profile: dict) -> set:
    """The candidate's keyword pool: every content word they've written PLUS the
    canonical skill/tool tokens. This is expensive (it re-reads + tokenizes the whole
    résumé) but IDENTICAL for every job in a batch — build it ONCE with this helper
    and hand it to ats_score(profile_keywords=...) when scoring many jobs."""
    return (content_tokens(_profile_text(profile))
            | set(profile.get("skills", [])) | set(profile.get("tools", [])))


def ats_score(profile: dict, job: dict, my_years: int = 0, target_roles=None,
              profile_keywords: set = None) -> dict:
    """Score a parsed `job` (from parse_job) against a `profile`.

    `target_roles` (optional) overrides the roles used for title alignment — pass
    the user's chosen role-focus roles so a 'Frontend Engineer' title scores well
    when the user is hunting frontend, instead of against the profile default.
    `profile_keywords` (optional) reuses a pre-built keyword pool (see
    profile_keyword_pool) so a batch of jobs doesn't rebuild it per call."""
    my_skills = set(profile.get("skills", []))
    my_tools = set(profile.get("tools", []))
    my_domains = set(profile.get("domains", []))
    # Keyword pool = every content word the candidate has written, plus the
    # canonical skill/tool tokens. This is what makes keyword-coverage fair.
    my_keywords = (profile_keywords if profile_keywords is not None
                   else profile_keyword_pool(profile))

    roles_for_title = target_roles or profile.get("target_roles", [])
    comp = {
        "hard_skill": round(_pct(my_skills, job["skills"]), 1),
        "keyword": round(_pct(my_keywords, job["keywords"]), 1),
        "title": _title_alignment(job["title"], roles_for_title),
        "tools": round(_pct(my_tools, job["tools"]), 1),
        "experience": _experience_fit(job["years_required"]),
        "domain": 100.0 if (my_domains & job["domains"]) else (50.0 if job["domains"] else 75.0),
        "formatting": _formatting_readiness(profile),
    }

    overall = sum(comp[k] * WEIGHTS[k] for k in WEIGHTS)

    missing_skills = sorted(job["skills"] - my_skills)
    missing_tools = sorted(job["tools"] - my_tools)
    missing_keywords = sorted(job["keywords"] - my_keywords)[:12]

    return {
        "score": round(overall),
        "band": band(overall),
        "components": comp,
        "missing_skills": missing_skills,
        "missing_tools": missing_tools,
        "missing_keywords": missing_keywords,
        "matched_skills": sorted(my_skills & job["skills"]),
    }


def band(score: float) -> str:
    if score >= 85:
        return "Strong"
    if score >= 70:
        return "Good"
    if score >= 55:
        return "Stretch"
    return "Weak"


def score_resume_vs_jd(profile: dict, jd_text: str, title: str = "", my_years: int = 0) -> dict:
    """Convenience: parse a raw JD and score it in one call."""
    return ats_score(profile, parse_job(jd_text, title), my_years=my_years)
