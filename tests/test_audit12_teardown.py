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



def test_worker_tests_can_run_before_widget_tests():
    # PySide returns the existing singleton even through QApplication.instance().
    # A core-only singleton makes subsequent widget construction abort the interpreter.
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "tests/test_worker.py::test_worker_waits_for_folder_choice",
         "tests/test_audit_fable_fixes.py::test_the_photos_database_of_one_apple_id_is_never_used_for_another",
         "tests/test_notes_window.py::test_window_lists_reads_and_searches"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
