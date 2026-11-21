#!/usr/bin/env python3
"""Small helpers for locating and querying the OpenCode CLI."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def version_key(path: Path) -> List[object]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", str(path))]


def find_cli(explicit: Optional[str] = None) -> Optional[str]:
    if explicit:
        candidate = Path(explicit).expanduser()
        return str(candidate) if candidate.exists() else None

    env_value = os.environ.get("OPENCODE_BIN")
    if env_value and Path(env_value).expanduser().exists():
        return str(Path(env_value).expanduser())

    on_path = shutil.which("opencode")
    if on_path:
        return on_path

    candidates = sorted(
        Path.home().glob(".config/ai.opencode.desktop/cli/*/opencode-cli"),
        key=version_key,
    )
    for candidate in reversed(candidates):
        if candidate.exists():
            return str(candidate)

    local = Path.home() / ".local/bin/opencode"
    if local.exists():
        return str(local)
    return None


def list_runtime_models(
    cli: str,
    timeout: int = 30,
) -> Tuple[List[str], str, Optional[str]]:
    """Return the model refs that the running OpenCode instance exposes."""
    try:
        completed = subprocess.run(
            [cli, "models"],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "NO_COLOR": "1"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [], "", str(exc)

    output = strip_ansi((completed.stdout or "") + "\n" + (completed.stderr or ""))
    if completed.returncode != 0:
        return [], output, "opencode models exited with %d" % completed.returncode

    refs: List[str] = []
    for line in output.splitlines():
        candidate = line.strip()
        if not candidate or " " in candidate or "/" not in candidate:
            continue
        provider, model_id = candidate.split("/", 1)
        if not re.match(r"^[A-Za-z0-9_.-]+$", provider):
            continue
        if not re.match(r"^[A-Za-z0-9_.:/+#-]+$", model_id):
            continue
        refs.append("%s/%s" % (provider, model_id))
    return sorted(set(refs)), output, None
