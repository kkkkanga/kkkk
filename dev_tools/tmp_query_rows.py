import json
from db import engine
import sqlalchemy as sa

def main():
    with engine.connect() as conn:
        cnt = conn.execute(sa.text("select count(*) from daily_sheet_rows where sheet_date='2025-09-28'"))
        print('count:', cnt.scalar())
        q2 = "select id, sheet_date, site, customer_name, phone, custom_values from daily_sheet_rows where sheet_date='2025-09-28' limit 5"
        res = conn.execute(sa.text(q2))
        rows = [dict(r) for r in res.mappings().all()]
    print(json.dumps(rows, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
