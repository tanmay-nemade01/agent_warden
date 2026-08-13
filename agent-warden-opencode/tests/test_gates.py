"""Gate PASS/FAIL parsing."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from app.gates import dump


class FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class GateParseTests(unittest.TestCase):
    @patch("app.gates.subprocess.run")
    def test_pass_when_exit_zero_no_fail(self, run):
        run.return_value = FakeProc(stdout="[PASS] ok\n", returncode=0)
        from app import gates
        res = gates._run(["python", "lint.py", "x"])
        self.assertTrue(res.passed)
        self.assertEqual(res.status, "PASS")
        self.assertTrue(any(f.startswith("PASS:") for f in res.findings))

    @patch("app.gates.subprocess.run")
    def test_fail_on_marker_even_if_exit_zero(self, run):
        run.return_value = FakeProc(
            stdout="[FAIL] leftover *[verify]*\n", returncode=0)
        from app import gates
        res = gates._run(["python", "lint.py", "x"])
        self.assertFalse(res.passed)
        self.assertEqual(res.status, "FAIL")

    @patch("app.gates.subprocess.run")
    def test_fail_on_nonzero_exit(self, run):
        run.return_value = FakeProc(stdout="boom", returncode=2)
        from app import gates
        res = gates._run(["python", "lint.py", "x"])
        self.assertFalse(res.passed)
        dumped = dump(res)
        self.assertEqual(dumped["status"], "FAIL")
        self.assertEqual(dumped["exit_code"], 2)


if __name__ == "__main__":
    unittest.main()
