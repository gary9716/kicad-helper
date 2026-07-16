#!/usr/bin/env python3
"""Check JLCPCB stock/price/basic-extended tier for a list of MPNs via jlcsearch.tscircuit.com."""
import json
import sys
import urllib.parse
import urllib.request

API = "https://jlcsearch.tscircuit.com/api/search"


def search(mpn: str) -> list[dict]:
    url = f"{API}?{urllib.parse.urlencode({'q': mpn, 'limit': 10})}"
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.0"})
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)["components"]


def main(parts: list[str]) -> None:
    for mpn in parts:
        print(f"\n== {mpn} ==")
        results = search(mpn)
        if not results:
            print("  no match")
            continue
        for r in results:
            tier = "basic" if r["is_basic"] else "extended"
            print(
                f"  LCSC C{r['lcsc']} {r['mfr']} [{r['package']}] "
                f"{tier} stock={r['stock']} price=${r['price']:.3f}"
            )


if __name__ == "__main__":
    parts = sys.argv[1:] or ["DRV5055", "STM32G0B1", "TCAN332"]
    main(parts)
