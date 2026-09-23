"""Conditional font fallback generation."""

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/make-fontconfig.py"
spec = importlib.util.spec_from_file_location("make_fontconfig", SCRIPT)
fontconfig = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fontconfig)


class FallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fonts = self.root / "fonts"
        self.fonts.mkdir()
        self.output = self.root / "new.conf"

    def invoke(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), "--fonts", str(self.fonts),
                               "--out", str(self.output), *args],
                              capture_output=True, text=True, timeout=10)

    def test_accept_fallback_preserves_existing_alias_rules(self):
        result = self.invoke("--alias", "Helvetica=Arial", "--fallback", "Arial Narrow=Arial")
        self.assertEqual(result.returncode, 0, result.stderr)
        root = ET.parse(self.output).getroot()
        fallback, = root.findall("alias")
        self.assertEqual(fallback.findtext("family"), "Arial Narrow")
        self.assertEqual(fallback.findtext("accept/family"), "Arial")
        self.assertIsNone(fallback.find("prefer"))
        alias = [node for node in root.findall("match")
                 if node.findtext("test/string") == "Helvetica"]
        self.assertEqual(len(alias), 1)
        self.assertEqual(alias[0].find("edit").get("mode"), "prepend")
        self.assertEqual(alias[0].findtext("edit/string"), "Arial")

    def test_fallback_names_are_xml_escaped(self):
        result = self.invoke("--fallback", "A & B=C < D")
        self.assertEqual(result.returncode, 0, result.stderr)
        fallback = ET.parse(self.output).getroot().find("alias")
        self.assertEqual(fallback.findtext("family"), "A & B")
        self.assertEqual(fallback.findtext("accept/family"), "C < D")

    def test_invalid_fallback_does_not_write_config_or_cache(self):
        for value in ("Arial", "=Arial", "Arial=", "Arial=arial", "Arial\x01=Other"):
            with self.subTest(value=value):
                result = self.invoke("--fallback", value)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(self.output.exists())
                self.assertFalse(Path(str(self.output) + ".cache").exists())

    def test_duplicate_source_rejected_and_distinct_sources_supported(self):
        with self.assertRaises(ValueError):
            fontconfig.parse_fallbacks(["Missing=Arial", "missing=Times"])
        self.assertEqual(fontconfig.parse_fallbacks(["Missing=Arial", "Other=Times"]),
                         [("Missing", "Arial"), ("Other", "Times")])

    def test_no_fallback_leaves_no_accept_alias(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(ET.parse(self.output).getroot().findall("alias"), [])


if __name__ == "__main__":
    unittest.main()
