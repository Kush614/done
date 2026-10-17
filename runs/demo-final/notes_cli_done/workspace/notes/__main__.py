"""Entry point for `python -m notes`."""

import sys

from notes.cli import main

if __name__ == "__main__":
    sys.exit(main())