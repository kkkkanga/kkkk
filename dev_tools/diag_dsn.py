from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv(dotenv_path=Path('c:/booking/.env'))
d = os.environ.get('DATABASE_URL')
print('repr:', repr(d))
if d is None:
    print('DATABASE_URL is None')
else:
    b = d.encode('utf-8', errors='surrogateescape')
    print('bytes:', b)
    for i, byte in enumerate(b):
        if byte > 127:
            print('non-ascii at', i, hex(byte))
    try:
        print('decoded utf-8 OK')
    except Exception as e:
        print('decode error', e)
