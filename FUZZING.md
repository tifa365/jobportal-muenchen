# Fuzzing the spec with Schemathesis

Property-based validation of [`openapi.yaml`](openapi.yaml) against the live API,
using [Schemathesis](https://schemathesis.readthedocs.io/) 4.24.3. Run on
**2026-08-09**.

The exercise is bidirectional: Schemathesis generates requests *from* the spec
and validates responses *against* it, so a mismatch indicts either side. It found
faults on both — **9 defects in my hand-written spec**, all fixed, and **6 defects
in the API**, documented rather than worked around since they are not ours to fix.

Two of the API defects are reproducible HTTP 500 crashes that hand-probing had
missed entirely.

## Result

```
Phases: Examples ✅   Coverage ✅   Fuzzing ✅
247 generated, 247 passed — No issues found
```

Clean across five independent seeds (5, 7, 23, 41, 99) with all checks enabled:
`status_code_conformance`, `content_type_conformance`,
`response_schema_conformance`, `response_headers_conformance`,
`positive_data_acceptance`, `not_a_server_error` and the rest.

## Reproducing

```bash
python3 -m venv venv && ./venv/bin/pip install schemathesis

./venv/bin/schemathesis run openapi.yaml \
  --url https://karriere-muenchen.jobs.hr.cloud.sap \
  --phases examples,coverage,fuzzing --checks all -m positive \
  -n 150 --rate-limit 3/s -w 1 --max-redirects 0 --seed 7
```

Two flags are load-bearing:

- **`--max-redirects 0`** — the `302` *is* the documented contract for a payload
  the server cannot deserialize. Following it lands on an HTML error page served
  as `200`, which the tool then reports as a bogus "undocumented content type on
  200" while hiding the real behaviour.
- **`--rate-limit 3/s -w 1`** — this is a municipality's production careers site
  with 30 postings, not a test fixture. A full run is ~250 requests over ~90
  seconds. Please leave these in.

Negative mode (`-m all` / `-m negative`) deliberately violates the schema and will
report the API defects in the second section. That is expected, not a regression.

## Spec defects found and fixed

### 1. Errors are not HTTP 200 — the spec claimed they were

The original spec asserted that this API "returns HTTP 200 for application-level
errors" and instructed callers to ignore the status code entirely. **That was
wrong.** The API uses `400` and `500` correctly.

It was wrong because of *how* I probed: I had piped every response body straight
into a JSON parser and never once looked at a status line. Seeing
`{"error":{...},"totalJobs":0}` come back, I inferred a `200` and then wrote that
inference up as a documented contract. Fuzzing caught it on the first run via
`status_code_conformance`, which checks the status line whether or not the person
driving it thought to.

The general hazard: **an inspection method that structurally cannot observe a
dimension will report that dimension as uniform.** Nothing in body-only evidence
hints that status codes are being missed — the gap is invisible from inside the
method. It took a tool with a different default view to expose it.

### 2. `facetFilters` keys were unconstrained

The API validates filter *keys* against configured facet fields, exactly as it
does `facetFields`, rejecting unknown ones with `400 InvalidField`. The spec
allowed anything. Surfaced as `positive_data_acceptance` failures — schema-valid
requests the API refused. Both now share a `FacetFieldName` enum.

### 3. `locale: ""` was permitted

An empty string is treated as absent and rejected with
`BadRequest: locale cannot be null`. Now `minLength: 1`.

### 4. Empty-string property names were permitted

`additionalProperties: true` let the generator emit a `""` key, which breaks
deserialization (`302`). Unknown keys with ordinary names are ignored harmlessly —
only the empty name breaks. Now guarded by `propertyNames: {minLength: 1}`.

### 5 & 6. `pageNumber` and `categoryId` were unbounded

Both are **32-bit signed**: `2147483647` is accepted, `2147483648` yields `302`.

`pageNumber` is worse than a clean boundary — the offset arithmetic
(`pageNumber × 10`) overflows well below the type limit, so large values return
**wrong pages instead of an error**; `pageNumber: 2147483647` returns what looks
like page 0.

`categoryId` is the more interesting of the two, because of how it hid — see the
next section.

### 7. `keywords` and `location` were missing their length cap

Both are capped at **250 characters**; 251 returns
`400 BadRequest: Maximum criteria string length allowed in queryList for each
field is 250`. Now `maxLength: 250`.

### 8. My own `alertId` regex was broken — twice

First attempt, `^\s*|[+-]?[0-9]+\s*$`, is an unanchored alternation. The left
branch `^\s*` matches the empty prefix of *any* string, so the pattern admitted
everything and constrained nothing. It looked plausible and did nothing.

Second attempt, `^(\s*|[+-]?[0-9]+)$`, was a correct regex but still leaked.
**`\s` denotes a wider set in Python's `re` than in ECMA-262**, the dialect JSON
Schema specifies. Python's `\s` matches U+0085 (NEL); ECMA-262's does not.
Schemathesis generates data with Python's engine, duly produced an `alertId` of
two U+0085 characters, and hit the very crash the pattern existed to prevent.

Now `^(|[+-]?[0-9]+)$` — no `\s` at all. Generally: **avoid `\s`, `\w` and `\b`
in JSON Schema `pattern`s** when generated data must be safe, because the
generator's dialect may be more permissive than the one you had in mind.

### 9. Faceting mode's extra constraints were undocumented

Faceting mode rejects inputs search mode accepts (API defects 2 and 3 below). Now
encoded as an `if`/`then` at the schema root. Because `if`/`then` leaves the named
properties "unevaluated" under strict 2020-12 semantics, the schema also declares
`unevaluatedProperties: true`.

## A finding disguised as a network error

Worth separating out, because it nearly escaped.

Every run reported one `Network Error` alongside its passes. I wrote it off in an
earlier draft of this document as portal-side flakiness — a plausible read, since
the site is a small municipal deployment and one blip per few hundred requests is
unremarkable.

It was not flakiness. The full text was **`Exceeded 0 redirects`**: a schema-valid
request had produced a `302`, and because `--max-redirects 0` makes the HTTP
client *raise* rather than return, Schemathesis classified it as a transport error
rather than a check failure. Errors are reported separately from failures and do
not fail the run, so a genuine spec gap sat in plain sight wearing the costume of
a network hiccup.

Recovering it meant recording traffic to a HAR (`--report har`) with redirects
allowed, then pulling the offending payload out of the archive: `categoryId`
above 2³¹, defect 6 above.

The lesson is about triage, not tooling. **The bucket a harness labels
"infrastructure" is exactly where real findings hide**, because it is the bucket
everyone is trained to skip. The tell here was that it recurred at a stable rate
across every run and every seed — genuine flakiness would not have been so
punctual.

## API defects found

Upstream faults in the SuccessFactors deployment, documented in the spec rather
than worked around. They will keep appearing in negative-mode runs.

### 1. Non-numeric `alertId` crashes the endpoint (HTTP 500)

In search mode, any non-empty, non-numeric `alertId` returns
`500 {"error":{"code":"Error","message":"Error retrieving jobs"}}`. Deterministic
— 5/5 identical across repeats.

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  https://karriere-muenchen.jobs.hr.cloud.sap/services/recruiting/v1/jobs \
  -H 'Content-Type: application/json' \
  -d '{"locale":"de_DE","pageNumber":0,"keywords":"","facetFilters":{},"alertId":"abc"}'
# → 500
```

| `alertId` in search mode | Result |
|---|---|
| `""`, `" "`, `"0"`, `"123"`, `"-1"`, `0` | `200` |
| `"abc"`, `"12x"`, `true`, `false`, a lone U+0084 or U+0085 | **`500`** |
| `[]`, `{}` | `302` |

The value is parsed as a number with the parse failure unhandled. `400` would be
correct.

### 2. In faceting mode, *any* non-empty `alertId` crashes

Stricter than search mode, and inconsistent with it: under `facetingOnly: true`
even well-formed numeric values crash.

```bash
# minimal reproducer, reduced from a 1077-byte generated payload
curl -s -o /dev/null -w '%{http_code}\n' \
  https://karriere-muenchen.jobs.hr.cloud.sap/services/recruiting/v1/jobs \
  -H 'Content-Type: application/json' -d '{"alertId": 0, "facetingOnly": true}'
# → 500
```

Note the reproducer omits `locale` — the crash preempts the `400` that a missing
`locale` would otherwise produce.

### 3. In faceting mode, an unknown `categoryId` crashes

Search mode treats an unknown category as an ordinary empty result. Faceting mode
crashes on it:

| `categoryId` under `facetingOnly: true` | Result |
|---|---|
| `0`, or any negative value | `200` |
| `8927001` (a category that exists) | `200` |
| `1`, `12345`, `2147483647` (unknown) | **`500`** |

This one has practical bite: valid category ids **cannot be discovered through the
API**, so any caller enumerating facets across categories is one stale id away
from a 500. The only reliably safe faceting request is `categoryId: 0`.

### 4. Type violations are silently accepted

`rcmCandidateId: null`, `brand: false`, `sortBy: false` and similar all return
`200` with full results rather than `400`. The server coerces or ignores instead
of validating, so **malformed client code fails silently rather than loudly** — a
wrong-typed value yields plausible results, not an error. Flagged as
`negative_data_rejection`.

### 5. Undecodable bodies return a bodyless 400

A body that is not decodable text (a bare NUL byte, or an empty body) returns
`400` with **no `Content-Type` header and no body at all**, so a client assuming
"4xx implies a JSON error object" throws while parsing.

### 6. `keywords` control characters return a misleading code

C0 control characters in `keywords` return `400` with code `InternalError` and
message `Internal error`. The status is right, but code and message suggest a
server fault when it is ordinary input rejection. Tab, LF and DEL pass through.

## Notes and honest limits

- **`redocly lint` reports 7 warnings, which are false positives.** Its
  `no-invalid-media-type-examples` rule mishandles `if`/`then`, reporting every
  property of the `facetingOnly` example as "unevaluated". Cross-checked against
  the reference `jsonschema` Draft 2020-12 validator: all four request examples
  validate, and six negative controls (faceting + `alertId`, faceting +
  `categoryId`, empty `locale`, empty-string key, `categoryId` ≥ 2³¹, 251-char
  `keywords`) are correctly rejected. The spec lints with **0 errors**.
- **Two schema constraints are deliberately stricter than the API.** The `alertId`
  pattern excludes whitespace the server tolerates, and the faceting-mode
  `categoryId: maximum 0` guard excludes real category ids that are perfectly
  valid. Both exist to keep generated data away from the 500s, and both are
  labelled in the spec as generation guards rather than as the real contract.
- **Schemathesis validates conformance, not correctness.** It confirms responses
  match the documented shape. It cannot tell you `sortBy` is inert, that `"*"` is
  not a wildcard, or that `cust_BerufGr` arrives with duplicated values — those
  came from hand-probing and remain the substance of the documentation.
- **A passing positive run does not mean the spec is complete.** It means nothing
  generated from the spec contradicted the API. Response fields absent from the
  current 30-posting corpus are invisible to it.
- **`sortBy`, `brand`, `skills` and `rcmCandidateId` remain uncharacterised.**
  Accepted and inert here; fuzzing cannot distinguish "unimplemented" from "needs
  an input nobody guessed".
- Runs are capped and rate-limited by choice. Deeper campaigns
  (`--generation-maximize response_time`, stateful phases) were not run — more
  traffic to a small public site for little additional insight.
