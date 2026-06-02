import csv, os, re
from datetime import datetime

raw_dir = r'C:\Users\Samuel\.jachin\client_volumes\bi_data\raw'
print('=== All file mtimes and max dates ===')
for name in sorted(os.listdir(raw_dir)):
    if not name.endswith('.csv'):
        continue
    path = os.path.join(raw_dir, name)
    mtime_str = datetime.fromtimestamp(os.path.getmtime(path)).strftime('%H:%M:%S')
    with open(path, encoding='utf-8-sig') as f:
        content = f.read()
    dates = sorted(set(re.findall(r'2026-05-\d{2}', content)))
    tail = dates[-3:] if dates else []
    print(name + ': mtime=' + mtime_str + ', maxdates=' + str(tail))
