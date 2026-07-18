"""RecCli command bootstrap with a dependency-light organization path."""

from __future__ import annotations

import sys
from typing import Optional, Sequence


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "organization":
        from .organization_cli import main as organization_main

        return organization_main(arguments[1:])

    from .runtime.cli import main as legacy_main

    if argv is None:
        return legacy_main()

    original = sys.argv
    try:
        sys.argv = [original[0], *arguments]
        return legacy_main()
    finally:
        sys.argv = original


if __name__ == "__main__":
    raise SystemExit(main())
