"""
ETF 执行策略模块 -- 分档拆单 + POV/VWAP
=====================================
按资金量自动选择执行方式:
  < 50万:   直接市价单 2:50 一把清
  50-200万:  分 2-3 笔尾盘分批
  200-1000万: POV 拆单(占每分钟成交量 10-15%)
  > 1000万:  全日 VWAP + POV 限速

用法:
  from etf_executor import ExecutionPlan, suggest_execution, plan_rebalance
  
  plan = suggest_execution('515880', '通信ETF', 'buy', 800000, 44330000)
  # plan_rebalance(sell_list, buy_list, total_capital)  # 换仓场景
"""

import math
from dataclasses import dataclass, field
from typing import List, Tuple


# -- 分档阈值 --
TIER_DIRECT = 500_000        # 50万以下直接买
TIER_BATCH  = 2_000_000      # 200万以下分批
TIER_POV    = 10_000_000     # 1000万以下POV拆单

POV_RATE_TAIL  = 0.15        # 尾盘分批: 每笔不超过当前1分钟成交量的15%
POV_RATE_FULL = 0.10         # 全日VWAP: 每笔不超过10%
MIN_ORDER_VALUE = 10_000     # 最小拆单金额 1万


@dataclass
class OrderSlice:
    time_window: str
    amount: float
    pct_of_daily: float
    method: str
    limit_price: float = 0.0


@dataclass
class ExecutionPlan:
    etf_code: str
    etf_name: str
    side: str
    order_amount: float
    avg_daily_amount: float
    tier: str = ""
    slices: List[OrderSlice] = field(default_factory=list)
    estimated_cost: float = 0.0

    def __post_init__(self):
        self._build_plan()

    def _build_plan(self):
        ratio = self.order_amount / self.avg_daily_amount if self.avg_daily_amount > 0 else 999
        if self.order_amount < TIER_DIRECT:
            self.tier = "direct"
            self._build_direct()
            self.estimated_cost = 2
        elif self.order_amount < TIER_BATCH:
            self.tier = "batch"
            self._build_batch()
            self.estimated_cost = 5 + ratio * 10
        elif self.order_amount < TIER_POV:
            self.tier = "pov"
            self._build_pov()
            self.estimated_cost = 10 + ratio * 20
        else:
            self.tier = "vwap"
            self._build_vwap()
            self.estimated_cost = 20 + ratio * 30

    def _build_direct(self):
        self.slices.append(OrderSlice(
            time_window="14:50-14:55",
            amount=self.order_amount,
            pct_of_daily=self.order_amount / self.avg_daily_amount * 100,
            method="market"
        ))

    def _build_batch(self):
        n = 2 if self.order_amount < 1_000_000 else 3
        slice_amount = self.order_amount / n
        if n == 2:
            times = [("14:35", "14:42"), ("14:45", "14:52")]
        else:
            times = [("14:30", "14:37"), ("14:40", "14:47"), ("14:50", "14:57")]
        for start, end in times:
            self.slices.append(OrderSlice(
                time_window=f"{start}-{end}",
                amount=slice_amount,
                pct_of_daily=slice_amount / self.avg_daily_amount * 100,
                method="limit",
            ))

    def _build_pov(self):
        tail_volume_est = self.avg_daily_amount * 0.30
        per_min_volume = tail_volume_est / 27
        max_per_slice = max(per_min_volume * POV_RATE_TAIL, MIN_ORDER_VALUE)
        remaining = self.order_amount
        current_time = 14 * 60 + 30
        end_time = 14 * 60 + 57
        while remaining > 0 and current_time < end_time:
            slice_amt = min(remaining, max_per_slice)
            if remaining - slice_amt < MIN_ORDER_VALUE:
                slice_amt = remaining
            h1, m1 = divmod(current_time, 60)
            h2, m2 = divmod(min(current_time + 1, end_time), 60)
            self.slices.append(OrderSlice(
                time_window=f"{h1:02d}:{m1:02d}-{h2:02d}:{m2:02d}",
                amount=slice_amt,
                pct_of_daily=slice_amt / self.avg_daily_amount * 100,
                method="pov"
            ))
            remaining -= slice_amt
            current_time += 1
        if remaining > 0:
            self.slices.append(OrderSlice(
                time_window="14:57-14:58",
                amount=remaining,
                pct_of_daily=remaining / self.avg_daily_amount * 100,
                method="market"
            ))

    def _build_vwap(self):
        morning_budget = self.order_amount * 0.35
        afternoon_budget = self.order_amount * 0.65
        self._build_session_vwap(9*60+45, 11*60+30, self.avg_daily_amount*0.40, morning_budget, 5, POV_RATE_FULL)
        self._build_session_vwap(13*60, 14*60+57, self.avg_daily_amount*0.60, afternoon_budget, 3, POV_RATE_FULL)

    def _build_session_vwap(self, start_min, end_min, session_volume, budget, interval, pov_rate):
        total_min = end_min - start_min
        per_interval_volume = session_volume / (total_min / interval)
        max_per_slice = max(per_interval_volume * pov_rate, MIN_ORDER_VALUE)
        remaining = budget
        current = start_min
        while remaining > 0 and current < end_min:
            slice_amt = min(remaining, max_per_slice)
            if remaining - slice_amt < MIN_ORDER_VALUE:
                slice_amt = remaining
            h1, m1 = divmod(current, 60)
            h2, m2 = divmod(min(current + interval, end_min), 60)
            self.slices.append(OrderSlice(
                time_window=f"{h1:02d}:{m1:02d}-{h2:02d}:{m2:02d}",
                amount=slice_amt,
                pct_of_daily=slice_amt / self.avg_daily_amount * 100,
                method="pov"
            ))
            remaining -= slice_amt
            current += interval
        if remaining > 0:
            if self.slices:
                self.slices[-1].amount += remaining
            else:
                h, m = divmod(end_min - 1, 60)
                self.slices.append(OrderSlice(
                    time_window=f"{h:02d}:{m:02d}-{h:02d}:{m+1:02d}",
                    amount=remaining,
                    pct_of_daily=remaining / self.avg_daily_amount * 100,
                    method="market"
                ))

    def describe(self):
        print(f"\n{'='*70}")
        print(f"  执行计划: {self.etf_code} {self.etf_name}")
        print(f"  方向: {'买入' if self.side=='buy' else '卖出'}  金额: {self.order_amount:,.0f}")
        print(f"  日均成交额: {self.avg_daily_amount:,.0f}  占比: {self.order_amount/self.avg_daily_amount*100:.2f}%")
        print(f"  执行档位: {self.tier.upper()}  估计冲击成本: ~{self.estimated_cost:.0f}bps")
        print(f"  拆单数: {len(self.slices)} 笔")
        print(f"{'='*70}")
        print(f"  {'时间段':<16} {'金额':>12} {'占日均%':>8} {'方式':>8}")
        print(f"  {'-'*50}")
        for s in self.slices:
            print(f"  {s.time_window:<16} {s.amount:>12,.0f} {s.pct_of_daily:>7.2f}% {s.method:>8}")
        print(f"  {'-'*50}")
        print(f"  合计: {sum(s.amount for s in self.slices):,.0f}")
        print()


def classify_liquidity(avg_daily_amount):
    if avg_daily_amount >= 50_000_000: return 'high'
    elif avg_daily_amount >= 15_000_000: return 'medium'
    elif avg_daily_amount >= 5_000_000: return 'low'
    else: return 'micro'


def get_safe_participation_rate(avg_daily_amount, order_amount):
    liq = classify_liquidity(avg_daily_amount)
    order_ratio = order_amount / avg_daily_amount if avg_daily_amount > 0 else 999
    rates = {'high': 0.20, 'medium': 0.15, 'low': 0.10, 'micro': 0.05}
    base_rate = rates.get(liq, 0.05)
    if order_ratio > 0.30: base_rate *= 0.5
    elif order_ratio > 0.10: base_rate *= 0.7
    return base_rate


def suggest_execution(etf_code, etf_name, side, order_amount, avg_daily_amount):
    plan = ExecutionPlan(etf_code=etf_code, etf_name=etf_name, side=side,
                         order_amount=order_amount, avg_daily_amount=avg_daily_amount)
    plan.describe()
    liq = classify_liquidity(avg_daily_amount)
    safe_rate = get_safe_participation_rate(avg_daily_amount, order_amount)
    print(f"  流动性等级: {liq}")
    print(f"  建议最大参与率: {safe_rate*100:.0f}%/分钟")
    if liq == 'micro' and order_amount > 200_000:
        print(f"  !! 警告: 该ETF流动性极低，{order_amount:,.0f}元可能造成显著冲击")
        print(f"     建议: 换用同类别高流动性ETF，或分多日执行")
    ratio = order_amount / avg_daily_amount * 100 if avg_daily_amount > 0 else 999
    if ratio > 50:
        print(f"  !! 警告: 订单占日均成交额 {ratio:.1f}%，建议分多日执行")
    print()
    return plan


def plan_rebalance(positions_to_sell, positions_to_buy, total_capital):
    print(f"\n{'#'*70}")
    print(f"  V10 换仓执行计划")
    print(f"  总资金: {total_capital:,.0f}  换仓日期: T 日")
    print(f"{'#'*70}")
    print(f"\n{'='*70}")
    print(f"  STEP 1: 卖出 ({len(positions_to_sell)} 个)")
    print(f"{'='*70}")
    for code, name, amount, avg_daily in positions_to_sell:
        suggest_execution(code, name, 'sell', amount, avg_daily)
    print(f"\n{'='*70}")
    print(f"  STEP 2: 买入 ({len(positions_to_buy)} 个)")
    print(f"{'='*70}")
    for code, name, amount, avg_daily in positions_to_buy:
        suggest_execution(code, name, 'buy', amount, avg_daily)
    tier = "direct" if total_capital < TIER_DIRECT else \
           "batch" if total_capital < TIER_BATCH else \
           "pov" if total_capital < TIER_POV else "vwap"
    print(f"\n{'='*70}")
    print(f"  总资金 {total_capital:,.0f} -> 执行档位: {tier.upper()}")
    if tier == "direct": print(f"  -> 全部 2:50 尾盘直接市价单，不需要拆单")
    elif tier == "batch": print(f"  -> 尾盘分 2-3 笔，14:30-14:57 完成")
    elif tier == "pov": print(f"  -> POV 拆单，14:30-14:57 按每分钟成交量 10-15% 参与")
    else: print(f"  -> 全日 VWAP + POV，9:45-14:57 分摊执行")
    print(f"{'='*70}\n")


if __name__ == '__main__':
    # V10 池流动性(万元)
    ETF_LIQ = {
        '588000': ('华夏科创50ETF', 9647),
        '159915': ('创业板ETF', 7751),
        '510300': ('沪深300ETF', 7588),
        '588170': ('科创半导体ETF', 4647),
        '515880': ('通信ETF', 4433),
        '588200': ('科创芯片ETF', 3156),
        '511260': ('十年国债ETF', 3153),
        '518880': ('华安黄金ETF', 2815),
        '510050': ('上证50ETF', 2577),
        '515050': ('5G通信ETF', 1980),
        '512480': ('半导体ETF', 1772),
        '159937': ('博时黄金ETF', 597),
        '513100': ('纳指ETF', 604),
    }
    print("=" * 70)
    print("  场景1: 30万 -> 通信ETF (直接买)")
    print("=" * 70)
    name, amt = ETF_LIQ['515880']; code = '515880'
    suggest_execution(code, name, 'buy', 300_000, amt * 10_000)

    print("=" * 70)
    print("  场景2: 80万 -> 通信ETF (分批)")
    print("=" * 70)
    suggest_execution(code, name, 'buy', 800_000, amt * 10_000)

    print("=" * 70)
    print("  场景3: 500万 -> 通信ETF (POV拆单)")
    print("=" * 70)
    suggest_execution(code, name, 'buy', 5_000_000, amt * 10_000)

    print("=" * 70)
    print("  场景4: 2000万 -> 通信ETF (全日VWAP)")
    print("=" * 70)
    suggest_execution(code, name, 'buy', 20_000_000, amt * 10_000)

    print("=" * 70)
    print("  场景5: V10换仓 -- 卖588170买515880，总资金500万")
    print("=" * 70)
    sn, sa = ETF_LIQ['588170']; sc = '588170'
    bn, ba = ETF_LIQ['515880']; bc = '515880'
    plan_rebalance(
        positions_to_sell=[(sc, sn, 5_000_000, sa * 10_000)],
        positions_to_buy=[(bc, bn, 5_000_000, ba * 10_000)],
        total_capital=5_000_000
    )