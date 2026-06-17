"""
latex_resume.py — command-center style LaTeX resume workflow.

This ports the useful part of swe_application_command_center.html:
  * keep a real LaTeX template when the user uploads one
  * build a strict CO-STAR prompt for Claude/ChatGPT to tailor the resume
  * save pasted LaTeX safely
  * compute honest JD-keyword coverage against the tailored LaTeX

The app still never invents content and never auto-applies.
"""
import re
import base64
import os
import shutil
import subprocess
import tempfile

from textutils import detect_skills, detect_tools


ATS_STOPWORDS = {
    "about", "across", "after", "again", "also", "and", "apply", "are", "based",
    "build", "business", "candidate", "company", "customer", "data", "design",
    "develop", "engineer", "engineering", "experience", "help", "including",
    "looking", "opportunity", "product", "products", "requirements", "role",
    "software", "strong", "support", "team", "teams", "technology", "using",
    "with", "work", "working", "years", "you", "your",
}


def latex_escape(text: str) -> str:
    repl = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(repl.get(ch, ch) for ch in str(text or ""))


def strip_latex_fences(code: str) -> str:
    """Remove markdown fences from AI-returned LaTeX."""
    t = (code or "").strip()
    t = re.sub(r"^```(?:latex|tex)?\s*", "", t, flags=re.I)
    t = re.sub(r"\s*```$", "", t).strip()
    return t


def tex_to_text(raw: str) -> str:
    """Convert LaTeX resume source into readable plain text for parsing/scoring."""
    t = str(raw or "")
    t = re.sub(r"(^|\n)\s*%[^\n]*", r"\1", t)
    t = re.sub(r"\\(section|subsection|subsubsection)\*?\{([^}]*)\}", r"\n\n\2\n", t, flags=re.I)
    t = re.sub(r"\\textbf\{([^}]*)\}", r"\1", t, flags=re.I)
    t = re.sub(r"\\(textit|emph|underline|texttt|textsc)\{([^}]*)\}", r"\2", t, flags=re.I)
    t = re.sub(r"\\href\{[^}]*\}\{([^}]*)\}", r"\1", t, flags=re.I)
    t = re.sub(r"\\item\b", "\n- ", t, flags=re.I)
    t = t.replace(r"\\", "\n")
    t = re.sub(r"\\(begin|end)\{[^}]*\}", "\n", t, flags=re.I)
    t = re.sub(r"\\[a-zA-Z@]+(\[[^\]]*\])?(\{[^}]*\})?", " ", t)
    t = re.sub(r"\\([%#_~^])", r"\1", t)
    t = re.sub(r"[{}$&]", " ", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def profile_plain_text(profile: dict) -> str:
    """Truth-source text for prompts when raw resume text is not available."""
    if profile.get("raw_text"):
        return profile["raw_text"]
    parts = [
        profile.get("name", ""),
        profile.get("email", ""),
        profile.get("phone", ""),
        profile.get("summary", ""),
        "Skills: " + ", ".join(profile.get("skills", [])),
        "Tools: " + ", ".join(profile.get("tools", [])),
        "Domains: " + ", ".join(profile.get("domains", [])),
    ]
    for e in profile.get("experience", []):
        parts.append("Experience: " + " | ".join(x for x in [e.get("role"), e.get("company")] if x))
        parts.extend(e.get("bullets", []))
    for p in profile.get("projects", []):
        parts.append("Project: " + p.get("name", ""))
        parts.extend(p.get("bullets", []))
    return "\n".join(p for p in parts if p)


def _pretty(skill: str) -> str:
    upper = {"aws", "gcp", "sql", "oop", "nlp", "llm", "json", "rest apis", "html5", "css3"}
    return skill.upper() if skill in upper else str(skill).title()


def build_latex_template(profile: dict) -> str:
    """Built-in one-page LaTeX template used when no uploaded .tex exists."""
    links = profile.get("links", {}) or {}
    contact = [
        profile.get("phone", ""),
        profile.get("email", ""),
        links.get("linkedin", ""),
        links.get("github", ""),
    ]
    contact_line = " $\\cdot$ ".join(latex_escape(x) for x in contact if x)
    skill_line = ", ".join(latex_escape(_pretty(s)) for s in profile.get("skills", [])[:28])

    def bullets(items):
        if not items:
            return ""
        body = "\n".join(f"  \\resumeItem{{{latex_escape(x)}}}" for x in items)
        return "\\resumeItemListStart\n" + body + "\n\\resumeItemListEnd"

    exp_blocks = []
    for e in profile.get("experience", []):
        head = e.get("role") or e.get("company") or "Experience"
        company = e.get("company", "")
        exp_blocks.append(
            f"\\textbf{{{latex_escape(head)}}} \\hfill {latex_escape(company)}\\\\\n"
            f"{bullets(e.get('bullets', [])[:4])}"
        )

    project_blocks = []
    for p in profile.get("projects", []):
        project_blocks.append(
            f"\\textbf{{{latex_escape(p.get('name', 'Project'))}}}\\\\\n"
            f"{bullets(p.get('bullets', [])[:3])}"
        )

    # Education + Additional are built from the candidate's OWN parsed profile — never
    # hard-coded — so this fallback template can't inject someone else's school/awards.
    edu_blocks = []
    for e in profile.get("education", []):
        if not isinstance(e, dict):
            continue
        school = latex_escape(e.get("school", ""))
        loc = latex_escape(e.get("location", ""))
        degree = latex_escape(" | ".join(x for x in [e.get("degree", ""), e.get("detail", "")] if x))
        dates = latex_escape(e.get("dates", ""))
        edu_blocks.append(
            f"\\textbf{{{school}}} \\hfill {loc}\\\\\n\\textit{{{degree}}} \\hfill {dates}")
    education_section = ("\\section*{Education}\n" + "\\\\[2pt]\n".join(edu_blocks)
                         if edu_blocks else "")
    additional = profile.get("additional", [])
    additional_section = ("\\section*{Additional}\n" + bullets(additional)
                          if additional else "")

    return rf"""\documentclass[letterpaper,10pt]{{article}}
\usepackage[empty]{{fullpage}}
\usepackage[top=0.4in,bottom=0.4in,left=0.55in,right=0.55in]{{geometry}}
\usepackage{{enumitem}}
\usepackage[usenames,dvipsnames]{{color}}
\definecolor{{linkblue}}{{RGB}}{{0,90,170}}
\usepackage[colorlinks=true,urlcolor=linkblue,linkcolor=linkblue]{{hyperref}}
\usepackage{{titlesec}}
\setlength{{\parindent}}{{0pt}}
\setlist[itemize]{{leftmargin=0.18in,topsep=3pt,itemsep=2pt,parsep=0pt}}
\titleformat{{\section}}{{\large\bfseries\scshape\raggedright}}{{}}{{0em}}{{}}[\titlerule]
\titlespacing*{{\section}}{{0pt}}{{8pt}}{{5pt}}
\newcommand{{\resumeItem}}[1]{{\item\small{{#1}}}}
\newcommand{{\resumeItemListStart}}{{\begin{{itemize}}}}
\newcommand{{\resumeItemListEnd}}{{\end{{itemize}}}}
\begin{{document}}
\begin{{center}}
{{\Large \textbf{{{latex_escape(profile.get('name', 'Candidate'))}}}}}\\
\small {contact_line}
\end{{center}}

\section*{{Professional Summary}}
\small {latex_escape(profile.get('summary', ''))}

\section*{{Technical Skills}}
\small {skill_line}

\section*{{Work Experience}}
{chr(10).join(exp_blocks)}

\section*{{Projects}}
{chr(10).join(project_blocks)}

{education_section}

{additional_section}

\end{{document}}
"""


def command_center_template() -> str:
    """Load the stronger built-in LaTeX template from the old HTML command-center if
    it happens to sit next to this project. Looked up relative to the current dir and
    the project root — no machine-specific absolute paths."""
    _here = os.path.dirname(os.path.abspath(__file__))
    _root = os.path.dirname(_here)                      # Job_Automation/
    names = ("swe_application_command_center_hardened.html",
             "swe_application_command_center.html")
    candidates = []
    for base in (os.getcwd(), _root, os.path.dirname(_root)):
        for n in names:
            candidates.append(os.path.join(base, n))
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
            m = re.search(r'RESUME_LATEX_TEMPLATE_B64\s*=\s*"([^"]+)"', text)
            if not m:
                continue
            decoded = base64.b64decode(m.group(1)).decode("utf-8", errors="ignore")
            if "\\documentclass" in decoded and "\\resumeProject" in decoded:
                return decoded
        except Exception:
            continue
    return ""


def active_latex_template(profile: dict) -> str:
    template = (profile.get("latex_template") or "").strip()
    if len(template) > 120:
        return template
    command_template = command_center_template()
    return command_template if command_template else build_latex_template(profile)


def _escape_reg(s: str) -> str:
    return re.escape(str(s or ""))


# Precompiled once (this runs hundreds of times per board pull — see perf note below).
_JD_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#./-]{2,}")   # candidate keywords in a JD
_KW_TOKEN_RE = re.compile(r"[a-z0-9+#.]+")                   # a "token" for résumé matching
_TECH_CHARS = frozenset("0123456789+#/")


def extract_jd_keywords(jd_text: str) -> list:
    """Extract ATS-ish keywords from a JD, mirroring the old command center.
    (Hot path: uses plain-Python char tests instead of two regex calls per token —
    a JD has ~600 tokens and this runs for every pulled job.)"""
    jd = str(jd_text or "")
    found = list(detect_skills(jd) | detect_tools(jd))
    display = {x.lower(): x for x in found}

    for tok in _JD_TOKEN_RE.findall(jd):
        clean = tok.strip("./-")
        low = clean.lower()
        if len(clean) < 3 or len(clean) > 24 or low in ATS_STOPWORDS:
            continue
        # "looks technical": any uppercase anywhere, or a digit / + / # / char.
        looks_tech = any(c.isupper() for c in clean) or any(c in _TECH_CHARS for c in clean)
        if looks_tech and low not in display:
            display[low] = clean
    return list(display.values())[:40]


def resume_token_set(resume_text: str) -> set:
    """Maximal [a-z0-9+#.] token runs of a résumé, lowercased — the set used for the
    fast keyword-coverage lookup. Identical for every job, so build it ONCE and pass
    it to compute_text_ats_match(resume_tokens=...) when scoring a batch."""
    return set(_KW_TOKEN_RE.findall((resume_text or "").lower()))


def compute_text_ats_match(resume_text: str, jd_text: str, resume_tokens: set = None):
    """Keyword coverage for ANY résumé plain text vs the full JD.
    Mirrors the Jobscan-style 'hard skills / keyword match' metric: how many of
    the JD's keywords actually appear in the résumé.

    Perf: the résumé is tokenized ONCE into a set; a single-token keyword is then an
    O(1) set lookup instead of a fresh regex per keyword (this was ~75% of board-pull
    scoring time). Multi-part keywords ('rest apis', 'ci/cd') still use the boundary
    regex — the set lookup is provably equivalent to that regex for single tokens.
    Pass `resume_tokens` (from resume_token_set) to reuse the tokenization across a
    batch of jobs instead of re-tokenizing the résumé for every job."""
    if len((jd_text or "").strip()) < 120:
        return None
    rt = (resume_text or "").lower()
    keywords = extract_jd_keywords(jd_text)
    if not keywords:
        return None
    rt_tokens = resume_tokens if resume_tokens is not None else set(_KW_TOKEN_RE.findall(rt))
    matched, missing = [], []
    for kw in keywords:
        low = kw.lower()
        if low in rt_tokens:                    # fast path: kw is a whole résumé token
            matched.append(kw)
        elif _KW_TOKEN_RE.fullmatch(low):       # single token, not present → missing
            missing.append(kw)
        else:                                   # multi-part kw → boundary regex (rare)
            pat = r"(^|[^a-z0-9+#.])" + _escape_reg(low) + r"([^a-z0-9+#.]|$)"
            (matched if re.search(pat, rt) else missing).append(kw)
    return {
        "score": round(100 * len(matched) / len(keywords)),
        "matched": matched,
        "missing": missing,
        "total": len(keywords),
    }


def compute_latex_ats_match(latex: str, jd_text: str):
    """Keyword coverage for a pasted tailored LaTeX resume vs the full JD."""
    return compute_text_ats_match(tex_to_text(latex), jd_text)


def latex_engine() -> str:
    """Return an available LaTeX engine name, or '' if none is installed.
    Prefers tectonic (self-contained, auto-fetches packages)."""
    for eng in ("tectonic", "pdflatex", "xelatex", "lualatex"):
        if shutil.which(eng):
            return eng
    return ""


def compile_latex_to_pdf(tex_code: str, out_pdf_path: str, timeout: int = 180) -> str:
    """Compile LaTeX source to a PDF locally. Returns the output path.

    Raises RuntimeError with the tail of the compile log on failure (so the UI
    can show what went wrong). Requires tectonic or a TeX distribution.
    """
    eng = latex_engine()
    if not eng:
        raise RuntimeError(
            "No LaTeX engine found. Install one:  brew install tectonic")
    code = strip_latex_fences(tex_code)
    if "\\documentclass" not in code:
        raise RuntimeError("That doesn't look like a complete LaTeX resume "
                           "(no \\documentclass).")
    with tempfile.TemporaryDirectory() as td:
        tex_path = os.path.join(td, "resume.tex")
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(code)
        pdf_tmp = os.path.join(td, "resume.pdf")
        if eng == "tectonic":
            cmd = [eng, "--outdir", td, "--chatter", "minimal",
                   "--keep-logs", tex_path]
            runs = 1  # tectonic resolves refs itself
        else:
            cmd = [eng, "-interaction=nonstopmode", "-halt-on-error",
                   "-output-directory", td, tex_path]
            runs = 2  # classic engines need a second pass for layout/refs
        last = None
        for _ in range(runs):
            last = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if not os.path.exists(pdf_tmp):
            log = ((last.stdout or "") + "\n" + (last.stderr or "")).strip()
            raise RuntimeError(log[-1800:] or "LaTeX compile failed with no output.")
        os.makedirs(os.path.dirname(out_pdf_path) or ".", exist_ok=True)
        shutil.copy(pdf_tmp, out_pdf_path)
    return out_pdf_path


def build_tailor_prompt(profile: dict, job: dict, score: dict = None) -> str:
    """Strict CO-STAR prompt copied from the old command-center strategy."""
    jd = job.get("description") or f"{job.get('title', '')} at {job.get('company', '')}. Apply link: {job.get('job_link', '')}"
    matched = job.get("matched_skills_ai") or (score or {}).get("matched_skills", [])
    gaps = job.get("gaps_ai") or ((score or {}).get("missing_skills", []) + (score or {}).get("missing_tools", []))
    return rf"""# Tailored LaTeX Resume Builder (CO-STAR prompt)

## C - Context
I am applying to the target job below. I am giving you my real resume (source of truth for facts), my exact LaTeX template (the format to reuse), and the job description. Tailor my resume to this job WITHOUT inventing anything.
- Company: {job.get('company', '')}
- Role: {job.get('title', '')}
- Location: {job.get('location', '')}
- Apply link: {job.get('job_link', '')}
- Fit reason: {job.get('fit_reason', '')}
- Matched skills I genuinely have: {", ".join(matched)}
- Known gaps I must NOT fake: {", ".join(gaps)}

## O - Objective
Produce ONE complete, compilable, ATS-friendly, one-page LaTeX (.tex) resume tailored to this job, reusing my template's exact formatting and using ONLY my real content. Then COMPILE it with pdflatex and visually inspect the PDF before giving it to me; iterate until correct.

## S - Steps
1. TRUTH FIRST: use only facts from my real resume/template. Do not invent skills, employers, dates, metrics, awards, or projects. If a job-description keyword is something I do not actually have, do NOT add it. Tailoring = reorder, select, rephrase my REAL content and surface the most relevant items first.
2. RECRUITER PASS: before writing the final resume, evaluate my real resume as a recruiter for this role. Identify vague wording, weak bullets, missing measurable impact, and sections that could reduce shortlisting chances. Use this diagnosis internally only; do not output it.
3. VALUE PROPOSITION PASS: rewrite the Professional Summary as a clear 3-4 line value proposition. Avoid generic phrases like "passionate", "hard-working", "team player", "results-driven", or empty claims. Lead with role-relevant real proof.
4. TRANSFERABLE SKILLS PASS: identify transferable skills from my experience/projects that matter for the role, especially production UI delivery, API integration, dashboards, OAuth/calendar integration, full-stack APIs, ML/CNN benchmarking, DSA, and CS fundamentals. Emphasize only the ones present in my real resume.
5. ACTION LANGUAGE PASS: refine bullets to be concise, professional, and action-verb driven. Keep real numbers and facts exactly as sourced; do not create new metrics.
6. ATS PASS: keep role-relevant keywords I genuinely have near the top of Summary, Skills, Experience, and Projects. Move missing JD keywords into gaps mentally; never add them to the resume.
7. REUSE MY TEMPLATE EXACTLY: keep the preamble, \documentclass[letterpaper,10pt], packages, custom commands (\resumeHeading, \resumeProject, \resumeItem, \resumeItemListStart/End), colored-link hyperref setup, margins, and section styling. Change only the CONTENT inside sections. Do not redesign the layout or swap packages.
8. Fill each section per the content rules below.
9. Escape LaTeX special characters: &, %, $, #, _, {{, }}, ~, ^.
10. Compile with pdflatex, render the PDF to an image, and inspect it. Fix issues, then re-compile until clean.

Section content rules:
- Professional Summary: 3-4 concise lines targeted to the job, naming role-relevant real skills/proof, no generic fluff, never starting with "0)".
- Technical Skills: grouped by category as in the template. Only skills already in my resume; job-relevant skills first; do not add tools from the JD I do not have.
- Work Experience: preserve real company/role/dates/location. Real role headers. 2-4 bullets each: what I did, how I did it, and impact/result.
- Projects: each project separate with its own header; never merge two projects into one bullet; never put another project title mid-bullet; include tech stack, dates, and live/GitHub links if present; 2-3 strong bullets each.
- Education: school, degree, dates, GPA if present, on clean aligned lines.
- Additional: only concise real achievements / certifications / coding practice / awards / eligibility. No broken/truncated lines, no double bullets.

## T - Tools / Data Formats
- Tools: pdflatex compilation + visual inspection of the rendered PDF; iterate.
- Output: ONLY the final LaTeX code (no markdown fences, no commentary) plus a compiled preview.pdf.

Hard formatting requirements:
- No text overflow off the right edge. Use tabular* with @{{\extracolsep{{\fill}}}} so titles align left and dates/locations align to the right margin and wrap within the page.
- Margins around top 0.4in, bottom 0.4in, left 0.55in, right 0.55in.
- Project heading 3-part layout:
  Row 1 = bold title + inline [Live]/[GitHub] links + right-aligned date.
  Row 2 = tech stack in \small\textit{{}} on its own line.
  Then a small gap, then bullets.
- Education:
  Institution bold left + location right on row 1.
  Degree + GPA italic left + dates italic right on row 2.
- Work experience:
  Role bold + dates on row 1.
  Company italic + location italic on row 2.
- Links clickable AND visibly blue.
- NOT hidelinks.
- Email, LinkedIn, GitHub, Portfolio are live \href links.
- Each project gets inline [Live]/[GitHub].
- Never print raw URLs.
- 10pt, single column, ATS-friendly, itemsep=2pt, topsep=3pt.
- Natural spacing, not crushed.
- Aim for one full page.
- Expand real content if short, tighten modestly if it spills.
- Never pad with fake content.

## A - Audience
An ATS keyword parser AND a human recruiter for this specific role. It must pass automated keyword screening and read cleanly to a person.

## R - Reflection
After compiling, check and fix:
(a) text running off the right edge,
(b) [Live]/[GitHub] links dropping to a second line or overlapping tech stack,
(c) raw URLs printing,
(d) education entries merging,
(e) tech-stack jammed against titles,
(f) spilling onto a 2nd page,
(g) any "0)" or double bullets,
(h) any invented skill/metric/date.

Re-render until all are clean and every fact is true.

=== JOB DESCRIPTION / POSTING ===
{jd}

=== MY LATEX RESUME TEMPLATE (reuse this format exactly; tailor the content) ===
{active_latex_template(profile)}

=== MY RESUME AS PLAIN TEXT (source of truth for facts) ===
{profile_plain_text(profile)}"""
