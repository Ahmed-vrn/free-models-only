# free-models-only

Skill + config manager for OpenCode V2 that keeps **only free models** in the
model catalog. It disables every paid model via
`providers.opencode.models.<id>.disabled = true`, the mechanism documented in
the official [Providers](https://opencode.ai/docs/providers) and
[Models](https://opencode.ai/docs/models) pages.

النسخة العربية: يخفي كل النماذج المدفوعة في OpenCode ويترك المجانية فقط،
بلا مفتاح API ولا رصيد. يعمل كـ skill这群 يُحدَّث عبر `git pull`.

## Why

OpenCode's Console catalog is dynamic and paid models keep reappearing with
rotated IDs (`gemini-4-neon` → `gemini-4-carbon`, etc.). This project keeps a
 curated list of paid IDs from the official
[Console Models / Pricing](https://opencode.ai/docs/console/models) page and
re-applies a managed block in `~/.config/opencode/opencode.jsonc`.

There is no whitelist setting in OpenCode V2 — `disabled: true` per model is
the supported approach.

## Install

```bash
git clone <REPO_URL> ~/.config/opencode/skills/free-models-only
bash ~/.config/opencode/skills/free-models-only/scripts/install.sh
```

OpenCode discovers the skill automatically from
`~/.config/opencode/skills/<id>/SKILL.md`.

## Update

```bash
bash ~/.config/opencode/skills/free-models-only/scripts/update.sh
# or: git -C ~/.config/opencode/skills/free-models-only pull && bash .../install.sh
```

## Uninstall (config only)

```bash
bash ~/.config/opencode/skills/free-models-only/scripts/uninstall.sh
```

This removes only the managed block; all other config settings are preserved.

## Files

| Path | Purpose |
| --- | --- |
| `SKILL.md` | Skill instructions and workflow. |
| `scripts/apply.py` | Replaces the managed block, validates JSONC, no other edits. |
| `scripts/install.sh` | Adds markers if needed, then applies. |
| `scripts/update.sh` | `git pull` + re-apply. |
| `scripts/uninstall.sh` | Remove the managed block. |
| `references/paid-model-ids.txt` | Curated paid model IDs (from official pricing table). |
| `version.json` | Version, last update date, source URLs. |

## Verify

```bash
python3 scripts/apply.py --paid-file references/paid-model-ids.txt --dry-run
```

Then in OpenCode, `/models` should list only `*-free` models plus `big-pickle`
(and `jev-1.13-free` if available). The configured default is
`opencode/deepseek-v4.1-flash-free`.

## License

MIT — see `LICENSE`.
