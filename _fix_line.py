"""Fix the problematic f-string line"""
import sys
path = sys.argv[1] if len(sys.argv) > 1 else 'fetch_and_backtest_all_etfs.py'
with open(path, encoding='utf-8') as f:
    lines = f.readlines()
for i, line in enumerate(lines):
    if 'ETFs (this)' in line and 'f"' in line:
        lines[i] = "    pool_nm = str(len(etfs)) + ' ETFs (this)'\n"
        lines.insert(i+1, "    print(f'{pool_nm:<20s} {sh:>7.3f} {tr*100:>7.2f}% {mdd*100:>6.2f}% {len(sell_tr):>5d}')\n")
        print(f'Fixed line {i+1}')
        break
with open(path, 'w', encoding='utf-8') as f:
    f.writelines(lines)
print('Done')
