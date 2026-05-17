#!/usr/bin/env python3
'''
sync_prompt.py — copy the canonical prompt from
`ai/prompts/controller-from-catalog.md` into the `SYSTEM_PROMPT` constant in
`td/modules/unicorner_generator.py`.

Why this exists: the .md file is the human-readable, design-tunable source.
The .py constant has to be a literal string because the COMP's drop-in Text
DAT is self-contained at TD runtime — it cannot read files from the repo.
So we have two copies, and this script guarantees they stay in lockstep.

Usage:
    python ai/sync_prompt.py             # update the .py file from the .md
    python ai/sync_prompt.py --check     # exit non-zero if they drift
                                         #   (use in CI/pre-commit)
    python ai/sync_prompt.py --diff      # show the diff without writing

The script extracts both the `## System prompt` and `## Optional: routings`
sections from the .md (because in the .py file they're concatenated into one
SYSTEM_PROMPT string), strips trailing horizontal rules, and writes the
result into the existing `SYSTEM_PROMPT = """..."""` block in the .py file.
'''

from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MD_PATH   = REPO_ROOT / "ai" / "prompts" / "controller-from-catalog.md"
PY_PATH   = REPO_ROOT / "td" / "modules" / "unicorner_generator.py"


# Explicit start/end markers in the .md frame the literal system prompt.
# This is robust against editing the surrounding doc structure (adding new
# sub-headings, reorganizing the few-shot examples, etc.).
_MD_MARKER_RE = re.compile(
    r"<!--\s*BEGIN_SYSTEM_PROMPT.*?-->\s*\n(?P<body>.*?)\n\s*<!--\s*END_SYSTEM_PROMPT\s*-->",
    re.DOTALL,
)
# Matches a Python triple-quoted SYSTEM_PROMPT assignment, capturing the
# body. Allows extra blank lines around the opening/closing triple quotes.
_PY_SYSTEM_PROMPT_RE = re.compile(
    r'(?P<prefix>SYSTEM_PROMPT\s*=\s*""")(?P<body>.*?)(?P<suffix>""")',
    re.DOTALL,
)


def extract_md_prompt(md_text: str) -> str:
    """Pull out the text between BEGIN_SYSTEM_PROMPT and END_SYSTEM_PROMPT
    markers. That text is treated as the literal system prompt — markdown
    headings included, since they're harmless to the model and help readers
    of both the .md and the .py constant.

    We strip a trailing `---` separator if present (designers sometimes
    leave one before the end marker as visual punctuation), and trim
    surrounding whitespace.
    """
    match = _MD_MARKER_RE.search(md_text)
    if not match:
        raise SystemExit(
            "could not find BEGIN_SYSTEM_PROMPT / END_SYSTEM_PROMPT markers in the .md"
        )
    body = match.group("body")
    body = re.sub(r"\n+---\s*$", "", body.strip(), flags=re.MULTILINE)
    return body.strip()


def replace_py_prompt(py_text: str, new_prompt: str) -> str:
    '''Substitute the SYSTEM_PROMPT = """...""" body in-place. We keep
    the existing prefix and suffix so unrelated formatting (the leading
    `SYSTEM_PROMPT = ` line, indentation, the closing triple quotes) is
    preserved.'''
    if '"""' in new_prompt:
        raise SystemExit(
            "the prompt body contains a literal `\"\"\"` — that would break the "
            "Python string. Edit the .md to escape it before re-running sync."
        )

    def _sub(m):
        return m.group("prefix") + new_prompt + m.group("suffix")

    new_text, n = _PY_SYSTEM_PROMPT_RE.subn(_sub, py_text, count=1)
    if n != 1:
        raise SystemExit(
            "could not find a SYSTEM_PROMPT triple-quoted assignment in the .py — "
            "did the file format change?"
        )
    return new_text


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="Exit non-zero if the .py is out of sync. Doesn't write.")
    ap.add_argument("--diff",  action="store_true",
                    help="Print a unified diff of the change. Doesn't write.")
    args = ap.parse_args()

    md_text  = MD_PATH.read_text(encoding="utf-8")
    py_text  = PY_PATH.read_text(encoding="utf-8")
    expected_prompt = extract_md_prompt(md_text)

    cur_match = _PY_SYSTEM_PROMPT_RE.search(py_text)
    if cur_match is None:
        print(f"{PY_PATH}: no SYSTEM_PROMPT triple-quoted assignment found.",
              file=sys.stderr)
        return 2
    cur_prompt = cur_match.group("body")

    if cur_prompt == expected_prompt:
        if args.check or args.diff:
            print("✓ in sync")
        else:
            print("✓ already in sync — no changes written.")
        return 0

    if args.check:
        # Show a short context diff so the failure is actionable.
        diff = "\n".join(difflib.unified_diff(
            cur_prompt.splitlines(),
            expected_prompt.splitlines(),
            fromfile="td/modules/unicorner_generator.py (SYSTEM_PROMPT)",
            tofile="ai/prompts/controller-from-catalog.md (canonical)",
            lineterm="",
            n=2,
        ))
        print("❌ DRIFT: SYSTEM_PROMPT in unicorner_generator.py does not match "
              "ai/prompts/controller-from-catalog.md.\n", file=sys.stderr)
        print(diff[:4000] + ("\n…(truncated)" if len(diff) > 4000 else ""),
              file=sys.stderr)
        print("\nRun: python ai/sync_prompt.py", file=sys.stderr)
        return 1

    if args.diff:
        for line in difflib.unified_diff(
            cur_prompt.splitlines(),
            expected_prompt.splitlines(),
            fromfile="(current) unicorner_generator.py SYSTEM_PROMPT",
            tofile="(canonical) ai/prompts/controller-from-catalog.md",
            lineterm="",
        ):
            print(line)
        return 0

    new_py = replace_py_prompt(py_text, expected_prompt)
    PY_PATH.write_text(new_py, encoding="utf-8")
    print(f"✓ wrote {PY_PATH.relative_to(REPO_ROOT)} — SYSTEM_PROMPT updated "
          f"({len(expected_prompt)} chars).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
