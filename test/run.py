#!/usr/bin/env python3

from pathlib import Path
import sys
import unittest


root = Path(__file__).resolve().parent
suite = unittest.defaultTestLoader.discover(str(root), pattern="test_*.py")
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
