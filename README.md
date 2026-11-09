# sam-fulltext

**SAM.gov's opportunities API returns a URL where you expect a description.**

If you are keyword-matching federal contract opportunities against the
`description` field of the Get Opportunities API, you are matching a web
address. The API documentation says so plainly: `description` is
*"A link to an opportunity description."* It is a pointer to a second endpoint,
one more call per notice, behind the same key.

So a search that looks like it reads every word of a notice is really reading
its title.

This repository is the fix, and the measurements behind it.

---

## What we measured

On one evening, against the live site:

| | |
|---|---|
| Active notices whose own text carried a given set of phrases | **108** |
| Caught by matching the title | **14** |
| Caught by matching the real description | **105** |

Three other things turned up in the same read, and each is easy to hit:

- **The NAICS filter parameter is `ncode`, not `naics`.** Send `naics` and the
  API accepts the request and ignores it. Our own calls had been sending the
  wrong name for months, so nothing was ever filtered. That accident is the
  only reason the numbers above exist, because every posting was being
  examined rather than a narrow slice.
- **There is a 1,000-record ceiling per query**, and crossing it is not an
  error. On two sampled days it silently dropped 139 of 1,139 and 916 of
  1,916 postings.
- **The requirement is often not in the notice at all.** Of 205 notices that
  SAM.gov's own full-text search returned for one set of phrases, **97**
  carried the phrase only inside an attached PDF: the PWS, the SOW, an
  amendment. A description that says "see the attached Performance Work
  Statement" is, to a description-only matcher, a notice about nothing.

## The source that fixes it

SAM.gov publishes every active contract opportunity, with its **full
description**, NAICS, PSC, set-aside, deadline, link, and one row per
amendment, as a single CSV. No key. No ceiling. No 90-day clock. Refreshed
daily around 03:30 GMT and catalogued on data.gov.

Measured on the same evening: **215 MB, 72,071 rows, 47 columns**, descriptions
up to 32,003 characters with a median of 518.

That file is what this tool reads.

---

## Install

```
git clone <this repo>
cd sam-fulltext
pip install -r requirements.txt
```

Python 3.9 or newer. `requests` is required. `pypdf` is optional and only
needed for `--attachments`.

## Use

```
python -m sam_fulltext --days 3 --keywords keywords.example.txt
```

Write your own phrase file. One phrase per line, `#` starts a comment. **This
tool ships no keyword list on purpose**: what you are hunting for is your
business, not ours. `keywords.example.txt` is a handful of obvious terms so
the thing runs out of the box.

```
python -m sam_fulltext --days 7 --keywords mine.txt --json > hits.json
python -m sam_fulltext --days 3 --keywords mine.txt --attachments
```

Useful flags:

| Flag | Does |
|---|---|
| `--days N` | how far back to read, inclusive of today |
| `--keywords FILE` | your phrase file, required |
| `--extract FILE` | read a CSV already on disk instead of downloading |
| `--attachments` | also read each match's PDF, DOCX and TXT attachments |
| `--json` | JSON on stdout instead of a table |
| `--quiet` | no progress on stderr |

Environment knobs, all optional: `SAM_EXTRACT_URL`, `SAM_EXTRACT_PATH`,
`SAM_EXTRACT_TYPES`, `SAM_USER_AGENT`, and the attachment caps
`SAM_ATTACH_PER_NOTICE`, `SAM_ATTACH_MAX_BYTES`, `SAM_ATTACH_MAX_CHARS`.

**Set `SAM_USER_AGENT` to something that identifies you** before running the
attachment reader against the live site.

## What it does

- Streams the daily extract to a temp file and reads only the rows inside your
  window, so a 215 MB download is not held in memory.
- Collapses amendments. SAM.gov issues a **new notice id on every amendment**
  and the extract carries every version as its own row, so the same
  solicitation appears several times. The newest row per solicitation and
  office is kept, and the count of versions it replaced travels with it.
- Matches on word boundaries, with an optional trailing `s`, and with spaces
  and hyphens interchangeable. One entry, `red team`, matches `red-team` and
  `red teams` and not `sacred teams`.
- Says when the window is short. If the newest posting in the file is older
  than yesterday, or the file is a fraction of its usual size, the run says
  **INCOMPLETE** rather than reporting a quiet day.

That last one matters more than it sounds. A monitor that cannot tell a quiet
day from a broken fetch will report both as silence.

## A trap worth knowing

The obvious way to allow `red team` to match `red-team` is to escape the whole
phrase and then substitute inside the escaped text to loosen the separator.
`re.escape` escapes the space and the hyphen too, so that substitution nests
into its own output and **no multi-word phrase matches anything at all**. The
tests catch it. Split the phrase first, escape the pieces, then join them with
the flexible separator.

## Tests

```
python -m pytest -q
```

The extract tests run against a committed fixture, so they need no network and
no key.

## Limits

- The extract is a daily snapshot. Something posted and amended within the same
  day appears once, as its newest version at the time the file was cut.
- Attachment reading is capped by design. A cap that bites is printed; it is
  never silent.
- Only the notice types a search usually wants are kept by default
  (Solicitation, Presolicitation, Combined Synopsis/Solicitation, Sources
  Sought, Special Notice). Override with `SAM_EXTRACT_TYPES`.
- Nothing here is affiliated with or endorsed by SAM.gov or GSA. The endpoints
  it reads are public; the undocumented one it uses for attachments could
  change without notice.

## Licence

MIT. Use it, fork it, sell what you build with it.

---

Built and released by [Potestas AI](https://potestasai.com).

---

### Potestas AI

**[Potestas AI](https://potestasai.com)** tests what AI agents do, not what they say, and publishes the evidence.

- **Putting an AI agent in front of money, patients or customers?** [See the screening](https://potestasai.com/screening) or [book a 30-minute call](https://calendly.com/joseph-cirello-potestasai).
- **Free book:** [Your AI Is Safe. Says Who?](https://potestasai.com/book)
- **What we find, as we find it:** [potestasai.com/findings](https://potestasai.com/findings)
