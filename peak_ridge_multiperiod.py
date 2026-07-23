"""
多周期峰岭因子: 7d/10d/14d 三周期 + 趋势强度修正
======================================================
Core: 同时计算3个周期的峰岭比
  LB=7d  (短) — 最近一周的资金行为
  LB=10d (中) — 两周
  LB=14d (长) — 三周基线

趋势调整方案:
  A. 加速加分: score = f14 + w * (f7-f14)/f14   (短期>长期=加分)
  B. 衰减惩罚: score = f14 * (1 - decay_factor) if f7 < f10 < f14
  C. 复合: score = f14 + w1*sign + w2*magnitude
"""
import sys,io,os,math
from collections import defaultdict
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
os.chdir(r'C:\Users\26776\Desktop\strategy-backtest')
from data_loader import load_prices,calc_ma,get_common_dates
import csv

INIT=10_000_000;RF=0.025;TD=252;MAX_POS=5
SLIP=0.003;B_FEE=0.00025;S_FEE=0.00025;STAX=0.0005
K=1.5;TRAIL=0.30;REBAL=21;MIN_F=0.8

FUND_DIR='data/fundamentals_70stocks'
csvs=sorted([f for f in os.listdir(FUND_DIR) if f.endswith('.csv')])
sm={}
with open(os.path.join(FUND_DIR,csvs[-1]),'r',encoding='utf-8-sig') as f:
    for r in csv.DictReader(f): sm[r['code'].strip()]=r.get('sector','').strip()

all_s=load_prices(stock_filter=None)
stocks={c:i for c,i in all_s.items() if i['dates'] and i['dates'][0]<='20200103' and len(i['dates'])>=1500}
cd=get_common_dates(stocks)

# Compute 3-period factors: 7d, 10d, 14d
def calc_factor(lb):
    fac={}
    for code,info in stocks.items():
        vols=info['volume'];dates=info['dates'];n=len(vols)
        ma_vol=calc_ma(vols,max(lb,20))
        vals={}
        for i in range(n):
            if i<lb or math.isnan(ma_vol[i]): continue
            wl=min(20,i+1);w=vols[i-wl+1:i+1]
            mu=sum(w)/wl;var=sum((v-mu)**2 for v in w)/wl;std=var**0.5
            thr=ma_vol[i]+K*std
            ps=0.0;rs=0.0
            for j in range(max(0,i-lb+1),i+1):
                erupt=vols[j]>=thr
                if erupt:
                    prev=(j>0 and vols[j-1]>=thr)
                    if prev: rs+=vols[j]
                    else: ps+=vols[j]
            vals[dates[i]]=ps/rs if rs>0 else float('nan')
        fac[code]=vals
    return fac

print('[FACTOR] Computing 7d, 10d, 14d...')
f7=calc_factor(7)
f10=calc_factor(10)
f14=calc_factor(14)
print('  7d: %d | 10d: %d | 14d: %d' % (
    sum(len(v) for v in f7.values()),
    sum(len(v) for v in f10.values()),
    sum(len(v) for v in f14.values())))

idx={c:{d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks}

# Common dates where all 3 factors exist
common_f=set()
for c in stocks:
    dates7=set(f7[c].keys());dates10=set(f10[c].keys());dates14=set(f14[c].keys())
    common_f.update(dates7 & dates10 & dates14)
f_dates=sorted(common_f & set(cd))
print('  Common dates: %d' % len(f_dates))

def compute_raw_scores(dt, scheme):
    """
    Compute scores for ALL stocks on a given date.
    Cross-sectional adjustment: turn trend into Z-score, blend with f14 Z-score.
    """
    # Step 1: Collect raw f14 and f7 for all valid stocks
    raw={}
    for code in stocks:
        v7=f7.get(code,{}).get(dt,float('nan'))
        v14=f14.get(code,{}).get(dt,float('nan'))
        if math.isnan(v14) or v14<MIN_F: continue
        if math.isnan(v7): v7=v14  # Default to no change
        raw[code]={'f14':v14,'f7':v7,
                   'trend':(v7-v14)/(abs(v14)+0.01)}  # Normalized change

    if len(raw)<5: return {}

    if scheme=='baseline':
        return {c:d['f14'] for c,d in raw.items()}

    # Step 2: Cross-sectional Z-scores
    f14_vals=[d['f14'] for d in raw.values()]
    trend_vals=[d['trend'] for d in raw.values()]
    n=len(f14_vals)
    mu_f=sum(f14_vals)/n;sd_f=(sum((v-mu_f)**2 for v in f14_vals)/n)**0.5 if n>1 else 1
    mu_t=sum(trend_vals)/n;sd_t=(sum((v-mu_t)**2 for v in trend_vals)/n)**0.5 if n>1 else 1
    if sd_f<0.01: sd_f=0.01
    if sd_t<0.01: sd_t=0.01

    z_f14={c:(raw[c]['f14']-mu_f)/sd_f for c in raw}
    z_trend={c:(raw[c]['trend']-mu_t)/sd_t for c in raw}

    if scheme=='accelerate':
        # Blend: 70% f14 Z + 30% trend Z
        return {c:z_f14[c]*0.7+z_trend[c]*0.3 for c in raw}

    elif scheme=='decay_penalty':
        # Full penalty for stocks in strict decay (f7<f10<f14)
        scores={}
        for c,d in raw.items():
            v10=f10.get(c,{}).get(dt,float('nan'))
            if math.isnan(v10): v10=d['f14']
            s=z_f14[c]
            if d['f7']<v10<d['f14']:  # Strict decay → heavy penalty
                s-=0.5
            scores[c]=s
        return scores

    elif scheme=='slope':
        # Pure trend signal: if accelerating strongly, even moderate f14 is ok
        return {c:z_f14[c]*0.5+z_trend[c]*0.5 for c in raw}

    elif scheme=='rank_boost':
        # Percentile rank blend: f14 gets 70% weight, trend-boosted rank gets 30%
        codes=list(raw.keys())
        ranked14=sorted(codes,key=lambda c:raw[c]['f14'])
        ranked_trend=sorted(codes,key=lambda c:raw[c]['f14']*(1+raw[c]['trend']*0.5))
        n=len(codes)
        rank14={c:i/n for i,c in enumerate(ranked14)}
        rank_t={c:i/n for i,c in enumerate(ranked_trend)}
        return {c:rank14[c]*0.7+rank_t[c]*0.3 for c in codes}

    elif scheme=='accel_only':
        # Trend-only ranking: stocks where f7>>f14 get promoted heavily
        return {c:z_f14[c]*0.3+z_trend[c]*0.7 for c in raw}

    elif scheme=='decay_flip':
        # Stocks in decay get ranked by f7 instead of f14 (use fresher signal)
        scores={}
        for c,d in raw.items():
            z7=(d['f7']-mu_f)/sd_f  # Z of f7 using f14's distribution
            if d['trend']<-0.15:  # Decaying strongly
                scores[c]=z7  # Use f7 rank
            else:
                scores[c]=z_f14[c]  # Use f14 rank
        return scores

    return {}

def backtest(scheme, trail=TRAIL):
    cash=INIT;pos={};eq=[];trades=[]
    for di,dt in enumerate(cd):
        # Trail exits
        for code,p in list(pos.items()):
            if code not in idx or dt not in idx[code]: continue
            px=stocks[code]['close'][idx[code][dt]]
            if px>p['peak']:p['peak']=px
            if px<=p['peak']*(1-trail):
                sp=px*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
                trades.append({'code':code,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,'exit':'trail'})
                del pos[code]

        if di%REBAL==0:
            stock_scores=compute_raw_scores(dt,scheme)
            cand=list(stock_scores.items())
            cand.sort(key=lambda x:x[1],reverse=True)

            selected=[];sel_secs=set()
            for c,s in cand:
                sec=sm.get(c,'')
                if sec and sec in sel_secs: continue
                if len(selected)>=MAX_POS: break
                selected.append((c,s));sel_secs.add(sec)
            top_codes=set(c for c,_ in selected)

            for code in list(pos.keys()):
                if code not in top_codes:
                    px=stocks[code]['close'][idx[code][dt]];sp=px*(1-SLIP-S_FEE-STAX)
                    cash+=pos[code]['shares']*sp
                    trades.append({'code':code,'ret':(sp-pos[code]['bp'])/pos[code]['bp'] if pos[code]['bp']>0 else 0,'exit':'rebal'})
                    del pos[code]

            pv_curr=sum(p['shares']*stocks[c]['close'][idx[c][dt]] for c,p in pos.items() if c in idx and dt in idx[c])
            total_nav=cash+pv_curr;target_val=total_nav/len(selected) if selected else 0
            for code,score in selected:
                if code in pos: continue
                if cash<target_val*0.99: break
                buy_val=min(target_val,cash)
                if buy_val<=0: break
                raw=stocks[code]['close'][idx[code][dt]];bp=raw*(1+SLIP+B_FEE)
                if bp>0 and buy_val>bp*0.01:
                    sh=buy_val/bp;cash-=buy_val
                    pos[code]={'shares':sh,'bp':bp,'peak':raw,'bd':dt,'bi':di}

        cash*=(1+RF/TD)
        pv=sum(p['shares']*stocks[c]['close'][idx[c][dt]] for c,p in pos.items() if c in idx and dt in idx[c])
        eq.append(cash+pv)

    ld=cd[-1]
    for code,p in list(pos.items()):
        if code in idx and ld in idx[code]:
            px=stocks[code]['close'][idx[code][ld]];sp=px*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
            trades.append({'code':code,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,'exit':'final'})
    pos.clear()

    v=eq;n_loss=sum(1 for t in trades if t['ret']<0)
    tr=(v[-1]-v[0])/v[0];rs=[(v[i]-v[i-1])/v[i-1] for i in range(1,len(v)) if v[i-1]>0]
    y=len(rs)/TD;cagr=(v[-1]/v[0])**(1/y)-1 if y>0 else 0
    mu=sum(rs)/len(rs) if rs else 0
    sd=(sum((r-mu)**2 for r in rs)/len(rs))**0.5 if rs else 0
    sh=(mu*TD-RF)/(sd*(TD**0.5)) if sd>0 else 0
    pk=v[0];mdd=0.0
    for x in v:
        if x>pk:pk=x
        dd=(pk-x)/pk
        if dd>mdd:mdd=dd
    cm=cagr/mdd if mdd>0 else float('inf')
    w=sum(1 for t in trades if t['ret']>0)
    return {'tr':tr,'cagr':cagr,'sh':sh,'mdd':mdd,'calmar':cm,'nt':len(trades),
            'wr':w/len(trades) if trades else 0,'n_loss':n_loss,
            'loss_rate':n_loss/len(trades)*100 if trades else 0}

# ======================
# TEST
# ======================
print('\n'+'='*90)
print('  多周期因子测试: 7d/10d/14d 趋势调整 (截面Z-Score混合)')
print('  Base: K=1.5 Trail=30% min_f=0.8 Strict-Sector NAV-Equal')
print('='*90)

schemes=[
    'baseline',
    'accelerate',
    'decay_penalty',
    'slope',
    'rank_boost',
    'accel_only',
    'decay_flip',
]

results={}
for scheme in schemes:
    r=backtest(scheme)
    results[scheme]=r
    tag=' *' if r['sh']==max(x['sh'] for x in results.values()) else ''
    tag2=' @' if r['mdd']==min(x['mdd'] for x in results.values()) else ''
    print('  %-20s S=%6.3f R=%7.1f%% DD=%5.1f%% CM=%6.3f Trd=%4d Win=%3.0f%% Loss=%.0f%%%s%s' % (
        scheme, r['sh'], r['tr']*100, r['mdd']*100, r['calmar'],
        r['nt'], r['wr']*100, r['loss_rate'],tag,tag2))

base_r=results['baseline']
print('\n'+'='*90)
print('  vs Baseline')
print('='*90)
print('  %-20s %8s %8s %8s %8s' % ('Scheme','dSharpe','dRet%','dMDD%','dTrd'))
print('  '+'-'*55)
for scheme,r in results.items():
    if scheme=='baseline': continue
    ds=r['sh']-base_r['sh'];dr=(r['tr']-base_r['tr'])*100
    dd=(r['mdd']-base_r['mdd'])*100;dtrd=r['nt']-base_r['nt']
    impr=(ds>0 or dd<0)
    tag=' *' if impr else ''
    print('  %-20s %+7.3f %+7.1f %+7.1f %+5d%s' % (scheme,ds,dr,dd,dtrd,tag))

# Best detail
best_scheme=max(results,key=lambda x:results[x]['sh'])
br=results[best_scheme]
print('\n'+'='*90)
print('  BEST: %s' % best_scheme)
print('  S=%.3f R=%.1f%% DD=%.1f%% CM=%.3f Trd=%d Loss=%d(%.0f%%)' % (
    br['sh'],br['tr']*100,br['mdd']*100,br['calmar'],br['nt'],br['n_loss'],br['loss_rate']))

# Annual comparison (skip - eq is numbers not dicts in simplified mode)
print('\nDone!')
