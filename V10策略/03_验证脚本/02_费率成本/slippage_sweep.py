"""滑点敏感性: 实盘不同资金量对应的滑点档位, 对收益影响."""
import json, os, math
from collections import defaultdict
from datetime import datetime

DATA_DIR = r"C:\Users\home\Desktop\策略文件夹\data"
START='2020-01-01'; END='2026-07-30'
RF=0.025; TD=252; INIT=int(1e7)
F_MA=6; S_MA=15; SL_MA=8
TRAIL_BULL=0.03; TRAIL_BEAR=0.06; BULL_MH=0; BEAR_MH=7; PULLBACK_MIN=0.05; NEED_N=7
COMM=0.0005; STAMP=0.0005  # 固定手续费/印花

ETF_33=['159782','588380','588870','588080','588300','518800','589720','588890','588170',
        '588200','159995','512480','515880','515050','159819','159992','512010',
        '518880','159937','513180','513050','513100','159509','588000','588220',
        '510300','159915','510050','511010','511260','510880','512890','159201']
DEF_POOL=['518880','159937','518800','511010','511260','510880','512890','159201','510050']
INDEX_CODES={'510300':'HS300','159915':'创业板','588000':'科创50','510050':'上证50',
    '515880':'通信ETF','512480':'半导体ETF','513100':'纳指ETF','511260':'十年国债',
    '512890':'红利低波','159992':'创新药ETF'}

def load():
    etfs={}
    for code in ETF_33:
        p=os.path.join(DATA_DIR,'etf_'+code+'.json')
        if not os.path.exists(p):continue
        d=json.load(open(p,encoding='utf-8')); bars=[]
        for b in d['bars']:
            dt=b['date'];px=float(b['close'])
            if len(dt)==8:dt=dt[:4]+'-'+dt[4:6]+'-'+dt[6:8]
            if START<=dt<=END:bars.append({'date':dt,'close':px,'high':float(b.get('high',px))})
        if bars:etfs[code]={'name':d['name'],'first_date':bars[0]['date'],'bars':bars}
    return etfs
def ma(d,w):
    m=[float('nan')]*(w-1)
    for i in range(w-1,len(d)):m.append(sum(d[i-w+1:i+1])/w)
    return m
def slp(ms,lb):
    s=[float('nan')]*len(ms)
    for i in range(len(ms)):
        if i<lb:continue
        ys=ms[i-lb+1:i+1]
        if any(math.isnan(y) for y in ys):continue
        n=len(ys);sx=sy=sxy=sxx=0
        for j,y in enumerate(ys):sx+=j;sy+=y;sxy+=j*y;sxx+=j*j
        d_=n*sxx-sx*sx
        if d_>0:s[i]=(n*sxy-sx*sy)/d_/ms[i] if ms[i]>0 else 0
    return s

etfs=load(); codes=sorted(etfs.keys())
all_trnd={};all_ratio={};above_ma60={};etf_highs={};index_slope={}
for c in codes:
    bars=etfs[c]['bars'];cl=[b['close'] for b in bars];hi=[b['high'] for b in bars]
    mf=ma(cl,F_MA);ms=ma(cl,S_MA);msl=ma(cl,SL_MA);slo_=slp(msl,max(SL_MA//2,3))
    m60=ma(cl,60);dts=[b['date'] for b in bars]
    for i in range(len(bars)):
        d=dts[i]
        if not math.isnan(mf[i]) and not math.isnan(ms[i]) and ms[i]>0:
            sk=not math.isnan(slo_[i]) and slo_[i]>0
            all_trnd.setdefault(c,{})[d]=mf[i-1]>ms[i-1] and sk if i>0 else False
            all_ratio.setdefault(c,{})[d]=mf[i-1]/ms[i-1] if i>0 and ms[i-1]>0 else 1.0
        else:
            all_trnd.setdefault(c,{})[d]=False;all_ratio.setdefault(c,{})[d]=1.0
        above_ma60.setdefault(c,{})[d]=not math.isnan(m60[i-1]) and cl[i-1]>m60[i-1] if i>0 else False
        etf_highs.setdefault(c,{})[d]=hi[i]
for code,name in INDEX_CODES.items():
    if code not in etfs:continue
    cl=[b['close'] for b in etfs[code]['bars']];dts=[b['date'] for b in etfs[code]['bars']]
    m60=ma(cl,60);sl=slp(m60,20)
    index_slope[code]={dts[i]:(not math.isnan(sl[i-1]) and sl[i-1]>0) if i>0 else False for i in range(len(dts))}
dm={c:{b['date']:b for b in etfs[c]['bars']} for c in codes}
fd={c:etfs[c]['first_date'] for c in codes}
all_dates=sorted(set(d for c in codes for d in dm[c].keys()))

def run(slip):
    cash=INIT;pos=None;shares=0.0;bp=0.0;peak=0.0;entry_d='';entry_date=None;dvs=[]
    pool_mode='all';rolling_pnl=[]
    for d in all_dates:
        avail=[c for c in codes if fd[c]<=d];dt_obj=datetime.strptime(d,'%Y-%m-%d')
        is_bear=not index_slope.get('510300',{}).get(d,False)
        cur_trail=TRAIL_BEAR if is_bear else TRAIL_BULL;cur_mh=BEAR_MH if is_bear else BULL_MH
        if pos:
            bar=dm[pos].get(d)
            if bar:
                px_raw=bar['close']; px_sell=px_raw*(1-slip-COMM-STAMP)
                if px_raw>peak:peak=px_raw
                er=None
                if px_raw<=peak*(1-cur_trail):er='trail'
                elif not all_trnd.get(pos,{}).get(d,False):er='off'
                if er:
                    pnl=shares*px_sell-shares*bp
                    rolling_pnl.append(pnl)
                    if len(rolling_pnl)>5:rolling_pnl.pop(0)
                    cash=shares*px_sell;pos=None;shares=0.0;bp=0.0;peak=0.0;entry_date=None
                    if len(rolling_pnl)>=5 and pool_mode=='all' and sum(rolling_pnl)<-0.10*INIT:pool_mode='defensive'
                    if pool_mode=='defensive' and sum(1 for c2 in INDEX_CODES if index_slope.get(c2,{}).get(d,False))>=NEED_N:pool_mode='all'
        if not pos and cash>0:
            cands=[]
            for c in avail:
                if pool_mode=='defensive' and c not in DEF_POOL:continue
                if not all_trnd.get(c,{}).get(d,False):continue
                if not above_ma60.get(c,{}).get(d,False):continue
                if PULLBACK_MIN>0:
                    hi20=0
                    for lb in range(1,21):
                        pdi=max(0,len(all_dates)-1-lb)
                        if 0<=pdi<len(all_dates):hi20=max(hi20,etf_highs[c].get(all_dates[pdi],0))
                    if hi20>0 and (hi20-dm[c][d]['close'])/hi20<PULLBACK_MIN:continue
                bar=dm[c].get(d)
                if bar:cands.append((c,all_ratio.get(c,{}).get(d,1.0),bar['close']))
            if cands:
                cands.sort(key=lambda x:x[1],reverse=True)
                c,ratio,px_raw=cands[0]; bp=px_raw*(1+slip+COMM)
                shares=cash/bp;peak=px_raw;pos=c;entry_d=d;entry_date=dt_obj;cash=0.0
        pos_val=shares*dm[pos].get(d,{}).get('close',0) if pos else 0
        dvs.append(cash+pos_val)
    if pos:
        bar=dm[pos].get(all_dates[-1])
        if bar:cash=shares*bar['close']*(1-slip-COMM-STAMP)
    fv=dvs[-1];rets=[(dvs[i]-dvs[i-1])/dvs[i-1] for i in range(1,len(dvs)) if dvs[i-1]>0]
    pk=dvs[0];mdd=0.0
    for v in dvs:
        if v>pk:pk=v
        dd=(pk-v)/pk
        if dd>mdd:mdd=dd
    tr=(fv-INIT)/INIT;mu=sum(rets)/len(rets)
    sd_=(sum((r-mu)**2 for r in rets)/(len(rets)-1))**0.5
    av=sd_*math.sqrt(TD);sh=(mu*TD-RF)/av if av>0 else 0
    return tr,mdd,sh

print('='*90)
print('  滑点敏感性测试 (对应不同实盘资金量)')
print('='*90)
print('  %-22s %8s %10s %9s %8s %10s' % ('滑点档位','单次成本','总收益','回撤','夏普','vs 0.1%'))
print('  '+'-'*80)
base=None
cases=[
    (0.0,   '0%   (理论极限)'),
    (0.0003,'0.03% (小资金<100万)'),
    (0.0005,'0.05% (100-500万)'),
    (0.001, '0.1%  (报告设置/500-2000万)'),
    (0.002, '0.2%  (2000万-5000万)'),
    (0.003, '0.3%  (>5000万/低流动性)'),
]
for slip,label in cases:
    tr,mdd,sh=run(slip)
    single=(slip+COMM)+(slip+COMM+STAMP)
    if slip==0.001:base=tr
    diff=(tr-base)*100 if base else 0
    print('  %-22s %7.3f%% %+9.0f%% %8.1f%% %7.2f %+9.0f' % (label,single*100,tr*100,mdd*100,sh,diff))

print('\n  ★ 小资金(<100万)滑点0.03% vs 报告0.1%: 收益提升显著')
