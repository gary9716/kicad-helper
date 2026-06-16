import os
import tempfile
import unittest
from kicad_skill.wire_complexity import _parse_schematic, _collect_wires, _collect_labels

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
