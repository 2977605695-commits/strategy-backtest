"""
减亏测试: C低因子过滤 + E崩盘熔断
"""
import sys,io,os,math
from collections import defaultdict
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
os.chdir(r'C:\Users\26776\Desktop\strategy-backtest')
from data_loader import load_prices,calc_ma,get_common_dates

INIT=10_000_000;RF=0.025;TD=252;MAX_POS=5
SLIP=0.003;B_FEE=0.00025;S_FEE=0.00025;STAX=0.0005
K=1.5;LB=14;TRAIL=0.30;REBAL=21

FUND_DIR='data/fundamentals_70stocks'
import csv
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
    ma_vol=calc_ma(vols,20)
    vals={}
    for i in range(n):
        if i<LB or math.isnan(ma_vol[i]): continue
        w=vols[i-19:i+1];mu=sum(w)/20;var=sum((v-mu)**2 for v in w)/20;std=var**0.5
        thr=ma_vol[i]+K*std
        ps=0.0;rs=0.0
        for j in range(max(0,i-LB+1),i+1):
            erupt=vols[j]>=thr
            if erupt:
                prev=(j>0 and vols[j-1]>=thr)
                if prev: rs+=vols[j]
                else: ps+=vols[j]
        vals[dates[i]]=ps/rs if rs>0 else float('nan')
    factor[code]=vals

def backtest(min_factor, crash_guard):
    cash=INIT;pos={};eq=[];trades=[];guards_triggered=0;guard_days=0
    idx={c:{d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks}
    prev_nav=INIT

    for di,dt in enumerate(cd):
        # --- Crash Guard: daily check ---
        pv_curr=sum(p['shares']*stocks[c]['close'][idx[c][dt]] for c,p in pos.items() if c in idx and dt in idx[c])
        nav=cash+pv_curr
        daily_ret=(nav-prev_nav)/prev_nav if prev_nav>0 else 0

        if crash_guard and daily_ret<-0.05:
            # Circuit breaker: sell everything, stay cash for 5 days
            for code,p in list(pos.items()):
                px=stocks[code]['close'][idx[code][dt]];sp=px*(1-SLIP-S_FEE-STAX)
                cash+=p['shares']*sp
                trades.append({'code':code,'name':stocks[code]['name'],
                    'bd':p['bd'],'sd':dt,
                    'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,
                    'exit':'crash_guard'})
            guards_triggered+=1
            pos.clear()
            guard_days=5  # Stay in cash for 5 trading days
            nav=cash

        if guard_days>0:
            guard_days-=1
            prev_nav=nav
            cash*=(1+RF/TD)
            eq.append({'date':dt,'equity':cash+sum(p['shares']*stocks[c]['close'][idx[c][dt]] for c,p in pos.items() if c in idx and dt in idx[c]),'pos':len(pos)})
            continue

        # Trail exits
        for code,p in list(pos.items()):
            if code not in idx or dt not in idx[code]: continue
            px=stocks[code]['close'][idx[code][dt]]
            if px>p['peak']:p['peak']=px
            if px<=p['peak']*(1-TRAIL):
                sp=px*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
                trades.append({'code':code,'name':stocks[code]['name'],
                    'bd':p['bd'],'sd':dt,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,
                    'exit':'trail'})
                del pos[code]

        if di%REBAL==0:
            cand=[(c,factor.get(c,{}).get(dt,float('nan'))) for c in stocks]
            cand=[(c,s) for c,s in cand if not math.isnan(s) and c in idx and dt in idx[c]]
            # C: Filter low factor entries
            if min_factor:
                cand=[(c,s) for c,s in cand if s>=min_factor]
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
                    trades.append({'code':code,'name':stocks[code]['name'],
                        'bd':pos[code]['bd'],'sd':dt,
                        'ret':(sp-pos[code]['bp'])/pos[code]['bp'] if pos[code]['bp']>0 else 0,
                        'exit':'rebal'})
                    del pos[code]

            pv_curr=sum(p['shares']*stocks[c]['close'][idx[c][dt]] for c,p in pos.items() if c in idx and dt in idx[c])
            total_nav=cash+pv_curr
            target_val=total_nav/len(selected) if selected else 0

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
        pv_curr2=sum(p['shares']*stocks[c]['close'][idx[c][dt]] for c,p in pos.items() if c in idx and dt in idx[c])
        eq.append({'date':dt,'equity':cash+pv_curr2,'pos':len(pos)})
        prev_nav=cash+pv_curr2

    ld=cd[-1]
    for code,p in list(pos.items()):
        if code in idx and ld in idx[code]:
            px=stocks[code]['close'][idx[code][ld]];sp=px*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
            trades.append({'code':code,'name':stocks[code]['name'],
                'bd':p['bd'],'sd':ld,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,
                'exit':'final'})
    pos.clear()

    v=[d['equity'] for d in eq]
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
    n_loss=sum(1 for t in trades if t['ret']<0)

    exits={}
    for t in trades:
        e=t['exit'];exits[e]=exits.get(e,{'cnt':0,'ret':0.0,'loss':0})
        exits[e]['cnt']+=1;exits[e]['ret']+=t['ret']
        if t['ret']<0: exits[e]['loss']+=1
    for e in exits:
        exits[e]['avg']=exits[e]['ret']/exits[e]['cnt']*100 if exits[e]['cnt'] else 0

    # Max single-day drawdown
    max_daily_dd=min(rs)*100 if rs else 0

    return {'tr':tr,'cagr':cagr,'sh':sh,'mdd':mdd,'calmar':cm,
        'nt':len(trades),'wr':w/len(trades) if trades else 0,
        'n_loss':n_loss,'loss_rate':n_loss/len(trades)*100 if trades else 0,
        'hp':sum(1 for d in eq if d['pos']>0)/len(eq),
        'exits':exits,'guards':guards_triggered,'max_daily_dd':max_daily_dd,
        'eq':eq}

# ======================
# TEST
# ======================
print('='*85)
print('  减亏测试: C低因子过滤 + E崩盘熔断')
print('  Baseline: K=1.5 LB=14 Trail=30% Strict-Sector NAV-Equal')
print('='*85)

configs=[
    ('#0 Baseline', None, False),
    ('C1: min_factor=0.5', 0.5, False),
    ('C2: min_factor=0.8', 0.8, False),
    ('C3: min_factor=1.0', 1.0, False),
    ('C4: min_factor=1.2', 1.2, False),
    ('E1: crash_guard -5%', None, True),
    ('E2: crash_guard -4%', None, True),
    ('C2+E1: f>=0.8 + crash-5%', 0.8, True),
    ('C3+E1: f>=1.0 + crash-5%', 1.0, True),
    ('C2+E2: f>=0.8 + crash-4%', 0.8, True),
]

results={}
for label,min_f,cg in configs:
    r=backtest(min_f,cg)
    results[label]=r
    print('  %-35s S=%6.3f R=%7.1f%% DD=%5.1f%% CM=%6.3f Trd=%4d Win=%3.0f%% Loss=%.0f%% Guard=%d' % (
        label[:35], r['sh'], r['tr']*100, r['mdd']*100, r['calmar'],
        r['nt'], r['wr']*100, r['loss_rate'], r['guards']))

# vs Baseline
base=results['#0 Baseline']
print('\n'+'='*85)
print('  vs Baseline')
print('='*85)
print('  %-35s %8s %8s %8s %8s %8s %8s' % ('Config','dSharpe','dRet%','dMDD%','dLoss%','dTrd','Guard'))
print('  '+'─'*75)
for label,r in results.items():
    if 'Baseline' in label: continue
    ds=r['sh']-base['sh'];dr=(r['tr']-base['tr'])*100
    dd=(r['mdd']-base['mdd'])*100;dl=r['loss_rate']-base['loss_rate']
    dtrd=r['nt']-base['nt']
    improved=(ds>0 or dd<0 or dl<0)
    tag=' *' if improved else ''
    print('  %-35s %+7.3f %+7.1f %+7.1f %+7.1f%% %+5d %5d%s' % (
        label[:35],ds,dr,dd,dl,dtrd,r['guards'],tag))

# Annual for best
print('\n'+'='*85)
print('  年度收益对比')
print('='*85)
for label in ['#0 Baseline','C2: min_factor=0.8','E1: crash_guard -5%','C2+E1: f>=0.8 + crash-5%']:
    r=results.get(label)
    if not r: continue
    yr=defaultdict(lambda:{'s':None,'e':None})
    for d in r['eq']:
        yk=d['date'][:4]
        if yr[yk]['s'] is None: yr[yk]['s']=d['equity']
        yr[yk]['e']=d['equity']
    ann={y:(v['e']-v['s'])/v['s']*100 for y,v in yr.items() if v['s'] and v['e'] and v['s']>0}
    parts=' '.join('%s:%+5.1f%%' % (y,ann.get(y,0)) for y in sorted(ann.keys()))
    print('  %-35s %s' % (label[:35],parts))

# Crash guard detail
print('\n'+'='*85)
print('  崩盘熔断触发详情')
print('='*85)
r_e1=results['E1: crash_guard -5%']
r_e2=results['E2: crash_guard -4%']
print('  E1 (-5%): %d triggers' % r_e1['guards'])
print('  E2 (-4%): %d triggers' % r_e2['guards'])
print('  最大单日跌幅(baseline): %.2f%%' % base['max_daily_dd'])
if r_e1['guards']>0:
    cg_trades=[t for t in r_e1.get('_eq',[])]  # need trades
    # re-run with tracking
    pass

# Best exit comparison
best_guard_label=max(results,key=lambda x:results[x]['sh'])
print('\n'+'='*85)
print('  BEST: %s' % best_guard_label)
br=results[best_guard_label]
print('  S=%.3f R=%.1f%% DD=%.1f%% CM=%.3f Trd=%d Loss=%d(%.0f%%)' % (
    br['sh'],br['tr']*100,br['mdd']*100,br['calmar'],br['nt'],
    br['n_loss'],br['loss_rate']))
print('  Exits:')
for e,d in sorted(br['exits'].items()):
    print('    %-15s %3d trades  avg=%.1f%%  loss=%d' % (e,d['cnt'],d['avg'],d['loss']))

print('\nDone!')
