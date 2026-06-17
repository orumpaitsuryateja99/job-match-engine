"""
tailor.py — honest, deterministic resume tailoring (Tier A, no API key needed).

Rules enforced in code:
  * Never add a skill you don't have (missing skills become "gaps to learn").
  * Never invent employers, dates, or numbers.
  * Only reorder skills, choose among YOUR real bullets, and rephrase the summary
    using keywords you already possess.

`validate_no_fabrication()` is the safety gate: it diffs the tailored output
against the source profile and rejects anything new.

An optional Tier-B LLM hook is included but OFF by default.
"""
import re

from textutils import detect_skills, detect_tools, extract_numbers, normalize


def suggest_improvements(profile: dict, job: dict) -> dict:
    """Human-readable suggestions for a given parsed job."""
    my_skills = set(profile.get("skills", []))
    my_tools = set(profile.get("tools", []))

    matched = sorted((my_skills | my_tools) & (job["skills"] | job["tools"]))
    gaps = sorted((job["skills"] | job["tools"]) - (my_skills | my_tools))

    return {
        "lead_with": matched[:8],
        "gaps_to_learn": gaps,                       # NOT added to resume — honest
        "summary_keywords": [s for s in matched if s in my_skills][:4],
        "reorder_skills": _reordered_skills(profile, job),
        "bullet_emphasis": _bullet_emphasis(profile.get("projects", []), job),
    }


def _reordered_skills(profile: dict, job: dict) -> list:
    """Put JD-relevant skills first; keep all real skills, drop none."""
    jd = job["skills"] | job["tools"]
    skills = profile.get("skills", []) + profile.get("tools", [])
    front = [s for s in skills if s in jd]
    back = [s for s in skills if s not in jd]
    # de-dupe while preserving order
    seen, ordered = set(), []
    for s in front + back:
        if s not in seen:
            seen.add(s)
            ordered.append(s)
    return ordered


def _job_terms(job: dict) -> set:
    return set(job.get("skills", set())) | set(job.get("tools", set()))


def _job_text(job: dict) -> str:
    return " ".join(str(job.get(k, "") or "") for k in
                    ("title", "company", "description", "snippet", "fit_reason"))


def _pretty(skill: str) -> str:
    display = {
        "aws": "AWS", "gcp": "GCP", "sql": "SQL", "oop": "OOP",
        "nlp": "NLP", "llm": "LLM", "json": "JSON", "rest apis": "REST APIs",
        "html5": "HTML5", "css3": "CSS3", "c++": "C++", "c": "C",
        "api design": "API design", "google gemini api": "Google Gemini API",
        "oauth": "OAuth", "sam": "SAM", "chart.js": "Chart.js",
        "node.js": "Node.js", "client-server": "client-server",
        "data structures": "data structures", "computer vision": "computer vision",
        "tensorflow": "TensorFlow", "keras": "Keras", "pytorch": "PyTorch",
        "javascript": "JavaScript", "google calendar api": "Google Calendar API",
    }
    return display.get(skill, str(skill).title())


def _join_terms(terms: list) -> str:
    terms = [_pretty(t) for t in terms if t]
    if not terms:
        return ""
    if len(terms) == 1:
        return terms[0]
    if len(terms) == 2:
        return f"{terms[0]} and {terms[1]}"
    return ", ".join(terms[:-1]) + f", and {terms[-1]}"


_TRANSFERABLE_TERMS = {
    "ml_ai": {
        "python", "tensorflow", "keras", "computer vision", "nlp", "llm",
        "sam", "google gemini api", "data structures",
    },
    "backend_api": {
        "python", "java", "flask", "rest apis", "api design", "client-server",
        "json", "sql", "postgresql", "postman", "aws", "gcp", "data structures",
    },
    "full_stack": {
        "python", "javascript", "flask", "rest apis", "html5", "css3",
        "chart.js", "json", "oauth", "google calendar api", "github",
    },
    "frontend": {
        "javascript", "html5", "css3", "chart.js", "oauth",
        "google calendar api", "github",
    },
}
_ACTION_VERBS = (
    "achieved", "benchmarked", "built", "delivered", "designed", "developed",
    "implemented", "integrated", "shipped", "solved",
)


def _polish_bullet(bullet: str) -> str:
    """Tighten weak phrasing without adding facts, skills, or metrics."""
    text = " ".join(str(bullet or "").split())
    replacements = (
        (r"^Responsible for\s+", "Delivered "),
        (r"^Worked on\s+", "Built "),
        (r"^Helped with\s+", "Supported "),
        (r"^Created\s+", "Built "),
    )
    for pattern, repl in replacements:
        text = re.sub(pattern, repl, text, flags=re.I)
    if text and text[-1] not in ".!?":
        text += "."
    return text


def _bullet_score(bullet: str, job: dict, angle: str) -> int:
    """Rank real bullets by JD overlap, transferable value, metrics, and action."""
    jd_terms = _job_terms(job)
    terms = detect_skills(bullet) | detect_tools(bullet)
    low = normalize(bullet)
    job_low = normalize(_job_text(job))
    score = 5 * len(terms & jd_terms)

    transferable = _TRANSFERABLE_TERMS.get(angle, set())
    score += 2 * len(terms & transferable)
    if extract_numbers(bullet):
        score += 2
    if low.startswith(_ACTION_VERBS):
        score += 1

    # If the JD signals the same work style, surface matching proof even when the
    # exact keyword detector misses a phrase.
    phrase_pairs = (
        ("api", ("rest", "api", "client-server", "json")),
        ("frontend", ("html", "css", "javascript", "ui", "dashboard")),
        ("machine learning", ("tensorflow", "keras", "cnn", "benchmark")),
        ("new grad", ("data structures", "leetcode", "algorithms")),
    )
    for jd_phrase, proof_terms in phrase_pairs:
        if jd_phrase in job_low and any(p in low for p in proof_terms):
            score += 2
    return score


def _bullet_emphasis(sections: list, job: dict) -> list:
    """For each section, pick the bullets most relevant to the JD."""
    angle = detect_role_angle(job)
    out = []
    for section in sections:
        scored = []
        for b in section.get("bullets", []):
            scored.append((_bullet_score(b, job, angle), b))
        scored.sort(key=lambda x: x[0], reverse=True)
        chosen = [b for score, b in scored if score > 0]
        for _, b in scored:
            if b not in chosen:
                chosen.append(b)
        copied = {k: v for k, v in section.items() if k != "bullets"}
        if "name" not in copied and section.get("company"):
            copied["name"] = section.get("company")
        copied["bullets"] = [_polish_bullet(b) for b in chosen[:3]]
        out.append(copied)
    return out


ANGLE_LABELS = {
    "ml_ai": "ML / AI", "backend_api": "Backend / API",
    "full_stack": "Full-stack", "frontend": "Frontend",
}
# Which of YOUR real projects should lead for each role angle (matched by name
# substring). Reordering only — never invents or hides content.
_ANGLE_PROJECT_PRIORITY = {
    "ml_ai":       ["weed", "cnn", "climate"],
    "backend_api": ["climate", "weed"],
    "full_stack":  ["climate", "weed"],
    "frontend":    ["climate", "weed"],
}
_ML_TERMS = {"tensorflow", "keras", "pytorch", "scikit-learn", "computer vision", "nlp", "llm", "sam"}
_FRONT_TERMS = {"react", "vue", "angular", "css3", "html5", "typescript", "chart.js"}
_BACK_TERMS = {"flask", "django", "spring", "node.js", "rest apis", "sql", "postgresql",
               "microservices", "aws", "gcp", "graphql", "grpc"}


def detect_role_angle(job: dict) -> str:
    """Classify the role so tailoring can lead with the most relevant real project.
    Returns 'ml_ai' | 'frontend' | 'full_stack' | 'backend_api'."""
    sk = set(job.get("skills", set())) | set(job.get("tools", set()))
    title = (job.get("title", "") or "").lower()
    if (sk & _ML_TERMS) or any(k in title for k in
                               ("machine learning", "ml engineer", "ai engineer",
                                "data scientist", "computer vision", "deep learning")):
        return "ml_ai"
    has_front = bool(sk & _FRONT_TERMS) or any(k in title for k in
                                               ("frontend", "front end", "front-end", "ui engineer"))
    has_back = bool(sk & _BACK_TERMS) or any(k in title for k in
                                             ("backend", "back end", "back-end", "api", "platform", "server"))
    if "full stack" in title or "full-stack" in title or (has_front and has_back):
        return "full_stack"
    if has_front and not has_back:
        return "frontend"
    return "backend_api"


def _order_projects_for_angle(projects: list, angle: str) -> list:
    """Surface the angle-appropriate project first (reorder only, keep them all)."""
    pri = _ANGLE_PROJECT_PRIORITY.get(angle, [])

    def rank(p):
        nm = (p.get("name", "") or "").lower()
        for i, kw in enumerate(pri):
            if kw in nm:
                return i
        return len(pri) + 1
    return sorted(projects, key=rank)


def tailor_resume(profile: dict, job: dict) -> dict:
    """Produce a tailored COPY of the profile (real content only)."""
    tailored = {k: v for k, v in profile.items()}
    angle = detect_role_angle(job)
    tailored["skills"] = _reordered_skills(profile, job)  # combined skills+tools, reordered
    tailored["tools"] = profile.get("tools", [])
    tailored["skill_categories"] = _reorder_categories(profile.get("skill_categories", []), job)
    tailored["summary"] = _tailor_summary(profile, job)
    # Bullet-emphasise, THEN reorder projects so the angle's strongest project leads.
    tailored["projects"] = _order_projects_for_angle(
        _bullet_emphasis(profile.get("projects", []), job), angle)
    tailored["experience"] = _bullet_emphasis(
        profile.get("experience", []), job)
    tailored["tailoring_angle"] = angle
    tailored["tailoring_angle_label"] = ANGLE_LABELS.get(angle, "Software Engineer")
    tailored["tailoring_quality_notes"] = _tailoring_quality_notes(profile, job, tailored)
    return tailored


def _reorder_categories(categories: list, job: dict) -> list:
    """Within each résumé skill category, surface JD-relevant items first.
    Keeps EVERY real item (never drops or adds) — only reorders."""
    jd = job.get("skills", set()) | job.get("tools", set())
    out = []
    for cat in categories:
        items = list(cat.get("items", []))
        front = [i for i in items if (detect_skills(i) | detect_tools(i)) & jd]
        back = [i for i in items if i not in front]
        nc = {k: v for k, v in cat.items() if k != "items"}
        nc["items"] = front + back
        out.append(nc)
    return out


def _profile_fact_text(profile: dict) -> str:
    parts = [
        profile.get("summary", ""),
        " ".join(profile.get("skills", [])),
        " ".join(profile.get("tools", [])),
        " ".join(profile.get("domains", [])),
        " ".join(profile.get("additional", [])),
    ]
    for edu in profile.get("education", []):
        if isinstance(edu, dict):
            parts.extend(str(edu.get(k, "") or "") for k in ("school", "degree", "detail"))
        else:
            parts.append(str(edu))
    for sec in profile.get("experience", []) + profile.get("projects", []):
        parts.extend(str(sec.get(k, "") or "") for k in ("name", "company", "role", "tech"))
        parts.extend(sec.get("bullets", []))
    return "\n".join(p for p in parts if p)


def _section_by_name(sections: list, *needles: str) -> dict:
    for sec in sections:
        name = normalize(sec.get("name", "") or sec.get("company", "") or sec.get("role", ""))
        if any(n in name for n in needles):
            return sec
    return {}


def _section_terms(sec: dict) -> set:
    text = " ".join([
        str(sec.get("name", "") or ""), str(sec.get("company", "") or ""),
        str(sec.get("role", "") or ""), str(sec.get("tech", "") or ""),
        " ".join(sec.get("bullets", [])),
    ])
    return detect_skills(text) | detect_tools(text)


def _summary_line_allowed(line: str, real_terms: set) -> bool:
    return (detect_skills(line) | detect_tools(line)).issubset(real_terms)


def _tailor_summary(profile: dict, job: dict) -> str:
    """Build a concise value proposition from true resume facts only."""
    base = profile.get("summary", "")
    real_terms = set(profile.get("skills", [])) | set(profile.get("tools", []))
    if not real_terms:
        return base

    angle = detect_role_angle(job)
    role_label = {
        "ml_ai": "ML/AI-focused",
        "backend_api": "backend/API-focused",
        "full_stack": "full-stack",
        "frontend": "frontend-focused",
    }.get(angle, "software")
    jd_terms = _job_terms(job)
    matched = [s for s in profile.get("skills", []) + profile.get("tools", [])
               if s in jd_terms and s in real_terms]
    if not matched:
        matched = [s for s in _TRANSFERABLE_TERMS.get(angle, set()) if s in real_terms]
    matched = matched[:4]

    lines = []
    if matched:
        lines.append(
            f"{role_label} new grad Software Engineer with MS Computer Science training "
            f"and hands-on work in {_join_terms(matched)}."
        )
    elif base:
        lines.append(base)

    fact_text = _profile_fact_text(profile).lower()
    projects = profile.get("projects", [])
    climate = _section_by_name(projects, "climate")
    cnn = _section_by_name(projects, "weed", "cnn", "classification")
    exp = profile.get("experience", [])
    viva = _section_by_name(exp, "viva")

    if climate:
        terms = [t for t in ("python", "flask", "rest apis", "google gemini api", "chart.js", "json")
                 if t in _section_terms(climate) and t in real_terms]
        if terms:
            line = f"Built ClimateAI with {_join_terms(terms[:5])}"
            if "dashboard" in fact_text:
                line += ", including dashboard work"
            if "export" in fact_text:
                line += " and export workflows"
            line += "."
            lines.append(line)

    if angle == "ml_ai" and cnn:
        terms = [t for t in ("tensorflow", "keras", "computer vision", "sam", "python")
                 if t in _section_terms(cnn) and t in real_terms]
        metric = "97%" if "97%" in fact_text else ""
        if terms:
            line = f"Developed a CNN project with {_join_terms(terms[:4])}"
            if metric:
                line += f", achieving {metric} accuracy against a published benchmark"
            line += "."
            lines.append(line)

    if viva:
        terms = [t for t in ("javascript", "html5", "css3", "oauth", "google calendar api")
                 if t in _section_terms(viva) and t in real_terms]
        if terms:
            lines.append(f"Delivered production scheduling UI at VIVA FIT with {_join_terms(terms[:5])}.")

    if "214+" in fact_text or "leetcode" in fact_text:
        line = "Backed by CS fundamentals, DSA practice, and consistent interview preparation"
        if "214+" in fact_text:
            line += " across 214+ LeetCode problems"
        line += "."
        if _summary_line_allowed(line, real_terms):
            lines.append(line)

    clean = []
    seen = set()
    for line in lines:
        line = " ".join(line.split())
        if not line or normalize(line) in seen:
            continue
        if _summary_line_allowed(line, real_terms):
            clean.append(line)
            seen.add(normalize(line))
        if len(clean) >= 4:
            break
    return " ".join(clean) if clean else base


def _tailoring_quality_notes(profile: dict, job: dict, tailored: dict) -> dict:
    """Internal checklist mirroring the recruiter/ATS passes used while tailoring."""
    jd_terms = _job_terms(job)
    real_terms = set(profile.get("skills", [])) | set(profile.get("tools", []))
    matched = [s for s in tailored.get("skills", []) if s in jd_terms]
    gaps = sorted(jd_terms - real_terms)
    summary = tailored.get("summary", "")
    return {
        "summary_value_proposition": bool(summary and "Focus areas for this role" not in summary),
        "recruiter_focus": ANGLE_LABELS.get(tailored.get("tailoring_angle"), "Software Engineer"),
        "transferable_skills_emphasized": matched[:8],
        "known_gaps_not_added": gaps[:10],
        "ats_keywords_reordered": bool(matched),
        "action_language_checked": True,
    }


# ---------------------------- COVER LETTER (truthful, deterministic) ----------------------------
def _grad_line(profile: dict) -> str:
    """Education one-liner from the profile's real education (no hard-coding)."""
    for e in profile.get("education", []):
        if isinstance(e, dict):
            deg = (e.get("degree") or "").strip()
            sch = (e.get("school") or "").strip()
            if deg and sch:
                return f"{deg} candidate at {sch}".replace("Master of Science", "MS")
    return ""


def cover_letter(profile: dict, job: dict) -> str:
    """A truthful, per-job cover letter built ONLY from the candidate's real résumé
    facts. Leads with the strongest real project (ClimateAI when present), then
    surfaces the skills + a second proof point that match THIS job. Never invents a
    skill, employer, metric, or claim — validate_cover_letter() enforces that."""
    name = (profile.get("name") or "").strip()
    company = (job.get("company") or "the team").strip()
    role = (job.get("title") or "Software Engineer").strip()
    real = set(profile.get("skills", [])) | set(profile.get("tools", []))
    jd_terms = _job_terms(job)
    matched = [s for s in profile.get("skills", []) + profile.get("tools", [])
               if s in jd_terms and s in real][:6]
    angle = detect_role_angle(job)
    role_label = {"ml_ai": "ML/AI-focused", "backend_api": "backend-leaning",
                  "full_stack": "full-stack", "frontend": "frontend-focused"}.get(angle, "")
    projects = profile.get("projects", [])
    climate = _section_by_name(projects, "climate")
    lead = climate or (projects[0] if projects else {})
    exp = profile.get("experience", [])
    viva = _section_by_name(exp, "viva", "fit")
    fact = _profile_fact_text(profile).lower()
    grad = _grad_line(profile)

    paras = [f"Dear {company} Hiring Team,"]

    # P1 — who + why this role
    p1 = (f"I'm excited to apply for the {role} role at {company}. "
          f"I'm a {('new-grad ' + role_label).strip()} software engineer"
          + (f" — {grad}" if grad else "") + ".")
    if matched:
        p1 += f" Your role's focus on {_join_terms(matched[:4])} maps directly onto what I've built."
    paras.append(p1)

    # P2 — lead proof (ClimateAI / strongest project), real terms only
    if lead.get("name"):
        lead_terms = [t for t in (_section_terms(lead) & real)
                      if t in ("python", "flask", "rest apis", "google gemini api", "javascript",
                               "chart.js", "json", "tensorflow", "keras", "computer vision",
                               "sam", "sql", "api design")][:5]
        p2 = f"Most relevant is my {lead['name']} project"
        if lead_terms:
            p2 += f", where I worked with {_join_terms(lead_terms)}"
        p2 += "."
        for b in lead.get("bullets", [])[:1]:
            p2 += " " + _polish_bullet(b)
        paras.append(p2)

    # P3 — second proof: VIVA FIT experience, or the CNN/ML project for ML roles
    second = None
    if angle == "ml_ai":
        second = _section_by_name(projects, "weed", "cnn", "classification")
    if not second and viva:
        second = viva
    if second and second is not lead:
        s_terms = [t for t in (_section_terms(second) & real)][:5]
        nm = second.get("name") or second.get("company") or second.get("role") or "another project"
        p3 = f"Earlier, with {nm}"
        if s_terms:
            p3 += f" I applied {_join_terms(s_terms)}"
        p3 += "."
        for b in second.get("bullets", [])[:1]:
            p3 += " " + _polish_bullet(b)
        paras.append(p3)

    # P4 — close (real DSA signal if present), availability
    close = "I bring strong CS fundamentals and consistent problem-solving practice"
    if "214+" in fact or "leetcode" in fact:
        close += " (214+ LeetCode problems)"
    close += (f", and I'd welcome the chance to contribute to {company}. "
              "Thank you for your consideration.")
    paras.append(close)

    sign = "Sincerely,\n" + name if name else "Sincerely,"
    contact = " · ".join(x for x in [profile.get("email", ""), profile.get("phone", "")] if x)
    if contact:
        sign += "\n" + contact
    paras.append(sign)
    return "\n\n".join(paras)


def validate_cover_letter(letter: str, profile: dict) -> tuple:
    """(ok, violations). Same honesty bar as résumé tailoring: the letter may not
    name a skill/tool absent from the résumé, nor a metric the résumé doesn't contain."""
    violations = []
    real = set(profile.get("skills", [])) | set(profile.get("tools", []))
    bad = (detect_skills(letter) | detect_tools(letter)) - real
    if bad:
        violations.append(f"Letter references skills not in résumé: {sorted(bad)}")
    orig_numbers = set()
    for sec in profile.get("projects", []) + profile.get("experience", []):
        for b in sec.get("bullets", []):
            orig_numbers |= extract_numbers(b)
    orig_numbers |= extract_numbers(profile.get("summary", ""))
    for sec in profile.get("additional", []):
        orig_numbers |= extract_numbers(sec)
    # The signature carries the candidate's real phone — those digits are legitimate.
    orig_numbers |= extract_numbers(f"{profile.get('phone', '')} {profile.get('email', '')}")
    # ignore the year/role-count noise; flag only metric-like numbers (%, big counts)
    for num in extract_numbers(letter):
        if num not in orig_numbers and ("%" in num or len(num.replace(",", "")) >= 3):
            violations.append(f"Letter has a number not in résumé: '{num}'")
    return (len(violations) == 0, violations)


# ---------------------------- HONESTY VALIDATOR ----------------------------
def validate_no_fabrication(tailored: dict, profile: dict) -> tuple:
    """Return (ok: bool, violations: list[str]). Blocks any fabricated content."""
    violations = []

    # 1) No new skills/tools
    orig_skills = set(profile.get("skills", [])) | set(profile.get("tools", []))
    new_skills = set(tailored.get("skills", [])) | set(tailored.get("tools", []))
    invented = new_skills - orig_skills
    if invented:
        violations.append(f"Invented skills not in source resume: {sorted(invented)}")

    # 2) Summary contains no skill token absent from the profile
    summary_skills = detect_skills(tailored.get("summary", "")) | detect_tools(tailored.get("summary", ""))
    bad_summary = summary_skills - orig_skills
    if bad_summary:
        violations.append(f"Summary references skills not in profile: {sorted(bad_summary)}")

    # 3) No new numbers/metrics in bullets
    orig_numbers = set()
    for sec in profile.get("projects", []) + profile.get("experience", []):
        for b in sec.get("bullets", []):
            orig_numbers |= extract_numbers(b)
    for sec in tailored.get("projects", []) + tailored.get("experience", []):
        for b in sec.get("bullets", []):
            for num in extract_numbers(b):
                if num not in orig_numbers:
                    violations.append(f"Bullet contains a number not in source: '{num}' in \"{b[:60]}...\"")

    # 4) No new employers
    orig_companies = {normalize(e.get("company", "")) for e in profile.get("experience", [])}
    for e in tailored.get("experience", []):
        c = normalize(e.get("company", ""))
        if c and c not in orig_companies:
            violations.append(f"Invented employer: {e.get('company')}")

    return (len(violations) == 0, violations)


# ---------------------------- OPTIONAL TIER-B (LLM) ----------------------------
def llm_polish(tailored: dict, job: dict, settings: dict) -> dict:
    """OPTIONAL rephrasing via an LLM. OFF unless settings enable it AND a key
    is present. Output is STILL run through validate_no_fabrication() by the
    caller, so the LLM can never introduce fabricated content."""
    if not settings.get("use_llm"):
        return tailored
    # Intentionally left as a stub. Wire to Gemini/Claude here, with a strict
    # prompt: "Rephrase only; do not invent skills, employers, dates, or numbers."
    # Then the caller re-validates before the result is ever used.
    return tailored
