#!/usr/bin/env python3
"""Run the whole test suite and say `OK`, or say what went wrong.

Almost every run is a pass, and the interesting question is only whether it
was. So: one line `OK (<n> tests)` on success, and on a failure the failing
tests with their output, nothing else. The private-data check runs inside the
suite (tests/test_privacy_check.py), so this is the complete gate before a
commit.

    python scripts/run-tests.py                # OK, or the failures
    python scripts/run-tests.py -v             # the usual unittest output as well
    python scripts/run-tests.py test_config    # one module, same reporting

Exit status is 0 on success and 1 otherwise, so it can gate a commit.
"""

from __future__ import annotations

import io
import sys
import unittest
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str]) -> int:
    verbose = "-v" in argv or "--verbose" in argv
    modules = [a for a in argv if not a.startswith("-")]
    sys.path.insert(0, str(ROOT))
    # The fakes open sockets and sqlite connections per test; their warnings
    # are noise here and say nothing about whether the code works.
    warnings.simplefilter("ignore", ResourceWarning)

    stream = sys.stderr if verbose else io.StringIO()
    loader = unittest.defaultTestLoader
    if modules:
        suite = loader.loadTestsFromNames([f"tests.{m}" for m in modules])
    else:
        suite = loader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
    result = unittest.TextTestRunner(stream=stream, verbosity=2 if verbose
                                     else 1).run(suite)

    if result.wasSuccessful():
        print(f"OK ({result.testsRun} tests)")
        return 0

    for label, cases in (("FAIL", result.failures), ("ERROR", result.errors)):
        for test, output in cases:
            print(f"{label}: {test}\n{output}")
    print(f"{len(result.failures)} failure(s), {len(result.errors)} error(s) "
          f"in {result.testsRun} tests")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
