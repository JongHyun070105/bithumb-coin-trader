import re

with open('src/bithumb_coin_trader/risk_engine.py', 'r') as f:
    text = f.read()

# Replace from "        ctx_hash = hashlib.sha256(json.dumps(ctx, sort_keys=True).encode("utf-8")).hexdigest()"
# to "        return verdict, tuple(reasons), audit"

replacement = """        ctx_hash = hashlib.sha256(json.dumps(ctx, sort_keys=True).encode("utf-8")).hexdigest()
        audit = RiskAuditRecord(
            timestamp_ms=timestamp_ms,
            order_id=order_id,
            verdict=verdict,
            reasons=tuple(reasons),
            context_hash=ctx_hash,
        )
        self.audit_log.append(audit)
        
        if self._audit_sink_path:
            with open(self._audit_sink_path, 'a') as f:
                import json
                f.write(json.dumps(audit.to_dict()) + '\\n')
                f.flush()
                
        return verdict, tuple(reasons), audit"""

text = re.sub(r'        ctx_hash = hashlib\.sha256\(json\.dumps\(ctx, sort_keys=True\)\.encode\("utf-8"\)\)\.hexdigest\(\)[\s\S]*?return verdict, tuple\(reasons\), audit', replacement, text)

with open('src/bithumb_coin_trader/risk_engine.py', 'w') as f:
    f.write(text)
