# build-ai-assistant (Claude skill)

A reusable **build playbook** for creating a personal, local-first AI assistant —
distilled from building Victoria (this repo). It's a Claude Code / Claude.ai
*skill*: guidance a coding agent follows to scaffold or extend a JARVIS-style
assistant, covering the reference architecture, a phased build order, provider
choices with trade-offs, privacy/guardrail invariants, and the hard-won gotchas.

Victoria is the worked example behind it — [`references/victoria-reference.md`](references/victoria-reference.md)
maps every layer and build phase to its exact path in this repo.

## Contents

- [`SKILL.md`](SKILL.md) — the skill itself (instructions only, no code).
- [`references/victoria-reference.md`](references/victoria-reference.md) — the layer/phase → repo-path map.
- `build-ai-assistant.skill` — the packaged, installable bundle.

## Install / use

- **Claude Code (personal skill):** copy the `build-ai-assistant/` folder into
  `~/.claude/skills/`, then invoke it with `/build-ai-assistant`.
- **Packaged bundle:** open `build-ai-assistant.skill` in Claude and use
  **Save skill** (installs it into your profile).
- **Just reading:** `SKILL.md` stands on its own as an architecture + build guide.

It triggers on requests to build, scaffold, architect, plan, or extend a personal
AI assistant / agent / copilot / "Jarvis"-style app.
