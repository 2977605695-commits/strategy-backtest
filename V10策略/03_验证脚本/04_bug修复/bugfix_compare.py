"""修复回撤过滤bug: len(all_dates)-1-lb → date_idx[d]-lb.
对比 bug版(回撤过滤失效=pullback=0) vs 修复版(正确取当前日期前20天高点).
33只精选池, 原版参数(MA6/15/Trail3-6%/MH7), 真实ETF费率.
另外测: 修复后原版参数 + pullback=0 (看bug版等效情况是否吻合).
"""
import json, os, math
from collections import defaultdict
from datetime import datetime

DATA_DIR=r"C:\Users\home\Desktop\策略文件夹\data"
START='2020-01-01'; END='2026-07-30'
RF=0.025; TD=252; INIT=int(1e7)
SL_MA=8; BULL_MH=0
SLIP=0.0003; COMM=0.00025; STAMP=0.0

ETF_33=['159782','588380','588870','588080','588300','518800','589720','588890','588170',
        '588200','159995','512480','515880','515050','159819','159992','512010',
        '518880','159937','513180','513050','513100','159509','588000','588220',
        '510300','159915','510050','511010','511260','510880','512890','159301']
DEF_POOL=['518880','159937','518800','511010','511260','510880','512890','510050']
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
    mf=ma(cl,6);ms_=ma(cl,15);msl=ma(cl,SL_MA);slo_=slp(msl,4)
    m60=ma(cl,60);dts=[b['date'] for b in bars]
    trnd={};rat={};abv={}
    for i in range(len(bars)):
        d=dts[i]
        if not math.isnan(mf[i]) and not math.isnan(ms_[i]) and ms_[i]>0:
            sk=not math.isnan(slo_[i]) and slo_[i]>0
            trnd[d]=mf[i-1]>ms_[i-1] and sk if i>0 else False
            rat[d]=mf[i-1]/ms_[i-1] if i>0 and ms_[i-1]>0 else 1.0
        else:trnd[d]=False;rat[d]=1.0
        abv[d]=not math.isnan(m60[i-1]) and cl[i-1]>m60[i-1] if i>0 else False
    all_trnd[c]=trnd;all_ratio[c]=rat;above_ma60[c]=abv;etf_highs[c]={b['date']:b['high'] for b in bars}
for code in INDEX_CODES:
    if code not in etfs:continue
    cl=[b['close'] for b in etfs[code]['bars']];dts=[b['date'] for b in etfs[code]['bars']]
    m60=ma(cl,60);sl=slp(m60,20)
    index_slope[code]={dts[i]:(not math.isnan(sl[i-1]) and sl[i-1]>0) if i>0 else False for i in range(len(dts))}
dm={c:{b['date']:b for b in etfs[c]['bars']} for c in codes}
fd={c:etfs[c]['first_date'] for c in codes}
all_dates=sorted(set(d for c in codes for d in dm[c].keys()))
date_idx={d:i for i,d in enumerate(all_dates)}

def run(pullback, pullback_fixed=True):
    """pullback_fixed=True: 修复版(用date_idx[d]-lb); False: bug版(用len-1-lb)."""
    trail_bull=0.03;trail_bear=0.06;mh_bear=7;need_n=7
    cash=INIT;pos=None;shares=0.0;bp=0.0;peak=0.0;entry_d='';entry_date=None;dvs=[]
    pool_mode='all';rolling_pnl=[];n_trades=0
    for d in all_dates:
        avail=[c for c in codes if fd[c]<=d];dt_obj=datetime.strptime(d,'%Y-%m-%d')
        is_bear=not index_slope.get('510300',{}).get(d,False)
        cur_trail=trail_bear if is_bear else trail_bull;cur_mh=mh_bear if is_bear else BULL_MH
        if pos:
            bar=dm[pos].get(d)
            if bar:
                px_raw=bar['close'];px_sell=px_raw*(1-SLIP-COMM-STAMP)
                if px_raw>peak:peak=px_raw
                er=None
                if px_raw<=peak*(1-cur_trail):er='trail'
                elif not all_trnd.get(pos,{}).get(d,False):
                    if cur_mh>0 and entry_date and (dt_obj-entry_date).days>=cur_mh:er='off'
                    else:er='off'
                if er:
                    n_trades+=1
                    pnl=shares*px_sell-shares*bp
                    rolling_pnl.append(pnl)
                    if len(rolling_pnl)>5:rolling_pnl.pop(0)
                    cash=shares*px_sell;pos=None;shares=0.0;bp=0.0;peak=0.0;entry_date=None
                    if len(rolling_pnl)>=5 and pool_mode=='all' and sum(rolling_pnl)<-0.10*INIT:pool_mode='defensive'
                    if pool_mode=='defensive' and sum(1 for c2 in INDEX_CODES if index_slope.get(c2,{}).get(d,False))>=need_n:pool_mode='all'
        if not pos and cash>0:
            cands=[]
            di=date_idx[d]
            for c in avail:
                if pool_mode=='defensive' and c not in DEF_POOL:continue
                if not all_trnd[c].get(d,False):continue
                if not above_ma60[c].get(d,False):continue
                if pullback>0:
                    hi20=0
                    for lb in range(1,21):
                        if pullback_fixed:
                            pdi=di-lb  # ★ 修复: 当前日期前lb天
                        else:
                            pdi=max(0,len(all_dates)-1-lb)  # bug: 回测末尾
                        if 0<=pdi<len(all_dates):hi20=max(hi20,etf_highs[c].get(all_dates[pdi],0))
                    if hi20>0 and (hi20-dm[c][d]['close'])/hi20<pullback:continue
                bar=dm[c].get(d)
                if bar:cands.append((c,all_ratio[c].get(d,1.0),bar['close']))
            if cands:
                cands.sort(key=lambda x:x[1],reverse=True)
                c,ratio,px_raw=cands[0];bp=px_raw*(1+SLIP+COMM)
                shares=cash/bp;peak=px_raw;pos=c;entry_d=d;entry_date=dt_obj;cash=0.0
        pos_val=shares*dm[pos].get(d,{}).get('close',0) if pos else 0
        dvs.append(cash+pos_val)
    if pos:
        bar=dm[pos].get(all_dates[-1])
        if bar:cash=shares*bar['close']*(1-SLIP-COMM-STAMP)
    fv=dvs[-1];rets=[(dvs[i]-dvs[i-1])/dvs[i-1] for i in range(1,len(dvs)) if dvs[i-1]>0]
    pk=dvs[0];mdd=0.0
    for v in dvs:
        if v>pk:pk=v
        dd=(pk-v)/pk
        if dd>mdd:mdd=dd
    tr=(fv-INIT)/INIT;mu=sum(rets)/len(rets)
    sd_=(sum((r-mu)**2 for r in rets)/(len(rets)-1))**0.5
    av=sd_*math.sqrt(TD);sh=(mu*TD-RF)/av if av>0 else 0
    yr={}
    for y in ['2020','2021','2022','2023','2024','2025','2026']:
        ld=max((dd for dd in all_dates if dd[:4]==y),default=all_dates[-1])
        sd0=min((dd for dd in all_dates if dd[:4]==y),default=all_dates[0])
        s_v=dvs[date_idx[sd0]-1] if date_idx[sd0]>0 else INIT
        e_v=dvs[date_idx[ld]]
        yr[y]=(e_v-s_v)/s_v if s_v>0 else 0
    return {'sh':sh,'tr':tr,'mdd':mdd,'n_trades':n_trades,'yr':yr}

print('='*100)
print('  回撤过滤bug修复对比 | 33只精选池 | 原版参数 | 真实ETF费率')
print('='*100)
print('  bug版: pdi=len(all_dates)-1-lb (取回测末尾高点, 过滤实际失效)')
print('  修复版: pdi=date_idx[d]-lb (正确取当前日期前20天高点)\n')

# 四种情况
cases=[
    ('① bug版 pullback=5%(原版)',   0.05, False),
    ('② bug版 pullback=0',          0.0,  False),
    ('③ 修复版 pullback=5%',        0.05, True),
    ('④ 修复版 pullback=0',         0.0,  True),
]
results={}
for label,pb,fix in cases:
    r=run(pb,fix)
    results[label]=r
    print('  %-30s 夏普=%.3f 收益=%+.0f%% 回撤=%.1f%% 笔数=%d' % (
        label,r['sh'],r['tr']*100,r['mdd']*100,r['n_trades']))

# 验证 ①≈② (bug导致pullback失效)
print('\n【验证: bug版pullback=5%% 是否≈ pullback=0】')
r1=results['① bug版 pullback=5%(原版)']; r2=results['② bug版 pullback=0']
print('  ① 收益=%+.0f%% 笔数=%d' % (r1['tr']*100,r1['n_trades']))
print('  ② 收益=%+.0f%% 笔数=%d' % (r2['tr']*100,r2['n_trades']))
print('  差异: 收益%+.0f 笔数%+d' % (r1['tr']*100-r2['tr']*100, r1['n_trades']-r2['n_trades']))
if abs(r1['tr']-r2['tr'])<0.01 and r1['n_trades']==r2['n_trades']:
    print('  ✓ 确认: bug导致pullback完全失效, 5%等效于0%')
else:
    print('  ○ 部分差异(可能边界效应)')

# 修复后影响
print('\n【bug修复的影响: ① vs ③】')
r1=results['① bug版 pullback=5%(原版)']; r3=results['③ 修复版 pullback=5%']
print('  夏普:   %.3f → %.3f  (%+.3f)' % (r1['sh'],r3['sh'],r3['sh']-r1['sh']))
print('  收益:   %+.0f%% → %+.0f%%  (%+.0f)' % (r1['tr']*100,r3['tr']*100,(r3['tr']-r1['tr'])*100))
print('  回撤:   %.1f%% → %.1f%%  (%+.1f)' % (r1['mdd']*100,r3['mdd']*100,(r3['mdd']-r1['mdd'])*100))
print('  笔数:   %d → %d  (%+d)' % (r1['n_trades'],r3['n_trades'],r3['n_trades']-r1['n_trades']))

print('\n【分年度: bug版 vs 修复版 (pullback=5%)】')
print('  %-6s %-12s %-12s %s' % ('年份','bug版','修复版','差异'))
for y in ['2020','2021','2022','2023','2024','2025','2026']:
    a=r1['yr'].get(y,0)*100;b=r3['yr'].get(y,0)*100
    print('  %-6s %+11.0f%% %+11.0f%% %+8.0f' % (y,a,b,b-a))

# 修复后最优pullback扫描
print('\n【修复版: pullback参数扫描】')
print('  %-12s %8s %10s %8s %6s' % ('pullback','夏普','收益','回撤','笔数'))
for pb in [0,0.02,0.03,0.05,0.08,0.10]:
    r=run(pb,True)
    print('  %-12s %8.3f %+9.0f%% %7.1f%% %5d' % ('%.0f%%'%(pb*100),r['sh'],r['tr']*100,r['mdd']*100,r['n_trades']))
