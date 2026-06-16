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
