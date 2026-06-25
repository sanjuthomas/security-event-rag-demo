from __future__ import annotations

import argparse
import sys

from harness.actions import (
    approve_instructions as approve_action,
    create_instructions as create_action,
    reject_instructions as reject_action,
    run_policy_scenario,
    submit_instructions as submit_action,
)
from harness.config import Settings
from harness.results import HarnessActionResult


def _print_result(result: HarnessActionResult) -> int:
    for line in result.logs:
        print(line)
    return 0 if result.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Instruction lifecycle test harness")
    parser.add_argument(
        "--seed-instructions",
        type=int,
        metavar="N",
        help="create N draft instructions",
    )
    parser.add_argument(
        "--submit-instructions",
        type=int,
        metavar="N",
        help="submit up to N DRAFT instructions",
    )
    parser.add_argument(
        "--approve-instructions",
        type=int,
        metavar="N",
        help="submit and approve up to N DRAFT/PENDING instructions",
    )
    parser.add_argument(
        "--reject-instructions",
        type=int,
        metavar="N",
        help="reject up to N PENDING instructions",
    )
    args = parser.parse_args(argv)
    settings = Settings()

    if args.seed_instructions is not None:
        if args.seed_instructions < 1:
            print("error: --seed-instructions must be at least 1", file=sys.stderr)
            return 1
        return _print_result(create_action(settings, args.seed_instructions))

    if args.submit_instructions is not None:
        if args.submit_instructions < 1:
            print("error: --submit-instructions must be at least 1", file=sys.stderr)
            return 1
        return _print_result(submit_action(settings, args.submit_instructions))

    if args.approve_instructions is not None:
        if args.approve_instructions < 1:
            print("error: --approve-instructions must be at least 1", file=sys.stderr)
            return 1
        return _print_result(approve_action(settings, args.approve_instructions))

    if args.reject_instructions is not None:
        if args.reject_instructions < 1:
            print("error: --reject-instructions must be at least 1", file=sys.stderr)
            return 1
        return _print_result(reject_action(settings, args.reject_instructions))

    return _print_result(run_policy_scenario(settings))


if __name__ == "__main__":
    raise SystemExit(main())
