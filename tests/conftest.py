"""
Shared fixtures for Remote Code tests.
"""

import pytest
import os
import sys

# Ensure head package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
