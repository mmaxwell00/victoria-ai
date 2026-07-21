# Victoria AI — start here

Victoria is Mark's local-first, JARVIS-style personal AI assistant (British,
witty): local LLM via Docker Model Runner, opt-in Claude escalation, layered
memory, a web HUD, tools, MCP, an encrypted vault, and an Obsidian-backed
knowledge base. This file is the auto-loaded entrypoint for any Claude/agent
session working in this repo. It is a **router**, not the full docs — read the
files below.

## Read in this order
1. **`plans/HANDOFF.md`** — the latest session state: goal, what's done / in
   progress, exact files touched, failed approaches (marked DO NOT REPEAT), and
   ordered next steps. **If it exists, read it first — you're resuming from it.**
2. **`claude-md.md`** — the project bible: architecture, tech stack, conventions,
   current focus. (It's named `claude-md.md`, not `CLAUDE.md`.)
3. **`docs/decisions-md.md`** — the decision log (lightweight ADRs), newest at
   the top of the `## Decided` section. (Named `docs/decisions-md.md`, not
   `docs/DECISIONS.md`, despite older in-doc references.)

## Standing rules
- **Never self-merge a PR.** Open it, report it, and wait for Mark to say "merge #N".
- Work on a branch; open a PR for every change.
- Keep `README.md` and the `build-ai-assistant` skill + its
  `docs/build-ai-assistant/references/victoria-reference.md` in sync with changes
  (test counts, endpoints, features, file tree).
- Use the `code-review-repo` skill for repo consistency audits.
- `git commit -m "…"` with backticks corrupts the message (the shell
  command-substitutes them) — use `git commit -F -` with a quoted heredoc, or
  `--body-file` for PRs.

## Run / test
- Web HUD (native): `uvicorn victoria.main:app --reload` → http://localhost:8000
- Tests: `python -m pytest -q`
- Docker Sandbox (isolated) deploy: `./deploy-sandbox.sh` — see `SANDBOX-DEPLOYMENT.md`
