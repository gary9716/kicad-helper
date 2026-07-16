---
name: sourcing-smt-parts
description: Use when selecting or checking a JLCPCB-assemblable SMT part, comparing basic/extended parts, verifying an LCSC part number/stock/price before BOM entry, or searching by value+package (resistor/cap/MCU/etc) before calling fetch-easyeda. Triggers on "find a part for", "check JLCPCB stock", "LCSC part number for", "basic vs extended part", "BOM parts sourcing".
---

# Sourcing SMT Parts (JLCPCB/LCSC)

## Overview
Layered lookup for JLCPCB-assemblable SMT parts. Use the free JSON API first; it only returns parts JLCPCB can actually place (basic/extended), which is what matters for a SMT-assembly BOM. Fall back down the list only if it can't answer.

## Layers (try top to bottom)

1. **jlcsearch (tscircuit)** — no key, JSON, JLCPCB-assembly-scoped. Default choice.
   Any list page + `.json` = API: `https://jlcsearch.tscircuit.com/resistors/list.json?resistance=10k&package=0402`, `/capacitors/list.json`, `/microcontrollers/list.json`, or generic `/search?q=<term>`. Params vary per category (resistance/package/mpn/etc) — fetch `list.json` with no params first to see fields, then filter. Returns LCSC part number, package, basic/extended flag, stock.
2. **yaqwsx/jlcparts** — same JLCPCB dataset, browsable/queryable parametric DB (github.com/yaqwsx/jlcparts) when jlcsearch's category coverage is too thin.
3. **LCSC official OpenAPI** (`lcsc.com/docs/openapi`) — full LCSC catalog (not JLCPCB-assembly-scoped), needs an API key (apply via LCSC, B2B-oriented). Keyword search caps at 30 results/page; searching by LCSC part number beats searching by MPN/keyword. Use when the part isn't in JLCPCB's basic/extended set at all (through-hole, exotic, distributor-only).
4. **EasyEDA internal endpoint** (`lcsc.com/api/eda/product/search`) — unofficial, undocumented, breaks without notice. Last resort only.

## Do NOT
Do not call LCSC's internal site endpoints directly for bulk/repeated lookups (`/api/products/search` etc. need CSRF+cookies and have banned scrapers, e.g. Part-DB users got 403'd). High-volume/production needs → apply for the official OpenAPI key instead of scraping.

## After finding the LCSC id
Feed it to this repo's `fetch-easyeda <lcsc_id>` command (see `skills/kicad-helper/SKILL.md` §7) to pull symbol/footprint/3D model and register it in the library.
