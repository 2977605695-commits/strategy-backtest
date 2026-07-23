"""
NoChase>10% 基础上:
  2. Trail 重扫 (15%~35%)
  3. Rebal 重扫 (10d/14d/21d/30d)
  4. 禁止买入20日新高
"""
import sys,io,os,math
from collections import defaultdict
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
os.chdir(r'C:\Users\26776\Desktop\strategy-backtest')
from data_loader import load_prices,calc_ma,get_common_dates
import csv

INIT=10_000_000;RF=0.025;TD=252
SLIP=0.003;B_FEE=0.00025;S_FEE=0.00025;STAX=0.0005
K=1.5;LB=14;MIN_F=0.8;NO_CHASE=0.10

FUND_DIR='data/fundamentals_70stocks'
csvs=sorted([f for f in os.listdir(FUND_DIR) if f.endswith('.csv')])
sm={}
with open(os.path.join(FUND_DIR,csvs[-1]),'r',encoding='utf-8-sig') as f:
    for r in csv.DictReader(f): sm[r['code'].strip()]=r.get('sector','').strip()
all_s=load_prices(stock_filter=None)
stocks={c:i for c,i in all_s.items() if i['dates'] and i['dates'][0]<='20200103' and len(i['dates'])>=1500}
cd=get_common_dates(stocks)
factor={}
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
    factor[code]=vals
idx={c:{d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks}

def get_si(code,dt):
    if code not in stocks: return None
    im={d:i for i,d in enumerate(stocks[code]['dates'])}
    return im.get(dt)

def bt(trail,rebal,no_chase,no_20d_high):
    cash=INIT;pos={};trades=[];eq=[]
    for di,dt in enumerate(cd):
        for code,p in list(pos.items()):
            if code not in idx or dt not in idx[code]: continue
            px=stocks[code]['close'][idx[code][dt]]
            if px>p['peak']:p['peak']=px
            if px<=p['peak']*(1-trail):
                sp=px*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
                trades.append({'code':code,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,'exit':'trail'})
                del pos[code]
        if di%rebal==0:
            cand=[(c,factor.get(c,{}).get(dt,float('nan'))) for c in stocks]
            cand=[(c,s) for c,s in cand if not math.isnan(s) and c in idx and dt in idx[c] and s>=MIN_F]

            # Filter: no_chase + no_20d_high
            filtered=[]
            for c,s in cand:
                si=get_si(c,dt)
                if si is None or si<20: filtered.append((c,s)); continue
                px_now=stocks[c]['close'][si]
                # NoChase: 5d rally > X% -> reject
                if no_chase and si>=5:
                    px_5d=stocks[c]['close'][si-5]
                    if px_5d>0 and (px_now-px_5d)/px_5d>no_chase: continue
                # No20dHigh: today's close == 20d high -> reject
                if no_20d_high:
                    hh20=max(stocks[c]['close'][si-19:si+1])
                    if px_now>=hh20*0.995: continue  # within 0.5% of 20d high
                filtered.append((c,s))
            cand=filtered

            cand.sort(key=lambda x:x[1],reverse=True)
            selected=[];sel_secs=set()
            for c,s in cand:
                sec=sm.get(c,'')
                if sec and sec in sel_secs: continue
                if len(selected)>=5: break
                selected.append((c,s));sel_secs.add(sec)
            n_select=len(selected);top=set(c for c,_ in selected)
            for code in list(pos.keys()):
                if code not in top:
                    px=stocks[code]['close'][idx[code][dt]];sp=px*(1-SLIP-S_FEE-STAX)
                    cash+=pos[code]['shares']*sp
                    trades.append({'code':code,'ret':(sp-pos[code]['bp'])/pos[code]['bp'] if pos[code]['bp']>0 else 0,'exit':'rebal'})
                    del pos[code]
            pv2=sum(p['shares']*stocks[c]['close'][idx[c][dt]] for c,p in pos.items() if c in idx and dt in idx[c])
            nav=cash+pv2;target=nav/n_select if n_select>0 else 0
            for code,score in selected:
                if code in pos: continue
                if cash<target*0.99: break
                bv=min(target,cash)
                if bv<=0: break
                raw=stocks[code]['close'][idx[code][dt]];bp=raw*(1+SLIP+B_FEE)
                if bp>0 and bv>bp*0.01:
                    sh=bv/bp;cash-=bv
                    pos[code]={'shares':sh,'bp':bp,'peak':raw,'bd':dt,'bi':di}
        cash*=(1+RF/TD)
        pv3=sum(p['shares']*stocks[c]['close'][idx[c][dt]] for c,p in pos.items() if c in idx and dt in idx[c])
        eq.append(cash+pv3)
    ld=cd[-1]
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
        if x>pk:pk=x
        cur_dd=(pk-x)/pk
        if cur_dd>maxdd:maxdd=cur_dd
    cm=cagr/maxdd if maxdd>0 else float('inf')
    w=sum(1 for t in trades if t['ret']>0);nl=sum(1 for t in trades if t['ret']<0)
    return {'tr':tr,'cagr':cagr,'sh':sh,'mdd':maxdd,'calmar':cm,'nt':len(trades),
        'wr':w/len(trades) if trades else 0,'n_loss':nl,
        'loss_rate':nl/len(trades)*100 if trades else 0}

print('='*95)
print('  NoChase>10% + 三向优化')
print('  基准(灰色): Trail=30% Rebal=21d NoChase=10% No20dHigh=No')
print('='*95)

# ---- 2. Trail re-scan ----
print('\n  --- Trail 重扫 (Rebal=21d, NoChase=10%) ---')
print('  %-6s %7s %8s %6s %6s %6s %5s %5s' % ('Trail','Sharpe','Ret','CAGR','MDD','Calmar','Trd','Win'))
tr_results={}
for tr in [0.15,0.20,0.22,0.25,0.28,0.30,0.35]:
    r=bt(tr,21,NO_CHASE,False)
    tr_results[tr]=r
    tag=' BEST' if r['sh']==max(x['sh'] for x in tr_results.values()) else ''
    base_tr=(tr==0.30)
    mark=' (base)' if base_tr else ''
    print('  %5.0f%%  %7.3f %7.1f%% %5.2f%% %5.1f%% %6.3f %4d %4.0f%%%s%s' % (
        tr*100,r['sh'],r['tr']*100,r['cagr']*100,r['mdd']*100,r['calmar'],r['nt'],r['wr']*100,tag,mark))

# ---- 3. Rebal 细扫 (Trail=22%, NoChase=10%) ----
print('\n  --- Rebal 细扫 18d~24d (Trail=22%, NoChase=10%) ---')
print('  %-6s %7s %8s %6s %6s %6s %5s %5s' % ('Rebal','Sharpe','Ret','CAGR','MDD','Calmar','Trd','Win'))
rb_results={}
for rb in [18,19,20,21,22,23,24]:
    r=bt(0.22,rb,NO_CHASE,False)
    rb_results[rb]=r
    tag=' BEST' if r['sh']==max(x['sh'] for x in rb_results.values()) else ''
    tag2=' MDD' if r['mdd']==min(x['mdd'] for x in rb_results.values()) else ''
    base_rb=(rb==21)
    mark=' (base)' if base_rb else ''
    print('  %4dd  %7.3f %7.1f%% %5.2f%% %5.1f%% %6.3f %4d %4.0f%%%s%s%s' % (
        rb,r['sh'],r['tr']*100,r['cagr']*100,r['mdd']*100,r['calmar'],r['nt'],r['wr']*100,tag,tag2,mark))

# ---- 4. No20dHigh ----
print('\n  --- 禁止20日新高 (Trail=30%, Rebal=21d, NoChase=10%) ---')
print('  %-20s %7s %8s %6s %6s %6s %5s %5s' % ('Config','Sharpe','Ret','CAGR','MDD','Calmar','Trd','Win'))
nh_results={}
for nh in [False,True]:
    r=bt(0.30,21,NO_CHASE,nh)
    label='No20dHigh=ON' if nh else 'No20dHigh=OFF(base)'
    nh_results[label]=r
    print('  %-20s %7.3f %7.1f%% %5.2f%% %5.1f%% %6.3f %4d %4.0f%%' % (
        label,r['sh'],r['tr']*100,r['cagr']*100,r['mdd']*100,r['calmar'],r['nt'],r['wr']*100))

# ---- BEST COMBO ----
print('\n  --- 最佳组合 (Trail=22%) ---')
best_rb=max(rb_results,key=lambda x:rb_results[x]['sh'])
print('  Best Rebal=%dd' % (best_rb))
r_baseline=bt(0.22,21,NO_CHASE,False)
r_combo=bt(0.22,best_rb,NO_CHASE,False)
print('  Baseline(T22,R21,NC10): S=%.3f R=%.1f%% DD=%.1f%% Trd=%d' % (r_baseline['sh'],r_baseline['tr']*100,r_baseline['mdd']*100,r_baseline['nt']))
print('  BestCombo(T22,R%d,NC10): S=%.3f R=%.1f%% DD=%.1f%% Trd=%d' % (best_rb,r_combo['sh'],r_combo['tr']*100,r_combo['mdd']*100,r_combo['nt']))
ds=r_combo['sh']-r_baseline['sh'];dd=(r_combo['mdd']-r_baseline['mdd'])*100;dr=(r_combo['tr']-r_baseline['tr'])*100
print('  Δ: dS=%+.3f dR=%+.0f%% dDD=%+.1fpp' % (ds,dr,dd))

print('\nDone!')
