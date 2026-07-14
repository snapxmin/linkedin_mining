# LinkedIn Talent Mining Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a compliant, locally runnable OKX talent-research application that discovers public search results, imports and reviews profiles, reports distributions, and exports normalized data.

**Architecture:** A FastAPI application serves JSON endpoints and Jinja pages backed by SQLite. Focused service modules own profile normalization, CSV parsing, search-provider integration, and analytics; all external discovery goes through an injectable provider so tests never call LinkedIn.

**Tech Stack:** Python 3.11+, FastAPI, Uvicorn, Jinja2, python-multipart, HTTPX, SQLite, pytest

---

## File Map

- `pyproject.toml`: package metadata, runtime dependencies, pytest configuration.
- `app/db.py`: SQLite connection, schema creation, and transaction helpers.
- `app/profiles.py`: profile validation, URL normalization, fingerprints, and upsert logic.
- `app/search.py`: query generation, provider protocol, demo and Serper providers.
- `app/imports.py`: atomic CSV parsing and import.
- `app/analytics.py`: verified/all aggregation queries.
- `app/main.py`: FastAPI routes and application wiring.
- `app/templates/index.html`: project creation and project list.
- `app/templates/project.html`: search, import, review table, charts, and export.
- `app/static/app.js`: dashboard API interactions and chart rendering.
- `app/static/style.css`: responsive dashboard presentation.
- `tests/`: unit and API behavior tests.
- `README.md`: setup, use, CSV contract, configuration, and compliance limits.

### Task 1: Project skeleton and database

**Files:**
- Create: `pyproject.toml`
- Create: `app/__init__.py`
- Create: `app/db.py`
- Create: `tests/test_db.py`

- [ ] Write a failing test that creates a temporary database, calls `init_db(path)`, and asserts the `projects`, `search_runs`, and `profiles` tables plus the profile uniqueness index exist.
- [ ] Run `python -m pytest tests/test_db.py -q`; expect failure because `app.db` does not exist.
- [ ] Implement `connect(path)`, `transaction(path)`, and `init_db(path)` with foreign keys enabled, WAL mode, timestamp fields, status checks, and a unique `(project_id, dedupe_key)` constraint.
- [ ] Re-run the test and confirm it passes.
- [ ] Commit with `feat: initialize sqlite data model`.

### Task 2: Profile normalization and deduplication

**Files:**
- Create: `app/profiles.py`
- Create: `tests/test_profiles.py`

- [ ] Write failing tests proving LinkedIn URLs lose query strings/trailing slashes, non-HTTP URLs are rejected, URL-backed dedupe keys are stable, no-URL keys use normalized name/company/role, and importing the same profile twice updates rather than duplicates it.
- [ ] Run `python -m pytest tests/test_profiles.py -q`; expect import or assertion failures.
- [ ] Implement `normalize_url(value)`, `profile_dedupe_key(data)`, `validate_profile(data)`, `upsert_profile(conn, project_id, data)`, `list_profiles(...)`, and `update_profile(...)`.
- [ ] Re-run profile tests and the full suite.
- [ ] Commit with `feat: add normalized profile storage`.

### Task 3: Atomic CSV import

**Files:**
- Create: `app/imports.py`
- Create: `tests/test_imports.py`

- [ ] Write failing tests for accepted header aliases, UTF-8 BOM, numeric years, missing required identity fields, invalid row numbers, all-or-nothing validation, and duplicate updates.
- [ ] Run `python -m pytest tests/test_imports.py -q`; expect failure because CSV import is missing.
- [ ] Implement `parse_csv(content)` returning normalized profile dictionaries and `import_csv(conn, project_id, content)` that validates the complete batch before transactionally upserting.
- [ ] Re-run import tests and the full suite.
- [ ] Commit with `feat: add atomic profile csv import`.

### Task 4: Search providers and metadata extraction

**Files:**
- Create: `app/search.py`
- Create: `tests/test_search.py`

- [ ] Write failing tests for quoted company query generation, optional role keywords, extraction of names/roles from common result titles, demo results, Serper payload mapping, and provider errors that do not create profiles.
- [ ] Run `python -m pytest tests/test_search.py -q`; expect failure because search services are missing.
- [ ] Implement `build_queries(company, roles)`, `extract_result(result, company)`, `DemoSearchProvider`, `SerperSearchProvider`, and `run_search(conn, project_id, provider, roles)`. Serper must read `SERPER_API_KEY`, use a timeout, and never fetch result profile URLs.
- [ ] Re-run search tests and the full suite.
- [ ] Commit with `feat: add compliant public search discovery`.

### Task 5: Analytics and CSV export

**Files:**
- Create: `app/analytics.py`
- Create: `tests/test_analytics.py`

- [ ] Write failing tests showing default aggregates include only verified records, rejected records never count, `scope=all` includes pending records, blank dimensions are labelled `Unknown`, ordering is count descending then label ascending, and CSV export uses the same scope.
- [ ] Run `python -m pytest tests/test_analytics.py -q`; expect missing functions.
- [ ] Implement `aggregate(conn, project_id, scope)`, `summary(conn, project_id, scope)`, and `export_csv(conn, project_id, scope)`.
- [ ] Re-run analytics tests and the full suite.
- [ ] Commit with `feat: add profile analytics and export`.

### Task 6: FastAPI routes

**Files:**
- Create: `app/main.py`
- Create: `tests/conftest.py`
- Create: `tests/test_api.py`

- [ ] Write failing API tests for health, project creation validation, project detail, demo search, CSV upload, paginated profiles, profile review updates, analytics scopes, 404s, and CSV download headers.
- [ ] Run `python -m pytest tests/test_api.py -q`; expect failure because the application is missing.
- [ ] Implement an application factory `create_app(database_path=None, search_provider=None)` and the documented API routes. Limit uploads to 5 MiB and page size to 100.
- [ ] Re-run API tests and the full suite.
- [ ] Commit with `feat: expose talent research api`.

### Task 7: Research dashboard

**Files:**
- Create: `app/templates/base.html`
- Create: `app/templates/index.html`
- Create: `app/templates/project.html`
- Create: `app/static/app.js`
- Create: `app/static/style.css`
- Create: `tests/test_web.py`

- [ ] Write failing tests asserting `/` renders project creation/listing and `/projects/{id}` renders company context, search/import controls, review table, sample scope, charts, and export action.
- [ ] Run `python -m pytest tests/test_web.py -q`; expect template route failures.
- [ ] Implement accessible server-rendered pages and small JavaScript handlers for API calls, pagination, editing/reviewing profiles, and horizontal count bars.
- [ ] Re-run web tests and the full suite.
- [ ] Commit with `feat: add talent research dashboard`.

### Task 8: Documentation and final verification

**Files:**
- Modify: `README.md`
- Create: `.env.example`
- Create: `sample_data/okx_profiles.csv`

- [ ] Document installation, `uvicorn app.main:app --reload`, the demo workflow, Serper configuration, CSV columns, export behavior, test command, 1,000-record guidance, and explicit prohibited-use boundaries.
- [ ] Add a safe sample CSV with fictional records and no real personal data.
- [ ] Run `python -m pytest -q`; expect all tests to pass without warnings.
- [ ] Run `python -m compileall -q app`; expect exit code 0.
- [ ] Start the server and verify `/health` returns `{"status":"ok"}`.
- [ ] Run a 1,000-row import/aggregation smoke test and confirm deduplication and pagination remain correct.
- [ ] Commit with `docs: add setup and compliance guidance`.
