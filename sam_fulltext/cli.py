"""Command line: search SAM.gov contract opportunities by their real text.

    python -m sam_fulltext --days 3 --keywords keywords.example.txt

Add --json to get the matching notices as JSON on stdout instead of a table,
and --attachments to also read each candidate's PDF and DOCX attachments,
which is where a requirement often actually lives.
"""

import argparse
import datetime as dt
import json
import sys

from . import extract, match


def _row(opp, hit):
    return {
        "noticeId": opp.get("noticeId"),
        "title": opp.get("title"),
        "agency": opp.get("fullParentPathName"),
        "posted": opp.get("postedDate"),
        "deadline": opp.get("responseDeadLine"),
        "naics": opp.get("naicsCode"),
        "psc": opp.get("classificationCode"),
        "type": opp.get("type"),
        "setAside": opp.get("typeOfSetAsideDescription"),
        "link": opp.get("uiLink"),
        "matched": hit["terms"],
        "matchedIn": sorted(hit["where"].keys()),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="sam_fulltext",
        description="Search SAM.gov contract opportunities against their full description, "
                    "not their title.")
    ap.add_argument("--days", type=int, default=1,
                    help="how many days back to read, inclusive of today (default 1)")
    ap.add_argument("--keywords", required=True,
                    help="file of phrases, one per line; # starts a comment")
    ap.add_argument("--extract", default=None,
                    help="read this CSV instead of downloading (also SAM_EXTRACT_PATH)")
    ap.add_argument("--attachments", action="store_true",
                    help="also read each match's PDF and DOCX attachments")
    ap.add_argument("--attach-max", type=int, default=25,
                    help="most notices to read attachments for (default 25)")
    ap.add_argument("--json", action="store_true", help="JSON on stdout instead of a table")
    ap.add_argument("--quiet", action="store_true", help="no progress on stderr")
    args = ap.parse_args(argv)

    log = (lambda *a, **k: None) if args.quiet else (lambda m: print(m, file=sys.stderr))

    terms = match.load_keywords(args.keywords)
    if not terms:
        print("no phrases in %s" % args.keywords, file=sys.stderr)
        return 2
    log("[run] %d phrase(s) from %s" % (len(terms), args.keywords))

    if args.extract:
        opps, stats = extract.parse_window(args.extract, args.days, log=log)
        coverage = {"route": "extract (local file)", "complete": True, **stats}
    else:
        opps, coverage = extract.fetch(args.days, log=log)

    results = []
    for opp in opps:
        hit = match.matches(opp, terms)
        if hit["terms"]:
            results.append((opp, hit))
    log("[run] %d of %d notice(s) matched" % (len(results), len(opps)))

    if args.attachments and results:
        from . import attachments as att
        subset = results[:args.attach_max]
        log("[run] reading attachments for %d notice(s)" % len(subset))
        for opp, hit in subset:
            try:
                text = att.text_for_notice(opp.get("noticeId"), log=log)
            except Exception as e:                      # noqa: BLE001
                log("[attach] %s: %s" % (opp.get("noticeId"), e))
                continue
            if not text:
                continue
            found = [t for t, _ in match.find_terms(terms, match.clean_text(text))]
            if found:
                hit["where"]["attachment"] = found
                for t in found:
                    if t not in hit["terms"]:
                        hit["terms"].append(t)

    if not coverage.get("complete", True):
        log("[run] *** the extract looked stale or short; this window is incomplete")

    rows = [_row(o, h) for o, h in results]
    if args.json:
        json.dump({"generated": dt.datetime.now().isoformat(timespec="seconds"),
                   "coverage": coverage, "count": len(rows), "results": rows},
                  sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        for r in rows:
            print("%s  %s" % (r["posted"][:10], r["title"][:88]))
            print("    %s" % (r["agency"] or "")[:96])
            print("    matched %s  in %s" % (", ".join(r["matched"][:6]),
                                             ", ".join(r["matchedIn"])))
            print("    %s" % r["link"])
            print()
        print("%d match(es)" % len(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
