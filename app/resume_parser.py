"""
resume_parser.py — turn a resume file into a structured profile dict.

Supports .pdf (PyMuPDF), .docx/.doc (python-docx), .txt/.md (plain text), and
.tex (routed through latex_resume.tex_to_text; the raw source is also kept as the
active LaTeX template). Falls back gracefully if a library is missing. The output
`profile` flows through scoring, tailoring, and PDF generation, so keep its shape
stable.

profile = {
    name, email, phone, links{}, summary,
    skills[], tools[], domains[],
    projects[{name, bullets[]}], experience[{company, role, bullets[]}],
    target_roles[], experience_years, raw_text
}
"""
import json
import os
import re

from textutils import detect_skills, detect_tools, detect_domains
from latex_resume import tex_to_text

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")


def _read_via_markitdown(path: str) -> str:
    from markitdown import MarkItDown
    md = MarkItDown()
    result = md.convert(path)
    return result.text_content or ""


def _looks_garbled(text: str) -> bool:
    """Detect degenerate markitdown output (e.g. a PDF mis-read as pipe tables:
    '|  |  | Suryateja |  |'). If a large share of lines are pipe-table rows,
    the extraction is unusable for section parsing — fall back to PyMuPDF."""
    lines = [l for l in (text or "").splitlines() if l.strip()]
    if not lines:
        return True
    pipey = sum(1 for l in lines
                if l.lstrip().startswith("|") or re.search(r"\|\s*---", l))
    return pipey / len(lines) > 0.20


def _read_pdf(path: str) -> str:
    try:
        text = _read_via_markitdown(path)
        if text.strip() and not _looks_garbled(text):
            return text
    except Exception:
        pass
    import fitz  # PyMuPDF fallback
    text = []
    with fitz.open(path) as doc:
        for page in doc:
            text.append(page.get_text("text"))
    return "\n".join(text)


def _read_pdf_links(path: str) -> list:
    import fitz  # PyMuPDF
    links = []
    with fitz.open(path) as doc:
        for page in doc:
            for link in page.get_links():
                uri = link.get("uri")
                if uri:
                    links.append(uri)
    return links


def _read_docx(path: str) -> str:
    try:
        text = _read_via_markitdown(path)
        if text.strip():
            return text
    except Exception:
        pass
    import docx  # python-docx fallback
    d = docx.Document(path)
    return "\n".join(p.text for p in d.paragraphs)


def read_resume_text(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return _read_pdf(path)
    if ext in (".docx", ".doc"):
        return _read_docx(path)
    if ext == ".tex":
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return tex_to_text(f.read())
    if ext in (".txt", ".md"):
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    raise ValueError(f"Unsupported resume format: {ext}")


def parse_resume(path: str, target_roles=None) -> dict:
    """Parse a resume file into a profile dict."""
    raw = read_resume_text(path)
    profile = build_profile_from_text(raw, target_roles=target_roles)
    if os.path.splitext(path)[1].lower() == ".pdf":
        _apply_links(profile, _read_pdf_links(path))
    if os.path.splitext(path)[1].lower() == ".tex":
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            profile["latex_template"] = f.read()
    return profile


def build_profile_from_text(raw: str, target_roles=None) -> dict:
    email = (EMAIL_RE.search(raw) or [None])
    email = email.group(0) if hasattr(email, "group") else None
    phone = PHONE_RE.search(raw)
    phone = phone.group(0) if phone else None

    # first non-empty line is usually the name
    name = None
    for line in raw.splitlines():
        s = line.strip()
        if s and not EMAIL_RE.search(s) and not PHONE_RE.search(s) and len(s) < 60:
            name = s
            break

    links = _extract_links(raw)
    skill_categories = _parse_skill_categories(raw)
    detected_skills = set(detect_skills(raw)) | _skills_from_skill_categories(skill_categories)
    detected_tools = set(detect_tools(raw)) | _tools_from_skill_categories(skill_categories)
    profile = {
        "name": name or "Candidate",
        "email": email,
        "phone": phone,
        "links": links,
        "summary": _extract_summary(raw) or _guess_summary(raw),
        "skill_categories": skill_categories,
        "skill_phrases": _flatten_skill_categories(skill_categories),
        "skills": sorted(detected_skills),
        "tools": sorted(detected_tools),
        "domains": sorted(detect_domains(raw)),
        "projects": _parse_projects(raw) or _guess_sections(raw, ("project", "projects")),
        "experience": _parse_experience(raw) or _guess_sections(raw, ("experience", "employment", "work")),
        "education": _parse_education(raw),
        "additional": _parse_additional(raw),
        "target_roles": target_roles or ["software engineer", "backend engineer", "full stack engineer"],
        "experience_years": 0,  # new grad default; override in config if needed
        "raw_text": raw,
        "latex_template": "",
    }
    return profile


def _clean_line(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _lines(raw: str) -> list:
    return [_clean_line(x) for x in (raw or "").splitlines() if _clean_line(x)]


def _norm_heading(s: str) -> str:
    return _clean_line(s).lower().rstrip(":")


def _block_between(raw: str, start: str, end: str = None) -> list:
    lines = _lines(raw)
    headings = [_norm_heading(x) for x in lines]
    try:
        i = headings.index(start.lower()) + 1
    except ValueError:
        return []
    j = len(lines)
    if end:
        try:
            j = headings.index(end.lower(), i)
        except ValueError:
            pass
    else:
        known = {h.lower() for h in SECTION_HEADERS}
        for k in range(i, len(lines)):
            if headings[k] in known:
                j = k
                break
    return lines[i:j]


def _is_date_range(s: str) -> bool:
    return bool(re.search(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{4}\s*[-–]\s*(?:present|\w+\s+\d{4})\b", s, re.I))


def _is_link_marker(s: str) -> bool:
    return bool(re.fullmatch(r"(?:\[[^\]]+\]\s*)+", s or ""))


def _is_bullet(s: str) -> bool:
    return bool(_BULLET_RE.match(s))


def _collect_bullets(lines: list, start: int) -> tuple:
    bullets, cur = [], ""
    i = start
    while i < len(lines):
        s = lines[i]
        if _is_bullet(s):
            if cur:
                bullets.append(cur)
            cur = _BULLET_RE.sub("", s).strip()
        elif cur:
            cur += " " + s
        else:
            break
        i += 1
    if cur:
        bullets.append(cur)
    return bullets, i


def _extract_links(raw: str) -> dict:
    links = {}
    text = raw or ""
    linkedin = re.search(r"(?:https?://)?linkedin\.com/[^\s·|]+", text, re.I)
    github = re.search(r"(?:https?://)?github\.com/[^\s·|]+", text, re.I)
    portfolio = re.search(r"(?:https?://)?[\w.-]+\.github\.io/[^\s·|]+", text, re.I)
    if linkedin:
        links["linkedin"] = linkedin.group(0).replace("https://", "")
    if github:
        links["github"] = github.group(0).replace("https://", "")
    if portfolio:
        links["portfolio"] = portfolio.group(0).replace("https://", "")
    return links


def _split_csv_honoring_parens(text: str) -> list:
    items, buf, depth = [], [], 0
    for ch in text or "":
        if ch == "(":
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
        if ch == "," and depth == 0:
            item = _clean_line("".join(buf))
            if item:
                items.append(item)
            buf = []
        else:
            buf.append(ch)
    item = _clean_line("".join(buf))
    if item:
        items.append(item)
    return items


def _parse_skill_categories(raw: str) -> list:
    """Preserve the exact resume Technical Skills categories for display and
    matching context. This keeps items like GitHub, Chart.js, project APIs, and
    parenthesized model lists from getting lost in the normalized detector."""
    lines = _block_between(raw, "technical skills", "work experience")
    categories = []
    for line in lines:
        if ":" in line:
            label, rest = line.split(":", 1)
            categories.append({
                "category": _clean_line(label),
                "items": _split_csv_honoring_parens(rest),
            })
        elif categories:
            categories[-1]["items"].extend(_split_csv_honoring_parens(line))
    return [c for c in categories if c.get("category") and c.get("items")]


def _flatten_skill_categories(categories: list) -> list:
    seen, out = set(), []
    for cat in categories or []:
        for item in cat.get("items", []):
            key = item.lower()
            if key not in seen:
                seen.add(key)
                out.append(item)
    return out


_EXACT_SKILL_MAP = {
    "c": "c",
    "c++": "c++",
    "chart.js": "chart.js",
    "google gemini api": "google gemini api",
    "sam": "sam",
    "google calendar api": "google calendar api",
    "oauth": "oauth",
    "restful web services": "rest apis",
    "data structures & algorithms": "data structures",
    "object-oriented programming": "oop",
    "agile / scrum": "agile",
    "google cloud apis": "gcp",
}


_EXACT_TOOL_MAP = {
    "git": "git",
    "github": "github",
    "vs code": "vs code",
    "postman": "postman",
}


def _skills_from_skill_categories(categories: list) -> set:
    out = set()
    for item in _flatten_skill_categories(categories):
        key = re.sub(r"\s*\([^)]*\)", "", item).strip().lower()
        if key in _EXACT_SKILL_MAP:
            out.add(_EXACT_SKILL_MAP[key])
    return out


def _tools_from_skill_categories(categories: list) -> set:
    out = set()
    for item in _flatten_skill_categories(categories):
        key = re.sub(r"\s*\([^)]*\)", "", item).strip().lower()
        if key in _EXACT_TOOL_MAP:
            out.add(_EXACT_TOOL_MAP[key])
    return out


def _apply_links(profile: dict, uris: list):
    clean = [u for u in uris if u and not u.startswith("mailto:")]
    links = profile.setdefault("links", {})
    for u in clean:
        lu = u.lower()
        if "linkedin.com" in lu and "linkedin" not in links:
            links["linkedin"] = u.replace("https://", "")
        elif "github.com" in lu and "github" not in links and lu.rstrip("/").count("/") <= 3:
            links["github"] = u.replace("https://", "")
        elif "github.io" in lu and "portfolio" not in links:
            links["portfolio"] = u

    projects = profile.get("projects", [])
    for p in projects:
        name = (p.get("name") or "").lower()
        p_links = p.setdefault("links", {})
        for u in clean:
            lu = u.lower()
            if "climate" in name and "climate" in lu:
                if "github.com" in lu:
                    p_links.setdefault("github", u)
                else:
                    p_links.setdefault("live", u)
            if "weed" in name and "github.com" in lu and "weed" in lu:
                p_links.setdefault("github", u)
    for e in profile.get("experience", []):
        company = (e.get("company") or "").lower()
        e_links = e.setdefault("links", {})
        for u in clean:
            lu = u.lower()
            if "viva" in company or "fit" in company:
                if "fitlife" in lu and "github.com" in lu:
                    e_links.setdefault("github", u)
                elif "fitlife" in lu:
                    e_links.setdefault("live", u)


def _block_between_any(raw: str, starts, end=None) -> list:
    """Try several section-header aliases; return the first non-empty block. Lets the
    parser handle résumés that title sections differently ('Experience' vs 'Work
    Experience', 'Summary' vs 'Professional Summary') without changing the result for
    résumés that already use the primary header."""
    for s in starts:
        block = _block_between(raw, s, end)
        if block:
            return block
    return []


def _extract_summary(raw: str) -> str:
    lines = _block_between(raw, "professional summary", "technical skills")
    if not lines:
        lines = _block_between_any(raw, ("summary", "professional summary", "objective",
                                         "profile", "about"))
    return " ".join(lines).strip()


def _parse_experience(raw: str) -> list:
    # Only the strict template header here; other headers ('Experience', 'Employment')
    # are handled by the _guess_sections fallback in build_profile_from_text, which
    # parses single-line "Role, Company  Date" formats better than this rigid block.
    lines = _block_between(raw, "work experience", "projects")
    if not lines:
        return []
    role = lines[0] if lines else ""
    i = 1
    if i < len(lines) and _is_link_marker(lines[i]):
        i += 1
    date = lines[i] if i < len(lines) and _is_date_range(lines[i]) else ""
    if date:
        i += 1
    company = lines[i] if i < len(lines) else ""
    i += 1
    location = lines[i] if i < len(lines) and not _is_bullet(lines[i]) else ""
    if location:
        i += 1
    bullets, _ = _collect_bullets(lines, i)
    return [{
        "company": company,
        "role": role,
        "date": date,
        "location": location,
        "bullets": bullets,
    }] if role or company or bullets else []


def _parse_projects(raw: str) -> list:
    # Strict template header only; varied headers fall back to _guess_sections (which
    # handles freeform project formats better than this rigid block parser).
    lines = _block_between(raw, "projects", "education")
    projects, i = [], 0
    while i < len(lines):
        title = lines[i]
        if _is_bullet(title) or _is_link_marker(title) or _is_date_range(title):
            i += 1
            continue
        i += 1
        has_links = False
        if i < len(lines) and _is_link_marker(lines[i]):
            has_links = True
            i += 1
        date = lines[i] if i < len(lines) and _is_date_range(lines[i]) else ""
        if date:
            i += 1
        tech = lines[i] if i < len(lines) and not _is_bullet(lines[i]) else ""
        if tech:
            i += 1
        bullets = []
        cur = ""
        while i < len(lines):
            s = lines[i]
            next_is_project = (
                cur and not _is_bullet(s) and not _is_date_range(s) and not _is_link_marker(s)
                and i + 1 < len(lines) and (_is_date_range(lines[i + 1]) or _is_link_marker(lines[i + 1]))
            )
            if next_is_project:
                break
            if _is_bullet(s):
                if cur:
                    bullets.append(cur)
                cur = _BULLET_RE.sub("", s).strip()
            elif cur:
                cur += " " + s
            else:
                break
            i += 1
        if cur:
            bullets.append(cur)
        name = title.split("|", 1)[0].strip()
        subtitle = title.split("|", 1)[1].strip() if "|" in title else ""
        projects.append({
            "name": name,
            "subtitle": subtitle,
            "date": date,
            "tech": tech,
            "links": {},
            "has_link_markers": has_links,
            "bullets": bullets,
        })
    return [p for p in projects if p.get("name") and p.get("bullets")]


def _parse_education(raw: str) -> list:
    lines = _block_between(raw, "education", "additional")
    if not lines:
        lines = _block_between_any(raw, ("education", "academic background", "academics",
                                         "educational qualifications"))
    out, i = [], 0
    while i + 3 < len(lines):
        out.append({
            "school": lines[i],
            "location": lines[i + 1],
            "degree": lines[i + 2].split("|", 1)[0].strip(),
            "detail": lines[i + 2].split("|", 1)[1].strip() if "|" in lines[i + 2] else "",
            "dates": lines[i + 3],
        })
        i += 4
    return out


def _parse_additional(raw: str) -> list:
    lines = _block_between(raw, "additional")
    bullets, _ = _collect_bullets(lines, 0)
    return bullets


def _guess_summary(raw: str) -> str:
    for line in raw.splitlines():
        s = line.strip()
        if len(s) > 60 and not s.lower().startswith(("skills", "experience", "education")):
            return s
    return ""


SECTION_HEADERS = (
    "experience", "work experience", "employment", "professional experience",
    "projects", "technical projects", "academic projects", "personal projects",
    "education", "skills", "technical skills", "certifications", "certification",
    "awards", "achievements", "honors", "summary", "objective", "profile",
    "publications", "leadership", "activities", "interests", "references", "coursework",
)
_BULLET_RE = re.compile(r"^[\-•‣◦⁃∙*▪·●▸>]+\s*")
_HEADER_HINT = re.compile(
    r"\b(20\d{2}|present|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|"
    r"engineer|developer|intern|analyst|research|manager|assistant|lead|scientist)\b", re.I)


def _section_block(raw: str, names) -> str:
    """Return the text between a section header (e.g. 'Projects') and the next
    known section header."""
    lines = raw.splitlines()
    start = None
    for i, l in enumerate(lines):
        s = l.strip().lower().rstrip(":")
        if s and (s in names or (any(s.startswith(n) for n in names) and len(l.strip()) < 40)):
            start = i + 1
            break
    if start is None:
        return ""
    end = len(lines)
    for j in range(start, len(lines)):
        s = lines[j].strip().lower().rstrip(":")
        if s and s in SECTION_HEADERS and len(lines[j].strip()) < 40:
            end = j
            break
    return "\n".join(lines[start:end])


def _grouped_sections(block: str) -> list:
    """Group a section block into [{name, bullets[]}], merging wrapped lines and
    detecting role/project headers. Falls back to one 'Highlights' group."""
    groups, cur, pending = [], None, ""

    def push_bullet(text):
        nonlocal cur
        t = re.sub(r"\s+", " ", text.strip(" -•*▪·\t")).strip()
        if len(t) >= 20:
            if cur is None:
                cur = {"name": "Highlights", "bullets": []}
                groups.append(cur)
            cur["bullets"].append(t)

    for line in block.splitlines():
        s = line.strip()
        if not s:
            if pending:
                push_bullet(pending); pending = ""
            continue
        is_bullet = bool(_BULLET_RE.match(s))
        looks_header = (not is_bullet and len(s) < 70 and _HEADER_HINT.search(s)
                        and not s.endswith((".", ",")))
        if is_bullet:
            if pending:
                push_bullet(pending)
            pending = _BULLET_RE.sub("", s)
        elif looks_header:
            if pending:
                push_bullet(pending); pending = ""
            cur = {"name": re.sub(r"\s+", " ", s)[:80], "bullets": []}
            groups.append(cur)
        elif pending:
            pending += " " + s            # wrapped continuation of the current bullet
        else:
            pending = s
    if pending:
        push_bullet(pending)
    return [g for g in groups if g.get("bullets")][:6]


def _guess_sections(raw: str, headers) -> list:
    """Extract a résumé section into [{name, bullets[]}]."""
    block = _section_block(raw, headers)
    if not block.strip():
        return []
    return _grouped_sections(block)


def save_profile(profile: dict, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    slim = {k: v for k, v in profile.items() if k != "raw_text"}
    slim["raw_text_len"] = len(profile.get("raw_text", ""))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(slim, f, indent=2)


def load_profile(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---- Reference profile from CLAUDE.md (NOT auto-loaded; the app starts empty) ----
def surya_default_profile() -> dict:
    """A hard-coded reference profile of Suryateja's background. The app no longer
    seeds this at startup — it starts empty until a résumé is uploaded. Kept as a
    stable fixture for the test suite and as a known-good profile shape."""
    return {
        "name": "Suryateja Orumpati",
        "email": "orumpatisuryateja@gmail.com",
        "phone": "(706) 380-4325",
        "links": {"linkedin": "linkedin.com/in/suryatejaorumpati",
                  "github": "github.com/orumpaitsuryateja99",
                  "portfolio": "https://orumpaitsuryateja99.github.io/Suryateja-Portfolio"},
        "summary": ("Backend-leaning Software Engineer (MS CS, UGA) — built a live full-stack "
                    "NLP application integrating 4 REST APIs; strong DSA (214+ LeetCode)."),
        "skill_categories": [
            {"category": "Languages", "items": ["Python", "Java", "JavaScript", "C++", "C"]},
            {"category": "Web / Backend",
             "items": ["Flask", "REST APIs", "RESTful Web Services", "Node.js (basics)",
                       "HTML5", "CSS3", "JSON", "Chart.js"]},
            {"category": "Databases & Cloud",
             "items": ["SQL", "PostgreSQL (learning)", "Google Cloud APIs", "AWS (fundamentals)"]},
            {"category": "ML / AI",
             "items": ["TensorFlow", "Keras", "CNN (ResNet50, VGG19, DenseNet169, Inception-v3)",
                       "SAM", "Google Gemini API"]},
            {"category": "Developer Tools", "items": ["Git", "GitHub", "VS Code", "Postman", "Agile / Scrum"]},
            {"category": "Core CS",
             "items": ["Data Structures & Algorithms", "Object-Oriented Programming",
                       "Client-Server Architecture", "API Design"]},
        ],
        "skill_phrases": [
            "Python", "Java", "JavaScript", "C++", "C", "Flask", "REST APIs",
            "RESTful Web Services", "Node.js (basics)", "HTML5", "CSS3", "JSON",
            "Chart.js", "SQL", "PostgreSQL (learning)", "Google Cloud APIs",
            "AWS (fundamentals)", "TensorFlow", "Keras",
            "CNN (ResNet50, VGG19, DenseNet169, Inception-v3)", "SAM",
            "Google Gemini API", "Git", "GitHub", "VS Code", "Postman",
            "Agile / Scrum", "Data Structures & Algorithms",
            "Object-Oriented Programming", "Client-Server Architecture", "API Design",
        ],
        "skills": ["python", "java", "javascript", "c++", "c", "flask", "rest apis",
                   "node.js", "sql", "postgresql", "html5", "css3", "json", "chart.js",
                   "tensorflow", "keras", "computer vision", "llm", "nlp",
                   "google gemini api", "sam", "oauth", "google calendar api",
                   "aws", "gcp", "data structures", "oop", "api design",
                   "client-server", "agile"],
        "tools": ["git", "github", "postman", "vs code"],
        "domains": ["ai/ml", "full-stack", "climate"],
        "projects": [
            {"name": "ClimateAI",
             "date": "Aug 2025 - Present",
             "tech": "Python, Flask, REST APIs, Google Gemini API, HTML/CSS/JS, Chart.js",
             "links": {"live": "https://climate-data-chatbot.onrender.com",
                       "github": "https://github.com/orumpaitsuryateja99/climate-data-chatbot"},
             "bullets": [
                 "Built a full-stack NLP climate chatbot with a Python/Flask backend serving 4 integrated REST APIs (NASA, NOAA, OpenWeather, GeoNames).",
                 "Integrated the Google Gemini API for natural-language responses and a Chart.js dashboard with CSV/PDF export.",
                 "Designed REST API endpoints and JSON contracts following client-server architecture.",
             ]},
            {"name": "Weed Classification CNN",
             "date": "Aug 2023 - Jan 2024",
             "tech": "Python, TensorFlow, Keras, ResNet50, Inception-v3, DenseNet169, VGG19, SAM",
             "links": {},
             "bullets": [
                 "Achieved 97% accuracy with ResNet50 on 17,509 images, beating the published DeepWeeds benchmark (95.7%) by 1.3%.",
                 "Integrated Meta's Segment Anything Model (SAM) for image segmentation.",
                 "Benchmarked ResNet50, VGG19, DenseNet169, and Inception-v3 in TensorFlow/Keras.",
             ]},
        ],
        "experience": [
            {"company": "VIVA FIT", "role": "Software Engineering Intern",
             "date": "Jul 2023 - Sep 2023",
             "location": "Remote, India",
             "links": {"live": "https://orumpaitsuryateja99.github.io/fitlife-studio",
                       "github": "https://github.com/orumpaitsuryateja99/fitlife-studio"},
             "bullets": [
                 "Built a responsive scheduling web app (HTML/CSS/JS) and shipped the production UI on deadline.",
                 "Integrated the Google Calendar API with OAuth for two-way event sync.",
             ]},
        ],
        "target_roles": ["software engineer", "backend engineer", "full stack engineer",
                         "software development engineer","New Grad Software Engineer","entry level software engineer","software engineer intern"],
        "education": [
            {"school": "University of Georgia", "location": "Athens, GA",
             "degree": "Master of Science, Computer Science", "dates": "Aug 2024 - May 2026",
             "detail": "GPA: 3.85 / 4.0"},
            {"school": "National Institute of Technology Karnataka (NITK)", "location": "India",
             "degree": "Bachelor of Technology, Information Technology", "dates": "Jul 2020 - Jun 2024",
             "detail": "GPA: 3.21 / 4.0"},
        ],
        "additional": [
            "Solved 214+ LeetCode problems with a 200-day streak badge and 300+ GeeksForGeeks problems.",
            "Ranked 3,198 out of 1.5M applicants in JEE Mains 2020.",
        ],
        "experience_years": 0,
        "raw_text": "",
        "latex_template": "",
    }
