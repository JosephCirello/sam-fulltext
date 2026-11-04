"""
Read SAM.gov contract opportunities from the PUBLIC DAILY EXTRACT, not the API.

WHY THIS EXISTS.

A monitor built on the Get Opportunities API matched its keywords against
`title` + `description` + set-aside for four months before anyone checked what
`description` actually holds. It is NOT text. The official documentation
defines it as "A link to an opportunity description": a URL to a second
endpoint, one more call per notice, behind the same 90-day key.

So every keyword match ran on the TITLE plus a web address. No dollar figure
was ever parsed, because that field was a URL too. A "short scope" signal was
measuring the length of that URL.

Measured against the live site on one evening: of 108 active notices whose own
text carried a given set of phrases, matching the title caught 14; matching the
real description catches 105.

Two more traps surfaced in the same read, and both are easy to hit:

  * The NAICS filter parameter is `ncode`, not `naics`. Send `naics` and the
    API accepts the request and ignores it, so nothing is filtered. That
    accident is the only reason the numbers above exist: every posting was
    being examined rather than a narrow slice.
  * There is a 1,000-records-per-query ceiling and crossing it is not an error.
    On two sampled days it silently dropped 139 of 1,139 and 916 of 1,916.

THE SOURCE. SAM.gov publishes every active contract opportunity, with its FULL
description, NAICS, PSC, set-aside, deadline, link and one row per amendment,
as a single CSV. Keyless, refreshed daily around 03:30 GMT, catalogued on
data.gov:

  https://sam.gov/api/prod/fileextractservices/v1/api/download/
      Contract%20Opportunities/datagov/ContractOpportunitiesFullCSV.csv?privacy=Public

Measured the same evening: 215 MB, 72,071 rows (all Active=Yes), 47 columns,
descriptions up to 32,003 characters with a median of 518, and 700 to 1,500
postings a day. No key, no ceiling, no 90-day clock.

The API path is still worth keeping as a fallback for a morning the extract
cannot be fetched, as long as the run says which route it took.

WHAT THIS MODULE DOES. Stream the extract to a temp file, read only the rows
posted inside your window, keep the notice types you asked for, collapse a
solicitation's amendments to its newest row, and hand back the same record
shape the API returns, so anything already built on that shape keeps working
and simply has something real to read.

A STALE FILE IS A SHORT WINDOW. If the newest posting in the file is older
than yesterday, or the file is a fraction of its usual size, `fetch` reports
the window as INCOMPLETE. A monitor that cannot tell a quiet day from a broken
fetch reports both as silence.

Env knobs (all optional, blank = default):
  SAM_EXTRACT_URL   override the download URL
  SAM_EXTRACT_PATH  read a file already on disk instead of downloading (tests,
                    local runs); the file is never written to
  SAM_EXTRACT_TYPES comma list of notice types to keep (default: Solicitation,
                    Presolicitation, Combined Synopsis/Solicitation, Sources
                    Sought, Special Notice)
"""

import csv
import os
import re
import sys
import tempfile
import datetime as dt

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

EXTRACT_URL = (
    "https://sam.gov/api/prod/fileextractservices/v1/api/download/"
    "Contract%20Opportunities/datagov/ContractOpportunitiesFullCSV.csv?privacy=Public")

# The five notice types the API path asked for as ptype o,p,k,r,s. Award
# notices, justifications, surplus sales and bundling notices stay out, as
# they always did.
DEFAULT_TYPES = (
    "Solicitation",
    "Presolicitation",
    "Combined Synopsis/Solicitation",
    "Sources Sought",
    "Special Notice",
)

# Columns, as the extract's header spells them (measured 2026-10-23).
COL = {
    "id": "NoticeId", "title": "Title", "sol": "Sol#", "dept": "Department/Ind.Agency",
    "subtier": "Sub-Tier", "office": "Office", "aac": "AAC Code", "posted": "PostedDate",
    "type": "Type", "basetype": "BaseType", "setaside_code": "SetASideCode",
    "setaside": "SetASide", "deadline": "ResponseDeadLine", "naics": "NaicsCode",
    "psc": "ClassificationCode", "active": "Active", "award_amt": "Award$",
    "link": "Link", "desc": "Description",
}


def _types():
    raw = (os.environ.get("SAM_EXTRACT_TYPES") or "").strip()
    if not raw:
        return set(DEFAULT_TYPES)
    return {t.strip() for t in raw.split(",") if t.strip()}


def download(dest_path, url=None, timeout=900, log=print):
    """Stream the extract to dest_path. Returns bytes written. Raises on failure.

    The service answers with a redirect to a short-lived S3 link; requests
    follows it. Nothing here retries -- the caller falls back to the API path
    and SAYS SO, which is the behaviour that matters.
    """
    import requests
    url = url or (os.environ.get("SAM_EXTRACT_URL") or "").strip() or EXTRACT_URL
    log(f"[extract] downloading {url.split('?')[0]}")
    n = 0
    with requests.get(url, stream=True, timeout=timeout, allow_redirects=True) as resp:
        resp.raise_for_status()
        with open(dest_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                if chunk:
                    fh.write(chunk)
                    n += len(chunk)
    log(f"[extract] downloaded {n:,} bytes")
    if n < 1_000_000:
        raise RuntimeError(f"extract is only {n:,} bytes -- not the full file")
    return n


def _clean_naics(s):
    s = (s or "").strip()
    return re.sub(r"[^0-9]", "", s)[:6]


def _amount(s):
    s = (s or "").strip().replace("$", "").replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def to_opp(row):
    """One extract row -> the same dict shape the Get Opportunities API returns.

    The description is REAL TEXT here, not a link to a second endpoint.
    That is the whole point of reading the extract instead of the API: a
    matcher pointed at this field is reading the notice, not its address.
    """
    g = lambda k: (row.get(COL[k]) or "").strip()
    agency = ".".join(p for p in (g("dept"), g("subtier"), g("office")) if p)
    return {
        "noticeId": g("id"),
        "title": g("title"),
        "description": g("desc"),
        "typeOfSetAsideDescription": g("setaside"),
        "typeOfSetAside": g("setaside_code"),
        "fullParentPathName": agency,
        "department": g("dept"),
        "naicsCode": _clean_naics(g("naics")),
        "classificationCode": g("psc"),
        "postedDate": g("posted"),
        "responseDeadLine": g("deadline") or None,
        "type": g("type"),
        "baseType": g("basetype"),
        "active": g("active"),
        "uiLink": g("link") or (f"https://sam.gov/opp/{g('id')}/view" if g("id") else ""),
        "solicitationNumber": g("sol"),
        "aac": g("aac"),
        "award": {"amount": _amount(g("award_amt"))} if _amount(g("award_amt")) is not None else {},
        "_route": "extract",
    }


def parse_window(path, days_back, today=None, types=None, log=print):
    """Rows posted within the last `days_back` days (inclusive of today), of the
    kept types, active, amendments collapsed. Returns (opps, stats).

    Amendments: SAM.gov issues a NEW notice id on every amendment and the
    extract carries every version as its own row. Within the window the newest
    row per (solicitation number, office code) is kept and the number of
    earlier versions it replaced is recorded on it as `_amendments`. Rows with
    no solicitation number are kept as they are.
    """
    today = today or dt.date.today()
    cutoff = (today - dt.timedelta(days=int(days_back))).isoformat()
    keep_types = types or _types()
    stats = {"rows_total": 0, "rows_in_window": 0, "rows_kept_type": 0,
             "rows_inactive": 0, "amendments_collapsed": 0, "cutoff": cutoff,
             "newest_posted": ""}
    best = {}      # (sol, aac) -> opp
    loose = []     # no solicitation number
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        for row in csv.DictReader(fh):
            stats["rows_total"] += 1
            posted = (row.get(COL["posted"]) or "")[:10]
            if posted > stats["newest_posted"]:
                stats["newest_posted"] = posted
            if posted < cutoff:
                continue
            stats["rows_in_window"] += 1
            if (row.get(COL["type"]) or "").strip() not in keep_types:
                continue
            if (row.get(COL["active"]) or "").strip().lower() not in ("yes", "y", "true", ""):
                stats["rows_inactive"] += 1
                continue
            stats["rows_kept_type"] += 1
            opp = to_opp(row)
            key = (opp["solicitationNumber"].upper().replace(" ", ""), opp["aac"].upper())
            if not key[0]:
                loose.append(opp)
                continue
            prev = best.get(key)
            if prev is None:
                opp["_amendments"] = 0
                best[key] = opp
            elif opp["postedDate"] >= prev["postedDate"]:
                opp["_amendments"] = prev.get("_amendments", 0) + 1
                opp["_replaces"] = prev["noticeId"]
                best[key] = opp
                stats["amendments_collapsed"] += 1
            else:
                prev["_amendments"] = prev.get("_amendments", 0) + 1
                prev.setdefault("_replaces", opp["noticeId"])
                stats["amendments_collapsed"] += 1
    opps = list(best.values()) + loose
    opps.sort(key=lambda o: o["postedDate"], reverse=True)
    log(f"[extract] {stats['rows_total']:,} rows in the file; {stats['rows_in_window']:,} posted "
        f"since {cutoff}; {stats['rows_kept_type']:,} of the kept types; "
        f"{stats['amendments_collapsed']} amendment rows collapsed -> {len(opps):,} opportunities")
    return opps, stats


def fetch(days_back, today=None, log=print):
    """Download (or read SAM_EXTRACT_PATH) and return (opps, coverage).

    coverage is what the mail prints: the route taken and the counts. The
    caller treats any exception as 'extract unavailable this morning' and
    falls back to the API path, saying so in the subject line.
    """
    local = (os.environ.get("SAM_EXTRACT_PATH") or "").strip()
    tmp = None
    try:
        if local:
            path = local
            log(f"[extract] reading local file {path}")
            size = os.path.getsize(path)
        else:
            fd, tmp = tempfile.mkstemp(prefix="sam_extract_", suffix=".csv")
            os.close(fd)
            size = download(tmp, log=log)
            path = tmp
        opps, stats = parse_window(path, days_back, today=today, log=log)
        # A STALE FILE IS A SHORT WINDOW. The extract is refreshed daily; if its
        # newest posting is older than yesterday, or the file is a fraction of
        # its usual size (72,071 rows on 2026-10-23), the window it serves is
        # not the window the mail claims. Said in the subject line, not here.
        today = today or dt.date.today()
        yesterday = (today - dt.timedelta(days=1)).isoformat()
        stale = bool(stats["newest_posted"]) and stats["newest_posted"] < yesterday
        thin = stats["rows_total"] < 20_000
        complete = not (stale or thin)
        if not complete:
            log(f"[extract] *** INCOMPLETE: newest posting in the file is {stats['newest_posted'] or '?'}"
                f"{' (stale)' if stale else ''}; {stats['rows_total']:,} rows"
                f"{' (far fewer than usual)' if thin else ''}. The window is SHORT.")
        coverage = {"route": "extract", "bytes": size, "complete": complete, **stats}
        return opps, coverage
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
