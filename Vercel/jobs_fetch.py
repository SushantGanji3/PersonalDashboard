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
# Same JobRight "mini-sites" API backs both newgrad-jobs.com and intern-list.com
# (intern-list.com just iframes https://jobright.ai/minisites-jobs/intern) -- so
# internships are pulled from the "intern:" categories of this one API rather
# than scraping intern-list.com's page.
NEWGRAD_JOBS_API = "https://jobright.ai/swan/mini-sites/list"
NEWGRAD_JOBS_CATEGORIES = [
    "newgrad:us:swe",
    "newgrad:us:ml_ai",
    "newgrad:us:data_engineer",
    "newgrad:us:cyber_security",
]
INTERN_JOBS_CATEGORIES = [
    "intern:us:swe",
    "intern:us:ml_ai",
    "intern:us:data_engineer",
    "intern:us:cyber_security",
]
UA = "Mozilla/5.0 (dashboard-jobs-fetch; personal use)"
TIMEOUT = 25


def http_json(url, data=None, headers=None):
    hdrs = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    payload = None
    if data is not None:
        if isinstance(data, (dict, list)):
            payload = json.dumps(data).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        else:
            payload = data
    req = urllib.request.Request(url, data=payload, headers=hdrs)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def compile_kw(keywords):
    parts = [re.escape(k.lower()) for k in keywords]
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(parts) + r")(?![a-z0-9])")


LEVEL_ONE_RE = re.compile(r"\b(?:engineer|developer|scientist|analyst|programmer|associate)[,\s-]*(?:i|1)\b")
SENIOR_RE = re.compile(r"\b(?:senior|sr\.?|staff|principal|distinguished|manager|director|head of|vp|president|architect|tech lead|team lead|lead engineer)\b")
SENIOR_LEVEL_RE = re.compile(r"\b(?:engineer|developer|scientist|analyst|programmer)[,\s-]*(?:ii|iii|iv|v|2|3|4|5)\b")
NOT_ENGINEERING_RE = re.compile(
    r"\b(?:recruiter|recruiting|talent acquisition|sourcer|sales|account executive|account manager|"
    r"marketing|customer success|business development|solutions consultant|technical writer|"
    r"data labeler|data labeling|labeling analyst|annotator|annotation|transcriber|transcription|translator)\b"
)
PHD_TITLE_RE = re.compile(r"\bph\.?\s?d\b|\bdoctoral\b|\bdoctorate\b")
INTERN_TITLE_RE = re.compile(r"\bintern(?:ship)?\b|\bco-?op\b")

# Undergrad CS vs Graduate degrees (PhD / Master's)
BACHELOR_RE = re.compile(
    r"\b(?:bachelor(?:'s)?|undergrad(?:uate)?|b\.?s\.?(?:\b|[^a-z])|b\.?a\.?(?:\b|[^a-z])|college\s+degree|college\s+diploma|associate(?:'s)?)\b",
    re.I,
)
PHD_RE = re.compile(r"\b(?:ph\.?d\.?|doctorate|doctoral)\b", re.I)
MASTER_RE = re.compile(r"\b(?:master(?:'s)?|m\.?s\.?(?:\s+in|\s+degree|\s+or|\s*\/|\b))\b", re.I)


def is_graduate_only(title, degrees=None, qualifications=""):
    """Returns True if the posting requires a PhD or Master's degree and is NOT open to undergrads."""
    t = title.lower()
    q = (qualifications or "").lower()

    # 1. Title explicitly targets PhD
    if PHD_RE.search(t):
        return True

    # 2. Title explicitly targets Master's (without bachelor/intern)
    if MASTER_RE.search(t) and not BACHELOR_RE.search(t) and not INTERN_TITLE_RE.search(t):
        return True

    # 3. Explicit degree list (e.g. from Simplify feeds)
    if degrees:
        degs = [d.lower() for d in degrees]
        has_bachelor = any("bachelor" in d or "undergrad" in d for d in degs)
        has_grad = any("master" in d or "phd" in d or "ph.d" in d or "doctor" in d for d in degs)
        if has_grad and not has_bachelor:
            return True

    # 4. Qualifications text check (e.g. from newgrad-jobs.com)
    if q:
        has_bachelor_qual = bool(BACHELOR_RE.search(q))
        has_phd_qual = bool(PHD_RE.search(q))
        has_master_qual = bool(MASTER_RE.search(q))
        if (has_phd_qual or has_master_qual) and not has_bachelor_qual:
            return True

    return False


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
    return is_graduate_only(title, degrees=degrees)


HIRE_TIME_RE = re.compile(r"^(20\d{2})-(Summer|Fall|Winter|Spring)$", re.I)
SEASON_YEAR_RE = re.compile(r"\b(summer|fall|winter|spring)\b[^a-z0-9]{0,10}(20\d{2})", re.I)
YEAR_SEASON_RE = re.compile(r"\b(20\d{2})\b[^a-z0-9]{0,10}(summer|fall|winter|spring)\b", re.I)
BARE_SEASON_RE = re.compile(r"\b(summer|fall|winter|spring)\b", re.I)


def extract_internship_season(hire_time, title, feed_season=None):
    """Returns e.g. "Summer 2027" for an internship posting (or just "Winter"
    if a year can't be pinned down), or None if no term could be determined
    from JobRight's hireTime field, the title, or the feed's own season field
    (SimplifyJobs/vanshb03 listings.json entries carry a "season" key --
    e.g. "Winter" -- with no year attached)."""
    m = HIRE_TIME_RE.match((hire_time or "").strip())
    if m:
        return f"{m.group(2).capitalize()} {m.group(1)}"
    t = title or ""
    m = SEASON_YEAR_RE.search(t)
    if m:
        return f"{m.group(1).capitalize()} {m.group(2)}"
    m = YEAR_SEASON_RE.search(t)
    if m:
        return f"{m.group(2).capitalize()} {m.group(1)}"
    fs = (feed_season or "").strip()
    if fs and fs.lower() != "null":
        year_m = re.search(r"20\d{2}", t)
        return f"{fs} {year_m.group(0)}" if year_m else fs
    m = BARE_SEASON_RE.search(t)
    if m:
        return m.group(1).capitalize()
    return None


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
        all_ids = []
        for m in members:
            if "ids" in m:
                all_ids.extend(m["ids"])
            else:
                all_ids.append(m["id"])
        winner["ids"] = sorted(set(all_ids))
        out.append(winner)
    return out


def fetch_and_filter():
    """Fetch + filter current open postings. Returns a list of dicts with
    id/company/title/url/location/date_posted, newest-first, uncapped."""
    cfg = http_json(SOURCES_URL)
    filt = cfg["filters"]
    level_re = compile_kw(filt["level_keywords"])
    role_re = compile_kw(filt["role_keywords"])
    exclude_grad_years = filt.get("exclude_grad_years", [])
    us_only = filt.get("us_only", False)
    alias_map = build_alias_map(cfg["companies"])
    simp_cfg = cfg.get("simplify", {})

    raw = []

    # 1. SimplifyJobs & vanshb03 aggregator feeds
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
            title = j.get("title", "")
            season = extract_internship_season("", title, j.get("season")) \
                if INTERN_TITLE_RE.search(title.lower()) else None
            raw.append({
                "id": f"simplify:{j.get('id') or j.get('url')}",
                "company": canonical or raw_name,
                "title": title,
                "url": j.get("url", ""),
                "location": ", ".join(j.get("locations", []) or []),
                "degrees": j.get("degrees", []) or [],
                "qualifications": "",
                "level_implied": simp_cfg.get("level_implied", False),
                "date_posted": j.get("date_posted") or j.get("date_updated") or None,
                "season": season,
            })

    # 2. newgrad-jobs.com / intern-list.com (both are JobRight mini-sites --
    #    same API, "newgrad:" vs "intern:" categories)
    for cat in NEWGRAD_JOBS_CATEGORIES + INTERN_JOBS_CATEGORIES:
        try:
            resp = http_json(
                f"{NEWGRAD_JOBS_API}?position=0&count=100",
                data={"category": cat}
            )
            job_list = resp.get("result", {}).get("jobList", [])
            for j in job_list:
                props = j.get("properties", {})
                raw_name = (props.get("company") or "").strip()
                if not raw_name:
                    continue
                canonical = alias_map.get(raw_name.lower())
                job_id = j.get("jobId")
                posted_at = j.get("postedAt")
                date_posted = int(posted_at / 1000) if posted_at else None
                title = props.get("title", "").strip()
                season = extract_internship_season(props.get("hireTime", ""), title) \
                    if cat in INTERN_JOBS_CATEGORIES else None

                raw.append({
                    "id": f"newgrad-jobs:{job_id}",
                    "company": canonical or raw_name,
                    "title": title,
                    "url": f"https://jobright.ai/jobs/info/{job_id}",
                    "location": props.get("location", "").strip(),
                    "degrees": [],
                    "qualifications": props.get("qualifications", "").strip(),
                    "level_implied": True,
                    "date_posted": date_posted,
                    "season": season,
                })
        except Exception as e:
            print(f"  newgrad-jobs feed failed: {cat} -> {e}", file=sys.stderr)
            continue

    def keep(p):
        if not matches(p["title"], level_re, role_re, p.get("level_implied")):
            return False
        # Filter out PhD-only and Master's-only positions for undergrad CS students
        if is_graduate_only(p["title"], p.get("degrees", []), p.get("qualifications", "")):
            return False
        if is_excluded_grad_year(p["title"], exclude_grad_years):
            return False
        if us_only and not is_us(p.get("location", ""), p["title"]):
            return False
        return True

    hits = [p for p in raw if keep(p)]
    postings = collapse(hits)
    # sort newest-first when a source date is available
    postings.sort(key=lambda p: p.get("date_posted") or 0, reverse=True)

    for p in postings:
        p.pop("degrees", None)
        p.pop("qualifications", None)
        p.pop("level_implied", None)

    return postings


def main():
    import datetime
    postings = fetch_and_filter()
    out = {
        "generatedAt": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(postings),
        "postings": postings[:60],
    }
    print(json.dumps(out))


if __name__ == "__main__":
    main()
