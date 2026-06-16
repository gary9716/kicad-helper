import os
import tempfile
import unittest
from kicad_skill.wire_complexity import _parse_schematic, _collect_wires, _collect_labels
from kicad_skill.wire_complexity import _collect_pins, _build_net_find, _reconstruct_connections

SCH = """(kicad_sch
  (version 20211123) (generator "eeschema") (generator_version "10.0")
  (uuid "u") (paper "A4")
  (wire (pts (xy 100.33 100.33) (xy 110.49 100.33)) (stroke (width 0) (type default)) (uuid "w1"))
  (label "SIG" (at 110.49 100.33 0) (effects (font (size 1.27 1.27))) (uuid "l1"))
)
"""

class TestParsing(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.sch = os.path.join(self.td.name, "t.kicad_sch")
        with open(self.sch, "w") as f:
            f.write(SCH)

    def tearDown(self):
        self.td.cleanup()

    def test_collect_wires_and_labels(self):
        sx = _parse_schematic(self.sch)
        wires = _collect_wires(sx)
        self.assertEqual(len(wires), 1)
        node, ga, gb = wires[0]
        self.assertEqual(ga, (79, 79))
        self.assertEqual(gb, (87, 79))
        labels = _collect_labels(sx)
        self.assertEqual(len(labels), 1)
        self.assertEqual(labels[0][1], "SIG")
        self.assertEqual(labels[0][2], (87, 79))


SCH2 = """(kicad_sch
  (version 20211123) (generator "eeschema") (generator_version "10.0")
  (uuid "u") (paper "A4")
  (lib_symbols
    (symbol "lib:IC"
      (property "Reference" "IC" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (property "Value" "IC" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (symbol "IC_1_1"
        (pin passive line (at -2.54 0 0) (length 2.54)
          (name "A" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
      )
    )
  )
  (symbol (lib_id "lib:IC") (at 50.8 50.8 0)
    (property "Reference" "U1" (at 50.8 45.72 0)) (property "Value" "IC" (at 50.8 55.88 0)))
  (symbol (lib_id "lib:IC") (at 76.2 60.96 0)
    (property "Reference" "U2" (at 76.2 55.88 0)) (property "Value" "IC" (at 76.2 66.04 0)))
  (wire (pts (xy 48.26 50.8) (xy 48.26 60.96)) (stroke (width 0) (type default)) (uuid "w1"))
  (wire (pts (xy 48.26 60.96) (xy 73.66 60.96)) (stroke (width 0) (type default)) (uuid "w2"))
)
"""

class TestConnections(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.sch = os.path.join(self.td.name, "t.kicad_sch")
        self.table = os.path.join(self.td.name, "sym-lib-table")
        with open(self.table, "w") as f:
            f.write("(sym_lib_table)\n")
        with open(self.sch, "w") as f:
            f.write(SCH2)

    def tearDown(self):
        self.td.cleanup()

    def test_reconstruct_single_connection(self):
        sx = _parse_schematic(self.sch)
        pins = _collect_pins(sx, self.table, self.td.name)
        self.assertEqual(len(pins), 2)
        conns = _reconstruct_connections(sx, pins)
        self.assertEqual(len(conns), 1)
        c = conns[0]
        refs = {f"{c['pin_a']['ref']}:{c['pin_a']['name']}",
                f"{c['pin_b']['ref']}:{c['pin_b']['name']}"}
        self.assertEqual(refs, {"U1:A", "U2:A"})
        self.assertEqual(len(c["wire_nodes"]), 2)
        self.assertEqual(c["path"][0], (38, 40))
        self.assertEqual(c["path"][-1], (58, 48))

    def test_net_find_groups_endpoints(self):
        sx = _parse_schematic(self.sch)
        find = _build_net_find(sx)
        self.assertEqual(find((38, 40)), find((58, 48)))


from kicad_skill.wire_complexity import (
    _path_bends, _path_length, _count_crossings, score_wire_complexity, DEFAULT_WEIGHTS,
)

class TestScoring(unittest.TestCase):
    def test_bends_and_length(self):
        straight = [(0, 0), (5, 0)]
        self.assertEqual(_path_bends(straight), 0)
        self.assertEqual(_path_length(straight), 5)
        ell = [(0, 0), (0, 3), (4, 3)]
        self.assertEqual(_path_bends(ell), 1)
        self.assertEqual(_path_length(ell), 7)

    def test_crossing_perpendicular_different_net(self):
        segs_self = [((0, 0), (10, 0))]
        other_segs = [((5, -5), (5, 5))]
        self.assertEqual(_count_crossings(segs_self, other_segs), 1)

    def test_no_crossing_when_shared_endpoint(self):
        segs_self = [((0, 0), (10, 0))]
        other_segs = [((10, 0), (10, 5))]
        self.assertEqual(_count_crossings(segs_self, other_segs), 0)

class TestScoreMonotonic(unittest.TestCase):
    def test_more_bends_scores_higher(self):
        straight = [(0, 0), (10, 0)]
        zig = [(0, 0), (0, 2), (5, 2), (5, 0), (10, 0)]
        from kicad_skill.wire_complexity import _path_bends, _path_length, DEFAULT_WEIGHTS
        w = DEFAULT_WEIGHTS
        s_straight = w["bends"] * _path_bends(straight) + w["length"] * _path_length(straight)
        s_zig = w["bends"] * _path_bends(zig) + w["length"] * _path_length(zig)
        self.assertGreater(s_zig, s_straight)


from kicad_skill.wire_complexity import simplify_wires, score_wire_complexity
from kicad_skill.evaluate_layout import evaluate_schematic_layout

class TestSimplify(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.sch = os.path.join(self.td.name, "t.kicad_sch")
        self.table = os.path.join(self.td.name, "sym-lib-table")
        with open(self.table, "w") as f:
            f.write("(sym_lib_table)\n")
        with open(self.sch, "w") as f:
            f.write(SCH2)

    def tearDown(self):
        self.td.cleanup()

    def test_convert_lowers_total_and_preserves_net(self):
        before = score_wire_complexity(self.sch, self.table)["total"]
        res = simplify_wires(self.sch, self.table, threshold=0.0)
        self.assertEqual(len(res["converted"]), 1)
        self.assertLess(res["total_after"], before)
        ev = evaluate_schematic_layout(self.sch, self.table)
        self.assertEqual(ev["shorts"], 0)
        self.assertEqual(ev["dangling"], 0)
        from kicad_skill.wire_complexity import _parse_schematic, _collect_labels
        labels = _collect_labels(_parse_schematic(self.sch))
        self.assertEqual(len(labels), 2)
        self.assertEqual(labels[0][1], labels[1][1])

    def test_dry_run_does_not_write(self):
        with open(self.sch) as f:
            original = f.read()
        res = simplify_wires(self.sch, self.table, threshold=0.0, dry_run=True)
        with open(self.sch) as f:
            self.assertEqual(f.read(), original)
        self.assertEqual(len(res["converted"]), 1)


class TestRollback(unittest.TestCase):
    def test_branch_net_not_converted(self):
        td = tempfile.TemporaryDirectory()
        sch = os.path.join(td.name, "t.kicad_sch")
        table = os.path.join(td.name, "sym-lib-table")
        with open(table, "w") as f:
            f.write("(sym_lib_table)\n")
        sch_text = """(kicad_sch
  (version 20211123) (generator "eeschema") (generator_version "10.0")
  (uuid "u") (paper "A4")
  (lib_symbols
    (symbol "lib:P"
      (property "Reference" "P" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (property "Value" "P" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (symbol "P_1_1"
        (pin passive line (at 0 0 0) (length 0)
          (name "K" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
      )
    )
  )
  (symbol (lib_id "lib:P") (at 50.8 50.8 0) (property "Reference" "A" (at 0 0 0)) (property "Value" "P" (at 0 0 0)))
  (symbol (lib_id "lib:P") (at 76.2 50.8 0) (property "Reference" "B" (at 0 0 0)) (property "Value" "P" (at 0 0 0)))
  (symbol (lib_id "lib:P") (at 63.5 63.5 0) (property "Reference" "C" (at 0 0 0)) (property "Value" "P" (at 0 0 0)))
  (wire (pts (xy 50.8 50.8) (xy 63.5 50.8)) (stroke (width 0) (type default)) (uuid "w1"))
  (wire (pts (xy 63.5 50.8) (xy 76.2 50.8)) (stroke (width 0) (type default)) (uuid "w2"))
  (wire (pts (xy 63.5 50.8) (xy 63.5 63.5)) (stroke (width 0) (type default)) (uuid "w3"))
)
"""
        with open(sch, "w") as f:
            f.write(sch_text)
        res = simplify_wires(sch, table, threshold=0.0)
        self.assertEqual(len(res["converted"]), 0)
        with open(sch) as f:
            self.assertIn("(wire", f.read())
        td.cleanup()


class TestEvalMetric(unittest.TestCase):
    def test_evaluate_reports_wire_complexity_total(self):
        td = tempfile.TemporaryDirectory()
        sch = os.path.join(td.name, "t.kicad_sch")
        table = os.path.join(td.name, "sym-lib-table")
        with open(table, "w") as f:
            f.write("(sym_lib_table)\n")
        with open(sch, "w") as f:
            f.write(SCH2)
        from kicad_skill.evaluate_layout import evaluate_schematic_layout
        res = evaluate_schematic_layout(sch, table)
        self.assertIn("wire_complexity_total", res)
        self.assertGreaterEqual(res["wire_complexity_total"], 0.0)
        td.cleanup()
