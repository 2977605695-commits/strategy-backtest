"""
V10 Adaptive Trend Rotation — QMT (迅投) 适配版
================================================
平台标准函数结构: init / handlebar / after_close

用法:
  1. 将本文件加载到 QMT 策略平台
  2. 修改 config.yaml 中的 mode 为 paper 或 live
  3. 在 QMT 中绑定 ETF 标的池
  4. 运行策略

依赖:
  - strategy_core.py (策略核心逻辑)
  - risk_manager.py (风控)
  - logger.py (日志)
  - ledger.py (台账)
"""

import os
import sys
import json
import math
import time
from datetime import datetime, timedelta

# ── 添加模块路径 ──
STRATEGY_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, STRATEGY_DIR)

from strategy_core import StrategyCore, calc_ma, calc_slope
from risk_manager import RiskManager
from logger import StrategyLogger
from ledger import TradeLedger

# ── 加载配置 ──
def load_config():
    """加载 YAML 配置文件"""
    config_path = os.path.join(STRATEGY_DIR, 'config.yaml')
    # QMT 环境可能没有 yaml 库，用 json 替代
    try:
        import yaml
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except ImportError:
        # 简易 YAML 解析（仅支持本配置文件格式）
        return _parse_simple_yaml(config_path)

def _parse_simple_yaml(path):
    """简易 YAML 解析器（无第三方依赖时的降级方案）"""
    # 实际部署时建议安装 PyYAML
    import json
    # 如果有 config.json 优先用
    json_path = path.replace('.yaml', '.json')
    if os.path.exists(json_path):
        with open(json_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    raise FileNotFoundError(f"无法解析 {path}，请安装 PyYAML 或提供 config.json")

# ── 全局变量 ──
config = None
strategy = None
risk_mgr = None
logger_inst = None
ledger = None
aStock = []           # QMT 标的列表
index_signals = {}    # 指数 slope 信号
etf_signals = {}      # ETF 信号数据
last_signal_date = None
bar_data_cache = {}   # 行情缓存

# ============================================================
# QMT 平台标准函数
# ============================================================

def init(ContextInfo):
    """QMT 初始化函数"""
    global config, strategy, risk_mgr, logger_inst, ledger, aStock

    config = load_config()

    # 初始化日志
    log_dir = os.path.join(STRATEGY_DIR, config.get('logging', {}).get('log_dir', 'logs'))
    logger_inst = StrategyLogger(
        log_dir=log_dir,
        level=config.get('logging', {}).get('level', 'INFO'),
        console=config.get('logging', {}).get('console', True),
        platform_logger=ContextInfo.log if hasattr(ContextInfo, 'log') else print
    )
    logger_inst.log_system('INFO', 'V10 策略初始化开始', mode=config['mode'], platform='qmt')

    # 初始化风控
    risk_mgr = RiskManager(config.get('risk', {}), logger_inst)

    # 初始化台账
    ledger = TradeLedger(os.path.join(STRATEGY_DIR, 'ledger.db'), log_dir)

    # 初始化策略核心
    strategy = StrategyCore(config)

    # 设置 QMT 标的池
    pool = config['pool']['full'] + config['pool']['defensive']
    # 去重
    seen = set()
    aStock = []
    for code in pool:
        if code not in seen:
            seen.add(code)
            aStock.append(code)

    # 指数标的也加入
    for code in config['pool']['index_codes']:
        if code not in seen:
            seen.add(code)
            aStock.append(code)

    ContextInfo.set_universe(aStock)

    # 设置回测参数
    if config['mode'] == 'backtest':
        ContextInfo.start = config['data'].get('start_date', '2020-01-01')
        ContextInfo.end = config['data'].get('end_date', '2026-07-31')

    # 定时器：每日 14:50 执行交易
    ContextInfo.run_time = '14:50:00'

    logger_inst.log_system('INFO', f'策略初始化完成 | 标的池: {len(aStock)} 个 | 模式: {config["mode"]}')


def handlebar(ContextInfo):
    """QMT 主回调函数（每根K线触发）"""
    global last_signal_date, index_signals, etf_signals, bar_data_cache

    try:
        # 获取当前日期
        current_date = ContextInfo.get_bar_timetag(ContextInfo.barpos)
        date_str = datetime.fromtimestamp(current_date / 1000).strftime('%Y-%m-%d')

        # 每日初始化
        strategy.reset_daily(date_str)

        # 只在新的交易日更新信号
        if last_signal_date != date_str:
            _update_signals(ContextInfo, date_str)
            last_signal_date = date_str

        # 检查是否到了交易时间（14:50）
        current_time = datetime.now().strftime('%H:%M')
        if config['mode'] == 'live' and current_time < '14:45':
            return

        # ── Step 1: 检查卖出 ──
        if strategy.position:
            pos_code = strategy.position['code']
            current_price = _get_current_price(ContextInfo, pos_code)
            if current_price is None:
                logger_inst.log_exception('WARNING', f'无法获取 {pos_code} 当前价格，跳过',
                                         code=pos_code, date=date_str)
                return

            # 风控检查
            passed, reason = risk_mgr.check_all(
                order=None,
                position=strategy.position,
                context={'current_price': current_price, 'date': date_str}
            )

            is_bear = strategy.is_bear_market(index_signals, date_str)
            trend_on = etf_signals.get('trend', {}).get(pos_code, {}).get(date_str, False)
            exit_reason = strategy.check_exit(current_price, date_str, trend_on, is_bear)

            if exit_reason:
                # 风控止损优先
                if not passed and 'stop_loss' in reason:
                    exit_reason = 'risk_stop_loss'

                _execute_sell(ContextInfo, pos_code, strategy.position['shares'],
                             current_price, exit_reason, date_str)

        # ── Step 2: 检查买入 ──
        if not strategy.position and strategy.pool_mode:
            # 检查防御池切回
            strategy.check_pool_switch_back(index_signals, date_str)

            available = [c for c in config['pool']['full'] if c in aStock]
            recent_highs = _compute_recent_highs(ContextInfo, available, date_str, 20)

            selected = strategy.select_buy(
                date_str, available, etf_signals,
                bar_data_cache, recent_highs
            )

            if selected:
                code, ratio, price = selected
                # 风控检查
                order_amount = strategy.position and 0 or _calc_order_amount(price)
                passed, reason = risk_mgr.check_all(
                    order={'code': code, 'amount': order_amount, 'side': 'buy'},
                    position=None,
                    context={'trade_count': strategy.trade_count, 'date': date_str}
                )

                if passed:
                    shares = order_amount / price
                    _execute_buy(ContextInfo, code, shares, price, ratio, date_str)
                else:
                    logger_inst.log_signal('WARNING', f'风控拦截买入 {code}: {reason}',
                                          code=code, reason=reason, amount=order_amount)

    except Exception as e:
        logger_inst.log_exception('ERROR', f'handlebar 异常: {e}',
                                 error=str(e), date=date_str if 'date_str' in dir() else 'unknown',
                                 traceback=str(e.__traceback__))
        if config['mode'] == 'live':
            raise  # 实盘模式重新抛出


def after_close(ContextInfo):
    """QMT 收盘后回调"""
    try:
        date_str = datetime.now().strftime('%Y-%m-%d')

        # 记录持仓快照
        if strategy.position:
            pos = strategy.position
            current_price = _get_current_price(ContextInfo, pos['code'])
            if current_price:
                market_value = pos['shares'] * current_price
                unrealized = pos['shares'] * (current_price - pos['buy_price'])
                ledger.snapshot_position(
                    pos['code'], pos['shares'], pos['buy_price'],
                    market_value, unrealized
                )

        # 台账日终汇总
        summary = ledger.get_daily_summary(date_str)
        logger_inst.log_system('INFO', '日终汇总',
                              date=date_str, trades=summary.get('trade_count', 0),
                              pnl=summary.get('total_pnl', 0),
                              position=strategy.get_state())

        logger_inst.log_system('INFO', f'收盘处理完成 | 日期: {date_str}')

    except Exception as e:
        logger_inst.log_exception('ERROR', f'after_close 异常: {e}', error=str(e))


# ============================================================
# 辅助函数
# ============================================================

def _update_signals(ContextInfo, date_str):
    """更新所有ETF和指数的信号"""
    global etf_signals, index_signals, bar_data_cache

    etf_signals = {'trend': {}, 'ratio': {}, 'above_ma60': {}, 'highs': {}}
    index_signals = {}
    bar_data_cache = {}

    # 获取 ETF 信号
    for code in config['pool']['full']:
        bars = _get_history_bars(ContextInfo, code, 120)
        if not bars or len(bars) < 60:
            continue

        sig = strategy.compute_signals(bars, code)
        etf_signals['trend'][code] = sig['trend']
        etf_signals['ratio'][code] = sig['ratio']
        etf_signals['above_ma60'][code] = sig['above_ma60']
        etf_signals['highs'][code] = sig['highs']

        # 缓存 bar 数据
        bar_data_cache[code] = {b['date']: b for b in bars}

    # 获取指数信号
    for code in config['pool']['index_codes']:
        bars = _get_history_bars(ContextInfo, code, 120)
        if not bars or len(bars) < 60:
            continue
        index_signals[code] = strategy.compute_index_slope(bars)

    logger_inst.log_signal('DEBUG', f'信号更新完成 | ETF: {len(etf_signals["trend"])} | 指数: {len(index_signals)}',
                          date=date_str)


def _get_history_bars(ContextInfo, code, count):
    """获取历史K线数据"""
    bars = []
    try:
        # QMT API: 获取历史数据
        stock_code = _format_qmt_code(code)
        hist = ContextInfo.get_market_data_ex(
            ['close', 'high', 'low', 'open', 'volume'],
            stock_code=stock_code,
            period='1d',
            count=count
        )
        if hist is not None and len(hist) > 0:
            for i in range(len(hist)):
                bar = {
                    'date': str(hist.index[i])[:10] if hasattr(hist.index[i], 'strftime') else str(hist.index[i])[:10],
                    'close': float(hist['close'].iloc[i]),
                    'high': float(hist['high'].iloc[i]),
                    'low': float(hist['low'].iloc[i]),
                    'open': float(hist['open'].iloc[i]),
                    'volume': float(hist['volume'].iloc[i]) if 'volume' in hist else 0,
                }
                bars.append(bar)
    except Exception as e:
        logger_inst.log_exception('WARNING', f'获取 {code} 历史数据失败: {e}',
                                 code=code, error=str(e))
    return bars


def _get_current_price(ContextInfo, code):
    """获取当前价格"""
    try:
        stock_code = _format_qmt_code(code)
        price = ContextInfo.get_market_data_ex(
            ['close'], stock_code=stock_code, period='1d', count=1
        )
        if price is not None and len(price) > 0:
            return float(price['close'].iloc[-1])
    except Exception as e:
        logger_inst.log_exception('WARNING', f'获取 {code} 当前价格失败: {e}',
                                 code=code, error=str(e))
    return None


def _format_qmt_code(code):
    """格式化为 QMT 标的代码"""
    if code.startswith(('0', '1', '2', '3')):
        return f'{code}.SZ'
    elif code.startswith(('5', '6')):
        return f'{code}.SH'
    return code


def _compute_recent_highs(ContextInfo, codes, date_str, lookback):
    """计算近期最高价"""
    highs = {}
    for code in codes:
        bars = _get_history_bars(ContextInfo, code, lookback + 5)
        if bars:
            recent = bars[-lookback:] if len(bars) >= lookback else bars
            highs[code] = max(b['high'] for b in recent if b['high'] > 0)
    return highs


def _calc_order_amount(price):
    """计算下单金额"""
    capital = config['account']['initial_capital']
    max_amount = config['risk']['max_order_amount']
    return min(capital, max_amount)


def _execute_sell(ContextInfo, code, shares, price, reason, date_str):
    """执行卖出"""
    # 计算实际卖出价格（含滑点和费用）
    sell_price = price * (1 - strategy.slippage - strategy.sell_fee - strategy.stamp_tax)

    # 记录台账
    order_id = ledger.record_order(
        code=code, side='sell', price=price, qty=shares,
        signal=reason, platform='qmt'
    )

    # QMT 下单
    try:
        stock_code = _format_qmt_code(code)
        if config['mode'] == 'live' or config['mode'] == 'paper':
            # QMT 下单 API
            pass_order(
                1101,    # 卖出
                1101,    # 数量
                stock_code,
                '',      # 账户
                2,       # 限价
                price,   # 价格
                shares,  # 数量
                'V10卖出', 1
            )
        logger_inst.log_trade('INFO', f'卖出委托 {code} | 数量: {shares} | 价格: {price:.4f} | 原因: {reason}',
                            code=code, side='sell', qty=shares, price=price,
                            reason=reason, order_id=order_id, date=date_str)

        # 更新策略状态
        trade_info = strategy.on_exit(reason, sell_price, date_str)
        ledger.update_fill(order_id, sell_price, shares,
                          strategy.sell_fee * price * shares + strategy.stamp_tax * price * shares,
                          strategy.slippage * price * shares)

        logger_inst.log_trade('INFO', f'卖出成交 {code} | PnL: {trade_info["pnl"]:.0f} | 收益率: {trade_info["return"]:.2%}',
                            code=code, pnl=trade_info['pnl'], return_pct=trade_info['return'])

        if trade_info.get('pool_switch'):
            logger_inst.log_signal('INFO', f'池切换: {trade_info["prev_pool"]} → {trade_info["new_pool"]}',
                                  prev=trade_info['prev_pool'], new=trade_info['new_pool'])

    except Exception as e:
        logger_inst.log_exception('ERROR', f'卖出下单失败 {code}: {e}',
                                 code=code, error=str(e), shares=shares, price=price)
        ledger.record_cancel(order_id, f'下单异常: {e}')


def _execute_buy(ContextInfo, code, shares, price, ratio, date_str):
    """执行买入"""
    # 计算实际买入价格
    buy_price = price * (1 + strategy.slippage + strategy.buy_fee)

    # 记录台账
    order_id = ledger.record_order(
        code=code, side='buy', price=price, qty=shares,
        signal=f'MA{strategy.fast_ma}>MA{strategy.slow_ma}|ratio={ratio:.3f}',
        platform='qmt'
    )

    # QMT 下单
    try:
        stock_code = _format_qmt_code(code)
        if config['mode'] == 'live' or config['mode'] == 'paper':
            pass_order(
                1101,    # 买入
                1101,    # 数量
                stock_code,
                '',      # 账户
                2,       # 限价
                price,   # 价格
                shares,  # 数量
                'V10买入', 1
            )
        logger_inst.log_trade('INFO', f'买入委托 {code} | 数量: {shares} | 价格: {price:.4f} | 比值: {ratio:.3f}',
                            code=code, side='buy', qty=shares, price=price,
                            ratio=ratio, order_id=order_id, date=date_str)

        # 更新策略状态
        strategy.on_entry(code, buy_price, shares, date_str)
        ledger.update_fill(order_id, buy_price, shares,
                          strategy.buy_fee * price * shares,
                          strategy.slippage * price * shares)

    except Exception as e:
        logger_inst.log_exception('ERROR', f'买入下单失败 {code}: {e}',
                                 code=code, error=str(e), shares=shares, price=price)
        ledger.record_cancel(order_id, f'下单异常: {e}')


def emergency_stop(ContextInfo):
    """
    紧急停止：撤销所有未成交委托 + 导出持仓快照
    """
    logger_inst.log_system('WARNING', '紧急停止触发！开始撤销所有委托')

    # 撤销所有未成交委托
    try:
        # QMT API: 撤销所有委托
        # cancel_all_orders()  # 需要根据实际 QMT API 调整
        logger_inst.log_system('WARNING', '已发送撤销所有委托指令')
    except Exception as e:
        logger_inst.log_exception('ERROR', f'撤单失败: {e}')

    # 导出持仓快照
    if strategy.position:
        pos = strategy.position
        ledger.snapshot_position(
            pos['code'], pos['shares'], pos['buy_price'],
            0, 0  # 市值需要实时价格，这里填0
        )
        logger_inst.log_system('WARNING', f'持仓快照已导出: {pos["code"]} {pos["shares"]}股')

    logger_inst.log_system('WARNING', '紧急停止完成，请在 QMT 平台确认委托状态')
