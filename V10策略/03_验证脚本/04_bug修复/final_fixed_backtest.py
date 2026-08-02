"""修复版完整回测: bug已修(date_idx[d]-lb), 原版参数, pullback扫描找最优, 真实ETF费率.
包含: 全周期/分年度/费率敏感/对比买入持有/交易归因."""
import json, os, math
from collections import defaultdict
from datetime import datetime

DATA_DIR=r"C:\Users\home\Desktop\策略文件夹\data"
START='2020-01-01'; END='2026-07-30'
RF=0.025; TD=252; INIT=int(1e7)
F_MA=6; S_MA=15; SL_MA=8
TRAIL_BULL=0.03; TRAIL_BEAR=0.06; BULL_MH=0; BEAR_MH=7; NEED_N=7
# 真实ETF费率
SLIP=0.0003; COMM=0.00025; STAMP=0.0

ETF_33=['159782','588380','588870','588080','588300','518800','589720','588890','588170',
        '588200','159995','512480','515880','515050','159819','159992','512010',
        '518880','159937','513180','513050','513100','159509','588000','588220',
        '510300','159915','510050','511010','511260','510880','512890','159301']
DEF_POOL=['518880','159937','518800','511010','511260','510880','512890','510050']
INDEX_CODES={'510300':'HS300','159915':'创业板','588000':'科创50','510050':'上证50',
    '515880':'通信ETF','512480':'半导体ETF','513100':'纳指ETF','511260':'十年国债',
    '512890':'红利低波','159992':'创新药ETF'}
NAME={}

def load():
    etfs={}
    for code in ETF_33:
        p=os.path.join(DATA_DIR,'etf_'+code+'.json')
        if not os.path.exists(p):continue
        d=json.load(open(p,encoding='utf-8')); NAME[code]=d['name']; bars=[]
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
    mf=ma(cl,F_MA);ms_=ma(cl,S_MA);msl=ma(cl,SL_MA);slo_=slp(msl,4)
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

def run(pullback, slip=SLIP, return_trades=False):
    cash=INIT;pos=None;shares=0.0;bp=0.0;peak=0.0;entry_d='';entry_date=None;dvs=[]
    pool_mode='all';rolling_pnl=[];n_trades=0;trades=[]
    for d in all_dates:
        avail=[c for c in codes if fd[c]<=d];dt_obj=datetime.strptime(d,'%Y-%m-%d')
        is_bear=not index_slope.get('510300',{}).get(d,False)
        cur_trail=TRAIL_BEAR if is_bear else TRAIL_BULL;cur_mh=BEAR_MH if is_bear else BULL_MH
        if pos:
            bar=dm[pos].get(d)
            if bar:
                px_raw=bar['close'];px_sell=px_raw*(1-slip-COMM-STAMP)
                if px_raw>peak:peak=px_raw
                er=None;reason=''
                if px_raw<=peak*(1-cur_trail):er='trail';reason='Trail%.0f%%'%(cur_trail*100)
                elif not all_trnd.get(pos,{}).get(d,False):
                    if cur_mh>0 and entry_date and (dt_obj-entry_date).days>=cur_mh:er='off';reason='趋势空>=%dd'%cur_mh
                    else:er='off';reason='趋势转空'
                if er:
                    n_trades+=1
                    pnl=shares*px_sell-shares*bp;ret=(px_sell-bp)/bp
                    trades.append({'code':pos,'name':NAME.get(pos,pos),'buy':entry_d,'sell':d,'pnl':pnl,'ret':ret,'reason':reason,'hold':(dt_obj-entry_date).days if entry_date else 0})
                    rolling_pnl.append(pnl)
                    if len(rolling_pnl)>5:rolling_pnl.pop(0)
                    cash=shares*px_sell;pos=None;shares=0.0;bp=0.0;peak=0.0;entry_date=None
                    if len(rolling_pnl)>=5 and pool_mode=='all' and sum(rolling_pnl)<-0.10*INIT:pool_mode='defensive'
                    if pool_mode=='defensive' and sum(1 for c2 in INDEX_CODES if index_slope.get(c2,{}).get(d,False))>=NEED_N:pool_mode='all'
        if not pos and cash>0:
            cands=[];di=date_idx[d]
            for c in avail:
                if pool_mode=='defensive' and c not in DEF_POOL:continue
                if not all_trnd[c].get(d,False):continue
                if not above_ma60[c].get(d,False):continue
                if pullback>0:
                    hi20=0
                    for lb in range(1,21):
                        pdi=di-lb  # ★ 修复版
                        if 0<=pdi<len(all_dates):hi20=max(hi20,etf_highs[c].get(all_dates[pdi],0))
                    if hi20>0 and (hi20-dm[c][d]['close'])/hi20<pullback:continue
                bar=dm[c].get(d)
                if bar:cands.append((c,all_ratio[c].get(d,1.0),bar['close']))
            if cands:
                cands.sort(key=lambda x:x[1],reverse=True)
                c,ratio,px_raw=cands[0];bp=px_raw*(1+slip+COMM)
                shares=cash/bp;peak=px_raw;pos=c;entry_d=d;entry_date=dt_obj;cash=0.0
        pos_val=shares*dm[pos].get(d,{}).get('close',0) if pos else 0
        dvs.append(cash+pos_val)
    if pos:
        bar=dm[pos].get(all_dates[-1])
        if bar:
            px_raw=bar['close'];px_sell=px_raw*(1-slip-COMM-STAMP)
            pnl=shares*px_sell-shares*bp;ret=(px_sell-bp)/bp
            trades.append({'code':pos,'name':NAME.get(pos,pos),'buy':entry_d,'sell':all_dates[-1],'pnl':pnl,'ret':ret,'reason':'末仓','hold':0})
            cash=shares*px_sell
    fv=dvs[-1];rets=[(dvs[i]-dvs[i-1])/dvs[i-1] for i in range(1,len(dvs)) if dvs[i-1]>0]
    pk=dvs[0];mdd=0.0
    for v in dvs:
        if v>pk:pk=v
        dd=(pk-v)/pk
        if dd>mdd:mdd=dd
    tr=(fv-INIT)/INIT;mu=sum(rets)/len(rets)
    sd_=(sum((r-mu)**2 for r in rets)/(len(rets)-1))**0.5
    av=sd_*math.sqrt(TD);sh=(mu*TD-RF)/av if av>0 else 0
    ar=(1+tr)**(TD/len(rets))-1 if tr>-1 else -1
    yr={}
    for y in ['2020','2021','2022','2023','2024','2025','2026']:
        ld=max((dd for dd in all_dates if dd[:4]==y),default=all_dates[-1])
        sd0=min((dd for dd in all_dates if dd[:4]==y),default=all_dates[0])
        s_v=dvs[date_idx[sd0]-1] if date_idx[sd0]>0 else INIT
        e_v=dvs[date_idx[ld]]
        yr[y]=(e_v-s_v)/s_v if s_v>0 else 0
    res={'sh':sh,'tr':tr,'mdd':mdd,'ar':ar,'n_trades':n_trades,'yr':yr,'dvs':dvs}
    if return_trades:res['trades']=trades
    return res

print('='*100)
print('  ★ 修复版完整回测 (回撤过滤bug已修) | 33只精选池 | 真实ETF费率')
print('='*100)
print('  费率: 万2.5佣金+0.03%滑点, 免印花税, 单次换手0.11%')
print('  参数: MA6/15, Trail3%/6%, MH熊7, DEF->ALL 7/10 (原版)')
print('  修复: 回撤过滤 date_idx[d]-lb (原bug: len(all_dates)-1-lb)\n')

# ===== 1. pullback 扫描找最优 =====
print('【1. 修复版 pullback 扫描】')
print('  %-10s %8s %10s %8s %6s' % ('pullback','夏普','收益','回撤','笔数'))
best_pb=0.03;best_sh=-99
for pb in [0.02,0.025,0.03,0.035,0.04,0.05,0.06]:
    r=run(pb)
    mark=''
    if r['sh']>best_sh:best_sh=r['sh'];best_pb=pb
    print('  %-10s %8.3f %+9.0f%% %7.1f%% %5d%s' % ('%.1f%%'%(pb*100),r['sh'],r['tr']*100,r['mdd']*100,r['n_trades'],mark))
print('  → 最优 pullback = %.1f%% (夏普%.3f)\n' % (best_pb*100,best_sh))

# ===== 2. 最优pullback完整指标 =====
r=run(best_pb,return_trades=True)
ts=r['trades'];w=sum(1 for t in ts if t['ret']>0)
wr=w/len(ts)*100 if ts else 0
print('【2. 修复版最优表现 (pullback=%.1f%%)】' % (best_pb*100))
print('  夏普:    %.3f' % r['sh'])
print('  总收益:  %+.0f%%' % (r['tr']*100))
print('  年化:    %.1f%%' % (r['ar']*100))
print('  最大回撤:%.1f%%' % (r['mdd']*100))
print('  交易笔数:%d' % r['n_trades'])
print('  胜率:    %.0f%%' % wr)
print('  终值:    %+.0f万 (初始1000万)' % (r['dvs'][-1]/1e4))

# ===== 3. 分年度 =====
print('\n【3. 分年度收益】')
print('  %-6s %-10s' % ('年份','收益'))
for y in ['2020','2021','2022','2023','2024','2025','2026']:
    v=r['yr'].get(y,0)*100
    bar='█'*int(abs(v)/10)
    print('  %-6s %+9.0f%% %s%s' % (y,v,bar,'▲' if v>0 else '▼'))

# ===== 4. 费率敏感 =====
print('\n【4. 费率敏感性 (pullback=%.1f%%)】' % (best_pb*100))
print('  %-22s %8s %10s %8s' % ('方案','夏普','收益','回撤'))
for slip,label in [(0,'无成本(理论)'),(0.0003,'真实(万2.5+0.03%滑点)'),(0.0005,'万2.5+0.05%滑点'),(0.001,'保守(0.1%滑点)')]:
    rr=run(best_pb,slip=slip)
    print('  %-22s %8.3f %+9.0f%% %7.1f%%' % (label,rr['sh'],rr['tr']*100,rr['mdd']*100))

# ===== 5. vs 买入持有 =====
print('\n【5. vs 持有最强ETF (2024-2026)】')
cum=1
for y in ['2024','2025','2026']:cum*=(1+r['yr'].get(y,0))
print('  策略(修复版): %+.0f%%' % ((cum-1)*100))
# 最强ETF
def buyhold(code,s,e):
    bars=[b for b in etfs[code]['bars'] if s<=b['date']<=e]
    if len(bars)<2:return 0
    return (bars[-1]['close']-bars[0]['close'])/bars[0]['close']
best_etf='';best_ret=0
for c in codes:
    ret=buyhold(c,'2024-01-01','2026-12-31')
    if ret>best_ret:best_ret=ret;best_etf=c
print('  持有最强 %s(%s): %+.0f%%' % (best_etf,NAME.get(best_etf,''),best_ret*100))

# ===== 6. 交易归因 =====
print('\n【6. 交易归因 (按退出原因)】')
by_reason=defaultdict(lambda:{'n':0,'pnl':0.0,'wins':0})
for t in ts:
    k=t['reason']
    by_reason[k]['n']+=1;by_reason[k]['pnl']+=t['pnl']
    if t['pnl']>0:by_reason[k]['wins']+=1
print('  %-16s %5s %6s %12s' % ('退出原因','笔数','胜率','净盈亏'))
for k,v in sorted(by_reason.items(),key=lambda x:x[1]['pnl']):
    print('  %-16s %5d %5.0f%% %+12.0f' % (k,v['n'],v['wins']/v['n']*100 if v['n'] else 0,v['pnl']))

# ===== 7. vs bug版对比 =====
print('\n【7. 修复前(bug版) vs 修复后 对比】')
print('  %-24s %-14s %-14s' % ('指标','bug版(文档)','修复版(真实)'))
print('  '+'-'*55)
print('  %-24s %-14s %-14s' % ('夏普','1.40(虚高)','%.2f(真实)'%r['sh']))
print('  %-24s %-14s %-14s' % ('总收益','+1126%(虚高)','%+.0f%%(真实)'%(r['tr']*100)))
print('  %-24s %-14s %-14s' % ('回撤','28.2%','%.1f%%'%r['mdd']))

print('\n【8. 池切换记录】')
cash2=INIT;pm='all';rpnl=[]
switches=[]
for d in all_dates:
    dt_obj=datetime.strptime(d,'%Y-%m-%d')
    # 简化: 复用逻辑检测切换
    pass
# 用trades推断: 重新跑一遍记录切换
cash2=INIT;pos=None;pm='all';rpnl=[];sw=[]
for d in all_dates:
    avail=[c for c in codes if fd[c]<=d];dt_obj=datetime.strptime(d,'%Y-%m-%d')
    is_bear=not index_slope.get('510300',{}).get(d,False)
    cur_trail=TRAIL_BEAR if is_bear else TRAIL_BULL;cur_mh=BEAR_MH if is_bear else BULL_MH
    if pos:
        bar=dm[pos].get(d)
        if bar:
            px_raw=bar['close'];px_sell=px_raw*(1-SLIP-COMM-STAMP)
            if px_raw>peak:pass
            er=None
            if px_raw<=0:er='x'
            elif px_raw<=peak*(1-cur_trail):er='trail'
            elif not all_trnd.get(pos,{}).get(d,False):er='off'
            if er:
                pnl=shares*px_sell-shares*bp if pos else 0
                rpnl.append(pnl)
                if len(rpnl)>5:rpnl.pop(0)
                if pos:cash2=shares*px_sell
                pos=None;shares=0;peak=0
                prev=pm
                if len(rpnl)>=5 and pm=='all' and sum(rpnl)<-0.10*INIT:pm='defensive'
                if pm=='defensive' and sum(1 for c2 in INDEX_CODES if index_slope.get(c2,{}).get(d,False))>=NEED_N:pm='all'
                if pm!=prev:sw.append((d,prev,pm))
    if not pos and cash2>0:
        cands=[];di=date_idx[d]
        for c in avail:
            if pm=='defensive' and c not in DEF_POOL:continue
            if not all_trnd[c].get(d,False):continue
            if not above_ma60[c].get(d,False):continue
            if best_pb>0:
                hi20=0
                for lb in range(1,21):
                    pdi=di-lb
                    if 0<=pdi<len(all_dates):hi20=max(hi20,etf_highs[c].get(all_dates[pdi],0))
                if hi20>0 and (hi20-dm[c][d]['close'])/hi20<best_pb:continue
            bar=dm[c].get(d)
            if bar:cands.append((c,all_ratio[c].get(d,1.0),bar['close']))
        if cands:
            cands.sort(key=lambda x:x[1],reverse=True)
            c,ratio,px_raw=cands[0];bp=px_raw*(1+SLIP+COMM)
            shares=cash2/bp;peak=px_raw;pos=c;cash2=0
    if pos:
        peak=max(peak,dm[pos].get(d,{}).get('close',0))
for d,prev,new in sw:
    print('  %s %s→%s' % (d,prev.upper(),new.upper()))
