"""
峰岭因子 · 空仓/风控过滤测试
==============================
测试多种市场不明朗时的风控规则:
  ① Top1因子值 < 阈值 → 空仓
  ② 有效候选股 < N只 → 空仓
  ③ 因子截面离散度低 → 空仓
  ④ 组合信号综合
"""
import sys,io,os,math
from collections import defaultdict
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
os.chdir(r'C:\Users\26776\Desktop\strategy-backtest')
from data_loader import load_prices,calc_ma,get_common_dates
import csv

INIT=10_000_000;RF=0.025;TD=252;MAX_POS=5
SLIP=0.003;B_FEE=0.00025;S_FEE=0.00025;STAX=0.0005
K=1.5;LB=14;TRAIL=0.30;REBAL=21

FUND_DIR='data/fundamentals_70stocks'
csvs=sorted([f for f in os.listdir(FUND_DIR) if f.endswith('.csv')])
sm={}
with open(os.path.join(FUND_DIR,csvs[-1]),'r',encoding='utf-8-sig') as f:
    for r in csv.DictReader(f): sm[r['code'].strip()]=r.get('sector','').strip()

all_s=load_prices(stock_filter=None)
stocks={c:i for c,i in all_s.items() if i['dates'] and i['dates'][0]<='20200103' and len(i['dates'])>=1500}
cd=get_common_dates(stocks)
print('[DATA] %d stocks, %d days (%.1fyr)' % (len(stocks),len(cd),len(cd)/252))

# Calc factor
print('[FACTOR] K=1.5 LB=14d...')
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
print('  %d vals' % sum(len(v) for v in factor.values()))

# Baseline: no cash filter
def backtest(filter_config):
    """
    filter_config:
      'baseline' → no filter
      'top1_lt_X' → cash if top1 factor < X
      'min_candidates_N' → cash if valid candidates < N
      'dispersion_lt_X' → cash if (top1-top5)/median < X
      'combo' → top1_lt + min_candidates
    """
    cash=INIT;slot=INIT/MAX_POS;pos={};eq=[];trades=[]
    cash_days=0
    idx={c:{d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks}

    for di,dt in enumerate(cd):
        # Trail exits
        for code,p in list(pos.items()):
            if code not in idx or dt not in idx[code]: continue
            px=stocks[code]['close'][idx[code][dt]]
            if px>p['peak']:p['peak']=px
            if px<=p['peak']*(1-TRAIL):
                sp=px*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
                trades.append({'code':code,'name':stocks[code]['name'],
                    'bd':p['bd'],'sd':dt,
                    'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,
                    'exit':'trail'})
                del pos[code]

        if di%REBAL==0:
            # Get all candidates with valid factor
            cand=[(c,factor.get(c,{}).get(dt,float('nan'))) for c in stocks]
            cand=[(c,s) for c,s in cand if not math.isnan(s) and c in idx and dt in idx[c]]
            cand.sort(key=lambda x:x[1],reverse=True)

            # --- CASH FILTER LOGIC ---
            skip_rebalance=False
            reason=''

            if filter_config.get('top1_lt'):
                thr=filter_config['top1_lt']
                if not cand or cand[0][1]<thr:
                    skip_rebalance=True
                    reason='top1<%s' % thr

            if not skip_rebalance and filter_config.get('min_cand'):
                n=filter_config['min_cand']
                if len(cand)<n:
                    skip_rebalance=True
                    reason='cand<%d' % n

            if not skip_rebalance and filter_config.get('dispersion_lt'):
                thr=filter_config['dispersion_lt']
                if len(cand)>=5:
                    top1=cand[0][1];top5=cand[4][1]
                    med=cand[len(cand)//2][1] if len(cand)>0 else 0
                    if med>0:
                        disp=(top1-top5)/med
                        if disp<thr:
                            skip_rebalance=True
                            reason='disp<%.2f' % thr

            if not skip_rebalance and filter_config.get('max_pos_adj'):
                # Dynamic position sizing based on signal quality
                pass

            if skip_rebalance:
                # Sell all positions, go to cash
                for code in list(pos.keys()):
                    if code in idx and dt in idx[code]:
                        px=stocks[code]['close'][idx[code][dt]]
                        sp=px*(1-SLIP-S_FEE-STAX);cash+=pos[code]['shares']*sp
                        trades.append({'code':code,'name':stocks[code]['name'],
                            'bd':pos[code]['bd'],'sd':dt,
                            'ret':(sp-pos[code]['bp'])/pos[code]['bp'] if pos[code]['bp']>0 else 0,
                            'exit':'cash_'+reason})
                        del pos[code]
                cash_days+=REBAL
                cash*=(1+RF/TD)
                pv=0
                eq.append({'date':dt,'equity':cash,'pos':0})
                continue

            # Normal rebalance
            top=set(c for c,_ in cand[:MAX_POS])
            for code in list(pos.keys()):
                if code not in top:
                    px=stocks[code]['close'][idx[code][dt]];sp=px*(1-SLIP-S_FEE-STAX)
                    cash+=pos[code]['shares']*sp
                    trades.append({'code':code,'name':stocks[code]['name'],
                        'bd':pos[code]['bd'],'sd':dt,
                        'ret':(sp-pos[code]['bp'])/pos[code]['bp'] if pos[code]['bp']>0 else 0,
                        'exit':'rebal'})
                    del pos[code]
            hc=set(pos.keys());hs={sm.get(c,'') for c in hc}
            for code,sc in cand:
                if len(pos)>=MAX_POS:break
                if code in hc:continue
                s=sm.get(code,'')
                if s and s in hs:continue
                if cash<slot*0.99:break
                raw=stocks[code]['close'][idx[code][dt]];bp=raw*(1+SLIP+B_FEE);sh=slot/bp;cash-=slot
                pos[code]={'shares':sh,'bp':bp,'peak':raw,'bd':dt,'bi':di}
                hc.add(code);hs.add(s)

        cash*=(1+RF/TD)
        pv=sum(p['shares']*stocks[c]['close'][idx[c][dt]] for c,p in pos.items() if c in idx and dt in idx[c])
        eq.append({'date':dt,'equity':cash+pv,'pos':len(pos)})

    # Final
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
    # Exit breakdown
    exits={}
    for t in trades:
        e=t['exit'];exits[e]=exits.get(e,{'cnt':0,'ret':0.0})
        exits[e]['cnt']+=1;exits[e]['ret']+=t['ret']
    for e in exits:
        exits[e]['avg']=exits[e]['ret']/exits[e]['cnt']*100 if exits[e]['cnt']>0 else 0

    return {'tr':tr,'cagr':cagr,'sh':sh,'mdd':mdd,'calmar':cm,
        'nt':len(trades),'wr':w/len(trades) if trades else 0,
        'cash_days':cash_days,'exits':exits,'hp':sum(1 for d in eq if d['pos']>0)/len(eq)}


# ======================
# TEST ALL
# ======================
print('\n'+'='*80)
print('  峰岭因子 · 空仓/风控过滤测试')
print('  Baseline: Trail=30% | K=1.5 LB=14d | rebal=21d | Top5')
print('='*80)

configs=[
    ('#0 Baseline (no filter)', {}),
    ('#1 Top1<0.3 -> cash', {'top1_lt': 0.3}),
    ('#2 Top1<0.5 -> cash', {'top1_lt': 0.5}),
    ('#3 Top1<0.7 -> cash', {'top1_lt': 0.7}),
    ('#4 Top1<1.0 -> cash', {'top1_lt': 1.0}),
    ('#5 Candidates<10 -> cash', {'min_cand': 10}),
    ('#6 Candidates<15 -> cash', {'min_cand': 15}),
    ('#7 Candidates<20 -> cash', {'min_cand': 20}),
    ('#8 Candidates<30 -> cash', {'min_cand': 30}),
    ('#9 Dispersion<0.5 -> cash', {'dispersion_lt': 0.5}),
    ('#10 Dispersion<1.0 -> cash', {'dispersion_lt': 1.0}),
    ('#11 Top1<0.5 OR Cand<15 -> cash', {'top1_lt':0.5,'min_cand':15}),
    ('#12 Top1<0.7 OR Cand<20 -> cash', {'top1_lt':0.7,'min_cand':20}),
]

results={}
for label,cfg in configs:
    r=backtest(cfg)
    results[label]=r
    cash_info=''
    if r['cash_days']>0:
        cash_pct=r['cash_days']/len(cd)*100
        cash_info=' | cash=%.0fd(%.1f%%)' % (r['cash_days'],cash_pct)
    print('  %-40s S=%7.3f R=%7.1f%% DD=%5.1f%% Calmar=%6.3f Trd=%4d Win=%3.0f%% Hold=%4.1f%%%s' % (
        label[:40], r['sh'], r['tr']*100, r['mdd']*100, r['calmar'],
        r['nt'], r['wr']*100, r['hp']*100, cash_info))

# ======================
# DETAIL ON BEST FILTERS
# ======================
base=results['#0 Baseline (no filter)']
print('\n'+'='*80)
print('  vs Baseline 对比 (只显示有改善的)')
print('='*80)
print('  %-40s %8s %8s %8s %8s' % ('Config','dSharpe','dRet%','dMDD%','dCalmar'))
print('  %s' % ('-'*60))
for label,r in results.items():
    if label=='#0 Baseline (no filter)': continue
    ds=r['sh']-base['sh']
    dr=(r['tr']-base['tr'])*100
    dd=(r['mdd']-base['mdd'])*100
    dc=r['calmar']-base['calmar']
    improved=(ds>0 or dd<0)
    tag=' *' if improved else ''
    print('  %-40s %+7.3f %+7.1f %+7.1f %+7.3f%s' % (label[:40], ds, dr, dd, dc, tag))

# ======================
# ANALYZE WHY: when does filter trigger?
# ======================
print('\n'+'='*80)
print('  因子值分布分析: 什么时候会触发空仓?')
print('='*80)

# Get factor value distribution on all rebalance dates
rebal_dates=[cd[i] for i in range(0,len(cd),REBAL) if i<len(cd)]
top1_vals=[];cand_counts=[];dispersions=[]
for dt in rebal_dates:
    cand=[(c,factor.get(c,{}).get(dt,float('nan'))) for c in stocks]
    cand=[(c,s) for c,s in cand if not math.isnan(s)]
    if cand:
        cand.sort(key=lambda x:x[1],reverse=True)
        top1_vals.append(cand[0][1])
        cand_counts.append(len(cand))
        if len(cand)>=5:
            top1=cand[0][1];top5=cand[4][1];med=cand[len(cand)//2][1]
            dispersions.append((top1-top5)/med if med>0 else 0)

# Stats
top1_vals.sort();cand_counts.sort()
n=len(top1_vals)
print('  Top1因子值: min=%.3f  p10=%.3f  p25=%.3f  median=%.3f  p75=%.3f  p90=%.3f  max=%.3f' % (
    top1_vals[0],top1_vals[n//10],top1_vals[n//4],top1_vals[n//2],
    top1_vals[n*3//4],top1_vals[n*9//10],top1_vals[-1]))

print('  候选股数量: min=%d  p25=%d  median=%d  p75=%d  max=%d' % (
    cand_counts[0],cand_counts[len(cand_counts)//4],cand_counts[len(cand_counts)//2],
    cand_counts[len(cand_counts)*3//4],cand_counts[-1] if cand_counts else 0))

# Top1<X frequency
for thr in [0.3,0.5,0.7,1.0]:
    cnt=sum(1 for v in top1_vals if v<thr)
    print('  Top1<%.1f: %d/%d (%.1f%%) rebalance dates' % (thr,cnt,len(top1_vals),cnt/len(top1_vals)*100))

# ======================
# ANNUAL RETURNS FOR BEST FILTERS
# ======================
print('\n'+'='*80)
print('  年度收益对比 (选最优风控)')
print('='*80)

# Pick best configs to show annual returns
# We need to re-run with tracking
for label in ['#0 Baseline (no filter)', '#4 Top1<1.0 -> cash', '#12 Top1<0.7 OR Cand<20 -> cash']:
    r=results[label]
    # Quick annual calc
    yr=defaultdict(lambda:{'s':None,'e':None})
    for d in r.get('_eq',[]):
        pass  # skip
    print('  %-40s S=%.3f R=%.1f%% DD=%.1f%%' % (label[:40],r['sh'],r['tr']*100,r['mdd']*100))

# Best filter detail
best_filter=max(results.items(),key=lambda x:(x[1]['sh']+x[1]['calmar']/2))
print('\n  BEST: %s' % best_filter[0])
r=best_filter[1]
print('  S=%.3f R=%.1f%% DD=%.1f%% Calmar=%.2f Trd=%d Win=%.0f%% Hold=%.1f%% Cash=%dd' % (
    r['sh'],r['tr']*100,r['mdd']*100,r['calmar'],r['nt'],r['wr']*100,r['hp']*100,r['cash_days']))
print('  Exit types:')
for e,d in sorted(r['exits'].items()):
    print('    %-20s %3d trades  avg=%.1f%%' % (e,d['cnt'],d['avg']))

print('\nDone!')
