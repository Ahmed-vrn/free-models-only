#!/usr/bin/env python3
"""Replace the free-models-only managed block in the OpenCode global config.

Usage:
    apply.py --paid-file /tmp/opencode/paid-ids.txt
    apply.py --paid "gpt-6.2-sol,claude-opus-5-5,..."
"""
import argparse
import json
import re
import sys
from pathlib import Path

CONFIG = Path.home() / ".config/opencode/opencode.jsonc"
START = "  // >>> free-models-only: START (managed — do not edit manually)\n"
END = "  // <<< free-models-only: END\n"


def strip_jsonc(text: str) -> str:
    # Remove // line comments outside strings, then trailing commas.
    out = []
    for line in text.splitlines():
        in_string = False
        esc = False
        cut = len(line)
        for i, ch in enumerate(line):
            if esc:
                esc = False
                continue
            if ch == "\\" and in_string:
                esc = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if ch == "/" and not in_string and i + 1 < len(line) and line[i + 1] == "/":
                cut = i
                break
        out.append(line[:cut])
    text = "\n".join(out)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--paid-file", help="file with one paid model ID per line")
    src.add_argument("--paid", help="comma/space separated paid model IDs")
    ap.add_argument("--config", type=Path, default=CONFIG)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.paid_file:
        raw = Path(args.paid_file).read_text(encoding="utf-8")
    else:
        raw = args.paid
    ids = sorted({x.strip() for x in re.split(r"[\s,]+", raw) if x.strip()})
    if not ids:
        print("error: no paid model IDs given", file=sys.stderr)
        return 2

    text = args.config.read_text(encoding="utf-8")
    s = text.find(START)
    e = text.find(END)
    if s < 0 or e < 0 or e <= s:
        print("error: managed markers not found in config", file=sys.stderr)
        return 3

    lines = "\n".join(f'        "{mid}": {{ "disabled": true }},' for mid in ids)
    lines = lines.rstrip(",")
    block = (
        START
        + "  // ═══════════════════════════════════════════════════════════════\n"
        + "  //  إخفاء كل النماذج المدفوعة — يبقى المجاني فقط (لا مفتاح ولا رصيد)\n"
        + "  //  مُدار بواسطة مهارة free-models-only — لا تُعدّل يدوياً\n"
        + "  // ═══════════════════════════════════════════════════════════════\n"
        + '  "providers": {\n'
        + '    "opencode": {\n'
        + '      "models": {\n'
        + lines
        + "\n      }\n"
        + "    }\n"
        + "  },\n"
        + END
    )
    new = text[:s] + block + text[e + len(END):]

    json.loads(strip_jsonc(new))  # fail before writing if invalid

    if args.dry_run:
        print(f"dry-run OK: {len(ids)} paid IDs would be disabled")
        return 0

    args.config.write_text(new, encoding="utf-8")
    print(f"updated {args.config}: {len(ids)} paid model IDs disabled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
