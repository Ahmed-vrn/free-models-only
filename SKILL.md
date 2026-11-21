---
name: Free Models Only
description: Hide every paid OpenCode model and keep only free ones — use when the user wants to disable paid models, restrict the model catalog to free tier, or refresh the free-only list after a catalog update.
metadata:
  opencode/autoinvoke: true
---

# Free Models Only

Replace the managed block in `~/.config/opencode/opencode.jsonc` so that only
models with zero input and output cost remain selectable in OpenCode.

This directory is itself a git repository: updating the skill is `git pull`.
See `README.md` for installation on another machine.

## When to use

- "احذف كل النماذج المدفوعة واترك المجانية فقط"
- "Disable paid models / keep only free models"
- After OpenCode rotates or adds model IDs and paid models reappear in `/models`.

## How it works

OpenCode V2 has no whitelist setting. The supported mechanism is
`providers.opencode.models.<model-id>.disabled = true` (documented at
`https://opencode.ai/docs/providers` → Models table, and `https://opencode.ai/docs/models`).
The script writes one entry per paid model ID.

The config block is delimited by these markers and is replaced as a whole on
every run; everything else in the file is preserved:

```
  // >>> free-models-only: START (managed — do not edit manually)
  ...
  // <<< free-models-only: END
```

## Workflow

1. Fetch the official Console models page (authoritative free/paid list):

   ```bash
   curl -sS https://opencode.ai/docs/console/models -o /tmp/opencode/console-models.html
   ```

   Or use `webfetch` on `https://opencode.ai/docs/console/models` and read the
   **Pricing** table: a model is free only when both Input and Output show `Free`.

2. Build the paid ID list. Use the **Model / Model ID** table to map display
   names to IDs, then exclude IDs whose pricing row says `Free` for input and
   output. Also exclude these always-free IDs (from the Free models section):

   ```
   big-pickle iceman-free inkling-2-small-free deepseek-v4.1-flash-free
   beam-free flint-40b-free longcat-2.5-free hy4-flash-free
   mimo-v2.6-flash-free nemotron-3.5-lightning-free
   nemotron-3.5-super-vl-free jev-1.13-free tidepool-alpha-free
   ```

   Known paid IDs are in `references/paid-model-ids.txt` (fallback). Merge them
   with anything newly discovered — never remove an ID from that file without
   checking the official pricing table.

3. Write the merged, sorted, unique IDs (one per line) to
   `/tmp/opencode/paid-ids.txt`, then apply. Paths below are relative to this
   skill directory:

   ```bash
   python3 scripts/apply.py --paid-file references/paid-model-ids.txt
   ```

   Use `--dry-run` first to preview. The script validates the config with a
   JSONC-tolerant parser, replaces only the managed block, and prints how many
   IDs were disabled.

4. Verify with the Code Mode model catalog tool:

   ```javascript
   const r = await tools.opencode.models({ limit: 100 })
   const all = r.providers.flatMap(p => p.models.map(m => ({
     id: m.id,
     cost: m.cost?.[0]?.input ?? 0,
     output: m.cost?.[0]?.output ?? 0,
   })))
   return { count: all.length, paidLeft: all.filter(m => m.cost > 0 || m.output > 0) }
   ```

   `paidLeft` must be empty. If a paid model appears, add its ID to
   `references/paid-model-ids.txt` and rerun step 3.

5. Confirm the configured default still runs:

   ```bash
   "$HOME/.config/ai.opencode.desktop/cli/2.3.2-x64/opencode-cli" run "Reply with exactly: FREE_OK"
   ```

## Update

```bash
git -C "$HOME/.config/opencode/skills/free-models-only" pull
bash "$HOME/.config/opencode/skills/free-models-only/scripts/install.sh"
```

`install.sh` re-applies the managed block from the updated ID list. OpenCode
reloads configuration automatically.

## Notes

- The catalog is dynamic: OpenCode may rotate IDs (e.g. `gemini-4-neon` →
  `gemini-4-carbon`). Re-run this skill after an OpenCode update.
- Do not disable the model used by the current session — configuration changes
  reload automatically for new requests, but a selected session model keeps its
  setting for that turn.
- `OPENCODE_DISABLE_FREE_MODELS=1` does the opposite; never set it here.
