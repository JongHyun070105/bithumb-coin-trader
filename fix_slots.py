import re

with open('tests/test_risk_engine.py', 'r') as f:
    text = f.read()

import_replace = 'import dataclasses\n'
if 'import dataclasses' not in text:
    text = import_replace + text

text = re.sub(r'clean_book = object\.__new__\(type\(clean_book\)\)\n\s+clean_book\.__dict__\.update\(locals\(\)\["clean_book"\]\.__dict__ if "clean_book" in locals\(\) else \{\}\)\n\s+object\.__setattr__\(clean_book, "asks", \(\(100_050_000\.0, 10\.0\),\)\)',
              'clean_book = dataclasses.replace(clean_book, asks=((100_050_000.0, 10.0),))', text)

text = re.sub(r'clean_book = object\.__new__\(type\(clean_book\)\)\n\s+clean_book\.__dict__\.update\(locals\(\)\["clean_book"\]\.__dict__ if "clean_book" in locals\(\) else \{\}\)\n\s+object\.__setattr__\(clean_book, "asks", \(\(100_050_000\.0, 0\.001\), \(101_000_000\.0, 1\.0\)\)\)',
              'clean_book = dataclasses.replace(clean_book, asks=((100_050_000.0, 0.001), (101_000_000.0, 1.0)))', text)

with open('tests/test_risk_engine.py', 'w') as f:
    f.write(text)
