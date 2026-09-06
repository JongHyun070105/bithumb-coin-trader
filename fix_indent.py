import re

with open('src/bithumb_coin_trader/risk_engine.py', 'r') as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    if "        audit = RiskAuditRecord(" in line or "            audit = RiskAuditRecord(" in line:
        pass
    new_lines.append(line)
