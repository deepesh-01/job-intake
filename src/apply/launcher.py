"""LinkedIn Easy Apply co-pilot.

Launches a headful Chromium window pointed at a LinkedIn job URL, waits for
the user to log in (one-time per machine — session persists in
`data/playwright/linkedin/`), clicks "Easy Apply", then walks through the
modal autofilling everything it can match. The user handles file uploads,
captchas, and clicking Next/Submit.

Design choices:
- **Best-effort, not 100%.** ATS forms are inconsistent — try multiple
  selectors per field, skip what we can't match, never crash.
- **No auto-submit.** The user always clicks the final Submit button so
  they can review what was filled.
- **No headless.** This is a co-pilot, not a bot — visible Chromium so the
  user can see what's happening and intervene at any step.
- **Persistent context per host.** Each ATS gets its own profile dir under
  `data/playwright/<host>/` — cookies, localStorage, formauth all cached.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import (
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeout,
    sync_playwright,
)

from apply.profile import Profile, load_profile

log = logging.getLogger("apply.launcher")
log.setLevel(logging.INFO)

PLAYWRIGHT_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "playwright"
LINKEDIN_USER_DIR = PLAYWRIGHT_DATA_DIR / "linkedin"

# A real-world UA — Playwright's default UA shouts "HeadlessChrome" which
# LinkedIn 100% blocks. Match a current macOS Chrome build instead.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)


def run_linkedin_easy_apply(
    job_url: str,
    job_role: str = "",
    job_company: str = "",
    *,
    profile: Profile | None = None,
) -> dict[str, Any]:
    """Open a LinkedIn job URL, click Easy Apply, autofill the modal.
    Returns a dict with `submitted` flag + `filled_fields` list. Blocks
    until the user closes the browser window.
    """
    profile = profile or load_profile()
    LINKEDIN_USER_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        ctx = _launch(p, LINKEDIN_USER_DIR)
        page = ctx.new_page()
        try:
            page.goto(job_url, wait_until="domcontentloaded", timeout=30_000)
        except PlaywrightTimeout:
            log.warning("initial nav timeout; user may need to log in")

        # Step 1: ensure user is logged in. LinkedIn redirects to /login if not.
        _wait_for_login(page)

        # Step 2: click "Easy Apply" — there are several variants of the button.
        easy_apply_clicked = _click_easy_apply(page)
        if not easy_apply_clicked:
            log.info("no Easy Apply button — leave page open for manual apply")
            _wait_until_closed(page)
            return {"submitted": False, "reason": "no_easy_apply", "filled_fields": []}

        # Step 3: autofill the multi-step modal.
        filled = _autofill_easy_apply_modal(page, profile, job_role, job_company)

        # Step 4: hand control back. User clicks Submit, reviews answers, etc.
        result = _wait_until_closed(page)
        result["filled_fields"] = filled
        return result


def _launch(p: Playwright, user_data_dir: Path) -> Any:
    return p.chromium.launch_persistent_context(
        user_data_dir=str(user_data_dir),
        headless=False,
        viewport={"width": 1280, "height": 900},
        user_agent=USER_AGENT,
        # Surface as real Chrome to defeat the cheap automation checks.
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
        ],
        ignore_default_args=["--enable-automation"],
    )


def _wait_for_login(page: Page, max_seconds: int = 600) -> None:
    """Block until the page navigates off /login. Gives the user up to 10
    minutes to type credentials + handle 2FA."""
    deadline = time.time() + max_seconds
    while time.time() < deadline:
        url = page.url
        if "/login" not in url and "/checkpoint" not in url:
            return
        time.sleep(1.0)
    log.warning("login wait timed out after %ss — proceeding anyway", max_seconds)


def _click_easy_apply(page: Page) -> bool:
    """Try each known selector for the Easy Apply button. Returns True on
    successful click."""
    selectors = [
        "button.jobs-apply-button",
        "button[aria-label*='Easy Apply']",
        "button:has-text('Easy Apply')",
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if btn.count() == 0:
                continue
            btn.scroll_into_view_if_needed(timeout=3000)
            btn.click(timeout=4000)
            page.wait_for_timeout(1500)  # let modal animate in
            log.info("clicked easy-apply via %s", sel)
            return True
        except Exception as e:  # noqa: BLE001 — best-effort
            log.debug("selector %s failed: %s", sel, e)
    return False


def _autofill_easy_apply_modal(
    page: Page,
    profile: Profile,
    role: str,
    company: str,
) -> list[str]:
    """Walk the modal's text inputs / selects / radios, filling each from
    the profile if we recognise the question. Returns names of filled fields.

    LinkedIn's modal lazy-renders steps; we handle the CURRENT step only and
    leave Next/Back to the user.
    """
    filled: list[str] = []

    # Try a few seconds for the modal to settle.
    try:
        page.wait_for_selector(
            "div.jobs-easy-apply-modal, div[role='dialog']",
            timeout=10_000,
        )
    except PlaywrightTimeout:
        log.warning("modal not found")
        return filled

    # Identity defaults — usually pre-filled by LinkedIn but stamp anyway.
    _try_fill(page, [
        ("input[name='firstName']", profile.get("identity", "first_name")),
        ("input[name='lastName']", profile.get("identity", "last_name")),
        ("input[name*='email']", profile.get("identity", "email")),
        ("input[name*='phone']:not([name*='country'])", profile.get("identity", "phone")),
    ], filled)

    # Walk all visible text inputs / textareas / selects in the modal — match
    # by the question label (LinkedIn renders each Q as a div containing both
    # the label and the input, so we can introspect by text).
    questions = page.locator("div.jobs-easy-apply-form-section__grouping, div[data-test-form-element]")
    n = questions.count()
    log.info("found %d question groups", n)
    for i in range(n):
        try:
            grp = questions.nth(i)
            label_text = ""
            try:
                label_text = grp.locator("label, span.fb-form-element-label__text").first.inner_text(timeout=1500).strip()
            except Exception:  # noqa: BLE001
                continue
            if not label_text:
                continue
            answer = _resolve_answer(label_text, profile, role=role, company=company)
            if answer is None:
                log.info("unrecognised question, skipping: %s", label_text[:80])
                continue

            # Try input → textarea → select for this group, in that order.
            if grp.locator("input[type='text'], input[type='tel'], input[type='email']").count() > 0:
                inp = grp.locator("input[type='text'], input[type='tel'], input[type='email']").first
                inp.fill(str(answer), timeout=3000)
                filled.append(label_text)
            elif grp.locator("textarea").count() > 0:
                inp = grp.locator("textarea").first
                inp.fill(str(answer), timeout=3000)
                filled.append(label_text)
            elif grp.locator("select").count() > 0:
                sel = grp.locator("select").first
                # Select by label fallback to value
                try:
                    sel.select_option(label=str(answer), timeout=2000)
                except Exception:  # noqa: BLE001
                    try:
                        sel.select_option(str(answer), timeout=2000)
                    except Exception:  # noqa: BLE001
                        log.debug("select failed for %s", label_text[:60])
                        continue
                filled.append(label_text)
            elif grp.locator("input[type='radio']").count() > 0:
                # Radios — match the answer to a radio's parent label text.
                radios = grp.locator("input[type='radio']")
                clicked = False
                for j in range(radios.count()):
                    radio = radios.nth(j)
                    rid = radio.get_attribute("id") or ""
                    label = grp.locator(f"label[for='{rid}']").first if rid else None
                    label_str = label.inner_text(timeout=1000).strip() if label and label.count() else ""
                    if label_str.lower() == str(answer).lower():
                        radio.check(timeout=2000)
                        clicked = True
                        break
                if clicked:
                    filled.append(label_text)
        except Exception as e:  # noqa: BLE001
            log.debug("question %d failed: %s", i, e)
            continue

    return filled


# ── question → answer resolver ────────────────────────────────────────


def _resolve_answer(question: str, p: Profile, *, role: str, company: str) -> Any:
    q = question.lower()

    # Identity
    if "first name" in q:
        return p.get("identity", "first_name")
    if "last name" in q or "surname" in q:
        return p.get("identity", "last_name")
    if "full name" in q or q == "name":
        return p.get("identity", "full_name")
    if "email" in q and "address" in q:
        return p.get("identity", "email")
    if "mobile" in q or "phone" in q:
        return p.get("identity", "phone")
    if "linkedin" in q and ("url" in q or "profile" in q):
        return p.get("identity", "linkedin_url")
    if "github" in q:
        return p.get("identity", "github_url")
    if "portfolio" in q or "website" in q:
        return p.get("identity", "portfolio_url")

    # Location
    if "city" in q:
        return p.get("identity", "city")
    if "state" in q or "province" in q:
        return p.get("identity", "state")
    if "country" in q and "code" not in q:
        return p.get("identity", "country")
    if "postal" in q or "zip" in q:
        return p.get("identity", "postal_code")

    # Comp
    if "current" in q and ("ctc" in q or "salary" in q or "compensation" in q):
        return p.get("comp", "current_inr_lpa")
    if "expected" in q and ("ctc" in q or "salary" in q or "compensation" in q):
        return p.get("comp", "expected_inr_lpa_min")
    if "notice" in q:
        return p.get("comp", "notice_period_text")

    # Work auth — yes/no questions
    if "authorized" in q or "authorised" in q or "right to work" in q or "eligible to work" in q:
        if "india" in q:
            return "Yes" if p.get("work_auth", "authorised_in_india") else "No"
        if "united states" in q or " us " in q or "u.s." in q:
            return "Yes" if p.get("work_auth", "authorised_in_us") else "No"
        if "united kingdom" in q or " uk " in q:
            return "Yes" if p.get("work_auth", "authorised_in_uk") else "No"
        return "Yes" if p.get("work_auth", "authorised_in_india") else "No"

    if "visa" in q or "sponsorship" in q:
        if "india" in q:
            return "Yes" if p.get("work_auth", "needs_visa_for_india") else "No"
        if "united states" in q or " us " in q:
            return "Yes" if p.get("work_auth", "needs_visa_for_us") else "No"
        return "No"  # default: not requiring sponsorship

    # Years of experience — pick the stack token from the question.
    if "year" in q and ("experience" in q or "work with" in q or "of " in q):
        for stack in ("python", "java", "javascript", "typescript", "react", "node",
                      "go", "ai", "ml", "llm", "aws", "gcp", "docker", "kubernetes",
                      "postgres", "mysql"):
            if stack in q:
                return p.years_for(stack)
        return p.get("experience", "total_years")

    # EEO / demographics — return decline if user opted out.
    if "gender" in q:
        return _eeo(p, "gender")
    if "veteran" in q:
        return _eeo(p, "veteran_status")
    if "disab" in q:
        return _eeo(p, "disability_status")
    if "ethnic" in q or "race" in q or "hispanic" in q or "latino" in q:
        return _eeo(p, "ethnicity")
    if "pronoun" in q:
        return _eeo(p, "pronouns")

    return None


def _eeo(p: Profile, key: str) -> Any:
    val = p.get("eeo", key)
    if val == "decline":
        return "Prefer not to answer"
    return val


# ── helpers ──


def _try_fill(page: Page, pairs: list[tuple[str, Any]], filled: list[str]) -> None:
    for sel, val in pairs:
        if not val:
            continue
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            loc.fill(str(val), timeout=2000)
            filled.append(sel)
        except Exception:  # noqa: BLE001
            continue


def _wait_until_closed(page: Page) -> dict[str, Any]:
    """Block until the user closes the browser window. We don't auto-detect
    submission — let the user explicitly close to confirm intent."""
    try:
        # `page.wait_for_event('close')` — this resolves when user closes tab.
        # Use a long-but-bounded wait so a stuck tab doesn't hang forever.
        page.wait_for_event("close", timeout=60 * 60 * 1000)  # 1 hour max
        return {"submitted": True, "reason": "closed_by_user"}
    except PlaywrightTimeout:
        return {"submitted": False, "reason": "timeout_1h"}
    except Exception as e:  # noqa: BLE001
        return {"submitted": False, "reason": f"error: {e}"}
