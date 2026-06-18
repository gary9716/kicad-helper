import unittest
import os
import tempfile
import sys
from kicad_skill.parser import parse_sexpr, format_sexpr
from kicad_skill.module import create_module_from_components
from kicad_skill.evaluate_layout import evaluate_schematic_layout

class TestModuleCreation(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.sch_path = os.path.join(self.test_dir.name, "test_project.kicad_sch")
        self.table_path = os.path.join(self.test_dir.name, "sym-lib-table")
        
        # Create a minimal sym-lib-table file
        with open(self.table_path, 'w', encoding='utf-8') as f:
            f.write('(sym_lib_table)\n')

        # Create a mock parent schematic content
        self.mock_sch_content = """(kicad_sch
  (version 20211123)
  (generator "eeschema")
  (generator_version "10.0")
  (uuid "parent-uuid-1234")
  (paper "A4")
  (lib_symbols
    (symbol "local_test:STM32_DEMO"
      (pin_names (offset 1.016))
      (property "Reference" "U" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (property "Value" "STM32_DEMO" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (symbol "STM32_DEMO_1_1"
        (pin power_in line (at -15.24 -2.54 0) (length 2.54)
          (name "VCC" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27))))
        )
        (pin power_in line (at -15.24 2.54 0) (length 2.54)
          (name "GND" (effects (font (size 1.27 1.27))))
          (number "2" (effects (font (size 1.27 1.27))))
        )
        (pin output line (at 15.24 0 180) (length 2.54)
          (name "TXD" (effects (font (size 1.27 1.27))))
          (number "3" (effects (font (size 1.27 1.27))))
        )
      )
    )
  )
  (symbol (lib_id "local_test:STM32_DEMO") (at 100 100 0)
    (property "Reference" "U101" (at 100 105 0))
    (property "Value" "STM32_DEMO" (at 100 110 0))
  )
  (symbol (lib_id "local_test:STM32_DEMO") (at 150 100 0)
    (property "Reference" "U102" (at 150 105 0))
    (property "Value" "STM32_DEMO" (at 150 110 0))
  )
  (symbol (lib_id "local_test:STM32_DEMO") (at 200 100 0)
    (property "Reference" "U103" (at 200 105 0))
    (property "Value" "STM32_DEMO" (at 200 110 0))
  )
  (wire (pts (xy 84.76 97.46) (xy 134.76 97.46)) (stroke (width 0) (type default)) (uuid "wire-uuid-1"))
  (wire (pts (xy 165.24 100.0) (xy 184.76 97.46)) (stroke (width 0) (type default)) (uuid "wire-uuid-2"))
)
"""
        with open(self.sch_path, 'w', encoding='utf-8') as f:
            f.write(self.mock_sch_content)

    def tearDown(self):
        self.test_dir.cleanup()

    def test_create_module(self):
        components = ["U101", "U102"]
        sheet_file = "sub_sheet.kicad_sch"
        
        # Run create module
        num_pins, num_wires = create_module_from_components(
            schematic_path=self.sch_path,
            table_path=self.table_path,
            components=components,
            module_name="TestModule",
            sheet_file_name=sheet_file
        )
        
        # 1. Assert sheet file is created
        sub_sch_path = os.path.join(self.test_dir.name, sheet_file)
        self.assertTrue(os.path.exists(sub_sch_path))
        
        # 2. Read sub-sheet and verify contents
        with open(sub_sch_path, 'r', encoding='utf-8') as f:
            sub_content = f.read()
        sub_sexpr = parse_sexpr(sub_content)
        
        # Check it has the version and correct root
        self.assertEqual(sub_sexpr[0], 'kicad_sch')
        
        # Verify moved components exist in sub-schematic
        sub_refs = []
        for child in sub_sexpr[1:]:
            if isinstance(child, list) and child[0] == 'symbol':
                for sub in child[1:]:
                    if isinstance(sub, list) and len(sub) > 2 and sub[0] == 'property' and sub[1] == 'Reference':
                        sub_refs.append(sub[2])
        self.assertIn("U101", sub_refs)
        self.assertIn("U102", sub_refs)
        self.assertNotIn("U103", sub_refs)
        
        # 3. Read parent sheet and verify contents
        with open(self.sch_path, 'r', encoding='utf-8') as f:
            parent_content = f.read()
        parent_sexpr = parse_sexpr(parent_content)
        
        parent_refs = []
        has_sheet = False
        sheet_filename = ""
        for child in parent_sexpr[1:]:
            if isinstance(child, list) and len(child) > 0:
                if child[0] == 'symbol':
                    for sub in child[1:]:
                        if isinstance(sub, list) and len(sub) > 2 and sub[0] == 'property' and sub[1] == 'Reference':
                            parent_refs.append(sub[2])
                elif child[0] == 'sheet':
                    has_sheet = True
                    for sub in child[1:]:
                        if isinstance(sub, list) and len(sub) > 2 and sub[0] == 'property' and sub[1] == 'Sheetfile':
                            sheet_filename = sub[2]
                            
        self.assertNotIn("U101", parent_refs)
        self.assertNotIn("U102", parent_refs)
        self.assertIn("U103", parent_refs)
        self.assertTrue(has_sheet)
        self.assertEqual(sheet_filename, sheet_file)
        
        # Verify boundary crossing connections
        self.assertEqual(num_pins, 1) # only U102:TXD connects to outside U103:VCC

    def test_no_shorts_or_dangling_after_module(self):
        """Regression: create-module must not introduce net shorts (overlapping
        different-net wires / through-pin merges) or dangling wires in either the
        parent or the sub-sheet. Guards the routing (blocked_pins/wires) and the
        dangling-wire prune."""
        create_module_from_components(
            schematic_path=self.sch_path,
            table_path=self.table_path,
            components=["U101", "U102"],
            module_name="TestModule",
            sheet_file_name="sub_sheet.kicad_sch",
        )
        for path in (self.sch_path, os.path.join(self.test_dir.name, "sub_sheet.kicad_sch")):
            res = evaluate_schematic_layout(path, self.table_path)
            self.assertEqual(res["shorts"], 0, f"shorts in {os.path.basename(path)}: {res['issues']}")
            self.assertEqual(res["dangling"], 0, f"dangling in {os.path.basename(path)}: {res['issues']}")
            self.assertEqual(res["duplicate_wires"], 0,
                             f"duplicate wires in {os.path.basename(path)}: {res['issues']}")
            self.assertFalse(res["fatal"], f"fatal in {os.path.basename(path)}")

    def test_evaluator_detects_duplicate_wires(self):
        """The evaluator must flag exact-duplicate wire segments and drop below 100, so an
        AI caller cannot mistake a wire-littered schematic for a clean one."""
        dup_path = os.path.join(self.test_dir.name, "dup.kicad_sch")
        with open(dup_path, "w", encoding="utf-8") as f:
            f.write("""(kicad_sch
  (version 20211123) (generator "eeschema") (generator_version "10.0")
  (uuid "u") (paper "A4")
  (wire (pts (xy 100.33 100.33) (xy 110.49 100.33)) (stroke (width 0) (type default)) (uuid "w1"))
  (wire (pts (xy 100.33 100.33) (xy 110.49 100.33)) (stroke (width 0) (type default)) (uuid "w2"))
)
""")
        res = evaluate_schematic_layout(dup_path, self.table_path)
        self.assertEqual(res["duplicate_wires"], 1)
        self.assertLess(res["score"], 100)

if __name__ == "__main__":
    unittest.main()
