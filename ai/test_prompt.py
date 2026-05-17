#!/usr/bin/env python3
"""
test_prompt.py — designer-facing prompt iteration loop.

Lets you change `ai/prompts/controller-from-catalog.md` (or the SYSTEM_PROMPT
constant in td/modules/unicorner_generator.py) and immediately see what the
model produces, *without* needing TouchDesigner running. This is the fast
loop for tuning routings, heuristics, or anything else in the prompt.

What it does:
  1. Loads a fixture scene catalog + (optional) DJay catalog.
  2. Calls the same `build_messages` + `_call_anthropic` + `parse_response`
     functions that the real TD-side generator uses, so the prompt under
     test is exactly what production runs.
  3. By default uses the System-prompt section straight from
     `ai/prompts/controller-from-catalog.md` — so designers edit the
     markdown and re-run this script, no need to sync into Python.
     Pass --use-py-prompt to test the SYSTEM_PROMPT constant instead.
  4. Pretty-prints the parsed spec (controls + routings).

Usage examples:

    # Test the default fixtures with a routing-asking prompt
    python ai/test_prompt.py "make this scene react to music with a bass blend knob"

    # Test without DJay (controls-only path)
    python ai/test_prompt.py --no-dj "give me intensity and color knobs"

    # Test against your own catalogs
    python ai/test_prompt.py --catalog mycat.json --dj-catalog mydj.json "..."

    # Test the SYSTEM_PROMPT in the .py file (what TD actually ships with)
    python ai/test_prompt.py --use-py-prompt "your prompt"

Requires:
    ANTHROPIC_API_KEY in the environment.

Designer iteration loop:
    1. Edit ai/prompts/controller-from-catalog.md
    2. python ai/test_prompt.py "your test prompt"
    3. Read the output, tweak the prompt, repeat
    4. When happy: python ai/sync_prompt.py   (copies the System prompt
       block into td/modules/unicorner_generator.py SYSTEM_PROMPT)
    5. Commit both files. In TD: reload main.toe (or re-run the drop-in
       scaffold) to pick up the new prompt.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES  = REPO_ROOT / "ai" / "fixtures"
MD_PROMPT = REPO_ROOT / "ai" / "prompts" / "controller-from-catalog.md"
GEN_PY    = REPO_ROOT / "td" / "modules" / "unicorner_generator.py"


def load_generator_module():
    """Import td/modules/unicorner_generator.py as a normal Python module.

    The file is designed to run inside TouchDesigner's bundled Python and
    relies on TD-injected globals (`op`, `parent`, `me`, `debug`, …) at
    function-call time — but at *import* time only stdlib is required, so
    a plain importlib import works.

    We provide a no-op `debug` shim in case any module-level code path
    references it (none do today, but cheap insurance).
    """
    spec = importlib.util.spec_from_file_location("unicorner_generator", GEN_PY)
    mod  = importlib.util.module_from_spec(spec)
    mod.__dict__.setdefault("debug", lambda *a, **kw: None)
    spec.loader.exec_module(mod)
    return mod


_MD_MARKER_RE = re.compile(
    r"<!--\s*BEGIN_SYSTEM_PROMPT.*?-->\s*\n(?P<body>.*?)\n\s*<!--\s*END_SYSTEM_PROMPT\s*-->",
    re.DOTALL,
)


def extract_system_prompt_from_md(md_path: Path) -> str:
    """Pull the literal system prompt out of the .md, bounded by the
    BEGIN_SYSTEM_PROMPT / END_SYSTEM_PROMPT marker comments. Marker-based
    extraction is robust against editing the surrounding doc structure
    (subheadings like `## Output`, `## Hard rules`, `## Optional: routings`
    live inside the markers and would otherwise terminate a heading-based
    regex). The .py constant in unicorner_generator.py keeps an exact copy
    of this body — `ai/sync_prompt.py` enforces no-drift."""
    text = md_path.read_text(encoding="utf-8")
    match = _MD_MARKER_RE.search(text)
    if not match:
        raise SystemExit(
            f"could not find BEGIN_SYSTEM_PROMPT / END_SYSTEM_PROMPT markers in {md_path}"
        )
    body = match.group("body")
    body = re.sub(r"\n+---\s*$", "", body.strip(), flags=re.MULTILINE)
    return body.strip()


def load_catalog(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def render_chip(routing: dict) -> str:
    """Mirror controller/src/DesignerDrawer.tsx's routingChipText so the
    test output and the iPad UI tell the same story."""
    rtype = routing.get("type")
    tail  = lambda p: (p or "").rsplit("/", 1)[-1]
    if rtype == "direct":
        return f"{routing.get('djay_channel')} → {tail(routing.get('target_path'))}.{routing.get('target_param')}"
    if rtype == "lfo_sync":
        targets = ", ".join(f"{tail(t.get('path'))}.{t.get('param')}"
                            for t in routing.get("targets", []))
        div = (f" ÷{routing['beats_per_cycle']}"
               if routing.get("beats_per_cycle") and routing["beats_per_cycle"] != 1 else "")
        return f"BPM{div} LFO → {targets}"
    if rtype == "bar_reset":
        return f"{routing.get('djay_channel')} → reset {tail(routing.get('target_lfo_path'))}"
    return f"unknown routing type {rtype!r}"


def print_spec(spec: dict, indent: int = 2) -> None:
    pad = " " * indent
    print(f"{pad}scene_id   : {spec.get('scene_id')}")
    print(f"{pad}rationale  : {spec.get('rationale', '<none>')}")
    print(f"{pad}controls   :")
    for c in spec.get("controls", []) or []:
        bind = c.get("bind") or {}
        mb   = c.get("macro_bindings")
        bind_desc = (f"-> {bind.get('path')}" if bind
                     else f"({len(mb)} macro legs)" if isinstance(mb, list)
                     else "")
        print(f"{pad}  • [{c.get('type'):<6}] {c.get('id'):<18} \"{c.get('label')}\" {bind_desc}")
    routings = spec.get("routings") or []
    if routings:
        print(f"{pad}routings   :")
        for r in routings:
            print(f"{pad}  • [{r.get('type'):<9}] {render_chip(r)}")
    else:
        print(f"{pad}routings   : (none)")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Standalone prompt tester for the controller-from-catalog generator.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(__doc__),
    )
    ap.add_argument("user_prompt", help="The prompt to feed the model (as if from the chat).")
    ap.add_argument("--catalog",       default=str(FIXTURES / "scene-catalog.json"),
                    help="Path to a scene catalog JSON (default: ai/fixtures/scene-catalog.json).")
    ap.add_argument("--dj-catalog",    default=str(FIXTURES / "dj-catalog.json"),
                    help="Path to a DJay catalog JSON (default: ai/fixtures/dj-catalog.json).")
    ap.add_argument("--no-dj",         action="store_true",
                    help="Skip the DJay catalog entirely (test the controls-only path).")
    ap.add_argument("--use-py-prompt", action="store_true",
                    help="Use SYSTEM_PROMPT from unicorner_generator.py "
                         "instead of the System-prompt section of the .md file. "
                         "Use this to verify the synced constant before committing.")
    ap.add_argument("--scene-summary", default=None,
                    help="Optional plain-language scene summary (mirrors the iPad scan).")
    ap.add_argument("--show-prompt",   action="store_true",
                    help="Print the assembled user-turn (before sending) instead of calling the API.")
    args = ap.parse_args()

    catalog    = load_catalog(Path(args.catalog))
    dj_catalog = None if args.no_dj else load_catalog(Path(args.dj_catalog))

    gen = load_generator_module()

    # Decide which system prompt the tester runs with.
    if args.use_py_prompt:
        system_prompt = gen.SYSTEM_PROMPT
        prompt_source = "td/modules/unicorner_generator.py SYSTEM_PROMPT"
    else:
        system_prompt = extract_system_prompt_from_md(MD_PROMPT)
        prompt_source = "ai/prompts/controller-from-catalog.md (live)"

    # Build the user turn exactly the way TD does.
    messages = gen.build_messages(
        catalog,
        args.user_prompt,
        scene_summary=args.scene_summary,
        dj_catalog=dj_catalog,
    )

    if args.show_prompt:
        print(f"# System prompt source: {prompt_source}")
        print(f"# System prompt length: {len(system_prompt)} chars")
        print(f"# User turn:")
        for m in messages:
            print(f"## {m['role']}")
            print(m["content"])
        return 0

    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        print("ANTHROPIC_API_KEY not set — export it or pass --show-prompt to dry-run.",
              file=sys.stderr)
        return 2

    print(f"→ Prompt source : {prompt_source}")
    print(f"→ Scene catalog : {args.catalog} ({len(catalog.get('modules', []))} modules)")
    if dj_catalog is not None:
        print(f"→ DJay catalog  : {args.dj_catalog} "
              f"({len(dj_catalog.get('channels', []))} channels)")
    else:
        print(f"→ DJay catalog  : (none — routings disabled)")
    print(f"→ User prompt   : {args.user_prompt}")
    print()
    print("Calling Anthropic…")

    # Patch SYSTEM_PROMPT for this call so the .md-loaded text is what the
    # API actually sees. (Cheaper than reimplementing _call_anthropic here.)
    original_prompt = gen.SYSTEM_PROMPT
    try:
        gen.SYSTEM_PROMPT = system_prompt
        response = gen._call_anthropic(
            messages, model=gen.DEFAULT_MODEL, api_key=api_key,
        )
    finally:
        gen.SYSTEM_PROMPT = original_prompt

    print()
    print("─" * 72)
    print("Raw response:")
    print(response if len(response) < 4000 else response[:4000] + "\n…(truncated)")
    print("─" * 72)

    try:
        parsed = gen.parse_response(response, catalog, dj_catalog=dj_catalog)
    except Exception as e:
        print(f"\n❌ Validation failed: {e}")
        return 1

    print()
    mode = parsed.get("mode")
    if mode == "spec":
        print("✓ Single-spec mode")
        print_spec(parsed["spec"])
    elif mode == "alternatives":
        alts = parsed["alternatives"]
        print(f"✓ Alternatives mode ({len(alts)} options)")
        for alt in alts:
            print(f"\n[{alt['id']}] {alt['label']} — {alt['description']}")
            print_spec(alt["spec"], indent=4)
    elif mode == "question":
        print("✓ Clarifying-question mode")
        print(f"  Q: {parsed['question']}")
        for a in parsed["alternatives"]:
            print(f"    • [{a['id']}] {a['label']} — {a['description']}")
    else:
        print(f"unknown parse mode: {mode!r}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
