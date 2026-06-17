"""
aggregator.py — the unified "Search all sources" engine.

One call fans out across every ENABLED source using the safest method per source
(see config/source_catalog.yaml), normalizes everything into one schema, resolves
discovery leads toward real employer/ATS links, and de-dupes across sources.

It deliberately does NOT score / H1B-filter / relevance-filter here — that stays in
app.py's `_add_jobs` so there's a single source of truth. This module's job is
FETCH → NORMALIZE → RESOLVE → DEDUPE, returning jobs ready for that pipeline plus
the counts the UI needs for its search summary.

No Streamlit import — unit-testable on its own.
"""
import os

import yaml

import sources as src

# ATS types that are a "direct public API" pull vs a "company/ATS discovery" pull.
_DIRECT_API_ATS = {"greenhouse", "lever", "ashby"}
_ATS_DISCOVERY_ATS = {"workday", "smartrecruiters"}

_JOB_API_LABELS = {
    "serpapi": "SerpApi Google Jobs",
    "jsearch_openweb": "OpenWeb Ninja JSearch",
    "jsearch_rapidapi": "RapidAPI JSearch",
    "careerjet": "Careerjet",
    "jooble": "Jooble",
    "tavily_discovery": "Tavily discovery",
}


def _pull_adzuna(cfg: dict, focus_keys, new_grad_only, max_years, max_age_hours) -> list:
    """Use Adzuna harder: several entry-level title queries + pagination, then
    dedupe. The source function applies the same entry-level/focus/year filters as
    board pulls, so higher volume does not mean senior/off-track roles leak in."""
    adz = cfg.get("adzuna", {}) or {}
    app_id = adz.get("app_id") or os.getenv("ADZUNA_APP_ID")      # env fallback (see .env)
    app_key = adz.get("app_key") or os.getenv("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        return []
    adz = {**adz, "app_id": app_id, "app_key": app_key}
    jobs = []
    for q in src.entry_level_title_queries(focus_keys, limit=5):
        jobs += [j for j in src.adzuna(
            q, adz["app_id"], adz["app_key"], pages=2, results=50,
            new_grad_only=new_grad_only, max_years=max_years,
            focus_keys=focus_keys, max_age_hours=max_age_hours)
                 if "_error" not in j]
    return src.dedupe(jobs)


def _pull_remote_apis(cfg: dict, focus_keys, new_grad_only, max_years, max_age_hours) -> tuple:
    """Free, no-key remote-job APIs (Remotive + RemoteOK) — full JD, US-filtered,
    same entry-level/focus gate as boards. Returns (jobs, errors)."""
    jobs, errors = [], []
    for fn in (src.remotive, src.remoteok):
        raw = fn(new_grad_only=new_grad_only, max_years=max_years,
                 focus_keys=focus_keys, max_age_hours=max_age_hours)
        errors += [j["_error"] for j in raw if "_error" in j]
        jobs += [j for j in raw if "_error" not in j]
    return src.dedupe(jobs), errors


def _pull_jsearch(cfg: dict, focus_keys, new_grad_only, max_years, max_age_hours) -> list:
    """Use JSearch as a quota-aware structured-job booster."""
    jcfg = cfg.get("jsearch", {}) or {}
    if not src.jsearch_available(cfg):
        return []
    try:
        limit = max(1, int(jcfg.get("max_queries_per_run", 4)))
    except Exception:
        limit = 4
    try:
        pages = max(1, int(jcfg.get("pages", 1)))
    except Exception:
        pages = 1
    jobs = []
    for q in src.entry_level_title_queries(focus_keys, limit=limit):
        query = f"{q} in United States"
        jobs += [j for j in src.jsearch(
            query,
            api_key=jcfg.get("api_key", ""),
            provider=jcfg.get("provider", "openwebninja"),
            country=jcfg.get("country", "us"),
            language=jcfg.get("language", "en"),
            date_posted=jcfg.get("date_posted", "today"),
            pages=pages,
            new_grad_only=new_grad_only,
            max_years=max_years,
            focus_keys=focus_keys,
            max_age_hours=max_age_hours,
            rapidapi_key=jcfg.get("rapidapi_key", ""),
        ) if "_error" not in j]
    return src.dedupe(jobs)


def _job_api_order(cfg: dict) -> list:
    fb = cfg.get("job_api_fallback", {}) or {}
    order = fb.get("provider_order") or [
        "serpapi", "jsearch_openweb", "jsearch_rapidapi", "careerjet", "jooble", "tavily_discovery"]
    return [str(p).strip().lower() for p in order if str(p).strip()]


def job_api_fallback_available(cfg: dict) -> bool:
    """True when at least one limited-search provider in the fallback chain is ready."""
    for provider in _job_api_order(cfg):
        if provider == "serpapi" and src.serpapi_available(cfg):
            return True
        if provider == "jsearch_openweb" and src.jsearch_available(cfg, "openwebninja"):
            return True
        if provider == "jsearch_rapidapi" and src.jsearch_available(cfg, "rapidapi"):
            return True
        if provider == "careerjet" and src.careerjet_available(cfg):
            return True
        if provider == "jooble" and src.jooble_available(cfg):
            return True
        if provider == "tavily_discovery" and src.discovery_available(cfg):
            return True
    return False


def _fallback_query_limit(cfg: dict) -> int:
    fb = cfg.get("job_api_fallback", {}) or {}
    try:
        return max(1, int(fb.get("max_queries_per_run", 4)))
    except Exception:
        return 4


def _fallback_min_results(cfg: dict) -> int:
    fb = cfg.get("job_api_fallback", {}) or {}
    try:
        return max(0, int(fb.get("min_results_before_stop", 0)))
    except Exception:
        return 0


def _call_structured_provider(provider: str, cfg: dict, focus_keys, new_grad_only,
                              max_years, max_age_hours) -> tuple:
    """Return (jobs, errors, failed) for one limited structured provider."""
    limit = _fallback_query_limit(cfg)
    queries = [f"{q} in United States"
               for q in src.entry_level_title_queries(focus_keys, limit=limit)]
    jobs, errors = [], []

    if provider == "serpapi":
        scfg = cfg.get("serpapi", {}) or {}
        if not src.serpapi_available(cfg):
            return [], [], True
        for q in queries:
            raw = src.serpapi_google_jobs(
                q, api_key=scfg.get("api_key", ""),
                location=scfg.get("location", "United States"),
                gl=scfg.get("gl", "us"), hl=scfg.get("hl", "en"),
                pages=scfg.get("pages", 1), new_grad_only=new_grad_only,
                max_years=max_years, focus_keys=focus_keys,
                max_age_hours=max_age_hours)
            errs = [j["_error"] for j in raw if "_error" in j]
            if errs:
                return src.dedupe(jobs), errs, not jobs
            jobs += [j for j in raw if "_error" not in j]
        return src.dedupe(jobs), errors, False

    if provider in ("jsearch_openweb", "jsearch_rapidapi"):
        jcfg = cfg.get("jsearch", {}) or {}
        jprovider = "rapidapi" if provider == "jsearch_rapidapi" else "openwebninja"
        if not src.jsearch_available(cfg, jprovider):
            return [], [], True
        for q in queries:
            raw = src.jsearch(
                q,
                api_key=jcfg.get("api_key", ""),
                provider=jprovider,
                country=jcfg.get("country", "us"),
                language=jcfg.get("language", "en"),
                date_posted=jcfg.get("date_posted", "today"),
                pages=jcfg.get("pages", 1),
                new_grad_only=new_grad_only,
                max_years=max_years,
                focus_keys=focus_keys,
                max_age_hours=max_age_hours,
                rapidapi_key=jcfg.get("rapidapi_key", ""),
            )
            errs = [j["_error"] for j in raw if "_error" in j]
            if errs:
                return src.dedupe(jobs), errs, not jobs
            jobs += [j for j in raw if "_error" not in j]
        return src.dedupe(jobs), errors, False

    if provider == "careerjet":
        ccfg = cfg.get("careerjet", {}) or {}
        if not src.careerjet_available(cfg):
            return [], [], True
        for q in queries:
            raw = src.careerjet(q, affid=ccfg.get("affid", ""),
                                location=ccfg.get("location", "United States"),
                                pages=ccfg.get("pages", 1), new_grad_only=new_grad_only,
                                max_years=max_years, focus_keys=focus_keys, max_age_hours=max_age_hours)
            errs = [j["_error"] for j in raw if "_error" in j]
            if errs:
                return src.dedupe(jobs), errs, not jobs
            jobs += [j for j in raw if "_error" not in j]
        return src.dedupe(jobs), errors, False

    if provider == "jooble":
        jcfg = cfg.get("jooble", {}) or {}
        if not src.jooble_available(cfg):
            return [], [], True
        for q in queries:
            raw = src.jooble(q, api_key=jcfg.get("api_key", ""),
                             location=jcfg.get("location", "United States"),
                             pages=jcfg.get("pages", 1), new_grad_only=new_grad_only,
                             max_years=max_years, focus_keys=focus_keys, max_age_hours=max_age_hours)
            errs = [j["_error"] for j in raw if "_error" in j]
            if errs:
                return src.dedupe(jobs), errs, not jobs
            jobs += [j for j in raw if "_error" not in j]
        return src.dedupe(jobs), errors, False

    return [], [], True


def _pull_tavily_fallback(cfg: dict, focus_keys, new_grad_only, max_years,
                          max_age_hours, h1b_only, disc_labels) -> tuple:
    """Use Tavily discovery only as the final fallback provider."""
    if not (disc_labels and src.discovery_available(cfg)):
        return [], [], True, {}
    queries = src.build_discovery_queries(
        focus_keys=focus_keys, location="United States",
        h1b_only=h1b_only, source_labels=disc_labels,
        max_role_variants=_fallback_query_limit(cfg), variants=1)
    leads = src.search_discovery(queries, cfg, max_age_hours=max_age_hours)
    errs = [l["_error"] for l in leads if "_error" in l]
    leads = [l for l in leads if "_error" not in l]
    jobs = []
    for lead in leads:
        j = src.resolve_lead(lead, new_grad_only=new_grad_only,
                             max_years=max_years, focus_keys=focus_keys,
                             max_age_hours=max_age_hours)
        if j:
            jobs.append(j)
    jobs, enrich = src.enrich_discovery_jobs(
        jobs, new_grad_only=new_grad_only, max_years=max_years,
        focus_keys=focus_keys, max_age_hours=max_age_hours)
    meta = {"discovery_leads": len(leads), **enrich}
    return jobs, errs, False, meta


def _pull_job_api_fallback(cfg: dict, focus_keys, new_grad_only, max_years,
                           max_age_hours, h1b_only=False, disc_labels=None) -> tuple:
    """Try limited providers in priority order, using at most one successful provider."""
    attempted, errors = [], []
    min_results = _fallback_min_results(cfg)
    for provider in _job_api_order(cfg):
        if provider == "tavily_discovery":
            if not src.discovery_available(cfg):
                continue
            attempted.append(provider)
            jobs, errs, failed, tavily_meta = _pull_tavily_fallback(
                cfg, focus_keys, new_grad_only, max_years, max_age_hours,
                h1b_only, disc_labels or [])
            errors += errs
        else:
            if provider == "serpapi" and not src.serpapi_available(cfg):
                continue
            if provider == "jsearch_openweb" and not src.jsearch_available(cfg, "openwebninja"):
                continue
            if provider == "jsearch_rapidapi" and not src.jsearch_available(cfg, "rapidapi"):
                continue
            if provider == "careerjet" and not src.careerjet_available(cfg):
                continue
            if provider == "jooble" and not src.jooble_available(cfg):
                continue
            attempted.append(provider)
            jobs, errs, failed = _call_structured_provider(
                provider, cfg, focus_keys, new_grad_only, max_years, max_age_hours)
            tavily_meta = {}
            errors += errs

        if failed:
            continue
        # Stop after the first provider that responds successfully. A 0-job success
        # still stops when min_results_before_stop=0, which avoids spending every key
        # just because the current query/freshness window is tight.
        if len(jobs) >= min_results:
            return src.dedupe(jobs), {
                "provider": provider,
                "provider_label": _JOB_API_LABELS.get(provider, provider),
                "attempted": attempted,
                "errors": errors,
                **tavily_meta,
            }
    return [], {"provider": "", "provider_label": "", "attempted": attempted,
                "errors": errors}


def load_catalog(path: str) -> dict:
    """Read config/source_catalog.yaml -> {key: {label, mode, group, enabled}}.
    Returns {} if the file is missing/unreadable (the app falls back to Path A)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return (yaml.safe_load(f) or {}).get("sources", {}) or {}
    except Exception:
        return {}


def groups_in_catalog(catalog: dict) -> list:
    """Distinct group names, in a stable display order."""
    order = ["direct_api", "ats_discovery", "discovery"]
    present = {v.get("group") for v in catalog.values()}
    return [g for g in order if g in present]


def sources_by_group(catalog: dict, group: str) -> list:
    """[(key, meta), ...] for one group, preserving catalog order."""
    return [(k, v) for k, v in catalog.items() if v.get("group") == group]


def discovery_labels(catalog: dict, enabled_groups) -> list:
    """Labels of discovery-mode sources whose group is enabled (for Track C search)."""
    return [v["label"] for v in catalog.values()
            if v.get("mode") == "discovery" and v.get("group") in enabled_groups
            and v.get("enabled", True)]


def _ensure_link_flags(job: dict) -> dict:
    """Guarantee a link_warning flag exists so Match & Score can be honest."""
    if "link_warning" not in job:
        lq = src.link_quality(job.get("job_link", ""))
        job["link_warning"] = not lq["ok"]
    return job


def all_discovery_labels(catalog: dict) -> list:
    """All discovery-mode source labels, in catalog order."""
    return [v["label"] for v in catalog.values() if v.get("mode") == "discovery"]


def search_selected_sources(cfg: dict, filters: dict, selected: dict) -> dict:
    """Fan out across ONLY the explicitly selected sources.

    selected = {
        "sponsor_boards": bool,         # Greenhouse / Lever / Ashby targets only
        "themuse":         bool,
        "adzuna":          bool,
        "jsearch":         bool,
        "workday":         bool,
        "smartrecruiters": bool,
        "discovery_labels": list[str],  # e.g. ["LinkedIn Jobs", "Dice"]
    }

    Returns the same shape as search_all_sources:
        {jobs, counts, errors, discovery_enabled, groups}
    """
    focus_keys  = filters.get("focus_keys")  or ["general"]
    new_grad_only = filters.get("new_grad_only", True)
    max_years   = filters.get("max_years",   src.MAX_YEARS_FOR_NEWGRAD)
    max_age_hours = filters.get("max_age_hours")
    location    = filters.get("location",    "")
    h1b_only    = filters.get("h1b_only",    False)
    discovery_enabled = src.discovery_available(cfg)
    disc_labels = selected.get("discovery_labels") or []

    targets_path = (filters.get("targets_path")
                    or cfg.get("paths", {}).get("target_companies",
                                                "config/target_companies.txt"))
    all_targets = src.load_targets(targets_path)

    # Split targets by ATS type once — reused below
    board_targets = [t for t in all_targets
                     if (t.get("ats") or "").lower() in _DIRECT_API_ATS]
    wd_targets    = [t for t in all_targets
                     if (t.get("ats") or "").lower() == "workday"]
    sr_targets    = [t for t in all_targets
                     if (t.get("ats") or "").lower() == "smartrecruiters"]

    def _pull(subset):
        if not subset:
            return [], []
        return src.pull_targets_verbose(
            subset, new_grad_only=new_grad_only, max_years=max_years,
            focus_keys=focus_keys, max_age_hours=max_age_hours)

    jobs, errors, counts = [], [], {}

    if selected.get("sponsor_boards"):
        bj, be = _pull(board_targets)
        jobs += bj;  errors += be
        counts["boards"] = len(bj)

    if selected.get("themuse"):
        muse = src.themuse(new_grad_only=new_grad_only, max_years=max_years,
                           focus_keys=focus_keys, max_age_hours=max_age_hours)
        errors += [m["_error"] for m in muse if "_error" in m]
        muse = [m for m in muse if "_error" not in m]
        counts["the_muse"] = len(muse)
        jobs += muse

    if selected.get("adzuna"):
        azj = _pull_adzuna(cfg, focus_keys, new_grad_only, max_years, max_age_hours)
        counts["adzuna"] = len(azj)
        jobs += azj

    if selected.get("remote_apis"):
        rmj, rme = _pull_remote_apis(cfg, focus_keys, new_grad_only, max_years, max_age_hours)
        jobs += rmj;  errors += rme
        counts["remote_apis"] = len(rmj)

    if selected.get("jsearch"):
        jsj, jsmeta = _pull_job_api_fallback(
            cfg, focus_keys, new_grad_only, max_years, max_age_hours,
            h1b_only=h1b_only, disc_labels=disc_labels)
        counts["jsearch"] = len(jsj)
        if jsmeta.get("provider_label"):
            counts["job_api_provider"] = jsmeta["provider_label"]
        if jsmeta.get("attempted"):
            counts["job_api_attempted"] = ", ".join(
                _JOB_API_LABELS.get(p, p) for p in jsmeta["attempted"])
        errors += jsmeta.get("errors", [])
        for k in ("discovery_leads", "official", "third_party", "enriched"):
            if k in jsmeta:
                counts[f"job_api_{k}"] = jsmeta[k]
        jobs += jsj

    if selected.get("workday"):
        wj, we = _pull(wd_targets)
        jobs += wj;  errors += we
        counts["workday"] = len(wj)

    if selected.get("smartrecruiters"):
        sj, se = _pull(sr_targets)
        jobs += sj;  errors += se
        counts["smartrecruiters"] = len(sj)

    if disc_labels and discovery_enabled and not selected.get("jsearch"):
        # Discovery providers are search-lead sources. We trust the provider's own
        # time_range for freshness; sources.search_discovery only drops leads it can
        # PROVE are too old (a parseable, out-of-window date) so good no-date leads
        # aren't silently lost.
        disc_age = max_age_hours
        queries = src.build_discovery_queries(
            focus_keys=focus_keys, location=location,
            h1b_only=h1b_only, source_labels=disc_labels)
        leads = src.search_discovery(queries, cfg, max_age_hours=disc_age)
        errors += [l["_error"] for l in leads if "_error" in l]
        leads = [l for l in leads if "_error" not in l]
        counts["discovery_leads"] = len(leads)
        for lead in leads:
            j = src.resolve_lead(lead, new_grad_only=new_grad_only,
                                 max_years=max_years, focus_keys=focus_keys,
                                 max_age_hours=disc_age)
            if j:
                jobs.append(j)
        # Discovery V2: upgrade official-ATS leads to full JDs via the public API.
        jobs, enrich = src.enrich_discovery_jobs(
            jobs, new_grad_only=new_grad_only, max_years=max_years,
            focus_keys=focus_keys, max_age_hours=disc_age)
        counts["discovery_official"]    = enrich["official"]
        counts["discovery_third_party"] = enrich["third_party"]
        counts["discovery_enriched"]    = enrich["enriched"]

    jobs = [_ensure_link_flags(j) for j in jobs if "_error" not in j]
    counts["fetched"] = len(jobs)
    jobs = src.merge_duplicates(jobs)
    counts["after_merge"] = len(jobs)

    active_groups = (
        (["direct_api"] if selected.get("sponsor_boards") or selected.get("themuse")
                          or selected.get("adzuna") or selected.get("jsearch")
                          or selected.get("remote_apis") else [])
        + (["ats_discovery"] if selected.get("workday") or selected.get("smartrecruiters") else [])
        + (["discovery"] if disc_labels else [])
    )
    return {
        "jobs": jobs,
        "counts": counts,
        "errors": errors,
        "discovery_enabled": discovery_enabled,
        "groups": active_groups,
    }


def search_all_sources(cfg: dict, filters: dict) -> dict:
    """Fan out across enabled sources. Returns:
       {jobs, counts, errors, discovery_enabled, groups}.
    `filters`: focus_keys, new_grad_only, max_years, max_age_hours, location,
               h1b_only, groups (iterable of enabled group names)."""
    catalog = filters.get("catalog") or load_catalog(
        cfg.get("paths", {}).get("source_catalog", "config/source_catalog.yaml"))
    enabled_groups = set(filters.get("groups") or groups_in_catalog(catalog) or ["direct_api"])

    focus_keys = filters.get("focus_keys") or ["general"]
    new_grad_only = filters.get("new_grad_only", True)
    max_years = filters.get("max_years", src.MAX_YEARS_FOR_NEWGRAD)
    max_age_hours = filters.get("max_age_hours")
    location = filters.get("location", "")
    h1b_only = filters.get("h1b_only", False)

    targets = src.load_targets(filters.get("targets_path")
                               or cfg.get("paths", {}).get("target_companies",
                                                           "config/target_companies.txt"))
    direct_targets = [t for t in targets if (t.get("ats") or "").lower() in _DIRECT_API_ATS]
    ats_targets = [t for t in targets if (t.get("ats") or "").lower() in _ATS_DISCOVERY_ATS]

    jobs, errors, counts = [], [], {}

    # Discovery board labels, computed up front so the job-API fallback (which can
    # route through Tavily discovery) sees the same labels as the discovery group.
    disc_board_labels = (discovery_labels(catalog, enabled_groups)
                         if enabled_groups & {"discovery", "ats_discovery"} else [])

    def _pull(subset):
        if not subset:
            return [], []
        return src.pull_targets_verbose(
            subset, new_grad_only=new_grad_only, max_years=max_years,
            focus_keys=focus_keys, max_age_hours=max_age_hours)

    # --- Group: direct public APIs (Greenhouse/Lever/Ashby + The Muse + Adzuna) ---
    if "direct_api" in enabled_groups:
        gla, gla_err = _pull(direct_targets)
        jobs += gla
        errors += gla_err
        counts["boards"] = len(gla)
        if catalog.get("themuse", {}).get("enabled", True):
            muse = src.themuse(new_grad_only=new_grad_only, max_years=max_years,
                               focus_keys=focus_keys, max_age_hours=max_age_hours)
            errors += [m["_error"] for m in muse if "_error" in m]
            muse = [m for m in muse if "_error" not in m]
            counts["the_muse"] = len(muse)
            jobs += muse
        if catalog.get("adzuna", {}).get("enabled", True):
            azj = _pull_adzuna(cfg, focus_keys, new_grad_only, max_years, max_age_hours)
            counts["adzuna"] = len(azj)
            jobs += azj
        if catalog.get("remote_apis", {}).get("enabled", True):
            rmj, rme = _pull_remote_apis(cfg, focus_keys, new_grad_only, max_years, max_age_hours)
            errors += rme
            counts["remote_apis"] = len(rmj)
            jobs += rmj
        if catalog.get("jsearch", {}).get("enabled", True):
            jsj, jsmeta = _pull_job_api_fallback(
                cfg, focus_keys, new_grad_only, max_years, max_age_hours,
                h1b_only=h1b_only, disc_labels=disc_board_labels)
            counts["jsearch"] = len(jsj)
            if jsmeta.get("provider_label"):
                counts["job_api_provider"] = jsmeta["provider_label"]
            errors += jsmeta.get("errors", [])
            jobs += jsj

    # --- Group: company/ATS discovery (Workday + SmartRecruiters public APIs) ---
    if "ats_discovery" in enabled_groups:
        atsj, ats_err = _pull(ats_targets)
        jobs += atsj
        errors += ats_err
        counts["workday"] = sum(1 for j in atsj if j.get("source") == "Workday")
        counts["smartrecruiters"] = sum(1 for j in atsj if j.get("source") == "SmartRecruiters")

    # --- Track C: key-gated search discovery (LinkedIn/Indeed/Glassdoor/…) ---
    discovery_enabled = src.discovery_available(cfg)
    if discovery_enabled and (enabled_groups & {"discovery", "ats_discovery"}):
        labels = disc_board_labels
        queries = src.build_discovery_queries(focus_keys=focus_keys, location=location,
                                              h1b_only=h1b_only, source_labels=labels)
        leads = src.search_discovery(queries, cfg, max_age_hours=max_age_hours)
        errors += [l["_error"] for l in leads if "_error" in l]
        leads = [l for l in leads if "_error" not in l]
        counts["discovery_leads"] = len(leads)
        for lead in leads:
            j = src.resolve_lead(lead, new_grad_only=new_grad_only,
                                 max_years=max_years, focus_keys=focus_keys,
                                 max_age_hours=max_age_hours)
            if j:
                jobs.append(j)
        jobs, enrich = src.enrich_discovery_jobs(
            jobs, new_grad_only=new_grad_only, max_years=max_years,
            focus_keys=focus_keys, max_age_hours=max_age_hours)
        counts["discovery_official"]    = enrich["official"]
        counts["discovery_third_party"] = enrich["third_party"]
        counts["discovery_enriched"]    = enrich["enriched"]

    jobs = [_ensure_link_flags(j) for j in jobs if "_error" not in j]
    counts["fetched"] = len(jobs)
    jobs = src.merge_duplicates(jobs)
    counts["after_merge"] = len(jobs)

    return {
        "jobs": jobs,
        "counts": counts,
        "errors": errors,
        "discovery_enabled": discovery_enabled,
        "groups": sorted(enabled_groups),
    }
