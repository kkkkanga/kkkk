import json
from db import engine
import sqlalchemy as sa

def main():
    with engine.connect() as conn:
        q = """
        select column_name, data_type
        from information_schema.columns
        where table_name = 'daily_sheets'
        order by ordinal_position
        """
        res = conn.execute(sa.text(q))
        rows = [dict(r) for r in res.mappings().all()]
    print(json.dumps(rows, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
