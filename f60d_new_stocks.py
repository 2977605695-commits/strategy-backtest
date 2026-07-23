"""
F60d DEV<-4.5% Trail45% 对比: 44只老股票 vs 64只全股票 (含新股)
2024-2026 共同日期区间
"""
import json, os, math, csv
from collections import defaultdict

DATA_DIR = r"C:\Users\home\Desktop\strategy-backtest\data"
FUND_DIR = os.path.join(DATA_DIR, "fundamentals_70stocks")
RISK_FREE=0.025; TD=252; INIT=10_000_000; MA_WIN=5; MP=5
BS=0.003; SS=0.003; BF=0.00025; SF=0.00075
WN=0.50; WR_=0.37; WY=0.13; BUY_THR=-0.045; TRAIL=0.45; MAX_HOLD=60

def calc_ma(d,w):
    m=[]
    for i in range(len(d)):
        if i<w-1: m.append(float('nan'))
        else: m.append(sum(d[i-w+1:i+1])/w)
    return m

def load_fund():
    fd=defaultdict(list)
    for fn in sorted(os.listdir(FUND_DIR)):
        if not fn.endswith('.csv'): continue
        with open(os.path.join(FUND_DIR,fn),'r',encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                try:
                    fd[row['code'].strip()].append({
                        'pub_date':row['pub_date'].strip(),'report_date':row['report_date'].strip(),
                        'roe':float(row['roe']),'net_margin':float(row['net_margin']),
                        'rev_yoy':float(row['rev_yoy'])})
                except: pass
    for c in fd: fd[c].sort(key=lambda x:x['pub_date'])
    return fd

def get_latest(fd,ds):
    latest={}
    for c,reps in fd.items():
        valid=[r for r in reps if r['pub_date']<=ds]
        if valid: latest[c]=valid[-1]
    return latest

def zscores(lf):
    if len(lf)<3: return {}
    mets={'roe':[],'net_margin':[],'rev_yoy':[]}; codes=[]
    for c,fund in lf.items():
        codes.append(c)
        mets['roe'].append(fund['roe']); mets['net_margin'].append(fund['net_margin'])
        mets['rev_yoy'].append(fund['rev_yoy'])
    stats={}
    for k,vals in mets.items():
        mu=sum(vals)/len(vals); var=sum((v-mu)**2 for v in vals)/len(vals)
        stats[k]=(mu,math.sqrt(var) if var>0 else 1.0)
    scores={}
    for i,c in enumerate(codes):
        zr=(mets['roe'][i]-stats['roe'][0])/stats['roe'][1]
        zn=(mets['net_margin'][i]-stats['net_margin'][0])/stats['net_margin'][1]
        zy=(mets['rev_yoy'][i]-stats['rev_yoy'][0])/stats['rev_yoy'][1]
        scores[c]=zn*WN+zr*WR_+zy*WY
    return scores

def bt(stk,q,fd,cd,label):
    cs=set(cd); pre={}
    for c,info in stk.items():
        bars=[b for b in info['bars'] if b['date'] in cs]
        closes=[b['close'] for b in bars]
        ma5=calc_ma(closes,MA_WIN)
        devs=[]
        for i,b in enumerate(bars):
            ma=ma5[i]
            if math.isnan(ma) or ma==0: devs.append(float('nan'))
            else: devs.append((b['close']-ma)/abs(ma))
        pre[c]={'bars':bars,'closes':closes,'devs':devs,'sector':info['sector'],'name':info['name'],'code':c}

    per=INIT/MP; holdings={}; cash=INIT; trades=[]
    dvs=[]; cscores={}; events=[]; trl=0; tim=0; fin=0

    for di,ds in enumerate(cd):
        nf=get_latest(fd,ds)
        if nf:
            ns=zscores(nf)
            if ns: cscores=ns

        sells=[]
        for c,h in list(holdings.items()):
            pc=pre[c]; px=pc['bars'][di]['close']; ext=None
            if di>h['buy_day']:
                if px>h['peak']: h['peak']=px
                if px<=h['peak']*(1-TRAIL): ext='trail'
            if not ext and di-h['buy_day']>=MAX_HOLD: ext='time'
            if ext:
                sp=px*(1-SS); gross=h['pos']*sp; nc=gross-gross*SF
                ret=(sp-h['buy_px'])/h['buy_px']
                events.append({'type':'SELL','stock':pc['name'],'ret':ret,'exit':ext})
                trades.append({'name':pc['name'],'ret':ret,'exit':ext,'days':di-h['buy_day']})
                if ext=='trail': trl+=1
                else: tim+=1
                sells.append((c,nc))
        for c,cr in sells: cash+=cr; del holdings[c]

        held_sec=set(h['sector'] for h in holdings.values())
        held_cd=set(holdings.keys())
        elig=[]
        for c,sc in sorted(cscores.items(),key=lambda x:x[1],reverse=True):
            if c in held_cd: continue
            if c not in pre: continue
            if pre[c]['sector'] in held_sec: continue
            dev=pre[c]['devs'][di]
            if not math.isnan(dev) and dev<BUY_THR: elig.append((c,sc,pre[c]['sector']))

        while len(holdings)<MP and elig and cash>=per:
            c,sc,sec=elig.pop(0); pc=pre[c]; px=pc['bars'][di]['close']
            bp=px*(1+BS); fee=per*BF; pos=(per-fee)/bp
            holdings[c]={'pos':pos,'buy_px':bp,'peak':px,'buy_day':di,'buy_date':ds,'sector':sec}
            cash-=per; held_sec.add(sec)
            events.append({'type':'BUY','stock':pc['name'],'sector':sec,'score':sc,
                          'dev':pre[c]['devs'][di],'date':ds,'code':c})

        pv=cash
        for c,h in holdings.items(): pv+=h['pos']*pre[c]['bars'][di]['close']
        dvs.append({'value':pv,'pos':len(holdings)})

    for c,h in list(holdings.items()):
        pc=pre[c]; fp=pc['bars'][-1]['close']
        sp=fp*(1-SS); gross=h['pos']*sp; nc=gross-gross*SF
        ret=(sp-h['buy_px'])/h['buy_px']
        events.append({'type':'FINAL','stock':pc['name'],'ret':ret})
        trades.append({'name':pc['name'],'ret':ret,'exit':'final','days':len(cd)-1-h['buy_day']}); fin+=1
        cash+=nc; del holdings[c]

    fv=dvs[-1]['value']; rets=[]
    for i in range(1,len(dvs)):
        p,c=dvs[i-1]['value'],dvs[i]['value']
        if p>0: rets.append((c-p)/p)
    pk=dvs[0]['value']; mdd=0.0
    for dv in dvs:
        if dv['value']>pk: pk=dv['value']
        dd=(pk-dv['value'])/pk
        if dd>mdd: mdd=dd
    tr=(fv-INIT)/INIT
    if len(rets)>1:
        mu=sum(rets)/len(rets); sd=(sum((r-mu)**2 for r in rets)/(len(rets)-1))**0.5
        av=sd*math.sqrt(TD); ar_=mu*TD
        sh=(ar_-RISK_FREE)/av if av>0 else 0
    else: av=sh=ar_=0.0
    cagr=(1+tr)**(TD/max(len(rets),1))-1 if tr>-1 else -1
    wins=sum(1 for t in trades if t['ret']>0); wr=wins/len(trades) if trades else 0

    print(f"\n  {'='*80}")
    print(f"  {label}")
    print(f"  {'='*80}")
    print(f"  {len(q)} stocks, {len(cd)} days ({cd[0]} -> {cd[-1]})")
    print(f"  Sharpe={sh:.4f}  Ret={tr*100:.1f}%  CAGR={cagr*100:.1f}%  DD={mdd*100:.1f}%")
    print(f"  Trades={len(trades)}  Win={wr*100:.0f}%  Trail={trl}  Time={tim}  Final={fin}")

    # Show buys grouped by stock type
    old_stocks=set()
    new_stocks=set()
    for e in events:
        if e['type']=='BUY':
            if e['code'].startswith('688') or e['code'].startswith('301'):
                new_stocks.add(e['stock'])
            else:
                old_stocks.add(e['stock'])

    print(f"\n  买入股票: {len(old_stocks)+len(new_stocks)}只")
    print(f"  老股票({len(old_stocks)}): {', '.join(sorted(old_stocks))}")
    print(f"  新股/科创板({len(new_stocks)}): {', '.join(sorted(new_stocks))}")

    # Trade summary
    print(f"\n  交易明细:")
    for i,t in enumerate(trades):
        tag=' LOSS' if t['ret']<0 else ''
        print(f"  {i+1:>2d}. {t['name']:<12s} {t['exit']:<6s} {t['ret']*100:>+7.1f}% {t['days']}d{tag}")

    return {'sh':sh,'tr':tr,'mdd':mdd,'np':len(trades),'wr':wr,'trades':trades,'events':events}

def main():
    fd=load_fund()
    all_stk={}
    for fn in sorted(os.listdir(DATA_DIR)):
        if not fn.endswith('.json') or fn.startswith('_'): continue
        with open(os.path.join(DATA_DIR,fn),'r',encoding='utf-8') as f: d=json.load(f)
        all_stk[d['code']]={'name':d['name'],'sector':d['sector'],'bars':d['bars']}

    # Version A: 44 veteran stocks (>=1500 bars, from 2020)
    vet_stk={c:s for c,s in all_stk.items() if len(s['bars'])>=1500 and s['bars'][0]['date']<='2020-01-03'}
    # Version B: all stocks with >=500 bars
    all_stk_b={c:s for c,s in all_stk.items() if len(s['bars'])>=500}

    # Filter to those with fundamentals
    vet_stk={c:s for c,s in vet_stk.items() if c in fd}
    all_stk_b={c:s for c,s in all_stk_b.items() if c in fd}

    # Common dates for each
    ds_v=[set(b['date'] for b in s['bars']) for s in vet_stk.values()]
    cd_v=sorted(ds_v[0].intersection(*ds_v[1:]))

    ds_a=[set(b['date'] for b in s['bars']) for s in all_stk_b.values()]
    cd_a=sorted(ds_a[0].intersection(*ds_a[1:]))

    # Run both
    r_vet=bt(vet_stk,vet_stk,fd,cd_v,"A: 44只老股票 (2020起, 1451天)")
    r_all=bt(all_stk_b,all_stk_b,fd,cd_a,"B: 64只全股票 (含科创板新股, 412天)")

    # Also run 44-stock version on the SAME 412-day period for fair comparison
    vet_stk_short={c:s for c,s in vet_stk.items()}
    r_vet_short=bt(vet_stk_short,vet_stk_short,fd,cd_a,"C: 44只老股票 (同B的412天周期, 公平对比)")

    print(f"\n\n{'='*80}")
    print(f"  三方对比")
    print(f"  {'='*80}")
    print(f"  {'':<30s} {'夏普':>7s} {'收益':>8s} {'回撤':>7s} {'交易':>5s} {'胜率':>6s}")
    print(f"  {'-'*65}")
    for label,r in [("A: 44只 6年(1451天)",r_vet),("B: 64只 2年(412天)",r_all),("C: 44只 2年(412天,公平)",r_vet_short)]:
        print(f"  {label:<30s} {r['sh']:>7.4f} {r['tr']*100:>7.1f}% {r['mdd']*100:>6.1f}% {r['np']:>5d} {r['wr']*100:>5.0f}%")

    # Show which new stocks were bought
    print(f"\n\n  --- B版本独有买入(科创板新股) ---")
    b_buys=set()
    for e in r_all['events']:
        if e['type']=='BUY':
            b_buys.add((e['stock'],e['sector']))
    c_buys=set()
    for e in r_vet_short['events']:
        if e['type']=='BUY':
            c_buys.add((e['stock'],e['sector']))
    new_only=b_buys-c_buys
    if new_only:
        for name,sec in sorted(new_only):
            print(f"  {name} ({sec})")
    else:
        print(f"  (无)")

    common=b_buys&c_buys
    print(f"\n  新老版本共同买入: {len(common)}只")
    print(f"  B版独有买入: {len(new_only)}只 (新股)")

    print(f"\n  Done!")

if __name__=='__main__':
    main()
