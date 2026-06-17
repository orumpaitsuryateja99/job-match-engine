"""
test_core.py — smoke tests for the core loop (no Streamlit, no network).
Run:  python tests/test_core.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(BASE, "app"))

import resume_parser as rp
import ats as ats_mod
import tailor as tailor_mod
import pdf_gen
import tracker as trk
import sources as src
import aggregator as agg
import roles
import h1b as h1b_mod
import llm_score
import latex_resume as ltx

PASS, FAIL = "✅", "❌"
results = []


def check(name, cond):
    results.append((cond, name))
    print(f"{PASS if cond else FAIL} {name}")


# 1) profile
profile = rp.surya_default_profile()
check("profile has skills", len(profile["skills"]) > 5)
check("profile has projects", len(profile["projects"]) >= 2)

# 2) parse + score the sample JD
with open(os.path.join(BASE, "sample_data", "sample_jd.txt"), encoding="utf-8") as f:
    jd_text = f.read()
parsed = ats_mod.parse_job(jd_text, "Software Engineer, New Grad (Backend)")
score = ats_mod.ats_score(profile, parsed)
print("   ATS score:", score["score"], score["band"], "| components:", score["components"])
check("score is 0-100", 0 <= score["score"] <= 100)
check("strong-ish match (>=70) for a backend SWE JD", score["score"] >= 70)
check("title alignment > 0", score["components"]["title"] > 0)
check("detects missing kubernetes OR kafka as a gap",
      any(k in score["missing_skills"] for k in ("kubernetes", "kafka")))
check("does NOT list python as missing (we have it)", "python" not in score["missing_skills"])

# 3) tailoring + honesty validator (positive case)
tailored = tailor_mod.tailor_resume(profile, parsed)
ok, violations = tailor_mod.validate_no_fabrication(tailored, profile)
check("honest tailoring passes validator", ok)
if not ok:
    print("   violations:", violations)
check("tailored skills are a subset of real skills",
      set(tailored["skills"]).issubset(set(profile["skills"]) | set(profile["tools"])))
check("tailoring does NOT duplicate experience as a project",
      all(p.get("name") != "VIVA FIT" for p in tailored.get("projects", [])))
check("tailoring keeps VIVA FIT in experience",
      any(e.get("company") == "VIVA FIT" for e in tailored.get("experience", [])))
check("tailored summary is a concise value proposition",
      "Focus areas for this role" not in tailored.get("summary", "")
      and len([s for s in tailored.get("summary", "").split(".") if s.strip()]) >= 2)
check("tailoring records recruiter/ATS quality pass",
      tailored.get("tailoring_quality_notes", {}).get("summary_value_proposition") is True
      and tailored.get("tailoring_quality_notes", {}).get("action_language_checked") is True)
check("tailoring keeps missing JD skills as gaps",
      any(g in tailored.get("tailoring_quality_notes", {}).get("known_gaps_not_added", [])
          for g in ("kubernetes", "kafka")))

# 3b) honesty validator NEGATIVE case — must catch a fabricated skill
bad = {k: v for k, v in tailored.items()}
bad["skills"] = tailored["skills"] + ["kubernetes"]   # we do NOT have this
ok_bad, viol_bad = tailor_mod.validate_no_fabrication(bad, profile)
check("validator CATCHES an invented skill", (not ok_bad) and len(viol_bad) > 0)

# 3c) validator catches an invented metric in a bullet
bad2 = {k: v for k, v in tailored.items()}
bad2["projects"] = [{"name": "ClimateAI", "bullets": ["Improved performance by 999%"]}]
ok_bad2, _ = tailor_mod.validate_no_fabrication(bad2, profile)
check("validator CATCHES an invented number", not ok_bad2)

# 3d) cover letter: truthful, names the company, leads with the strongest project,
#     passes its own honesty validator; and the validator catches an injected fake skill.
_cl_job = ats_mod.parse_job("Build REST APIs in Python and Flask, SQL, AWS, microservices. 0-2 years.",
                            "Backend Software Engineer, New Grad")
_cl_job["company"] = "Stripe"
_letter = tailor_mod.cover_letter(profile, _cl_job)
_cl_ok, _cl_v = tailor_mod.validate_cover_letter(_letter, profile)
check("cover_letter is truthful (validates), names the company, leads with a real project",
      _cl_ok and "Stripe" in _letter
      and ("ClimateAI" in _letter or any(p["name"] in _letter for p in profile.get("projects", []))))
check("validate_cover_letter CATCHES a skill not in the résumé",
      not tailor_mod.validate_cover_letter(_letter + " Also expert in Kubernetes and Rust.", profile)[0])

# 4) PDF generation
with tempfile.TemporaryDirectory() as td:
    pdf_path = os.path.join(td, "out.pdf")
    pdf_gen.generate_resume_pdf(tailored, pdf_path)
    check("PDF created", os.path.exists(pdf_path))
    check("PDF non-trivial size", os.path.getsize(pdf_path) > 1500)
    with open(pdf_path, "rb") as f:
        head = f.read(5)
    check("PDF has %PDF header", head == b"%PDF-")

# 5) tracker write/read/follow-up
with tempfile.TemporaryDirectory() as td:
    xlsx = os.path.join(td, "tracker", "job_applications.xlsx")
    app_id = trk.append_application(xlsx, {
        "company": "Stripe", "job_title": "SWE New Grad", "location": "Remote",
        "job_link": "https://stripe.com/jobs/x", "source": "Greenhouse",
        "ats_score": score["score"], "h1b_sponsor": True,
        "resume_file": "Resume_Stripe.pdf", "applied": True, "notes": "test",
    })
    check("app_id generated", app_id.startswith("APP-"))
    rows = trk.read_all(xlsx)
    check("one row written", len(rows) == 1)
    check("row has correct company", rows[0]["company"] == "Stripe")
    check("follow_up_date auto-set", bool(rows[0]["follow_up_date"]))
    counts = trk.summary_counts(xlsx)
    check("summary counts total = 1", counts["_total"] == 1)

# 5b) SENIORITY + YEARS FILTER — the exact bug from the screenshot
import textutils as tu
# titles that MUST be rejected as senior/mid
for bad_title in ["Sr. Software Engineer - Performance", "Senior Backend Engineer",
                  "Staff Software Engineer", "Software Engineer II", "Software Engineer III",
                  "Lead Developer", "Engineering Manager", "Principal Engineer",
                  "Software Engineer 3", "Level 4 Engineer", "Software Engineer, L5"]:
    check(f"is_senior_title('{bad_title[:28]}') == True", tu.is_senior_title(bad_title))
# titles that MUST pass as entry-level
for ok_title in ["Software Engineer", "Software Engineer I", "Software Engineer, New Grad",
                 "Associate Software Engineer", "Backend Engineer", "Full Stack Engineer"]:
    check(f"is_senior_title('{ok_title[:28]}') == False", not tu.is_senior_title(ok_title))
# REGRESSION: a bare standalone digit (duration / count) must NOT read as a level —
# these used to be silently dropped as "senior" by a stray \b[2-9]\b.
for ok_title in ["Software Engineer - 3 Month Contract", "Hiring 5 Software Engineers",
                 "Software Engineer, 2 openings", "New Grad Software Engineer (Backend Rust)"]:
    check(f"is_senior_title('{ok_title[:30]}') == False (bare digit)",
          not tu.is_senior_title(ok_title))
# a numeric level attached to a role word IS still senior
check("is_senior_title keeps 'SDE 2' / 'Software Engineer 3' as senior",
      tu.is_senior_title("SDE 2") and tu.is_senior_title("Software Engineer 3"))
# REGRESSION: years required must come from a real requirement, not company age / fluff
check("extract_years_required ignores company-age phrasing",
      tu.extract_years_required("Trusted for over 20 years; entry level role welcome") == 0
      and tu.extract_years_required("We have 10 years of history. New grads encouraged.") == 0
      and tu.extract_years_required("Founded 8 years ago") == 0)
check("extract_years_required still reads a genuine requirement",
      tu.extract_years_required("5+ years of experience") == 5
      and tu.extract_years_required("0-2 years experience") == 0
      and tu.extract_years_required("minimum 3 years of experience") == 3)

# the entry-level gate end-to-end (title + JD years)
check("REJECT: Sr. Software Engineer - Performance",
      not src._is_entry_level_swe("Sr. Software Engineer - Performance", ""))
check("REJECT: Full Stack Engineer requiring 3-5 years",
      not src._is_entry_level_swe("Full Stack Engineer, Billing",
                                  "We require 3-5 years of professional experience."))
check("REJECT: Software Engineer II",
      not src._is_entry_level_swe("Software Engineer II", "0-2 years"))
check("PASS: Software Engineer, New Grad (0-2 years)",
      src._is_entry_level_swe("Software Engineer, New Grad", "0-2 years of experience"))
check("PASS: Software Engineer I",
      src._is_entry_level_swe("Software Engineer I", "1-2 years"))
check("PASS: plain Software Engineer, no years stated",
      src._is_entry_level_swe("Software Engineer", "Build great backend services."))
check("REJECT: Data Scientist (not SWE)",
      not src._is_entry_level_swe("Data Scientist", "0-2 years"))
check("New Grad focus accepts New Grad SWE",
      src._is_entry_level_swe("Software Engineer, New Grad", "0-2 years",
                              focus_keys=["newgrad"]))
check("New Grad focus accepts Associate SWE",
      src._is_entry_level_swe("Associate Software Engineer", "0-1 years",
                              focus_keys=["newgrad"]))
check("New Grad focus still rejects senior title",
      not src._is_entry_level_swe("Senior Software Engineer", "0-2 years",
                                  focus_keys=["newgrad"]))
check("New Grad focus still rejects non-SWE title",
      not src._is_entry_level_swe("Data Scientist", "0-2 years",
                                  focus_keys=["newgrad"]))

# 5b.2) focus filter rejects non-software "entry level <trade>" + off-type roles
WEB = ["newgrad", "backend", "frontend", "fullstack"]
check("REJECT: 'Entry Level Auto Body Painter' for a web/new-grad hunt",
      not roles.title_matches_focus("Entry Level Auto Body Painter", WEB))
check("REJECT: 'Entry-Level Auto Technician'",
      not roles.title_matches_focus("Entry-Level Auto Technician", WEB))
check("REJECT: robotics SWE when hunting web roles",
      not roles.title_matches_focus("Software Engineer - Simulation & Robotics", WEB))
check("REJECT: GPU kernel SWE when hunting web roles",
      not roles.title_matches_focus("Software Engineer - GPU Kernels", WEB))
check("REJECT: 'Wireless Software Engineer' (RF/firmware) for a web/new-grad hunt",
      not roles.title_matches_focus("Wireless Software Engineer", WEB)
      and not roles.title_matches_focus("Wireless Software Engineer", ["newgrad"]))
check("REJECT: 'Software Engineer, Network' (networking) for a web hunt",
      not roles.title_matches_focus("Software Engineer, Network", WEB))
check("KEEP: 'Software Engineer, Network' WHEN DevOps focus is selected (networking on-focus)",
      roles.title_matches_focus("Software Engineer, Network", ["devops"]))
check("KEEP: plain Software Engineer for web/new-grad hunt",
      roles.title_matches_focus("Software Engineer", WEB))
check("KEEP: Full Stack Engineer for web/new-grad hunt",
      roles.title_matches_focus("Full Stack Engineer, Growth", WEB))
check("KEEP: robotics SWE when ML focus IS selected",
      roles.title_matches_focus("Software Engineer - Robotics", ["mlai"]))

# 5b.2b) HARD-REJECT non-software role families on EVERY focus (incl. general) —
#        the exact leak the user hit ("Semiconductor Quality Assurance Engineer").
for _bad in ["Semiconductor Quality Assurance Engineer", "QA Engineer", "Test Engineer",
             "Data Entry Clerk", "IT Support Specialist", "Help Desk Technician",
             "Business Analyst", "Sales Engineer", "Solutions Engineer",
             "Mechanical Engineer", "Field Service Technician", "Product Manager",
             "Customer Success Manager", "Technical Recruiter"]:
    check(f"HARD-REJECT (web focus): {_bad[:30]}", not roles.title_matches_focus(_bad, WEB))
    check(f"HARD-REJECT (general focus): {_bad[:30]}", not roles.title_matches_focus(_bad, ["general"]))
# …but a clearly software-dev title with an incidental word is KEPT
check("KEEP: 'Software Engineer in Test' (SDET is still a SWE dev title)",
      roles.title_matches_focus("Software Engineer in Test", WEB))
check("KEEP: 'Software Engineer, Sales Platform' (builds sales software)",
      roles.title_matches_focus("Software Engineer, Sales Platform", ["general"]))

# 5b.3) New-grad detector: 'New Grad' focus must require a real new-grad signal,
#       so a plain 'Software Engineer' is NOT treated as a new-grad posting.
check("new-grad: 'New Grad Software Engineer' title matches",
      roles.is_new_grad_role("New Grad Software Engineer"))
check("new-grad: 'Software Engineer I' / 'Associate SWE' match",
      roles.is_new_grad_role("Software Engineer I")
      and roles.is_new_grad_role("Associate Software Engineer"))
check("new-grad: detected from JD language when title is generic",
      roles.is_new_grad_role("Software Engineer", "We welcome recent graduates / early-career engineers."))
check("new-grad: plain 'Software Engineer' with a normal JD is NOT new-grad",
      not roles.is_new_grad_role("Software Engineer", "Build backend services. 2+ years experience."))
check("new-grad: 'Software Engineer II' / 'Senior' are NOT new-grad",
      not roles.is_new_grad_role("Software Engineer II")
      and not roles.is_new_grad_role("Senior Software Engineer"))
# New Grad is a SOFT signal: it tags + prioritizes genuine new-grad roles but does
# NOT hard-drop other entry-level roles (so the list isn't empty off-season).
check("board gate: New Grad focus KEEPS a plain 'Software Engineer' (soft, not dropped)",
      src._is_entry_level_swe("Software Engineer", "Build services. 0-2 years.",
                              focus_keys=["newgrad"]))
check("board gate: New Grad focus KEEPS 'Software Engineer I'",
      src._is_entry_level_swe("Software Engineer I", "0-2 years.", focus_keys=["newgrad"]))
check("new-grad TAG distinguishes 'Software Engineer I' from a plain 'Software Engineer'",
      roles.is_new_grad_role("Software Engineer I")
      and not roles.is_new_grad_role("Software Engineer", "Build services. 0-2 years."))
_entry_ui_keys = ["newgrad_swe", "entry_swe", "junior_dev", "associate_swe",
                  "swe_i", "new_college_grad_swe"]
check("Role focus exposes exact entry-level default chip labels",
      [roles.ROLE_FOCUS[k]["label"] for k in _entry_ui_keys]
      == ["New Grad Software Engineer", "Entry Level Software Engineer",
          "Junior Software Developer", "Associate Software Engineer",
          "Software Engineer I", "New College Grad SWE"])
check("entry_level_title_queries starts with the six exact default role chips",
      src.entry_level_title_queries(_entry_ui_keys, limit=6)
      == ["new grad software engineer", "entry level software engineer",
          "junior software developer", "associate software engineer",
          "software engineer i", "new college grad software engineer"])

# 5b.1) skill detection precision (combined-regex matcher)
check("detect_skills finds real skills",
      {"python", "java", "react", "aws"}.issubset(
          tu.detect_skills("We use Python, Java, React and AWS daily.")))
check("detect_skills tags javascript from a standalone 'JS' token",
      "javascript" in tu.detect_skills("Strong JS fundamentals required."))
check("detect_skills does NOT tag javascript from a bare 'node.js' mention",
      "javascript" not in tu.detect_skills("Built services in node.js on the backend.")
      and "node.js" in tu.detect_skills("Built services in node.js on the backend."))
check("detect_skills respects word boundaries (no 'sql' inside 'mysql')",
      tu.detect_skills("We run MySQL in production.") == {"mysql"})
check("detect_tools finds git + github",
      {"git", "github"}.issubset(tu.detect_tools("Use Git and GitHub for version control.")))
check("detect_domains maps 'machine learning' to ai/ml",
      "ai/ml" in tu.detect_domains("Experience with machine learning models."))

# de-duplication
dupes = [
    {"company": "Databricks", "title": "Sr. SWE - Performance", "location": "SF", "job_link": "https://x/1"},
    {"company": "Databricks", "title": "Sr. SWE - Performance", "location": "SF", "job_link": "https://x/1"},
    {"company": "Stripe", "title": "Full Stack Engineer", "location": "Remote", "job_link": "https://x/2"},
]
check("dedupe removes the duplicate", len(src.dedupe(dupes)) == 2)

# 5c) AI-paste import (command-center JSON schema) with filtering + dedupe
paste = '''```json
[
  {"company":"Stripe","role":"Software Engineer, New Grad","location":"Remote","apply_link":"https://stripe.com/jobs/1","job_description":"Build APIs. 0-2 years.","fit_score":88,"experience_required":"0-2 years"},
  {"company":"Databricks","role":"Sr. Software Engineer - Performance","location":"SF","apply_link":"https://db/2","job_description":"5+ years.","fit_score":80},
  {"company":"Acme","role":"Full Stack Engineer","location":"NYC","apply_link":"https://acme/3","job_description":"We need 3-5 years.","fit_score":70},
  {"company":"Stripe","role":"Software Engineer, New Grad","location":"Remote","apply_link":"https://stripe.com/jobs/1","job_description":"dup","fit_score":88}
]
```'''
pasted, stats = src.normalize_pasted_jobs(paste, new_grad_only=True, max_years=2)
check("AI-paste keeps only the entry-level role", len(pasted) == 1)
check("AI-paste kept Stripe new grad", bool(pasted) and pasted[0]["company"] == "Stripe")
check("AI-paste filtered the senior + 3-5yr roles", stats["filtered_senior"] >= 2)
check("AI-paste carried fit_score through", bool(pasted) and pasted[0].get("fit_score") == 88)

# 5c.1) command-center link quality + richer JSON fields
check("safe_url adds https to www links",
      src.safe_url("www.example.com/jobs/123").startswith("https://www.example.com"))
check("relative posting age: 7 days ago fails a 3-hour filter",
      src._too_old("7 days ago", 3))
check("relative posting age: 2 hours ago passes a 3-hour filter",
      not src._too_old("2 hours ago", 3))
check("search-like Greenhouse board root is flagged",
      src.is_search_like_link("https://boards.greenhouse.io/stripe"))
check("exact Greenhouse posting is not flagged",
      not src.is_search_like_link("https://boards.greenhouse.io/stripe/jobs/123456"))
check("Lever board root is flagged",
      src.is_search_like_link("https://jobs.lever.co/plaid"))
check("exact Lever posting is not flagged",
      not src.is_search_like_link("https://jobs.lever.co/plaid/abc-123"))
rich_paste = '''[
  {
    "company":"Stripe",
    "role":"Software Engineer, New Grad",
    "location":"Remote",
    "apply_link":"https://boards.greenhouse.io/stripe",
    "job_description":"Build APIs. 0-2 years.",
    "fit_score":88,
    "matched_skills":["Python","REST APIs"],
    "gaps":["Kubernetes"],
    "posted_date":"2026-05-28",
    "salary":"$120k",
    "priority":"Apply immediately",
    "h1b_note":"Recent sponsor history - verify"
  }
]'''
rich_jobs, rich_stats = src.normalize_pasted_jobs(rich_paste, new_grad_only=True, max_years=2)
check("AI-paste preserves matched skills",
      rich_jobs and rich_jobs[0]["matched_skills_ai"] == ["Python", "REST APIs"])
check("AI-paste preserves gaps/priority/salary",
      rich_jobs and rich_jobs[0]["gaps_ai"] == ["Kubernetes"]
      and rich_jobs[0]["priority"] == "Apply immediately"
      and rich_jobs[0]["salary"] == "$120k")
check("AI-paste marks search-like link warning",
      rich_jobs and rich_jobs[0]["link_warning"] and rich_stats["link_warnings"] == 1)
# An honest empty array (the prompt says "If none pass, return []") is a VALID result,
# not a paste error; genuinely unparseable text still errors.
_e_jobs, _e_stats = src.normalize_pasted_jobs("[]", new_grad_only=True, max_years=2)
_b_jobs, _b_stats = src.normalize_pasted_jobs("not json", new_grad_only=True, max_years=2)
check("AI-paste: empty [] is valid (no error), unparseable text errors",
      _e_jobs == [] and not _e_stats.get("error") and _e_stats.get("empty")
      and _b_jobs == [] and bool(_b_stats.get("error")))

# 5d) hardened search prompt builder
import prompts as pr
pr_text = pr.build_job_search_prompt(profile, prefs="backend", location="Remote", max_years=2)
check("prompt builder non-empty", len(pr_text) > 400)
check("prompt forbids recalling from memory", "memory" in pr_text.lower())
check("prompt includes the JSON schema", "apply_link" in pr_text)
for source_name in ("OPTnation", "Interstride", "Dice", "Glassdoor", "Built In", "Lensa",
                    "LinkedIn", "Wellfound", "Handshake", "RippleMatch", "Workday"):
    check(f"prompt includes {source_name}", source_name in pr_text)

# 5e) improved resume parser extracts + merges wrapped bullets
sample_resume = """Jane Doe
jane@example.com | (123) 456-7890

EXPERIENCE
Software Engineer Intern, Acme Corp  Jun 2024 - Aug 2024
- Built a REST API in Python and Flask serving 2 endpoints
- Wrote unit tests and deployed on AWS

PROJECTS
Chat App
- Built a full-stack chat app with Node.js and PostgreSQL handling
  real-time messages over WebSockets
- Designed the database schema and REST endpoints

EDUCATION
MS Computer Science
"""
jp = rp.build_profile_from_text(sample_resume)
check("parser extracts experience bullets", sum(len(s["bullets"]) for s in jp["experience"]) >= 2)
check("parser extracts project bullets", sum(len(s["bullets"]) for s in jp["projects"]) >= 2)
check("parser merges a wrapped line",
      any("real-time messages over WebSockets" in b for s in jp["projects"] for b in s["bullets"]))
check("parser detects python from the text", "python" in jp["skills"])

# 5e.1) real Suryateja PDF parsing keeps all major resume sections
real_resume = os.path.join(BASE, "input_resume", "resume_suryateja_claude.pdf")
if os.path.exists(real_resume):
    real_profile = rp.parse_resume(real_resume)
    check("real resume extracts LinkedIn/GitHub/portfolio links",
          all(k in real_profile.get("links", {}) for k in ("linkedin", "github", "portfolio")))
    check("real resume extracts full professional summary",
          len(real_profile.get("summary", "")) > 400 and "new grad Software Engineer" in real_profile["summary"])
    check("real resume extracts 3 VIVA FIT bullets",
          real_profile.get("experience") and len(real_profile["experience"][0].get("bullets", [])) == 3)
    check("real resume extracts project metadata",
          len(real_profile.get("projects", [])) == 2
          and real_profile["projects"][0].get("subtitle")
          and real_profile["projects"][0].get("tech")
          and real_profile["projects"][0].get("links", {}).get("live"))
    check("real resume preserves exact skill categories",
          len(real_profile.get("skill_categories", [])) >= 6
          and any(c.get("category") == "Developer Tools" and "GitHub" in c.get("items", [])
                  for c in real_profile.get("skill_categories", [])))
    check("real resume detects expanded skills/tools",
          all(x in real_profile.get("skills", []) for x in ("chart.js", "google gemini api", "sam", "c"))
          and "github" in real_profile.get("tools", []))
    check("real resume extracts education",
          len(real_profile.get("education", [])) == 2
          and real_profile["education"][0].get("school") == "University of Georgia")
    check("real resume extracts additional achievements",
          len(real_profile.get("additional", [])) == 2)

# 5e.2) command-center LaTeX resume workflow
sample_tex = r"""\documentclass[letterpaper,10pt]{article}
\begin{document}
\section{Technical Skills}
\textbf{Languages:} Python, Java, SQL
\section{Projects}
\textbf{ClimateAI}
\begin{itemize}
\item Built REST APIs with Flask and AWS deployment support.
\end{itemize}
\end{document}
"""
check("tex_to_text keeps section text", "Technical Skills" in ltx.tex_to_text(sample_tex))
with tempfile.TemporaryDirectory() as td:
    tex_path = os.path.join(td, "resume.tex")
    with open(tex_path, "w", encoding="utf-8") as f:
        f.write(sample_tex)
    tex_profile = rp.parse_resume(tex_path)
    check("parse_resume preserves uploaded .tex template",
          tex_profile.get("latex_template", "").startswith("\\documentclass"))
    check("parse_resume detects skills from .tex", "python" in tex_profile["skills"])
prompt_job = {
    "company": "Stripe",
    "title": "Software Engineer, New Grad",
    "location": "Remote",
    "job_link": "https://boards.greenhouse.io/stripe/jobs/123",
    "description": jd_text,
    "fit_reason": "Backend API fit",
    "matched_skills_ai": ["Python", "REST APIs"],
    "gaps_ai": ["Kubernetes"],
}
latex_prompt = ltx.build_tailor_prompt(profile, prompt_job, score)
check("LaTeX tailor prompt includes exact template block",
      "MY LATEX RESUME TEMPLATE" in latex_prompt)
check("LaTeX tailor prompt includes truth-first rule",
      "Do not invent skills" in latex_prompt)
check("LaTeX tailor prompt includes project heading layout rule",
      "Project heading 3-part layout" in latex_prompt)
check("LaTeX tailor prompt includes raw URL rule",
      "Never print raw URLs" in latex_prompt)
check("strip_latex_fences removes markdown fence",
      ltx.strip_latex_fences("```tex\n\\documentclass{article}\n```") == "\\documentclass{article}")
ats_latex = ltx.compute_latex_ats_match(sample_tex, jd_text)
check("LaTeX ATS coverage computes", ats_latex is not None and 0 <= ats_latex["score"] <= 100)
check("LaTeX ATS coverage reports matched keywords",
      ats_latex is not None and len(ats_latex["matched"]) > 0)

# 5e.3) parser robustness — missing sections, edge skills, .tex structure
mini_resume = "John Smith\njohn@x.com | (111) 222-3333\nSome intro about being a developer.\n"
mini_profile = rp.build_profile_from_text(mini_resume)
check("parser handles a résumé with NO sections (no crash, empty lists)",
      mini_profile["experience"] == [] and mini_profile["projects"] == []
      and mini_profile["skill_categories"] == [])
check("parser still extracts name + email from a minimal résumé",
      mini_profile["email"] == "john@x.com" and mini_profile["name"] == "John Smith")

cat_profile = rp.build_profile_from_text(
    "TECHNICAL SKILLS\nLanguages: C, C++, Python, Java\nWORK EXPERIENCE\n")
check("skill categories capture bare 'C' and 'C++'",
      "c" in cat_profile["skills"] and "c++" in cat_profile["skills"])
check("detect_skills finds C++ in free text", "c++" in tu.detect_skills("Strong in C++ and Python."))
# guarded bare-"C" detection: credited only in real C-language contexts (and never
# from noisy prose), while C++ still detected independently in the same string.
check("detect_skills credits bare C in 'C, C++, Python' (and keeps c++)",
      {"c", "c++", "python"}.issubset(tu.detect_skills("Languages: C, C++, Python")))
check("detect_skills credits C in 'C/C++' and 'embedded C'",
      "c" in tu.detect_skills("Proficient in C/C++.") and "c" in tu.detect_skills("Embedded C experience."))
check("detect_skills does NOT credit C from noisy prose",
      "c" not in tu.detect_skills("Earned a C in calculus; works at C Corp on the C-suite team."))
# US-location detector (H1B-critical strict-US filter)
check("US location: 'Austin, TX' / 'San Francisco, CA' / 'Remote, US' → us",
      tu.detect_us_location("Austin, TX") == "us"
      and tu.detect_us_location("San Francisco, CA") == "us"
      and tu.detect_us_location("Remote, US") == "us")
check("US location: 'Berlin, Germany' / 'London, UK' / 'Bangalore, India' → foreign",
      tu.detect_us_location("Berlin, Germany") == "foreign"
      and tu.detect_us_location("London, UK") == "foreign"
      and tu.detect_us_location("Bangalore, India") == "foreign"
      and tu.detect_us_location("Junior Software Engineer - Kharagpur") == "foreign")
check("US location: bare 'Remote' / empty → unknown (strict-US drops later)",
      tu.detect_us_location("Remote") == "unknown" and tu.detect_us_location("") == "unknown")

with tempfile.TemporaryDirectory() as td:
    tp_path = os.path.join(td, "structured.tex")
    with open(tp_path, "w", encoding="utf-8") as f:
        f.write(sample_tex)
    tex_structured = rp.parse_resume(tp_path)
    check("parse_resume extracts a project from .tex source",
          any(p.get("name") == "ClimateAI" and p.get("bullets")
              for p in tex_structured.get("projects", [])))
    check("parse_resume detects skills (incl. rest apis) from .tex source",
          {"python", "java", "sql", "rest apis"}.issubset(set(tex_structured.get("skills", []))))

# 5f) Ashby is wired into pull_targets routing
check("ashby() exists", hasattr(src, "ashby"))
check("pull_targets_verbose returns (jobs, errors)",
      isinstance(src.pull_targets_verbose([], new_grad_only=True), tuple))

# 6) sources helpers (no network)
targets = src.load_targets(os.path.join(BASE, "config", "target_companies.txt"))
check("target list parsed", len(targets) >= 5)
check("targets have ats+token", all("ats" in t and "token" in t for t in targets))
# (H1B matching is covered by the structured h1b.status tests in §6b — the old
#  substring-based src.is_h1b_sponsor() has been removed.)
manual_job = src.manual("SWE", "Acme", "NYC", "https://x", "We use Python and AWS.")
check("manual job shaped correctly", manual_job["source"] == "Manual")

# 6b) H1B structured database (h1b.py) — exact/alias matching against the real JSON
H1B_DB = os.path.join(BASE, "config", "h1b_sponsors.json")
h1b_mod._CACHE.clear()
g = h1b_mod.status("Google", H1B_DB)
check("H1B DB: Google is a verified (high) sponsor",
      g["sponsor"] and g["confidence"] == "high" and g["label"] == "Verified")
check("H1B DB: every target company resolves as a sponsor",
      all(h1b_mod.status(t["name"], H1B_DB)["sponsor"]
          for t in src.load_targets(os.path.join(BASE, "config", "target_companies.txt"))))
check("H1B DB: alias + merged entry resolve (Mistral AI / 'mistral')",
      h1b_mod.status("Mistral AI", H1B_DB)["sponsor"]
      and h1b_mod.status("mistral", H1B_DB)["sponsor"])
check("H1B DB: NO loose substring match ('Goog' != Google)",
      not h1b_mod.status("Goog", H1B_DB)["sponsor"])
check("H1B DB: unknown company is Unknown, not a sponsor",
      not h1b_mod.status("Totally Made Up LLC", H1B_DB)["sponsor"])
check("H1B DB: badges map confidence correctly",
      h1b_mod.badge({"confidence": "high"}) == "✅ H1B (verified)"
      and h1b_mod.badge({"confidence": "medium"}) == "🟢 H1B (likely)"
      and h1b_mod.badge({}) == "⚠️ verify H1B")
check("H1B DB: every entry has aliases and a confidence",
      all(s.get("aliases") and s.get("confidence") in ("high", "medium")
          for s in __import__("json").load(open(H1B_DB, encoding="utf-8"))["sponsors"]))

# 6d) Role-angle tailoring — lead with the right project per role type
ml_job = ats_mod.parse_job("ML engineer. TensorFlow, Keras, PyTorch, computer vision, CNN.", "ML Engineer")
be_job = ats_mod.parse_job("Backend engineer. Python, Flask, REST APIs, SQL, AWS, microservices.", "Backend Engineer")
ml_tailored = tailor_mod.tailor_resume(profile, ml_job)
be_tailored = tailor_mod.tailor_resume(profile, be_job)
check("role-angle: ML role detected", ml_tailored["tailoring_angle"] == "ml_ai")
check("role-angle: ML role leads with the CNN project",
      "weed" in (ml_tailored["projects"][0]["name"].lower()))
check("role-angle: backend role leads with ClimateAI",
      "climate" in (be_tailored["projects"][0]["name"].lower()))
check("role-angle: ML summary surfaces real ML proof",
      "TensorFlow" in ml_tailored.get("summary", "") and "97%" in ml_tailored.get("summary", ""))
check("role-angle: backend summary surfaces real API proof",
      "ClimateAI" in be_tailored.get("summary", "") and "REST APIs" in be_tailored.get("summary", ""))
check("role-angle tailoring still passes the honesty validator",
      tailor_mod.validate_no_fabrication(ml_tailored, profile)[0]
      and tailor_mod.validate_no_fabrication(be_tailored, profile)[0])

# 6e) Optional Claude relevance scorer (llm_score) — pure logic, no network
check("llm_score.available is False without a key", llm_score.available("") is False)
check("llm_score.available returns a bool with a key", isinstance(llm_score.available("sk-x"), bool))
_sample = ('{"results":[{"index":0,"fit_score":150,"fit_reason":"strong python/flask overlap",'
           '"matched_skills":["python","flask"],"gaps":["kubernetes"]},'
           '{"index":1,"fit_score":-5,"fit_reason":"off-domain","matched_skills":[],"gaps":["c++"]}]}')
_parsed = llm_score._parse_results(_sample)
check("llm_score parses results + clamps fit_score to 0-100",
      len(_parsed) == 2 and _parsed[0]["fit_score"] == 100 and _parsed[1]["fit_score"] == 0)
_batch = [{"job": {"title": "SWE", "company": "A"}}, {"job": {"title": "SWE2", "company": "B"}}]
_merged = llm_score.merge_scores(_batch, _parsed)
check("llm_score.merge_scores writes fit_score + reason + ai skills/gaps onto jobs",
      _merged == 2 and _batch[0]["job"]["fit_score"] == 100
      and _batch[0]["job"]["matched_skills_ai"] == ["python", "flask"]
      and _batch[1]["job"]["gaps_ai"] == ["c++"])
check("llm_score.merge_scores ignores out-of-range indices",
      llm_score.merge_scores(_batch, [{"index": 9, "fit_score": 50}]) == 0)

# 5d) The Muse source: normalization + entry-level/focus gating (network stubbed)
class _FakeResp:
    def __init__(self, payload, status_code=200):
        self._p = payload
        self.status_code = status_code

    @property
    def ok(self):
        return self.status_code == 200

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._p


_muse_payload = {"results": [
    {"name": "Software Engineer, Backend", "company": {"name": "Acme"},
     "locations": [{"name": "Austin, TX"}], "contents": "Build REST APIs in Python.",
     "publication_date": "", "refs": {"landing_page": "https://www.themuse.com/jobs/acme/swe"}},
    {"name": "Senior Staff Software Engineer", "company": {"name": "Acme"},
     "locations": [{"name": "Austin, TX"}], "contents": "10+ years required.",
     "refs": {"landing_page": "https://www.themuse.com/x"}},
    {"name": "Car Wash Attendant", "company": {"name": "Chevron"},
     "locations": [{"name": "San Jose, CA"}], "contents": "Wash cars.",
     "refs": {"landing_page": "https://www.themuse.com/y"}},
]}
_muse_calls = {"n": 0}


def _fake_muse_get(url, **kw):
    _muse_calls["n"] += 1
    # First page returns rows; later pages empty so the pagination loop stops.
    return _FakeResp(_muse_payload if _muse_calls["n"] == 1 else {"results": []})


_orig_get = src._SESSION.get
src._SESSION.get = _fake_muse_get
try:
    _muse = src.themuse(new_grad_only=True, focus_keys=["general"], max_pages=3)
finally:
    src._SESSION.get = _orig_get
_muse_ok = [j for j in _muse if "_error" not in j]
check("themuse keeps entry-level SWE, drops senior + non-SWE roles",
      [j["title"] for j in _muse_ok] == ["Software Engineer, Backend"])
check("themuse normalizes company / location / link / source",
      _muse_ok and _muse_ok[0]["company"] == "Acme"
      and _muse_ok[0]["location"] == "Austin, TX"
      and _muse_ok[0]["source"] == "The Muse"
      and _muse_ok[0]["job_link"] == "https://www.themuse.com/jobs/acme/swe")

# 5d.2) Adzuna source: pagination + entry-level/focus gating (network stubbed)
_adz_calls = []


def _fake_adz_get(url, **kw):
    _adz_calls.append(url)
    if "/search/1" in url:
        return _FakeResp({"results": [
            {"title": "Entry Level Software Engineer", "company": {"display_name": "Acme"},
             "location": {"display_name": "Remote, US"}, "redirect_url": "https://adz/1",
             "description": "Build Flask REST APIs. 0-2 years.", "created": "2026-06-05T10:00:00Z"},
            {"title": "Senior Software Engineer", "company": {"display_name": "Acme"},
             "location": {"display_name": "Remote, US"}, "redirect_url": "https://adz/2",
             "description": "5+ years required.", "created": "2026-06-05T10:00:00Z"},
        ]})
    return _FakeResp({"results": [
        {"title": "Junior Software Developer", "company": {"display_name": "Beta"},
         "location": {"display_name": "Atlanta, GA"}, "redirect_url": "https://adz/3",
         "description": "JavaScript, SQL, and APIs. 0-1 years.", "created": "2026-06-05T10:00:00Z"},
        {"title": "QA Engineer", "company": {"display_name": "Beta"},
         "location": {"display_name": "Atlanta, GA"}, "redirect_url": "https://adz/4",
         "description": "Manual QA testing.", "created": "2026-06-05T10:00:00Z"},
    ]})


_orig_get_adz = src._SESSION.get
src._SESSION.get = _fake_adz_get
try:
    _adz = src.adzuna("entry level software engineer", "id", "key", pages=2, results=2,
                      new_grad_only=True, focus_keys=["newgrad"], max_years=2)
finally:
    src._SESSION.get = _orig_get_adz
_adz_ok = [j for j in _adz if "_error" not in j]
check("adzuna paginates and keeps entry-level/junior SWE roles only",
      len(_adz_calls) == 2 and [j["title"] for j in _adz_ok]
      == ["Entry Level Software Engineer", "Junior Software Developer"])
check("adzuna normalizes posted date, work mode, link, and source",
      _adz_ok[0]["posted_date"] == "2026-06-05" and _adz_ok[0]["work_mode"] == "Remote"
      and _adz_ok[0]["job_link"] == "https://adz/1" and _adz_ok[0]["source"] == "Adzuna")

# 5d.3) JSearch source: structured API normalization + quota-safe pagination
check("jsearch_available is False without key and True with direct key",
      not src.jsearch_available({"jsearch": {"enabled": True}})
      and src.jsearch_available({"jsearch": {"enabled": True, "api_key": "k"}}))
_js_calls = []


def _fake_jsearch_get(url, **kw):
    _js_calls.append({"url": url, "params": kw.get("params") or {},
                      "headers": kw.get("headers") or {}})
    if len(_js_calls) == 1:
        return _FakeResp({
            "cursor": "next-page",
            "data": [
                {"job_title": "Entry Level Software Engineer", "employer_name": "Acme",
                 "job_location": "Remote, US", "job_country": "US",
                 "job_description": "Build Flask REST APIs. 0-1 years.",
                 "job_posted_at_datetime_utc": "2026-06-05T10:00:00Z",
                 "apply_options": [
                     {"publisher": "LinkedIn", "apply_link": "https://linkedin.com/jobs/view/1",
                      "is_direct": False},
                     {"publisher": "Acme Careers", "apply_link": "https://acme.com/jobs/1",
                      "is_direct": True},
                 ]},
                {"job_title": "Senior Software Engineer", "employer_name": "Acme",
                 "job_location": "Remote, US", "job_country": "US",
                 "job_description": "5+ years required.",
                 "job_posted_at_datetime_utc": "2026-06-05T10:00:00Z",
                 "job_apply_link": "https://acme.com/jobs/2"},
                {"job_title": "Junior Software Developer", "employer_name": "OffshoreCo",
                 "job_location": "Kharagpur, India", "job_country": "IN",
                 "job_description": "0-1 years.",
                 "job_posted_at_datetime_utc": "2026-06-05T10:00:00Z",
                 "job_apply_link": "https://offshore.example/jobs/3"},
            ],
        })
    return _FakeResp({"data": [
        {"job_title": "Junior Software Developer", "employer_name": "Beta",
         "job_location": "Atlanta, GA", "job_country": "US",
         "job_description": "JavaScript, SQL, APIs. 0-1 years.",
         "job_posted_at": "2 hours ago",
         "job_apply_link": "https://beta.com/jobs/4"},
        {"job_title": "QA Engineer", "employer_name": "Beta",
         "job_location": "Atlanta, GA", "job_country": "US",
         "job_description": "Manual QA testing.",
         "job_posted_at": "2 hours ago",
         "job_apply_link": "https://beta.com/jobs/5"},
    ]})


_orig_get_js = src._SESSION.get
src._SESSION.get = _fake_jsearch_get
try:
    _js = src.jsearch("entry level software engineer in United States", api_key="key",
                      pages=2, new_grad_only=True, focus_keys=["newgrad"], max_years=1)
finally:
    src._SESSION.get = _orig_get_js
_js_ok = [j for j in _js if "_error" not in j]
check("jsearch paginates and keeps US entry-level SWE roles only",
      len(_js_calls) == 2 and [j["title"] for j in _js_ok]
      == ["Entry Level Software Engineer", "Junior Software Developer"])
check("jsearch sends direct OpenWeb Ninja auth + country/date params",
      _js_calls[0]["url"].endswith("/jsearch/search-v2")
      and _js_calls[0]["headers"].get("X-API-Key") == "key"
      and _js_calls[0]["params"].get("country") == "us"
      and _js_calls[0]["params"].get("date_posted") == "today"
      and _js_calls[1]["params"].get("cursor") == "next-page")
check("jsearch normalizes direct apply link, posted date, work mode, and source",
      _js_ok and _js_ok[0]["job_link"] == "https://acme.com/jobs/1"
      and _js_ok[0]["posted_date"] == "2026-06-05"
      and _js_ok[0]["work_mode"] == "Remote"
      and _js_ok[0]["source"] == "JSearch")

# 5d.4) SerpApi source: structured Google Jobs normalization (network stubbed)
check("serpapi_available is False without key and True with key",
      not src.serpapi_available({"serpapi": {"enabled": True}})
      and src.serpapi_available({"serpapi": {"enabled": True, "api_key": "s"}}))
_serp_calls = []


def _fake_serp_get(url, **kw):
    _serp_calls.append({"url": url, "params": kw.get("params") or {}})
    if len(_serp_calls) == 1:
        return _FakeResp({
            "serpapi_pagination": {"next_page_token": "tok2"},
            "jobs_results": [
                {"title": "Entry Level Software Engineer", "company_name": "Acme",
                 "location": "Austin, TX",
                 "description": "Build Python APIs. 0-1 years.",
                 "detected_extensions": {"posted_at": "2 hours ago"},
                 "apply_options": [
                     {"title": "LinkedIn", "link": "https://linkedin.com/jobs/view/1"},
                     {"title": "Acme Careers", "link": "https://acme.com/jobs/1"},
                 ],
                 "job_id": "s1"},
                {"title": "Senior Software Engineer", "company_name": "Acme",
                 "location": "Austin, TX", "description": "5+ years.",
                 "detected_extensions": {"posted_at": "2 hours ago"},
                 "apply_options": [{"title": "Acme", "link": "https://acme.com/jobs/2"}],
                 "job_id": "s2"},
            ],
        })
    return _FakeResp({"jobs_results": [
        {"title": "Junior Software Developer", "company_name": "Beta",
         "location": "Atlanta, GA", "description": "0-1 years. JavaScript APIs.",
         "extensions": ["2 hours ago", "Full-time"],
         "apply_options": [{"title": "Beta Careers", "link": "https://beta.com/jobs/3"}],
         "job_id": "s3"},
        {"title": "Junior Software Developer", "company_name": "OffshoreCo",
         "location": "Kharagpur, India", "description": "0-1 years.",
         "extensions": ["2 hours ago"],
         "apply_options": [{"title": "Offshore", "link": "https://offshore/jobs/4"}],
         "job_id": "s4"},
    ]})


_orig_get_serp = src._SESSION.get
src._SESSION.get = _fake_serp_get
try:
    _serp = src.serpapi_google_jobs("entry level software engineer in United States",
                                    api_key="serp", pages=2, focus_keys=["newgrad"],
                                    max_years=1, max_age_hours=24)
finally:
    src._SESSION.get = _orig_get_serp
_serp_ok = [j for j in _serp if "_error" not in j]
check("serpapi paginates and keeps US entry-level SWE roles only",
      len(_serp_calls) == 2 and [j["title"] for j in _serp_ok]
      == ["Entry Level Software Engineer", "Junior Software Developer"])
check("serpapi sends Google Jobs params + next_page_token",
      _serp_calls[0]["params"].get("engine") == "google_jobs"
      and _serp_calls[0]["params"].get("gl") == "us"
      and _serp_calls[1]["params"].get("next_page_token") == "tok2")
check("serpapi normalizes direct-looking apply link, posted date, work mode, and source",
      _serp_ok and _serp_ok[0]["job_link"] == "https://acme.com/jobs/1"
      and _serp_ok[0]["posted_date"]
      and _serp_ok[0]["work_mode"] == ""
      and _serp_ok[0]["source"] == "SerpApi Google Jobs")

# 5e) cross-source dedupe + URL ranking (pure, offline)
check("url_rank: employer/ATS > board > search > missing",
      src.url_rank("https://boards.greenhouse.io/stripe/jobs/9") == 3
      and src.url_rank("https://www.linkedin.com/jobs/view/9") == 2
      and src.url_rank("https://www.indeed.com/jobs?q=swe") == 1
      and src.url_rank("") == 0)
_dups = [
    {"title": "Software Engineer, New Grad", "company": "Stripe", "location": "SF",
     "source": "LinkedIn", "job_link": "https://www.linkedin.com/jobs/view/1", "description": "x"},
    {"title": "Software Engineer New Grad", "company": "Stripe", "location": "San Francisco",
     "source": "Greenhouse", "job_link": "https://boards.greenhouse.io/stripe/jobs/9",
     "description": "full jd"},
    {"title": "Backend Engineer", "company": "Datadog", "location": "NYC",
     "source": "Lever", "job_link": "https://jobs.lever.co/datadog/a", "description": "y"},
]
_merged = src.merge_duplicates(_dups)
check("merge_duplicates collapses same role across sources, keeps ATS link",
      len(_merged) == 2
      and next(j for j in _merged if j["company"] == "Stripe")["source"] == "Greenhouse")

# 5f) discovery (Track C) is key-gated + builds site-targeted queries
check("discovery_available is False without a provider/key", not src.discovery_available({}))
check("discovery_available True with tavily key",
      src.discovery_available({"discovery": {"provider": "tavily", "tavily_api_key": "k"}}))
_old_tavily = os.environ.get("TAVILY_API_KEY")
os.environ["TAVILY_API_KEY"] = "env-key"
try:
    check("discovery_available auto-detects TAVILY_API_KEY env var",
          src.discovery_available({"discovery": {}}))
finally:
    if _old_tavily is None:
        os.environ.pop("TAVILY_API_KEY", None)
    else:
        os.environ["TAVILY_API_KEY"] = _old_tavily
_dq = src.build_discovery_queries(focus_keys=["newgrad", "backend"], location="United States",
                                  h1b_only=True, source_labels=["LinkedIn Jobs", "Company careers"],
                                  variants=1)
check("build_discovery_queries emits site-targeted queries (no H1B clause — it zeroed yield)",
      len(_dq) == 6 and "site:linkedin.com/jobs" in _dq[0][1]
      and "new grad" in _dq[0][1].lower() and "H1B" not in _dq[0][1]
      and any("junior software developer" in q for _, q in _dq))
check("search_discovery returns [] with no key", src.search_discovery(_dq, {}) == [])
_orig_post_discovery = src._SESSION.post
_seen_tavily_payload = {}

def _fake_tavily_post(url, **kw):
    _seen_tavily_payload.update(kw.get("json") or {})
    return _FakeResp({"results": [
        {"title": "Software Engineer, New Grad", "url": "https://linkedin.com/jobs/view/old",
         "content": "This role closed on Jul 2025."},
        {"title": "Software Engineer, New Grad", "url": "https://linkedin.com/jobs/view/weekold",
         "content": "Backend role posted 7 days ago."},
        {"title": "Software Engineer, New Grad", "url": "https://linkedin.com/jobs/view/unknown",
         "content": "Backend role with no posting date shown."},
        {"title": "Stripe hiring Software Engineer, New Grad",
         "url": "https://linkedin.com/jobs/view/fresh", "content": "Backend role posted today."},
    ]})

src._SESSION.post = _fake_tavily_post
try:
    _fresh_leads = src.search_discovery(
        [("LinkedIn Jobs", "site:linkedin.com/jobs/view software engineer")],
        {"discovery": {"provider": "tavily", "tavily_api_key": "k"}},
        max_age_hours=3)
finally:
    src._SESSION.post = _orig_post_discovery
check("search_discovery passes time_range to Tavily WITHOUT start_date (Tavily 400s if both set)",
      _seen_tavily_payload.get("time_range") == "day" and not _seen_tavily_payload.get("start_date"))
check("search_discovery filters closed/stale discovery results",
      # "closed on Jul 2025" and "posted 7 days ago" are dropped; unknown-date lead
      # is KEPT (trust Tavily's time_range rather than vetoing unknown dates); "today" kept.
      len(_fresh_leads) == 2
      and any("fresh" in l["discovery_url"] for l in _fresh_leads)
      and not any("weekold" in l["discovery_url"] for l in _fresh_leads))
check("search_discovery carries inferred posted_date for discovery leads",
      any(l.get("posted_date") for l in _fresh_leads))
_lead = {"source": "LinkedIn", "title_guess": "Software Engineer, New Grad",
         "discovery_url": "https://www.linkedin.com/jobs/view/55", "snippet": "Backend role"}
_rl = src.resolve_lead(_lead)
check("resolve_lead flags non-ATS board link for verification",
      _rl["link_warning"] is True and _rl["source"] == "LinkedIn")
check("resolve_lead drops a senior-titled lead",
      src.resolve_lead({"title_guess": "Senior Staff Engineer",
                        "discovery_url": "https://x.com/job/1"}) is None)
check("resolve_lead drops a NON-SWE discovery lead (role-family gate)",
      src.resolve_lead({"title_guess": "Semiconductor Quality Assurance Engineer",
                        "discovery_url": "https://www.indeed.com/viewjob?jk=1"},
                       focus_keys=["newgrad", "backend"]) is None
      and src.resolve_lead({"title_guess": "Business Analyst",
                            "discovery_url": "https://x.com/2"}, focus_keys=["general"]) is None)
check("resolve_lead keeps a real SWE discovery lead",
      src.resolve_lead({"title_guess": "Software Engineer, New Grad",
                        "discovery_url": "https://job-boards.greenhouse.io/acme/jobs/9"},
                       focus_keys=["newgrad", "backend"]) is not None)
# resolve_lead drops search/aggregation landing pages (not single postings)
check("resolve_lead drops aggregation landing pages ('N jobs in …', 'Best … Jobs in …')",
      src.resolve_lead({"title_guess": "37 New Grad Software Engineer jobs in United States",
                        "discovery_url": "https://www.linkedin.com/jobs/visa-new-grad-jobs"}) is None
      and src.resolve_lead({"title_guess": "Best Entry Level Software Engineer Jobs in California",
                            "discovery_url": "https://x.com/jobs"}) is None
      and src.resolve_lead({"title_guess": "Entry Level Software Engineer jobs in New York",
                            "discovery_url": "https://x.com/jobs"}) is None)
# lead_company extraction from common title shapes + ATS URL slug
check("lead_company parses 'Company hiring …'",
      src.lead_company("Notion hiring Software Engineer, New Grad in SF", "https://lnkd.in/x") == "Notion")
check("lead_company parses '… at Company'",
      src.lead_company("Job Application for Software Engineer at Sigma Computing",
                       "https://x") == "Sigma Computing")
check("lead_company parses 'Title - Company - Lever' and ignores the site suffix",
      src.lead_company("Software Engineer (New Grad) - Peak - Lever",
                       "https://jobs.lever.co/peak/abc") == "Peak")
check("lead_company falls back to the employer slug in an ATS URL",
      src.lead_company("Software Engineering, New Grad",
                       "https://job-boards.greenhouse.io/stripe/jobs/7176977") == "Stripe")
check("lead_company rejects a location/role fragment as the company",
      src.lead_company("Software Engineer - San Francisco, CA", "https://x") == "")
_rl2 = src.resolve_lead({"source": "Dice", "title_guess": "Scale AI hiring Software Engineer - New Grad",
                         "discovery_url": "https://dice.com/job-detail/1"})
check("resolve_lead extracts the company + marks from_discovery",
      _rl2["company"] == "Scale AI" and _rl2["from_discovery"] is True)

# 5f-2) Discovery V2: ats_ref_from_url + full-JD enrichment from the public ATS API
check("ats_ref_from_url parses Greenhouse / Lever / Ashby, ignores third-party",
      src.ats_ref_from_url("https://job-boards.greenhouse.io/stripe/jobs/7176977") == ("greenhouse", "stripe")
      and src.ats_ref_from_url("https://jobs.lever.co/plaid/abc-123") == ("lever", "plaid")
      and src.ats_ref_from_url("https://jobs.ashbyhq.com/notion/xyz") == ("ashby", "notion")
      and src.ats_ref_from_url("https://www.linkedin.com/jobs/view/999") is None)
check("ats_ref_from_url parses Workday tenant|wdN|site + SmartRecruiters company id",
      src.ats_ref_from_url("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/X_JR1")
      == ("workday", "nvidia|wd5|NVIDIAExternalCareerSite")
      and src.ats_ref_from_url("https://visa.wd5.myworkdayjobs.com/Visa_Early_Careers/job/Y_REF2")
      == ("workday", "visa|wd5|Visa_Early_Careers")
      and src.ats_ref_from_url("https://jobs.smartrecruiters.com/Mastercard/743-swe") == ("smartrecruiters", "Mastercard"))
# A discovery lead pointing at a Greenhouse board gets upgraded to the FULL JD; a
# LinkedIn-only lead is flagged needs_verification and keeps its snippet.
_gh_board = {"jobs": [
    {"title": "Software Engineer, New Grad", "content": "<p>Build backend services in Python and Java.</p>",
     "first_published": "2026-06-01", "location": {"name": "Remote, US"},
     "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/123"},
]}
_disc_jobs = [
    {"title": "Software Engineer, New Grad", "company": "Acme", "job_link": "https://boards.greenhouse.io/acme/jobs/123",
     "source": "Company careers", "description": "snippet only…", "from_discovery": True, "link_warning": True},
    {"title": "Backend Engineer", "company": "Stripe", "job_link": "https://www.linkedin.com/jobs/view/55",
     "source": "LinkedIn Jobs", "description": "5 minutes ago…", "from_discovery": True, "link_warning": True},
]
_orig_get_enrich = src._SESSION.get
src._SESSION.get = lambda url, **kw: _FakeResp(_gh_board)
try:
    _enr_jobs, _enr_counts = src.enrich_discovery_jobs(_disc_jobs, focus_keys=["newgrad", "backend"])
finally:
    src._SESSION.get = _orig_get_enrich
_gh_job = next(j for j in _enr_jobs if j["company"].lower().startswith("acme"))
_li_job = next(j for j in _enr_jobs if j["source"] == "LinkedIn Jobs")
check("enrich_discovery_jobs upgrades an official-ATS lead to the full JD",
      "Python and Java" in _gh_job["description"] and _gh_job["jd_source"] == "ats"
      and _gh_job["link_warning"] is False and _gh_job["needs_verification"] is False
      and _enr_counts["official"] == 1 and _enr_counts["enriched"] == 1)
check("enrich_discovery_jobs flags a third-party-only lead as needs_verification",
      _li_job["needs_verification"] is True and _li_job["jd_source"] == "snippet"
      and _enr_counts["third_party"] == 1)
# build_discovery_queries emits compact UNQUOTED title phrases (quoted ("a" OR "b")
# role clauses collapse Tavily to ~1 result; plain terms keep full recall) and emits
# `variants` tails per title/site to widen recall.
_dqv1 = src.build_discovery_queries(focus_keys=["newgrad", "backend"],
                                    source_labels=["LinkedIn Jobs"], variants=1)
_dqv_q = _dqv1[0][1]
check("build_discovery_queries emits unquoted entry-level title phrases (variants=1)",
      len(_dqv1) == 3 and "site:linkedin.com/jobs" in _dqv_q
      and '"software engineer"' not in _dqv_q and " OR software" not in _dqv_q
      and "new grad software engineer" in _dqv_q
      and any("junior software developer" in q for _, q in _dqv1))
_dqv2 = src.build_discovery_queries(focus_keys=["newgrad", "backend"],
                                    source_labels=["LinkedIn Jobs"], variants=2)
check("build_discovery_queries emits 2 tails per title phrase with distinct tails",
      len(_dqv2) == 6 and all("site:linkedin.com/jobs" in q for _, q in _dqv2)
      and _dqv2[0][1] != _dqv2[1][1]
      and "new grad" in _dqv2[0][1].lower() and "entry level" in _dqv2[1][1].lower())

# 5g) Workday CXS connector: list -> detail -> normalized job (network stubbed)
_wd_list = {"jobPostings": [
    {"title": "Software Engineer, New Grad", "externalPath": "/job/US-CA/SWE_JR1"},
    {"title": "Senior Staff Software Engineer", "externalPath": "/job/US-CA/Sr_JR2"},
]}
_wd_detail = {"jobPostingInfo": {
    "title": "Software Engineer, New Grad", "location": "US, CA, Santa Clara",
    "startDate": "", "jobDescription": "<p>Build backend services in Python.</p>",
    "externalUrl": "https://acme.wd5.myworkdayjobs.com/Careers/job/US-CA/SWE_JR1"}}
_orig_post, _orig_get2 = src._SESSION.post, src._SESSION.get
src._SESSION.post = lambda url, **kw: _FakeResp(_wd_list)
src._SESSION.get = lambda url, **kw: _FakeResp(_wd_detail)
try:
    _wd = src.workday("acme|wd5|Careers", new_grad_only=True, focus_keys=["general"])
finally:
    src._SESSION.post, src._SESSION.get = _orig_post, _orig_get2
_wd_ok = [j for j in _wd if "_error" not in j]
check("workday keeps entry-level SWE, drops senior, uses canonical externalUrl + Workday source",
      len(_wd_ok) == 1 and _wd_ok[0]["source"] == "Workday"
      and _wd_ok[0]["job_link"].endswith("SWE_JR1")
      and "Python" in _wd_ok[0]["description"])
check("workday rejects a malformed token",
      any("_error" in j for j in src.workday("badtoken")))

# 5g-2) SerpApi Google Jobs + JSearch normalization (network stubbed)
_serp_payload = {"jobs_results": [
    {"title": "Entry Level Software Engineer", "company_name": "Acme",
     "location": "Austin, TX", "description": "Build backend services in Python. 0-2 years.",
     "detected_extensions": {"posted_at": "2 days ago"},
     "apply_options": [{"title": "Greenhouse", "link": "https://job-boards.greenhouse.io/acme/jobs/1"}]},
    {"title": "Senior Software Engineer", "company_name": "Acme", "location": "Austin, TX",
     "description": "10+ years required.", "detected_extensions": {"posted_at": "1 day ago"},
     "apply_options": [{"title": "x", "link": "https://x/2"}]},
    {"title": "Software Engineer", "company_name": "Foo", "location": "Berlin, Germany",
     "description": "Entry level role.", "detected_extensions": {"posted_at": "1 day ago"},
     "apply_options": [{"title": "x", "link": "https://x/3"}]},
]}
_orig_get_sp = src._SESSION.get
src._SESSION.get = lambda url, **kw: _FakeResp(_serp_payload)
try:
    _serp = [j for j in src.serpapi_google_jobs("swe", api_key="k", focus_keys=["general"]) if "_error" not in j]
finally:
    src._SESSION.get = _orig_get_sp
check("serpapi_google_jobs normalizes, drops senior + foreign, prefers non-aggregator link",
      len(_serp) == 1 and _serp[0]["company"] == "Acme" and _serp[0]["source"] == "SerpApi Google Jobs"
      and _serp[0]["jd_source"] == "api" and "greenhouse" in _serp[0]["job_link"]
      and "Python" in _serp[0]["description"])

_js_payload = {"data": [
    {"job_title": "Entry Level Software Engineer", "employer_name": "Beta",
     "job_country": "US", "job_location": "Remote, US",
     "job_description": "Python backend. New grad welcome. 0-2 years.",
     "job_posted_at_datetime_utc": "2026-06-04T00:00:00Z",
     "job_apply_link": "https://beta.com/apply/1"},
    {"job_title": "Software Engineer", "employer_name": "Gamma", "job_country": "IN",
     "job_location": "Bangalore", "job_description": "Entry level.",
     "job_posted_at_datetime_utc": "2026-06-04T00:00:00Z", "job_apply_link": "https://g/2"},
]}
src._SESSION.get = lambda url, **kw: _FakeResp(_js_payload)
try:
    _js = [j for j in src.jsearch("swe", api_key="k", focus_keys=["general"]) if "_error" not in j]
finally:
    src._SESSION.get = _orig_get_sp
check("jsearch normalizes + drops non-US (job_country), tags jd_source=api",
      len(_js) == 1 and _js[0]["company"] == "Beta" and _js[0]["source"] == "JSearch"
      and _js[0]["jd_source"] == "api" and _js[0]["job_link"].endswith("/apply/1"))
check("_serpapi_date_chips maps the freshness window server-side",
      src._serpapi_date_chips(24) == "today" and src._serpapi_date_chips(168) == "week"
      and src._serpapi_date_chips(None) == "")

# 5g-3) Free remote-job APIs (Remotive + RemoteOK) normalization (network stubbed)
_remotive_payload = {"jobs": [
    {"title": "Entry Level Software Engineer", "company_name": "RemoteCo",
     "url": "https://remotive.com/j/1", "candidate_required_location": "USA Only",
     "publication_date": "2026-06-04", "description": "Build backend services in Python. 0-2 years."},
    {"title": "Senior Software Engineer", "company_name": "X", "url": "https://r/2",
     "candidate_required_location": "Worldwide", "publication_date": "2026-06-04",
     "description": "10+ years."},
]}
_orig_get_rm = src._SESSION.get
src._SESSION.get = lambda url, **kw: _FakeResp(_remotive_payload)
try:
    _rmv = [j for j in src.remotive(focus_keys=["general"]) if "_error" not in j]
finally:
    src._SESSION.get = _orig_get_rm
check("remotive normalizes + drops senior, tags Remote + jd_source=api",
      len(_rmv) == 1 and _rmv[0]["company"] == "RemoteCo" and _rmv[0]["source"] == "Remotive"
      and _rmv[0]["work_mode"] == "Remote" and _rmv[0]["jd_source"] == "api")

_remoteok_payload = [
    {"legal": "RemoteOK legal notice — no 'position' field, must be skipped"},
    {"position": "Junior Software Engineer", "company": "OkCo", "url": "https://remoteok.com/j/1",
     "location": "United States", "date": "2026-06-04",
     "description": "Python backend. New grad welcome. 0-2 years."},
    {"position": "Marketing Manager", "company": "Y", "url": "https://r/2",
     "location": "Remote", "date": "2026-06-04", "description": "Non-SWE role."},
]
src._SESSION.get = lambda url, **kw: _FakeResp(_remoteok_payload)
try:
    _rok = [j for j in src.remoteok(focus_keys=["general"]) if "_error" not in j]
finally:
    src._SESSION.get = _orig_get_rm
check("remoteok skips the legal-notice header + non-SWE, keeps entry-level SWE",
      len(_rok) == 1 and _rok[0]["company"] == "OkCo" and _rok[0]["source"] == "RemoteOK"
      and _rok[0]["jd_source"] == "api")

# 5g-4) Careerjet + Jooble normalization (network stubbed); dormant without a key.
check("careerjet/jooble availability is False without a key",
      not src.careerjet_available({}) and not src.jooble_available({}))
_cj_payload = {"jobs": [
    {"title": "Entry Level Software Engineer", "company": "CjCo", "locations": "Austin, TX",
     "url": "https://careerjet.com/j/1", "date": "2026-06-05",
     "description": "Python backend. New grad. 0-2 years."},
    {"title": "Senior Software Engineer", "company": "X", "locations": "Austin, TX",
     "url": "https://c/2", "date": "2026-06-05", "description": "10+ years."}]}
_orig_get_cj = src._SESSION.get
src._SESSION.get = lambda url, **kw: _FakeResp(_cj_payload)
try:
    _cj = [j for j in src.careerjet("swe", affid="x", focus_keys=["general"]) if "_error" not in j]
finally:
    src._SESSION.get = _orig_get_cj
check("careerjet normalizes + drops senior, tags jd_source=api",
      len(_cj) == 1 and _cj[0]["company"] == "CjCo" and _cj[0]["source"] == "Careerjet"
      and _cj[0]["jd_source"] == "api")
_jb_payload = {"jobs": [
    {"title": "Junior Software Engineer", "company": "JbCo", "location": "Remote, US",
     "link": "https://jooble.org/j/1", "updated": "2026-06-05",
     "snippet": "Python backend. Entry level. 0-2 years."}]}
_orig_post_jb = src._SESSION.post
src._SESSION.post = lambda url, **kw: _FakeResp(_jb_payload)
try:
    _jb = [j for j in src.jooble("swe", api_key="x", focus_keys=["general"]) if "_error" not in j]
finally:
    src._SESSION.post = _orig_post_jb
check("jooble normalizes (POST), keeps entry-level SWE, tags jd_source=api",
      len(_jb) == 1 and _jb[0]["company"] == "JbCo" and _jb[0]["source"] == "Jooble"
      and _jb[0]["jd_source"] == "api")

# 5h) source catalog loads + aggregator helpers
_cat = agg.load_catalog(os.path.join(BASE, "config", "source_catalog.yaml"))
check("source_catalog.yaml loads named sources (direct + ATS + discovery)",
      len(_cat) >= 18 and "greenhouse" in _cat and "workday" in _cat
      and "linkedin" in _cat and "glassdoor" in _cat
      and "lensa" not in _cat and "myvisajobs" not in _cat)   # zero-yield sites removed
check("catalog groups present in display order",
      agg.groups_in_catalog(_cat) == ["direct_api", "ats_discovery", "discovery"])
check("discovery_labels only returns discovery-mode sources of enabled groups",
      "LinkedIn Jobs" in agg.discovery_labels(_cat, {"discovery"})
      and "Greenhouse" not in agg.discovery_labels(_cat, {"discovery", "direct_api"}))

# 5i) aggregator.search_all_sources: normalized schema + source counts + dedupe (stubbed)
_orig = {n: getattr(src, n) for n in ("load_targets", "pull_targets_verbose", "themuse",
                                      "discovery_available")}
src.load_targets = lambda p: [{"ats": "greenhouse", "token": "stripe", "name": "Stripe"},
                              {"ats": "workday", "token": "acme|wd5|Careers", "name": "Acme"}]
src.pull_targets_verbose = lambda subset, **kw: (
    ([{"title": "Software Engineer", "company": subset[0]["name"], "location": "Austin, TX",
       "source": "Greenhouse" if subset[0]["ats"] == "greenhouse" else "Workday",
       "job_link": f"https://x/{subset[0]['token']}", "description": "d"}], [])
    if subset else ([], []))
src.themuse = lambda **kw: [{"title": "Backend Engineer", "company": "MuseCo", "location": "NY",
                             "source": "The Muse", "job_link": "https://m/1", "description": "d"}]
src.discovery_available = lambda cfg: False
try:
    _res = agg.search_all_sources(
        {"paths": {"target_companies": "x", "source_catalog": "y"}, "adzuna": {}},
        {"focus_keys": ["general"], "groups": ["direct_api", "ats_discovery"], "catalog": _cat})
finally:
    for n, fn in _orig.items():
        setattr(src, n, fn)
check("aggregator returns jobs in the normalized schema",
      _res["jobs"] and all(set(("title", "company", "location", "source", "job_link",
                                "description")).issubset(j) for j in _res["jobs"]))
check("aggregator reports per-source counts (boards + The Muse + Workday)",
      _res["counts"].get("the_muse") == 1 and _res["counts"].get("workday") == 1
      and _res["counts"].get("boards", 0) >= 1)
check("aggregator marks discovery disabled when no key", _res["discovery_enabled"] is False)

# 5j) search_selected_sources — per-source precision (all stubbed, no network)
_orig2 = {n: getattr(src, n) for n in ("load_targets", "pull_targets_verbose", "themuse",
                                        "adzuna", "jsearch", "jsearch_available",
                                        "serpapi_available", "serpapi_google_jobs",
                                        "discovery_available", "search_discovery")}
_calls2 = {"boards": 0, "workday": 0, "sr": 0, "muse": 0, "disc": 0}

def _fake_pull_verbose2(subset, **kw):
    ats = (subset[0].get("ats") or "").lower() if subset else ""
    if ats in ("greenhouse", "lever", "ashby"):
        _calls2["boards"] += 1
        return ([{"title": "SWE", "company": "A", "location": "US",
                  "source": "Greenhouse", "job_link": "https://g/1", "description": "d"}], [])
    if ats == "workday":
        _calls2["workday"] += 1
        return ([{"title": "SWE WD", "company": "B", "location": "US",
                  "source": "Workday", "job_link": "https://wd/1", "description": "d"}], [])
    if ats == "smartrecruiters":
        _calls2["sr"] += 1
        return ([{"title": "SWE SR", "company": "C", "location": "US",
                  "source": "SmartRecruiters", "job_link": "https://sr/1", "description": "d"}], [])
    return ([], [])

src.load_targets = lambda p: [
    {"ats": "greenhouse", "token": "stripe", "name": "Stripe"},
    {"ats": "workday",    "token": "acme|wd5|C", "name": "Acme"},
    {"ats": "smartrecruiters", "token": "Visa", "name": "Visa"},
]
src.pull_targets_verbose = _fake_pull_verbose2
src.themuse = lambda **kw: [{"title": "Muse", "company": "M", "location": "NY",
                              "source": "The Muse", "job_link": "https://m/1", "description": "d"}]
src.adzuna = lambda q, ai, ak, **kw: [{"title": "Adz", "company": "Z", "location": "US",
                                        "source": "Adzuna", "job_link": "https://a/1", "description": "d"}]
_calls2["jsearch"] = 0
_calls2["serpapi"] = 0
def _fake_jsearch2(q, **kw):
    _calls2["jsearch"] += 1
    return [{"title": "JS", "company": "J", "location": "US",
             "source": "JSearch", "job_link": "https://j/1", "description": q}]

def _fake_serp2(q, **kw):
    _calls2["serpapi"] += 1
    return [{"title": "SG", "company": "S", "location": "US",
             "source": "SerpApi Google Jobs", "job_link": "https://s/1", "description": q}]

src.jsearch = _fake_jsearch2
src.jsearch_available = lambda cfg, provider="": bool((cfg.get("jsearch") or {}).get("api_key"))
src.serpapi_available = lambda cfg: bool((cfg.get("serpapi") or {}).get("api_key"))
src.serpapi_google_jobs = _fake_serp2
src.discovery_available = lambda cfg: True
src.search_discovery = lambda queries, cfg, **kw: (
    [{"source": q[0], "title_guess": f"Software Engineer ({q[0]})", "discovery_url": f"https://x/{i}",
      "snippet": "s", "confidence": "lead_only"} for i, q in enumerate(queries)])

_cfg2 = {"paths": {"target_companies": "x"}, "adzuna": {"app_id": "id", "app_key": "k"},
         "serpapi": {"api_key": "serp", "pages": 1},
         "jsearch": {"api_key": "key", "max_queries_per_run": 1, "pages": 1},
         "job_api_fallback": {"provider_order": ["serpapi", "jsearch_openweb"],
                              "max_queries_per_run": 1}}
_flt2 = {"focus_keys": ["general"], "new_grad_only": False, "max_years": 5, "location": "US"}

try:
    # Test 1: only sponsor boards — no Workday, no Muse, no discovery
    _calls2.update({"boards": 0, "workday": 0, "sr": 0, "muse": 0})
    _r1 = agg.search_selected_sources(_cfg2, _flt2,
                                       {"sponsor_boards": True, "themuse": False,
                                        "adzuna": False, "workday": False,
                                        "smartrecruiters": False, "discovery_labels": []})
    check("search_selected_sources: sponsor_boards only → no Workday / Muse / discovery",
          _calls2["boards"] >= 1 and _calls2["workday"] == 0
          and all(j["source"] == "Greenhouse" for j in _r1["jobs"]))

    # Test 2: only Workday — no boards, no Muse
    _calls2.update({"boards": 0, "workday": 0})
    _r2 = agg.search_selected_sources(_cfg2, _flt2,
                                       {"sponsor_boards": False, "themuse": False,
                                        "adzuna": False, "workday": True,
                                        "smartrecruiters": False, "discovery_labels": []})
    check("search_selected_sources: workday only → no sponsor boards",
          _calls2["workday"] >= 1 and _calls2["boards"] == 0
          and all(j["source"] == "Workday" for j in _r2["jobs"]))

    # Test 3: sponsor_boards + specific discovery labels — only those labels queried
    _r3 = agg.search_selected_sources(_cfg2, _flt2,
                                       {"sponsor_boards": True, "themuse": False,
                                        "adzuna": False, "workday": False,
                                        "smartrecruiters": False,
                                        "discovery_labels": ["LinkedIn Jobs", "Dice"]})
    _r3_sources = {j["source"] for j in _r3["jobs"]}
    check("search_selected_sources: boards + LinkedIn + Dice only → discovery limited to those 2",
          "Greenhouse" in _r3_sources
          and "LinkedIn Jobs" in _r3_sources or "Dice" in _r3_sources
          and "Workday" not in _r3_sources)

    # Test 3b: selected recency is passed through exactly; no hidden 14-day widening.
    _seen_disc_kw = {}
    src.search_discovery = lambda queries, cfg, **kw: (
        _seen_disc_kw.update(kw) or
        [{"source": q[0], "title_guess": f"Software Engineer ({q[0]})",
          "discovery_url": f"https://x/recent/{i}", "snippet": "posted 2 hours ago",
          "confidence": "lead_only"} for i, q in enumerate(queries)])
    _flt_recent = dict(_flt2, max_age_hours=3)
    agg.search_selected_sources(_cfg2, _flt_recent,
                                {"sponsor_boards": False, "themuse": False,
                                 "adzuna": False, "workday": False,
                                 "smartrecruiters": False,
                                 "discovery_labels": ["LinkedIn Jobs"]})
    check("search_selected_sources passes Past 3 hours to discovery as 3 hours",
          _seen_disc_kw.get("max_age_hours") == 3)

    # Test 4: Job API fallback only → SerpApi wins first; JSearch is not spent
    _calls2.update({"boards": 0, "workday": 0, "sr": 0, "serpapi": 0, "jsearch": 0})
    _r_js = agg.search_selected_sources(_cfg2, _flt2,
                                        {"sponsor_boards": False, "themuse": False,
                                         "adzuna": False, "jsearch": True,
                                         "workday": False, "smartrecruiters": False,
                                         "discovery_labels": []})
    check("search_selected_sources: Job API fallback uses only first successful provider",
          _r_js["counts"].get("jsearch") == 1
          and _r_js["counts"].get("job_api_provider") == "SerpApi Google Jobs"
          and _calls2["serpapi"] == 1 and _calls2["jsearch"] == 0
          and _calls2["boards"] == 0
          and _calls2["workday"] == 0
          and all(j["source"] == "SerpApi Google Jobs" for j in _r_js["jobs"]))

    # Test 4b: SerpApi error falls through to OpenWeb JSearch, still one fallback chain
    src.serpapi_google_jobs = lambda q, **kw: [{"_error": "SerpApi: quota exhausted"}]
    _calls2.update({"jsearch": 0})
    _r_fall = agg.search_selected_sources(_cfg2, _flt2,
                                          {"sponsor_boards": False, "themuse": False,
                                           "adzuna": False, "jsearch": True,
                                           "workday": False, "smartrecruiters": False,
                                           "discovery_labels": []})
    check("search_selected_sources: SerpApi error falls through to JSearch",
          _r_fall["counts"].get("job_api_provider") == "OpenWeb Ninja JSearch"
          and _calls2["jsearch"] == 1
          and all(j["source"] == "JSearch" for j in _r_fall["jobs"]))

    # Test 5: nothing selected → empty jobs, no errors raised
    _r4 = agg.search_selected_sources(_cfg2, _flt2,
                                       {"sponsor_boards": False, "themuse": False,
                                        "adzuna": False, "workday": False,
                                        "smartrecruiters": False, "discovery_labels": []})
    check("search_selected_sources: nothing selected → 0 jobs fetched", _r4["counts"]["fetched"] == 0)

    # Test 6: all_discovery_labels returns only discovery-mode sources
    _disc_all = agg.all_discovery_labels(_cat)
    check("all_discovery_labels includes LinkedIn but not Greenhouse/Workday",
          "LinkedIn Jobs" in _disc_all and "Greenhouse" not in _disc_all
          and "Workday" not in _disc_all)

    # Test 7: Workday + SmartRecruiters independently toggleable
    _calls2.update({"workday": 0, "sr": 0})
    agg.search_selected_sources(_cfg2, _flt2,
                                 {"sponsor_boards": False, "themuse": False, "adzuna": False,
                                  "workday": True, "smartrecruiters": True, "discovery_labels": []})
    check("search_selected_sources: Workday + SmartRecruiters independently toggleable",
          _calls2["workday"] >= 1 and _calls2["sr"] >= 1)
finally:
    for n, fn in _orig2.items():
        setattr(src, n, fn)

# 6c) README test-count claim stays in sync with the actual suite
_readme = open(os.path.join(BASE, "README.md"), encoding="utf-8").read()
check("README states the correct test count",
      f"{len(results) + 1}/{len(results) + 1}" in _readme)

# summary
passed = sum(1 for ok, _ in results if ok)
print(f"\n{passed}/{len(results)} checks passed")
sys.exit(0 if passed == len(results) else 1)
