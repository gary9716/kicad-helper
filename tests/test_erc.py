import os
import shutil
import unittest

from kicad_skill.erc import find_kicad_cli, run_erc


class TestFindKicadCli(unittest.TestCase):
    def test_returns_path_or_none(self):
        # Either a real executable path, or None when KiCad is not installed.
        p = find_kicad_cli()
        if p is not None:
            self.assertTrue(os.path.exists(p))


class TestRunErc(unittest.TestCase):
    """run_erc shells out to `kicad-cli sch erc --format json` and returns a
    structured report. It is the authoritative gate — KiCad's own engine — that
    replaces the hand-rolled connectivity model.
    """

    def setUp(self):
        if find_kicad_cli() is None:
            self.skipTest("kicad-cli not installed")
        self.base = os.path.join(os.path.dirname(__file__), "fixtures", "can_node")
        self.table = os.path.join(self.base, "sym-lib-table")
        self.gt = os.path.join(self.base, "can_node.groundtruth.json")
        if not os.path.exists(self.gt):
            self.skipTest("mcp_test artifacts not present")

    def test_detects_injected_dangling_wire(self):
        # run_erc must surface a wire whose endpoint touches nothing.
        from kicad_skill.regenerate import regenerate_schematic
        from kicad_skill.parser import parse_sexpr, format_sexpr
        from kicad_skill.schematic import make_wire_sexpr
        out = os.path.join(self.base, "_erc_dangling.kicad_sch")
        self.addCleanup(lambda: os.path.exists(out) and os.remove(out))
        regenerate_schematic(self.gt, self.table, out)
        self.assertEqual(run_erc(out)["error_count"], 0)  # clean to start
        # Append a stray wire into empty space (both ends connect to nothing).
        sx = parse_sexpr(open(out).read())
        sx.append(make_wire_sexpr(10.16, 10.16, 25.4, 10.16))
        with open(out, "w") as f:
            f.write(format_sexpr(sx))
        rep = run_erc(out)
        types = {v["type"] for v in rep["violations"]}
        self.assertIn("wire_dangling", types)
        self.assertTrue(rep["error_count"] >= 1)

    def test_erc_gated_regeneration_is_clean(self):
        # The default ERC-gated regeneration demotes any dangling wire to a label
        # until KiCad ERC reports zero errors.
        from kicad_skill.regenerate import regenerate_schematic
        out = os.path.join(self.base, "_erc_gated.kicad_sch")
        self.addCleanup(lambda: os.path.exists(out) and os.remove(out))
        _, report = regenerate_schematic(self.gt, self.table, out)
        self.assertEqual(report["erc_error_count"], 0, report.get("erc_violations"))
        self.assertEqual(run_erc(out)["error_count"], 0)

    def test_clean_all_label_schematic_has_no_errors(self):
        # All-label routing is electrically clean -> ERC reports zero errors.
        import kicad_skill.regenerate as R
        from kicad_skill.regenerate import regenerate_schematic
        out = os.path.join(self.base, "_erc_clean.kicad_sch")
        self.addCleanup(lambda: os.path.exists(out) and os.remove(out))
        old = R.ADJ_THRESHOLD
        R.ADJ_THRESHOLD = 0.0  # force all nets to label-routing
        try:
            regenerate_schematic(self.gt, self.table, out)
        finally:
            R.ADJ_THRESHOLD = old
        rep = run_erc(out)
        self.assertEqual(rep["error_count"], 0, rep["violations"])


class TestRoutingModes(unittest.TestCase):
    def setUp(self):
        if find_kicad_cli() is None:
            self.skipTest("kicad-cli not installed")
        self.base = os.path.join(os.path.dirname(__file__), "fixtures", "can_node")
        self.table = os.path.join(self.base, "sym-lib-table")
        self.gt = os.path.join(self.base, "can_node.groundtruth.json")
        if not os.path.exists(self.gt):
            self.skipTest("mcp_test artifacts not present")

    def test_all_wire_routing_is_erc_clean(self):
        # Bare wires connect (instances-block fix), so an all-wire flat is clean.
        from kicad_skill.regenerate import regenerate_schematic
        out = os.path.join(self.base, "_route_wires.kicad_sch")
        self.addCleanup(lambda: os.path.exists(out) and os.remove(out))
        _, rep = regenerate_schematic(self.gt, self.table, out, routing="wires")
        self.assertEqual(rep["erc_error_count"], 0, rep.get("erc_violations"))


if __name__ == "__main__":
    unittest.main()
