"""Preloaded independent actor; the real production erasure begins only at the barrier."""

import asyncio
import json
import sys

from sqlalchemy.exc import SQLAlchemyError

from task_platform.demo_retention import TerminalEvidence, erase_demo_content
from task_platform.provider_commands import read_json_file


def main() -> None:
    try:
        value = read_json_file(sys.argv[1])
        TerminalEvidence.model_validate_json(json.dumps(value))
        print("ready", flush=True)
        if sys.stdin.readline() != "run\n":
            raise SystemExit(1)
        result = asyncio.run(erase_demo_content(value))
    except (ValueError, OSError, SQLAlchemyError) as error:
        print("actor.failed:" + type(error).__name__, file=sys.stderr, flush=True)
        raise SystemExit(1) from None
    print(json.dumps({"purged": result}), flush=True)


if __name__ == "__main__":
    main()
