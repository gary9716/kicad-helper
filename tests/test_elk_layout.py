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


from kicad_skill.elk_layout import name_nets


class TestNameNets(unittest.TestCase):
    def test_label_name_wins_else_synthesized(self):
        nets = [{"U1:5", "U2:18"}, {"U2:1", "U3:1"}]
        # pin_positions: pin id -> (x, y)
        pin_positions = {
            "U1:5": (10.16, 20.32), "U2:18": (50.8, 20.32),
            "U2:1": (50.8, 30.48), "U3:1": (90.17, 30.48),
        }
        # labels_at: (x, y) -> name  (from existing label/global_label sexprs)
        labels_at = {(10.16, 20.32): "VDD"}
        named = name_nets(nets, pin_positions, labels_at)
        by_pins = {frozenset(p): n for n, p in named}
        self.assertEqual(by_pins[frozenset({"U1:5", "U2:18"})], "VDD")
        synth = by_pins[frozenset({"U2:1", "U3:1"})]
        self.assertTrue(synth.startswith("NET_"))


from kicad_skill.elk_layout import build_elk_graph


class TestBuildElkGraph(unittest.TestCase):
    def test_graph_structure_from_fixture(self):
        _, symbols = load_fixture_symbols()
        edge_nets = [("TXCAN", {"U2:1", "U3:1"})]
        graph = build_elk_graph(symbols, edge_nets)

        self.assertEqual(graph["layoutOptions"]["elk.algorithm"], "layered")
        self.assertEqual(graph["layoutOptions"]["elk.direction"], "RIGHT")
        self.assertEqual(graph["layoutOptions"]["elk.edgeRouting"], "ORTHOGONAL")

        nodes = {n["id"]: n for n in graph["children"]}
        self.assertEqual(set(nodes), {s["ref"] for s in symbols})

        u2 = nodes["U2"]
        self.assertEqual(u2["layoutOptions"]["elk.portConstraints"], "FIXED_POS")
        self.assertGreater(u2["width"], 0)
        self.assertGreater(u2["height"], 0)
        ports = {p["id"]: p for p in u2["ports"]}
        self.assertIn("U2:1", ports)
        p = ports["U2:1"]
        # port coords are relative to node origin (bbox min corner), inside node
        self.assertGreaterEqual(p["x"], 0)
        self.assertLessEqual(p["x"], u2["width"])
        self.assertIn(p["layoutOptions"]["elk.port.side"],
                      ("WEST", "EAST", "NORTH", "SOUTH"))

        self.assertEqual(len(graph["edges"]), 1)
        e = graph["edges"][0]
        self.assertEqual(set(e["sources"] + e["targets"]), {"U2:1", "U3:1"})

    def test_port_side_matches_pin_position(self):
        _, symbols = load_fixture_symbols()
        graph = build_elk_graph(symbols, [])
        by_ref = {s["ref"]: s for s in symbols}
        for node in graph["children"]:
            sym = by_ref[node["id"]]
            b = sym["bbox"]
            for port in node["ports"]:
                pin = next(p for p in sym["pins"]
                           if f'{sym["ref"]}:{p["number"]}' == port["id"])
                dists = {
                    "WEST": pin["x"] - b.xmin, "EAST": b.xmax - pin["x"],
                    "NORTH": pin["y"] - b.ymin, "SOUTH": b.ymax - pin["y"],
                }
                self.assertEqual(port["layoutOptions"]["elk.port.side"],
                                 min(dists, key=dists.get))


if __name__ == "__main__":
    unittest.main()
