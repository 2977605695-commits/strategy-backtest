"""
ETF 均线偏离策略 — 网格搜索最优参数
====================================
遍历买入/卖出阈值组合，评估稳健性
"""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import urllib.request
import json
import math
import itertools
import time
from collections import defaultdict

# ============================================================
# 配置
# ============================================================
ETF_CONFIG = {
    '科创50ETF南方':    {'code': 'sh588150'},
    '科创半导体ETF华夏':  {'code': 'sh588170'},
    '华宝中证银行ETF':    {'code': 'sh512800'},
    '华宝中证医疗ETF':    {'code': 'sh512170'},
    '华泰柏瑞沪深300ETF': {'code': 'sh510300'},
}

START_DATE = '2024-01-01'
END_DATE   = '2026-07-01'
RISK_FREE_RATE = 0.025
TRADING_DAYS   = 252

# ============================================================
# 1. 数据获取
# ============================================================
def fetch_tencent_kline(code: str, start: str, end: str) -> dict:
    url = (
        f'http://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
        f'?param={code},day,{start},{end},640,qfq'
    )
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://gu.qq.com/',
    }
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except Exception as e:
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
                    if days:
                        break
        if not days:
            return []
        result = []
        for d in days:
            if len(d) >= 6:
                result.append({
                    'date': str(d[0]), 'open': float(d[1]),
                    'close': float(d[2]), 'high': float(d[3]),
                    'low': float(d[4]), 'volume': float(d[5]),
                })
        return result
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


def generate_signals(bars: list[dict], buy_trigger: float, sell_trigger: float):
    """原地给bars添加信号"""
    closes = [b['close'] for b in bars]
    ma5 = calc_ma(closes, 5)
    for i, bar in enumerate(bars):
        bar['ma5'] = ma5[i]
        if math.isnan(ma5[i]) or ma5[i] == 0:
            bar['deviation'] = float('nan')
            bar['signal'] = 'hold'
        else:
            bar['deviation'] = (bar['close'] - ma5[i]) / abs(ma5[i])
            if bar['deviation'] < buy_trigger:
                bar['signal'] = 'buy'
            elif bar['deviation'] > sell_trigger:
                bar['signal'] = 'sell'
            else:
                bar['signal'] = 'hold'
    return bars


def backtest_single(bars: list[dict], init_capital: float = 1_000_000) -> dict:
    position = 0.0
    cash = init_capital
    trades = []
    daily_values = []

    for bar in bars:
        signal = bar['signal']
        price = bar['close']

        if signal == 'buy' and position == 0 and cash > 0:
            position = cash / price
            cash = 0.0
            trades.append({'date': bar['date'], 'action': 'buy',
                           'price': price, 'shares': position})

        elif signal == 'sell' and position > 0:
            cash = position * price
            trades.append({'date': bar['date'], 'action': 'sell',
                           'price': price, 'shares': position})
            position = 0.0

        daily_values.append({
            'date': bar['date'],
            'value': cash + position * price,
        })

    if position > 0:
        final_price = bars[-1]['close']
        cash = position * final_price
        trades.append({'date': bars[-1]['date'], 'action': 'sell_final',
                       'price': final_price, 'shares': position})
        position = 0.0

    final_value = cash

    # 日收益率
    returns = []
    for i in range(1, len(daily_values)):
        r = (daily_values[i]['value'] - daily_values[i-1]['value']) / daily_values[i-1]['value']
        returns.append(r)

    # 回撤
    peak = daily_values[0]['value']
    max_dd = 0.0
    for dv in daily_values:
        if dv['value'] > peak:
            peak = dv['value']
        dd = (peak - dv['value']) / peak
        if dd > max_dd:
            max_dd = dd

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

    n_trades = len([t for t in trades if t['action'] in ('buy', 'sell')])
    buy_trades  = [t for t in trades if t['action'] == 'buy']
    sell_trades = [t for t in trades if t['action'] in ('sell', 'sell_final')]
    win_rate = 0
    if buy_trades and sell_trades:
        pairs = min(len(buy_trades), len(sell_trades))
        wins = sum(1 for j in range(pairs)
                   if sell_trades[j]['price'] > buy_trades[j]['price'])
        win_rate = wins / pairs

    return {
        'init_capital': init_capital,
        'final_value': final_value,
        'total_return': total_return,
        'annual_return': annual_return,
        'volatility': annual_std,
        'sharpe': sharpe,
        'calmar': calmar,
        'max_drawdown': max_dd,
        'win_rate': win_rate,
        'n_trades': n_trades,
        'trades': trades,
        'daily_values': daily_values,
        'daily_returns': returns,
    }


def backtest_portfolio(etf_results: list[dict], weights: list[float] = None) -> dict:
    n = len(etf_results)
    if weights is None:
        weights = [1.0 / n] * n

    all_date_sets = [set(dv['date'] for dv in r['daily_values']) for r in etf_results]
    common_dates = all_date_sets[0]
    for ds in all_date_sets[1:]:
        common_dates = common_dates & ds
    common_dates = sorted(common_dates)

    if len(common_dates) < 2:
        return {'total_return': 0, 'annual_return': 0, 'volatility': 0,
                'sharpe': 0, 'calmar': 0, 'max_drawdown': 0}

    value_maps = [{dv['date']: dv['value'] / r['init_capital']
                   for dv in r['daily_values']} for r in etf_results]

    portfolio_values = []
    for d in common_dates:
        total = sum(w * vm[d] for w, vm in zip(weights, value_maps))
        portfolio_values.append({'date': d, 'value': total})

    init_value = portfolio_values[0]['value']
    final_value = portfolio_values[-1]['value']
    total_return = (final_value - init_value) / init_value

    returns = []
    for i in range(1, len(portfolio_values)):
        prev = portfolio_values[i-1]['value']
        curr = portfolio_values[i]['value']
        if prev > 0:
            returns.append((curr - prev) / prev)

    peak = init_value
    max_dd = 0.0
    for pv in portfolio_values:
        if pv['value'] > peak:
            peak = pv['value']
        dd = (peak - pv['value']) / peak
        if dd > max_dd:
            max_dd = dd

    n_days = len(returns)
    annual_return = (final_value / init_value) ** (TRADING_DAYS / max(n_days, 1)) - 1

    if n_days > 1:
        mean_ret = sum(returns) / n_days
        variance = sum((r - mean_ret) ** 2 for r in returns) / (n_days - 1)
        annual_std = variance ** 0.5 * math.sqrt(TRADING_DAYS)
        sharpe = ((mean_ret * TRADING_DAYS - RISK_FREE_RATE) / annual_std
                  if annual_std > 0 else 0)
    else:
        annual_std = sharpe = 0.0

    calmar = annual_return / max_dd if max_dd > 0 else float('inf')

    return {
        'total_return': total_return, 'annual_return': annual_return,
        'volatility': annual_std, 'sharpe': sharpe,
        'calmar': calmar, 'max_drawdown': max_dd,
    }


def evaluate_params(buy_trigger: float, sell_trigger: float, all_bars: dict,
                    etf_names: list) -> dict:
    """对一组 (buy_trigger, sell_trigger) 评估全部组合"""

    # ---- Step 1: 重新生成信号 & 单ETF回测 ----
    individual = {}
    for name in etf_names:
        bars = all_bars[name]
        # 重新打信号
        signal_bars = generate_signals(bars, buy_trigger, sell_trigger)
        individual[name] = backtest_single(signal_bars)

    # ---- Step 2: 枚举全部 31 种组合 ----
    best_sharpe_combo = None
    best_return_combo = None
    best_sharpe_val = -999
    best_return_val = -999

    all_combo_results = []

    for k in range(1, len(etf_names) + 1):
        for combo in itertools.combinations(etf_names, k):
            etf_res = [individual[n] for n in combo]
            port = backtest_portfolio(etf_res)
            all_combo_results.append({
                'etfs': combo, 'n': k, **port,
            })

            if port['sharpe'] > best_sharpe_val:
                best_sharpe_val = port['sharpe']
                best_sharpe_combo = {'etfs': combo, **port}

            if port['total_return'] > best_return_val:
                best_return_val = port['total_return']
                best_return_combo = {'etfs': combo, **port}

    # ---- Step 3: 稳健性指标 ----
    # 有多少只ETF真正交易了（至少1笔）
    n_active = sum(1 for r in individual.values() if r['n_trades'] > 0)

    # 所有交易ETF的平均夏普
    active_sharpes = [r['sharpe'] for r in individual.values() if r['n_trades'] > 0]
    avg_active_sharpe = sum(active_sharpes) / len(active_sharpes) if active_sharpes else 0

    # 所有单ETF收益的中位数
    all_returns = [r['total_return'] for r in individual.values()]
    median_return = sorted(all_returns)[len(all_returns) // 2]

    # 所有组合的夏普中位数
    all_combo_sharpes = [c['sharpe'] for c in all_combo_results]
    median_combo_sharpe = sorted(all_combo_sharpes)[len(all_combo_sharpes) // 2]

    # 综合分 = 最佳组合夏普 × 活跃ETF数 × 组合夏普中位数稳定性
    stability_score = (best_sharpe_val
                       * math.sqrt(n_active / len(etf_names))
                       * (1 + median_combo_sharpe) if median_combo_sharpe > 0
                       else best_sharpe_val * n_active / len(etf_names))

    return {
        'buy': buy_trigger,
        'sell': sell_trigger,
        'n_active': n_active,
        'best_sharpe': best_sharpe_val,
        'best_sharpe_etfs': ' + '.join(best_sharpe_combo['etfs']),
        'best_sharpe_return': best_sharpe_combo['total_return'],
        'best_sharpe_dd': best_sharpe_combo['max_drawdown'],
        'best_return': best_return_val,
        'best_return_etfs': ' + '.join(best_return_combo['etfs']),
        'best_return_sharpe': best_return_combo['sharpe'],
        'avg_active_sharpe': avg_active_sharpe,
        'median_return': median_return,
        'median_combo_sharpe': median_combo_sharpe,
        'stability_score': stability_score,
        'individual': individual,
        'all_combos': all_combo_results,
    }


# ============================================================
# 主程序
# ============================================================
def main():
    print('=' * 80)
    print('  ETF 均线偏离策略 — 网格搜索最优参数')
    print('  数据: 腾讯财经  |  2024-01-01 → 2026-07-01')
    print('=' * 80)

    # ---- Step 1: 获取数据（只获取一次）----
    print('\n📡 获取行情数据...')
    all_bars = {}
    for name, cfg in ETF_CONFIG.items():
        code = cfg['code']
        print(f'  ↓ {name} ({code}) ...', end=' ', flush=True)
        raw = fetch_tencent_kline(code, START_DATE, END_DATE)
        bars = parse_kline_data(raw, code)
        if bars:
            all_bars[name] = bars
            print(f'✅ {len(bars)} 条 ({bars[0]["date"]} ~ {bars[-1]["date"]})')
        else:
            print(f'❌')
        time.sleep(0.2)

    etf_names = list(all_bars.keys())
    print(f'\n  成功: {len(all_bars)}/{len(ETF_CONFIG)} 只 ETF')

    # ---- Step 2: 网格搜索 ----
    # 测试的阈值对: buy ∈ [-0.03, -0.08], sell ∈ [0.03, 0.08]
    buy_candidates  = [-0.03, -0.04, -0.045, -0.05, -0.055, -0.06, -0.07, -0.08]
    sell_candidates = [ 0.03,  0.04,  0.045,  0.05,  0.055,  0.06,  0.07,  0.08]

    print(f'\n🔍 网格搜索: {len(buy_candidates)}×{len(sell_candidates)} = '
          f'{len(buy_candidates)*len(sell_candidates)} 组参数...')

    results = []
    total = len(buy_candidates) * len(sell_candidates)
    count = 0
    for buy_val in buy_candidates:
        for sell_val in sell_candidates:
            count += 1
            print(f'  [{count}/{total}] buy={buy_val:.3f}  sell={sell_val:.3f} ...',
                  end=' ', flush=True)
            r = evaluate_params(buy_val, sell_val, all_bars, etf_names)
            results.append(r)
            print(f'夏普={r["best_sharpe"]:.3f}  '
                  f'收益={r["best_sharpe_return"]*100:.1f}%  '
                  f'回撤={r["best_sharpe_dd"]*100:.1f}%  '
                  f'活跃={r["n_active"]}/5')

    # ---- Step 3: 排名输出 ----
    print('\n' + '=' * 80)
    print('  🏆 综合排名 (按 stability_score 排序)')
    print('=' * 80)

    # 按综合稳健分排序
    sorted_by_stability = sorted(results, key=lambda x: x['stability_score'], reverse=True)

    print(f'\n  {"排名":<4s} {"买入":>6s} {"卖出":>6s} '
          f'{"活跃":>4s} {"最佳夏普":>8s} {"组合收益":>8s} '
          f'{"最大回撤":>8s} {"中位夏普":>8s} {"稳健分":>8s} '
          f'{"最佳组合":<35s}')
    print('  ' + '-' * 100)
    for rank, r in enumerate(sorted_by_stability[:25], 1):
        combo_str = r['best_sharpe_etfs']
        if len(combo_str) > 33:
            combo_str = combo_str[:30] + '...'
        print(f'  {rank:<4d} {r["buy"]:>+6.3f} {r["sell"]:>+6.3f} '
              f'{r["n_active"]:>4d} {r["best_sharpe"]:>8.3f} '
              f'{r["best_sharpe_return"]*100:>7.2f}% '
              f'{r["best_sharpe_dd"]*100:>7.2f}% '
              f'{r["median_combo_sharpe"]:>8.3f} '
              f'{r["stability_score"]:>8.4f} '
              f'{combo_str:<35s}')

    # ---- Step 4: 纯夏普排名 ----
    print(f'\n  🏆 纯夏普排名 (TOP 15)')
    sorted_by_sharpe = sorted(results, key=lambda x: x['best_sharpe'], reverse=True)
    print(f'\n  {"排名":<4s} {"买入":>6s} {"卖出":>6s} '
          f'{"最佳夏普":>8s} {"组合收益":>8s} {"最大回撤":>8s} '
          f'{"活跃":>4s} {"稳健分":>8s}')
    print('  ' + '-' * 60)
    for rank, r in enumerate(sorted_by_sharpe[:15], 1):
        print(f'  {rank:<4d} {r["buy"]:>+6.3f} {r["sell"]:>+6.3f} '
              f'{r["best_sharpe"]:>8.3f} '
              f'{r["best_sharpe_return"]*100:>7.2f}% '
              f'{r["best_sharpe_dd"]*100:>7.2f}% '
              f'{r["n_active"]:>4d} '
              f'{r["stability_score"]:>8.4f}')

    # ---- Step 5: 详细展示 TOP 3 参数组 ----
    print('\n' + '=' * 80)
    print('  📋 TOP 3 参数组详细分析')
    print('=' * 80)

    for rank, r in enumerate(sorted_by_stability[:3], 1):
        print(f'\n  ┌─────────────────────────────────────────────┐')
        print(f'  │ #{rank}  buy={r["buy"]:+0.3f}  sell={r["sell"]:+0.3f}  '
              f'稳健分={r["stability_score"]:.4f}  │')
        print(f'  └─────────────────────────────────────────────┘')

        # 单ETF
        ind = r['individual']
        print(f'\n    单ETF表现:')
        print(f'    {"ETF":<18s} {"总收益":>7s} {"夏普":>7s} '
              f'{"回撤":>7s} {"交易次":>6s} {"胜率":>6s}')
        print(f'    {"-"*55}')
        for name in etf_names:
            res = ind[name]
            print(f'    {name:<18s} {res["total_return"]*100:>6.2f}% '
                  f'{res["sharpe"]:>7.3f} {res["max_drawdown"]*100:>6.2f}% '
                  f'{res["n_trades"]:>6d} {res["win_rate"]*100:>5.0f}%')

        # 最佳组合
        print(f'\n    最佳等权组合: {r["best_sharpe_etfs"]}')
        print(f'    夏普={r["best_sharpe"]:.4f}  '
              f'总收益={r["best_sharpe_return"]*100:.2f}%  '
              f'回撤={r["best_sharpe_dd"]*100:.2f}%')

        # 所有组合的夏普分布
        all_sharpes = [c['sharpe'] for c in r['all_combos']]
        all_sharpes.sort(reverse=True)
        print(f'    全部31组合夏普: max={all_sharpes[0]:.3f}  '
              f'中位={all_sharpes[15]:.3f}  min={all_sharpes[-1]:.3f}')

    # ---- Step 6: 推荐 ----
    print('\n' + '=' * 80)
    print('  🎯 最终推荐')
    print('=' * 80)

    top1 = sorted_by_stability[0]
    top2 = sorted_by_stability[1]
    top3 = sorted_by_stability[2]

    print(f'''
  在 64 组参数中，综合考虑以下维度：
    - 最佳组合的夏普比率（越高越好）
    - 触发交易的 ETF 数量（越多越稳健，避免过度拟合单一 ETF）
    - 所有组合夏普的中位数（策略在跨 ETF 配置中的泛化能力）

  ┌────────────────────────────────────────────────────────────┐
  │  🥇 推荐: buy={top1["buy"]:+.3f}  sell={top1["sell"]:+.3f}                             │
  │     组合: {top1["best_sharpe_etfs"]:<45s} │
  │     夏普: {top1["best_sharpe"]:.4f}   总收益: {top1["best_sharpe_return"]*100:.2f}%  回撤: {top1["best_sharpe_dd"]*100:.2f}% │
  ├────────────────────────────────────────────────────────────┤
  │  🥈 备选: buy={top2["buy"]:+.3f}  sell={top2["sell"]:+.3f}                             │
  │     组合: {top2["best_sharpe_etfs"]:<45s} │
  │     夏普: {top2["best_sharpe"]:.4f}   总收益: {top2["best_sharpe_return"]*100:.2f}%  回撤: {top2["best_sharpe_dd"]*100:.2f}% │
  ├────────────────────────────────────────────────────────────┤
  │  🥉 备选: buy={top3["buy"]:+.3f}  sell={top3["sell"]:+.3f}                             │
  │     组合: {top3["best_sharpe_etfs"]:<45s} │
  │     夏普: {top3["best_sharpe"]:.4f}   总收益: {top3["best_sharpe_return"]*100:.2f}%  回撤: {top3["best_sharpe_dd"]*100:.2f}% │
  └────────────────────────────────────────────────────────────┘

  ⚠ 注意事项:
  - 科创半导体ETF华夏 (588170) 2025年4月才上市，数据量较小
  - 华宝中证银行ETF波动极小，几乎所有阈值下都无法触发买入
  - 推荐优先选择活跃ETF数≥3的参数组，说明策略有跨品种泛化能力
  - 最终选择需要在「高夏普」和「多ETF分散」之间权衡
''')

    print('\n  计算完成 ✅')


if __name__ == '__main__':
    main()
