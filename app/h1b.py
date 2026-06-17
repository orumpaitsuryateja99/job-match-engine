"""
h1b.py — structured H1B sponsor lookup with confidence.

Replaces loose substring matching ("unit" matching "United") with normalized
exact / alias matching against config/h1b_sponsors.json. Returns a confidence
level so the UI can be honest: Verified (high) / Likely (medium) / Unknown.

⚠️ H1B-CRITICAL: confidence is a heuristic from a curated list, NOT a guarantee.
Always verify on MyVisaJobs / H1BGrader before applying.
"""
import json
import os
import re

_CACHE = {}     # path -> {normalized_alias: confidence}

LABELS = {"high": "Verified", "medium": "Likely", "unknown": "Unknown"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def load_db(path: str) -> dict:
    """Load + index the sponsor DB as {normalized_alias: confidence}. Cached."""
    if path in _CACHE:
        return _CACHE[path]
    index = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for e in data.get("sponsors", []):
            conf = e.get("confidence", "medium")
            keys = set(e.get("aliases", [])) | {e.get("name", "")}
            for k in keys:
                nk = _norm(k)
                if nk:
                    # keep the strongest confidence if an alias collides
                    if index.get(nk) != "high":
                        index[nk] = conf
    except Exception:
        index = {}
    _CACHE[path] = index
    return index


def status(company: str, db_path: str, fallback_list=None) -> dict:
    """Return {sponsor, confidence, label} for a company.

    Matches the normalized company name exactly against the DB aliases (no
    substring matching). `fallback_list` (e.g. the settings.yaml list) is used
    only if the DB is missing, and is treated as 'medium' confidence.
    """
    norm = _norm(company)
    if not norm:
        return {"sponsor": False, "confidence": "unknown", "label": "Unknown"}
    index = load_db(db_path)
    if norm in index:
        conf = index[norm]
        return {"sponsor": True, "confidence": conf, "label": LABELS.get(conf, "Likely")}
    if not index and fallback_list:
        if norm in {_norm(s) for s in fallback_list}:
            return {"sponsor": True, "confidence": "medium", "label": "Likely"}
    return {"sponsor": False, "confidence": "unknown", "label": "Unknown"}


def badge(st_dict: dict) -> str:
    """Short header badge for a job card."""
    conf = st_dict.get("confidence")
    if conf == "high":
        return "✅ H1B (verified)"
    if conf == "medium":
        return "🟢 H1B (likely)"
    return "⚠️ verify H1B"
