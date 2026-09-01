#!/usr/bin/env python3
"""
Standalone fetch for the dashboard's "New Grad SWE Jobs" card.

Pulls the filter config + target company list straight from Sushant's
friend's job-alerts repo (rishabhsabnavis/job-alerts) each run, so any
tuning he does there applies automatically. Only hits the aggregator
listings.json feeds (SimplifyJobs / vanshb03) -- the per-company ATS
endpoints (Greenhouse/Lever/Ashby/...) are blocked by this environment's
network egress, but the aggregator feeds alone cover ~850+ matching open
roles across the target company list.

Stdlib only. Prints one JSON object to stdout:
  {"generatedAt": "...", "postings": [ {id, company, title, url, location}, ... ]}
"""
import json
import re
import sys
import urllib.request

SOURCES_URL = "https://raw.githubusercontent.com/rishabhsabnavis/job-alerts/main/sources.json"
UA = "Mozilla/5.0 (dashboard-jobs-fetch; personal use)"
TIMEOUT = 25


def http_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def compile_kw(keywords):
    parts = [re.escape(k.lower()) for k in keywords]
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(parts) + r")(?![a-z0-9])")


LEVEL_ONE_RE = re.compile(r"\b(?:engineer|developer|scientist|analyst|programmer|associate)[,\s-]*(?:i|1)\b")
SENIOR_RE = re.compile(r"\b(?:senior|sr\.?|staff|principal|distinguished|manager|director|head of|vp|president|architect|tech lead|team lead|lead engineer)\b")
SENIOR_LEVEL_RE = re.compile(r"\b(?:engineer|developer|scientist|analyst|programmer)[,\s-]*(?:ii|iii|iv|v|2|3|4|5)\b")
NOT_ENGINEERING_RE = re.compile(r"\b(?:recruiter|recruiting|talent acquisition|sourcer|sales|account executive|account manager|marketing|customer success|business development|solutions consultant|technical writer)\b")
PHD_TITLE_RE = re.compile(r"\bph\.?\s?d\b|\bdoctoral\b|\bdoctorate\b")
INTERN_TITLE_RE = re.compile(r"\bintern(?:ship)?\b|\bco-?op\b")


def matches(title, level_re, role_re, level_implied=False):
    t = title.lower()
    if not role_re.search(t) or NOT_ENGINEERING_RE.search(t):
        return False
    if level_re.search(t):
        return True
    if SENIOR_RE.search(t):
        return False
    if LEVEL_ONE_RE.search(t):
        return True
    if level_implied:
        return not SENIOR_LEVEL_RE.search(t)
    return False


def is_phd_only(title, degrees):
    if PHD_TITLE_RE.search(title.lower()):
        return True
    if degrees:
        degs = [d.lower() for d in degrees]
        has_lower = any("bachelor" in d or "master" in d or "undergrad" in d for d in degs)
        has_phd = any("phd" in d or "ph.d" in d or "doctor" in d for d in degs)
        if has_phd and not has_lower:
            return True
    return False


def is_excluded_grad_year(title, excluded_years):
    if not excluded_years:
        return False
    t = title.lower()
    if INTERN_TITLE_RE.search(t):
        return False
    return any(re.search(r"(?<![0-9])" + re.escape(y) + r"(?![0-9])", t) for y in excluded_years)


US_CODES = ("al ak az ar ca co ct de fl ga hi id il in ia ks ky la me md ma "
            "mi mn ms mo mt ne nv nh nj nm ny nc nd oh ok or pa ri sc sd tn "
            "tx ut vt va wa wv wi wy dc").split()
US_CODE_RE = re.compile(r"(?:^|,\s*)(" + "|".join(US_CODES) + r")\b", re.I)
US_MARKERS = ("united states", "usa", "u.s.", "u.s.a", "us-", ", us", "(us", "remote, us", "us remote")
NON_US_TERMS = [
    "canada", "toronto", "vancouver", "montreal", "ottawa", "waterloo", "calgary",
    "united kingdom", "uk", "u.k.", "england", "scotland", "wales", "britain",
    "london", "manchester", "edinburgh", "glasgow", "bristol", "ireland", "dublin",
    "germany", "berlin", "munich", "hamburg", "france", "paris", "netherlands",
    "amsterdam", "spain", "madrid", "barcelona", "switzerland", "zurich", "geneva",
    "sweden", "stockholm", "poland", "warsaw", "india", "bangalore", "bengaluru",
    "hyderabad", "mumbai", "delhi", "gurgaon", "pune", "chennai", "china", "beijing",
    "shanghai", "shenzhen", "japan", "tokyo", "korea", "seoul", "singapore", "taiwan",
    "taipei", "hong kong", "australia", "sydney", "melbourne", "israel", "tel aviv",
    "brazil", "sao paulo", "mexico", "uae", "dubai", "emea", "apac", "latam",
]
NON_US_RE = re.compile(r"\b(?:" + "|".join(re.escape(t) for t in NON_US_TERMS) + r")\b")


def is_us(location, title=""):
    l = (location or "").lower()
    if location and (US_CODE_RE.search(location) or any(m in l for m in US_MARKERS)):
        return True
    if l and NON_US_RE.search(l):
        return False
    if title and NON_US_RE.search(title.lower()):
        return False
    return True


def build_alias_map(companies):
    amap = {}
    for c in companies:
        amap[c["name"].lower()] = c["name"]
        for a in c.get("aliases", []):
            amap[a.lower()] = c["name"]
    return amap


def collapse(postings):
    groups = {}
    for p in postings:
        key = (p["company"].strip().lower(), p["title"].strip().lower())
        groups.setdefault(key, []).append(p)
    out = []
    for members in groups.values():
        winner = dict(members[0])
        winner["ids"] = sorted({m["id"] for m in members})
        out.append(winner)
    return out


def main():
    cfg = http_json(SOURCES_URL)
    filt = cfg["filters"]
    level_re = compile_kw(filt["level_keywords"])
    role_re = compile_kw(filt["role_keywords"])
    exclude_phd = filt.get("exclude_phd", False)
    exclude_grad_years = filt.get("exclude_grad_years", [])
    us_only = filt.get("us_only", False)
    alias_map = build_alias_map(cfg["companies"])
    simp_cfg = cfg.get("simplify", {})

    raw = []
    for url in simp_cfg.get("listings", []):
        try:
            data = http_json(url)
        except Exception as e:
            print(f"  feed failed: {url} -> {e}", file=sys.stderr)
            continue
        for j in data:
            if not (j.get("active", True) and j.get("is_visible", True)):
                continue
            raw_name = j.get("company_name", "")
            canonical = alias_map.get(raw_name.lower())
            if simp_cfg.get("match_target_companies_only", True) and not canonical:
                continue
            raw.append({
                "id": f"simplify:{j.get('id') or j.get('url')}",
                "company": canonical or raw_name,
                "title": j.get("title", ""),
                "url": j.get("url", ""),
                "location": ", ".join(j.get("locations", []) or []),
                "degrees": j.get("degrees", []) or [],
                "level_implied": simp_cfg.get("level_implied", False),
                "date_posted": j.get("date_posted") or j.get("date_updated") or None,
            })

    def keep(p):
        if not matches(p["title"], level_re, role_re, p.get("level_implied")):
            return False
        if exclude_phd and is_phd_only(p["title"], p.get("degrees", [])):
            return False
        if is_excluded_grad_year(p["title"], exclude_grad_years):
            return False
        if us_only and not is_us(p.get("location", ""), p["title"]):
            return False
        return True

    hits = [p for p in raw if keep(p)]
    postings = collapse(hits)
    # sort newest-first when a source date is available
    postings.sort(key=lambda p: p.get("date_posted") or "", reverse=True)

    for p in postings:
        p.pop("degrees", None)
        p.pop("level_implied", None)

    import datetime
    out = {
        "generatedAt": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(postings),
        "postings": postings[:60],
    }
    print(json.dumps(out))


if __name__ == "__main__":
    main()
