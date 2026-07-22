"""
ETF 优先排序轮动策略 — 网格搜索最优参数
==========================================
策略:
  - 优先级排名: 科创半导体 > 科创50 > 沪深300 > 医疗 > 银行
  - 同时只持1只ETF
  - 持仓触发卖出 → 当天立即判断是否有其他ETF满足买入条件
  - 按优先级买入第一个满足条件的
  - 无买入信号 → 空仓等待
"""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import urllib.request
import json
import math
import time
from collections import defaultdict

# ============================================================
# 配置
# ============================================================
ETF_CONFIG = {
    '科创半导体ETF华夏':  {'code': 'sh588170', 'priority': 1},
    '科创50ETF南方':    {'code': 'sh588150', 'priority': 2},
    '华泰柏瑞沪深300ETF': {'code': 'sh510300', 'priority': 3},
    '华宝中证医疗ETF':    {'code': 'sh512170', 'priority': 4},
    '华宝中证银行ETF':    {'code': 'sh512800', 'priority': 5},
}

START_DATE = '2024-01-01'
END_DATE   = '2026-07-01'
RISK_FREE_RATE = 0.025
TRADING_DAYS   = 252

# ============================================================
# 数据获取
# ============================================================
def fetch_tencent_kline(code: str, start: str, end: str) -> dict:
    url = (f'http://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
           f'?param={code},day,{start},{end},640,qfq')
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                      'AppleWebKit/537.36',
        'Referer': 'https://gu.qq.com/',
    }
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except:
            time.sleep(1)
    return {}

def parse_kline_data(raw: dict, code: str) -> list[dict]:
    try:
        days = None
        if 'data' in raw:
            for k in raw['data']:
                if isinstance(raw['data'][k], dict):
                    for field in ['qfqday', 'day']:
                        if field in raw['data'][k]:
                            days = raw['data'][k][field]
                            break
                    if days: break
        if not days: return []
        return [{'date': str(d[0]), 'close': float(d[2])}
                for d in days if len(d) >= 6]
    except:
        return []

def calc_ma(data: list[float], window: int = 5) -> list[float]:
    ma = []
    for i in range(len(data)):
        if i < window - 1:
            ma.append(float('nan'))
        else:
            ma.append(sum(data[i - window + 1:i + 1]) / window)
    return ma

def generate_signals(bars: list[dict], buy_t: float, sell_t: float):
    closes = [b['close'] for b in bars]
    ma5 = calc_ma(closes, 5)
    for i, bar in enumerate(bars):
        bar['ma5'] = ma5[i]
        if math.isnan(ma5[i]) or ma5[i] == 0:
            bar['deviation'] = float('nan')
            bar['signal'] = 'hold'
        else:
            bar['deviation'] = (bar['close'] - ma5[i]) / abs(ma5[i])
            if bar['deviation'] < buy_t:
                bar['signal'] = 'buy'
            elif bar['deviation'] > sell_t:
                bar['signal'] = 'sell'
            else:
                bar['signal'] = 'hold'
    return bars


# ============================================================
# 优先排序轮动策略回测
# ============================================================
def backtest_priority_rotation(all_signals: dict, init_capital: float = 1_000_000
                               ) -> dict:
    """
    核心策略:
    - 获取所有ETF的对齐日期
    - 每天:
        1. 若持仓: 检查当前持仓ETF是否触发卖出 → 卖, 变现金
        2. 若空仓: 按优先级遍历, 买第一个有买入信号的
        3. 无信号 → 继续空仓
    - 最后一天若持仓 → 平仓
    """
    # 对齐日期
    date_maps = {}
    for name, bars in all_signals.items():
        date_maps[name] = {b['date']: b for b in bars}

    all_date_sets = [set(m.keys()) for m in date_maps.values()]
    common_dates = sorted(all_date_sets[0].intersection(*all_date_sets[1:]))

    if len(common_dates) < 2:
        return {'total_return': 0.0, 'annual_return': 0.0, 'volatility': 0.0,
                'sharpe': 0.0, 'calmar': 0.0, 'max_drawdown': 0.0,
                'n_trades': 0, 'win_rate': 0.0,
                'etf_stats': {}, 'trades': [], 'daily_values': []}

    # 按优先级排序
    etfs_by_priority = sorted(all_signals.keys(),
                              key=lambda n: ETF_CONFIG[n]['priority'])

    cash = init_capital
    position = 0.0          # 持仓份额
    holding = None           # 当前持仓的ETF名称
    buy_price = 0.0
    trades = []
    daily_values = []
    etf_trades = defaultdict(list)    # 每只ETF的交易
    etf_pnl = defaultdict(float)      # 每只ETF的累计盈亏

    for d in common_dates:
        # Step 1: 检查是否需要卖出
        if holding is not None:
            bar = date_maps[holding].get(d)
            if bar and bar['signal'] == 'sell':
                price = bar['close']
                cash = position * price
                pnl = cash - (position * buy_price)
                trades.append({
                    'date': d, 'action': 'sell', 'etf': holding,
                    'price': price, 'shares': position,
                    'value': cash, 'pnl': pnl,
                })
                etf_trades[holding].append({
                    'action': 'sell', 'date': d, 'price': price,
                    'shares': position, 'pnl': pnl,
                })
                etf_pnl[holding] += pnl
                position = 0.0
                holding = None
                buy_price = 0.0

        # Step 2: 若空仓, 按优先级找买入机会
        if holding is None:
            bought = False
            for name in etfs_by_priority:
                bar = date_maps[name].get(d)
                if bar and bar['signal'] == 'buy':
                    price = bar['close']
                    position = cash / price
                    buy_price = price
                    holding = name
                    trades.append({
                        'date': d, 'action': 'buy', 'etf': name,
                        'price': price, 'shares': position,
                        'value': cash,
                    })
                    etf_trades[name].append({
                        'action': 'buy', 'date': d, 'price': price,
                        'shares': position,
                    })
                    cash = 0.0
                    bought = True
                    break
            # 没买到 → 空仓待机

        # 当日总资产
        if holding is not None:
            bar = date_maps[holding].get(d)
            price = bar['close'] if bar else 0
            total = position * price
        else:
            total = cash

        daily_values.append({
            'date': d,
            'value': total,
            'holding': holding,
        })

    # 最后若持仓, 平仓
    if holding is not None:
        last_date = common_dates[-1]
        bar = date_maps[holding].get(last_date)
        price = bar['close'] if bar else 0
        cash = position * price
        pnl = cash - (position * buy_price)
        trades.append({
            'date': last_date, 'action': 'sell_final', 'etf': holding,
            'price': price, 'shares': position,
            'value': cash, 'pnl': pnl,
        })
        etf_pnl[holding] += pnl
        daily_values[-1]['value'] = cash
        daily_values[-1]['holding'] = None

    final_value = cash

    # ---- 收益计算 ----
    returns = []
    for i in range(1, len(daily_values)):
        prev = daily_values[i-1]['value']
        curr = daily_values[i]['value']
        if prev > 0:
            returns.append((curr - prev) / prev)
        else:
            returns.append(0.0)

    # 回撤
    peak = daily_values[0]['value']
    max_dd = 0.0
    dd_start = dd_end = ''
    for dv in daily_values:
        if dv['value'] > peak:
            peak = dv['value']
        dd = (peak - dv['value']) / peak
        if dd > max_dd:
            max_dd = dd

    # 绩效指标
    total_return = (final_value - init_capital) / init_capital

    if len(returns) > 1:
        mean_ret = sum(returns) / len(returns)
        std_ret = (sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)) ** 0.5
        annual_std = std_ret * math.sqrt(TRADING_DAYS)
        annual_ret = mean_ret * TRADING_DAYS
        sharpe = (annual_ret - RISK_FREE_RATE) / annual_std if annual_std > 0 else 0
    else:
        annual_std = mean_ret = sharpe = 0.0
        annual_ret = 0.0

    annual_return = (1 + total_return) ** (TRADING_DAYS / max(len(returns), 1)) - 1
    calmar = annual_return / max_dd if max_dd > 0 else float('inf')

    # 交易统计
    n_buys = len([t for t in trades if t['action'] == 'buy'])
    n_sells = len([t for t in trades if t['action'] in ('sell', 'sell_final')])
    n_trades = n_buys + n_sells

    # 胜率 (每笔完整买卖)
    buy_sell_pairs = []
    current_buy = None
    for t in trades:
        if t['action'] == 'buy':
            current_buy = t
        elif t['action'] in ('sell', 'sell_final') and current_buy:
            buy_sell_pairs.append({
                'etf': t['etf'],
                'buy_date': current_buy['date'],
                'sell_date': t['date'],
                'buy_price': current_buy['price'],
                'sell_price': t['price'],
                'return': (t['price'] - current_buy['price']) / current_buy['price'],
                'pnl': t.get('pnl', 0),
            })
            current_buy = None

    win_count = sum(1 for p in buy_sell_pairs if p['return'] > 0)
    win_rate = win_count / len(buy_sell_pairs) if buy_sell_pairs else 0

    # 每只ETF的统计
    etf_stats = {}
    for name in etfs_by_priority:
        n_b = len([t for t in etf_trades[name] if t['action'] == 'buy'])
        n_s = len([t for t in etf_trades[name] if t['action'] == 'sell'])
        etf_pairs = [p for p in buy_sell_pairs if p['etf'] == name]
        etf_wins = sum(1 for p in etf_pairs if p['return'] > 0)
        etf_stats[name] = {
            'n_buys': n_b,
            'n_sells': n_s,
            'total_pnl': etf_pnl[name],
            'pnl_pct': etf_pnl[name] / init_capital,
            'win_rate': etf_wins / len(etf_pairs) if etf_pairs else 0,
            'n_pairs': len(etf_pairs),
        }

    # 空仓天数
    empty_days = sum(1 for dv in daily_values if dv['holding'] is None)
    empty_pct = empty_days / len(daily_values) if daily_values else 0

    return {
        'total_return': total_return,
        'annual_return': annual_return,
        'volatility': annual_std,
        'sharpe': sharpe,
        'calmar': calmar,
        'max_drawdown': max_dd,
        'n_trades': n_trades,
        'n_pairs': len(buy_sell_pairs),
        'win_rate': win_rate,
        'empty_days': empty_days,
        'empty_pct': empty_pct,
        'etf_stats': etf_stats,
        'trades': trades,
        'buy_sell_pairs': buy_sell_pairs,
        'daily_values': daily_values,
        'daily_returns': returns,
    }


# ============================================================
# 主程序
# ============================================================
def main():
    print('=' * 80)
    print('  ETF 优先排序轮动策略 — 网格搜索最优参数')
    print('  优先级: 科创半导体 > 科创50 > 沪深300 > 医疗 > 银行')
    print('  规则: 一次持1只, 卖出当天判断下一只买入, 按优先级选')
    print('=' * 80)

    # ---- 获取数据 ----
    print('\n📡 获取行情数据...')
    all_bars = {}
    for name, cfg in ETF_CONFIG.items():
        code = cfg['code']
        print(f'  ↓ {name} ({code}) ...', end=' ', flush=True)
        raw = fetch_tencent_kline(code, START_DATE, END_DATE)
        bars = parse_kline_data(raw, code)
        if bars:
            all_bars[name] = bars
            print(f'✅ {len(bars)} 条')
        else:
            print(f'❌')
        time.sleep(0.2)

    etf_priority_order = sorted(all_bars.keys(),
                                key=lambda n: ETF_CONFIG[n]['priority'])

    # ---- 网格搜索 ----
    buy_candidates  = [-0.03, -0.035, -0.04, -0.045, -0.05, -0.055, -0.06, -0.07, -0.08]
    sell_candidates = [0.03,  0.035,  0.04,  0.045,  0.05,  0.055,  0.06,  0.07,  0.08]

    print(f'\n🔍 网格搜索: {len(buy_candidates)}×{len(sell_candidates)} = '
          f'{len(buy_candidates)*len(sell_candidates)} 组参数...')

    results = []
    total = len(buy_candidates) * len(sell_candidates)
    count = 0

    for buy_val in buy_candidates:
        for sell_val in sell_candidates:
            count += 1

            # 重新生成信号
            signals = {}
            for name in etf_priority_order:
                import copy
                bars_copy = [dict(b) for b in all_bars[name]]
                signals[name] = generate_signals(bars_copy, buy_val, sell_val)

            r = backtest_priority_rotation(signals)
            r['buy'] = buy_val
            r['sell'] = sell_val
            results.append(r)

            print(f'  [{count:>2d}/{total}] buy={buy_val:+.3f} sell={sell_val:+.3f}  '
                  f'夏普={r["sharpe"]:>7.3f}  收益={r["total_return"]*100:>7.2f}%  '
                  f'回撤={r["max_drawdown"]*100:>5.2f}%  交易={r["n_pairs"]:>2d}笔  '
                  f'空仓={r["empty_pct"]*100:>4.0f}%  胜率={r["win_rate"]*100:>4.0f}%')

    # ---- 综合排名 ----
    # 稳健分 = 夏普 * sqrt(交易笔数) * (1 - 回撤惩罚)
    for r in results:
        dd_penalty = max(0, 1 - r['max_drawdown'] / 0.35)  # 回撤>35%不奖励
        trade_bonus = min(math.sqrt(max(r['n_pairs'], 1)), 3)  # 交易至少1笔
        r['stability'] = r['sharpe'] * trade_bonus * (0.5 + 0.5 * dd_penalty)

    # ---- 综合排名输出 ----
    sorted_stable = sorted(results, key=lambda x: x['stability'], reverse=True)

    print('\n' + '=' * 80)
    print('  🏆 综合排名 (夏普 × 交易频率 × 回撤惩罚)')
    print('=' * 80)

    print(f'\n  {"排名":<4s} {"买入":>7s} {"卖出":>7s} '
          f'{"夏普":>7s} {"总收益":>8s} {"年化":>7s} '
          f'{"回撤":>7s} {"卡玛":>6s} {"交易":>4s} '
          f'{"胜率":>6s} {"空仓":>5s} {"稳健分":>8s}')
    print('  ' + '-' * 90)

    for rank, r in enumerate(sorted_stable[:20], 1):
        print(f'  {rank:<4d} {r["buy"]:>+7.3f} {r["sell"]:>+7.3f} '
              f'{r["sharpe"]:>7.3f} {r["total_return"]*100:>7.2f}% '
              f'{r["annual_return"]*100:>6.2f}% '
              f'{r["max_drawdown"]*100:>6.2f}% {r["calmar"]:>6.3f} '
              f'{r["n_pairs"]:>4d} {r["win_rate"]*100:>5.0f}% '
              f'{r["empty_pct"]*100:>4.0f}% '
              f'{r["stability"]:>8.4f}')

    # ---- 纯夏普排名 ----
    sorted_sharpe = sorted(results, key=lambda x: x['sharpe'], reverse=True)
    print(f'\n  🏆 纯夏普排名 (TOP 15)')
    print(f'  {"排名":<4s} {"买入":>7s} {"卖出":>7s} '
          f'{"夏普":>7s} {"总收益":>8s} {"回撤":>7s} '
          f'{"交易":>4s} {"空仓":>5s} {"胜率":>6s}')
    print('  ' + '-' * 60)
    for rank, r in enumerate(sorted_sharpe[:15], 1):
        print(f'  {rank:<4d} {r["buy"]:>+7.3f} {r["sell"]:>+7.3f} '
              f'{r["sharpe"]:>7.3f} {r["total_return"]*100:>7.2f}% '
              f'{r["max_drawdown"]*100:>6.2f}% '
              f'{r["n_pairs"]:>4d} {r["empty_pct"]*100:>4.0f}% '
              f'{r["win_rate"]*100:>5.0f}%')

    # ---- 纯收益排名 ----
    sorted_return = sorted(results, key=lambda x: x['total_return'], reverse=True)
    print(f'\n  🏆 纯收益排名 (TOP 15)')
    print(f'  {"排名":<4s} {"买入":>7s} {"卖出":>7s} '
          f'{"总收益":>8s} {"夏普":>7s} {"回撤":>7s} '
          f'{"交易":>4s} {"空仓":>5s} {"胜率":>6s}')
    print('  ' + '-' * 60)
    for rank, r in enumerate(sorted_return[:15], 1):
        print(f'  {rank:<4d} {r["buy"]:>+7.3f} {r["sell"]:>+7.3f} '
              f'{r["total_return"]*100:>7.2f}% '
              f'{r["sharpe"]:>7.3f} {r["max_drawdown"]*100:>6.2f}% '
              f'{r["n_pairs"]:>4d} {r["empty_pct"]*100:>4.0f}% '
              f'{r["win_rate"]*100:>5.0f}%')

    # ---- TOP 3 详细分析 ----
    print('\n' + '=' * 80)
    print('  📋 TOP 3 详细分析')
    print('=' * 80)

    for rank, r in enumerate(sorted_stable[:3], 1):
        print(f'\n  ┌──────────────────────────────────────────────────┐')
        print(f'  │ #{rank}  buy={r["buy"]:+.3f}  sell={r["sell"]:+.3f}  '
              f'夏普={r["sharpe"]:.4f}  稳健分={r["stability"]:.4f}  │')
        print(f'  └──────────────────────────────────────────────────┘')

        print(f'\n    总收益: {r["total_return"]*100:.2f}%  |  '
              f'年化: {r["annual_return"]*100:.2f}%  |  '
              f'回撤: {r["max_drawdown"]*100:.2f}%  |  '
              f'卡玛: {r["calmar"]:.3f}')
        print(f'    交易: {r["n_pairs"]} 笔完整买卖  |  '
              f'胜率: {r["win_rate"]*100:.1f}%  |  '
              f'空仓: {r["empty_pct"]*100:.1f}% ({r["empty_days"]}天)')

        # 每只ETF的交易统计
        print(f'\n    各ETF贡献:')
        print(f'    {"ETF":<18s} {"买入次":>6s} {"卖出次":>6s} '
              f'{"盈亏%":>8s} {"胜率":>6s} {"交易对":>6s}')
        print(f'    {"-"*54}')
        for name in etf_priority_order:
            s = r['etf_stats'][name]
            print(f'    {name:<18s} {s["n_buys"]:>6d} {s["n_sells"]:>6d} '
                  f'{s["pnl_pct"]*100:>7.2f}% {s["win_rate"]*100:>5.0f}% '
                  f'{s["n_pairs"]:>6d}')

        # 交易时间线
        print(f'\n    完整交易时间线:')
        print(f'    {"买入日":<12s} {"卖出日":<12s} {"ETF":<18s} '
              f'{"买入价":>8s} {"卖出价":>8s} {"收益率":>8s}')
        print(f'    {"-"*72}')
        for pair in r['buy_sell_pairs']:
            print(f'    {pair["buy_date"]:<12s} {pair["sell_date"]:<12s} '
                  f'{pair["etf"]:<18s} '
                  f'{pair["buy_price"]:>8.4f} {pair["sell_price"]:>8.4f} '
                  f'{pair["return"]*100:>7.2f}%')

    # ---- 最终推荐 ----
    print('\n' + '=' * 80)
    print('  🎯 最终推荐')
    print('=' * 80)

    top1 = sorted_stable[0]
    top2 = sorted_stable[1]
    top3 = sorted_stable[2]

    # 计算 vs 等权策略的对比
    # 用共同的一组最优参数对比
    best_sharpe = sorted_sharpe[0]
    best_return = sorted_return[0]

    print(f'''
  策略对比 (优先轮动 vs 之前等权组合):

  ┌──────────────────────────────────────────────────────────────┐
  │  🥇 综合最优: buy={top1["buy"]:+.3f}  sell={top1["sell"]:+.3f}                               │
  │     夏普: {top1["sharpe"]:.4f}   总收益: {top1["total_return"]*100:.2f}%    回撤: {top1["max_drawdown"]*100:.2f}% │
  │     交易: {top1["n_pairs"]} 笔   胜率: {top1["win_rate"]*100:.0f}%    空仓: {top1["empty_pct"]*100:.0f}% │
  ├──────────────────────────────────────────────────────────────┤
  │  🥈 备选: buy={top2["buy"]:+.3f}  sell={top2["sell"]:+.3f}                               │
  │     夏普: {top2["sharpe"]:.4f}   总收益: {top2["total_return"]*100:.2f}%    回撤: {top2["max_drawdown"]*100:.2f}% │
  │     交易: {top2["n_pairs"]} 笔   胜率: {top2["win_rate"]*100:.0f}%    空仓: {top2["empty_pct"]*100:.0f}% │
  ├──────────────────────────────────────────────────────────────┤
  │  🥉 备选: buy={top3["buy"]:+.3f}  sell={top3["sell"]:+.3f}                               │
  │     夏普: {top3["sharpe"]:.4f}   总收益: {top3["total_return"]*100:.2f}%    回撤: {top3["max_drawdown"]*100:.2f}% │
  │     交易: {top3["n_pairs"]} 笔   胜率: {top3["win_rate"]*100:.0f}%    空仓: {top3["empty_pct"]*100:.0f}% │
  └──────────────────────────────────────────────────────────────┘

  ┌──────────────────────────────────────────────────────────────┐
  │  📊 最高夏普 (纯): buy={best_sharpe["buy"]:+.3f}  sell={best_sharpe["sell"]:+.3f}                     │
  │     夏普: {best_sharpe["sharpe"]:.4f}   总收益: {best_sharpe["total_return"]*100:.2f}%    回撤: {best_sharpe["max_drawdown"]*100:.2f}% │
  │  💰 最高收益 (纯): buy={best_return["buy"]:+.3f}  sell={best_return["sell"]:+.3f}                     │
  │     夏普: {best_return["sharpe"]:.4f}   总收益: {best_return["total_return"]*100:.2f}%    回撤: {best_return["max_drawdown"]*100:.2f}% │
  └──────────────────────────────────────────────────────────────┘

  💡 优先轮动策略 vs 等权策略的关键差异:
  - 轮动: 资金集中在最强信号, 空仓规避下跌
  - 等权: 分散风险, 但稀释收益
  - 轮动适合: 各ETF走势分化明显的市场
  - 等权适合: 相关性高、同涨同跌的市场
''')

    print('\n  计算完成 ✅')


if __name__ == '__main__':
    main()
