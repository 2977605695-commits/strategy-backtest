"""64只全股 + 优质科技扩展 · 全面对比"""
import sys,io,os,math,json
from collections import defaultdict
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
os.chdir(r'C:\Users\26776\Desktop\strategy-backtest')
from data_loader import load_prices,calc_ma,get_common_dates
import csv

INIT=10_000_000;RF=0.025;TD=252
SLIP=0.003;B_FEE=0.00025;S_FEE=0.00025;STAX=0.0005
TRAIL=0.22;REBAL=21;MIN_F=0.8;NO_CHASE=0.10;K=1.5;LB=14

FUND_DIR='data/fundamentals_70stocks'
csvs=sorted([f for f in os.listdir(FUND_DIR) if f.endswith('.csv')])
sm={}
with open(os.path.join(FUND_DIR,csvs[-1]),'r',encoding='utf-8-sig') as f:
    for r in csv.DictReader(f): sm[r['code'].strip()]=r.get('sector','').strip()

def load_json_stocks(dir_path,min_bars=None,max_first_date=None):
    stocks={}
    for fname in sorted(os.listdir(dir_path)):
        if not fname.endswith('.json') or fname.startswith('_'): continue
        with open(os.path.join(dir_path,fname),encoding='utf-8') as f:
            d=json.load(f)
        nb=len(d['bars'])
        fd=d['bars'][0]['date']
        if min_bars and nb<min_bars: continue
        if max_first_date and fd>max_first_date: continue
        dt=[b['date'] for b in d['bars']];cl=[b['close'] for b in d['bars']]
        vl=[b['volume'] for b in d['bars']]
        stocks[d['code']]={'name':d['name'],'dates':dt,'close':cl,'volume':vl}
    return stocks

def get_common_dates_safe(stocks):
    sets=[set(s['dates']) for s in stocks.values()]
    if not sets: return []
    return sorted(sets[0].intersection(*sets[1:]))

def calc_factor(stocks):
    fac={}
    for code,info in stocks.items():
        vols=info['volume'];dates=info['dates'];n=len(vols)
        ma_vol=calc_ma(vols,20);vals={}
        for i in range(n):
            if i<LB or math.isnan(ma_vol[i]): continue
            w=vols[i-19:i+1];mu=sum(w)/20;var=sum((v-mu)**2 for v in w)/20;std=var**0.5
            thr=ma_vol[i]+K*std;ps=0.0;rs=0.0
            for j in range(max(0,i-LB+1),i+1):
                erupt=vols[j]>=thr
                if erupt:
                    prev=(j>0 and vols[j-1]>=thr)
                    if prev: rs+=vols[j]
                    else: ps+=vols[j]
            vals[dates[i]]=ps/rs if rs>0 else float('nan')
        fac[code]=vals
    return fac

def bt(stocks,factor,dates,use_sectors=True):
    idx={c:{d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks}
    cash=INIT;pos={};trades=[];eq=[]
    for di,dt in enumerate(dates):
        for code,p in list(pos.items()):
            if code not in idx or dt not in idx[code]: continue
            px=stocks[code]['close'][idx[code][dt]]
            if px>p['peak']:p['peak']=px
            if px<=p['peak']*(1-TRAIL):
                sp=px*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
                trades.append({'code':code,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,'exit':'trail'})
                del pos[code]
        if di%REBAL==0:
            cand=[(c,factor.get(c,{}).get(dt,float('nan'))) for c in stocks]
            cand=[(c,s) for c,s in cand if not math.isnan(s) and c in idx and dt in idx[c] and s>=MIN_F]
            filtered=[]
            for c,s in cand:
                si=idx[c].get(dt)
                if si is not None and si>=5:
                    px_now=stocks[c]['close'][si];px_5d=stocks[c]['close'][si-5]
                    if px_5d>0 and (px_now-px_5d)/px_5d>NO_CHASE: continue
                filtered.append((c,s))
            cand=filtered
            cand.sort(key=lambda x:x[1],reverse=True)
            selected=[];sel_secs=set()
            for c,s in cand:
                if use_sectors:
                    sec=sm.get(c,'')
                    if sec and sec in sel_secs: continue
                if len(selected)>=5: break
                selected.append((c,s));sel_secs.add(sm.get(c,''))
            n_select=len(selected);top=set(c for c,_ in selected)
            for code in list(pos.keys()):
                if code not in top:
                    if code in idx and dt in idx[code]:
                        px=stocks[code]['close'][idx[code][dt]];sp=px*(1-SLIP-S_FEE-STAX)
                        cash+=pos[code]['shares']*sp
                        trades.append({'code':code,'ret':(sp-pos[code]['bp'])/pos[code]['bp'] if pos[code]['bp']>0 else 0,'exit':'rebal'})
                        del pos[code]
            pv2=sum(p['shares']*stocks[c]['close'][idx[c][dt]] for c,p in pos.items() if c in idx and dt in idx[c])
            nav=cash+pv2;target=nav/n_select if n_select>0 else 0
            for code,score in selected:
                if code in pos: continue
                if code not in idx or dt not in idx[code]: continue
                if cash<target*0.99: break
                bv=min(target,cash)
                if bv<=0: break
                raw=stocks[code]['close'][idx[code][dt]];bp=raw*(1+SLIP+B_FEE)
                if bp>0 and bv>bp*0.01: sh=bv/bp;cash-=bv;pos[code]={'shares':sh,'bp':bp,'peak':raw}
        cash*=(1+RF/TD)
        pv3=sum(p['shares']*stocks[c]['close'][idx[c][dt]] for c,p in pos.items() if c in idx and dt in idx[c])
        eq.append(cash+pv3)
    ld=dates[-1]
    for code,p in list(pos.items()):
        if code in idx and ld in idx[code]:
            px=stocks[code]['close'][idx[code][ld]];sp=px*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
            trades.append({'code':code,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,'exit':'final'})
    pos.clear()
    v=eq;tr=(v[-1]-v[0])/v[0];rs=[(v[i]-v[i-1])/v[i-1] for i in range(1,len(v)) if v[i-1]>0]
    if not rs: rs=[0]
    y=len(rs)/TD;cagr=(v[-1]/v[0])**(1/y)-1 if y>0 and v[0]>0 else 0
    mu=sum(rs)/len(rs) if rs else 0
    sd=(sum((r-mu)**2 for r in rs)/len(rs))**0.5 if rs else 0
    sh=(mu*TD-RF)/(sd*(TD**0.5)) if sd>0 else 0
    pk=v[0];maxdd=0.0
    for x in v:
        if x>pk: pk=x
        drop=(pk-x)/pk if pk>0 else 0
        if drop>maxdd: maxdd=drop
    cm=cagr/maxdd if maxdd>0 else float('inf')
    w=sum(1 for t in trades if t['ret']>0);nl=sum(1 for t in trades if t['ret']<0)
    return {'tr':tr,'cagr':cagr,'sh':sh,'mdd':maxdd,'calmar':cm,'nt':len(trades),
        'wr':w/len(trades) if trades else 0,'n_loss':nl,
        'loss_rate':nl/len(trades)*100 if trades else 0,'eq':eq}

# ==========================================
# LOAD POOLS
# ==========================================
s44=load_json_stocks('data',min_bars=1500,max_first_date='20200103')
s64=load_json_stocks('data',min_bars=200)
s_oos=load_json_stocks('data_oos_tech',min_bars=200)
# Remove overlap with s64
s_oos={c:i for c,i in s_oos.items() if c not in s64}
print('[POOLS] 44-old=%d | 64-all=%d | OOS-new=%d' % (len(s44),len(s64),len(s_oos)))

# ==========================================
# FACTORS
# ==========================================
f44=calc_factor(s44);f64=calc_factor(s64);fo=calc_factor(s_oos)

# ==========================================
# DATES
# ==========================================
cd44=get_common_dates_safe(s44);cd64=get_common_dates_safe(s64)
print('  44 common: %dd | 64 common: %dd' % (len(cd44),len(cd64)))

# Full period for 44
r44_full=bt(s44,f44,cd44,True)

# OOS: majority dates
all_dts=set();all_stocks={**s64,**s_oos}
for s in all_stocks.values(): all_dts.update(s['dates'])
min_cnt=max(50,int(len(all_stocks)*0.7))
dall=sorted([d for d in all_dts if sum(1 for s in all_stocks.values() if d in s['dates'])>=min_cnt])
dall=[d for d in dall if d>='20240121']  # MA20 buffer

# Intersect with 64 common
cd64_24=[d for d in cd64 if d>='20240121']

print('  2024+ Majority: %dd (>=%d stocks) | 64 common: %dd' % (len(dall),min_cnt,len(cd64_24)))

# ==========================================
# BACKTEST
# ==========================================
r44=bt(s44,f44,cd64_24,True)
r64=bt(s64,f64,cd64_24,True)
r64_ns=bt(s64,f64,cd64_24,False)
rall=bt(all_stocks,calc_factor(all_stocks),dall,True)
rall_ns=bt(all_stocks,calc_factor(all_stocks),dall,False)

print('\n'+'='*95)
print('  多池对比')
print('  K=%.1f LB=%dd Trail=%d%% NC=%d%% min_f=%.1f Rebal=%dd' % (K,LB,TRAIL*100,NO_CHASE*100,MIN_F,REBAL))
print('='*95)

pool_results=[]
for label,r,n_stk,n_day,sec in [
    ('44-old (2020-26 full)',r44_full,len(s44),len(cd44),True),
    ('44-old (2024+)',r44,len(s44),len(cd64_24),True),
    ('64-all (2024+, sector)',r64,len(s64),len(cd64_24),True),
    ('64-all (2024+, free)',r64_ns,len(s64),len(cd64_24),False),
    ('64+OOS (2024+, sector)',rall,len(all_stocks),len(dall),True),
    ('64+OOS (2024+, free)',rall_ns,len(all_stocks),len(dall),False),
]:
    pool_results.append((label,r,n_stk,n_day,sec))
    print('  %-30s %4ds %4dd %7.3f %7.1f%% %5.2f%% %5.1f%% %6.3f %4d %4.0f%%' % (
        label[:30],n_stk,n_day,r['sh'],r['tr']*100,r['cagr']*100,
        r['mdd']*100,r['calmar'],r['nt'],r['wr']*100))

# ==========================================
# STOCK QUALITY TABLE
# ==========================================
print('\n'+'='*95)
print('  完整科技股候选池')
print('  【已有64池】+【OOS优质科技】')
print('='*95)

CATEGORIES={
    'AI芯片/算力': ['寒武纪','海光信息','龙芯中科','景嘉微','澜起科技','芯原股份','复旦微电','国芯科技'],
    '半导体设备': ['北方华创','中微公司','拓荆科技','华峰测控','精智达','芯源微','盛美上海'],
    '半导体材料': ['沪硅产业','鼎龙股份','江丰电子','安集科技','沪硅产业','雅克科技'],
    '光通信/光模块': ['中际旭创','新易盛','天孚通信','腾景科技','源杰科技','光库科技','博创科技'],
    '存储/封装': ['江波龙','兆易创新','通富微电','华天科技','长电科技','佰维存储'],
    'PCB/覆铜板': ['胜宏科技','东山精密','生益科技','华正新材','深南电路','鹏鼎控股'],
    '功率半导体': ['士兰微','扬杰科技','斯达半导','东微半导','华润微','时代电气'],
    '模拟/射频芯片': ['圣邦股份','卓胜微','思瑞浦','纳芯微','艾为电子','晶丰明源'],
    '晶圆代工': ['中芯国际','华虹公司','华润微'],
    '机器人/自动化': ['汇川技术','埃斯顿','绿的谐波','机器人','拓斯达','禾川科技'],
    '新能源/光伏': ['阳光电源','宁德时代','隆基绿能','通威股份','晶科能源','天合光能'],
    '消费电子': ['立讯精密','蓝思科技','歌尔股份','领益智造','信维通信','长盈精密'],
    '汽车电子/智驾': ['德赛西威','中科创达','经纬恒润','均胜电子','华阳集团'],
    '软件/SaaS': ['金山办公','恒生电子','用友网络','广联达','深信服','奇安信'],
    '智能硬件/物联网': ['石头科技','乐鑫科技','恒玄科技','格科微','全志科技'],
    '军工/航天': ['西部超导','航发动力','中航光电','中国卫星','航天电子'],
    '医药科技': ['迈瑞医疗','联影医疗','药明康德','泰格医药','华大智造'],
    '稀有金属/新材料': ['北方稀土','天齐锂业','赣锋锂业','华友钴业','洛阳钼业'],
    '云计算/数据中心': ['中科曙光','浪潮信息','光环新网','奥飞数据','网宿科技'],
}

# Map name to code
NAME_TO_CODE={}
for pool in [s64,s_oos]:
    for code,info in pool.items(): NAME_TO_CODE[info['name']]=code

print('  %-22s %-16s %8s %6s %5s %s' % ('Category','Stock','Code','Bars','In64','First'))
print('  '+'-'*85)
total_in_pool=0;total_available=0
for cat,names in CATEGORIES.items():
    for nm in names:
        total_available+=1
        code=NAME_TO_CODE.get(nm,'?')
        in_64=code in s64
        in_oos=code in s_oos
        if in_64 or in_oos:
            total_in_pool+=1
            pool=all_stocks if code in all_stocks else (s_oos if in_oos else {})
            info=all_stocks.get(code,s_oos.get(code,{}))
            nb=len(info.get('dates',[])) if info else 0
            first=info.get('dates',['?'])[0] if info and info.get('dates') else '?'
            tag='✅64' if in_64 else '🆕OOS'
            print('  %-22s %-16s %8s %5d %5s %s' % (cat,nm,code,nb,tag,first))
        else:
            print('  %-22s %-16s %8s %5s %5s %s' % (cat,nm,code,'-','❌','需要拉取'))

print('\n  总计: %d/%d 已在池中' % (total_in_pool,total_available))

# ==========================================
# SHOW MISSING - need to fetch
# ==========================================
missing=[]
for cat,names in CATEGORIES.items():
    for nm in names:
        code=NAME_TO_CODE.get(nm,'?')
        if code not in all_stocks and code not in s_oos:
            # Need a real code
            pass

# Also show what OOS stocks we have that are NOT in this table
oos_in_table=set()
for cat,names in CATEGORIES.items():
    for nm in names:
        c=NAME_TO_CODE.get(nm,'?')
        if c in s_oos: oos_in_table.add(c)
oos_unused=set(s_oos.keys())-oos_in_table
if oos_unused:
    print('\n  OOS池中未被列入的科技股:')
    for c in sorted(oos_unused):
        print('    %s %s %db' % (c,s_oos[c]['name'],len(s_oos[c]['dates'])))

print('\nDone!')
