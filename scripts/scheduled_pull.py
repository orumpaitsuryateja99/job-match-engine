#!/usr/bin/env python3
"""
scheduled_pull.py — headless overnight job pull (run from cron).

Fetches across every configured source (boards / The Muse / Adzuna / SerpApi / JSearch /
Careerjet / Jooble / remote APIs / discovery), saves the RAW jobs to
logs/overnight_jobs.json, writes a short digest, and optionally notifies you (Slack/email).

It does NOT score or need your résumé — that stays session-only by design. Open the app,
upload your résumé, and click "📥 Import overnight pull" to score them in-session.

Cron example (daily 6am):
    0 6 * * *  cd "/path/to/Job_Automation" && .venv/bin/python scripts/scheduled_pull.py
"""
import os
import sys
import json
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "app"))


def _load_dotenv():
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


def main():
    _load_dotenv()
    import yaml
    import sources as src
    import aggregator as agg
    try:
        import notify
    except Exception:
        notify = None

    cfg = yaml.safe_load(open(os.path.join(ROOT, "config", "settings.yaml"), encoding="utf-8"))
    # Fresh pull (no cache); broad entry-level focus. Window is configurable via
    # settings.yaml `overnight_max_age_hours` (default 24 — the daily-fresh-jobs goal).
    filters = {
        "focus_keys": ["newgrad", "backend", "fullstack"],
        "new_grad_only": True,
        "max_years": int(cfg.get("search_max_years", 2)),
        "max_age_hours": int(cfg.get("overnight_max_age_hours", 24)),
        "location": "United States",
        "h1b_only": False,
    }
    res = agg.search_all_sources(cfg, filters)
    jobs = res.get("jobs", [])
    counts = res.get("counts", {})
    now = datetime.datetime.now().isoformat(timespec="seconds")

    out_dir = os.path.join(ROOT, "logs")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "overnight_jobs.json"), "w", encoding="utf-8") as f:
        json.dump({"fetched_at": now, "jobs": jobs, "counts": counts}, f)

    src_breakdown = ", ".join(f"{k}={v}" for k, v in counts.items()
                              if k not in ("fetched", "after_merge") and isinstance(v, int) and v)
    digest = (f"Overnight pull {now}: {len(jobs)} jobs ready to import.\n"
              f"Sources: {src_breakdown or 'n/a'}.\n"
              f"Open the app → upload résumé → '📥 Import overnight pull'.")
    print(digest)

    if notify and notify.available(cfg):
        ok, ch = notify.send(cfg, f"🎯 {len(jobs)} new jobs — overnight pull", digest)
        print(f"notify: {'sent via ' + ', '.join(ch) if ok else 'no channel reached'}")
    return 0


def _notify_failure(err: str):
    """Best-effort: ping the configured channel so a broken cron run isn't silent."""
    try:
        import yaml
        import notify
        cfg = yaml.safe_load(open(os.path.join(ROOT, "config", "settings.yaml"), encoding="utf-8"))
        if notify.available(cfg):
            notify.send(cfg, "⚠️ Overnight job pull FAILED", err[:1500])
    except Exception:
        pass


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        msg = f"scheduled_pull failed: {e}\n{traceback.format_exc()}"
        print(msg, file=sys.stderr)
        _notify_failure(msg)
        sys.exit(1)
