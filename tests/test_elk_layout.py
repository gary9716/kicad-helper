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

    def test_synth_name_never_collides_with_existing_label(self):
        nets = [{"U2:1", "U3:1"}]
        pin_positions = {"U2:1": (0.0, 0.0), "U3:1": (2.54, 0.0)}
        labels_at = {(99.0, 99.0): "NET_U2_1"}  # existing label with the synth name
        named = name_nets(nets, pin_positions, labels_at)
        self.assertNotEqual(named[0][0], "NET_U2_1")


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


import json as _json
from unittest import mock

from kicad_skill.elk_layout import run_elk


class TestRunElk(unittest.TestCase):
    @mock.patch("kicad_skill.elk_layout.subprocess.run")
    def test_pipes_graph_json_through_node_runner(self, mock_run):
        graph = {"id": "root", "children": []}
        mock_run.return_value = mock.Mock(stdout=_json.dumps({"id": "root", "x": 0}))
        result = run_elk(graph)
        self.assertEqual(result["id"], "root")
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[0], "node")
        self.assertTrue(cmd[1].endswith(os.path.join("tools", "elk_runner.js")))
        kwargs = mock_run.call_args[1]
        self.assertEqual(_json.loads(kwargs["input"]), graph)
        self.assertTrue(kwargs["check"])
        self.assertTrue(kwargs["capture_output"])
        self.assertTrue(kwargs["text"])


from kicad_skill.elk_layout import snap_deltas, derive_wires


class TestSnapAndDerive(unittest.TestCase):
    def assert_on_grid(self, v, msg=None):
        # Float modulo artifact: an on-grid value like 91.44 % 1.27 can return
        # ~1.2699999 instead of 0. Accept residues near either end of [0, 1.27).
        r = v % 1.27
        self.assertLess(min(r, 1.27 - r), 1e-6, msg or f"{v} off grid")

    def test_deltas_snapped_to_grid(self):
        _, symbols = load_fixture_symbols()
        # fake ELK result: every node shifted to a float origin
        layouted = {"children": [
            {"id": s["ref"], "x": 3.1, "y": 7.9} for s in symbols
        ]}
        deltas = snap_deltas(layouted, symbols)
        for ref, (dx, dy) in deltas.items():
            self.assert_on_grid(dx, msg=f"{ref} dx {dx} off grid")
            self.assert_on_grid(dy)

    def test_derive_wires_endpoints_are_exact_pins_and_orthogonal(self):
        # one edge U2:1 -> U3:1 with a float ELK route
        moved_pins = {"U2:1": (50.8, 30.48), "U3:1": (91.44, 40.64)}
        elk_edges = [{
            "id": "e0_TXCAN",
            "sources": ["U2:1"], "targets": ["U3:1"],
            "sections": [{
                "startPoint": {"x": 50.79, "y": 30.5},
                "endPoint": {"x": 91.4, "y": 40.6},
                "bendPoints": [{"x": 70.1, "y": 30.5}, {"x": 70.1, "y": 40.6}],
            }],
        }]
        wires = derive_wires(elk_edges, moved_pins)
        # flatten all segments
        pts = set()
        for seg in wires:
            (x1, y1), (x2, y2) = seg
            pts.add((x1, y1)); pts.add((x2, y2))
            # orthogonal
            self.assertTrue(x1 == x2 or y1 == y2, f"diagonal segment {seg}")
            # on grid
            for v in (x1, y1, x2, y2):
                self.assert_on_grid(v)
        # exact pin endpoints present
        self.assertIn((50.8, 30.48), pts)
        self.assertIn((91.44, 40.64), pts)

    def test_snap_breaking_orthogonality_inserts_jog(self):
        # start/end pins whose snap forces a non-collinear join
        moved_pins = {"A:1": (0.0, 0.0), "B:1": (5.08, 2.54)}
        elk_edges = [{
            "id": "e0_X",
            "sources": ["A:1"], "targets": ["B:1"],
            "sections": [{
                "startPoint": {"x": 0.0, "y": 0.0},
                "endPoint": {"x": 5.0, "y": 2.5},
                "bendPoints": [],
            }],
        }]
        wires = derive_wires(elk_edges, moved_pins)
        for (x1, y1), (x2, y2) in wires:
            self.assertTrue(x1 == x2 or y1 == y2)
        # path connects A to B
        pts = {p for seg in wires for p in seg}
        self.assertIn((0.0, 0.0), pts)
        self.assertIn((5.08, 2.54), pts)


import shutil
import tempfile

from kicad_skill.elk_layout import elk_layout_schematic


class TestElkLayoutSchematic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        for fname in os.listdir(FIXTURE):
            shutil.copy(os.path.join(FIXTURE, fname), self.tmp)
        self.sch = os.path.join(self.tmp, "mcp_test.kicad_sch")
        self.table = os.path.join(self.tmp, "sym-lib-table")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    @mock.patch("kicad_skill.elk_layout.run_elk")
    def test_identity_layout_preserves_connectivity(self, mock_elk):
        """ELK mocked to return every node at its CURRENT origin with no edge
        sections: symbols must not move; edge-nets (with no routes returned)
        fall back to labels; connectivity must survive exactly."""
        from kicad_skill.netlist_eval import extract_actual_netlist

        before = {frozenset(n) for n in
                  extract_actual_netlist(self.sch, self.table) if len(n) >= 2}

        def fake_run(graph):
            # echo each node at its current origin (bbox min corner), no routes
            _, symbols = load_fixture_symbols()
            by_ref = {s["ref"]: s for s in symbols}
            for c in graph["children"]:
                b = by_ref[c["id"]]["bbox"]
                c["x"], c["y"] = b.xmin, b.ymin
            for e in graph["edges"]:
                e["sections"] = []
            return graph
        mock_elk.side_effect = fake_run

        report = elk_layout_schematic(self.sch, self.table)
        self.assertTrue(report["ok"], report)

        after = {frozenset(n) for n in
                 extract_actual_netlist(self.sch, self.table) if len(n) >= 2}
        self.assertEqual(before, after)

    @mock.patch("kicad_skill.elk_layout.run_elk")
    def test_uniform_shift_preserves_connectivity(self, mock_elk):
        """Nonzero deltas: pins must move with symbols (regression for the
        stale-moved_pins bug — labels written at pre-move coords)."""
        from kicad_skill.netlist_eval import extract_actual_netlist

        before = {frozenset(n) for n in
                  extract_actual_netlist(self.sch, self.table) if len(n) >= 2}

        def fake_run(graph):
            _, symbols = load_fixture_symbols()
            by_ref = {s["ref"]: s for s in symbols}
            for c in graph["children"]:
                b = by_ref[c["id"]]["bbox"]
                c["x"], c["y"] = b.xmin + 12.7, b.ymin + 12.7
            for e in graph["edges"]:
                e["sections"] = []
            return graph
        mock_elk.side_effect = fake_run

        report = elk_layout_schematic(self.sch, self.table)
        self.assertTrue(report["ok"], report)

        after = {frozenset(n) for n in
                 extract_actual_netlist(self.sch, self.table) if len(n) >= 2}
        self.assertEqual(before, after)


class TestRegenerateElkMode(unittest.TestCase):
    @mock.patch("kicad_skill.elk_layout.run_elk")
    def test_regenerate_with_elk_routing(self, mock_elk):
        from kicad_skill.regenerate import regenerate_schematic

        def spread_nodes(graph):
            # Distinct, widely separated positions: stacking every node at
            # the origin would make pins coincide and short the netlist.
            for i, c in enumerate(graph["children"]):
                c.setdefault("x", i * 200.0)
                c.setdefault("y", 0.0)
            for e in graph["edges"]:
                e["sections"] = []
            return graph
        mock_elk.side_effect = spread_nodes

        tmp = tempfile.mkdtemp()
        try:
            for fname in os.listdir(FIXTURE):
                shutil.copy(os.path.join(FIXTURE, fname), tmp)
            out = os.path.join(tmp, "regen_elk.kicad_sch")
            gt = os.path.join(tmp, "can_node.groundtruth.json")
            table = os.path.join(tmp, "sym-lib-table")
            out_sch, rep = regenerate_schematic(gt, table, out,
                                                use_erc=False, routing="elk")
            self.assertFalse(rep["fatal"], rep)
            self.assertTrue(mock_elk.called)
        finally:
            shutil.rmtree(tmp)


import shutil as _shutil

_TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
_HAS_ELKJS = (_shutil.which("node") is not None
              and os.path.isdir(os.path.join(_TOOLS, "node_modules", "elkjs")))


@unittest.skipUnless(_HAS_ELKJS, "node + tools/node_modules/elkjs required (npm install --prefix tools/)")
class TestElkIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        for fname in os.listdir(FIXTURE):
            shutil.copy(os.path.join(FIXTURE, fname), self.tmp)
        self.sch = os.path.join(self.tmp, "mcp_test.kicad_sch")
        self.table = os.path.join(self.tmp, "sym-lib-table")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_full_gate_on_can_node(self):
        from kicad_skill.elk_layout import elk_layout_schematic
        from kicad_skill.erc import find_kicad_cli, run_erc
        from kicad_skill.evaluate_layout import evaluate_schematic_layout

        # baseline score of the fixture as-is (current-pipeline output)
        baseline = evaluate_schematic_layout(self.sch, self.table)

        rep = elk_layout_schematic(self.sch, self.table)

        # Gate 1: connectivity preserved
        self.assertTrue(rep["ok"], rep)

        # Gate 2: ERC clean of LAYOUT-class violations (when kicad-cli present).
        # The can_node fixture is deliberately shorted (VDD/GND merged), so it
        # reports pre-existing electrical errors (e.g. pin_to_pin power-output
        # conflicts) that predate layout. Only violations the layout step could
        # have introduced gate here.
        if find_kicad_cli():
            erc = run_erc(self.sch)
            layout_classes = {"wire_dangling", "label_dangling", "pin_not_connected"}
            layout_violations = [v for v in erc["violations"]
                                 if v.get("type") in layout_classes]
            self.assertEqual(layout_violations, [])

        # Gate 3: quality — not worse than the input layout
        after = evaluate_schematic_layout(self.sch, self.table)
        self.assertFalse(after["fatal"], after)
        self.assertGreaterEqual(after["score"], baseline["score"],
                                f"ELK {after['score']} < baseline {baseline['score']}")


if __name__ == "__main__":
    unittest.main()
