import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_service_section_validates(self):
        config = {
            "service": {"port": 9999, "bind": "0.0.0.0", "workers": 1, "threads": 5},
            "demo": [{"action": "hello", "run": "echo hi"}],
        }
        pipeline.validate_config(config)

    def test_service_section_rejects_bad_port(self):
        config = {"service": {"port": "many", "bind": "0.0.0.0"}}
        with self.assertRaises(pipeline.PipelineError):
            pipeline.validate_config(config)

    def test_service_settings_reads_overrides_and_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            conf = Path(td) / "pida.yaml"
            conf.write_text("service:\n  port: 8080\n  threads: 9\n", encoding="utf-8")
            with mock.patch.object(pipeline, "COMMANDS", conf):
                svc = pipeline.service_settings()
        self.assertEqual(svc["port"], 8080)
        self.assertEqual(svc["threads"], 9)
        self.assertEqual(svc["workers"], 1)
        self.assertEqual(svc["bind"], "0.0.0.0")

    def test_service_settings_missing_file_uses_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            conf = Path(td) / "missing.yaml"
            with mock.patch.object(pipeline, "COMMANDS", conf):
                svc = pipeline.service_settings()
        self.assertEqual(svc, dict(pipeline.SERVICE_DEFAULTS))

    def test_load_skips_service_section(self):
        with tempfile.TemporaryDirectory() as td:
            conf = Path(td) / "pida.yaml"
            conf.write_text(
                "service:\n  port: 9999\n"
                "demo:\n- action: hello\n  run: echo hi\n",
                encoding="utf-8",
            )
            with mock.patch.object(pipeline, "COMMANDS", conf):
                cfg = pipeline.load()
        self.assertIn("demo/hello", cfg["actions"])
        self.assertNotIn("service/port", cfg["actions"])


if __name__ == "__main__":
    unittest.main()
