import requests
import json
from pprint import pprint
from pathlib import Path

API_BASE = "http://127.0.0.1:8000"
# Resolve payload path relative to this script so CI/workdir doesn't matter
PAYLOAD_PATH = Path(__file__).parent / "sample_payload.json"

def main():
    with open(PAYLOAD_PATH, encoding='utf-8') as f:
        payload = json.load(f)

    # Fetch current sheet meta to avoid version conflicts
    try:
        meta = requests.get(f"{API_BASE}/api/daily-sheet/meta", params={'date': payload['date']}, timeout=5)
        if meta.status_code == 200:
            payload['version'] = meta.json().get('version', payload.get('version'))
    except Exception:
        # ignore and proceed with existing payload version
        pass

    print('POST /api/update-daily-sheet ->')
    r = requests.post(f"{API_BASE}/api/update-daily-sheet", json=payload, timeout=30)
    print('status', r.status_code)
    pprint(r.json())

    print('\nGET /api/daily-sheet?date=' + payload['date'] + ' ->')
    r2 = requests.get(f"{API_BASE}/api/daily-sheet", params={'date': payload['date']}, timeout=10)
    print('status', r2.status_code)
    data = r2.json()
    print('returned rows:', len(data.get('sheet', [])))
    # print small sample
    if data.get('sheet'):
        pprint(data['sheet'][0])

if __name__ == '__main__':
    main()
