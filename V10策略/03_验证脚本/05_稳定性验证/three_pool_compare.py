"""三池对比 + 真实ETF费率(万2.5佣金+0.03%滑点, 免印花).
33池(原版) / 56池(新标的) / 89池(合并).
"""
import json, os, math
from collections import defaultdict
from datetime import datetime

RF=0.025; TD=252; INIT=int(1e7)
F_MA=6; S_MA=15; SL_MA=8
TRAIL_BULL=0.03; TRAIL_BEAR=0.06; BULL_MH=0; BEAR_MH=7; PULLBACK_MIN=0.05; NEED_N=7
# 真实ETF费率: 万2.5佣金/边 + 0.03%滑点 + 免印花
SLIP=0.0003; COMM=0.00025; STAMP=0.0

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

def load_pool(codes):
    etfs={}
    for code in codes:
        for dd in ['data_new','data']:
            p=os.path.join(dd,'etf_'+code+'.json')
            if os.path.exists(p):
                d=json.load(open(p,encoding='utf-8'))
                bars=[]
                for b in d['bars']:
                    dt=b['date'];px=float(b['close'])
                    if len(dt)==8:dt=dt[:4]+'-'+dt[4:6]+'-'+dt[6:8]
                    bars.append({'date':dt,'close':px,'high':float(b.get('high',px)) if px>0 else 0.0,'valid':px>0})
                if bars:
                    etfs[code]={'name':d['name'],'bars':bars}
                    break
    return etfs

def run_pool(etfs, def_codes, index_dict, label):
    codes=sorted(etfs.keys())
    all_trnd={};all_ratio={};above_ma60={};etf_highs={}
    for c in codes:
        bars=etfs[c]['bars']
        cl=[b['close'] if b['valid'] else float('nan') for b in bars]
        hi=[b['high'] if b['valid'] else 0.0 for b in bars]
        dts=[b['date'] for b in bars]
        mf=ma(cl,F_MA);ms_=ma(cl,S_MA);msl=ma(cl,SL_MA);slo_=slp(msl,4)
        m60=ma(cl,60)
        trnd={};rat={};abv={}
        for i in range(len(bars)):
            d=dts[i];valid=bars[i]['valid']
            if valid and not math.isnan(mf[i]) and not math.isnan(ms_[i]) and ms_[i]>0:
                sk=not math.isnan(slo_[i]) and slo_[i]>0
                trnd[d]=mf[i-1]>ms_[i-1] and sk if i>0 else False
                rat[d]=mf[i-1]/ms_[i-1] if i>0 and ms_[i-1]>0 else 1.0
            else:trnd[d]=False;rat[d]=1.0
            abv[d]=valid and (not math.isnan(m60[i-1]) and cl[i-1]>m60[i-1]) if i>0 else False
        all_trnd[c]=trnd;all_ratio[c]=rat;above_ma60[c]=abv;etf_highs[c]={b['date']:b['high'] for b in bars}
    index_slope={}
    for code in index_dict:
        if code not in etfs:continue
        bars=etfs[code]['bars']
        cl=[b['close'] if b['valid'] else float('nan') for b in bars]
        dts=[b['date'] for b in bars]
        m60=ma(cl,60);sl=slp(m60,20)
        index_slope[code]={dts[i]:(not math.isnan(sl[i-1]) and sl[i-1]>0) if i>0 else False for i in range(len(dts))}
    dm={c:{b['date']:b for b in etfs[c]['bars']} for c in codes}
    fd={c:next((b['date'] for b in etfs[c]['bars'] if b['valid']),'9999-12-31') for c in codes}
    all_dates=sorted(set(d for c in codes for d in dm[c].keys()))
    date_idx={d:i for i,d in enumerate(all_dates)}

    cash=INIT;pos=None;shares=0.0;bp=0.0;peak=0.0;entry_d='';entry_date=None;dvs=[]
    pool_mode='all';rolling_pnl=[];n_trades=0
    for d in all_dates:
        avail=[c for c in codes if fd[c]<=d];dt_obj=datetime.strptime(d,'%Y-%m-%d')
        # 牛熊判断: 优先510300, 没有则用index_dict第一个宽基
        bear_code='510300' if '510300' in index_slope else next(iter(index_slope),None)
        is_bear=not index_slope.get(bear_code,{}).get(d,False) if bear_code else True
        cur_trail=TRAIL_BEAR if is_bear else TRAIL_BULL;cur_mh=BEAR_MH if is_bear else BULL_MH
        if pos:
            bar=dm[pos].get(d)
            if bar and bar['valid']:
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
                    if pool_mode=='defensive' and sum(1 for c2 in index_dict if index_slope.get(c2,{}).get(d,False))>=NEED_N:pool_mode='all'
        if not pos and cash>0:
            cands=[]
            for c in avail:
                if pool_mode=='defensive' and c not in def_codes:continue
                if not all_trnd.get(c,{}).get(d,False):continue
                if not above_ma60.get(c,{}).get(d,False):continue
                if PULLBACK_MIN>0:
                    hi20=0
                    for lb in range(1,21):
                        pdi=max(0,len(all_dates)-1-lb)
                        if 0<=pdi<len(all_dates):hi20=max(hi20,etf_highs[c].get(all_dates[pdi],0))
                    if hi20>0 and (hi20-dm[c][d]['close'])/hi20<PULLBACK_MIN:continue
                bar=dm[c].get(d)
                if bar and bar['valid']:cands.append((c,all_ratio[c].get(d,1.0),bar['close']))
            if cands:
                cands.sort(key=lambda x:x[1],reverse=True)
                c,ratio,px_raw=cands[0];bp=px_raw*(1+SLIP+COMM)
                shares=cash/bp;peak=px_raw;pos=c;entry_d=d;entry_date=dt_obj;cash=0.0
        pos_val=shares*dm[pos].get(d,{}).get('close',0) if pos else 0
        dvs.append(cash+pos_val)
    if pos:
        bar=dm[pos].get(all_dates[-1])
        if bar and bar['valid']:cash=shares*bar['close']*(1-SLIP-COMM-STAMP)
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
    return {'sh':sh,'tr':tr,'mdd':mdd,'n_trades':n_trades,'yr':yr,'label':label,'n_pool':len(codes)}

# ===== 三池配置 =====
ETF_33=['159782','588380','588870','588080','588300','518800','589720','588890','588170',
        '588200','159995','512480','515880','515050','159819','159992','512010',
        '518880','159937','513180','513050','513100','159509','588000','588220',
        '510300','159915','510050','511010','511260','510880','512890','159301']
DEF_OLD=['518880','159937','518800','511010','511260','510880','512890','510050']
INDEX_OLD={'510300':'HS300','159915':'创业板','588000':'科创50','510050':'上证50',
    '515880':'通信ETF','512480':'半导体ETF','513100':'纳指ETF','511260':'十年国债',
    '512890':'红利低波','159992':'创新药ETF'}

# 新56只
NEW_56=sorted([f.replace('etf_','').replace('.json','') for f in os.listdir('data_new') if f.startswith('etf_')])
DEF_NEW=['159934','518850','159981','518860','511220','159972','511030','515080','515450','159901']
INDEX_NEW={'510310':'沪深300','159901':'深100','512100':'中证1000','510050':'上证50',
    '512760':'半导体50','159994':'5G','513500':'标普500','511220':'城投债','515080':'红利','512170':'医疗'}

# 89池 = 33+56 去重
ETF_89=sorted(set(ETF_33+NEW_56))
DEF_89=sorted(set(DEF_OLD+DEF_NEW))
INDEX_89=dict(INDEX_OLD)  # 用原版检测池(更稳)

print('='*110)
print('  三池稳定性对比 | 真实ETF费率(万2.5佣金+0.03%滑点, 免印花, 单次换手0.11%)')
print('='*110)
print('  费率: 买入=滑点0.03%%+佣金0.025%%=0.055%% | 卖出=0.055%% | 单次换手=0.11%%\n')

results={}
for codes,dc,idx,label in [
    (ETF_33,DEF_OLD,INDEX_OLD,'33池(原版)'),
    (NEW_56,DEF_NEW,INDEX_NEW,'56池(新标的)'),
    (ETF_89,DEF_89,INDEX_89,'89池(合并)'),
]:
    etfs=load_pool(codes)
    r=run_pool(etfs,dc,idx,label)
    results[label]=r
    print('  %-14s %d只 夏普=%.3f 收益=%+.0f%% 回撤=%.1f%% 笔数=%d' % (
        label,r['n_pool'],r['sh'],r['tr']*100,r['mdd']*100,r['n_trades']))

print('\n【分年度对比】')
print('  %-14s %7s %7s %7s %7s %7s %7s %7s' % ('池','2020','2021','2022','2023','2024','2025','2026'))
print('  '+'-'*70)
for label in ['33池(原版)','56池(新标的)','89池(合并)']:
    r=results[label]
    vals=' '.join('%+6.0f%%'%(r['yr'].get(y,0)*100) for y in ['2020','2021','2022','2023','2024','2025','2026'])
    print('  %-14s %s' % (label[:12],vals))

print('\n【稳定性判定 (vs 33池)】')
b=results['33池(原版)']
for label in ['56池(新标的)','89池(合并)']:
    r=results[label]
    sh_ratio=r['sh']/b['sh'] if b['sh']>0 else 0
    agree=sum(1 for y in ['2020','2021','2022','2023','2024','2025','2026']
              if (b['yr'].get(y,0)>0)==(r['yr'].get(y,0)>0))
    print('  %-14s 夏普比=%.0f%%  年度方向一致=%d/7' % (label[:12], sh_ratio*100, agree))
