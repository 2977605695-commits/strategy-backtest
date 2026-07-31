# V10 ETF 自适应趋势轮动策略 — 生产版

> 32 ETF 池 | MA6/MA15 交叉 + MA8 斜率 + MA60 过滤 | 牛熊自适应防御池 | QMT/PTrade 双平台适配

## 策略概要

| 指标 | 修复前 | 修复后(T+1) |
|------|:---:|:---:|
| 夏普比率 | 1.521 | 1.510 |
| 总收益 | +1446% | +1381% |
| 最大回撤 | 22.3% | 25.6% |
| 交易笔数 | 161 | 172 |
| 胜率 | 55% | 55% |

## 文件结构

```
etf_strategy/
├── config.yaml              # 参数化配置（资金/标的池/风控/运行模式）
├── config.json              # JSON配置（无PyYAML降级）
├── strategy_core.py         # 策略核心逻辑（平台无关）
├── qmt_strategy.py          # QMT适配版（init/handlebar/after_close）
├── ptrade_strategy.py       # PTrade适配版（initialize/handle_data）
├── risk_manager.py          # 风控模块（6项可配置）
├── logger.py                # 四通道日志（signal/trade/exception/system）
├── ledger.py                # 交易台账（SQLite+CSV双写）
├── emergency_rollback.py    # 紧急回滚脚本
├── etf_executor.py          # 分档执行策略（VWAP/POV）
├── fix_lookahead.py         # 未来函数修复工具
├── etf30_vs_33.py           # V10核心回测代码（修复后）
├── etf_contribution.py      # ETF贡献分析
├── docs/
│   ├── 部署交接文档.html     # 环境/安装/检查清单/监控/异常排查/回滚
│   ├── API差异对照表.html     # QMT vs PTrade 12项映射+迁移指南
│   └── 回测验证报告.html     # 修复前后对比+逐年收益+指标卡片
└── logs/                    # 运行日志目录
```

## 快速开始

### 1. 回测验证
```bash
cd strategy-backtest
python etf_strategy/etf30_vs_33.py
```

### 2. 部署到 QMT
1. 将 `qmt_strategy.py` 加载到 QMT 平台
2. 修改 `config.yaml` 中 `mode: paper`，设置实际资金
3. 在 QMT 中绑定 32 个 ETF 标的
4. 运行策略

### 3. 部署到 PTrade
1. 将 `ptrade_strategy.py` 加载到 PTrade 平台
2. 修改 `config.yaml` 中 `mode: paper`，设置实际资金
3. 运行策略

### 4. 紧急停止
```bash
python etf_strategy/emergency_rollback.py --execute
```

## 信号规则

- **买入**: MA6 > MA15 且 MA8 斜率正 且 close > MA60（均用T-1日数据）
- **卖出**: Trail止损（牛市3%/熊市6%）或趋势退出
- **选股**: MA6/MA15 比值降序 Top1
- **池切换**: 连续5笔亏损>10%切防御池，7/10指数slope正切回全池

## 风控参数

| 参数 | 默认值 | 说明 |
|------|:---:|------|
| stop_loss_ratio | 8% | 单笔最大亏损 |
| take_profit_ratio | 30% | 单笔止盈 |
| max_positions | 1 | 最大同时持仓数 |
| max_order_amount | 2000万 | 单笔最大下单金额 |
| max_daily_trades | 20 | 日内最大交易次数 |
| max_cancel_reissue | 3 | 最大撤单重发次数 |

## 数据源

- 回测: 腾讯财经API (web.ifzq.gtimg.cn) + AkShare
- 实盘: QMT/PTrade 平台内置行情

## 许可

私有策略，仅供个人使用。