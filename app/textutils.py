"""
textutils.py — lightweight text helpers (no heavy NLP deps).

Provides: alias-aware skill/tool detection, keyword extraction (TF over a
stopword-filtered vocabulary), and number extraction (used by the honesty
validator). Pure-Python so `pip install` stays fast for a busy student.
"""
import re
from collections import Counter

from skills_db import SKILL_ALIASES, TOOL_ALIASES, DOMAINS

STOPWORDS = set("""
a an the and or but if then else for to of in on at by with without from as is are was were be been being
this that these those it its we you they he she them our your their will would shall should can could may might must
do does did done have has had not no yes will our ll re ve s t job role work experience years year team teams
ability strong excellent good great new grad graduate entry level senior junior including include included etc
preferred required requirement requirements responsibilities responsible qualifications qualification plus
candidate candidates company companies position positions opportunity opportunities looking seeking join
build building develop developing design designing using use used across within using able help support
who what when where which while about into more most other some such only own same so than too very
""".split())

WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+#.\-]*")
NUM_RE = re.compile(r"\b\d+(?:[.,]\d+)?%?\b")


def normalize(text: str) -> str:
    return (text or "").lower()


def _compile_alias_regex(aliases) -> "re.Pattern":
    """One whole-token alternation, longest alias first. A single regex pass
    over the text replaces N per-alias searches — ~20x faster on a long JD, and
    because the longest match wins it also stops 'node.js' from matching the bare
    'js' alias (a false 'JavaScript' tag the old per-alias scan produced)."""
    ordered = sorted(aliases, key=len, reverse=True)
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(a) for a in ordered)
                      + r")(?![a-z0-9])")


_SKILL_RE = _compile_alias_regex(SKILL_ALIASES)
_TOOL_RE = _compile_alias_regex(TOOL_ALIASES)
# flatten domain keyword -> domain, then one regex over all keywords
_DOMAIN_KW = {kw: domain for domain, kws in DOMAINS.items() for kw in kws}
_DOMAIN_RE = _compile_alias_regex(_DOMAIN_KW.keys())


def _match_aliases(text_lower: str, alias_map: dict, combined_re) -> set:
    """Canonical terms whose aliases appear as whole tokens (one regex pass)."""
    return {alias_map[m.group(0)] for m in combined_re.finditer(text_lower)}


# High-precision rule for the bare "C" language. A standalone "C" is far too noisy
# (grades, "Section C", "C-suite", "C Corp"), so we only credit it in unambiguous
# C-language contexts: next to C++ ("C/C++", "C, C++", "C and C++"), or explicit
# phrasing ("C programming", "embedded C", "ANSI C", "C language"). This closes the
# common "Languages: C, C++, Python" recall gap without false positives.
_C_CONTEXT_RE = re.compile(
    r"\bc\s*[/,]\s*c\+\+|\bc\+\+\s*[/,]\s*c\b|\bc\s+and\s+c\+\+|\bc\+\+\s+and\s+c\b"
    r"|\bembedded\s+c\b|\bansi\s+c\b|\bc\s+programming\b|\bprogramming\s+in\s+c\b"
    r"|\bc\s+language\b", re.I)


def detect_skills(text: str) -> set:
    found = _match_aliases(normalize(text), SKILL_ALIASES, _SKILL_RE)
    if "c" not in found and _C_CONTEXT_RE.search(text or ""):
        found.add("c")
    return found


def detect_tools(text: str) -> set:
    return _match_aliases(normalize(text), TOOL_ALIASES, _TOOL_RE)


def detect_domains(text: str) -> set:
    return {_DOMAIN_KW[m.group(0)] for m in _DOMAIN_RE.finditer(normalize(text))}


def content_tokens(text: str) -> set:
    """All distinct non-stopword content tokens (len>2). Used to build the
    candidate's keyword pool from their full resume text."""
    return {t.lower() for t in WORD_RE.findall(text or "")
            if t.lower() not in STOPWORDS and len(t) > 2}


def top_keywords(text: str, n: int = 25) -> list:
    """Top-n high-signal keywords by frequency, stopwords removed."""
    tokens = [t.lower() for t in WORD_RE.findall(text or "")]
    tokens = [t for t in tokens if t not in STOPWORDS and len(t) > 2]
    counts = Counter(tokens)
    return [w for w, _ in counts.most_common(n)]


def extract_numbers(text: str) -> set:
    """Numbers/percentages mentioned (e.g., '97%', '4', '17509'). Used by the
    honesty validator to ensure tailoring never invents a metric."""
    return set(NUM_RE.findall(text or ""))


# Words near a "<n> years" mention that mean it's company age / tenure / marketing
# copy, NOT an experience requirement (so "10 years of history" doesn't read as 10).
_NON_REQUIREMENT_YEARS = re.compile(
    r"\b(history|ago|growth|business|operation|operating|innovation|anniversary|"
    r"old|founded|building|built|serving|served|trusted|since|established|industry "
    r"leader|in the (?:market|industry)|track record)\b")


def extract_years_required(text: str) -> int:
    """Best-effort: minimum years of EXPERIENCE the JD asks for. 0 if none.
    Normalizes en/em dashes so '0–2 years' is read as a range starting at 0.
    Skips '<n> years' mentions that are clearly company age / tenure / marketing
    ('10 years of history', 'founded 8 years ago') rather than a requirement."""
    tl = normalize(text).replace("–", "-").replace("—", "-")
    out = []
    # capture a little context before and after each "<n> years" so we can reject
    # non-requirement phrasing ("for over 20 years", "10 years of history").
    for m in re.finditer(r"([^.\n]{0,24}?)(\d+)\s*\+?\s*(?:-\s*\d+\s*)?years?\b([^.\n]{0,22})", tl):
        pre, n, tail = m.group(1), int(m.group(2)), m.group(3)
        if _NON_REQUIREMENT_YEARS.search(pre + " " + tail):
            continue
        out.append(n)
    return min(out) if out else 0


# Mid/senior signals in a job TITLE. Handles abbreviations ("Sr."), Roman-numeral
# levels ("II"+), explicit levels ("L3", "Level 4", "IC2"), and a numeric level
# attached to a role word ("Software Engineer 3", "SDE 2"). Deliberately does NOT
# flag "I"/"1" (entry-level), and — critically — does NOT flag a bare standalone
# digit, which used to drop legitimate titles like "Software Engineer - 3 Month
# Contract" or "Hiring 5 Software Engineers". A numeric level is only counted when
# it directly follows a role word and is NOT a duration/count ("3 Month", "5 roles").
_SENIOR_TITLE_RE = re.compile(
    r"\b(?:senior|sr|staff|principal|lead|leads|director|mgr|manager|managing|"
    r"architect|head|vp|svp|evp|distinguished|fellow|experienced|expert|"
    r"ii|iii|iv|vi|vii|viii)\b"
    r"|\b(?:l|lvl|level|grade|ic|t)\s*-?\s*[2-9]\b"
    r"|\b(?:engineer|developer|sde|swe|programmer|analyst|scientist)\s+[2-9]\b"
    r"(?!\s*(?:month|months|week|weeks|year|years|yr|yrs|mo|day|days|hour|hours|"
    r"opening|openings|position|positions|role|roles|spot|spots|seat|seats))"
)


def is_senior_title(title: str) -> bool:
    """True if a job TITLE signals mid/senior level (Sr., Staff, Lead, II, L3, …).
    'Software Engineer', 'Software Engineer I', and 'New Grad …' return False."""
    return bool(_SENIOR_TITLE_RE.search((title or "").lower()))


# High-precision phrases that mean an international student should NOT apply.
# (reason, compiled pattern) — order matters; first match wins. Tuned to fire on
# clear negatives ("does not sponsor") without tripping on positives ("we sponsor").
_SPONSOR_BLOCK = [
    ("does not offer sponsorship",
     re.compile(r"\b(?:not able to|unable to|cannot|can ?not|will not|won'?t|"
                r"do(?:es)? not|are not able to|not (?:currently )?(?:able|in a position) to)\b"
                r"[^.\n]{0,18}\bsponsor")),
    ("no sponsorship",
     re.compile(r"\b(?:no|without)\b[^.\n]{0,18}\b(?:visa )?sponsorship\b")),
    ("sponsorship not available",
     re.compile(r"\bsponsorship\b[^.\n]{0,20}\b(?:not|isn'?t|won'?t be)\b[^.\n]{0,15}"
                r"\b(?:available|offered|provided|possible)\b")),
    ("not eligible for sponsorship",
     re.compile(r"\bnot\b[^.\n]{0,15}\beligible\b[^.\n]{0,18}\bsponsorship\b")),
    ("must not require sponsorship",
     re.compile(r"\bmust not require\b[^.\n]{0,15}\bsponsorship\b")),
    ("US citizens only", re.compile(r"\bu\.?\s?s\.?\s*citizens?\s*only\b")),
    ("must be a US citizen",
     re.compile(r"\bmust be (?:a |an )?(?:u\.?\s?s\.?|united states) citizens?\b")),
    ("US citizenship required",
     re.compile(r"\b(?:u\.?\s?s\.?|united states)\s+citizenship\b|"
                r"\b(?:require[sd]?|need(?:s|ed)?|must (?:hold|have|possess|be))\b"
                r"[^.\n]{0,18}\bcitizenship\b")),
    ("security clearance required",
     re.compile(r"\b(?:security|secret|top[- ]secret|ts/sci)\s+clearance\b|"
                r"\bclearance\b[^.\n]{0,18}\b(?:required|needed|mandatory|active|eligible)\b|"
                r"\brequires?\b[^.\n]{0,25}\bclearance\b|\bts/sci\b")),
]


def detect_sponsorship_block(text: str) -> str:
    """Return a short reason if the JD clearly excludes visa sponsorship / requires
    citizenship or clearance, else ''. Used to hard-flag roles to SKIP — a high
    ATS score is wasted on a company that won't sponsor."""
    tl = normalize(text)
    if not tl or "sponsor" not in tl and "citizen" not in tl and "clearance" not in tl:
        return ""
    for reason, pat in _SPONSOR_BLOCK:
        if pat.search(tl):
            return reason
    return ""


# ---- US-location detection (H1B-critical: keep the search in the United States) ----
_US_STATE_CODES = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id", "il",
    "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms", "mo", "mt",
    "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri",
    "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy", "dc",
}
_US_WORDS = ("united states", "u.s.a", "usa", "u.s.", "remote, us", "remote (us",
             "remote - us", "remote-us", "us remote", "remote us", "us-remote",
             "anywhere in the us", "us based", "u.s.-based")
_US_CITIES = ("san francisco", "new york", "seattle", "austin", "boston", "atlanta",
              "chicago", "los angeles", "san jose", "mountain view", "palo alto",
              "sunnyvale", "menlo park", "bellevue", "redmond", "denver", "dallas",
              "houston", "san diego", "san mateo", "washington", "arlington",
              "nyc", "bay area", "silicon valley", "brooklyn", "pittsburgh", "raleigh",
              "durham", "charlotte", "phoenix", "salt lake city", "minneapolis",
              "detroit", "columbus", "nashville", "miami", "tampa", "kansas city",
              "santa clara", "santa monica", "culver city", "irvine", "plano",
              "reston", "mclean", "boulder", "ann arbor")
_FOREIGN_LOC = (
    "germany", "berlin", "munich", "münchen", "united kingdom", " uk", "u.k.", "london",
    "manchester", "edinburgh", "france", "paris", "netherlands", "amsterdam", "ireland",
    "dublin", "canada", "toronto", "vancouver", "montreal", "ottawa", "waterloo, on",
    "india", "bangalore", "bengaluru", "hyderabad", "pune", "mumbai", "new delhi",
    "gurgaon", "gurugram", "noida", "chennai", "kolkata", "kharagpur", "delhi",
    "ahmedabad", "coimbatore", "kochi", "thiruvananthapuram", "trivandrum", "jaipur",
    "indore", "bhubaneswar", "chandigarh", "mohali", "lucknow", "surat", "vadodara",
    "nagpur", "mysore", "mangalore", "visakhapatnam", "vizag", "singapore", "australia",
    "sydney", "melbourne", "japan", "tokyo", "israel", "tel aviv", "switzerland",
    "zurich", "zürich", "sweden", "stockholm", "denmark", "copenhagen", "norway", "oslo",
    "finland", "helsinki", "spain", "madrid", "barcelona", "portugal", "lisbon", "poland",
    "warsaw", "krakow", "kraków", "czech", "prague", "austria", "vienna", "belgium",
    "brussels", "italy", "milan", "brazil", "são paulo", "sao paulo", "mexico city",
    "colombia", "bogota", "bogotá", "argentina", "buenos aires", "china", "shanghai",
    "beijing", "shenzhen", "hong kong", "taiwan", "taipei", "philippines", "manila",
    "indonesia", "jakarta", "malaysia", "kuala lumpur", "thailand", "bangkok", "vietnam",
    "hanoi", "ho chi minh", "dubai", "abu dhabi", "riyadh", "cairo", "lagos", "nairobi",
    "johannesburg", "cape town", "new zealand", "auckland", "emea", "apac", "latam",
)
_STATE_CODE_RE = re.compile(r",\s*([a-z]{2})\b")


def detect_us_location(text: str) -> str:
    """Classify a job's location string as 'us' | 'foreign' | 'unknown'.
    US is checked first (so 'SF, CA, United States' wins over a stray foreign word).
    'unknown' covers bare 'Remote' / empty — the caller decides whether to keep it."""
    t = (text or "").lower()
    if not t.strip():
        return "unknown"
    if any(w in t for w in _US_WORDS):
        return "us"
    for m in _STATE_CODE_RE.finditer(t):           # "Austin, TX" / "Remote, CA"
        if m.group(1) in _US_STATE_CODES:
            return "us"
    if any(c in t for c in _US_CITIES):
        return "us"
    if any(f in t for f in _FOREIGN_LOC):
        return "foreign"
    return "unknown"


def detect_work_mode(text: str) -> str:
    """Best-effort work arrangement from a location string + JD text.
    Returns 'Remote', 'Hybrid', 'Onsite', or '' (unknown). Hybrid wins over
    Remote because hybrid posts usually mention both."""
    tl = normalize(text)
    if not tl:
        return ""
    if "hybrid" in tl:
        return "Hybrid"
    # avoid false "remote" on phrases like "no remote" / "not remote"
    if re.search(r"(?<!no )(?<!not )(?<!non-)\bremote\b", tl) and "not remote" not in tl:
        return "Remote"
    if re.search(r"\b(on-?site|in[- ]office|in[- ]person)\b", tl):
        return "Onsite"
    return ""
