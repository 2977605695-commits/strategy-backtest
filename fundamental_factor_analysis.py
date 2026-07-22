"""
基本面因子排序分析 — 提炼3个影响股价最大的基本面因子
=========================================================
从腾讯财经拉取70只个股的季度财务数据，
计算各因子在不同时间维度（月度/季度/半年度/年度）上的Rank IC，
筛选出预测力最强的3个因子。

候选因子：
  F1: ROE (净资产收益率)
  F2: 营收同比增长率 (Revenue YoY)
  F3: 净利润同比增长率 (Net Profit YoY)
  F4: 毛利率 (Gross Margin)
  F5: EPS同比增长
  F6: 净利率 (净利润/营收)

数据来源：
  AKShare stock_yjbb_em（底层：东方财富·腾讯财经）
  季报数据：2020Q1 ~ 2026Q1，共25个报告期
"""

import json
import os
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# 强制UTF-8输出
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ============================================================
# 0. 配置
# ============================================================
DATA_DIR = Path(r"C:\Users\home\Desktop\strategy-backtest\data")
CACHE_DIR = DATA_DIR / "_fundamentals_cache"
CACHE_DIR.mkdir(exist_ok=True)

# 报告期列表
QUARTERS = [
    "20200331", "20200630", "20200930", "20201231",
    "20210331", "20210630", "20210930", "20211231",
    "20220331", "20220630", "20220930", "20221231",
    "20230331", "20230630", "20230930", "20231231",
    "20240331", "20240630", "20240930", "20241231",
    "20250331",
]

# stock_yjbb_em 返回的列位置（固定格式，16列）
# col[0]  = 序号
# col[1]  = 股票代码
# col[2]  = 股票简称
# col[3]  = 每股收益(EPS)
# col[4]  = 营业收入(万元)
# col[5]  = 营业收入-同比增长(%)
# col[6]  = 营业收入-环比增长(%)
# col[7]  = 净利润(万元)
# col[8]  = 净利润-同比增长(%)
# col[9]  = 净利润-环比增长(%)
# col[10] = 每股净资产(BPS)
# col[11] = 净资产收益率(ROE%)
# col[12] = 每股经营现金流
# col[13] = 销售毛利率(%)
# col[14] = 所处行业
# col[15] = 公告日期

COL_MAP = {
    "code": 1,
    "name": 2,
    "eps": 3,
    "revenue": 4,
    "rev_yoy": 5,
    "rev_qoq": 6,
    "net_profit": 7,
    "profit_yoy": 8,
    "profit_qoq": 9,
    "bps": 10,
    "roe": 11,
    "cps": 12,
    "gross_margin": 13,
    "industry": 14,
    "pub_date": 15,
}


# ============================================================
# 1. 加载股票列表 & 价格数据
# ============================================================
def load_stocks():
    stocks = []
    for f in sorted(DATA_DIR.glob("*.json")):
        if f.name.startswith("_"):
            continue
        try:
            with open(f, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            stocks.append({
                "code": d["code"],
                "name": d["name"],
                "sector": d.get("sector", ""),
            })
        except Exception:
            pass
    return pd.DataFrame(stocks)


def load_prices():
    all_bars = []
    for f in sorted(DATA_DIR.glob("*.json")):
        if f.name.startswith("_"):
            continue
        try:
            with open(f, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            code = d["code"]
            df = pd.DataFrame(d["bars"])
            df["code"] = code
            df["date"] = pd.to_datetime(df["date"])
            # 统一用 close 作为价格
            all_bars.append(df[["date", "code", "close"]].rename(columns={"close": "price"}))
        except Exception:
            pass
    prices = pd.concat(all_bars, ignore_index=True)
    prices = prices.sort_values(["code", "date"]).reset_index(drop=True)
    return prices


# ============================================================
# 2. 拉取季度基本面数据
# ============================================================
def fetch_one_quarter(q_date):
    """拉取单个报告期的全市场数据，按位置解析列"""
    cache_file = CACHE_DIR / f"yjbb_{q_date}.csv"
    if cache_file.exists():
        return pd.read_csv(cache_file, dtype={"code": str})

    try:
        import akshare as ak
        raw = ak.stock_yjbb_em(date=q_date)

        if raw is None or raw.empty:
            return pd.DataFrame()

        # 按位置取值构建干净DataFrame
        n = len(raw)
        data = {}
        for name, idx in COL_MAP.items():
            if idx < raw.shape[1]:
                data[name] = raw.iloc[:, idx].values
            else:
                data[name] = [np.nan] * n

        df = pd.DataFrame(data)
        df["report_date"] = pd.Timestamp(q_date)

        # 确保code是6位字符串
        df["code"] = df["code"].astype(str).str.replace(r"\D", "", regex=True).str.zfill(6)

        # 数值列转换
        numeric_cols = ["eps", "revenue", "rev_yoy", "rev_qoq", "net_profit",
                        "profit_yoy", "profit_qoq", "bps", "roe", "cps", "gross_margin"]
        for c in numeric_cols:
            df[c] = pd.to_numeric(df[c], errors="coerce")

        df.to_csv(cache_file, index=False)
        return df

    except Exception as e:
        print(f"    [{q_date}] 拉取失败: {e}")
        return pd.DataFrame()


def build_factor_table(stocks_df):
    """拉取所有季度的数据并构建因子表"""
    our_codes = set(stocks_df["code"].values)
    all_data = []

    print(f"\n拉取 {len(QUARTERS)} 个报告期的基本面数据...")

    for i, q in enumerate(QUARTERS):
        df = fetch_one_quarter(q)
        if df.empty:
            print(f"  [{i+1:2d}/{len(QUARTERS)}] {q}  (空)")
            continue

        # 筛选目标股票
        our = df[df["code"].isin(our_codes)]
        n_found = len(our)
        print(f"  [{i+1:2d}/{len(QUARTERS)}] {q}  全市场{len(df):5d}只 | 命中{n_found:2d}只")
        all_data.append(our)
        time.sleep(0.25)

    if not all_data:
        print("无数据")
        return pd.DataFrame()

    raw = pd.concat(all_data, ignore_index=True)

    # 构建因子表
    factor_df = raw[["code", "report_date", "roe", "rev_yoy", "profit_yoy",
                      "gross_margin", "eps", "revenue", "net_profit", "bps"]].copy()
    factor_df = factor_df.rename(columns={"report_date": "date"})
    factor_df["date"] = pd.to_datetime(factor_df["date"])
    factor_df = factor_df.sort_values(["code", "date"]).reset_index(drop=True)

    # --- 衍生因子 ---
    # F6: 净利率 = 净利润/营收 * 100
    factor_df["net_margin"] = np.where(
        factor_df["revenue"].abs() > 0,
        factor_df["net_profit"] / factor_df["revenue"] * 100,
        np.nan,
    )

    # F5: EPS同比增长（4Q前 = 去年同期）
    factor_df["eps_yoy"] = (
        factor_df.groupby("code")["eps"]
        .transform(lambda x: x.pct_change(4, fill_method=None) * 100)
    )

    # 清理极端值（winsorize 1%）
    for col in ["roe", "rev_yoy", "profit_yoy", "gross_margin", "net_margin", "eps_yoy"]:
        if col in factor_df.columns:
            lo = factor_df[col].quantile(0.01)
            hi = factor_df[col].quantile(0.99)
            factor_df[col] = factor_df[col].clip(lo, hi)

    print(f"因子表: {len(factor_df)} 条, {factor_df['code'].nunique()} 只股票")
    return factor_df


# ============================================================
# 3. 因子与价格对齐
# ============================================================
def align_and_forward(prices, factor_df):
    """
    因子→日频对齐 + 计算各期限前向收益。
    季报滞后45天生效（模拟实际披露延迟）。
    """
    # 前向收益
    horizon_defs = {
        "月度 (21d)": 21,
        "季度 (63d)": 63,
        "半年 (126d)": 126,
        "年度 (252d)": 252,
    }

    for name, days in horizon_defs.items():
        prices[f"fwd_{days}d"] = (
            prices.groupby("code")["price"]
            .transform(lambda x: x.shift(-days) / x - 1)
        )

    # merge_asof 用 on="date" 后，date列只有一份，但其他同名列（如name）会有后缀
    # 重命名：prices里的date是trade_date, factor_df里的date是report_date
    prices_ren = prices.rename(columns={"date": "trade_date"})
    factor_ren = factor_df.rename(columns={"date": "report_date"})

    merged = pd.merge_asof(
        prices_ren.sort_values("trade_date"),
        factor_ren.sort_values("report_date"),
        by="code",
        left_on="trade_date",
        right_on="report_date",
        direction="backward",
        tolerance=pd.Timedelta(days=200),
    )

    # 季报披露滞后（保守45天，模拟从季末到实际公告的延迟）
    LAG = pd.Timedelta(days=45)
    merged["_factor_valid"] = merged["report_date"] + LAG

    factor_cols = ["roe", "rev_yoy", "profit_yoy", "gross_margin",
                    "net_margin", "eps_yoy"]
    for col in factor_cols:
        if col in merged.columns:
            merged.loc[merged["trade_date"] < merged["_factor_valid"], col] = np.nan

    return merged, horizon_defs


# ============================================================
# 4. Rank IC 分析
# ============================================================
def rank_ic_analysis(merged, horizons):
    """
    Rank IC = Spearman(factor_value, forward_return) 逐月截面。
    返回IC均值、ICIR、胜率、t统计量。
    """
    factors = {
        "roe":           "ROE (净资产收益率)",
        "rev_yoy":       "营收同比 (Revenue YoY)",
        "profit_yoy":    "净利同比 (Net Profit YoY)",
        "gross_margin":  "毛利率 (Gross Margin)",
        "net_margin":    "净利率 (Net Margin)",
        "eps_yoy":       "EPS同比 (EPS YoY)",
    }

    results = []
    merged = merged.copy()
    merged["_ym"] = merged["trade_date"].dt.to_period("M")

    for h_name, h_days in horizons.items():
        fwd = f"fwd_{h_days}d"

        for fac_key, fac_label in factors.items():
            if fac_key not in merged.columns:
                continue

            ic_list = []
            for ym, grp in merged.groupby("_ym"):
                valid = grp[[fac_key, fwd]].dropna()
                if len(valid) < 12:
                    continue
                # Spearman = Pearson on ranks (no scipy needed)
                ic = valid[fac_key].rank().corr(valid[fwd].rank())
                if pd.notna(ic):
                    ic_list.append(ic)

            if len(ic_list) < 5:
                continue

            ic_arr = np.array(ic_list)
            mu = np.mean(ic_arr)
            sd = np.std(ic_arr, ddof=1) if len(ic_arr) > 1 else 1.0
            ir = mu / sd if sd > 0 else 0
            pos = np.mean(ic_arr > 0)
            tstat = mu / (sd / np.sqrt(len(ic_arr))) if sd > 0 else 0

            results.append({
                "时间维度": h_name,
                "因子": fac_label,
                "因子代码": fac_key,
                "|IC|均值": round(abs(mu), 4),
                "Mean IC": round(mu, 4),
                "IC_IR": round(ir, 4),
                "IC胜率": round(pos, 4),
                "t值": round(tstat, 2),
                "截面数": len(ic_list),
            })

    return pd.DataFrame(results) if results else pd.DataFrame()


# ============================================================
# 5. 因子变化量 Rank IC
# ============================================================
def factor_change_ic(merged, horizons):
    """
    计算因子变化量（环比/半年比/年比）对前向收益的预测力
    """
    df = merged.sort_values(["code", "trade_date"]).copy()

    base = ["roe", "rev_yoy", "profit_yoy", "gross_margin", "net_margin", "eps_yoy"]
    labels = {
        "roe": "ROE", "rev_yoy": "营收YoY", "profit_yoy": "净利YoY",
        "gross_margin": "毛利率", "net_margin": "净利率", "eps_yoy": "EPS YoY",
    }

    # 变化量：1期(季) / 2期(半年) / 4期(年)
    for fac in base:
        if fac not in df.columns:
            continue
        for lag, tag in [(1, "季变"), (2, "半年变"), (4, "年变")]:
            df[f"{fac}_{tag}"] = df.groupby("code")[fac].diff(lag)

    change_cols = []
    for fac in base:
        for tag in ["季变", "半年变", "年变"]:
            c = f"{fac}_{tag}"
            if c in df.columns:
                change_cols.append((c, f"{labels.get(fac, fac)}{tag}"))

    results = []
    df["_ym"] = df["trade_date"].dt.to_period("M")

    for h_name, h_days in horizons.items():
        fwd = f"fwd_{h_days}d"
        if fwd not in df.columns:
            continue

        for col, label in change_cols:
            ic_list = []
            for ym, grp in df.groupby("_ym"):
                valid = grp[[col, fwd]].dropna()
                if len(valid) < 12:
                    continue
                # Spearman = Pearson on ranks
                ic = valid[col].rank().corr(valid[fwd].rank())
                if pd.notna(ic):
                    ic_list.append(ic)

            if len(ic_list) < 5:
                continue

            ic_arr = np.array(ic_list)
            mu = np.mean(ic_arr)
            sd = np.std(ic_arr, ddof=1) if len(ic_arr) > 1 else 1.0
            ir = mu / sd if sd > 0 else 0
            pos = np.mean(ic_arr > 0)

            results.append({
                "时间维度": h_name,
                "因子(变化)": label,
                "|IC|均值": round(abs(mu), 4),
                "Mean IC": round(mu, 4),
                "IC_IR": round(ir, 4),
                "IC胜率": round(pos, 4),
                "截面数": len(ic_list),
            })

    return pd.DataFrame(results) if results else pd.DataFrame()


# ============================================================
# 6. 因子衰减分析
# ============================================================
def factor_decay_analysis(merged, horizons):
    """
    分析每个因子的IC在各时间维度上的衰减模式。
    好的长期因子：短期IC温和，长期IC逐步放大（趋势预测力）。
    好的短期因子：短期IC高，长期IC衰减快（反转/时效性强）。
    """
    factors = {
        "roe": "ROE", "rev_yoy": "营收YoY", "profit_yoy": "净利YoY",
        "gross_margin": "毛利率", "net_margin": "净利率", "eps_yoy": "EPS YoY",
    }

    print("\n" + "-" * 64)
    print("  因子 IC 衰减分析（|IC| 在各时间维度上的变化）")
    print("-" * 64)
    print(f"  {'因子':<22s}", end="")
    for h_name in horizons:
        print(f"{h_name:>14s}", end="")
    print(f"  {'趋势':>6s}")
    print("  " + "-" * 62)

    decay_data = {}
    for fac_key, fac_label in factors.items():
        if fac_key not in merged.columns:
            continue
        ic_by_horizon = []
        row_str = f"  {fac_label:<22s}"
        for h_name, h_days in horizons.items():
            fwd = f"fwd_{h_days}d"
            merged["_ym"] = merged["trade_date"].dt.to_period("M")
            ic_list = []
            for ym, grp in merged.groupby("_ym"):
                valid = grp[[fac_key, fwd]].dropna()
                if len(valid) < 12:
                    continue
                # Spearman = Pearson on ranks (no scipy needed)
                ic = valid[fac_key].rank().corr(valid[fwd].rank())
                if pd.notna(ic):
                    ic_list.append(abs(ic))
            avg_abs_ic = np.mean(ic_list) if ic_list else 0
            ic_by_horizon.append(avg_abs_ic)
            row_str += f"{avg_abs_ic:>14.4f}"

        # 趋势判断
        if len(ic_by_horizon) >= 3:
            if ic_by_horizon[-1] > ic_by_horizon[0] * 1.1:
                trend = "📈 递增"
            elif ic_by_horizon[-1] < ic_by_horizon[0] * 0.9:
                trend = "📉 递减"
            else:
                trend = "➡ 平稳"
        else:
            trend = "—"
        row_str += f"  {trend:>6s}"
        print(row_str)
        decay_data[fac_key] = ic_by_horizon

    return decay_data


# ============================================================
# 7. 主流程
# ============================================================
def main():
    print("=" * 64)
    print("  基本面因子排序 ─ 多时间维度 Rank IC 分析")
    print("  数据源: 腾讯财经 (AKShare stock_yjbb_em)")
    print("=" * 64)

    # ── 加载数据 ──
    stocks = load_stocks()
    print(f"\n股票池: {len(stocks)} 只  |  报告期: {len(QUARTERS)} 个季报")
    print(f"区间: {QUARTERS[0]} ~ {QUARTERS[-1]}")

    prices = load_prices()
    print(f"价格: {len(prices)} 条日线, {prices['code'].nunique()} 只, "
          f"{prices['date'].min().date()} ~ {prices['date'].max().date()}")

    # ── 拉取因子 ──
    factor_df = build_factor_table(stocks)
    if factor_df.empty:
        print("无法获取基本面数据，退出")
        return

    # 显示因子覆盖情况
    print(f"\n因子覆盖统计:")
    for col in ["roe", "rev_yoy", "profit_yoy", "gross_margin", "net_margin", "eps_yoy"]:
        if col in factor_df.columns:
            n = factor_df[col].notna().sum()
            print(f"  {col:<15s}: {n:5d} / {len(factor_df)}")

    # ── 对齐 ──
    print(f"\n对齐因子到日频价格...")
    merged, horizons = align_and_forward(prices, factor_df)

    # ── 水平值 IC ──
    print("计算因子水平值 Rank IC...")
    level_ic = rank_ic_analysis(merged, horizons)

    # ── 变化量 IC ──
    print("计算因子变化量 Rank IC...")
    change_ic = factor_change_ic(merged, horizons)

    # ============================================================
    # 输出
    # ============================================================
    print("\n" + "=" * 64)
    print("  📊 结果一：因子水平值 Rank IC")
    print("=" * 64)
    if not level_ic.empty:
        # 按 |IC| 降序排列
        disp_level = level_ic.sort_values("|IC|均值", ascending=False)
        print(disp_level.to_string(index=False))

        # 各时间维度 Top 3
        print("\n  ─── 各时间维度 Top 3 ───")
        for h_name in horizons:
            sub = level_ic[level_ic["时间维度"] == h_name].nlargest(3, "|IC|均值")
            if sub.empty:
                continue
            print(f"\n  【{h_name}】")
            for _, r in sub.iterrows():
                print(f"    {r['因子']:<30s}  "
                      f"IC={r['Mean IC']:+.4f}  "
                      f"|IC|={r['|IC|均值']:.4f}  "
                      f"IR={r['IC_IR']:+.2f}  "
                      f"胜率={r['IC胜率']:.0%}")

        # ── 综合排名 ──
        print(f"\n  ─── 综合排名（4维度 |IC| 加权平均）───")
        summary = level_ic.groupby(["因子代码", "因子"])["|IC|均值"].mean().sort_values(ascending=False)
        for rank, ((code, name), score) in enumerate(summary.items(), 1):
            print(f"    #{rank}  {name:<30s}  |IC|均值={score:.4f}")

    else:
        print("  (无结果)")

    # ── 变化量结果 ──
    print("\n" + "=" * 64)
    print("  📊 结果二：因子变化量 Rank IC（环比变化→前向收益）")
    print("=" * 64)
    if not change_ic.empty:
        disp_change = change_ic.sort_values("|IC|均值", ascending=False)
        print(disp_change.head(20).to_string(index=False))

        # 月度维度最佳变化量因子
        print("\n  ─── 月度维度 Top 5 变化量因子 ───")
        sub = change_ic[change_ic["时间维度"] == "月度 (21d)"].nlargest(5, "|IC|均值")
        for _, r in sub.iterrows():
            print(f"    {r['因子(变化)']:<30s}  "
                  f"IC={r['Mean IC']:+.4f}  "
                  f"|IC|={r['|IC|均值']:.4f}  "
                  f"IR={r['IC_IR']:+.2f}")

    # ── 衰减分析 ──
    factor_decay_analysis(merged, horizons)

    # ── 保存 ──
    if not level_ic.empty:
        level_ic.to_csv(DATA_DIR.parent / "factor_level_ic.csv", index=False)
    if not change_ic.empty:
        change_ic.to_csv(DATA_DIR.parent / "factor_change_ic.csv", index=False)
    print(f"\n结果已保存到 {DATA_DIR.parent}")

    # ============================================================
    # 🏆 最终推荐
    # ============================================================
    print("\n" + "=" * 64)
    print("  🏆 最终推荐：3个核心基本面因子")
    print("=" * 64)

    if level_ic.empty:
        print("  数据不足")
        return

    summary = level_ic.groupby(["因子代码", "因子"])["|IC|均值"].mean().sort_values(ascending=False)
    top3 = list(summary.head(3).items())

    for rank, ((code, name), score) in enumerate(top3, 1):
        fac_data = level_ic[level_ic["因子代码"] == code]
        best = fac_data.loc[fac_data["|IC|均值"].idxmax()]
        print(f"\n  ┌─ #{rank} 因子: {name}")
        print(f"  ├─ 综合 |IC| 均值: {score:.4f}")
        print(f"  ├─ 最佳维度: {best['时间维度']}")
        print(f"  │   (IC={best['Mean IC']:+.4f}, IR={best['IC_IR']:+.2f}, 胜率={best['IC胜率']:.0%})")

        # 各维度IC明细
        dim_detail = level_ic[level_ic["因子代码"] == code].sort_values("时间维度")
        print(f"  └─ 各维度IC: ", end="")
        dims = []
        for _, dr in dim_detail.iterrows():
            dims.append(f"{dr['时间维度']}={dr['Mean IC']:+.4f}")
        print(" | ".join(dims))

    print(f"\n{'='*64}")
    print(f"  分析完成")
    print(f"{'='*64}")


if __name__ == "__main__":
    main()
