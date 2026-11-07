"""
tools/add_postcodes.py - version 7: the postal-code directory of 12 more countries.

Source: GeoNames postal codes (CC BY 4.0), as converted to CSV by github.com/zauberware/postal-codes-json-xml-csv
(CC BY 4.0). Run:   python tools/add_postcodes.py <folder with RU.zip, DE.zip, ...>
It writes data/geo/postcode_dir.tsv.gz with: country, postal code, place, state, latitude, longitude
(one line per code; the first place of a code is kept; Great Britain has the outward part only, e.g. "SW1A").
"""

import csv
import gzip
import io
import os
import sys
import zipfile

COUNTRIES = ["RU", "BY", "DE", "FR", "GB", "US", "ES", "IT", "TR", "ZA", "KE"]
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "data", "geo", "postcode_dir.tsv.gz")


def main(folder):
    rows, seen = [], set()
    for cc in COUNTRIES:
        path = os.path.join(folder, f"{cc}.zip")
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.endswith(".csv"))
            text = z.read(name).decode("utf-8")
        n = 0
        for r in csv.DictReader(io.StringIO(text)):
            code = " ".join(r["zipcode"].upper().split())
            key = (cc, code)
            if not code or key in seen:
                continue
            seen.add(key)
            try:
                lat, lng = round(float(r["latitude"]), 4), round(float(r["longitude"]), 4)
            except ValueError:
                continue
            place = r["place"].replace("\t", " ").strip()
            state = (r["state"] or r["province"] or "").replace("\t", " ").strip()
            rows.append(f"{cc}\t{code}\t{place}\t{state}\t{lat}\t{lng}")
            n += 1
        print(cc, n)
    with gzip.open(OUT, "wt", encoding="utf-8", compresslevel=9) as f:
        f.write("\n".join(rows) + "\n")
    print(len(rows), "codes ->", OUT, os.path.getsize(OUT) // 1024, "KB")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
