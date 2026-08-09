# Karriere München Job Search API

Reverse-engineered documentation of the job search API behind
`karriere-muenchen.jobs.hr.cloud.sap`, the Landeshauptstadt München careers
portal (SAP SuccessFactors Recruiting Marketing / "Career Site Builder").

- **[`openapi.yaml`](openapi.yaml)** — OpenAPI 3.1 spec (validates clean under `redocly lint`)
- **[`FUZZING.md`](FUZZING.md)** — Schemathesis validation: 9 spec defects fixed, 6 API defects found
- **[`fetch_jobs.py`](fetch_jobs.py)** — working reference client (stdlib only, no deps)

```bash
./fetch_jobs.py                              # all 30 postings, as a table
./fetch_jobs.py --json                       # same, as JSON (ISO dates, detail URLs)
./fetch_jobs.py --facets                     # facet counts
./fetch_jobs.py Erzieher                     # keyword search
./fetch_jobs.py --facet cust_BerufGr=Lehrberufe
```

All findings below were verified against the live API on **2026-08-09**.

---

## Why your requests returned no results

Short answer: **the API was working correctly the whole time.** Your requests
were fine mechanically — all eight returned HTTP 200. They matched nothing
because of what was *in* the payloads. There are three independent causes, and
most of your calls hit more than one at once.

I replayed all eight of your job-API bodies verbatim; every one still returns
`{"totalJobs": 0}` today, for the reasons below.

### Cause 1 — `keywords: "*"` is not a wildcard

`*` is treated as a literal search term and matches nothing. The match-all query
is the **empty string**:

```jsonc
{"keywords": "*"}   // → totalJobs: 0
{"keywords": ""}    // → totalJobs: 30   ← this is "everything"
```

This alone explains the two `"*"` calls, which were presumably your control
group for "is the API alive?". That control reported a false negative, which is
what made the whole set look broken.

Related gotcha: matching is **token-prefix**, not substring. `Arbeit` matches 16
postings, but `rbeit` and `beit` match zero. A trailing `*` is stripped rather
than expanded, so `Arbeit*` ≡ `Arbeit`. Matching is case-insensitive.

### Cause 2 — the portal has no IT jobs, and no "Sachbearbeiter" jobs

`keywords: "it"` and `keywords: "Sachbearbeiter"` genuinely match zero postings.
There are only **30 open positions on the entire portal**, and none is an IT or
clerical-administration role. The complete occupational-group breakdown:

| Occupational group (`cust_BerufGr`) | Jobs |
|---|---:|
| Erziehungsberufe | 9 |
| Handwerklich-technische Berufe | 8 |
| Lehrberufe | 7 |
| Sonstige Berufe | 5 |
| Ingenieure & Architekten | 1 |

There is no IT category at all. The 30 postings are teachers, educators, cooks,
winter-service street cleaners, theatre lighting technicians, and vehicle
mechanics.

`categoryId: 8927001` ("IT & Telekommunikation", from your `/go/` referer) is a
**valid** category id that currently contains **zero** postings — the landing
page still exists, but nothing is staffed under it. A category filter that
matches nothing is indistinguishable from a broken one in the response.

### Cause 3 — `locale` other than `de_DE` silently returns zero

This portal publishes exclusively in German. `en_US` returns `{"totalJobs": 0}`
with no error — as does a syntactically invalid locale like `xx_YY`. Your calls
correctly used `de_DE`, so this wasn't your problem, but it's the same failure
signature and worth knowing.

### What was *not* the problem: authentication

The `X-CSRF-Token` and `JSESSIONID` you carefully copied out of the browser are
**not validated at all**. Verified three ways:

| Sent | Result |
|---|---|
| No cookie, no CSRF token | `200` + all 30 jobs |
| Bogus CSRF token (`deadbeef-…`) | `200` + all 30 jobs |
| Stale `JSESSIONID` from your capture | `200` + all 30 jobs |

So the endpoint is fully anonymous, and a stale token is never the explanation
for an empty result here. Worth stating plainly because that's the natural first
suspicion when a copied-from-DevTools request comes back empty — it's a reasonable
hypothesis that happens to be wrong on this portal.

### Proof

```bash
# your original query — genuinely zero matches
curl -s https://karriere-muenchen.jobs.hr.cloud.sap/services/recruiting/v1/jobs \
  -H 'Content-Type: application/json' \
  -d '{"locale":"de_DE","pageNumber":0,"keywords":"it","location":"","facetFilters":{},"categoryId":0}'
# → {"totalJobs":0}

# same request, empty keywords, no auth headers at all
curl -s https://karriere-muenchen.jobs.hr.cloud.sap/services/recruiting/v1/jobs \
  -H 'Content-Type: application/json' \
  -d '{"locale":"de_DE","pageNumber":0,"keywords":"","location":"","facetFilters":{},"categoryId":0}'
# → {"jobSearchResult":[...10 jobs...],"totalJobs":30}
```

---

## Quick reference

One endpoint: **`POST /services/recruiting/v1/jobs`**, `Content-Type:
application/json`, no authentication. `GET` → `405`, form encoding → `415`.

Only `locale` is required.

```bash
curl -s https://karriere-muenchen.jobs.hr.cloud.sap/services/recruiting/v1/jobs \
  -H 'Content-Type: application/json' \
  -d '{"locale":"de_DE","pageNumber":0,"keywords":"","location":"","facetFilters":{},"categoryId":0}'
```

### Two modes

| Mode | Trigger | Returns |
|---|---|---|
| Search | `facetingOnly` absent/`false` | `jobSearchResult[]` + `totalJobs` |
| Faceting | `facetingOnly: true` | `facetFields[]` + `totalJobs`, **no job records** |

### Traps worth knowing

- **`200` does not mean "found something", and `totalJobs: 0` does not mean
  "error".** Errors carry `400` (`BadRequest`, `InvalidField`, `InternalError`)
  or `500`, each with an `error` object. Branch on the presence of `error`, not
  on `totalJobs == 0`.
- **Zero results are ambiguous.** An unpublished locale, an empty category, and a
  genuinely non-matching keyword all produce an identical `200 {"totalJobs":0}`
  with no error. Keep a known-good `keywords: ""` query as your control.
- **A `302` returns HTML, not JSON**, so a JSON parser will throw rather than
  hand you an error object. It signals a payload the server could not
  deserialize — see the table below.
- **`jobSearchResult` is omitted entirely on zero hits** — it is not an empty
  array. Guard accordingly.
- **Page size is hard-fixed at 10.** `pageSize`, `limit`, `size` and
  `itemsPerPage` are all silently ignored. Page until `jobSearchResult` is absent.
- **`totalJobs` is the grand total**, not the count in the current page.
- **`facetFilters` values must be arrays.** A bare string yields an HTTP `302` to
  an error page instead of JSON. Multiple facet keys combine with **AND**.
- **`categoryId: 0` means "no filter"**, not a real category.
- **Two date formats coexist in one object**: `unifiedStandardStart` /
  `unifiedStandardEnd` are `DD.MM.YY` (two-digit year), while
  `cust_Bewerbungsfrist` is `DD.MM.YYYY`. Neither is ISO — parse explicitly, or a
  generic parser will read `01.02.26` as February under US conventions.
- **`cust_BerufGr` values are often duplicated** (`["Lehrberufe","Lehrberufe"]`).
  De-duplicate before display.
- **`sortBy` does nothing** (see caveats). Sort client-side.
- **`keywords` and `location` are capped at 250 characters**; 251 returns `400`.
- **Strip C0 control characters from `keywords`** — they return
  `400 InternalError`. (Tab, LF and DEL are harmless.)
- **`pageNumber` and `categoryId` are 32-bit signed.** Beyond 2³¹ they `302`;
  `pageNumber`'s offset arithmetic overflows well before that and silently
  returns *wrong pages*.
- **Three ways to crash the server with a `500`** — all avoidable, all detailed
  in [`FUZZING.md`](FUZZING.md):
  1. a non-numeric `alertId` in search mode;
  2. **any** non-empty `alertId` under `facetingOnly: true`;
  3. an unknown positive `categoryId` under `facetingOnly: true`.

  Omit `alertId` (or send `""`), and keep faceting requests on `categoryId: 0`.

### Status codes at a glance

| Status | Content-Type | Cause |
|---|---|---|
| `200` | `application/json` | Query ran (may legitimately have matched nothing) |
| `400` | `application/json` | `BadRequest` (no `locale`, or a field over 250 chars) · `InvalidField` (unconfigured facet) · `InternalError` (control char in `keywords`) |
| `400` | *none, empty body* | Body absent or not decodable as text |
| `302` | `text/html` | Deserialization failure: wrong JSON type, `pageNumber`/`categoryId` ≥ 2³¹, empty-string property name, malformed JSON |
| `500` | `application/json` | Server-side crash — see the three `alertId`/`categoryId` cases above |
| `405` / `415` | — | Not `POST` / not `application/json` |

### Building a job detail URL

```
/job/{urlTitle}/{id}-{locale}/
```

e.g. `/job/x/24289-de_DE/` — the slug segment is cosmetic, any placeholder
resolves. `urlTitle` is already percent-encoded; do not re-encode it.

Do **not** confuse the API's `id` (a requisition id, e.g. `24289`) with the
longer posting ids in `/sitemap.xml` (e.g. `1287679301`). They are different id
spaces, and the sitemap ids are not obtainable from this API. A URL without the
`-{locale}` suffix expects a posting id and errors out for a requisition id.

### Facets

Only two facet fields are configured on this portal — `cust_BerufGr`
(occupational group) and `cust_ReferatKarriereseite` (municipal department).
Anything else returns `InvalidField: Field not configured in facets`.

Faceting honours the active query, so it doubles as a cheap way to discover
valid filter values before running a search.

---

## Coverage caveats

This spec was produced by black-box probing, not from SAP documentation. Being
explicit about what that does and does not establish:

**Confirmed live** (observed directly, 2026-08-09):

- Endpoint, method, content-type requirement, and every status code in the table
  above, each reproduced deterministically
- Absence of authentication (three negative controls)
- Both request modes and the full response envelope
- Property-based validation: **247/247 generated positive cases pass** across
  five seeds (Schemathesis, all checks). See [`FUZZING.md`](FUZZING.md)
- Effects of `locale`, `pageNumber`, `keywords`, `location`, `categoryId`,
  `facetFilters`, `facetingOnly`, `facetFields`
- Fixed page size of 10, across four rejected override spellings
- All four error codes (`BadRequest`, `InvalidField`, `InternalError`, `Error`)
- `pageNumber` is 32-bit signed; the offset arithmetic overflows well below that
- Every `Job` field below, from a **census of all 30 published postings** — so
  the required/optional split is exact *for the current corpus*
- The `/job/{urlTitle}/{id}-{locale}/` URL pattern

**Inferred or unresolved** — treat with caution:

- **`sortBy` is undocumented and appears inert.** Six values (including a
  deliberately bogus one) produced identical ordering, and invalid input is not
  rejected. I could not establish whether it is unimplemented on this portal or
  needs a value I did not guess. Do not assume result order is stable.
- **`brand`, `skills`, `alertId`, `rcmCandidateId` have no observable effect**
  here. Their documented purpose is inferred from SuccessFactors RMK
  conventions; they are plausibly functional on portals that enable those
  features.
- **The `Job` schema reflects one 30-posting snapshot.** Fields used only by job
  families the portal is not currently advertising — notably anything IT-related
  — would be invisible to this census. Expect additional `cust_*` fields to
  appear over time. The spec sets `additionalProperties: true` accordingly.
- **`categoryId` values are not enumerable** through this API. Harvest them from
  `/go/` links on the site. `8927001` is known-valid but currently empty.
- **Keyword ranking internals are opaque.** Prefix-matching and
  case-insensitivity are demonstrated; the analyzer, stemming rules, German
  compound decomposition, and stopword list are not. Some single letters behave
  unintuitively (`a` → 27, `i` → 0), consistent with a German-language analyzer
  over full description text, but I did not confirm the mechanism.
- **`unifiedUrlTitle` vs `urlTitle`** were identical on all 30 postings. The
  distinction presumably matters on multi-locale portals; unverifiable here.
- Field *semantics* for München-specific `cust_*` fields are read from their
  German names and observed values, not from a data dictionary.

**Not covered at all:** job detail content, application submission, job alerts,
and candidate-profile endpoints. Probing confirmed that `/services/recruiting/v1/`
exposes **only** `jobs` — nine other plausible paths (`jobs/{id}`, `categories`,
`locations`, `typeahead`, `jobalerts`, …) all return `404`. Detail pages are
server-rendered HTML and would need separate scraping.

---

## Note on collection etiquette

The portal is public and unauthenticated, and this documents its own front-end's
traffic. Still, it's a single municipality's careers site with 30 postings —
poll it gently. The whole corpus is three requests; there is no reason to hit it
more than a few times an hour. `/sitemap.xml` advertises `changefreq: daily`,
which is a fair guide to how often the data actually moves.
