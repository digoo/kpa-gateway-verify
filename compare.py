#!/usr/bin/env python3
"""Compare answers from two clients on the same fixed test items.

Usage:
    # 1. run the suite against the API endpoint
    python3 kpa_verify.py                       # writes out/expected_answers.json

    # 2. paste the prompts into Copilot (one prompt per chat), collect the
    #    answers, and save them as JSON:
    #      {"multiply": "4432684638056704", "count_r": "114", ...}

    # 3. compare
    python3 compare.py other_answers.json
"""

import json
import re
import sys
from pathlib import Path

WS = re.compile(r"\s+")
DIGIT = re.compile(r"\D")

KEYS = ("multiply", "count_r", "sort", "python_stdout", "logic_divisible_by_3_or_5", "needle")
LABELS = {
    "multiply": "48271936 * 91827364",
    "count_r": "letter r in 40 words",
    "sort": "sort 25 numbers",
    "python_stdout": "python closure stdout",
    "logic_divisible_by_3_or_5": "divisible by 3 or 5, not both",
    "needle": "needle in ~30k tokens",
}


def normalize_sort(value):
    if isinstance(value, str):
        try:
            value = json.loads(re.sub(r"```\w*", "", value).strip())
        except Exception:
            return value
    if isinstance(value, list):
        try:
            return [int(item) for item in value]
        except Exception:
            return value
    return value


def judge(key, given, expected):
    if given is None:
        return False, "no answer"
    text = str(given)
    if key == "sort":
        got = normalize_sort(text)
        return got == expected, str(got)[:60]
    if key == "python_stdout":
        flat = WS.sub(" ", text).strip()
        return expected in flat, flat[:60]
    if key == "needle":
        return expected in DIGIT.sub("", text), DIGIT.sub("", text)[:40]
    digits = DIGIT.sub("", text)
    return digits == str(expected), digits[:40]


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    other_path = Path(sys.argv[1])
    other = json.loads(other_path.read_text())
    expected = json.loads((Path("out") / "expected_answers.json").read_text())

    print("")
    print("=" * 74)
    print("%-28s %-8s %-8s %s" % ("ITEM", "EXPECTED", "OTHER", "MATCH"))
    print("=" * 74)
    hits = 0
    total = 0
    for key in KEYS:
        if key not in expected:
            continue
        total += 1
        given = other.get(key)
        ok, detail = judge(key, given, expected[key])
        hits += ok
        print("%-28s %-8s %-8s %s" % (
            LABELS.get(key, key)[:28],
            "yes" if expected[key] is not None else "-",
            "yes" if ok else "NO",
            detail,
        ))
    print("")
    print("other client score: %d/%d" % (hits, total))
    print("")
    print("Run the same suite against the other endpoint to compare directly:")
    print("  ONLY=claude python3 kpa_verify.py")
    print("then diff out/summary.json between the two runs.")


if __name__ == "__main__":
    main()
