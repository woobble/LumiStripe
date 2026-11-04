"""Export the FastAPI REST contract for rollback and differential checks."""

from __future__ import annotations

import json
import sys

from lumistripe_web.app import create_app


def main() -> None:
    json.dump(create_app().openapi(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
