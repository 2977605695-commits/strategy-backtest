"""
64只全股票(含新股) F30/60/90d × DEV × Trail 网格 + 对比44只老股
"""
import json, os, math, csv
from collections import defaultdict

DATA_DIR = r"C:\Users\home\Desktop\strategy-backtest\data"
FUND_DIR = os.path.join(DATA_DIR, "fundamentals_70stocks")
RISK_FREE=0.025; TD=252; INIT=10_000_000; MA_WIN=5; MP=5
BS=0.003; SS=0.003; BF=0.00025; SF=0.00075
WN=0.50; WR_=0.37; WY=0.13

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

def bt(bt_,tp,mh,stk,fd,cd):
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
    dvs=[]; cscores={}; trl=0; tim=0; fin=0
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
                if px<=h['peak']*(1-tp): ext='trail'
            if not ext and mh and di-h['buy_day']>=mh: ext='time'
            if ext:
                sp=px*(1-SS); gross=h['pos']*sp; nc=gross-gross*SF
                ret=(sp-h['buy_px'])/h['buy_px']
                trades.append({'ret':ret,'exit':ext})
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
            if not math.isnan(dev) and dev<bt_: elig.append((c,sc,pre[c]['sector']))
        while len(holdings)<MP and elig and cash>=per:
            c,sc,sec=elig.pop(0); pc=pre[c]; px=pc['bars'][di]['close']
            bp=px*(1+BS); fee=per*BF; pos=(per-fee)/bp
            holdings[c]={'pos':pos,'buy_px':bp,'peak':px,'buy_day':di,'sector':sec}
            cash-=per; held_sec.add(sec)
        pv=cash
        for c,h in holdings.items(): pv+=h['pos']*pre[c]['bars'][di]['close']
        dvs.append({'value':pv,'pos':len(holdings)})
    for c,h in list(holdings.items()):
        pc=pre[c]; fp=pc['bars'][-1]['close']
        sp=fp*(1-SS); gross=h['pos']*sp; nc=gross-gross*SF
        ret=(sp-h['buy_px'])/h['buy_px']
        trades.append({'ret':ret,'exit':'final'}); fin+=1
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
    cm=cagr/mdd if mdd>0 else float('inf')
    wins=sum(1 for t in trades if t['ret']>0); wr=wins/len(trades) if trades else 0
    ap=sum(dv['pos'] for dv in dvs)/len(dvs)
    return {'bt':bt_,'tp':tp,'mh':mh,'tr':tr,'ar':cagr,'av':av,'sh':sh,'mdd':mdd,'cm':cm,
            'np':len(trades),'wr':wr,'ap':ap,'fv':fv,'trl':trl,'tim':tim,'fin':fin}

def run_grid(stk,q,fd,cd,label):
    buy_ts=[-0.035,-0.040,-0.045,-0.050,-0.055]
    trail_ps=[0.25,0.30,0.35,0.40,0.45]
    times=[30,60,90]
    total=len(buy_ts)*len(trail_ps)*len(times)
    all_r=[]; done=0
    print(f"\n{'='*80}")
    print(f"  {label}: {len(q)} stocks, {len(cd)} days")
    print(f"{'='*80}")
    for mh in times:
        for bt_ in buy_ts:
            for tp in trail_ps:
                done+=1
                r=bt(bt_,tp,mh,stk,fd,cd)
                all_r.append(r)
                print(f'[{done:>2d}/{total}] F{mh:>2d}d DEV<{bt_:.1%} Tr{tp:.0%} | '
                      f'Sh={r["sh"]:>7.4f} Ret={r["tr"]*100:>6.0f}% DD={r["mdd"]*100:>5.1f}% '
                      f'Trd={r["np"]:>3d} Win={r["wr"]*100:>4.0f}% '
                      f'Trl={r["trl"]} Tim={r["tim"]} Fin={r["fin"]}')
    return all_r

def main():
    fd=load_fund()
    all_stk={}
    for fn in sorted(os.listdir(DATA_DIR)):
        if not fn.endswith('.json') or fn.startswith('_'): continue
        with open(os.path.join(DATA_DIR,fn),'r',encoding='utf-8') as f: d=json.load(f)
        all_stk[d['code']]={'name':d['name'],'sector':d['sector'],'bars':d['bars']}

    # 64 stocks (all >=500 bars)
    stk64={c:s for c,s in all_stk.items() if len(s['bars'])>=500 and c in fd}
    # 44 veteran stocks
    stk44={c:s for c,s in all_stk.items() if len(s['bars'])>=1500 and s['bars'][0]['date']<='2020-01-03' and c in fd}

    # Common dates
    ds64=[set(b['date'] for b in s['bars']) for s in stk64.values()]
    cd64=sorted(ds64[0].intersection(*ds64[1:]))
    ds44=[set(b['date'] for b in s['bars']) for s in stk44.values()]
    cd44=sorted(ds44[0].intersection(*ds44[1:]))

    # Run grids
    r64=run_grid(stk64,stk64,fd,cd64,"64只全股票(含新股) - 412天")
    r44=run_grid(stk44,stk44,fd,cd64,"44只老股票 - 同412天")

    # ================================================================
    # Summary: best by time limit
    print(f"\n\n{'='*90}")
    print(f"  64只 vs 44只: 各时限最优对比")
    print(f"{'='*90}")
    print(f"  {'时限':<6s} {'64只最优':<32s} {'夏普':>7s} {'收益':>7s} {'回撤':>6s}  |  "
          f"{'44只最优':<32s} {'夏普':>7s} {'收益':>7s} {'回撤':>6s}")
    print(f"  {'-'*88}")
    for mh in [30,60,90]:
        s64=[r for r in r64 if r['mh']==mh]
        s44=[r for r in r44 if r['mh']==mh]
        b64=max(s64,key=lambda r:r['sh'])
        b44=max(s44,key=lambda r:r['sh'])
        print(f"  F{mh:>2d}d  DEV<{b64['bt']:.1%} Trail{b64['tp']:.0%}               "
              f"{b64['sh']:>7.4f} {b64['tr']*100:>6.0f}% {b64['mdd']*100:>5.1f}%  |  "
              f"DEV<{b44['bt']:.1%} Trail{b44['tp']:.0%}               "
              f"{b44['sh']:>7.4f} {b44['tr']*100:>6.0f}% {b44['mdd']*100:>5.1f}%")

    # Global best
    g64=max(r64,key=lambda r:r['sh'])
    g44=max(r44,key=lambda r:r['sh'])
    print(f"\n  64只全局最优: F{g64['mh']}d DEV<{g64['bt']:.1%} Trail{g64['tp']:.0%}  "
          f"Sharpe={g64['sh']:.4f}  Ret={g64['tr']*100:.0f}%  DD={g64['mdd']*100:.1f}%")
    print(f"  44只全局最优: F{g44['mh']}d DEV<{g44['bt']:.1%} Trail{g44['tp']:.0%}  "
          f"Sharpe={g44['sh']:.4f}  Ret={g44['tr']*100:.0f}%  DD={g44['mdd']*100:.1f}%")

    # Heatmaps side by side
    for mh in [30,60,90]:
        s64=[r for r in r64 if r['mh']==mh]
        s44=[r for r in r44 if r['mh']==mh]
        b64=max(r['sh'] for r in s64)
        b44=max(r['sh'] for r in s44)

        print(f"\n  --- F{mh:>2d}d 热力图: 64只(左) vs 44只(右) ---")
        buy_ts=[-0.035,-0.040,-0.045,-0.050,-0.055]
        trail_ps=[0.25,0.30,0.35,0.40,0.45]
        print(f"  {'':<12s} {'64只 (best='+f'{b64:.2f}'+')':<38s} {'44只 (best='+f'{b44:.2f}'+')':<38s}")
        for bt_ in buy_ts:
            row64=f"  DEV<{bt_:.1%}  "
            row44=f"  DEV<{bt_:.1%}  "
            for tp in trail_ps:
                m64=[x for x in s64 if abs(x['bt']-bt_)<0.001 and abs(x['tp']-tp)<0.001]
                m44=[x for x in s44 if abs(x['bt']-bt_)<0.001 and abs(x['tp']-tp)<0.001]
                sh64=m64[0]['sh'] if m64 else 0
                sh44=m44[0]['sh'] if m44 else 0
                if sh64==b64: row64+=f' *{sh64:.2f}*'
                else: row64+=f'  {sh64:.2f} '
                if sh44==b44: row44+=f' *{sh44:.2f}*'
                else: row44+=f'  {sh44:.2f} '
            print(f'{row64}    {row44}')

    # Average Sharpe by DEVxTrail
    print(f"\n\n  --- 64只 vs 44只: DEVxTrail 均夏普(跨30/60/90d) ---")
    buy_ts=[-0.035,-0.040,-0.045,-0.050,-0.055]
    trail_ps=[0.25,0.30,0.35,0.40,0.45]
    for bt_ in buy_ts:
        for tp in trail_ps:
            avg64=sum(r['sh'] for r in r64 if abs(r['bt']-bt_)<0.001 and abs(r['tp']-tp)<0.001)/3
            avg44=sum(r['sh'] for r in r44 if abs(r['bt']-bt_)<0.001 and abs(r['tp']-tp)<0.001)/3
            diff=avg64-avg44
            d='<<<' if diff<-0.15 else ('<' if diff<-0.05 else ('=' if abs(diff)<0.05 else '>'))
            print(f'  DEV<{bt_:.1%} Tr{tp:.0%}  64={avg64:.3f}  44={avg44:.3f}  d={diff:+.3f} {d}')

    print(f'\n  Done!')

if __name__=='__main__':
    main()
