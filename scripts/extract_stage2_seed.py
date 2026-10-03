#!/usr/bin/env python3
"""Print the session seed and prompts recorded in one or more rollout.asl.

Replaces the ad-hoc /tmp/extract_seed.py helper used by
check_stage2_task24.py. Output per file (the gate greps for
``random_seed=``):

    <asl path>
    random_seed=<int>
    prompt_positive=<one line>
    prompt_negative=<one line>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stage2_asl_replay import load_replay  # noqa: E402


def main(argv: list[str]) -> None:
    if not argv:
        sys.exit("usage: extract_stage2_seed.py <rollout.asl> [...]")
    for asl_path in argv:
        replay = load_replay(asl_path)
        print(replay.asl_path)
        print(f"random_seed={replay.session_seed}")
        print(f"prompt_positive={replay.prompt_positive.strip()!r}")
        print(f"prompt_negative={replay.prompt_negative.strip()!r}")


if __name__ == "__main__":
    main(sys.argv[1:])
