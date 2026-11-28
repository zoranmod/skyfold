"""A worker-only test process may finish with a QCoreApplication and no widgets."""

import subprocess
import sys
from pathlib import Path


def test_session_teardown_with_only_a_core_application():
    # A full test run already owns a QApplication; only a fresh process exercises this case.
    script = """
from PySide6.QtCore import QCoreApplication
from tests.conftest import pytest_sessionfinish

app = QCoreApplication([])
pytest_sessionfinish(None, 0)
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr

