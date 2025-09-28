from dotenv import load_dotenv
from pathlib import Path
import os, traceback
load_dotenv(dotenv_path=Path('c:/booking/.env'))
print('DATABASE_URL repr:', repr(os.environ.get('DATABASE_URL')))
try:
    import psycopg2
    print('psycopg2 version:', getattr(psycopg2, '__version__', 'unknown'))
except Exception as e:
    print('psycopg2 import failed:', e)
    raise

dsn1 = os.environ.get('DATABASE_URL')
print('\nAttempt psycopg2.connect(dsn1)')
try:
    psycopg2.connect(dsn1)
    print('connected with dsn1')
except Exception as e:
    print('EXC dsn1:', type(e), e)
    traceback.print_exc()

print('\nAttempt psycopg2.connect with libpq style string')
dsn2 = 'host=db port=5432 dbname=camfitdb user=camfit_user password=mr001125!'
try:
    psycopg2.connect(dsn2)
    print('connected with dsn2')
except Exception as e:
    print('EXC dsn2:', type(e), e)
    traceback.print_exc()

print('\nAttempt psycopg2.connect with kwargs')
try:
    psycopg2.connect(dbname='camfitdb', user='camfit_user', password='mr001125!', host='db', port=5432)
    print('connected with kwargs')
except Exception as e:
    print('EXC kwargs:', type(e), e)
    traceback.print_exc()
