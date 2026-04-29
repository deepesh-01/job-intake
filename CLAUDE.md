# CLAUDE.md — Project pointer for Claude Code

This is the **job-intake** repo (a.k.a. System B / `ready-to-apply`). Read the relevant pointer below before answering — don't synthesize from training data.

## What to read, when

| If the user is asking about… | Read first |
|---|---|
| Project rules, conventions, footguns, exact tech versions | [`_bmad-output/project-context.md`](./_bmad-output/project-context.md) — the dense LLM-optimized rule sheet (47 rules). **Always check this before writing code.** |
| What the system does + 10x roadmap | [`docs/vision.md`](./docs/vision.md) |
| How to run / deploy / debug / restart services | [`docs/how-to-journey.md`](./docs/how-to-journey.md) |
| "Why is it built this way?" / "Should we change X?" | [`docs/decisions.md`](./docs/decisions.md) — ADR log. Check this before proposing architectural changes; many "obvious improvements" are explicitly rejected with reasoning. |
| Source diversity / why so few India jobs / how to broaden | [`docs/sources-roadmap.md`](./docs/sources-roadmap.md) |
| Build history / what shipped when | [`docs/tasks.md`](./docs/tasks.md) |
| Original v1 design (frozen, historical) | [`docs/job-intake-design-v1-original.md`](./docs/job-intake-design-v1-original.md) |
| Architecture map / which symbol depends on what | `graphify-out/GRAPH_REPORT.md` if present, else run `/graphify ./src` to regenerate |

## Sibling project

System A lives at `~/Documents/resume-builder/` (separate repo, separate Telegram bot). The cross-system contract is `dist/cli-tailor.js` invoked as a subprocess from `src/tailor_bridge.py`. **Do not modify System A from this repo.**

## Push gotcha

A stale `GITHUB_TOKEN` may be set in the parent shell env. If `git push` 401s, retry with `env -u GITHUB_TOKEN git push`. (Permanently fixed in `~/dotfiles/secrets.sh` 2026-04-27, but old shells still have it.)

## BMAD agents

This repo has BMAD-METHOD installed under `_bmad/` with 42 skills under `.claude/skills/bmad-*`. Invoke `bmad-help` to see the agent + workflow catalog.
