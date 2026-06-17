"""
llm_score.py — OPTIONAL résumé↔JD relevance scoring with Claude (your own API key).

OFF by default. The app works fully without it. Enable by putting an Anthropic API
key in config/settings.yaml (`llm_api_key`) and `pip install anthropic`. Then Match &
Score shows an "🤖 AI-score with Claude" button that reads each job's FULL description
plus your résumé and returns a real fit score + reason + matched/missing skills — true
resume-to-JD reasoning, not keyword overlap. The score blends into the priority ranking.

Uses the official anthropic SDK with prompt caching: your résumé is the stable cached
prefix, so re-scoring batches is cheap. Nothing is ever sent without your key + click.
"""
import json

DEFAULT_MODEL = "claude-opus-4-8"   # per the user's Claude default; override via settings llm_model
MAX_JD_CHARS = 2600                 # trim each JD so a 25-job batch stays well within context
MAX_RESUME_CHARS = 8000


def available(api_key: str) -> bool:
    """True only if a key is set AND the anthropic SDK is importable."""
    if not api_key:
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except Exception:
        return False


# Structured-output schema. No numeric min/max (unsupported in structured outputs) —
# fit_score is clamped to 0-100 in code.
_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "fit_score": {"type": "integer"},
                    "fit_reason": {"type": "string"},
                    "matched_skills": {"type": "array", "items": {"type": "string"}},
                    "gaps": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["index", "fit_score", "fit_reason", "matched_skills", "gaps"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}

_SYSTEM_TMPL = """You are a precise technical recruiter scoring how well a CANDIDATE'S \
RÉSUMÉ matches each job, for a new-grad / early-career software engineer who needs H1B \
sponsorship.

Score each job 0-100 by REAL fit to THIS résumé — the candidate's actual skills, \
projects, and level — NOT generic desirability:
- 85-100: strong match, apply immediately.
- 70-84: good match, apply after light tailoring.
- 55-69: stretch, apply only if the company is unusually strong.
- below 55: weak / off-target.
Penalize hard: senior/mid roles, >2 years required, roles needing skills the candidate \
lacks, and off-domain specializations the résumé shows NO evidence of (wireless/RF, \
firmware/embedded, kernel, robotics, hardware). A generic "Software Engineer" title is \
not enough — judge by the JD's real requirements vs this résumé.

In fit_reason, name the concrete overlap or the concrete gap in one sentence. \
matched_skills: only skills actually present in the résumé. gaps: real missing \
requirements (never tell the candidate to fake anything).

=== CANDIDATE RÉSUMÉ (source of truth) ===
{resume}"""


def score_jobs(resume_text: str, jobs: list, api_key: str,
               model: str = None, max_jobs: int = 25) -> list:
    """Score up to `max_jobs` jobs against the résumé in ONE cached API call.

    `jobs` is a list of dicts with title/company/description. Returns a list of
    {index, fit_score, fit_reason, matched_skills, gaps} (index into the batch).
    Raises on API/SDK errors so the caller can surface them.
    """
    import anthropic
    model = model or DEFAULT_MODEL
    batch = jobs[:max_jobs]
    payload = [{
        "index": i,
        "title": j.get("title", ""),
        "company": j.get("company", ""),
        "jd": (j.get("description", "") or "")[:MAX_JD_CHARS],
    } for i, j in enumerate(batch)]

    client = anthropic.Anthropic(api_key=api_key)
    system = [{
        "type": "text",
        "text": _SYSTEM_TMPL.format(resume=(resume_text or "")[:MAX_RESUME_CHARS]),
        "cache_control": {"type": "ephemeral"},   # résumé is the reused prefix
    }]
    messages = [{
        "role": "user",
        "content": ("Score these jobs against the résumé. Return ONLY the JSON object "
                    'matching {"results":[{"index","fit_score","fit_reason",'
                    '"matched_skills","gaps"}]}.\n\n' + json.dumps(payload, ensure_ascii=False)),
    }]
    # Prefer structured output where the SDK/model supports it; fall back to plain
    # JSON-in-text if the `output_config` param isn't accepted by the installed SDK
    # (the param shape has changed across anthropic versions). _parse_results tolerates
    # prose-wrapped JSON either way, so scoring works regardless of SDK version.
    try:
        resp = client.messages.create(
            model=model, max_tokens=6000, system=system, messages=messages,
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}})
    except TypeError:
        resp = client.messages.create(
            model=model, max_tokens=6000, system=system, messages=messages)
    text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    return _parse_results(text)


def _parse_results(text: str) -> list:
    """Parse + sanitize the model's JSON. Tolerant of stray prose around the object."""
    try:
        data = json.loads(text)
    except Exception:
        a, b = text.find("{"), text.rfind("}")
        if a == -1 or b <= a:
            return []
        try:
            data = json.loads(text[a:b + 1])
        except Exception:
            return []
    out = []
    for r in (data.get("results") or []):
        try:
            idx = int(r["index"])
            fs = max(0, min(100, int(r["fit_score"])))
        except Exception:
            continue
        out.append({
            "index": idx,
            "fit_score": fs,
            "fit_reason": str(r.get("fit_reason", ""))[:300],
            "matched_skills": [str(x) for x in (r.get("matched_skills") or []) if str(x).strip()][:15],
            "gaps": [str(x) for x in (r.get("gaps") or []) if str(x).strip()][:15],
        })
    return out


def merge_scores(batch_items: list, results: list) -> int:
    """Write AI scores back onto the session items' job dicts (pure; unit-testable).

    `batch_items` are the session items passed to score_jobs (each a dict with 'job').
    Sets fit_score / fit_reason / matched_skills_ai / gaps_ai. Returns how many merged.
    """
    n = 0
    for r in results:
        i = r.get("index")
        if not isinstance(i, int) or not (0 <= i < len(batch_items)):
            continue
        j = batch_items[i].get("job") if isinstance(batch_items[i], dict) else None
        if not isinstance(j, dict):
            continue
        j["fit_score"] = r["fit_score"]
        j["fit_reason"] = r.get("fit_reason", "")
        if r.get("matched_skills"):
            j["matched_skills_ai"] = r["matched_skills"]
        if r.get("gaps"):
            j["gaps_ai"] = r["gaps"]
        n += 1
    return n
