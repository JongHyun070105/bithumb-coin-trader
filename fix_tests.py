import re

with open('tests/test_risk_engine.py', 'r') as f:
    text = f.read()

text = text.replace('clean_book.asks = ((100_050_000.0, 10.0),)', 'clean_book = object.__new__(type(clean_book))\n    clean_book.__dict__.update(locals()["clean_book"].__dict__ if "clean_book" in locals() else {})\n    object.__setattr__(clean_book, "asks", ((100_050_000.0, 10.0),))')
text = text.replace('clean_book.asks = ((100_050_000.0, 0.001), (101_000_000.0, 1.0))', 'clean_book = object.__new__(type(clean_book))\n    clean_book.__dict__.update(locals()["clean_book"].__dict__ if "clean_book" in locals() else {})\n    object.__setattr__(clean_book, "asks", ((100_050_000.0, 0.001), (101_000_000.0, 1.0)))')

with open('tests/test_risk_engine.py', 'w') as f:
    f.write(text)

with open('src/bithumb_coin_trader/risk_engine.py', 'r') as f:
    text = f.read()
    
text = text.replace('f.write(json.dumps(audit.to_dict()) + "\\\\n")', 'f.write(json.dumps(audit.to_dict()) + "\\n")')

with open('src/bithumb_coin_trader/risk_engine.py', 'w') as f:
    f.write(text)

