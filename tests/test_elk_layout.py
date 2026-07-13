# tests/test_elk_layout.py
import os
import unittest

from kicad_skill.parser import parse_sexpr
from kicad_skill.resolve_layout import _extract_symbols
from kicad_skill.schematic import load_sym_lib_table

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "can_node")
SCH = os.path.join(FIXTURE, "mcp_test.kicad_sch")
TABLE = os.path.join(FIXTURE, "sym-lib-table")


def load_fixture_symbols():
    with open(SCH, encoding="utf-8") as f:
        sch = parse_sexpr(f.read())
    local_defs = {}
    for child in sch[1:]:
        if isinstance(child, list) and child[0] == "lib_symbols":
            for sym in child[1:]:
                if isinstance(sym, list) and sym[0] == "symbol" and len(sym) > 1:
                    local_defs[sym[1]] = sym
    lib_map = load_sym_lib_table(TABLE)
    return sch, _extract_symbols(sch, local_defs, lib_map, FIXTURE)


class TestExtractSymbolsPins(unittest.TestCase):
    def test_symbols_carry_full_pin_dicts(self):
        _, symbols = load_fixture_symbols()
        by_ref = {s["ref"]: s for s in symbols}
        self.assertIn("U2", by_ref)  # MCP2515 (fixture symbol has 11 pins)
        u2 = by_ref["U2"]
        self.assertIn("pins", u2)
        self.assertEqual(len(u2["pins"]), 11)
        p = u2["pins"][0]
        for key in ("number", "name", "type", "x", "y"):
            self.assertIn(key, p)
        # pins agree with the pin_pts the function already exposed
        self.assertEqual(
            sorted((pp["x"], pp["y"]) for pp in u2["pins"]),
            sorted(u2["pin_pts"]),
        )


from kicad_skill.elk_layout import classify_for_elk


class TestClassifyForElk(unittest.TestCase):
    def test_power_fanout_and_singleton_rules(self):
        nets = [
            ("VDD", {"U1:5", "U2:18", "C3:1"}),      # power name -> label
            ("GND_A", {"U1:6", "C3:2"}),              # startswith GND -> label
            ("SPI_SCK", {"U1:4", "U2:13", "U3:1", "J1:2"}),  # fanout 4 -> label
            ("TXCAN", {"U2:1", "U3:1"}),              # 2-pin signal -> edge
            ("OSC1", {"U2:8", "Y1:1", "C1:1"}),       # 3-pin signal -> edge
            ("NC_PIN", {"U2:11"}),                    # singleton -> neither
        ]
        edge_nets, label_nets = classify_for_elk(nets, fanout_threshold=4)
        self.assertEqual({n for n, _ in edge_nets}, {"TXCAN", "OSC1"})
        self.assertEqual({n for n, _ in label_nets}, {"VDD", "GND_A", "SPI_SCK"})


if __name__ == "__main__":
    unittest.main()
