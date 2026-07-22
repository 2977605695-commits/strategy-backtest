# MA5 均线偏离策略 · 个股回测项目

基于 MA5 均线偏离度的量化交易策略回测系统，覆盖 5 只 A 股半导体标的（北方华创、士兰微、澜起科技、长电科技、长川科技）。

## 文件说明

| 文件 | 用途 |
|------|------|
| `strategy_backtest_report.md` | 📊 **完整回测报告**（所有方案对比、最终推荐） |
| `bounce_ma7_strategy.py` | 🔧 个股策略主回测脚本（支持 Trail/ATR/MACD/高位换手） |
| `etf_priority_rotation.py` | 🔧 ETF 优先轮动策略回测 |
| `etf_grid_search.py` | 🔧 ETF 等权组合网格搜索 |
| `etf_aggressive_sweep.py` | 🔧 ETF 激进参数全覆盖扫描 |
| `etf_optimizer.py` | 🔧 ETF 参数优化器 |
| `etf_strategy_formula.md` | 📊 ETF 策略公式手册 |

## 最终推荐策略

```
策略: MA5 均线偏离 + Trail 10% 移动止损 + 高位放量止盈

买入:   DEV(MA5) < -4.5%
止盈:   持仓收益 ≥ 5% AND 成交量 ≥ 2.5x MA20(成交量)
止损:   收盘价 ≤ 持仓最高价 × 0.90
仓位:   等权 20% × 5 只

预期:   夏普 1.61 | 总收益 274% | 最大回撤 27% (2024-01 → 2026-07)
```

## 回测汇总

| 排名 | 方案 | 夏普 | 收益 | 回撤 |
|:---:|------|:---:|------:|------:|
| 🥇 | 高位放量止盈 + Trail 10% | **1.61** | 273.7% | 26.5% |
| 🥈 | Trail 10% | 1.58 | 279.5% | 26.9% |
| 🥉 | Trail 12% | 1.46 | 250.0% | 27.4% |
| 4 | 不止损 | 1.26 | 149.7% | 23.7% |

## 运行方式

```bash
# 个股策略回测
python bounce_ma7_strategy.py

# ETF 优先轮动
python etf_priority_rotation.py

# ETF 网格搜索
python etf_grid_search.py
```

数据源：腾讯财经免费日线（前复权），无需 API Key。
