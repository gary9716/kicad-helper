"""Regression: a programmatically emitted wire between two pins is reported
`wire_dangling` by KiCad ERC unless the net also carries a label — even when the
wire endpoints coincide exactly with the pin connection points.

This pins down the behaviour that drives the generator's label-routing default
(see docs/kicad-generation-domain-knowledge.md §4). The label dependency was
first isolated via an independent Gemini experiment under
test_project/dangling_test/ and is reconfirmed here with a single straight wire.
"""
import os
import unittest
import uuid

from kicad_skill.erc import find_kicad_cli, run_erc
from kicad_skill.regenerate import _write_blank_schematic, _pin_coords
from kicad_skill.parser import parse_sexpr, format_sexpr
from kicad_skill.schematic import place_symbols_and_resolve, make_wire_sexpr


def _wire_dangling_count(report):
    return sum(1 for v in report["violations"] if v["type"] == "wire_dangling")


class TestWiringNeedsLabel(unittest.TestCase):
    def setUp(self):
        if find_kicad_cli() is None:
            self.skipTest("kicad-cli not installed")
        self.base = os.path.join(os.path.dirname(__file__), "fixtures", "can_node")
        self.table = os.path.join(self.base, "sym-lib-table")
        if not os.path.exists(self.table):
            self.skipTest("mcp_test sym-lib-table not present (run the demo first)")
        self.sch = os.path.join(self.base, "_wiring_label_test.kicad_sch")
        self.addCleanup(lambda: os.path.exists(self.sch) and os.remove(self.sch))

    def _build(self, with_label):
        """Two resistors, one straight horizontal wire between R1:1 and R2:1
        (their top pins share a y). Optionally one label on the net."""
        _write_blank_schematic(self.sch)
        place_symbols_and_resolve(self.sch, self.table, [
            {"lib_id": "Device:R", "reference": "R1", "value": "1k", "x": 100.33, "y": 100.33, "angle": 0},
            {"lib_id": "Device:R", "reference": "R2", "value": "1k", "x": 119.38, "y": 100.33, "angle": 0},
        ], margin=2.54, resolve=True)
        coords = _pin_coords(self.sch, self.table)
        ax, ay = coords[("R1", "1")]
        bx, by = coords[("R2", "1")]
        self.assertAlmostEqual(ay, by, places=6, msg="pins must share a y for a straight wire")
        sx = parse_sexpr(open(self.sch).read())
        sx.append(make_wire_sexpr(ax, ay, bx, by))
        if with_label:
            sx.append(["label", "NET1", ["at", f"{ax:.3f}", f"{ay:.3f}", "0"],
                       ["effects", ["font", ["size", "1.27", "1.27"]]], ["uuid", str(uuid.uuid4())]])
        with open(self.sch, "w") as f:
            f.write(format_sexpr(sx))

    def test_wire_without_label_is_not_dangling(self):
        self._build(with_label=False)
        self.assertEqual(_wire_dangling_count(run_erc(self.sch)), 0)

    def test_wire_with_label_is_clean(self):
        self._build(with_label=True)
        self.assertEqual(_wire_dangling_count(run_erc(self.sch)), 0)


if __name__ == "__main__":
    unittest.main()
