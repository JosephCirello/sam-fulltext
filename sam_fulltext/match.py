"""Phrase matching against notice text.

This module deliberately ships NO keyword list. What you are hunting for is
your business, not this tool's. Put your phrases in a text file, one per line,
and pass it in. `keywords.example.txt` shows the format with a handful of
obvious terms so the tool runs out of the box.

What it does give you is the matching that took a few goes to get right:

  * HTML is stripped and entities unescaped before anything is matched. The
    extract's descriptions carry markup, so a raw substring search on them
    finds tags as often as words.

  * Matching is on WORD BOUNDARIES, with an optional trailing "s", and with
    spaces and hyphens interchangeable. So one entry, "red team", matches
    "red team", "red-team", "red teams" and "Red-Teams", and does NOT match
    "red teaming" unless you add it, or "sacred teams" at all.

  * Proximity. Some phrases only mean something near another phrase.
    "penetration test" on its own is ordinary IT work; within a few hundred
    characters of "machine learning" it is something else. `near()` answers
    that question so you can score on it.

One trap worth knowing, because it cost an afternoon. The obvious way to build
the pattern is to escape the whole phrase and then substitute inside the
escaped text to make the separator flexible. `re.escape` escapes the space and
the hyphen too, so that substitution nests inside its own output and no
multi-word phrase ever matches. Split first, escape the pieces, then join.
"""

import html as _html
import re

_TAG_RE = re.compile(r"<[^>]+>")
_RX_CACHE = {}


def clean_text(s):
    """HTML stripped, entities unescaped, lower-cased. One place, so every
    caller matches against the same thing."""
    return _html.unescape(_TAG_RE.sub(" ", s or "")).lower()


def term_rx(term):
    """Compiled pattern for one phrase. Cached: the same few hundred phrases
    run against tens of thousands of rows."""
    rx = _RX_CACHE.get(term)
    if rx is None:
        parts = [re.escape(p) for p in re.split(r"[\s\-]+", term.lower().strip()) if p]
        if not parts:
            return None
        rx = _RX_CACHE[term] = re.compile(r"\b" + r"[\s\-]+".join(parts) + r"s?\b")
    return rx


def find_terms(terms, text):
    """[(term, position)] for every phrase that occurs, first hit of each.

    `text` should already have been through clean_text.
    """
    out = []
    for term in terms:
        rx = term_rx(term)
        if rx is None:
            continue
        m = rx.search(text)
        if m:
            out.append((term, m.start()))
    return out


def near(text, terms_a, terms_b, window=300):
    """True when some phrase from A occurs within `window` characters of some
    phrase from B.

    Use it for phrases that are only interesting in company: a
    "vulnerability assessment" beside "large language model" is a different
    job from a "vulnerability assessment" beside "server room".
    """
    a = find_terms(terms_a, text)
    if not a:
        return False
    b = find_terms(terms_b, text)
    if not b:
        return False
    # find_terms gives the FIRST hit of each phrase, which is enough to rule a
    # pair in but not always to rule one out, so re-scan when the first hits
    # are far apart.
    for term_a, _ in a:
        for ma in term_rx(term_a).finditer(text):
            for term_b, _ in b:
                for mb in term_rx(term_b).finditer(text):
                    if abs(ma.start() - mb.start()) <= window:
                        return True
    return False


def load_keywords(path):
    """Read a phrase file: one phrase per line, blank lines and lines starting
    with # ignored. Returns a list in file order, lower-cased.

    A phrase is matched as written, so "ai red team" will not match
    "red team ai". List both if you want both.
    """
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip().lower()
            if line:
                out.append(line)
    seen, uniq = set(), []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def matches(opp, terms, fields=("title", "description")):
    """Phrases found in a notice, and where.

    Returns {"terms": [...], "where": {"title": [...], "description": [...]}}.
    An empty "terms" means no phrase occurred anywhere you looked.
    """
    where, hits = {}, []
    for f in fields:
        found = [t for t, _ in find_terms(terms, clean_text(opp.get(f) or ""))]
        if found:
            where[f] = found
            hits.extend(found)
    seen, uniq = set(), []
    for t in hits:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return {"terms": uniq, "where": where}
