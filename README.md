# job-intake

Daily job-board scraper that feeds a Google Sheet, scores postings against
your resume, and exposes a polished webapp for triage + tailoring +
applying. Single-user, local-first, free except for Claude API on the
tailoring side.

**Live:** https://takejob.deepesh-engg.in (read-only public view)

## Docs

- **[Vision](./docs/vision.md)** — what the system is now, 10x roadmap
- **[How-to Journey](./docs/how-to-journey.md)** — operational guide
- **[Build log](./docs/tasks.md)** — chronological build steps
- **[Decisions](./docs/decisions.md)** — ADR log of non-obvious choices
- **[Original v1 design](./docs/job-intake-design-v1-original.md)** — frozen for history

## Architecture (one-liner)

```
Scout (cron) → Google Sheet (DB) → FastAPI + React (webapp)
                       │
                       └─ Processor → System A's cli-tailor.js → Drive PDF
```

## Sibling project

[`resume-builder`](https://github.com/deepesh-01/resume-bot) — System A,
the Telegram-based resume tailoring bot. We invoke it as a subprocess
via `dist/cli-tailor.js`.

## Stack

Python 3.11 (uv), gspread, FastAPI, structlog · React + Vite + TypeScript +
Tailwind + framer-motion · Cloudflare Tunnel · Google Sheets + Drive APIs.
