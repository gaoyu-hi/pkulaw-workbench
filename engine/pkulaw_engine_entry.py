import sys

for stream in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="backslashreplace")

from pkulaw_engine.main import main


if __name__ == "__main__":
    raise SystemExit(main())
