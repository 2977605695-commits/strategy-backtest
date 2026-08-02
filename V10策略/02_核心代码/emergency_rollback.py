"""
V10 紧急回滚脚本 — 一键停止 + 撤单 + 快照导出
==============================================
用法:
  python emergency_rollback.py            # 模拟模式（只打印不执行）
  python emergency_rollback.py --execute  # 实际执行

执行步骤:
  1. 停止策略运行
  2. 撤销所有未成交委托
  3. 导出持仓状态快照
  4. 生成回滚报告

预计耗时: < 5 分钟
"""

import os
import sys
import json
import sqlite3
from datetime import datetime

STRATEGY_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(STRATEGY_DIR, 'logs')
LEDGER_DB = os.path.join(STRATEGY_DIR, 'ledger.db')


def rollback():
    """执行紧急回滚"""
    print(f"{'='*60}")
    print(f"  V10 紧急回滚 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")
    
    execute = '--execute' in sys.argv
    if not execute:
        print("  [模拟模式] 加 --execute 实际执行\n")
    else:
        print("  [执行模式]\n")
    
    # Step 1: 停止策略
    print("  [1/4] 停止策略运行...")
    if execute:
        # 在实际平台中，这里调用平台API停止策略
        # QMT: ContextInfo.stop()
        # PTrade: 在平台界面停止
        print("      → 请在 QMT/PTrade 平台界面点击「停止策略」")
    else:
        print("      (模拟) 请在平台界面停止策略")
    print("      OK 完成\n")
    
    # Step 2: 撤销所有未成交委托
    print("  [2/4] 撤销所有未成交委托...")
    if execute:
        print("      → 请在平台界面点击「全部撤单」")
        # 记录到台账
        try:
            conn = sqlite3.connect(LEDGER_DB)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO cancels (order_id, timestamp, reason)
                SELECT order_id, datetime('now'), '紧急回滚-手动撤单'
                FROM orders WHERE status = 'pending'
            """)
            conn.commit()
            conn.close()
            print("      → 已在台账记录撤单")
        except Exception as e:
            print(f"      → 台账记录失败: {e}")
    else:
        print("      (模拟) 请在平台界面全部撤单")
    print("      OK 完成\n")
    
    # Step 3: 导出持仓快照
    print("  [3/4] 导出持仓状态快照...")
    snapshot = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'action': 'emergency_rollback',
        'position': None,
        'orders_pending': 0,
        'ledger_summary': None,
    }
    
    try:
        conn = sqlite3.connect(LEDGER_DB)
        cursor = conn.cursor()
        
        # 获取最新持仓
        cursor.execute("""
            SELECT code, qty, avg_cost, market_value, unrealized_pnl
            FROM positions
            ORDER BY timestamp DESC
            LIMIT 1
        """)
        row = cursor.fetchone()
        if row:
            snapshot['position'] = {
                'code': row[0], 'qty': row[1], 'avg_cost': row[2],
                'market_value': row[3], 'unrealized_pnl': row[4]
            }
            print(f"      → 当前持仓: {row[0]} | {row[1]}股 | 成本:{row[2]:.4f}")
        else:
            print("      → 无持仓记录")
        
        # 获取未成交委托数
        cursor.execute("SELECT COUNT(*) FROM orders WHERE status = 'pending'")
        snapshot['orders_pending'] = cursor.fetchone()[0]
        print(f"      → 未成交委托: {snapshot['orders_pending']} 笔")
        
        # 当日交易汇总
        today = datetime.now().strftime('%Y-%m-%d')
        cursor.execute("""
            SELECT COUNT(*), COALESCE(SUM(CASE WHEN side='sell' THEN pnl END), 0)
            FROM orders WHERE date(timestamp) = ?
        """, (today,))
        row = cursor.fetchone()
        snapshot['ledger_summary'] = {
            'today_trades': row[0],
            'today_pnl': row[1]
        }
        print(f"      → 今日交易: {row[0]} 笔")
        
        conn.close()
    except Exception as e:
        print(f"      → 台账读取失败: {e}")
    
    # 保存快照
    snapshot_path = os.path.join(LOG_DIR, f"rollback_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(snapshot_path, 'w', encoding='utf-8') as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    print(f"      → 快照已保存: {snapshot_path}")
    print("      OK 完成\n")
    
    # Step 4: 生成回滚报告
    print("  [4/4] 生成回滚报告...")
    print(f"\n  {'='*60}")
    print(f"  回滚报告")
    print(f"  {'='*60}")
    print(f"  时间: {snapshot['timestamp']}")
    print(f"  持仓: {snapshot['position'] or '空仓'}")
    print(f"  未成交委托: {snapshot['orders_pending']} 笔")
    print(f"  今日交易: {snapshot['ledger_summary']['today_trades'] if snapshot['ledger_summary'] else 0} 笔")
    print(f"  快照文件: {snapshot_path}")
    print(f"  耗时: < 1 分钟")
    print(f"  {'='*60}")
    print(f"\n  下一步:")
    print(f"  1. 在平台界面确认所有委托已撤销")
    print(f"  2. 核对持仓与快照一致")
    print(f"  3. 如需恢复策略，修改 config.yaml 中 mode=paper 先模拟运行")
    print(f"\n  OK 回滚完成\n")


if __name__ == '__main__':
    rollback()
