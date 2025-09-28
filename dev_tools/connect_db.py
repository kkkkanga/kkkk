from pathlib import Path
from dotenv import load_dotenv
import os, traceback
load_dotenv(dotenv_path=Path('c:/booking/.env'))
print('DATABASE_URL repr:', repr(os.environ.get('DATABASE_URL')))
try:
    import psycopg2
    dsn = os.environ.get('DATABASE_URL')
    print('Attempt connect...')
    conn = psycopg2.connect(dsn)
    print('Connected OK')
    conn.close()
except Exception as e:
    print('EXCEPTION TYPE:', type(e))
    print('EXCEPTION:', e)
    traceback.print_exc()
    # if UnicodeDecodeError, print problematic bytes
    try:
        if isinstance(e, UnicodeDecodeError):
            print('UnicodeDecodeError details:', e.start, e.end, e.object)
    except Exception:
        pass
