"""
Max Gain Factor Analysis - Recalculate factor impact weights and re-rank
========================================================================
Target: forward MAXIMUM gain (not point-to-point return)
Output: factor impact ratios + latest stock ranking with weighted factors
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

DATA_DIR = Path(r"C:\Users\home\Desktop\strategy-backtest\data")
CACHE_DIR = DATA_DIR / "_fundamentals_cache"


def load_prices():
    all_bars = []
    for f in sorted(DATA_DIR.glob("*.json")):
        if f.name.startswith("_"):
            continue
        with open(f, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        code = d["code"]
        df = pd.DataFrame(d["bars"])
        df["code"] = code
        df["date"] = pd.to_datetime(df["date"])
        all_bars.append(df[["date", "code", "close"]].rename(columns={"close": "price"}))
    prices = pd.concat(all_bars, ignore_index=True)
    prices = prices.sort_values(["code", "date"]).reset_index(drop=True)
    return prices


def load_factor_table():
    records = []
    for cache_file in sorted(CACHE_DIR.glob("yjbb_*.csv")):
        q_date = cache_file.stem.replace("yjbb_", "")
        df = pd.read_csv(cache_file, dtype={"code": str})
        df["report_date"] = pd.Timestamp(q_date)
        df["code"] = df["code"].str.zfill(6)
        records.append(df)

    if not records:
        raise RuntimeError("No cache data")

    raw = pd.concat(records, ignore_index=True)
    cols = ["code", "report_date", "roe", "rev_yoy", "profit_yoy",
            "gross_margin", "eps", "revenue", "net_profit", "bps"]
    factor_df = raw[cols].copy()
    factor_df["report_date"] = pd.to_datetime(factor_df["report_date"])
    factor_df["net_margin"] = np.where(
        factor_df["revenue"].abs() > 0,
        factor_df["net_profit"] / factor_df["revenue"] * 100,
        np.nan,
    )
    factor_df["eps_yoy"] = factor_df.groupby("code")["eps"].transform(
        lambda x: x.pct_change(4) * 100
    )
    for col in ["roe", "rev_yoy", "profit_yoy", "gross_margin", "net_margin", "eps_yoy"]:
        if col in factor_df.columns:
            lo = factor_df[col].quantile(0.01)
            hi = factor_df[col].quantile(0.99)
            factor_df[col] = factor_df[col].clip(lo, hi)
    return factor_df


def compute_max_gain_and_align(prices, factor_df):
    horizons = {
        "Monthly(21d)": 21,
        "Quarterly(63d)": 63,
        "SemiAnnual(126d)": 126,
        "Annual(252d)": 252,
    }

    prices = prices.sort_values(["code", "date"]).reset_index(drop=True)

    for name, days in horizons.items():
        prices[f"max_gain_{days}d"] = (
            prices.groupby("code")["price"]
            .transform(lambda x: x.rolling(days, min_periods=5).max().shift(-days) / x - 1)
        )

    prices_ren = prices.rename(columns={"date": "trade_date"})
    factor_ren = factor_df.rename(columns={"report_date": "date"})

    merged = pd.merge_asof(
        prices_ren.sort_values("trade_date"),
        factor_ren.sort_values("date"),
        by="code",
        left_on="trade_date",
        right_on="date",
        direction="backward",
        tolerance=pd.Timedelta(days=200),
    )

    LAG = pd.Timedelta(days=45)
    merged["_valid"] = merged["date"] + LAG
    factor_cols = ["roe", "rev_yoy", "profit_yoy", "gross_margin", "net_margin", "eps_yoy"]
    for col in factor_cols:
        if col in merged.columns:
            merged.loc[merged["trade_date"] < merged["_valid"], col] = np.nan

    return merged, horizons


def max_gain_ic_and_weights(merged, horizons):
    factors = {
        "roe": "ROE",
        "rev_yoy": "Revenue_YoY",
        "profit_yoy": "NP_YoY",
        "gross_margin": "GrossMargin",
        "net_margin": "NetMargin",
        "eps_yoy": "EPS_YoY",
    }

    results = []
    merged = merged.copy()
    merged["_ym"] = merged["trade_date"].dt.to_period("M")

    for h_name, h_days in horizons.items():
        fwd = f"max_gain_{h_days}d"
        if fwd not in merged.columns:
            continue

        for fac_key, fac_label in factors.items():
            if fac_key not in merged.columns:
                continue

            ic_list = []
            for ym, grp in merged.groupby("_ym"):
                valid = grp[[fac_key, fwd]].dropna()
                if len(valid) < 12:
                    continue
                ic = valid[fac_key].rank().corr(valid[fwd].rank())
                if pd.notna(ic):
                    ic_list.append(ic)

            if len(ic_list) < 5:
                continue

            ic_arr = np.array(ic_list)
            mu = np.mean(ic_arr)
            sd = np.std(ic_arr, ddof=1)
            ir = mu / sd if sd > 0 else 0
            pos = np.mean(ic_arr > 0)

            results.append({
                "Horizon": h_name,
                "Factor": fac_label,
                "FactorCode": fac_key,
                "Mean_IC": round(mu, 4),
                "Abs_IC": round(abs(mu), 4),
                "IC_IR": round(ir, 4),
                "WinRate": round(pos, 4),
                "N_Periods": len(ic_list),
            })

    ic_df = pd.DataFrame(results)
    summary = ic_df.groupby(["FactorCode", "Factor"])["Abs_IC"].mean().sort_values(ascending=False)
    total_ic = summary.sum()
    weights = (summary / total_ic * 100).round(2)

    return ic_df, summary, weights


def rerank_with_weights(weights_dict):
    df = pd.read_csv(CACHE_DIR / "yjbb_20250331.csv", dtype={"code": str})
    df["code"] = df["code"].str.zfill(6)

    df["net_margin"] = np.where(
        df["revenue"].abs() > 0,
        df["net_profit"] / df["revenue"] * 100,
        np.nan,
    )

    # eps_yoy from 2024Q1
    prev_path = CACHE_DIR / "yjbb_20240331.csv"
    if prev_path.exists():
        prev = pd.read_csv(prev_path, dtype={"code": str})
        prev["code"] = prev["code"].str.zfill(6)
        eps_prev = dict(zip(prev["code"], prev["eps"]))
        df["eps_yoy"] = df.apply(
            lambda r: (r["eps"] / eps_prev[r["code"]] - 1) * 100
            if r["code"] in eps_prev and pd.notna(eps_prev[r["code"]])
               and pd.notna(r["eps"]) and eps_prev[r["code"]] != 0
            else np.nan, axis=1
        )

    # Winsorize
    for col in ["roe", "rev_yoy", "profit_yoy", "gross_margin", "net_margin", "eps_yoy"]:
        if col in df.columns:
            lo = df[col].quantile(0.02)
            hi = df[col].quantile(0.98)
            df[col] = df[col].clip(lo, hi)

    # Z-score
    for col in ["roe", "rev_yoy", "profit_yoy", "gross_margin", "net_margin", "eps_yoy"]:
        if col in df.columns:
            df[f"z_{col}"] = (df[col] - df[col].mean()) / df[col].std()

    # Weighted score
    df["score"] = 0.0
    for fac_key, weight in weights_dict.items():
        z_col = f"z_{fac_key}"
        if z_col in df.columns:
            df["score"] += df[z_col] * weight / 100

    df = df.sort_values("score", ascending=False).reset_index(drop=True)
    df["rank"] = range(1, len(df) + 1)
    return df


def main():
    print("=" * 80)
    print("  Max Gain Factor Analysis - Impact Weights & Re-ranking")
    print("=" * 80)

    prices = load_prices()
    factor_df = load_factor_table()
    print(f"\nPrices: {len(prices)} rows | Factors: {len(factor_df)} rows")

    merged, horizons = compute_max_gain_and_align(prices, factor_df)
    print(f"Aligned: {len(merged)} rows")

    ic_df, summary, weights = max_gain_ic_and_weights(merged, horizons)

    # --- Results ---
    print("\n" + "=" * 80)
    print("  Rank IC by Horizon (Target: Max Forward Gain)")
    print("=" * 80)
    for h in ["Monthly(21d)", "Quarterly(63d)", "SemiAnnual(126d)", "Annual(252d)"]:
        sub = ic_df[ic_df["Horizon"] == h].sort_values("Abs_IC", ascending=False)
        if sub.empty:
            continue
        print(f"\n  [{h}]")
        for _, r in sub.iterrows():
            bar = "+" * int(r["Abs_IC"] * 200) + "-" * max(0, 10 - int(r["Abs_IC"] * 200))
            print(f"    {r['Factor']:<15s} IC={r['Mean_IC']:+.4f}  "
                  f"|IC|={r['Abs_IC']:.4f}  IR={r['IC_IR']:+.2f}  "
                  f"Win={r['WinRate']:.0%}  {bar}")

    # --- Impact Weights ---
    print("\n" + "=" * 80)
    print("  Factor Impact Weights (based on |IC| across all horizons)")
    print("=" * 80)
    total = summary.sum()
    print(f"\n  {'Factor':<20s} {'Abs_IC':>8s} {'Weight':>10s}  Distribution")
    print("  " + "-" * 60)
    for (code, name), score in summary.items():
        w = score / total * 100
        bar = "#" * int(w)
        print(f"  {name:<20s} {score:>8.4f} {w:>9.2f}%  {bar}")

    # --- Weights Table ---
    print(f"\n  Final weights:")
    for (code, name), score in summary.items():
        w = score / total * 100
        print(f"    {name:<20s} = {w:.1f}%")

    # Save weights
    weight_map = {}
    for (code, _), w in zip(summary.index, weights):
        weight_map[code] = float(w)

    # --- Re-rank ---
    print("\n" + "=" * 80)
    print("  Latest Ranking (2025Q1) - Weighted by Max Gain Factor Impact")
    print("=" * 80)

    ranked = rerank_with_weights(weight_map)

    # Name mapping
    with open(DATA_DIR / "_summary.json", "r", encoding="utf-8") as f:
        stocks_info = json.load(f)["stocks"]
    name_map = {s["code"]: s["name"] for s in stocks_info}
    sector_map = {s["code"]: s["sector"] for s in stocks_info}
    ranked["name"] = ranked["code"].map(lambda c: name_map.get(c, "?"))
    ranked["sector"] = ranked["code"].map(lambda c: sector_map.get(c, "?"))

    # TOP 25
    print(f"\n  [ TOP 25 ]\n")
    for _, r in ranked.head(25).iterrows():
        py = r["profit_yoy"]
        py_s = f'{py:>7.0f}%' if pd.notna(py) else '    N/A'
        tag = "*" if r["rank"] <= 10 else " "
        print(f"  {tag}{int(r['rank']):>3d} {r['code']} {r['name']:<10s}  "
              f"ROE={r['roe']:>5.1f}  NPM={r['net_margin']:>6.1f}  "
              f"NP-YoY={py_s}  score={r['score']:>+.3f}  [{r['sector']}]")

    # Bottom 10
    print(f"\n  [ Bottom 10 ]\n")
    for _, r in ranked.tail(10).iterrows():
        sc = r['score']
        sc_s = f'{sc:>+.3f}' if pd.notna(sc) else '    N/A'
        py = r["profit_yoy"]
        py_s = f'{py:>7.0f}%' if pd.notna(py) else '    N/A'
        print(f"  XX{int(r['rank']):>3d} {r['code']} {r['name']:<10s}  "
              f"ROE={r['roe']:>5.1f}  NPM={r['net_margin']:>6.1f}  "
              f"NP-YoY={py_s}  score={sc_s}  [{r['sector']}]")

    # Save
    out_cols = ["rank", "code", "name", "sector", "roe", "net_margin", "profit_yoy",
                "gross_margin", "rev_yoy", "eps_yoy", "score"]
    ranked[out_cols].to_csv("latest_ranking_max_gain.csv", index=False, encoding="utf-8-sig")
    ic_df.to_csv("max_gain_factor_ic.csv", index=False, encoding="utf-8-sig")
    print(f"\n  Saved: latest_ranking_max_gain.csv, max_gain_factor_ic.csv")


if __name__ == "__main__":
    main()
