import unittest
import sys
import os

# Ensure package path is visible
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kicad_skill.schematic import BoundingBox

class TestBoundingBox(unittest.TestCase):
    def test_default_box(self):
        box = BoundingBox()
        self.assertFalse(box.is_valid())
        self.assertEqual(box.width, 0)
        self.assertEqual(box.height, 0)

    def test_update_point(self):
        box = BoundingBox()
        box.update_point(10, 20)
        self.assertTrue(box.is_valid())
        self.assertEqual(box.xmin, 10)
        self.assertEqual(box.ymin, 20)
        self.assertEqual(box.xmax, 10)
        self.assertEqual(box.ymax, 20)

        box.update_point(5, 30)
        self.assertEqual(box.xmin, 5)
        self.assertEqual(box.ymin, 20)
        self.assertEqual(box.xmax, 10)
        self.assertEqual(box.ymax, 30)

    def test_width_height_center(self):
        box = BoundingBox(xmin=5, ymin=10, xmax=15, ymax=30)
        self.assertTrue(box.is_valid())
        self.assertEqual(box.width, 10)
        self.assertEqual(box.height, 20)
        self.assertEqual(box.center, (10.0, 20.0))

    def test_update_box(self):
        box1 = BoundingBox(xmin=0, ymin=0, xmax=10, ymax=10)
        box2 = BoundingBox(xmin=-5, ymin=5, xmax=5, ymax=15)
        box1.update_box(box2)
        
        self.assertEqual(box1.xmin, -5)
        self.assertEqual(box1.ymin, 0)
        self.assertEqual(box1.xmax, 10)
        self.assertEqual(box1.ymax, 15)

    def test_pad(self):
        box = BoundingBox(xmin=0, ymin=0, xmax=10, ymax=10)
        padded = box.pad(2.5)
        self.assertEqual(padded.xmin, -2.5)
        self.assertEqual(padded.ymin, -2.5)
        self.assertEqual(padded.xmax, 12.5)
        self.assertEqual(padded.ymax, 12.5)

if __name__ == "__main__":
    unittest.main()
