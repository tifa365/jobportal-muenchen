#!/usr/bin/env python3
"""Fetch job postings from the Karriere München portal.

Reference client for the API documented in openapi.yaml. Standard library only.

Deliberately sends no cookie and no X-CSRF-Token: the endpoint is anonymous and
neither is validated.

Usage:
    ./fetch_jobs.py                  # all postings, as a table
    ./fetch_jobs.py --json           # all postings, as JSON
    ./fetch_jobs.py --facets         # facet counts only
    ./fetch_jobs.py Erzieher         # keyword search
    ./fetch_jobs.py --facet cust_BerufGr=Lehrberufe

Pass NO keyword to match everything. "*" is a literal search term, not a
wildcard, and matches nothing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request

BASE = "https://karriere-muenchen.jobs.hr.cloud.sap"
ENDPOINT = f"{BASE}/services/recruiting/v1/jobs"
LOCALE = "de_DE"
PAGE_SIZE = 10      # fixed server-side; pageSize/limit/size/itemsPerPage are ignored
MAX_PAGES = 100     # runaway guard
FACET_FIELDS = ["cust_BerufGr", "cust_ReferatKarriereseite"]


class ApiError(RuntimeError):
    pass


def post(payload: dict) -> dict:
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode()
    except urllib.error.HTTPError as exc:                   # 405 / 415
        raise ApiError(f"HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise ApiError(f"network failure: {exc.reason}") from exc

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        # A 302 to the error page means a structurally wrong field, e.g. a
        # facetFilters value passed as a bare string instead of an array.
        raise ApiError("response was not JSON (malformed request payload?)") from None

    # Application errors arrive as HTTP 200 with an `error` member, so the body
    # must be inspected on every response -- the status code alone is not enough.
    if err := data.get("error"):
        raise ApiError(f'{err.get("code")}: {err.get("message")}')
    return data


def search_page(keywords: str, page: int, facet_filters: dict) -> dict:
    return post({
        "locale": LOCALE,
        "pageNumber": page,
        "sortBy": "",                # no observable effect; sort client-side
        "keywords": keywords,        # "" matches everything
        "location": "",
        "facetFilters": facet_filters,
        "categoryId": 0,             # 0 means "no category filter"
    })


def fetch_all(keywords: str, facet_filters: dict) -> list[dict]:
    jobs: list[dict] = []
    for page in range(MAX_PAGES):
        data = search_page(keywords, page, facet_filters)
        # jobSearchResult is ABSENT (not an empty array) past the last page;
        # that absence is the only reliable stop condition.
        batch = data.get("jobSearchResult") or []
        if not batch:
            break
        jobs.extend(j["response"] for j in batch)
        if len(batch) < PAGE_SIZE:
            break
    return jobs


def fetch_facets(keywords: str) -> dict:
    # Faceting mode has two server-side crash triggers that search mode lacks:
    # any non-empty alertId, and an unknown positive categoryId both return 500.
    # Omitting alertId entirely and pinning categoryId to 0 avoids both.
    return post({
        "facetingOnly": True,
        "locale": LOCALE,
        "keywords": keywords,
        "location": "",
        "categoryId": 0,
        "facetFields": FACET_FIELDS,
        "facetFilters": {},
    })


_DATE_RE = re.compile(r"^(\d{2})\.(\d{2})\.(\d{2}(?:\d{2})?)$")


def to_iso(value: str | None) -> str:
    """Normalise the API's two coexisting date formats to ISO 8601.

    unifiedStandardStart/End are DD.MM.YY (two-digit year) while
    cust_Bewerbungsfrist is DD.MM.YYYY. Neither is ISO, and a generic parser
    would read 01.02.26 as February under US conventions.
    """
    if not value:
        return ""
    m = _DATE_RE.match(value.strip())
    if not m:
        return value
    day, month, year = m.groups()
    return f"{'20' + year if len(year) == 2 else year}-{month}-{day}"


def enrich(job: dict) -> dict:
    job["startISO"] = to_iso(job.get("unifiedStandardStart"))
    job["endISO"] = to_iso(job.get("unifiedStandardEnd"))
    job["deadlineISO"] = to_iso(job.get("cust_Bewerbungsfrist"))
    # cust_BerufGr routinely repeats its values, e.g. ["Lehrberufe","Lehrberufe"].
    job["berufGr"] = list(dict.fromkeys(job.get("cust_BerufGr") or []))
    # urlTitle is already percent-encoded -- do not re-encode. The slug segment
    # is cosmetic; the "{id}-{locale}" segment is what actually resolves.
    job["url"] = f'{BASE}/job/{job.get("urlTitle", "x")}/{job["id"]}-{LOCALE}/'
    return job


def print_table(jobs: list[dict]) -> None:
    print(f"{len(jobs)} jobs\n")
    for j in jobs:
        title = re.sub(r"\s+", " ", j.get("unifiedStandardTitle", "")).strip()
        pay = j.get("cust_EntgeltGr_Karriereseite") or j.get("cust_BesoldGr_Karriereseite") or []
        meta = [j.get("cust_ReferatEigen", "")]
        if pay:
            meta.append(", ".join(dict.fromkeys(pay)))
        if j["deadlineISO"]:
            meta.append(f'Frist {j["deadlineISO"]}')
        print(f'{j["id"]:>9}  {j["startISO"]:10}  {title[:70]}')
        print(f'{"":>9}  {" | ".join(m for m in meta if m)}')


def print_facets(data: dict) -> None:
    print(f'{data.get("totalJobs", 0)} jobs total\n')
    for field in data.get("facetFields") or []:
        print(field["name"])
        for value in field.get("values") or []:
            print(f'  {value["count"]:3d}  {value["name"]}')
        print()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("keywords", nargs="?", default="",
                    help='search terms; omit to match everything (not "*")')
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    ap.add_argument("--facets", action="store_true", help="show facet counts only")
    ap.add_argument("--facet", action="append", default=[], metavar="FIELD=VALUE",
                    help="filter by a facet value; repeatable (combines with AND)")
    args = ap.parse_args()

    # facetFilters values must be arrays -- a bare string yields a 302, not JSON.
    facet_filters: dict[str, list[str]] = {}
    for raw in args.facet:
        field, _, value = raw.partition("=")
        if not value:
            ap.error(f"--facet expects FIELD=VALUE, got {raw!r}")
        facet_filters.setdefault(field, []).append(value)

    try:
        if args.facets:
            print_facets(fetch_facets(args.keywords))
            return 0

        jobs = [enrich(j) for j in fetch_all(args.keywords, facet_filters)]
    except ApiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        json.dump(jobs, sys.stdout, ensure_ascii=False, indent=2)
        print()
    elif not jobs:
        # Zero results are ambiguous: no keyword match, an empty category, and a
        # locale the portal does not publish all look identical.
        print("No jobs matched. Note that \"*\" is not a wildcard -- run with no "
              "keyword to list everything.")
    else:
        print_table(jobs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
