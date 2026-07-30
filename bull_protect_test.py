"""Bull Market Loss Protection · 4 techniques tested"""
import json,os,math
from collections import defaultdict
from datetime import datetime
DATA_DIR='data';START='2020-01-01';END='2026-07-29'
RF=0.025;TD=252;INIT=int(1e7);F_MA=6;S_MA=15;SL_MA=8

ETF_CODES=['159782','588380','588870','588080','588300','518800','589720','588890','588170',
           '588200','159995','512480','515880','515050','159819','159992','512010',
           '518880','159937','513180','513050','513100','159509','588000','588220',
           '510300','159915','510050','511010','511260','510880','512890','159301']

def load():
    etfs={}
    for code in ETF_CODES:
        path=os.path.join(DATA_DIR,'etf_'+code+'.json')
        if not os.path.exists(path):continue
        d=json.load(open(path,encoding='utf-8'))
        bars=[]
        for b in d['bars']:
            dt=b['date'];px=float(b['close'])
            if len(dt)==8:dt=dt[:4]+'-'+dt[4:6]+'-'+dt[6:8]
            if START<=dt<=END:bars.append({'date':dt,'close':px})
        if bars:etfs[code]={'name':d['name'],'first_date':bars[0]['date'],'bars':bars}
    return etfs

def ma(data,w):
    m=[];n=len(data)
    for i in range(n):
        if i<w-1:m.append(float('nan'))
        else:m.append(sum(data[i-w+1:i+1])/w)
    return m
def slp(ms,lb):
    s=[float('nan')]*len(ms)
    for i in range(len(ms)):
        if i<lb:continue
        ys=ms[i-lb+1:i+1]
        if any(math.isnan(y) for y in ys):continue
        n=len(ys);sx=sy=sxy=sxx=0
        for j,y in enumerate(ys):sx+=j;sy+=y;sxy+=j*y;sxx+=j*j
        d=n*sxx-sx*sx
        if d>0:s[i]=(n*sxy-sx*sy)/d/ms[i] if ms[i]>0 else 0
    return s

def run(etfs,trail_pct,confirm_days,cooldown_days):
    codes=sorted(etfs.keys())
    all_trnd={};all_ratio={};above_ma60={}
    for c in codes:
        bars=etfs[c]['bars'];cl=[b['close'] for b in bars];n=len(bars)
        mf=ma(cl,F_MA);ms=ma(cl,S_MA);msl=ma(cl,SL_MA);slo_=slp(msl,max(SL_MA//2,3))
        m60=ma(cl,60);dts=[b['date'] for b in bars]
        trnd={};rat={};abv={}
        for i in range(n):
            d=dts[i]
            if not math.isnan(mf[i]) and not math.isnan(ms[i]) and ms[i]>0:
                sk=not math.isnan(slo_[i]) and slo_[i]>0
                trnd[d]=mf[i]>ms[i] and sk;rat[d]=mf[i]/ms[i]
            else:trnd[d]=False;rat[d]=1.0
            abv[d]=not math.isnan(m60[i]) and cl[i]>m60[i]
        all_trnd[c]=trnd;all_ratio[c]=rat;above_ma60[c]=abv

    dm={c:{b['date']:b for b in etfs[c]['bars']} for c in codes}
    fd={c:etfs[c]['first_date'] for c in codes}
    ad=set()
    for c in codes:
        for k in dm[c]:ad.add(k)
    all_dates=sorted(ad)

    cash=INIT;pos_code=None;shares=0.0;bp=0.0;peak=0.0;entry_d='';entry_date=None
    last_exit_date='';trades=[];dvs=[]

    for d in all_dates:
        avail=[c for c in codes if fd[c]<=d];dt_obj=datetime.strptime(d,'%Y-%m-%d')

        # Cooldown check
        in_cooldown=False
        if cooldown_days>0 and last_exit_date:
            ld=datetime.strptime(last_exit_date,'%Y-%m-%d')
            if (dt_obj-ld).days<cooldown_days:in_cooldown=True

        if pos_code:
            bar=dm[pos_code].get(d)
            if bar:
                px=bar['close']
                if px>peak:peak=px
                ton=all_trnd[pos_code].get(d,False);er=None
                if px<=peak*(1-trail_pct):er='trail'
                elif not ton:er='off'
                if er:
                    pnl=shares*px-shares*bp
                    trades.append({'pnl':pnl,'r':(px-bp)/bp,'e':er,'c':pos_code,'b':entry_d,'s':d,
                                   'days':(dt_obj-entry_date).days if entry_date else 0})
                    cash=shares*px;pos_code=None;shares=0.0;bp=0.0;peak=0.0
                    last_exit_date=d;entry_date=None

        if not pos_code and cash>0 and not in_cooldown:
            cands=[]
            for c in avail:
                ton=all_trnd[c].get(d,False)
                if not ton:continue
                # Secondary confirmation: need confirm_days consecutive days of trend=true
                if confirm_days>0:
                    passed=True
                    for back_n in range(1,confirm_days+1):
                        # Check if d-back_n exists and trend was true
                        # Simplified: require that d-1 also had trend
                        pass  # We don't have index-based lookup here easily
                    # Actually: we check if the day BEFORE also had trend
                    if confirm_days==1:
                        prev_date=None
                        for j in range(len(all_dates)):
                            if all_dates[j]==d and j>0:prev_date=all_dates[j-1];break
                        if prev_date:
                            passed=all_trnd[c].get(prev_date,False)
                        else:passed=False
                    else:passed=True
                    if not passed:continue
                if not above_ma60.get(c,{}).get(d,False):continue
                bar=dm[c].get(d)
                if bar:cands.append((c,all_ratio[c].get(d,1.0),bar['close']))
            if cands:
                cands.sort(key=lambda x:x[1],reverse=True)
                c,ratio,px=cands[0]
                shares=cash/px;bp=px;peak=px;pos_code=c;entry_d=d;entry_date=dt_obj;cash=0.0

        pos_val=shares*dm[pos_code].get(d,{}).get('close',0) if pos_code else 0
        dvs.append(cash+pos_val)

    if pos_code:
        bar=dm[pos_code].get(all_dates[-1])
        if bar:
            px=bar['close'];pnl=shares*px-shares*bp
            dt_last=datetime.strptime(all_dates[-1],'%Y-%m-%d')
            trades.append({'pnl':pnl,'r':(px-bp)/bp,'e':'final','c':pos_code,'b':entry_d,'s':all_dates[-1],
                           'days':(dt_last-entry_date).days if entry_date else 0})
            cash=shares*px

    fv=cash;rets=[]
    for i in range(1,len(dvs)):
        if dvs[i-1]>0:rets.append((dvs[i]-dvs[i-1])/dvs[i-1])
    if not rets:rets=[0.0]
    pk=dvs[0];mdd=0.0
    for v in dvs:
        if v>pk:pk=v
        dd=(pk-v)/pk
        if dd>mdd:mdd=dd
    tr=(fv-INIT)/INIT;mu=sum(rets)/len(rets)
    sd_=(sum((r-mu)**2 for r in rets)/(len(rets)-1))**0.5 if len(rets)>1 else 0.01
    av=sd_*math.sqrt(TD);sh=(mu*TD-RF)/av if av>0 else 0;ar=(1+tr)**(TD/len(rets))-1 if tr>-1 else -1
    st=[t for t in trades if t['e'] in('trail','off','final')]
    w=sum(1 for t in st if t['r']>0);wr=w/len(st) if st else 0
    return{'sh':sh,'tr':tr,'mdd':mdd,'ar':ar,'np':len(st),'wr':wr,'trades':trades,'dvs':dvs,'fv':fv}

def main():
    etfs=load();codes=sorted(etfs.keys())
    print('%d ETFs  MA%d/%d s%d  ALL with ETF>MA60 filter'%(len(codes),F_MA,S_MA,SL_MA))

    # Grid
    trails=[0.03,0.05,0.07]
    confirms=[0,1]  # 0=no confirm, 1=1-day secondary check
    cooldowns=[0,1,3,5,7]
    TOTAL=len(trails)*len(confirms)*len(cooldowns)
    results=[];count=0
    print('Running %d combos...'%TOTAL)
    best_s=-999

    for trail in trails:
        for cf in confirms:
            for cd in cooldowns:
                count+=1
                r=run(etfs,trail,cf,cd)
                r.update({'trail':trail,'confirm':cf,'cooldown':cd})
                results.append(r)
                if r['sh']>best_s:best_s=r['sh']
                print('  [%3d/%d] Trail=%.0f%% Cfm=%d CD=%dd S=%.3f Ret=%.1f%% DD=%.1f%% Trd=%d Win=%.0f%% Best=%.4f'%(
                    count,TOTAL,trail*100,cf,cd,r['sh'],r['tr']*100,r['mdd']*100,r['np'],r['wr']*100,best_s))

    results.sort(key=lambda x:x['sh'],reverse=True)
    print('\n'+'='*100)
    print('  RANKING')
    print('='*100)
    print('  %-3s %7s %9s %7s %7s %7s %5s %5s %5s %5s'%('Rk','Trail','Cfm','CD','S','Ret','DD','Ann','Trd','Win'))
    for i,r in enumerate(results[:20]):
        print('  %-3d %6.0f%% %7s %5s %7.3f %8.2f%% %6.2f%% %6.2f%% %5d %4.0f%%'%(
            i+1,r['trail']*100,'1d' if r['confirm'] else 'off',str(r['cooldown'])+'d',
            r['sh'],r['tr']*100,r['mdd']*100,r['ar']*100,r['np'],r['wr']*100))

    # Best
    best=results[0]
    print('\n\nBEST: Trail=%.0f%% Confirm=%dd Cooldown=%dd'%(best['trail']*100,best['confirm'],best['cooldown']))
    print('S=%.3f Ret=%.1f%% DD=%.1f%% Trd=%d Win=%.0f%%'%(best['sh'],best['tr']*100,best['mdd']*100,best['np'],best['wr']*100))

    # Compare to baseline
    base=[r for r in results if r['trail']==0.05 and r['confirm']==0 and r['cooldown']==0]
    if base:
        b=base[0]
        delta_s=best['sh']-b['sh'];delta_dd=best['mdd']-b['mdd']
        print('\nvs BASELINE (Trail=5%% no confirm no cooldown): S=%.3f DD=%.1f%%'%(b['sh'],b['mdd']*100))
        print('Delta: S%+.3f DD%+.1f%%'%(delta_s,delta_dd*100))

    # Sensitivity
    for pname,pkey in[('Trail','trail'),('Confirm','confirm'),('Cooldown','cooldown')]:
        lv=defaultdict(list)
        for r in results:lv[r[pkey]].append(r['sh'])
        print('\n%s:'%pname)
        for v in sorted(lv.keys()):
            a=sum(lv[v])/len(lv[v]);t=max(r['sh'] for r in results if r[pkey]==v)
            lbl='%.0f%%'%(v*100) if pkey=='trail' else(str(v)+'d')
            bar='#'*max(int(a*60),1) if a>0 else '·'
            print('  %-5s avg S=%.3f best=%.3f %s'%(lbl,a,t,bar))

    # Best per ETF
    print('\n\n  Contribution breakdown for best (%d trades):'%best['np'])
    st=best['trades']
    etf_pnl=defaultdict(float);etf_cnt=defaultdict(int);etf_wr=defaultdict(list)
    for t in st:
        if t['e'] in('trail','off','final'):
            etf_pnl[t['c']]+=t['pnl'];etf_cnt[t['c']]+=1;etf_wr[t['c']].append(t['r']>0)
    top10=sorted(etf_pnl.items(),key=lambda x:x[1],reverse=True)[:10]
    for c,pnl in top10:
        name=etfs[c]['name'];wr=sum(etf_wr[c])/len(etf_wr[c])*100
        print('  %s %s: PnL=%+.0f Trd=%d Win=%.0f%%'%(c,name,pnl,etf_cnt[c],wr))

    print('\nDone!')

if __name__=='__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
