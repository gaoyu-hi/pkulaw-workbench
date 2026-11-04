"""Enable opt-in coverage collection for Python child processes."""

import os


if os.environ.get("COVERAGE_PROCESS_START"):
    import coverage

    coverage.process_startup()
