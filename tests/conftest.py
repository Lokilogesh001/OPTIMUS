"""Pytest root configuration for OPTIMUS test suites."""

import os
import sys

# Ensure repository root is always at the head of sys.path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
