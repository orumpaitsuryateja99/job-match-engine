"""
pdf_gen.py — render a tailored profile to a clean, professional, ATS-friendly PDF.

Layout mirrors a strong one-page SWE résumé:
  * centered name + clickable contact links (email, LinkedIn, GitHub, Portfolio)
  * section headings with rules
  * two-column entry rows: title/company on the LEFT, dates/location RIGHT-aligned
  * justified summary, grouped skill categories, inline [Live]/[GitHub] project links
Text stays selectable (no images) and bullets are plain paragraphs so ATS parsing
remains reliable.
"""
import os
import re

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_RIGHT
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                HRFlowable, KeepTogether, Table, TableStyle)
from reportlab.lib.colors import HexColor

ACCENT = "#11243f"
LINK = "#005aaa"
LMARGIN = RMARGIN = 0.55 * inch
USABLE_W = letter[0] - LMARGIN - RMARGIN     # printable width in points
RIGHT_W = 150                                 # width of the right-aligned column


def _styles():
    base = ParagraphStyle("Base", fontName="Helvetica", fontSize=8.9, leading=10.6,
                          textColor=HexColor("#111111"), spaceAfter=0)
    name = ParagraphStyle("Name", parent=base, fontName="Helvetica-Bold", fontSize=16.5,
                          leading=18, spaceAfter=1, alignment=TA_CENTER,
                          textColor=HexColor(ACCENT))
    contact = ParagraphStyle("Contact", parent=base, fontSize=8.2, leading=10,
                             alignment=TA_CENTER, textColor=HexColor("#333333"))
    section = ParagraphStyle("Section", parent=base, fontName="Helvetica-Bold",
                             fontSize=10.2, leading=11.6, spaceBefore=6, spaceAfter=1,
                             textColor=HexColor(ACCENT))
    body = ParagraphStyle("Body", parent=base)
    summary = ParagraphStyle("Summary", parent=base, alignment=TA_JUSTIFY)
    small = ParagraphStyle("Small", parent=base, fontSize=8.5, leading=10.6)
    title_l = ParagraphStyle("TitleL", parent=base, fontSize=9.3, leading=11)
    date_r = ParagraphStyle("DateR", parent=base, fontSize=8.5, leading=11,
                            alignment=TA_RIGHT, textColor=HexColor("#444444"))
    italic_l = ParagraphStyle("ItalicL", parent=base, fontName="Helvetica-Oblique",
                              fontSize=8.6, leading=10.4)
    italic_r = ParagraphStyle("ItalicR", parent=base, fontName="Helvetica-Oblique",
                              fontSize=8.6, leading=10.4, alignment=TA_RIGHT,
                              textColor=HexColor("#444444"))
    bullet = ParagraphStyle("Bullet", parent=base, leftIndent=11, firstLineIndent=-7,
                            bulletIndent=0, spaceAfter=1.0, alignment=TA_JUSTIFY)
    return dict(name=name, contact=contact, section=section, body=body, summary=summary,
                small=small, title_l=title_l, date_r=date_r, italic_l=italic_l,
                italic_r=italic_r, bullet=bullet)


def _esc(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def safe_filename(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")


def _url(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if value.startswith(("http://", "https://", "mailto:")):
        return value
    return "https://" + value


def _link(url: str, label: str) -> str:
    """Clickable, blue link markup."""
    if not url:
        return ""
    return f'<link href="{_esc(_url(url))}"><font color="{LINK}">{_esc(label)}</font></link>'


def _tag(url: str, label: str) -> str:
    """Bracketed inline link like [Live] / [GitHub]."""
    return f'<link href="{_esc(_url(url))}"><font color="{LINK}">[{_esc(label)}]</font></link>' if url else ""


def _add_section(flow, styles, title):
    flow.append(Paragraph(title.upper(), styles["section"]))
    flow.append(HRFlowable(width="100%", thickness=0.8, color=HexColor(ACCENT),
                           spaceBefore=0, spaceAfter=3))


def _row(left_para, right_para):
    """A borderless two-column row: left content, right-aligned content."""
    t = Table([[left_para, right_para]], colWidths=[USABLE_W - RIGHT_W, RIGHT_W])
    t.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return t


def generate_resume_pdf(profile: dict, out_path: str, accent: str = ACCENT) -> str:
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    st = _styles()
    doc = SimpleDocTemplate(out_path, pagesize=letter,
                            leftMargin=LMARGIN, rightMargin=RMARGIN,
                            topMargin=0.42 * inch, bottomMargin=0.38 * inch,
                            title=f"{profile.get('name','Resume')} — Resume")
    flow = []

    # ---------------- Header: name + clickable contact links ----------------
    flow.append(Paragraph(_esc(profile.get("name", "Candidate")), st["name"]))
    links = profile.get("links", {}) or {}
    bits = []
    if profile.get("email"):
        bits.append(_link("mailto:" + profile["email"], profile["email"]))
    if profile.get("phone"):
        bits.append(_esc(profile["phone"]))
    if links.get("linkedin"):
        bits.append(_link(links["linkedin"], links["linkedin"].replace("https://", "").replace("http://", "")))
    if links.get("github"):
        bits.append(_link(links["github"], links["github"].replace("https://", "").replace("http://", "")))
    if links.get("portfolio"):
        bits.append(_link(links["portfolio"], "Portfolio"))
    if bits:
        flow.append(Paragraph("  |  ".join(bits), st["contact"]))
    flow.append(Spacer(1, 3))
    flow.append(HRFlowable(width="100%", thickness=1.0, color=HexColor(accent),
                           spaceBefore=0, spaceAfter=2))

    # ---------------- Summary (justified) ----------------
    if profile.get("summary"):
        _add_section(flow, st, "Professional Summary")
        flow.append(Paragraph(_esc(profile["summary"]), st["summary"]))

    # ---------------- Technical Skills (grouped categories) ----------------
    skill_cats = profile.get("skill_categories") or []
    skills = profile.get("skills", [])
    phrases = profile.get("skill_phrases") or []
    if skill_cats:
        _add_section(flow, st, "Technical Skills")
        for cat in skill_cats:
            items = ", ".join(i for i in cat.get("items", []) if i)
            if items:
                label = f"<b>{_esc(cat.get('category',''))}:</b> " if cat.get("category") else ""
                flow.append(Paragraph(f"{label}{_esc(items)}", st["small"]))
    elif phrases:
        _add_section(flow, st, "Technical Skills")
        flow.append(Paragraph(_esc(", ".join(phrases[:40])), st["small"]))
    elif skills:
        _add_section(flow, st, "Technical Skills")
        flow.append(Paragraph(_esc(", ".join(_pretty(s) for s in skills[:34])), st["small"]))

    # ---------------- Work Experience ----------------
    exp = profile.get("experience", [])
    if exp:
        _add_section(flow, st, "Work Experience")
        for i, e in enumerate(exp):
            if i:
                flow.append(Spacer(1, 4))
            block = []
            role = e.get("role") or "Experience"
            elinks = e.get("links") or {}
            tags = " ".join(x for x in [_tag(elinks.get("live"), "Live"),
                                        _tag(elinks.get("github"), "GitHub")] if x)
            left = f"<b>{_esc(role)}</b>" + (f"  {tags}" if tags else "")
            block.append(_row(Paragraph(left, st["title_l"]),
                              Paragraph(_esc(e.get("date", "")), st["date_r"])))
            company = e.get("company") or e.get("name", "")
            if company or e.get("location"):
                block.append(_row(Paragraph(f"<i>{_esc(company)}</i>", st["italic_l"]),
                                  Paragraph(f"<i>{_esc(e.get('location',''))}</i>", st["italic_r"])))
            for b in e.get("bullets", [])[:4]:
                block.append(Paragraph(_esc(b), st["bullet"], bulletText="•"))
            flow.append(KeepTogether(block))

    # ---------------- Projects ----------------
    proj = profile.get("projects", [])
    if proj:
        _add_section(flow, st, "Projects")
        for i, p in enumerate(proj):
            if i:
                flow.append(Spacer(1, 4))
            block = []
            plinks = p.get("links") or {}
            tags = " ".join(x for x in [_tag(plinks.get("live"), "Live"),
                                        _tag(plinks.get("github"), "GitHub")] if x)
            title = f"<b>{_esc(p.get('name','Project'))}</b>"
            if p.get("subtitle"):
                title += f" <font color='#555555'>| {_esc(p['subtitle'])}</font>"
            if tags:
                title += f"  {tags}"
            block.append(_row(Paragraph(title, st["title_l"]),
                              Paragraph(_esc(p.get("date", "")), st["date_r"])))
            if p.get("tech"):
                block.append(Paragraph(f"<i>{_esc(p['tech'])}</i>", st["italic_l"]))
            for b in p.get("bullets", [])[:3]:
                block.append(Paragraph(_esc(b), st["bullet"], bulletText="•"))
            flow.append(KeepTogether(block))

    # ---------------- Education ----------------
    # Only the candidate's OWN parsed education — never a hard-coded fallback, so the
    # generated résumé can never contain a school/degree the source résumé didn't have.
    education = profile.get("education") or []
    if education:
        _add_section(flow, st, "Education")
        for i, e in enumerate(education[:3]):
            if not isinstance(e, dict):
                flow.append(Paragraph(_esc(str(e)), st["body"]))
                continue
            if i:
                flow.append(Spacer(1, 3))
            school = e.get("school") or e.get("institution") or ""
            loc = e.get("location") or ""
            degree = e.get("degree") or ""
            dates = e.get("dates") or e.get("graduation") or ""
            detail = e.get("detail") or e.get("gpa") or ""
            flow.append(_row(Paragraph(f"<b>{_esc(school)}</b>", st["title_l"]),
                             Paragraph(f"<i>{_esc(loc)}</i>", st["italic_r"])))
            left2 = " | ".join(x for x in [degree, detail] if x)
            if left2 or dates:
                flow.append(_row(Paragraph(f"<i>{_esc(left2)}</i>", st["italic_l"]),
                                 Paragraph(f"<i>{_esc(dates)}</i>", st["italic_r"])))

    # ---------------- Additional ----------------
    additional = profile.get("additional") or []      # own content only — no fallback
    if additional:
        _add_section(flow, st, "Additional")
        for b in additional[:4]:
            flow.append(Paragraph(_esc(b), st["bullet"], bulletText="•"))

    doc.build(flow)
    return out_path


def _pretty(skill: str) -> str:
    upper = {"aws", "gcp", "sql", "oop", "nlp", "llm", "json", "rest apis",
             "ci/cd", "html5", "css3", "dsa"}
    return skill.upper() if skill in upper else skill.title()
