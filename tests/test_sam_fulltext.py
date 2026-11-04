"""Tests that need no network and no key.

The extract tests run against a small committed CSV written here, so the suite
is self-contained. The matching tests pin the trap described in the README:
escape the whole phrase before loosening its separator and no multi-word phrase
matches anything, which is silent and looks exactly like "nothing posted today".
"""

import csv
import datetime as dt
import io
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sam_fulltext import extract, match  # noqa: E402


# ----------------------------------------------------------------------
# a tiny extract, written in the real column names
# ----------------------------------------------------------------------
HEADER = [extract.COL[k] for k in (
    "id", "title", "sol", "dept", "subtier", "office", "aac", "posted", "type",
    "basetype", "setaside_code", "setaside", "deadline", "naics", "psc",
    "active", "award_amt", "link", "desc")]


def row(notice_id, title, desc, posted, sol="", aac="AAA", ntype="Solicitation",
        active="Yes", naics="541715", award=""):
    return {
        extract.COL["id"]: notice_id, extract.COL["title"]: title,
        extract.COL["sol"]: sol, extract.COL["dept"]: "DEPT",
        extract.COL["subtier"]: "SUB", extract.COL["office"]: "OFFICE",
        extract.COL["aac"]: aac, extract.COL["posted"]: posted,
        extract.COL["type"]: ntype, extract.COL["basetype"]: ntype,
        extract.COL["setaside_code"]: "", extract.COL["setaside"]: "",
        extract.COL["deadline"]: "", extract.COL["naics"]: naics,
        extract.COL["psc"]: "R425", extract.COL["active"]: active,
        extract.COL["award_amt"]: award, extract.COL["link"]: "",
        extract.COL["desc"]: desc,
    }


@pytest.fixture()
def csv_path(tmp_path):
    def _write(rows):
        p = tmp_path / "extract.csv"
        with io.open(p, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=HEADER)
            w.writeheader()
            for r in rows:
                w.writerow(r)
        return str(p)
    return _write


TODAY = dt.date(2026, 10, 23)


# ----------------------------------------------------------------------
# the point of the whole tool
# ----------------------------------------------------------------------
def test_the_phrase_is_in_the_description_not_the_title(csv_path):
    """The failure this exists to fix: a title match finds nothing, a
    description match finds the notice."""
    rows = [row("N1", "Technical Support Services",
                "The contractor shall perform red team assessments of the agency "
                "large language model deployments.", "2026-10-23")]
    opps, _ = extract.parse_window(csv_path(rows), 1, today=TODAY, log=lambda *_: None)
    assert len(opps) == 1
    terms = ["red team"]
    assert match.matches(opps[0], terms, fields=("title",))["terms"] == []
    assert match.matches(opps[0], terms)["terms"] == ["red team"]


def test_description_is_real_text_not_a_url(csv_path):
    rows = [row("N1", "T", "A real sentence about machine learning.", "2026-10-23")]
    opps, _ = extract.parse_window(csv_path(rows), 1, today=TODAY, log=lambda *_: None)
    assert not opps[0]["description"].startswith("http")
    assert "machine learning" in opps[0]["description"]


# ----------------------------------------------------------------------
# the window
# ----------------------------------------------------------------------
def test_rows_outside_the_window_are_dropped(csv_path):
    rows = [row("IN", "in", "machine learning", "2026-10-23"),
            row("OUT", "out", "machine learning", "2026-09-01")]
    opps, stats = extract.parse_window(csv_path(rows), 1, today=TODAY, log=lambda *_: None)
    assert [o["noticeId"] for o in opps] == ["IN"]
    assert stats["rows_total"] == 2 and stats["rows_in_window"] == 1


def test_unwanted_notice_types_and_inactive_rows_are_dropped(csv_path):
    rows = [row("KEEP", "a", "ml", "2026-10-23"),
            row("AWARD", "b", "ml", "2026-10-23", ntype="Award Notice"),
            row("DEAD", "c", "ml", "2026-10-23", active="No")]
    opps, stats = extract.parse_window(csv_path(rows), 1, today=TODAY, log=lambda *_: None)
    assert [o["noticeId"] for o in opps] == ["KEEP"]
    assert stats["rows_inactive"] == 1


# ----------------------------------------------------------------------
# amendments: a new notice id every time, every version its own row
# ----------------------------------------------------------------------
def test_amendments_collapse_to_the_newest_and_the_count_travels(csv_path):
    rows = [row("V1", "orig", "ml", "2026-10-21", sol="SOL-1"),
            row("V2", "amend 1", "ml", "2026-10-22", sol="SOL-1"),
            row("V3", "amend 2", "ml", "2026-10-23", sol="SOL-1")]
    opps, stats = extract.parse_window(csv_path(rows), 7, today=TODAY, log=lambda *_: None)
    assert len(opps) == 1
    assert opps[0]["noticeId"] == "V3"
    assert opps[0]["_amendments"] == 2
    assert stats["amendments_collapsed"] == 2


def test_rows_with_no_solicitation_number_are_all_kept(csv_path):
    rows = [row("A", "a", "ml", "2026-10-23", sol=""),
            row("B", "b", "ml", "2026-10-23", sol="")]
    opps, _ = extract.parse_window(csv_path(rows), 1, today=TODAY, log=lambda *_: None)
    assert sorted(o["noticeId"] for o in opps) == ["A", "B"]


def test_same_solicitation_different_office_is_not_collapsed(csv_path):
    rows = [row("A", "a", "ml", "2026-10-23", sol="SOL-1", aac="AAA"),
            row("B", "b", "ml", "2026-10-23", sol="SOL-1", aac="BBB")]
    opps, _ = extract.parse_window(csv_path(rows), 1, today=TODAY, log=lambda *_: None)
    assert len(opps) == 2


# ----------------------------------------------------------------------
# shape and parsing
# ----------------------------------------------------------------------
def test_award_amount_is_parsed_and_absent_when_blank(csv_path):
    rows = [row("A", "a", "ml", "2026-10-23", award="$1,250,000.00"),
            row("B", "b", "ml", "2026-10-23", award="")]
    opps, _ = extract.parse_window(csv_path(rows), 1, today=TODAY, log=lambda *_: None)
    by = {o["noticeId"]: o for o in opps}
    assert by["A"]["award"]["amount"] == 1250000.0
    assert by["B"]["award"] == {}


def test_a_link_is_built_when_the_extract_has_none(csv_path):
    rows = [row("N1", "a", "ml", "2026-10-23")]
    opps, _ = extract.parse_window(csv_path(rows), 1, today=TODAY, log=lambda *_: None)
    assert opps[0]["uiLink"] == "https://sam.gov/opp/N1/view"


# ----------------------------------------------------------------------
# matching
# ----------------------------------------------------------------------
def test_multi_word_phrases_match_at_all():
    """The trap in the README. If the whole phrase is escaped before the
    separator is loosened, this returns nothing and the failure is silent."""
    t = match.clean_text("We need a red team for this.")
    assert [x[0] for x in match.find_terms(["red team"], t)] == ["red team"]


@pytest.mark.parametrize("text", [
    "a red team exercise", "a red-team exercise", "two red teams", "RED TEAM",
])
def test_separator_and_plural_are_flexible(text):
    assert match.find_terms(["red team"], match.clean_text(text))


@pytest.mark.parametrize("text", ["sacred teams", "redteam", "red  teaming of"])
def test_word_boundaries_are_respected(text):
    assert not match.find_terms(["red team"], match.clean_text(text))


def test_a_phrase_split_by_a_tag_still_matches():
    """The extract's descriptions carry markup, and a phrase is often broken
    across it: "machine <b>learning</b>". Tags become whitespace and the
    separator in the pattern is "one or more spaces or hyphens", so the phrase
    survives the split. Pinned because a stricter separator would silently
    drop every emphasised phrase in the feed."""
    t = match.clean_text("<p>machine <b>learning</b> services</p>")
    assert [x[0] for x in match.find_terms(["machine learning"], t)] == ["machine learning"]


def test_markup_itself_is_never_matched():
    t = match.clean_text('<a href="https://example.gov/red-team">see here</a>')
    assert match.find_terms(["href"], t) == []
    assert match.find_terms(["red team"], t) == []


def test_entities_are_unescaped():
    assert "r&d" in match.clean_text("R&amp;D")


def test_proximity():
    t = match.clean_text("penetration test of the large language model deployment")
    assert match.near(t, ["penetration test"], ["large language model"], window=300)
    far = match.clean_text("penetration test " + ("x " * 400) + "large language model")
    assert not match.near(far, ["penetration test"], ["large language model"], window=300)


def test_load_keywords_ignores_comments_and_duplicates(tmp_path):
    p = tmp_path / "k.txt"
    p.write_text("# a comment\n\nRed Team\nred team\nmachine learning  # trailing\n",
                 encoding="utf-8")
    assert match.load_keywords(str(p)) == ["red team", "machine learning"]


def test_matches_reports_where_it_found_each_phrase(csv_path):
    rows = [row("N1", "Red Team Services", "about machine learning", "2026-10-23")]
    opps, _ = extract.parse_window(csv_path(rows), 1, today=TODAY, log=lambda *_: None)
    hit = match.matches(opps[0], ["red team", "machine learning"])
    assert hit["where"]["title"] == ["red team"]
    assert hit["where"]["description"] == ["machine learning"]
    assert sorted(hit["terms"]) == ["machine learning", "red team"]
