"""
tools/security_check.py - version 7: the security checks before a release.

    python tools/security_check.py

1. bandit (a static checker for Python) over the code, without the tests. Medium and high findings fail the check.
   B608 ("SQL built from strings") is left out on purpose: every value goes to the database as a parameter (?);
   only table and column names from constants in the code are put into the text of a query. Each remaining
   "nosec" in the code says why it is safe (for example "a fixed https address").
2. pip-audit (the Python packaging authority's checker) of the packages in requirements.txt against the
   vulnerability database of PyPI. It needs the internet; without it the check says "skipped".

pip install bandit pip-audit
"""

import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_bandit():
    if not shutil.which("bandit"):
        print("bandit: not installed (pip install bandit)")
        return None
    r = subprocess.run(["bandit", "-q", "-r", ".", "-x", "./tests,./tools/tr", "-ll", "-s", "B608", "-f", "json"],
                       cwd=ROOT, capture_output=True, text=True)
    data = json.loads(r.stdout or "{}")
    found = data.get("results", [])
    for f in found:
        print(f"  {f['issue_severity']:6} {f['test_id']} {f['filename']}:{f['line_number']} {f['issue_text']}")
    print(f"bandit: {len(found)} medium or high findings")
    return not found


def run_pip_audit():
    if not shutil.which("pip-audit"):
        print("pip-audit: not installed (pip install pip-audit)")
        return None
    r = subprocess.run(["pip-audit", "-r", "requirements.txt", "--progress-spinner", "off"], cwd=ROOT,
                       capture_output=True, text=True, timeout=600)
    text = (r.stdout + r.stderr).strip()
    if "ConnectionError" in text or "Failed to" in text:
        print("pip-audit: skipped (no internet connection to PyPI)")
        return None
    print(text.splitlines()[-1] if text else "pip-audit: no output")
    return r.returncode == 0


def main():
    results = [run_bandit(), run_pip_audit()]
    return 1 if any(x is False for x in results) else 0


if __name__ == "__main__":
    sys.exit(main())
