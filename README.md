# Karriere München Job Search API

Every open position at the Landeshauptstadt München, machine-readable.

The city's careers portal (`karriere-muenchen.jobs.hr.cloud.sap`) runs on SAP
SuccessFactors Recruiting Marketing. Its search UI is powered by a single
undocumented JSON endpoint, documented here: **one POST, no authentication, no
API key**.

- **[`openapi.yaml`](openapi.yaml)** — OpenAPI 3.1 spec, lints clean under `redocly`
- **[`fetch_jobs.py`](fetch_jobs.py)** — reference client, standard library only
- **[`FUZZING.md`](FUZZING.md)** — how the spec was validated

Verified against the live API on 2026-08-09.

## Quick start

```bash
curl -s https://karriere-muenchen.jobs.hr.cloud.sap/services/recruiting/v1/jobs \
  -H 'Content-Type: application/json' \
  -d '{"locale":"de_DE","pageNumber":0,"keywords":"","location":"","facetFilters":{},"categoryId":0}'
```

Or with the included client:

```bash
./fetch_jobs.py                                # all postings, as a table
./fetch_jobs.py --json                         # JSON, with ISO dates and detail URLs
./fetch_jobs.py --facets                       # counts by occupational group and department
./fetch_jobs.py Erzieher                       # keyword search
./fetch_jobs.py --facet cust_BerufGr=Lehrberufe
```

## What you get

Per posting:

| Field | Meaning |
|---|---|
| `id`, `unifiedStandardTitle` | Requisition id and job title |
| `urlTitle` | Slug for building the detail URL |
| `unifiedStandardStart` / `End` | Publication and expiry date (`DD.MM.YY`) |
| `cust_Bewerbungsfrist` | Application deadline (`DD.MM.YYYY`) |
| `cust_ReferatEigen` | Hiring municipal department (Referat) |
| `cust_BerufGr` | Occupational group |
| `cust_EntgeltGr_Karriereseite` | Pay grade, TVöD (public employees) |
| `cust_BesoldGr_Karriereseite` | Pay grade, A-scale (civil servants) |
| `cust_Stellenmerkmal1` / `2` | Attribute badges (experience, allowances) |
| `jobLocationShort…` | Work address, with WGS84 coordinates |

Pay grade, deadline, and address are present on some postings only.

## Search and filter

Two modes on the same endpoint:

| Mode | Trigger | Returns |
|---|---|---|
| Search | `facetingOnly` absent/`false` | `jobSearchResult[]` + `totalJobs` |
| Faceting | `facetingOnly: true` | `facetFields[]` + `totalJobs`, no records |

Useful request fields — only `locale` is required:

| Field | Notes |
|---|---|
| `locale` | Required. This portal publishes `de_DE` only |
| `keywords` | Full-text, case-insensitive, token-prefix match. Max 250 chars |
| `location` | Free-text address filter. Max 250 chars |
| `facetFilters` | `{"cust_BerufGr": ["Lehrberufe"]}` — values must be arrays; keys combine with AND |
| `pageNumber` | Zero-based. Page size is fixed at 10 |
| `categoryId` | `0` means no filter |

Faceting honours the active query, so it doubles as a cheap way to discover valid
filter values. Two facet fields are configured: `cust_BerufGr` (occupational
group) and `cust_ReferatKarriereseite` (department).

## Job detail URLs

```
/job/{urlTitle}/{id}-{locale}/      e.g. /job/x/24289-de_DE/
```

The slug segment is cosmetic — any placeholder resolves. `urlTitle` is already
percent-encoded; do not re-encode it. The API's `id` is a requisition id and is a
different id space from the longer posting ids in `/sitemap.xml`.

## Gotchas

- **`keywords: ""` is the match-all query.** `*` is a literal term and matches
  nothing.
- **Matching is token-prefix, not substring.** `Arbeit` matches; `rbeit` does not.
- **`jobSearchResult` is omitted entirely on zero hits** — not an empty array.
- **`totalJobs` is the grand total**, not the size of the current page. Page until
  `jobSearchResult` is absent.
- **Zero results are ambiguous.** A non-matching keyword, an unpublished locale,
  and an empty category all return `200 {"totalJobs":0}` with no error. Keep a
  `keywords: ""` query as a control.
- **Errors use real status codes** — branch on the presence of an `error` object,
  not on `totalJobs == 0`. A `302` returns HTML, so a JSON parser will throw.
- **Two date formats coexist in one record** (`DD.MM.YY` and `DD.MM.YYYY`).
  Neither is ISO — parse explicitly.
- **`cust_BerufGr` values are often duplicated.** De-duplicate before display.
- **`sortBy` has no observable effect.** Sort client-side.
- **Omit `alertId`, and keep faceting requests on `categoryId: 0`** — other values
  can trigger a server-side `500`. Details in [`FUZZING.md`](FUZZING.md).

### Status codes

| Status | Cause |
|---|---|
| `200` | Query ran — may legitimately have matched nothing |
| `400` | Missing `locale`, field over 250 chars, unconfigured facet, or a control character in `keywords` |
| `302` | Payload could not be deserialized (wrong JSON type, integer ≥ 2³¹, malformed JSON). Returns HTML |
| `500` | Server-side crash — see `alertId` / `categoryId` above |
| `405` / `415` | Not `POST` / not `application/json` |

## Coverage

Black-box documentation, not vendor material. Endpoint behaviour, both request
modes, all status codes, and the absence of authentication were reproduced
directly; the response schema comes from a census of all currently published
postings, validated by 247 generated test cases across five seeds
([`FUZZING.md`](FUZZING.md)).

Less certain: `sortBy`, `brand`, `skills` and `rcmCandidateId` are accepted but
inert here, so their purpose is inferred from SuccessFactors conventions. Valid
`categoryId` values are not enumerable through the API — harvest them from `/go/`
links. Keyword ranking internals (analyzer, stemming, stopwords) are opaque.
Fields belonging to job families the portal is not currently advertising would be
invisible to the schema census, so expect additional `cust_*` fields over time.

Not covered: job detail content, application submission, job alerts, and
candidate profiles. `/services/recruiting/v1/` exposes only `jobs` — nine other
plausible paths return `404`.

## Collection etiquette

Public and unauthenticated, and this documents the portal's own front-end
traffic. Still a single municipality's careers site — the whole corpus is a few
requests, and `/sitemap.xml` advertises `changefreq: daily`. Poll gently.
