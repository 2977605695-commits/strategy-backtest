"""Fetch 100 OOS tech/AI stocks for domain-specific validation"""
import os, json, time, urllib.request, random

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
OOS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_oos_tech")
os.makedirs(OOS_DIR, exist_ok=True)

existing = set()
for f in os.listdir(DATA_DIR):
    if f.endswith('.json') and not f.startswith('_'):
        with open(os.path.join(DATA_DIR, f), encoding='utf-8') as fh:
            existing.add(json.load(fh)['code'])

# 100 TECH/AI stocks NOT in existing pool
# Covers: AI chips, AI software, robotics, autonomous driving, consumer electronics,
# optical comm, datacenter, cloud, IoT, sensors, new materials, quantum, satellite, etc.
TECH_CANDIDATES = {
    # Semiconductors & Chips
    '002156': '通富微电', '002185': '华天科技', '002049': '紫光国微',
    '300223': '北京君正', '300327': '中颖电子', '300672': '国科微',
    '300782': '卓胜微', '688368': '晶丰明源', '688595': '芯海科技',
    '688608': '恒玄科技', '688018': '乐鑫科技', '688728': '格科微',
    '300661': '圣邦股份', '688521': '芯原股份', '688206': '概伦电子',
    '688110': '东芯股份', '688536': '思瑞浦', '300613': '富瀚微',
    '300671': '富满微', '688002': '睿创微纳', '688007': '光峰科技',
    '300456': '赛微电子', '688126': '沪硅产业', '688385': '复旦微电',

    # AI / Robotics / Autonomous Driving
    '002236': '大华股份', '002414': '高德红外', '300496': '中科创达',
    '300458': '全志科技', '688088': '虹软科技', '300552': '万集科技',
    '002920': '德赛西威', '300627': '华测导航', '300177': '中海达',
    '300161': '华中数控', '688003': '天准科技', '300024': '机器人',
    '300222': '科大智能', '688160': '步科股份', '300660': '江苏雷利',
    '300124': '汇川技术', '300750': '宁德时代', '688017': '绿的谐波',
    '603728': '鸣志电器', '002747': '埃斯顿', '688165': '埃夫特',
    '300751': '迈为股份', '688006': '杭可科技', '688390': '固德威',
    '300274': '阳光电源', '688063': '派能科技', '688598': '金博股份',

    # Optical Communication / LiDAR / Sensors
    '300620': '光库科技', '688160': '步科股份', '300852': '四会富仕',
    '300708': '聚灿光电', '300303': '聚飞光电', '300582': '英飞特',
    '300620': '光库科技', '300566': '激智科技', '300632': '光莆股份',

    # Datacenter / Cloud / Server
    '301236': '软通动力', '300687': '赛意信息', '300634': '彩讯股份',
    '300525': '博思软件', '300738': '奥飞数据', '300383': '光环新网',
    '603019': '中科曙光', '000977': '浪潮信息', '300226': '上海钢联',
    '600850': '电科数字', '300803': '指南针',

    # Consumer Electronics / Hardware
    '002913': '奥士康', '002217': '合力泰', '002415': '海康威视',
    '300115': '长盈精密', '300136': '信维通信', '300207': '欣旺达',
    '002475': '立讯精密', '300433': '蓝思科技', '002241': '歌尔股份',
    '002600': '领益智造', '300053': '航宇微', '002389': '航天彩虹',
    '600118': '中国卫星', '600879': '航天电子',

    # New Energy / Battery Tech
    '300014': '亿纬锂能', '300750': '宁德时代', '300763': '锦浪科技',
    '002812': '恩捷股份', '300438': '鹏辉能源', '002459': '晶澳科技',
    '002129': 'TCL中环', '688599': '天合光能', '688223': '晶科能源',
    '300316': '晶盛机电', '300450': '先导智能', '300724': '捷佳伟创',
    '300850': '新强联', '002610': '爱康科技', '002865': '钧达股份',
    '600438': '通威股份', '601012': '隆基绿能', '605117': '德业股份',

    # Software / SaaS
    '002410': '广联达', '300271': '华宇软件', '300036': '超图软件',
    '300166': '东方国信', '300212': '易华录', '300377': '赢时胜',
    '600570': '恒生电子', '600588': '用友网络', '600845': '宝信软件',
    '300253': '卫宁健康', '300369': '绿盟科技', '300454': '深信服',
    '688111': '金山办公', '688561': '奇安信',

    # High-end Manufacturing / Equipment
    '300457': '赢合科技', '300457': '赢合科技', '002008': '大族激光',
    '300124': '汇川技术', '300408': '三环集团', '300285': '国瓷材料',
    '601100': '恒立液压', '603338': '浙江鼎力', '300604': '长川科技',
    '688012': '中微公司', '688037': '芯源微', '688072': '拓荆科技',

    # Display / OLED / MiniLED
    '000725': '京东方A', '000100': 'TCL科技', '002456': '欧菲光',
    '300088': '长信科技', '002387': '维信诺', '300128': '锦富技术',

    # Medical Tech
    '300760': '迈瑞医疗', '300529': '健帆生物', '300003': '乐普医疗',
    '300015': '爱尔眼科', '688271': '联影医疗', '300347': '泰格医药',
    '300601': '康泰生物', '300122': '智飞生物', '688180': '君实生物',
    '688363': '华熙生物',
}

# Deduplicate
tech_unique = {}
for code, name in TECH_CANDIDATES.items():
    if code not in existing and code not in tech_unique:
        tech_unique[code] = name

print(f'{len(tech_unique)} unique tech candidates not in existing pool')

# Pick 100 (or all if less)
selected = sorted(tech_unique.items())[:100]
print(f'Selected {len(selected)} tech stocks')

def fetch_one(code, name):
    out_f = os.path.join(OOS_DIR, code + '.json')
    if os.path.exists(out_f):
        with open(out_f, encoding='utf-8') as f:
            d = json.load(f)
        return len(d.get('bars', []))

    market = 'sh' if code.startswith('6') else 'sz'
    url = f'http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={market}{code},day,2020-01-01,2026-07-23,640,qfq'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://gu.qq.com/'})

    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            bars = None
            for k in data.get('data', {}):
                if isinstance(data['data'][k], dict):
                    for ff in ['qfqday', 'day']:
                        if ff in data['data'][k]:
                            bars = data['data'][k][ff]; break
                    if bars: break
            if bars and len(bars) > 100:
                recs = []
                for d in bars:
                    if len(d) >= 6:
                        dt = str(d[0]).replace('-', '')
                        recs.append({'date': dt, 'open': float(d[1]), 'close': float(d[2]),
                                     'high': float(d[3]), 'low': float(d[4]), 'volume': float(d[5])})
                with open(out_f, 'w', encoding='utf-8') as f:
                    json.dump({'code': code, 'name': name, 'first_date': recs[0]['date'],
                               'last_date': recs[-1]['date'], 'n_days': len(recs),
                               'bars': recs}, f, ensure_ascii=False)
                return len(recs)
            return 0
        except Exception as e:
            if attempt == 2: print(f'  {code} {name} ERR: {str(e)[:60]}')
            time.sleep(2)
    return 0

success, fail = 0, 0
for code, name in selected:
    n = fetch_one(code, name)
    if n > 0:
        success += 1
        if success % 15 == 0:
            print(f'  ... {success}/{len(selected)} done')
    else:
        fail += 1
        print(f'  {code} {name} FAIL ({n} bars)')
    time.sleep(0.4)

print(f'\nDone: {success}/{len(selected)} fetched, {fail} failed')
print(f'Total files in {OOS_DIR}: {len(os.listdir(OOS_DIR))}')
