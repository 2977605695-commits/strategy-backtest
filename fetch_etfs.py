"""Fetch 9 ETFs 2020+ by chunking Tencent API (500-day windows)."""
import json, time, os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import urllib.request
from datetime import datetime, timedelta

ETFS = {'159782':'科创50ETF','588380':'科创100ETF','588870':'科创200ETF',
        '588080':'科创50ETF易方达','588300':'科创芯片ETF','518800':'黄金ETF',
        '589720':'半导体设备ETF','588890':'科创AIETF','588170':'科创半导体ETF'}
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
os.makedirs(DATA_DIR, exist_ok=True)

def fetch_chunk(code, s, e):
    prefix = 'sz' if code.startswith(('1','3')) else 'sh'
    url = f'http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,{s},{e},640,qfq'
    h = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://gu.qq.com/'}
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=15) as r:
                raw = json.loads(r.read().decode('utf-8'))
                days = None
                if 'data' in raw:
                    for k in raw['data']:
                        if isinstance(raw['data'][k],dict):
                            for f in ['qfqday','day']:
                                if f in raw['data'][k]: days=raw['data'][k][f]; break
                            if days: break
                if days:
                    return [{'date':str(d[0]),'open':float(d[1]),'close':float(d[2]),
                             'high':float(d[3]),'low':float(d[4]),'volume':float(d[5])}
                            for d in days if len(d)>=6]
        except: time.sleep(1)
    return []

for code, name in ETFS.items():
    print(f'{code} {name} ...', end=' ', flush=True)
    all_bars = []
    chunk_start = datetime(2020,1,1)
    chunk_end_dt = datetime(2026,7,28)
    while chunk_start < chunk_end_dt:
        ce = min(chunk_start+timedelta(days=450), chunk_end_dt)
        bars = fetch_chunk(code, chunk_start.strftime('%Y-%m-%d'), ce.strftime('%Y-%m-%d'))
        for b in bars:
            if not all_bars or b['date']!=all_bars[-1]['date']:
                all_bars.append(b)
        chunk_start = ce+timedelta(days=1)
        time.sleep(0.1)
    if all_bars:
        d = {'code':code,'name':name,'first_date':all_bars[0]['date'],'last_date':all_bars[-1]['date'],
             'n_days':len(all_bars),'bars':all_bars}
        with open(os.path.join(DATA_DIR,f'etf_{code}.json'),'w',encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False)
        print(f'OK {len(all_bars)} bars ({all_bars[0]["date"]}~{all_bars[-1]["date"]})')
    else:
        print('FAIL')
print('Done!')
