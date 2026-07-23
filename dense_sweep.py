"""
F30/60/90d × DEV × Trail 密集网格 (75场景)
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
        pre[c]={'bars':bars,'closes':closes,'devs':devs,'sector':info['sector'],'name':info['name']}
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
                trades.append({'ret':ret,'exit':ext,'days':di-h['buy_day']})
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
        trades.append({'ret':ret,'exit':'final','days':len(cd)-1-h['buy_day']}); fin+=1
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
            'np':len(trades),'wr':wr,'ap':ap,'fv':fv,'trl':trl,'tim':tim,'fin':fin,'trades':trades}

def main():
    fd=load_fund()
    stk={}
    for fn in sorted(os.listdir(DATA_DIR)):
        if not fn.endswith('.json') or fn.startswith('_'): continue
        with open(os.path.join(DATA_DIR,fn),'r',encoding='utf-8') as f: d=json.load(f)
        if d['bars'][0]['date']<='2020-01-03' and len(d['bars'])>=1500:
            stk[d['code']]={'name':d['name'],'sector':d['sector'],'bars':d['bars']}
    q={c:s for c,s in stk.items() if c in fd}
    ds_=[set(b['date'] for b in s['bars']) for s in q.values()]
    cd=sorted(ds_[0].intersection(*ds_[1:]))
    print(f'{len(q)} stocks, {len(cd)} days\n')

    buy_ts=[-0.035,-0.040,-0.045,-0.050,-0.055]
    trail_ps=[0.25,0.30,0.35,0.40,0.45]
    times=[30,60,90]

    total=len(buy_ts)*len(trail_ps)*len(times)
    all_r=[]; done=0
    for mh in times:
        for bt_ in buy_ts:
            for tp in trail_ps:
                done+=1
                r=bt(bt_,tp,mh,q,fd,cd)
                all_r.append(r)
                print(f'[{done:>2d}/{total}] F{mh:>2d}d DEV<{bt_:.1%} Tr{tp:.0%} | '
                      f'Sh={r["sh"]:>7.4f} Ret={r["tr"]*100:>6.0f}% DD={r["mdd"]*100:>5.1f}% '
                      f'Trd={r["np"]:>3d} Win={r["wr"]*100:>4.0f}% '
                      f'Trl={r["trl"]} Tim={r["tim"]} Fin={r["fin"]}')

    sorted_all=sorted(all_r,key=lambda r:r['sh'],reverse=True)
    gmax=max(r['sh'] for r in all_r)

    print(f'\n{"="*110}')
    print(f'  TOP 30 | F30/60/90d x DEV x Trail (75场景)')
    print(f'{"="*110}')
    print(f'  {"#":<3s} {"时限":<6s} {"策略":<24s} {"夏普":>7s} {"收益":>7s} {"年化":>6s} '
          f'{"回撤":>6s} {"卡玛":>6s} {"交易":>4s} {"胜率":>5s} {"Tr/Tm/Fi":>9s} {"持仓":>4s}')
    print(f'  {"-"*108}')
    for rank,r in enumerate(sorted_all[:30],1):
        tag=' << MAX' if r['sh']==gmax else ''
        ex=f"{r['trl']}/{r['tim']}/{r['fin']}"
        print(f'  {rank:<3d} F{r["mh"]:>2d}d  DEV<{r["bt"]:.1%} Trail{r["tp"]:.0%}     '
              f'{r["sh"]:>7.4f} {r["tr"]*100:>6.0f}% {r["ar"]*100:>5.1f}% '
              f'{r["mdd"]*100:>5.2f}% {r["cm"]:>6.3f} '
              f'{r["np"]:>4d} {r["wr"]*100:>4.0f}% {ex:>9s} {r["ap"]:>4.1f}{tag}')

    # Best by time
    print(f'\n\n  --- 各时限最优 ---')
    for mh in times:
        sub=[r for r in all_r if r['mh']==mh]
        best=max(sub,key=lambda r:r['sh'])
        print(f'  F{mh:>2d}d  BEST: DEV<{best["bt"]:.1%} Trail{best["tp"]:.0%}  '
              f'Sh={best["sh"]:.4f}  Ret={best["tr"]*100:.0f}%  DD={best["mdd"]*100:.1f}%  '
              f'Trd={best["np"]}  Win={best["wr"]*100:.0f}%  '
              f'Trl={best["trl"]} Time={best["tim"]} Fin={best["fin"]}')

    # Heatmaps
    for mh in times:
        sub=[r for r in all_r if r['mh']==mh]
        bsh=max(r['sh'] for r in sub)
        print(f'\n  --- F{mh:>2d}d 夏普热力图 (best={bsh:.4f}) ---')
        hdr='           '
        for t in trail_ps: hdr+=f' Tr{int(t*100):<4d}'
        print(f'  {hdr}')
        for bt_ in buy_ts:
            row=f'  DEV<{bt_:.1%}'
            for tp in trail_ps:
                m=[x for x in sub if abs(x['bt']-bt_)<0.001 and abs(x['tp']-tp)<0.001]
                if m:
                    sh=m[0]['sh']
                    if sh==bsh: row+=f' *{sh:.2f}*'
                    elif sh>=1.2: row+=f'  {sh:.2f} '
                    elif sh>=1.0: row+=f'  {sh:.2f} '
                    else: row+=f'  {sh:.2f} '
            print(row)

    # Cross-time average
    print(f'\n\n  --- DEVxTrail 均值(跨30/60/90d) ---')
    hdr='           '
    for t in trail_ps: hdr+=f' Tr{int(t*100):<4d}'
    print(f'  {hdr}')
    bc=None; ba=-99
    for bt_ in buy_ts:
        row=f'  DEV<{bt_:.1%}'
        for tp in trail_ps:
            vals=[r['sh'] for r in all_r if abs(r['bt']-bt_)<0.001 and abs(r['tp']-tp)<0.001]
            avg=sum(vals)/len(vals)
            if avg>ba: ba=avg; bc=(bt_,tp)
            row+=f'  {avg:.2f} '
        print(row)
    print(f'  最稳健组合: DEV<{bc[0]:.1%} Trail{bc[1]:.0%} (均Sharpe={ba:.4f})')

    # Exit breakdown
    best=sorted_all[0]
    et=defaultdict(lambda:{'c':0,'r':[]})
    for t in best.get('trades',[]):
        et[t['exit']]['c']+=1; et[t['exit']]['r'].append(t['ret'])
    print(f'\n\n  --- BEST [F{best["mh"]}d DEV<{best["bt"]:.1%} Tr{best["tp"]:.0%}] 退出方式 ---')
    for ext in ['trail','time','final']:
        if ext in et:
            e=et[ext]; ar_=sum(e['r'])/len(e['r'])*100; wr_=sum(1 for r in e['r'] if r>0)/len(e['r'])*100
            print(f'  {ext:<8s} {e["c"]:>4d}笔  均收益={ar_:>+6.1f}%  胜率={wr_:>5.0f}%')

    # Time exit gain distribution
    if 'time' in et:
        trets=sorted(et['time']['r'])
        print(f'\n  --- 时限退出收益分布 ---')
        print(f'  P10={trets[len(trets)//10]*100:+.1f}%  P25={trets[len(trets)//4]*100:+.1f}%  '
              f'中位={trets[len(trets)//2]*100:+.1f}%  '
              f'P75={trets[3*len(trets)//4]*100:+.1f}%  P90={trets[9*len(trets)//10]*100:+.1f}%')

    # Time exit vs hold days
    if 'time' in et:
        tdays=[t.get('days',0) for t in best['trades'] if t['exit']=='time']
        tw=[t for t in best['trades'] if t['exit']=='time' and t['ret']>0]
        tl=[t for t in best['trades'] if t['exit']=='time' and t['ret']<=0]
        print(f'\n  --- 时限退出持天分析 ---')
        print(f'  盈利时限退出: {len(tw)}笔 均持{sum(t.get("days",0) for t in tw)/max(len(tw),1):.0f}天')
        print(f'  亏损时限退出: {len(tl)}笔 均持{sum(t.get("days",0) for t in tl)/max(len(tl),1):.0f}天')

    print(f'\n  Done!')

if __name__=='__main__':
    main()
