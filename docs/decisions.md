# Architecture Decisions

*A log of non-obvious choices made during the build, with reasoning so future-you (or a collaborator) can reconstruct the why. New decisions go at the bottom.*

> Sibling docs:
> - [`vision.md`](./vision.md) — current state + 10x roadmap
> - [`how-to-journey.md`](./how-to-journey.md) — operational guide
> - [`tasks.md`](./tasks.md) — chronological build log
> - [`sources-roadmap.md`](./sources-roadmap.md) — funnel diversity audit + tiered plan to broaden sources
> - [`job-intake-design-v1-original.md`](./job-intake-design-v1-original.md) — frozen v1 design

---

## ADR-001 · Google Sheet as the database
**Date:** 2026-04-26 · **Status:** Accepted

**Context.** Daily inflow of ~50-150 deduped rows; weekly user editing of `status`, `notes`, `applied_at`, `response_at`. Conventional choice would be Postgres or SQLite.

**Decision.** Google Sheet (single tab `Jobs`) is the source of truth.

**Reasoning.**
- Zero ops, free, works on any device. No backups to manage; Sheet's native version history is the audit log.
- The user's filter views (saved sort + tag combinations) become first-class application state. With Postgres I'd be reinventing the filter UI from scratch.
- Exportable forever: CSV / Excel / API anytime, no migration drama.
- Multi-device editing is trivial — phone, laptop, browser, all the same. Conflicts are rare because mutations are point edits, not transactions.
- The review workflow (eyeball cards, mark `tailor`/`apply`/`reject`) is fundamentally a spreadsheet workflow. The webapp is a *better* view over the Sheet, not a replacement.

**Trade-offs accepted.**
- Sheets API caps at 60 reads/min/user. Mitigated by 30s in-memory snapshot cache (ADR-009).
- Schema migrations require live edits to the Sheet (no `ALTER TABLE`). Mitigated by `migrate.py` framework + version cell at A1 (ADR-008).
- No transactions. Mitigated by per-row file locks for the only multi-step write (processor) — ADR-010.

**Consequence.** Any future "move to Postgres" proposal needs to demonstrate a concrete user-facing failure that Sheets caused, not generic "real databases scale better" claims.

---

## ADR-002 · Local-first server + Cloudflare Tunnel (vs hosted deploy)
**Date:** 2026-04-26 · **Status:** Accepted

**Context.** The webapp + Scout + Processor need to run somewhere. Options: laptop + tunnel, VPS, Fly.io / Render, Cloudflare Workers.

**Decision.** Run everything on the user's laptop. Expose the webapp via the existing `cloudflared` tunnel at `takejob.deepesh-engg.in`.

**Reasoning.**
- $0 hosting cost. The Claude API + GCP + Cloudflare are the only paid surfaces, and only one (Claude) bills usage.
- The system already needs the laptop awake for scout (which must hit the user's `~/bot/users/<chat_id>/base_resume.md` and the user's Google Sheet via the SA JSON kept in `~/secrets/`). Hosted servers would need to either ship those secrets or re-architect.
- The user already runs a `cloudflared` tunnel for `welog.deepesh-engg.in`; adding a second hostname to its ingress is one YAML line.
- Full control over which version is running, easy `tail -f` of logs, no SSH needed for debugging.

**Trade-offs accepted.**
- Laptop must be awake at 9am for Scout to fire. Mitigated by `caffeinate -dims` wrapping the launchd invocation.
- Single point of failure: laptop crashes = webapp down. Acceptable for a single-user tool; not acceptable if we ever multi-tenant.

**Consequence.** When the user is on a long trip / laptop dies, the webapp goes dark. Vision.md lists "deploy to Fly.io" as a tier-4 follow-up if this becomes a real friction.

---

## ADR-003 · OAuth user delegation for Drive uploads (not service account)
**Date:** 2026-04-26 · **Status:** Accepted

**Context.** Tailored PDFs need a clickable URL the user can open from any device. Local filesystem paths (e.g. `/Users/.../tailored/<id>.pdf`) aren't clickable from the browser-served webapp.

**Decision.** Upload PDFs to a user-owned Drive folder using OAuth user delegation (not the service-account key). One-time browser auth via `scripts/auth_drive.py`.

**Reasoning.**
- Service accounts have **0 GB Drive storage quota for personal Google accounts**. Even uploading to a folder shared with the SA fails with `storageQuotaExceeded` because the SA becomes the file owner.
- The `drive.file` scope plus user OAuth means the file is owned by the user → uses the user's normal Drive quota → no quota issue.
- One-time browser dance vs perpetual frustration.
- Empirical: SA upload returned 403 immediately on real test; OAuth flow worked first try and refresh token persists indefinitely.

**Trade-offs accepted.**
- One extra GCP setup step (OAuth Client ID + consent screen). Documented in `how-to-journey.md`.
- Token at `~/secrets/job-intake-oauth-token.json` is a credential; chmod 600 is mandatory.
- For Workspace users with Shared Drives, the SA path could work — but coding for two modes is more complexity than payoff. The `DriveClient.from_service_account()` path exists as a fallback but isn't used.

**Consequence.** Any future "use the SA for Drive" proposal needs to start with a Workspace Shared Drive in the setup steps.

---

## ADR-004 · Read-only public mode with token-gated mutations (vs Cloudflare Access)
**Date:** 2026-04-26 · **Status:** Accepted (interim)

**Context.** User wanted to share `takejob.deepesh-engg.in` with friends to demo the project, but mutations could let anyone burn Claude credits or vandalize the Sheet.

**Decision.** Server checks `WRITE_TOKEN` env var. If set, mutations require `Authorization: Bearer <token>` header or `?token=<token>` query param. Frontend stores the token in `localStorage` after one visit to the owner URL with `?token=…`. All other visitors get a labeled `Preview mode` banner with disabled action buttons.

**Reasoning.**
- Cloudflare Access is the right answer long-term (gates at the edge, no app-side code), but requires Zero Trust dashboard configuration and ~10 min of click-ops the user wanted to defer.
- A bearer-token-in-URL is friction-free for personal use: bookmark once on each device, never see it again. Strong enough for a personal demo where the URL is shared with friends, not posted publicly.
- Server enforces auth on the actual mutation endpoints, not just hides UI — so a curl with no token still gets 403.
- Frontend disables (rather than hides) action buttons in viewer mode so visitors see the affordance and understand the system.

**Trade-offs accepted.**
- Token in URL is visible in browser history / referrer headers. Acceptable risk for personal use; not acceptable if URL is ever embedded in a third-party page.
- If the token leaks, rotating means editing `.env` + service kickstart. No revocation list, no per-user tokens.

**Consequence.** Vision.md's tier-4 lists swapping to Cloudflare Access. Until then, every commit is reminded by `WRITE_TOKEN` in `.env.example` not being a placeholder for a real-world deploy.

---

## ADR-005 · No LLM for tagging, comp parsing, or exclusion matching
**Date:** 2026-04-26 · **Status:** Accepted

**Context.** Scout's hot path tags every row, parses comp, applies exclusions. Tempting to use an LLM for "smarter" tagging.

**Decision.** All of tagging, comp parsing, and exclude matching is regex + config rules. Zero LLM calls in the scout pipeline.

**Reasoning.**
- **Cost.** ~150 inserted rows/day × $0.001/call (small model) = $0.15/day, $4.50/month. Cheap but unbounded as scaling grows.
- **Determinism.** A regex either matches or doesn't. An LLM might tag the same JD differently on Monday vs Tuesday based on prompt drift, model updates, temperature.
- **Failure mode asymmetry.** Regex misses a comp range → `comp_unknown` (safe default, user can read the JD). LLM hallucinates a comp range → wrong number in the Sheet, user filters incorrectly. The first is recoverable; the second is dangerous.
- **Speed.** Whole scout run is ~30 seconds; an LLM-per-row would push it to minutes and add network dependency.
- **Auditability.** `tag_reasons` cell shows the exact keyword that fired. Try debugging "why did the LLM tag this comp_below" without that.

**Trade-offs accepted.**
- Regex misses ~28% of comp data (matches the natural disclosure rate). The remaining `comp_unknown` rows still sort/filter usefully.
- Tagging is opinionated — adding a new category requires editing `tag_rules.yaml`, not "asking the LLM nicely".
- Resume-match uses skill-overlap intersection, not embeddings. Vision.md's tier-2 lists embeddings as a future addition; explicitly *additive*, not replacement.

**Consequence.** LLM proposals need a concrete row where regex was wrong AND LLM would be reliably right, plus a budget proposal. The bar is high.

---

## ADR-006 · System A invoked as subprocess CLI, not Python import
**Date:** 2026-04-26 · **Status:** Accepted

**Context.** When the user marks `status=tailor`, the Processor needs to invoke System A's tailoring pipeline (tailor + critic + refinement + render). System A is TypeScript/Node; System B is Python.

**Decision.** Subprocess call: `node $SYSTEM_A_PATH/dist/cli-tailor.js --jd-path … --chat-id … --output-dir … --output-format json`. JSON line on stdout.

**Reasoning.**
- Cross-language. No shared runtime; no import is possible without an extra translation layer (e.g. PyO3 binding, gRPC service) that adds more complexity than the subprocess does.
- Subprocess + stdout JSON is the universal cross-language contract. Trivially debuggable: run the same command from terminal, get the same JSON.
- `cli-tailor.js` reuses the bot's exact tailoring functions (`runTailoring`, `runCritic`, `runRefinement`, `renderResumePdf`), so output quality is byte-identical to Telegram-driven runs.
- The CLI does not write to System A's SQLite DB — it's stateless, owned conceptually by System B's Sheet. Keeps the bot's job history clean.

**Trade-offs accepted.**
- Subprocess startup overhead (~500ms node boot per invocation). Negligible relative to 4 minutes of Claude tailoring.
- If System A's CLI surface changes, `tailor_bridge.py` is the one place to update. Already documented as such in System A's ADR-021.
- Errors cross the subprocess boundary as text — error classification is by string-matching stderr. Acceptable; there are only 4 error classes (timeout, auth, rate-limit, generic).

**Consequence.** Don't propose making this in-process unless the hop becomes a measurable bottleneck (it isn't; Claude is the bottleneck).

---

## ADR-007 · Two redundant watchdogs (jobintake + launchd) on the resume bot
**Date:** 2026-04-27 · **Status:** Accepted

**Context.** Resume-builder bot has a known failure mode: it occasionally hangs (Telegram polling stalls). Their existing watchdog is a launchd-driven bash script firing every 2 min.

**Decision.** Add a *second* watchdog inside our FastAPI process (60s poll, 5min cooldown, `_auto_watchdog_loop` background task). Both watchdogs read the same `~/bot/.heartbeat` file and use the same kill-then-`npm start` recipe.

**Reasoning.**
- Single watchdog is a single point of failure. If the launchd plist is unloaded (e.g. during macOS update), the bot can stay hung indefinitely.
- Both watchdogs are **idempotent**: `kill -INT` followed by `kill -KILL` followed by `nohup npm start` works whether the bot is alive, dead, or already being killed.
- Cooldowns prevent thrashing: ours is 5 min between auto-restarts; their launchd is 2 min between *checks*. The races are absorbed by the kill-then-start being safe.
- Bonus: surfaces a "Bot health" chip in the webapp header — the user sees state at a glance and has a manual "Restart" button without SSHing.

**Trade-offs accepted.**
- Two systems can race. Mitigated by the unified restart log (ADR-020) so the user can attribute every restart event.
- Code duplication between the bash watchdog and `bot_health.py`. Acceptable: the protocols are simple (heartbeat file, kill PID, spawn), and rewriting one to call the other adds coupling.

**Consequence.** If our `bot_health.py` ever significantly diverges from `scripts/watchdog.sh` (e.g. different stale threshold), document the divergence here.

---

## ADR-008 · Schema version cell at `Jobs!A1`
**Date:** 2026-04-26 · **Status:** Accepted

**Context.** Sheet schema (column count + order + headers) will evolve. Both Scout and Processor need to refuse to run against an incompatible Sheet rather than silently corrupt rows.

**Decision.** Cell `Jobs!A1` holds the literal string `schema_version: N`. On startup, both scripts compare against `SCHEMA_VERSION` constant in `sheet/schema.py` and refuse to run if Sheet is newer.

**Reasoning.**
- Simplest possible invariant — a single cell read on startup costs one API call.
- A separate "metadata" tab adds complexity for no gain.
- Header row at row 2 (frozen) means the schema marker doesn't conflict with column names.
- `migrate.py` framework walks `MIGRATIONS[from_version]` chain forward to target. Per-version migration is a small function (e.g. `_v1_to_v2` adds the `resume_match` column).

**Trade-offs accepted.**
- Migrations are forward-only. No "rollback to v1" — if the new code is wrong, you fix the code, not the schema.
- The cell is editable by hand (no protection range). User could break the version marker. Acceptable: this is a personal tool, not a multi-user service.

**Consequence.** Bumping `SCHEMA_VERSION` is a deliberate act: bump the constant, write the migration, update the column lists. Done in step 12 (v1 → v2 added `resume_match`).

---

## ADR-009 · 30-second TTL cache on Sheet snapshot
**Date:** 2026-04-26 · **Status:** Accepted

**Context.** Webapp polls multiple endpoints (jobs list, stats, process status, bot health). Sheets API caps at 60 reads/min/user. Hitting it on every UI request would saturate quickly.

**Decision.** `web/cache.py` — single-key TTL cache for the full Jobs snapshot. 30s TTL. PATCH endpoints invalidate the cache so the next read is fresh.

**Reasoning.**
- 30s is the "good enough" sweet spot — UI feels live; quota is healthy.
- Single-key cache (vs per-row) keeps the cache logic trivially correct.
- Invalidate-on-write means user-driven mutations show up instantly; only multi-tab cross-mutation has up-to-30s lag.
- Cache lives in the FastAPI process. If we ever move to multi-worker uvicorn, switch to a shared cache (Redis) — but for single-process serving, in-memory is the right answer.

**Trade-offs accepted.**
- Stats can lag up to 30s after a row's status changes from another tab. Surfaced in the UI as "cached Ns ago" so user understands.
- Concurrent writes from multiple tabs work but the second one sees the first's effect only after cache expiry. Acceptable for single-user.

**Consequence.** Don't add aggressive client-side polling without first considering whether the cache layer absorbs it.

---

## ADR-010 · Per-row file locks for the processor
**Date:** 2026-04-26 · **Status:** Accepted

**Context.** Processor sweeps `status=tailor` rows and tailors each. Two concurrent processor invocations (e.g. user clicks "Process queue" twice) would double-tailor the same row.

**Decision.** Per-row file lock at `data/locks/<safe_id>.lock`. Acquired before tailoring, released on completion. Orphaned locks (>1h old) auto-cleared.

**Reasoning.**
- File locks survive process crashes — if the processor SIGKILLed mid-tailor, the orphan-clear policy releases the lock on the next run.
- Per-row (not global) means we *could* parallelize processing later (one process per CPU). Today the processor is sequential, but the lock granularity supports the future.
- 1-hour orphan threshold is generous: real tailoring takes ~5 min; if a lock is older than 1h, the tailor either crashed or hit Claude API timeout.

**Trade-offs accepted.**
- Filesystem-based locks are not portable to a hosted multi-server deploy. Mitigated: today we're single-server (ADR-002).
- A genuinely-stuck tailor for >1h would let a second invocation start processing the same row. Empirically hasn't happened.

**Consequence.** If we ever distribute the processor across multiple machines, swap file locks for a shared coordinator (Redis SETNX, Postgres advisory locks).

---

## ADR-011 · `is_strong()` requires `count >= 3` overlapping skills
**Date:** 2026-04-26 · **Status:** Accepted, tunable as constant

**Context.** Resume-aware match needs a binary "strong enough to suggest the user could compete here" signal — used as the `resume_strong` tag and a key filter view.

**Decision.** Threshold = 3 unique skill-vocabulary tokens that appear in BOTH the user's resume and the JD body.

**Reasoning.**
- Empirically tuned on initial 395-row dataset: count=3 caught real matches without false-positives from generic single-keyword overlap (e.g. "python" alone is too noisy).
- Count is more robust than fraction. A short JD with 4 skills total + user matching 4 → score 1.0 looks identical to a long JD with 100 skills + user matching 4 → score 0.04. Both cases the user can do the role; the count is what matters.
- The webapp also surfaces the score (0.0–1.0) as a sort key, so users with stronger overlap bubble to the top within the resume-strong set.

**Trade-offs accepted.**
- Empirical only — would benefit from labeled data to tune properly. Today it's "the user has eyeballed it and it looks right."
- Some genuinely-good roles where the JD is sparsely worded miss the threshold (e.g. "Senior Engineer · Bengaluru · backend" with 2 skill mentions). Mitigated by the user noticing in the broader filter view that the score is high but `resume_strong` is missing.

**Consequence.** Bumping the threshold to 4 would tighten the funnel; lowering to 2 would broaden but introduce noise. Document the change with rationale before adjusting.

---

## ADR-012 · Stack tag requires `section_hint` anchor
**Date:** 2026-04-26 · **Status:** Accepted

**Context.** Stack tags (`stack_typescript`, `stack_python`) drive a primary filter. Naive keyword match (e.g. anywhere "Python" appears) overfires on JDs that mention a stack in passing ("we use Python for scripts but the role is Java").

**Decision.** Stack tag fires only when the keyword appears under (or near, ±400 chars before) a section header from `section_hint`: `["required", "must have", "requirements", "you have", "you bring", "what we look for"]`.

**Reasoning.**
- High-precision filter is more useful than high-recall. A false positive (irrelevant role tagged Python) wastes the user's morning review.
- The 400-char window covers typical pre-bullet preamble in JDs without overflowing into adjacent sections.
- Section-anchored matching makes stack tags genuine commitments by the JD, not incidental mentions.

**Trade-offs accepted.**
- JDs that don't structure their requirements with one of the anchor phrases miss tagging entirely. Empirically rare in tech postings; the standard "Requirements" section is near-universal.

**Consequence.** Adding a new stack (e.g. Go, Rust) requires picking keyword aliases AND deciding if section_hint matching is right (it usually is).

---

## ADR-013 · Universal `linkedin:ALL:<urn>` ID across LinkedIn searches
**Date:** 2026-04-27 · **Status:** Accepted

**Context.** LinkedIn source supports multiple search queries (Bengaluru senior backend, India remote AI, founding engineer, etc.) — each is a separate "board" in `boards.yaml`. The same job appears in multiple search results.

**Decision.** Posting `id` is `linkedin:ALL:<urn_numeric>`, **not** `linkedin:<board_id>:<urn>`.

**Reasoning.**
- LinkedIn's URN (`urn:li:jobPosting:1234567890`) is globally unique per job. Same URN in two searches = same job.
- If we keyed by board, the same job from "Bangalore senior backend" and "Bangalore staff swe" would be inserted twice — wasted Sheet space + duplicated reviewing burden.
- Universal ID dedups them in scout's id-cache check before any fuzzy-match needs to fire.

**Trade-offs accepted.**
- Information loss: the row's `board_id` field still records *which* search surfaced it first, but if the same job was found by 3 searches, only the first attribution is preserved.
- Other source clients use board-specific IDs (`greenhouse:anthropic:5544332`); this is a divergence. Documented in `linkedin.py` docstring.

**Consequence.** This pattern works because LinkedIn URN is universal. Don't apply the same shape to sources where the posting ID is board-scoped (Greenhouse posting IDs are unique within a board, not across boards).

---

## ADR-014 · 7-day first-run cutoff
**Date:** 2026-04-26 · **Status:** Accepted

**Context.** First Scout run on a fresh Sheet has no dedup cache. Boards typically expose all open postings, including months-old ones. Without a cutoff, the Sheet floods with hundreds of stale rows the user doesn't care about.

**Decision.** On first run (sqlite cache empty AND Sheet has zero rows), only insert postings where `posted_at >= today - 7 days`.

**Reasoning.**
- 7 days is enough to catch what's freshly listed without polluting with months-old roles.
- Subsequent runs don't apply the cutoff — once dedup is hot, the user sees only what's actually new since last run.
- Aggregator sources (yc_waas, hn_hiring) without reliable `posted_at` use `discovered_at` as a proxy; first run for those still pulls a chunk, but the user can mark `skip` quickly.

**Trade-offs accepted.**
- A genuinely-old-but-still-open role posted 8+ days before bootstrap is missed. Acceptable: the user will see it on the next round when the company refreshes the listing, or they can find it manually.

**Consequence.** Don't remove the first-run check unless the dedup story changes.

---

## ADR-015 · Single-port deployment (FastAPI serves React `dist/`)
**Date:** 2026-04-26 · **Status:** Accepted

**Context.** Webapp + API need to be reachable. Could run Vite dev server (`:5173`) + FastAPI (`:8090`) as two services + reverse proxy.

**Decision.** Production: FastAPI on `:8090` mounts `webapp/dist/` as static + SPA-fallback. Single port, single tunnel ingress, single origin.

**Reasoning.**
- One Cloudflare Tunnel ingress entry vs two.
- No CORS — webapp and API share origin.
- One `launchd` plist to manage.
- The Vite dev server is for development only (`npm run dev`); production-build → static serve is faster (no compile-on-request, smaller bundles).

**Trade-offs accepted.**
- Frontend changes require `npm run build` to ship. Mitigated by Vite's ~2s build time.
- No HMR in production. Acceptable — production is the user's daily-use surface, not active development.

**Consequence.** Don't propose splitting into separate API + frontend services without a real reason (e.g. CDN-hosted frontend with a remote API).

---

## ADR-016 · No per-card animations in the list (fixed the "wave" shake bug)
**Date:** 2026-04-27 · **Status:** Accepted

**Context.** First webapp version had `motion.div` with `layout` prop + `AnimatePresence mode="popLayout"` + staggered `delay` on each card. Filter changes triggered a visible "wave" cascade; user described it as "vibration" and a blocker.

**Decision.** Strip all per-card animation. List renders as a plain `<div>` of `<JobCard>` siblings. Hover/active feedback stays as CSS transitions on the card itself.

**Reasoning.**
- `layout` prop runs FLIP on every reorder. With sort changing, every card animates simultaneously → visible jitter.
- `AnimatePresence` mode="popLayout" treats every filter change as exit-then-enter for each row. Combined with staggered delay = the cascade.
- Card height is content-driven (some have tag rows, some don't). Layout animations on variable-height items make the jitter worse.
- The right place for animation is *between view states*, not within them. The detail drawer slide, status-toast, and processor button transition are all kept.

**Trade-offs accepted.**
- No "satisfying entrance" animation for new rows from a refetch. Mitigated by `placeholderData: keepPreviousData` (ADR-017) — old list stays visible during refetch, new data swaps in atomically.

**Consequence.** Don't reintroduce `layout` or `AnimatePresence` to the JobsList without explicit user feedback that the lack of animation feels wrong.

---

## ADR-017 · `keepPreviousData` on every list-style query
**Date:** 2026-04-27 · **Status:** Accepted

**Context.** TanStack Query's default behavior on a query-key change (filter change → new params) is to clear data and re-fetch. UI shows a blank/loading state during refetch.

**Decision.** Every query that drives a visible list uses `placeholderData: keepPreviousData`.

**Reasoning.**
- The user's mental model is "I changed the filter; show me the result". A blank flash mid-flight breaks that.
- The new data swaps in atomically when ready; the user sees the old list right up until the new list takes its place.
- Combined with no per-card animation (ADR-016), filter changes feel instant — even when the underlying refetch takes 100-200ms.

**Trade-offs accepted.**
- For the first ~200ms after a filter change, the visible list is "stale" relative to the filters. Mitigated by the `isFetching` indicator (small pulsing dot in the count line) so the user knows new data is on the way.

**Consequence.** Don't disable this option without considering whether the resulting flash is acceptable.

---

## ADR-018 · `architect` in `application_eng` default fallback
**Date:** 2026-04-27 · **Status:** Accepted

**Context.** First-run analysis showed only 19 of 71 senior-tagged roles also got `application_eng`. Of the 52 missing, 49 were titled "Architect" (mostly Anthropic Applied AI Architect, plus various Solutions Architect titles).

**Decision.** Added `architect` to `_DEFAULT_ENG_HINTS` in `tag.py`'s application_eng fallback. So a role with `architect` in title that doesn't match `wrong_discipline` exclusions gets `application_eng=default:architect` tag.

**Reasoning.**
- "Architect" in modern SaaS = senior IC engineering role with extra responsibility, not the literal building-architect or pure-management variant.
- Anthropic Applied AI Architect, Confluent Solutions Architect, etc. are legitimate IC eng roles the user might apply to.
- The user's resume (Tech Lead / Founding Engineer) maps cleanly to architect-style positioning.

**Trade-offs accepted.**
- Pure pre-sales "Solutions Architect" roles get tagged too (less code-focused). Mitigated by the user's review process — they read the JD before marking `tailor`.

**Consequence.** If the user starts seeing too many non-IC architect roles, add specific exclusions (e.g. "enterprise architect", "data architect" if they're consistently wrong shape).

---

## ADR-019 · `comp_ok` is a sort signal, not a required filter
**Date:** 2026-04-27 · **Status:** Accepted

**Context.** Original "Ultimate" filter view chained 6 ANDs including `comp_ok`. Result: 3 rows out of 395. User flagged the discrepancy ("3 jobs at 1 company can't be right").

**Decision.** Removed `comp_ok` (and `enjoy_eligible`) from required-AND lists in default filter views. They're surfaced as sort signals (rows with `comp_ok` rank higher) and visible as tag chips, but don't gate the visible set.

**Reasoning.**
- `comp_ok` is **coverage-bound**, not quality-bound. ~28% of JDs disclose comp publicly. Filtering by comp_ok hides 72% of real matches just because the JD didn't happen to mention salary.
- A user reviewing 10 senior IC roles can eyeball comp manually for the ones without explicit signals. Filtering it out at scout time loses information.
- The right place for "must have comp" is a per-search question ("today I want to triage only comp-disclosed roles"), accessible via a dedicated filter view (#6 "Comp-disclosed"), not as a default.

**Trade-offs accepted.**
- The "Ultimate shortlist" view is gone. Replaced by view #2 "Top picks (resume-strong senior IC)" which surfaces 80+ rows the user can actually browse.

**Consequence.** Default filter views chain at most 3 ANDs. New views need to justify why a 4th AND won't reduce the visible set to single-digit rows.

---

## ADR-020 · Cross-watchdog unified restart log at `~/bot/logs/restarts.log`
**Date:** 2026-04-27 · **Status:** Accepted

**Context.** Two watchdogs (jobintake's FastAPI loop + resume-builder's launchd bash script) both restart the bot on detected hangs. User couldn't tell which fired without grepping two separate log files with different formats.

**Decision.** Both watchdogs append BEGIN/END markers to a single shared log at `~/bot/logs/restarts.log`. Format: `[ISO_TS] BEGIN triggered_by=<id> reason=<...> pids_to_kill=[...]` followed by an END line with the new PIDs.

**Reasoning.**
- Single `tail -f` answers "who restarted the bot, when, and why".
- Format is grep-friendly: `grep triggered_by=launchd-watchdog restarts.log` for one source's history.
- Includes a `FREED port=8787 killed_listeners=[...]` line when the EADDRINUSE port-cleanup fires (ADR-021).

**Trade-offs accepted.**
- Two writers to one file. POSIX append is atomic for small writes (<PIPE_BUF), so no locking needed for our line lengths.
- No log rotation. Acceptable for personal use; if it grows past 10MB someday, add `logrotate`.

**Consequence.** Both watchdogs must continue writing in the same format. Documented in `tasks.md` step 22 + the prompt I wrote for the resume-builder side.

---

## ADR-021 · Pre-spawn port-8787 cleanup before `npm start`
**Date:** 2026-04-27 · **Status:** Accepted

**Context.** Caught the resume-builder bot in a 2-minute restart loop. Logs showed `Error: listen EADDRINUSE: address already in use 127.0.0.1:8787`. The bot's internal health-endpoint server couldn't bind because a previous bot's child process still held the port.

**Decision.** Both watchdogs (job-intake side via `_free_port(8787)` in `bot_health.py`, resume-builder side via `lsof -tiTCP:8787 | xargs kill -9`) explicitly clear port 8787 before spawning the new bot.

**Reasoning.**
- Root cause was correct: SIGINT to the parent doesn't always reap children fast enough; the OS may keep the port bound for a moment.
- Killing whoever holds the port directly is the cheapest reliable break in the loop.
- Logged as `FREED port=8787 killed_listeners=[...]` to the unified restart log so root-cause is visible if it recurs.

**Trade-offs accepted.**
- We're SIGKILL'ing processes by port-binding, not by name match. If something legitimately *unrelated* binds 8787, we kill it. Acceptable: only the resume-builder bot binds 8787 in this user's setup.

**Consequence.** A proper fix would be in resume-builder's health-endpoint server (`SO_REUSEADDR` or use port 0 / OS-assigned), so the OS releases the port immediately on child exit. Listed in vision.md as a future improvement; today's port-kill is the pragmatic patch.

---

## ADR-022 · Launchd plist always injects fnm node path; subprocess code re-injects too
**Date:** 2026-04-27 · **Status:** Accepted

**Context.** Webapp runs as `com.user.jobintake.web` launchd service.
Its `/api/process` endpoint spawns the processor as a subprocess; that
subprocess calls `node dist/cli-tailor.js`. The user's node is managed
by fnm at `~/.local/share/fnm/aliases/default/bin/node` — NOT in any
default macOS PATH.

When the user triggered processor from the webapp, the subprocess
inherited the launchd-defined PATH (which lacked the fnm path) and
every tailor failed with `bridge_error: No such file or directory: 'node'`.
8 rows landed in status=error before we caught it.

**Decision.** Two layers, both required:

1. **Plist `EnvironmentVariables.PATH` includes the fnm path** as the
   first segment. So any subprocess of the webapp inherits a sane PATH.
2. **`web/api.py:process_queue` explicitly augments PATH** when spawning
   the processor subprocess: `env={..., "PATH": fnm_node + ":" + os.environ["PATH"]}`.

**Reasoning.** The plist alone is fine on this user's machine, but a
co-maintainer (or this user on a fresh deploy) might forget the plist
edit. The api.py code is the safety net: even with a bare plist PATH,
the subprocess always finds node. Belt + suspenders.

**Trade-offs accepted.**
- Hard-coded path `~/.local/share/fnm/aliases/default/bin` is fnm-
  specific and macOS-flavored. If we ever target Linux + nvm, this
  needs generalization (e.g. `which node` lookup at startup).

**Consequence.** This pattern — explicitly compose PATH for child
processes — should be applied to any other launchd-spawned background
service that needs to reach for tools outside the system PATH.

---

## ADR-023 · Mirror resume-builder's docs-sync protocol byte-for-byte
**Date:** 2026-04-27 · **Status:** Accepted

**Context.** Resume-builder ships a `scripts/docs-sync.sh` that uses the
`claude` CLI to (a) audit docs vs code surface (`check` mode, read-only)
and (b) write the updates back (`apply` mode, edit). Two npm aliases
expose it: `docs:check` and `docs:sync`. The protocol catches drift
that linters can't (e.g. "every command in `bot.command(...)` should be
in `/help` table"). User asked us to mirror it here.

**Decision.** Add `scripts/docs-sync.sh` with identical structure to the
resume-builder original — same `--check`/`--apply` modes, same
`claude -p` invocation, same output format (`DRIFT_DETECTED: yes|no`
followed by a per-item list). Exposed via a `Makefile` (we're Python +
uv, no npm) with `make docs-check` / `make docs-sync` targets that map
1:1 to resume-builder's npm scripts.

**Reasoning.**
- **Cross-repo familiarity.** Working on either repo, the workflow is
  identical: `make docs-check` (or `npm run docs:check` in the other).
  No mental switch cost.
- **Append-only invariants enforced in the prompt.** `tasks.md` and
  `decisions.md` say "do NOT edit prior steps/ADRs" right in the prompt
  Claude reads. `job-intake-design-v1-original.md` is explicitly off-limits.
- **The audit list is project-specific.** 12 numbered checks tailored
  to job-intake's surface (sources registered in 3 places, env keys in
  config.py + .env doc, status enum in schema + FilterBar pills, every
  webapp component referenced, watchdog constants matching ADR-020/021,
  PWA assets in tasks.md Step 27, etc.). Different from resume-builder's
  6 checks.
- **Cost is bounded** (~$0.05-0.20 check, ~$0.30-0.50 apply). Worth it
  on every feature ship.

**Trade-offs accepted.**
- Hard dependency on `claude` CLI being authenticated. If the user's
  Claude Code session expires, the script errors with a useful message.
- LLM judgement varies run-to-run. Two checks back-to-back can produce
  slightly different drift lists. Acceptable: the high-signal items are
  consistent; the noise is in marginal cases.
- The audit list lives in the script — when we add a new doc-relevant
  surface (e.g. a new ADR family), we update the prompt's check list.
  Documented as "if you add X, update the audit checks in scripts/docs-sync.sh".

**Consequence.** Doc drift is now a `make docs-check` away from being
caught. Mirrors resume-builder's exact UX so a contributor (or future-
self after a long pause) doesn't have to remember which repo uses which
command — both projects use the same `check`/`sync` verbs.
