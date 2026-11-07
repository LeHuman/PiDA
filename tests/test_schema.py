import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pipeline


class PipelineSchemaTests(unittest.TestCase):
    def test_valid_config_matches_schema(self):
        config = {"demo": [{"action": "hello", "run": 'echo "hello"'}]}
        pipeline.validate_config(config)

    def test_action_requires_run_or_needs(self):
        config = {"lights": [{"action": "on"}]}
        with self.assertRaises(pipeline.PipelineError):
            pipeline.validate_config(config)

    def test_numeric_bounds_are_rejected_for_string_params(self):
        config = {
            "lights": [{
                "action": "set",
                "run": "true",
                "params": {"name": {"type": "string", "min": 1}},
            }]
        }
        with self.assertRaises(pipeline.PipelineError):
            pipeline.validate_config(config)


if __name__ == "__main__":
    unittest.main()
