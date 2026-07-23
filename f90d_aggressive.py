"""
进取版: F90d DEV<-5.0% Trail30% 64只全股票 完整操作
"""
import json, os, math, csv
from collections import defaultdict

DATA_DIR = r"C:\Users\home\Desktop\strategy-backtest\data"
FUND_DIR = os.path.join(DATA_DIR, "fundamentals_70stocks")
RISK_FREE=0.025; TD=252; INIT=10_000_000; MA_WIN=5; MP=5
BS=0.003; SS=0.003; BF=0.00025; SF=0.00075
WN=0.50; WR_=0.37; WY=0.13; BUY_THR=-0.050; TRAIL=0.30; MAX_HOLD=90

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

def main():
    fd=load_fund()
    stk={}
    for fn in sorted(os.listdir(DATA_DIR)):
        if not fn.endswith('.json') or fn.startswith('_'): continue
        with open(os.path.join(DATA_DIR,fn),'r',encoding='utf-8') as f: d=json.load(f)
        stk[d['code']]={'name':d['name'],'sector':d['sector'],'bars':d['bars']}
    q={c:s for c,s in stk.items() if len(s['bars'])>=500 and c in fd}
    ds_=[set(b['date'] for b in s['bars']) for s in q.values()]
    cd=sorted(ds_[0].intersection(*ds_[1:]))
    cs=set(cd)

    pre={}
    for c,info in q.items():
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
                events.append({'type':'SELL','stock':pc['name'],'sector':h['sector'],
                              'buy_px':h['buy_px'],'sell_px':sp,'ret':ret,
                              'date':ds,'buy_date':h['buy_date'],'peak':h['peak'],
                              'days':di-h['buy_day'],'exit':ext})
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
            if not math.isnan(dev) and dev<BUY_THR:
                elig.append((c,sc,pre[c]['sector']))

        while len(holdings)<MP and elig and cash>=per:
            c,sc,sec=elig.pop(0); pc=pre[c]; px=pc['bars'][di]['close']
            bp=px*(1+BS); fee=per*BF; pos=(per-fee)/bp
            holdings[c]={'pos':pos,'buy_px':bp,'peak':px,'buy_day':di,'buy_date':ds,'sector':sec}
            cash-=per; held_sec.add(sec)
            events.append({'type':'BUY','stock':pc['name'],'sector':sec,
                          'price':bp,'dev':pre[c]['devs'][di],'score':sc,
                          'date':ds,'code':c})

        pv=cash
        for c,h in holdings.items(): pv+=h['pos']*pre[c]['bars'][di]['close']
        dvs.append({'date':ds,'value':pv,'pos':len(holdings)})

    for c,h in list(holdings.items()):
        pc=pre[c]; fp=pc['bars'][-1]['close']
        sp=fp*(1-SS); gross=h['pos']*sp; nc=gross-gross*SF
        ret=(sp-h['buy_px'])/h['buy_px']
        events.append({'type':'FINAL','stock':pc['name'],'sector':h['sector'],
                      'buy_px':h['buy_px'],'sell_px':sp,'ret':ret,
                      'date':cd[-1],'buy_date':h['buy_date'],'days':len(cd)-1-h['buy_day']})
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

    # ================================================================
    print("="*100)
    print(f"  进取版 F90d DEV<{-BUY_THR:.1%} Trail{TRAIL:.0%}  64只全股票")
    print(f"  {len(q)} stocks, {len(cd)} days ({cd[0]} -> {cd[-1]})")
    print("="*100)
    print(f"\n  绩效: {INIT:,.0f} -> {fv:,.0f} | +{tr*100:.1f}% | 年化{cagr*100:.1f}%")
    print(f"  夏普 {sh:.4f} | 回撤 {mdd*100:.1f}% | {len(trades)}笔")
    print(f"  Trail={trl} | Time={tim} | Final={fin}")

    # Timeline
    print(f"\n{'='*100}")
    print(f"  完整时间线 ({len(events)} 个事件)")
    print(f"{'='*100}")

    current_held=[]
    for ev in events:
        if ev['type']=='BUY': current_held.append(ev['stock'])
        elif ev['type'] in ('SELL','FINAL'):
            if ev['stock'] in current_held: current_held.remove(ev['stock'])

        if ev['type']=='BUY':
            is_new = ev['code'].startswith('688') or ev['code'].startswith('301')
            tag = ' [新股]' if is_new else ''
            print(f"\n  {ev['date']} [买入]{tag} {ev['stock']:<10s} {ev['sector']:<16s} "
                  f"@{ev['price']:>8.2f}  DEV={ev['dev']:.1%}  得分={ev['score']:.3f}")
            print(f"    持仓({len(current_held)}): {', '.join(current_held)}")

        elif ev['type']=='SELL':
            tag='Trail' if ev['exit']=='trail' else f"{ev['days']}d时限"
            print(f"\n  {ev['date']} [{tag}] {ev['stock']:<10s} {ev['sector']:<16s} "
                  f"买@{ev['buy_px']:.2f} -> 卖@{ev['sell_px']:.2f}  "
                  f"收益={ev['ret']:.1%}  高点@{ev['peak']:.2f}")
            print(f"    剩余持仓({len(current_held)}): {', '.join(current_held) if current_held else '(空仓)'}")

        elif ev['type']=='FINAL':
            print(f"\n  {ev['date']} [期末] {ev['stock']:<10s} {ev['sector']:<16s} "
                  f"买@{ev['buy_px']:.2f} -> 卖@{ev['sell_px']:.2f}  "
                  f"收益={ev['ret']:.1%}  持{ev['days']}天")

    # Trade summary
    print(f"\n\n{'='*80}")
    print(f"  全部 {len(trades)} 笔交易明细")
    print(f"{'='*80}")
    wins=0
    for i,t in enumerate(trades):
        tag=' LOSS' if t['ret']<0 else ''
        if t['ret']>0: wins+=1
        print(f"  {i+1:>2d}. {t['name']:<12s} {t['exit']:<6s} {t['ret']*100:>+7.1f}% {t['days']}d{tag}")
    print(f"\n  胜率: {wins}/{len(trades)}={wins/len(trades)*100:.0f}%")

    # New vs old stats
    new_wins=sum(1 for t in trades if t['ret']>0 and any(e['type']=='BUY' and e['stock']==t['name'] and (e.get('code','').startswith('688') or e.get('code','').startswith('301')) for e in events))
    print(f"\n  Done!")

if __name__=='__main__':
    main()
