#!/usr/bin/env python3

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InstallTests(unittest.TestCase):
    def test_installed_entrypoints_share_the_stdlib_module(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "prefix"
            install = subprocess.run(
                [sys.executable, str(ROOT / "install.py"), "--prefix", str(prefix)],
                text=True,
                capture_output=True,
            )
            self.assertEqual(install.returncode, 0, install.stderr)
            for name in ("cpen", "cpen-focus", "cpen-save", "cpen-guard", "cpen_cli.py"):
                self.assertTrue((prefix / "bin" / name).is_file())
            help_result = subprocess.run(
                [sys.executable, str(prefix / "bin" / "cpen"), "--help"],
                env={"PATH": str(prefix / "bin")},
                text=True,
                capture_output=True,
            )
            self.assertEqual(help_result.returncode, 0, help_result.stderr)


if __name__ == "__main__":
    unittest.main()
