"""
sam_attachments.py — read the attachments where the real requirement lives.

WHY (deep dive 2026-10-23). Of 205 live notices sam.gov's own full-text search
returned for one set of search phrases, 97 carried the phrase NOWHERE in the title
or the description: it was in a PDF -- the PWS, the SOW, the amendment. The
extract has no attachment column and the API never read one. A notice whose
description says "see attached Performance Work Statement" is, to a
description-only matcher, a notice about nothing.

WHAT. For a bounded set of candidates per run, list the notice's public
attachments through sam.gov's own (keyless, undocumented) resources endpoint,
download the ones that can carry text (PDF, DOCX, TXT) within a size cap, pull
the text, and hand it back so assign_tier can score the notice on it. The
tier decided from an attachment says so in `why_matched`.

WHO IS A CANDIDATE. Reading every attachment on SAM.gov is thousands of PDFs a
day. The run reads two groups: (1) notices already tiered from their text, so
the attachment can sharpen the score and the reasons; (2) notices NOT tiered
whose description is thin or points at an attachment, and whose NAICS, PSC or
notice type says the requirement could be ours. Everything is capped
(SAM_ATTACH_MAX notices, SAM_ATTACH_SECONDS wall clock, SAM_ATTACH_PER_NOTICE
files, SAM_ATTACH_MAX_BYTES per file) and the run prints what it read and what
the caps left unread. A cap that is hit is REPORTED, never silent.

Endpoints (measured 2026-10-23 from a plain client, no cookie, no key):
  GET https://sam.gov/api/prod/opps/v3/opportunities/{id}/resources
      -> _embedded.opportunityAttachmentList[].attachments[]
         {resourceId, name, mimeType, size, accessLevel, deletedFlag}
  GET https://sam.gov/api/prod/opps/v3/opportunities/resources/files/{resourceId}/download?&token=
      -> 303 to a signed S3 link that lives about ten seconds; follow at once.
"""

import io
import os
import re
import time
import zipfile

RESOURCES_URL = "https://sam.gov/api/prod/opps/v3/opportunities/{id}/resources?excludeDeleted=false&withFileCounts=true"
DOWNLOAD_URL = "https://sam.gov/api/prod/opps/v3/opportunities/resources/files/{rid}/download?&token="
# SAM.gov wants a real User-Agent with a way to reach you. Set SAM_USER_AGENT
# to your own before running this against the live site.
USER_AGENT = os.environ.get("SAM_USER_AGENT") or "sam-fulltext/1.0 (+https://github.com/)"

TEXT_TYPES = {".pdf", ".docx", ".txt", ".md", ".rtf"}
# Attachments that are never the requirement: forms, pricing sheets, drawings.
SKIP_NAME = re.compile(r"(sf[-\s]?1449|sf[-\s]?33|sf[-\s]?30|wage determination|price|pricing|"
                       r"cost sheet|drawing|dwg|map|photo|image)", re.I)


def _env_int(name, default):
    raw = (os.environ.get(name) or "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def caps():
    return {
        # Measured 2026-10-23 live: 3.4 s per notice (23 files, 635,243 chars for
        # 12 notices); a 3-day window had 439 candidates. 150 notices in ~9 min
        # reads every tiered notice and the best-ranked thin ones; the rest are
        # counted as UNREAD in the log line.
        "max_notices": _env_int("SAM_ATTACH_MAX", 150),
        "seconds": _env_int("SAM_ATTACH_SECONDS", 540),
        "per_notice": _env_int("SAM_ATTACH_PER_NOTICE", 3),
        "max_bytes": _env_int("SAM_ATTACH_MAX_BYTES", 12_000_000),
        "max_chars": _env_int("SAM_ATTACH_MAX_CHARS", 80_000),
    }


# ----------------------------------------------------------------------
# text extraction -- each returns "" rather than raising
# ----------------------------------------------------------------------
def text_from_pdf(data):
    try:
        import pypdf
    except ImportError:
        return ""
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        parts = []
        for page in reader.pages[:120]:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue
        return "\n".join(parts)
    except Exception:
        return ""


def text_from_docx(data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    except Exception:
        return ""
    xml = re.sub(r"</w:p>", "\n", xml)
    return re.sub(r"<[^>]+>", " ", xml)


def text_from_bytes(name, data):
    ext = os.path.splitext(name or "")[1].lower()
    if ext == ".pdf":
        return text_from_pdf(data)
    if ext == ".docx":
        return text_from_docx(data)
    if ext in (".txt", ".md", ".rtf"):
        return data.decode("utf-8", errors="replace")
    return ""


# ----------------------------------------------------------------------
# the network half -- injectable, so the selection and the scoring are
# testable without sam.gov
# ----------------------------------------------------------------------
def list_attachments(notice_id, session=None):
    import requests
    s = session or requests
    resp = s.get(RESOURCES_URL.format(id=notice_id), headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    out = []
    for lst in ((resp.json().get("_embedded") or {}).get("opportunityAttachmentList") or []):
        for a in lst.get("attachments") or []:
            if str(a.get("deletedFlag") or "0") != "0":
                continue
            if (a.get("accessLevel") or "").lower() != "public":
                continue
            out.append({"rid": a.get("resourceId"), "name": a.get("name") or "",
                        "ext": (a.get("mimeType") or os.path.splitext(a.get("name") or "")[1] or "").lower(),
                        "size": int(a.get("size") or 0)})
    return out


def download(rid, max_bytes, session=None):
    import requests
    s = session or requests
    resp = s.get(DOWNLOAD_URL.format(rid=rid), headers={"User-Agent": USER_AGENT},
                 timeout=60, allow_redirects=True, stream=True)
    resp.raise_for_status()
    buf = io.BytesIO()
    for chunk in resp.iter_content(chunk_size=1 << 16):
        buf.write(chunk)
        if buf.tell() > max_bytes:
            break
    return buf.getvalue()


# ----------------------------------------------------------------------
# reading one notice
# ----------------------------------------------------------------------
_POINTS_AT_ATTACHMENT = re.compile(
    r"(attach|see sow|statement of work|\bpws\b|performance work statement|"
    r"solicitation document|rfp package|see the attached|statement of objectives|\bsoo\b)",
    re.I)

TEXT_BEARING = (".pdf", ".docx", ".txt", ".md", ".rtf")


def points_at_attachment(opp):
    """True when a notice's own text defers to a document.

    A description that reads "see the attached Performance Work Statement" is,
    to a description-only matcher, a notice about nothing. These are the ones
    worth spending a download on first.
    """
    return bool(_POINTS_AT_ATTACHMENT.search(opp.get("description") or ""))


def text_for_notice(notice_id, session=None, log=print, limits=None):
    """Concatenated text of a notice's public, readable attachments.

    Bounded by caps(): SAM_ATTACH_PER_NOTICE files, SAM_ATTACH_MAX_BYTES per
    file, SAM_ATTACH_MAX_CHARS returned. Every cap that bites is SAID. A cap
    you cannot see looks exactly like an attachment that held nothing, which is
    the failure this whole tool exists to avoid.
    """
    c = dict(caps())
    if limits:
        c.update(limits)
    try:
        items = list_attachments(notice_id, session=session)
    except Exception as e:                                        # noqa: BLE001
        log("[attach] %s: could not list attachments (%s)" % (notice_id, e))
        return ""

    readable = [a for a in items
                if os.path.splitext(a.get("name") or "")[1].lower() in TEXT_BEARING]
    if len(items) > len(readable):
        log("[attach] %s: %d of %d attachment(s) carry no text we can read"
            % (notice_id, len(items) - len(readable), len(items)))

    out, used = [], 0
    for a in readable:
        if used >= c["per_notice"]:
            log("[attach] %s: stopped at the per-notice cap of %d; %d file(s) left UNREAD"
                % (notice_id, c["per_notice"], len(readable) - used))
            break
        try:
            data = download(a["rid"], c["max_bytes"], session=session)
        except Exception as e:                                    # noqa: BLE001
            log("[attach] %s: %s failed (%s)" % (notice_id, a.get("name"), e))
            continue
        if len(data) >= c["max_bytes"]:
            log("[attach] %s: %s hit the %s-byte cap and was read only that far"
                % (notice_id, a.get("name"), format(c["max_bytes"], ",")))
        text = text_from_bytes(a.get("name"), data)
        used += 1
        if text:
            out.append(text)

    joined = "\n".join(out)
    if len(joined) > c["max_chars"]:
        log("[attach] %s: text truncated at %s characters"
            % (notice_id, format(c["max_chars"], ",")))
        joined = joined[:c["max_chars"]]
    return joined
