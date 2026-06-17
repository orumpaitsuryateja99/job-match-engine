"""
roles.py — role-focus / specialization model.

Lets the user say "frontend" (or backend, full-stack, ML/AI, …) and have that
flow through every layer:
  * board pull + AI-paste import — gate job TITLES to the chosen focus
  * the AI search prompt          — tell the model which kind of role to find
  * ATS title alignment           — score titles against focus-appropriate roles

Design goal: be permissive enough to not starve the results (the #1 complaint
was "not finding relevant jobs"), but precise enough that choosing "frontend"
drops obvious backend/ML/data roles.
"""
import re

# Generic SWE titles that belong to ANY software-engineering focus.
GENERIC_SWE = (
    "software engineer", "software developer", "software development engineer",
    "software development", "sde", "swe", "programmer", "software engineering",
    "applications engineer", "application developer",
)

# Each focus: title hints (substring match), target_roles (for prompt + ATS
# title alignment), and core_skills (emphasised in the prompt).
ROLE_FOCUS = {
    "newgrad_swe": {
        "label": "New Grad Software Engineer",
        "hints": ("new grad software engineer", "software engineer new grad",
                  "new graduate software engineer", "new grad swe", "swe new grad"),
        "target_roles": ["new grad software engineer"],
        "core_skills": ["python", "java", "javascript", "sql", "rest apis",
                        "data structures", "algorithms", "oop", "git"],
    },
    "entry_swe": {
        "label": "Entry Level Software Engineer",
        "hints": ("entry level software engineer", "entry-level software engineer",
                  "entry level swe", "entry-level swe"),
        "target_roles": ["entry level software engineer"],
        "core_skills": ["python", "java", "javascript", "sql", "rest apis",
                        "data structures", "algorithms", "oop", "git"],
    },
    "junior_dev": {
        "label": "Junior Software Developer",
        "hints": ("junior software developer", "junior software engineer",
                  "junior developer"),
        "target_roles": ["junior software developer"],
        "core_skills": ["python", "java", "javascript", "sql", "rest apis",
                        "html5", "css3", "git"],
    },
    "associate_swe": {
        "label": "Associate Software Engineer",
        "hints": ("associate software engineer", "associate software developer",
                  "associate swe"),
        "target_roles": ["associate software engineer"],
        "core_skills": ["python", "java", "javascript", "sql", "rest apis",
                        "data structures", "algorithms", "oop", "git"],
    },
    "swe_i": {
        "label": "Software Engineer I",
        "hints": ("software engineer i", "software engineer 1", "sde i", "sde 1",
                  "software developer i", "software developer 1"),
        "target_roles": ["software engineer i"],
        "core_skills": ["python", "java", "javascript", "sql", "rest apis",
                        "data structures", "algorithms", "oop", "git"],
    },
    "new_college_grad_swe": {
        "label": "New College Grad SWE",
        "hints": ("new college grad software engineer", "new college graduate software engineer",
                  "new college grad swe", "new college graduate swe", "ncg software engineer"),
        "target_roles": ["new college grad software engineer"],
        "core_skills": ["python", "java", "javascript", "sql", "rest apis",
                        "data structures", "algorithms", "oop", "git"],
    },
    "newgrad": {
        "label": "New Grad",
        "hints": ("new grad", "new graduate", "university graduate", "university grad",
                  "entry level", "entry-level", "early career", "campus", "graduate",
                  "associate software engineer", "software engineer i", "software engineer 1",
                  "software developer i", "software developer 1", "sde i", "sde 1",
                  "junior software engineer", "junior software developer",
                  "associate software developer"),
        "target_roles": ["software engineer new grad", "new graduate software engineer",
                         "entry level software engineer", "associate software engineer",
                         "junior software developer", "junior software engineer",
                         "entry level software developer", "software engineer i",
                         "software development engineer i"],
        "core_skills": ["python", "java", "javascript", "sql", "rest apis",
                        "data structures", "algorithms", "oop", "git"],
    },
    "backend": {
        "label": "Backend",
        "hints": ("backend", "back end", "back-end", "server", "server-side",
                  "api engineer", "services engineer", "distributed systems"),
        "target_roles": ["backend engineer", "backend developer", "software engineer",
                         "server engineer", "api engineer"],
        "core_skills": ["python", "java", "sql", "rest apis", "spring", "node.js",
                        "microservices", "postgresql", "aws", "docker"],
    },
    "frontend": {
        "label": "Frontend",
        "hints": ("frontend", "front end", "front-end", "front-end engineer",
                  "ui engineer", "ui/ux engineer", "web developer", "react developer",
                  "javascript engineer"),
        "target_roles": ["frontend engineer", "front end engineer", "ui engineer",
                         "web developer", "software engineer"],
        "core_skills": ["javascript", "typescript", "react", "html5", "css3",
                        "vue", "angular", "rest apis"],
    },
    "fullstack": {
        "label": "Full-stack",
        "hints": ("full stack", "full-stack", "fullstack", "web application engineer"),
        "target_roles": ["full stack engineer", "full-stack engineer", "software engineer",
                         "web developer"],
        "core_skills": ["javascript", "typescript", "react", "node.js", "python",
                        "java", "sql", "rest apis", "html5", "css3"],
    },
    "mlai": {
        "label": "ML / AI",
        "hints": ("machine learning", "ml engineer", "ai engineer", "applied scientist",
                  "deep learning", "data scientist", "mlops", "ai/ml", "research engineer"),
        "target_roles": ["machine learning engineer", "ai engineer", "ml engineer",
                         "software engineer, machine learning", "applied scientist"],
        "core_skills": ["python", "tensorflow", "pytorch", "keras", "nlp",
                        "computer vision", "scikit-learn", "pandas", "numpy"],
    },
    "data": {
        "label": "Data",
        "hints": ("data engineer", "data engineering", "etl", "analytics engineer",
                  "data platform", "data infrastructure"),
        "target_roles": ["data engineer", "analytics engineer", "software engineer, data"],
        "core_skills": ["python", "sql", "postgresql", "kafka", "aws", "spark"],
    },
    "mobile": {
        "label": "Mobile",
        "hints": ("mobile engineer", "mobile developer", "ios", "android",
                  "react native", "flutter"),
        "target_roles": ["mobile engineer", "ios engineer", "android engineer",
                         "software engineer, mobile"],
        "core_skills": ["swift", "kotlin", "java", "react", "javascript"],
    },
    "devops": {
        "label": "DevOps / Platform",
        "hints": ("devops", "site reliability", "sre", "platform engineer",
                  "infrastructure engineer", "cloud engineer"),
        "target_roles": ["devops engineer", "site reliability engineer", "platform engineer",
                         "cloud engineer", "software engineer, infrastructure"],
        "core_skills": ["aws", "gcp", "azure", "docker", "kubernetes", "terraform",
                        "ci/cd", "linux", "python"],
    },
    # "general" mirrors the original broad SWE gate so default behaviour is unchanged.
    "general": {
        "label": "General SWE",
        "hints": ("software engineer", "software developer", "sde", "backend",
                  "full stack", "full-stack", "software development", "frontend",
                  "front end", "front-end"),
        "target_roles": ["software engineer", "software developer"],
        "core_skills": [],
    },
}

# Non-software role FAMILIES that must never pass as a SWE match (the exact
# leakage the user hit: "Semiconductor Quality Assurance Engineer"). These reject
# UNLESS the title is clearly a software-dev title (e.g. "Software Engineer in
# Test" keeps, a bare "Test Engineer" / "QA Engineer" / "Sales Engineer" drops).
_HARD_REJECT = (
    "quality assurance", "qa engineer", "qa analyst", "quality engineer",
    "test engineer", "validation engineer", "semiconductor", "data entry",
    "help desk", "helpdesk", "service desk", "desktop support", "it support",
    "technical support", "tech support", "business analyst", "business systems analyst",
    "sales engineer", "solutions engineer", "pre-sales", "presales",
    "account executive", "account manager", "recruiter", "talent acquisition",
    "marketing", "accountant", "bookkeeper", "financial analyst",
    "mechanical engineer", "electrical engineer", "civil engineer",
    "industrial engineer", "manufacturing engineer", "process engineer",
    "chemical engineer", "biomedical engineer", "field engineer", "field service",
    "service technician", "maintenance technician", "customer success",
    "customer support", "customer service", "scrum master", "project manager",
    "program manager", "product manager", "office", "administrative", "warehouse",
    "driver", "nurse", "teacher", "consultant",
)

# All specialization hints, used to detect "this title clearly belongs to a
# focus the user did NOT pick" so we can drop it.
_SPECIALIZED = ("backend", "frontend", "fullstack", "mlai", "data", "mobile", "devops")

# SWE-specific new-grad phrases — these unambiguously mean an entry-level SOFTWARE
# role (so they qualify the 'newgrad' focus on their own). The broad level words
# ("entry level", "graduate", "campus") deliberately do NOT, because "Entry Level
# Auto Body Painter" is not a software job.
_SWE_NEWGRAD_HINTS = (
    "associate software engineer", "software engineer i", "software engineer 1",
    "software developer i", "software developer 1", "sde i", "sde 1",
    "new grad software", "graduate software engineer", "software engineering",
    "junior software engineer", "junior software developer",
    "associate software developer", "entry level software developer",
)

# Non-web-software specializations (hardware / robotics / kernel / wireless-RF /
# networking). A generic "Software Engineer" title carrying one of these is NOT a
# backend/frontend/full-stack new-grad role, so we drop it when the user hasn't
# picked an ML/data/devops/general focus (e.g. "Wireless Software Engineer",
# "RF Software Engineer", "Network Engineer", "Embedded Firmware Engineer").
_HARDWARE_HINTS = (
    # hardware / robotics / low-level
    "robotics", "embedded", "firmware", "fpga", "asic", "verilog", "rtl design",
    "hardware engineer", "device driver", "gpu kernel", "cuda kernel",
    "kernel engineer", "silicon", "control systems",
    # wireless / RF / networking / signal
    "wireless", "rf engineer", "rf software", "radio frequency", "wlan", "baseband",
    "modem", "cellular", "antenna", "ofdma", "telecom", "network", "signal processing",
)

# A genuine NEW-GRAD / early-career signal, in the title or the JD. Used when the
# user picks the "New Grad" focus so a plain "Software Engineer" (any level) is NOT
# treated as a new-grad posting. New-grad reqs are seasonal (peak Aug–Nov), so this
# is intentionally strict — the UI explains the scarcity rather than padding.
_NEWGRAD_TITLE_RE = re.compile(
    r"\b(new[\s-]?grad(uate)?|recent\s+grad(uate)?|university\s+grad(uate)?|"
    r"college\s+grad(uate)?|new\s+college\s+grad|ncg|early[\s-]?career|entry[\s-]?level|"
    r"campus|rotational|apprentice|associate\s+(software\s+)?(engineer|developer)|"
    r"junior|grad(uate)?\s+(software\s+)?(engineer|developer))\b"
    r"|\b(software\s+engineer|software\s+developer|sde|sw\s+engineer)\s*(i|1)\b"
    r"|\bgrad(uate)?\s+program\b", re.I)
_NEWGRAD_JD_RE = re.compile(
    r"\b(new[\s-]?grad(uate)?|recent(ly)?\s+grad(uate|uated)?|recent\s+college\s+grad|"
    r"new\s+college\s+grad|early[\s-]?career|entry[\s-]?level|university\s+grad(uate)?|"
    r"currently\s+pursuing|graduating\s+(in|by|between)|final[\s-]?year\s+student|"
    r"about\s+to\s+graduate|students?\s+and\s+(new\s+)?grad|for\s+new\s+grad)\b", re.I)


def is_new_grad_role(title: str, content: str = "") -> bool:
    """True if a role genuinely signals new-grad / early-career, by TITLE
    ('Software Engineer I', 'Associate SWE', 'New Grad …', 'University Graduate')
    or by explicit JD language. A plain 'Software Engineer' with a normal JD is
    NOT a new-grad posting."""
    return bool(_NEWGRAD_TITLE_RE.search(title or "")
                or _NEWGRAD_JD_RE.search(content or ""))


def _norm_keys(focus_keys):
    """Normalize a list of focus keys; empty / unknown -> ['general']."""
    if not focus_keys:
        return ["general"]
    keys = [k for k in focus_keys if k in ROLE_FOCUS]
    return keys or ["general"]


def labels_for(focus_keys) -> list:
    return [ROLE_FOCUS[k]["label"] for k in _norm_keys(focus_keys)]


def target_roles_for(focus_keys) -> list:
    """Merged, de-duplicated target roles for ATS title alignment + the prompt."""
    out = []
    for k in _norm_keys(focus_keys):
        for r in ROLE_FOCUS[k]["target_roles"]:
            if r not in out:
                out.append(r)
    if "software engineer" not in out:
        out.append("software engineer")
    return out


def core_skills_for(focus_keys) -> list:
    out = []
    for k in _norm_keys(focus_keys):
        for s in ROLE_FOCUS[k]["core_skills"]:
            if s not in out:
                out.append(s)
    return out


def _is_generic_swe(title_lower: str) -> bool:
    return any(g in title_lower for g in GENERIC_SWE)


def title_matches_focus(title: str, focus_keys) -> bool:
    """True if a job TITLE fits the chosen focus.

    - 'general': broad SWE gate (original behaviour).
    - specific focuses: keep titles matching a SELECTED specialization's hints, or
      a genuine generic-SWE title, but DROP titles that clearly belong to an
      un-selected specialization (so 'frontend' drops 'Data Engineer') or that are
      hardware/robotics work when the user is hunting web/new-grad roles.
    - 'newgrad' requires a real software title — bare "entry level <trade>" titles
      (e.g. "Entry Level Auto Body Painter") are rejected.
    """
    tl = (title or "").lower()
    keys = _norm_keys(focus_keys)

    # 0) Hard-reject non-software role families up front — applies to EVERY focus
    #    (incl. 'general'), unless the title is clearly a software-dev title.
    if any(r in tl for r in _HARD_REJECT) and not _is_generic_swe(tl):
        return False

    if "general" in keys:
        return any(h in tl for h in ROLE_FOCUS["general"]["hints"]) or _is_generic_swe(tl)

    # 1) Matches a SELECTED specialization's hints (backend/frontend/… that the
    #    user actually picked) → keep. (newgrad's broad level words excluded here.)
    selected_spec = [k for k in keys if k in _SPECIALIZED]
    spec_hints = tuple(h for k in selected_spec for h in ROLE_FOCUS[k]["hints"])
    if any(h in tl for h in spec_hints):
        return True

    # 2) Generic SWE title → keep, unless it clearly belongs to an un-selected
    #    specialization, or is hardware/robotics/kernel work for a web-focused hunt.
    if _is_generic_swe(tl):
        unselected = [k for k in _SPECIALIZED if k not in keys]
        other_hints = tuple(h for k in unselected for h in ROLE_FOCUS[k]["hints"])
        if any(h in tl for h in other_hints):
            return False
        web_focus_only = not ({"mlai", "data", "devops", "general"} & set(keys))
        if web_focus_only and any(h in tl for h in _HARDWARE_HINTS):
            return False
        return True

    # 3) New-grad focus: only a SWE-specific new-grad phrase qualifies a non-generic
    #    title (so non-software "entry level" postings are dropped).
    if "newgrad" in keys and any(h in tl for h in _SWE_NEWGRAD_HINTS):
        return True

    return False
