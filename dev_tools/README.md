Moved helper scripts

These scripts were relocated from the repository root to keep the workspace cleaner.
Run them with:

    python dev_tools/<script>.py

Files:

- tmp_query_rows.py - query sample rows
- tmp_show_sheet.py - show daily_sheets row
- tmp_show_sheet_safe.py - show daily_sheets with date serialization
- tmp_inspect_cols.py - inspect daily_sheets columns
- tmp_inspect_rows_cols.py - inspect daily_sheet_rows columns
- psycopg_test.py - psycopg2 connection diagnostics
- psycopg_test2.py - basic psycopg2 connect attempts
- connect_db.py - test DB connect using .env DATABASE_URL

Original root-level scripts were replaced with small stubs that point to these files.
