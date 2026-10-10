# conftest.py (project root)
# Ensures the project root is on sys.path so tests can do
# `from src.phase1_ml... import ...` no matter where pytest is launched from.
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
