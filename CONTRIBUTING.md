# Contributing to Victoria

## Testing policy

Match verification depth to blast radius — **run the cheap checks on every change, and reserve deep end-to-end for changes that affect runtime behaviour.** The lesson behind this: a green unit suite once shipped an app that wouldn't boot, because mocked/temp-DB unit tests don't exercise real startup with the real environment.

### Every PR (always)

- **Unit suite** — `python -m pytest tests/ -q` (fast, mocked, temp DBs).
- **Boot / smoke check** — the app must import and settings must load:
  ```bash
  python -c "from victoria.main import app; from victoria.config import Settings; Settings(); print('boot OK')"
  ```
  This catches import-time and config-load failures unit tests miss.

Both run automatically in CI (`.github/workflows/ci.yml`) on every PR and push to `main`; a red run blocks merge.

### Runtime-affecting PRs → also full end-to-end

For changes to config/startup, the LLM router, tools, escalation, MCP, or the launch/deploy scripts, drive the real flow before merging:

1. Restart on the merged state: `scripts/start.sh`
2. Exercise the actual path (send a chat that hits the tool/model/escalation you changed) and confirm the response — including the model badge for routing changes.

Docs / comments / skill-text-only PRs don't need this — unit + smoke is enough.

### Always

- **Add a regression test for every bug.** If it broke once, a test should fail if it breaks again.
- **Verify on `main` after merge, in the real environment — not just the feature branch.** Merge-order and `.env` interactions only surface on the merged, real-config state. (A branch can pass while the intermediate `main` is broken.)
- **Never log secret values.** Config warnings and errors reference variable *names* only.

### One feature per PR

Keep changes scoped and reviewable; open a PR and let a human merge. Small, focused PRs are easier to verify end-to-end and safer to roll back.
