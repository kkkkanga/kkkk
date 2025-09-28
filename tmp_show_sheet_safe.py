

    with engine.connect() as conn:
        q = "select date, version, top, headers, stats, footer, option_cols, sheet_hash from daily_sheets where date='2025-09-28'"
        res = conn.execute(sa.text(q))
        rows = []
        for r in res.mappings().all():
            d = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in dict(r).items()}
            rows.append(d)
    print(json.dumps(rows, ensure_ascii=False, indent=2))

print('This helper has been moved to dev_tools/tmp_show_sheet_safe.py')
print('Run: python dev_tools/tmp_show_sheet_safe.py')
