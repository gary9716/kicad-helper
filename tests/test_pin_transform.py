import unittest

from kicad_skill.schematic import transform_pin_coordinate


class TestPinTransformYFlip(unittest.TestCase):
    """Symbol-library pin coords are Y-up; the schematic canvas is Y-down.

    KiCad applies an inherent vertical flip when instantiating a symbol, so a
    pin one unit *above* the symbol origin in the library lands one unit *below*
    the instance origin on the schematic. Without this flip, every pin (and any
    label/wire placed at it) lands mirrored about the symbol origin and KiCad
    ERC reports pins/labels unconnected. Verified against `kicad-cli sch erc`
    on the MCP2515 demo (18 pin/label errors -> 0 after the flip).
    """

    def test_pin_above_origin_lands_below_instance(self):
        # Library pin at local +Y, no rotation/mirror.
        gx, gy = transform_pin_coordinate(0.0, 3.81, 100.0, 100.0, 0.0)
        self.assertAlmostEqual(gx, 100.0, places=3)
        self.assertAlmostEqual(gy, 96.19, places=3)  # 100 - 3.81, not 100 + 3.81

    def test_matches_kicad_erc_pin_position(self):
        # U2 (MCP2515) instance origin (124.46, 100.33); pin 16 [CS] library at
        # local (-15.24, -5.08); KiCad ERC reports the pin at (109.22, 105.41).
        gx, gy = transform_pin_coordinate(-15.24, -5.08, 124.46, 100.33, 0.0)
        self.assertAlmostEqual(gx, 109.22, places=2)
        self.assertAlmostEqual(gy, 105.41, places=2)


if __name__ == "__main__":
    unittest.main()
