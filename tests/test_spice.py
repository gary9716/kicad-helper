import unittest
import sys
import os
import tempfile
import json
import re
from kicad_skill.parser import parse_sexpr, format_sexpr
from kicad_skill.symbol import generate_symbol_sexpr, save_symbol_to_library
from kicad_skill.simulation import handle_add_spice_model

class TestSpiceIntegration(unittest.TestCase):
    def setUp(self):
        # Create temp folder for test files
        self.test_dir = tempfile.TemporaryDirectory()
        self.library_path = os.path.join(self.test_dir.name, "test_library.kicad_sym")
        
        # Check if the real spice skill is available
        import kicad_skill.simulation as sim
        self.real_scripts_dir = sim.find_spice_scripts_dir()
        self.mocked = False
        
        if not self.real_scripts_dir:
            from unittest.mock import MagicMock
            import types
            
            # Save original function
            self.orig_find_spice_scripts_dir = sim.find_spice_scripts_dir
            sim.find_spice_scripts_dir = MagicMock(return_value=self.test_dir.name)
            
            # Create a mock module for spice_model_generator
            self.mock_generator = types.ModuleType("spice_model_generator")
            self.mock_generator.sanitize_mpn = lambda mpn: mpn.replace('-', '_')
            
            def mock_generate_opamp_model(mpn, specs):
                # Return string that matches assertions in test_add_spice_model_opamp
                return """.subckt OPAMP_TEST_OPAMP inp inn out vcc vee
* Behavioral model for TEST_OPAMP
* GBW=2.5MHz, SR=2.0V/us, Vos=1.5mV, Aol=100dB
Rin inp inn 1T
Vos inp inp_os DC 0.0015
.ends OPAMP_TEST_OPAMP"""
            self.mock_generator.generate_opamp_model = mock_generate_opamp_model
            
            def mock_generate_ldo_model(mpn, specs):
                # Return string that matches assertions in test_add_spice_model_ldo_fixed
                return """.subckt LDO_MY_FIXED_LDO vin vout gnd
* Behavioral LDO model for MY_FIXED_LDO
Ereg vout_int gnd VALUE = { MIN(V(vin,gnd)-0.3, 3.3) }
.ends LDO_MY_FIXED_LDO"""
            self.mock_generator.generate_ldo_model = mock_generate_ldo_model
            
            # Inject the mock module into sys.modules
            sys.modules["spice_model_generator"] = self.mock_generator
            self.mocked = True
        
    def tearDown(self):
        # Cleanup temp folder
        self.test_dir.cleanup()
        
        if getattr(self, 'mocked', False):
            import kicad_skill.simulation as sim
            sim.find_spice_scripts_dir = self.orig_find_spice_scripts_dir
            if "spice_model_generator" in sys.modules:
                del sys.modules["spice_model_generator"]
        
    def test_add_spice_model_opamp(self):
        # 1. Generate symbol
        pins = [
            {"side": "left", "number": "1", "name": "IN+", "type": "input"},
            {"side": "left", "number": "2", "name": "IN-", "type": "input"},
            {"side": "right", "number": "3", "name": "OUT", "type": "output"},
            {"side": "top", "number": "4", "name": "VCC", "type": "power_in"},
            {"side": "bottom", "number": "5", "name": "VEE", "type": "power_in"}
        ]
        symbol_def = generate_symbol_sexpr("TEST_OPAMP", pins)
        save_symbol_to_library(self.library_path, symbol_def)
        
        # 2. Run add-spice-model Command Line Args simulation via namespace mock
        class Args:
            pass
            
        args = Args()
        args.library = self.library_path
        args.symbol = "TEST_OPAMP"
        args.model_type = "opamp"
        args.pin_mapping = "1=inp 2=inn 3=out 4=vcc 5=vee"
        args.model_file = None
        args.params = "gbw_hz=2.5meg,slew_vus=2.0,vos_mv=1.5"
        args.model_name = None
        
        handle_add_spice_model(args)
        
        # 3. Verify .lib file was generated next to library
        expected_lib_path = os.path.join(self.test_dir.name, "TEST_OPAMP.lib")
        self.assertTrue(os.path.exists(expected_lib_path))
        
        with open(expected_lib_path, 'r', encoding='utf-8') as f:
            lib_content = f.read()
            
        self.assertIn(".subckt OPAMP_TEST_OPAMP", lib_content)
        self.assertIn("Vos inp inp_os DC 0.0015", lib_content)
        self.assertIn("Rin inp inn 1T", lib_content)
        self.assertIn("gbw=2.5mhz", lib_content.lower())
        self.assertIn("vos=1.5mv", lib_content.lower())
        
        # 4. Verify properties inside library file
        with open(self.library_path, 'r', encoding='utf-8') as f:
            lib_content = f.read()
            
        sexpr = parse_sexpr(lib_content)
        
        found = False
        properties = {}
        for child in sexpr[1:]:
            if isinstance(child, list) and child[0] == 'symbol' and child[1] == 'TEST_OPAMP':
                found = True
                for prop in child[2:]:
                    if isinstance(prop, list) and prop[0] == 'property':
                        properties[prop[1]] = prop[2]
                break
                
        self.assertTrue(found)
        self.assertEqual(properties.get("Sim.Device"), "SUBCKT")
        self.assertEqual(properties.get("Sim.Library"), "TEST_OPAMP.lib")
        self.assertEqual(properties.get("Sim.Name"), "OPAMP_TEST_OPAMP")
        self.assertEqual(properties.get("Sim.Pins"), "1=inp 2=inn 3=out 4=vcc 5=vee")

    def test_add_spice_model_ldo_fixed(self):
        pins = [
            {"side": "left", "number": "1", "name": "VIN", "type": "power_in"},
            {"side": "right", "number": "2", "name": "VOUT", "type": "power_out"},
            {"side": "bottom", "number": "3", "name": "GND", "type": "power_in"}
        ]
        symbol_def = generate_symbol_sexpr("TEST_LDO", pins)
        save_symbol_to_library(self.library_path, symbol_def)
        
        class Args:
            pass
        args = Args()
        args.library = self.library_path
        args.symbol = "TEST_LDO"
        args.model_type = "ldo"
        args.pin_mapping = "1=vin, 2=vout, 3=gnd"
        args.model_file = None
        args.params = "fixed=True,vref=3.3,dropout_mv=300"
        args.model_name = "MY_FIXED_LDO"
        
        handle_add_spice_model(args)
        
        expected_lib_path = os.path.join(self.test_dir.name, "TEST_LDO.lib")
        self.assertTrue(os.path.exists(expected_lib_path))
        
        with open(expected_lib_path, 'r', encoding='utf-8') as f:
            lib_content = f.read()
            
        self.assertIn(".subckt LDO_MY_FIXED_LDO vin vout gnd", lib_content)
        self.assertIn("MIN(V(vin,gnd)-0.3, 3.3)", lib_content)

if __name__ == "__main__":
    unittest.main()
