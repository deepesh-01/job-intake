#!/usr/bin/env bash
# docs-sync.sh — claude-driven doc audit + update for job-intake.
#
# Compares the current code surface (sources, env, schema, endpoints,
# webapp components) against the docs and either:
#   --check (default) → prints drift; non-zero exit if any
#   --apply           → asks claude to write the updates back to the docs
#
# Cost: ~$0.05-0.20 per check, ~$0.30-0.50 per apply.
# Pre-req: `claude` CLI authenticated.
#
# Mirrors the same protocol resume-builder uses (~/Documents/resume-builder/
# scripts/docs-sync.sh) so the two repos behave identically.

set -euo pipefail

MODE="${1:-check}"
case "$MODE" in
  --check|-c|check) MODE=check ;;
  --apply|-a|apply) MODE=apply ;;
  *) echo "usage: $0 [check|apply]" >&2; exit 2 ;;
esac

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DOCS_DIR="$REPO_ROOT/docs"

[ -d "$DOCS_DIR" ] || { echo "docs dir not found: $DOCS_DIR" >&2; exit 1; }
command -v claude >/dev/null 2>&1 || { echo "claude CLI not in PATH" >&2; exit 1; }

PROMPT_BASE="You are a documentation maintenance assistant for job-intake — a daily job-board scraper that feeds a Google Sheet, scores postings against a base resume, and exposes a webapp for triage + tailoring. Sibling project: resume-builder (System A) which provides the headless tailoring CLI invoked by the processor.

Read these files (paths under $REPO_ROOT for code, $DOCS_DIR for docs):

CODE (source of truth):
- pyproject.toml (Python deps; scout/processor entry-points)
- boards.yaml (every enabled board + source_type)
- tag_rules.yaml (currency floors, stack keywords, culture signals, target city aliases)
- exclude.yaml (companies/keywords/patterns dropped at insert time)
- src/lib/config.py (every env key required at boot)
- src/lib/posting.py (Posting + EnrichedRow dataclasses)
- src/lib/drive.py (Google Drive uploader — OAuth user delegation)
- src/sheet/schema.py (column order + SCHEMA_VERSION + status enum)
- src/sheet/migrate.py (per-version migrations)
- src/sheet/client.py (gspread wrapper — what reads/writes are exposed)
- src/scout/runner.py (scout end-to-end flow + first-run cutoff + follow-up sweep)
- src/scout/extract.py (HTML→text + currency-aware comp parser)
- src/scout/tag.py (every tag family + their rules)
- src/scout/resume.py (resume↔JD skill overlap scorer)
- src/scout/dedup.py (sqlite cache + fuzzy match)
- src/scout/exclude.py (exclude.yaml loader + matcher)
- src/scout/sources/*.py (every source client — Greenhouse/Lever/Ashby/Workday/RemoteOK/Remotive/Arbeitnow/Hasjob/HN-Hiring/LinkedIn/yc_waas)
- src/processor/runner.py (processor sweep + lock + Drive upload)
- src/tailor_bridge.py (subprocess call into resume-builder's cli-tailor.js)
- src/web/api.py (every FastAPI endpoint, the auth middleware, the SPA static fallback)
- src/web/bot_health.py (resume-builder watchdog)
- src/web/cache.py (TTL cache for Sheet snapshot)
- scripts/*.py and scripts/*.sh (every helper script)
- webapp/index.html (PWA tags + manifest references)
- webapp/public/manifest.webmanifest (installable app metadata)
- webapp/public/sw.js (service worker)
- webapp/src/App.tsx (top-level layout + view modes)
- webapp/src/lib/api.ts (every API client function)
- webapp/src/lib/auth.ts (canMutate / token bootstrap)
- webapp/src/components/*.tsx (every visible component)
- com.user.jobintake.web.plist + com.user.jobintake.scout.plist (launchd services + PATH)

DOCS (target):
- $DOCS_DIR/vision.md (current state + 10x roadmap; edit anywhere)
- $DOCS_DIR/how-to-journey.md (operational guide; edit anywhere)
- $DOCS_DIR/tasks.md (chronological build log; APPEND-ONLY — never edit prior steps)
- $DOCS_DIR/decisions.md (ADRs; APPEND-ONLY — never edit prior ADRs; new ADR uses next sequential ADR-NNN)

Reference (frozen):
- $DOCS_DIR/job-intake-design-v1-original.md (original v1 spec; do NOT modify)

Your job: identify drift between what the code does and what the docs say. Specifically check:

1. **Sources.** Every source_type appearing in boards.yaml has (a) a module under src/scout/sources/, (b) a registration in runner._SOURCE_MODULES, (c) a registration in scripts/verify_boards.py _SOURCE_MODULES, and (d) a row in how-to-journey.md \"Sources\" table.
2. **Env vars.** Every required key in src/lib/config.py is in how-to-journey.md \`.env\` section. Optional vars (drive_folder_id, write_token, etc.) are explicitly noted as optional.
3. **Sheet columns.** JOBS_COLUMNS in src/sheet/schema.py matches the column list in how-to-journey.md \"Jobs tab\" section. SCHEMA_VERSION constant matches the migration chain in migrate.py and the version cell description.
4. **Status states.** Every member of VALID_STATUSES in src/sheet/schema.py is in how-to-journey.md \"Status state machine\" + supported by the FilterBar status pill list in webapp/src/components/FilterBar.tsx.
5. **API endpoints.** Every route registered in src/web/api.py (GET/POST/PATCH) is in how-to-journey.md \"API endpoints\" table with correct auth annotation (open vs owner).
6. **Scripts.** Every file under scripts/ (.py and .sh) is in how-to-journey.md \"Scripts\" table with one-line description.
7. **Webapp features.** Major visible components (CardStack swipe view, JobDetail drawer, ProcessorButton FAB, BotHealthChip, PreviewBanner, RetryErroredBanner, view toggle, filter pills) are mentioned in how-to-journey.md \"Two view modes\" / \"Card view gestures\" / \"Animations + interaction model\" sections.
8. **Watchdog protocol.** bot_health.py constants (HEARTBEAT_STALE_SECONDS, RESTART_COOLDOWN_SECONDS, RESTART_LOG path) match how-to-journey.md Logs section + decisions.md ADR-020/021 narrative.
9. **PWA assets.** Every file in webapp/public/ has a corresponding mention in tasks.md Step 27 + how-to-journey.md \"Install as a phone app\" section.
10. **Build steps.** Every committed feature (check git log --oneline since the last tasks.md step) has a corresponding Step in tasks.md OR an explanation of why it was an in-step refinement of an earlier step.
11. **ADRs.** Every non-obvious choice that future-self might re-litigate (e.g. new auth model, new source type with TOS implications, new background task) has a corresponding ADR entry in decisions.md.
12. **Failure modes.** Newly-discovered failure modes from recent commits should appear in how-to-journey.md Troubleshooting / Failure-modes table."

if [ "$MODE" = "check" ]; then
  echo "🔍 Auditing docs vs code (read-only)..."
  PROMPT="$PROMPT_BASE

Output strictly in this format (no surrounding prose):

DRIFT_DETECTED: yes | no

If yes, list each drift item on its own line:
- <doc_file>: <missing or wrong>: <one-line description>

If everything is in sync, output 'DRIFT_DETECTED: no' alone."

  cd "$REPO_ROOT"
  RESULT=$(claude -p "$PROMPT" --output-format text --allowedTools Read 2>&1)
  echo "$RESULT"
  if echo "$RESULT" | grep -qi "DRIFT_DETECTED: yes"; then
    echo
    echo "❌ Drift detected. Run '$0 apply' to have claude write the updates."
    exit 1
  fi
  echo "✅ Docs and code in sync."

elif [ "$MODE" = "apply" ]; then
  echo "✏️  Applying doc updates via claude (this may modify $DOCS_DIR/...)"
  PROMPT="$PROMPT_BASE

Apply the necessary updates to bring docs into sync with code.

Rules:
- vision.md and how-to-journey.md: edit anywhere it has drifted (rewrite sections as needed; PRESERVE the overall structure and headers).
- tasks.md: ONLY append a new build step at the end (e.g. '# Step N+1 — <title>'). Do NOT edit prior steps. Use the established format: numbered subtasks (N.1, N.2, ...), each ending with a smoke checklist.
- decisions.md: ONLY append a new ADR (next sequential ADR-NNN number). Do NOT edit prior ADRs. Use the established format: Context / Decision / Reasoning / Trade-offs accepted / Consequence.
- Do NOT modify job-intake-design-v1-original.md.
- Do NOT introduce new docs files.
- After editing, write a one-line summary of changes to $DOCS_DIR/.docs-sync-last.txt.

Use Read,Edit,Write tools."

  cd "$REPO_ROOT"
  claude -p "$PROMPT" --output-format text --allowedTools Read,Edit,Write
  echo
  if [ -f "$DOCS_DIR/.docs-sync-last.txt" ]; then
    echo "📝 $(cat "$DOCS_DIR/.docs-sync-last.txt")"
  fi
  echo
  echo "Done. Run \`git diff docs/\` and commit if it looks right."
fi
