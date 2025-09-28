Title: Fix normalize_bookings recursion & add CI for tests

Summary
-------
This PR fixes a recursion bug in `normalize_bookings()` (in `day.py`) that caused RecursionError during unit tests. It centralizes and implements proper normalization for rows (ensuring `관리메모` is a list, `같이온사이트` is a list of strings, numeric/money fields are strings, and common display fields are string-typed). The PR also adds a minimal GitHub Actions workflow to run the test suite on push and pull requests.

Files changed / added
--------------------
- `c:\booking\day.py`
  - Replaced recursive call inside `normalize_bookings()` with an actual normalization implementation.

- `c:\booking\.github\workflows\python-tests.yml`
  - New CI workflow: installs dependencies from `requirements.txt` (if present) and runs `pytest` on Python 3.11.

- Tests
  - Existing tests under `c:\booking\tests` were used to validate behavior. `tests/test_normalize_bookings.py` passes locally.

How tested
----------
- Ran `pytest tests/test_normalize_bookings.py` locally; 2 tests passed.
- Ran a local smoke test of the test suite (normalization-focused tests) using the configured Python environment.

Notes and follow-ups
--------------------
- CI will run on GitHub (Ubuntu runner by default). If you want Windows or a matrix of Python versions, I can extend the workflow.
- There are other integration flows in the repository that spawn a browser (Selenium). CI currently runs the entire test suite; if you prefer to avoid running Selenium-dependent tests on CI, we can tag them or adjust the workflow to run only unit tests.
- Operationally, the app still requires `NAS_FOLDER` environment variable; CI sets `NAS_FOLDER` to the workspace root to avoid failures in tests that write to disk.

Suggested PR title
------------------
Fix normalize_bookings recursion + add CI for pytest

Suggested PR body (short)
-------------------------
Fix a recursion bug in `normalize_bookings()` and add a GitHub Actions workflow to run tests automatically on push and PRs. This makes normalization deterministic and prevents regressions.

If you want, I can also create the branch and open the PR for you (I will provide the exact git commands to run locally).
Title: Fix: align DB types (Date/ARRAY) and normalize inputs for update-daily-sheet

Summary

- Align SQLAlchemy models with the actual PostgreSQL schema: make `daily_sheets.date` and `daily_sheet_rows.sheet_date` use DATE.
- Convert `manage_memo` and `together_sites` to PostgreSQL `text[]` (SQLAlchemy `ARRAY(Text)`) and normalize incoming values to Python lists before DB insert.
- Harden `/api/update-daily-sheet` payload handling: parse `date` and `예약일` into `datetime.date` when possible, normalize list-like fields (관리메모/같이온사이트), compute `sheet_hash` consistently.
- Replace temporary debug prints in `day.py` with proper `log_debug` calls.
- Add a small integration script to verify POST->GET flow: `tests/integration_test_update_daily_sheet.py`.

Files changed (high-level)

- `db_models.py` — change types: Date for date columns, ARRAY(Text) for list columns.
- `api.py` — normalize incoming payloads, ensure proper types for DB insertion, upsert metadata and rows.
- `day.py` — remove/convert temporary debug prints.
- `tests/integration_test_update_daily_sheet.py` — integration test script for manual verification.
- misc: small diagnostic scripts used during debugging (not required for production).

How to test locally (Docker Compose)

1. Start the stack (if not running):

```powershell
docker compose up -d
```

2. Run the integration check inside the backend container (pytest not required):

```powershell
# run from project root on host
docker compose exec backend python tests/integration_test_update_daily_sheet.py
```

Expected

- POST `/api/update-daily-sheet` returns 200 and {ok: True, version: N, sheet_hash: "..."}
- GET `/api/daily-sheet?date=YYYY-MM-DD` returns 200 and the expected rows in `sheet` (rows count matches payload)

Notes / Caveats

- Model type changes were made to match the current DB schema observed during debugging. If your production DB has a different schema, please prepare and run a migration.
- We normalized inputs (dates and arrays) defensively; downstream code that relied on raw string shapes may need small adjustments.
- `gh` CLI is not available in the environment; create PR using the GitHub web link provided after pushing the branch.

Suggested PR Description (copy into GitHub PR body)

This PR fixes type mismatches and input normalization issues for the daily-sheet upload flow.

Problem:
- `sheet_date` and array-like fields were being bound as incorrect Python types (string/JSON) causing PostgreSQL type errors (DatatypeMismatch) during bulk insert.

Fix:
- Use SQLAlchemy Date for date columns and ARRAY(Text) for text[] columns.
- Normalize incoming JSON payloads to pass Python `datetime.date` and `list[str]` to SQLAlchemy inserts.
- Update `day.py` debug printing and add a simple integration test to verify upload + retrieval.

How reviewers can validate:
- Start the stack and run the integration check: see `tests/integration_test_update_daily_sheet.py`.
- Optionally run a local `day.py` run against the backend to exercise the E2E flow.


---

Generated by local dev tooling for reviewer convenience.
