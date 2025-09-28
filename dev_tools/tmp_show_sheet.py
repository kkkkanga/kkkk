import json
from db import engine
import sqlalchemy as sa

def main():
    with engine.connect() as conn:
        q = "select date, version, top, headers, stats, footer, option_cols, sheet_hash from daily_sheets where date='2025-09-28'"
        res = conn.execute(sa.text(q))
        rows = [dict(r) for r in res.mappings().all()]
    print(json.dumps(rows, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
