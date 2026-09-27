#!/usr/bin/env python3
# ABOUTME: Wraps Agency-composed agent prompt with the speedrift protocol envelope.
# ABOUTME: Reads composed prompt on stdin, original prompt as arg, outputs merged prompt.

"""
Agency supplies the cognitive role (who the agent is, trade-offs, style).
Speedrift supplies the protocol (wg-contract, drift checks, workgraph rules).

This script merges the two: Agency identity first, then the original speedrift
prompt. The agent sees a single coherent prompt.
"""

import sys


def wrap(composed_prompt: str, original_prompt: str) -> str:
    """Merge Agency composition with speedrift protocol prompt."""
    parts = []

    # Agency-composed identity block
    parts.append("## Agency-Composed Agent Identity\n")
    parts.append(composed_prompt.strip())
    parts.append("\n\n---\n")

    # Original speedrift prompt (contains task details, wg-contract, drift protocols)
    parts.append(original_prompt)

    return "\n".join(parts)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: agency-speedrift-wrap.py ORIGINAL_PROMPT_FILE", file=sys.stderr)
        print("  Reads Agency-composed prompt from stdin.", file=sys.stderr)
        sys.exit(1)

    original_prompt_file = sys.argv[1]
    try:
        with open(original_prompt_file, "r", encoding="utf-8") as f:
            original_prompt = f.read()
    except (OSError, IOError) as e:
        print(f"error: cannot read original prompt: {e}", file=sys.stderr)
        sys.exit(1)

    composed_prompt = sys.stdin.read()
    if not composed_prompt.strip():
        # No composition — pass through original unchanged
        print(original_prompt, end="")
        sys.exit(0)

    print(wrap(composed_prompt, original_prompt), end="")


if __name__ == "__main__":
    main()
