"""
tools/build_catalogs.py - adds the texts of tools/tr/*.tsv to translations/<lang>.json.

Every line of a .tsv file: English <TAB> ru <TAB> fr <TAB> es <TAB> ar <TAB> zh <TAB> rw
A key that already exists in a catalog keeps its old translation (pass --replace to overwrite).
Keys with {0}, {1} ... are patterns: the page translator fills the gaps (see i18n.py).
Run:  python tools/build_catalogs.py
"""

import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LANGS = ["ru", "fr", "es", "ar", "zh", "rw"]
REPLACE = "--replace" in sys.argv

rows = []
for path in sorted(glob.glob(os.path.join(HERE, "tr", "*.tsv"))):
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if not line.strip():
                continue
            cols = line.split("\t")
            if len(cols) != 7:
                raise SystemExit(f"{os.path.basename(path)} line {n}: {len(cols)} columns instead of 7")
            rows.append(cols)

for i, lang in enumerate(LANGS, start=1):
    path = os.path.join(ROOT, "translations", f"{lang}.json")
    with open(path, encoding="utf-8") as f:
        catalog = json.load(f)
    added = 0
    for cols in rows:
        key, value = " ".join(cols[0].split()), cols[i].strip()
        if value and (REPLACE or key not in catalog):
            added += key not in catalog
            catalog[key] = value
    with open(path, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=1, sort_keys=True)
    print(f"{lang}: {len(catalog)} texts ({added} new)")
