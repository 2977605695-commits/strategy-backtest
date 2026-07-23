"""
Fetch daily price data for 70 A-share stocks from Tencent Finance.
Saves one JSON per stock to data/ + _summary.json.
Period: 2020-01-01 to today.

Usage: python fetch_all_stocks.py
"""

import sys, io, urllib.request, json, math, time, os
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
START = '2020-01-01'
END = '2026-07-23'

# 70 stock codes from fundamentals data
CODES = [
    '000657','000938','000977','002050','002371','002384','002460','002466',
    '002837','002851','300054','300308','300373','300394','300475','300476',
    '300502','300604','300620','300661','300666','300757','301018','301165',
    '301308','301392','301629','600030','600111','600183','600276','600460',
    '600584','600900','601088','601138','601288','601398','601600','601689',
    '601869','601899','601995','603186','603259','603296','603738','603986',
    '688008','688012','688037','688072','688126','688141','688195','688256',
    '688261','688347','688409','688498','688502','688503','688627','688702',
    '688726','688727','688795','688802','688820','688981',
]

# Stock name mapping from fundamentals
STOCK_NAMES = {
    '000657': '中钨高新','000938': '紫光股份','000977': '浪潮信息','002050': '三花智控',
    '002371': '北方华创','002384': '东山精密','002460': '赣锋锂业','002466': '天齐锂业',
    '002837': '英维克','002851': '麦格米特','300054': '鼎龙股份','300308': '中际旭创',
    '300373': '扬杰科技','300394': '天孚通信','300475': '香农芯创','300476': '胜宏科技',
    '300502': '新易盛','300604': '长川科技','300620': '光库科技','300661': '圣邦股份',
    '300666': '江丰电子','300757': '罗博特科','301018': '申菱环境','301165': '中瑞股份',
    '301308': '江波龙','301392': '汇成真空','301629': '矽电股份','600030': '中信证券',
    '600111': '北方稀土','600183': '生益科技','600276': '恒瑞医药','600460': '士兰微',
    '600584': '长电科技','600900': '长江电力','601088': '中国神华','601138': '工业富联',
    '601288': '农业银行','601398': '工商银行','601600': '中国铝业','601689': '拓普集团',
    '601869': '长飞光纤','601899': '紫金矿业','601995': '中金公司','603186': '华正新材',
    '603259': '药明康德','603296': '华勤技术','603738': '泰晶科技','603986': '兆易创新',
    '688008': '澜起科技','688012': '中微公司','688037': '芯源微','688072': '拓荆科技',
    '688126': '沪硅产业','688141': '杰华特','688195': '腾景科技','688256': '寒武纪',
    '688261': '东微半导','688347': '华虹宏力','688409': '富创精密','688498': '源杰科技',
    '688502': '茂莱光学','688503': '聚和材料','688627': '精智达','688702': '盛科通信',
    '688726': '拉普拉斯','688727': '衡泰技术','688795': '联动科技','688802': '中科飞测',
    '688820': '盛美上海','688981': '中芯集成',
}

SECTORS = {
    '000657': '有色', '000938': 'IT', '000977': 'IT', '002050': '机械', '002371': '半导体',
    '002384': 'PCB', '002460': '锂电', '002466': '锂电', '002837': '温控', '002851': '电源',
    '300054': '材料', '300308': '光通信', '300373': '半导体', '300394': '光通信', '300475': '半导体',
    '300476': 'PCB', '300502': '光通信', '300604': '设备', '300620': '光通信', '300661': '模拟芯片',
    '300666': '靶材', '300757': '设备', '301018': '温控', '301165': '结构件', '301308': '存储',
    '301392': '设备', '301629': '设备', '600030': '券商', '600111': '稀土', '600183': 'PCB',
    '600276': '医药', '600460': '半导体', '600584': '封测', '600900': '电力', '601088': '煤炭',
    '601138': 'IT', '601288': '银行', '601398': '银行', '601600': '有色', '601689': '汽车',
    '601869': '光纤', '601899': '黄金', '601995': '券商', '603186': 'PCB', '603259': '医药',
    '603296': '电子', '603738': '晶振', '603986': '存储', '688008': '接口芯片', '688012': '设备',
    '688037': '设备', '688072': '设备', '688126': '硅片', '688141': '电源', '688195': '光学',
    '688256': 'AI芯片', '688261': '功率器件', '688347': '代工', '688409': '零部件', '688498': '光芯片',
    '688502': '光学', '688503': '材料', '688627': 'ATE', '688702': '交换机', '688726': '设备',
    '688727': '工业软件', '688795': '设备', '688802': '检测', '688820': '设备', '688981': '代工',
}


def fetch_tencent_kline(code, start, end):
    """Fetch daily kline from Tencent Finance."""
    # Determine prefix: sz for 0/3, sh for 6
    prefix = 'sz' if code.startswith(('0','3')) else 'sh'
    full_code = prefix + code

    url = (f'http://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
           f'?param={full_code},day,{start},{end},640,qfq')
    h = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://gu.qq.com/'}
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode('utf-8'))
        except Exception as e:
            time.sleep(1)
    return {}


def parse_kline(raw, code):
    """Parse Tencent kline response to list of bars."""
    try:
        days = None
        if 'data' in raw:
            for k in raw['data']:
                if isinstance(raw['data'][k], dict):
                    for f in ['qfqday', 'day']:
                        if f in raw['data'][k]:
                            days = raw['data'][k][f]
                            break
                    if days: break
        if not days: return []
        # date, open, close, high, low, volume
        return [{'date': str(d[0]), 'open': float(d[1]), 'close': float(d[2]),
                 'high': float(d[3]), 'low': float(d[4]), 'volume': float(d[5])}
                for d in days if len(d) >= 6]
    except:
        return []


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    total = len(CODES)
    success = 0
    failed = []

    print(f'Fetching {total} stocks from {START} to {END}...')
    print(f'Output dir: {DATA_DIR}')
    print()

    for i, code in enumerate(CODES, 1):
        name = STOCK_NAMES.get(code, code)
        print(f'[{i:>3d}/{total}] {code} {name} ...', end=' ', flush=True)

        raw = fetch_tencent_kline(code, START, END)
        bars = parse_kline(raw, code)

        if not bars:
            print('FAIL')
            failed.append(code)
            time.sleep(0.3)
            continue

        # Trim to common date range
        bars = [b for b in bars if START <= b['date'] <= END]

        stock_data = {
            'code': code,
            'name': name,
            'sector': SECTORS.get(code, ''),
            'first_date': bars[0]['date'],
            'last_date': bars[-1]['date'],
            'n_days': len(bars),
            'bars': bars,
        }

        out_path = os.path.join(DATA_DIR, f'{code}.json')
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(stock_data, f, ensure_ascii=False)

        print(f'OK {len(bars):>4d} bars ({bars[0]["date"]} ~ {bars[-1]["date"]})')
        success += 1

        # Be nice to the server
        time.sleep(0.15)

    # Write summary
    summary = {
        'fetch_date': datetime.now().strftime('%Y-%m-%d'),
        'period': f'{START}~{END}',
        'total_stocks': total,
        'success': success,
        'failed': failed,
    }
    with open(os.path.join(DATA_DIR, '_summary.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f'\nDone: {success}/{total} success, {len(failed)} failed')
    if failed:
        print(f'Failed: {failed}')
    print(f'Data saved to {DATA_DIR}/')


if __name__ == '__main__':
    main()
