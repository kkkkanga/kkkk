# Normalize bookings helper

This document explains the `normalize_bookings` helper in `day.py` and how to run the small tests.

What it does
- Ensures each booking row matches the API expectations:
  - `관리메모` -> list of strings
  - `같이온사이트` -> list of strings
  - money/number fields (`총 이용료`, `현장결제 금액`, `선결제 금액`, `예약 인원`) -> strings
  - common fields (`사이트`, `고객명`, `연락처`, `예약일`, `차량`, `요청사항`) exist and are strings

Run tests (locally)

1. Create a virtualenv and install test deps if needed. The project already has `requirements.txt`.

2. Run pytest from project root (PowerShell):

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -r requirements.txt; pip install pytest
pytest -q
```

Notes / operational tips
- The extraction step (browser login to admin site) may be blocked by Cloudflare. If `day.py` fails to extract due to Cloudflare, perform a manual login in Chrome and save cookies to `C:\app\camfit_cookies.json` (existing helper scripts in `scripts/` can assist). Then re-run the scraping.
- The push flow includes automatic server-version conflict handling (409 -> GET server -> server-first merge -> retry). If retry still fails, a conflict backup file is saved under project cwd.
