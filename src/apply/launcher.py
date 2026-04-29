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

        # After login, LinkedIn often redirects to /feed instead of back to
        # the original job URL. If we're not on the job page, re-navigate.
        # Without this the Easy Apply selector search runs on the feed page
        # and silently misses (observed 2026-04-30 first-run).
        try:
            current = page.url
        except Exception:  # noqa: BLE001
            current = ""
        job_path = job_url.split("?")[0]
        if job_path not in current:
            log.info("post-login URL mismatch; re-nav to job page", extra={"current": current})
            try:
                page.goto(job_url, wait_until="domcontentloaded", timeout=30_000)
            except PlaywrightTimeout:
                log.warning("re-nav timeout; proceeding anyway")

        # Step 2: click "Easy Apply" — there are several variants of the button.
        easy_apply_clicked = _click_easy_apply(page)
        if not easy_apply_clicked:
            log.info("no Easy Apply button — leave page open for manual apply")
            _wait_until_closed(page)
            return {"submitted": False, "reason": "no_easy_apply", "filled_fields": []}

        # Step 2.5: install the submit-block + Re-autofill banner.
        _install_submit_block(page)

        # Step 3: autofill loop. Initial autofill on whatever step is showing,
        # then wait for user actions (Re-autofill click → re-run, window
        # close → exit). Each Re-autofill walks ALL visible questions in the
        # current step — so the user can edit a value and click Re-autofill
        # OR advance to a new step (clicking LinkedIn's Next manually) and
        # click Re-autofill to populate the new step's fields.
        all_filled: list[str] = []
        round_num = 0
        while True:
            round_num += 1
            filled = _autofill_easy_apply_modal(page, profile, job_role, job_company)
            log.info("autofill round %d filled %d field(s): %s",
                     round_num, len(filled), filled[:5])
            all_filled.extend(filled)
            action = _wait_for_user_action(page, timeout_seconds=3600)
            if action == "close":
                return {
                    "submitted": True,
                    "reason": "closed_by_user",
                    "filled_fields": all_filled,
                }
            if action == "timeout":
                return {
                    "submitted": False,
                    "reason": "idle_timeout_1h",
                    "filled_fields": all_filled,
                }
            # action == 'refill' → loop and run autofill again on whatever
            # step is currently visible. Re-injection of the banner is safe
            # (idempotent thanks to the __copilotSubmitBlockInstalled flag).
            try:
                page.evaluate(_SUBMIT_BLOCK_JS)
            except Exception:  # noqa: BLE001
                pass


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


_SUBMIT_BLOCK_JS = r"""
(() => {
  if (window.__copilotSubmitBlockInstalled) return;
  window.__copilotSubmitBlockInstalled = true;
  window.__copilotSubmitUnlocked = false;
  window.__copilotRefillRequested = false;

  // Strict immediate-target check ONLY — do NOT walk up the parent chain.
  // Walking up caused Next/Continue buttons to be blocked because their
  // ancestors contained text like "Submit application" (footer disclaimer,
  // step heading on a later step, etc.).
  const isSubmit = (el) => {
    if (!el || el.nodeType !== 1) return false;
    const text = (el.innerText || el.textContent || '').trim().toLowerCase();
    const aria = (el.getAttribute('aria-label') || '').toLowerCase();
    // aria-label EXACTLY mentioning Submit
    if (/^submit application/i.test(aria) || /^submit$/i.test(aria)) return true;
    // Visible label EXACTLY "Submit application" or "Submit" — exact-only
    // so "Submit & Save Draft" still triggers but "Save and submit later"
    // does not. Whole-string match.
    if (/^submit application$|^submit$/i.test(text)) return true;
    return false;
  };

  // Capture-phase listener fires BEFORE LinkedIn's own handlers. Only the
  // immediate clicked element is inspected — parent chain explicitly NOT
  // walked. Avoids the false-positive that blocked the Next button.
  document.addEventListener('click', (e) => {
    if (window.__copilotSubmitUnlocked) return;
    if (isSubmit(e.target) || isSubmit(e.target.closest('button, a'))) {
      e.preventDefault();
      e.stopImmediatePropagation();
      const banner = document.getElementById('__copilot-banner');
      if (banner) banner.style.background = '#fef2f2';
      alert('🔒 Co-pilot has blocked Submit. Click the Unlock button (top of the page) first.');
      return;
    }
  }, true);

  // Floating banner — Submit lock state + Re-autofill action button.
  const banner = document.createElement('div');
  banner.id = '__copilot-banner';
  banner.style.cssText = `
    position: fixed; top: 0; left: 0; right: 0; z-index: 2147483647;
    background: #fffbeb; color: #92400e; padding: 8px 14px;
    font: 600 13px -apple-system, system-ui, sans-serif;
    border-bottom: 2px solid #f59e0b;
    display: flex; align-items: center; gap: 12px;
  `;
  banner.innerHTML = `
    <span id="__copilot-status">🔒 Co-pilot — Submit blocked. Review fields, then click Unlock when ready to submit.</span>
    <button id="__copilot-refill" style="
      margin-left:auto; padding: 4px 12px; border-radius: 6px;
      background: #1e40af; color: white; border: none;
      font: 600 12px -apple-system; cursor: pointer;
    ">🤖 Re-autofill this step</button>
    <button id="__copilot-unlock" style="
      padding: 4px 12px; border-radius: 6px;
      background: #92400e; color: white; border: none;
      font: 600 12px -apple-system; cursor: pointer;
    ">Unlock Submit</button>
  `;
  document.body.appendChild(banner);

  document.getElementById('__copilot-unlock').addEventListener('click', () => {
    window.__copilotSubmitUnlocked = true;
    banner.style.background = '#dcfce7';
    banner.style.borderBottomColor = '#16a34a';
    banner.style.color = '#14532d';
    document.getElementById('__copilot-status').innerText = '🔓 Submit unlocked. Co-pilot will not interfere.';
    document.getElementById('__copilot-unlock').remove();
  });

  // Re-autofill: just sets a flag. The Python loop polls this every 500ms
  // and re-runs the autofill walker on the currently-visible step.
  document.getElementById('__copilot-refill').addEventListener('click', () => {
    window.__copilotRefillRequested = true;
    const refillBtn = document.getElementById('__copilot-refill');
    const orig = refillBtn.innerText;
    refillBtn.innerText = '⏳ Filling…';
    refillBtn.disabled = true;
    setTimeout(() => { refillBtn.innerText = orig; refillBtn.disabled = false; }, 1500);
  });
})();
"""


def _install_submit_block(page: Page) -> None:
    """Inject capture-phase click interceptor that blocks Submit clicks +
    a floating banner with [🤖 Re-autofill this step] and [Unlock Submit]
    buttons. Survives DOM rerenders because the banner is appended to body
    and the listener stays attached to document."""
    try:
        page.evaluate(_SUBMIT_BLOCK_JS)
        log.info("submit-block installed — interceptor + Re-autofill + Unlock buttons")
    except Exception as e:  # noqa: BLE001
        log.warning("submit-block injection failed: %s", e)


def _wait_for_user_action(page: Page, timeout_seconds: int = 3600) -> str:
    """Poll the page every 500ms for either:
      - user clicked the Re-autofill button → returns 'refill'
      - user closed the tab/window → returns 'close'
      - timeout (default 1h) → returns 'timeout'
    """
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            if page.is_closed():
                return "close"
            flag = page.evaluate("window.__copilotRefillRequested === true")
            if flag:
                page.evaluate("window.__copilotRefillRequested = false")
                return "refill"
        except Exception:  # noqa: BLE001 — page might be closing
            return "close"
        time.sleep(0.5)
    return "timeout"


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
    """Try each known selector for the Easy Apply button/link. Wait up to
    15s total for ANY of them to render — LinkedIn's React SPA hydrates the
    apply target asynchronously after URL change. Returns True on success.

    LinkedIn currently renders Easy Apply in TWO different ways depending
    on whether the user is rolled into the new SDUI flow:
      - Legacy: <button class='jobs-apply-button'>...
      - SDUI:   <a href='.../apply/?openSDUIApplyFlow=true' aria-label='Easy Apply to this job'>...
    Hashed CSS-module class names ("_41df6e23"…) rotate often so are
    unusable; aria-label + visible text are the only stable hooks.
    """
    combined = (
        # SDUI anchor (current as of 2026-04-30)
        "a[aria-label*='Easy Apply'], "
        "a[aria-label^='Easy Apply'], "
        "a[href*='/apply/'][aria-label*='Easy Apply'], "
        # Legacy button
        "button.jobs-apply-button:has-text('Easy Apply'), "
        "button[aria-label*='Easy Apply'], "
        "button[aria-label^='Easy Apply'], "
        "button.jobs-apply-button, "
        "button:has-text('Easy Apply'), "
        ".jobs-apply-button--top-card button, "
        "button[data-control-name*='easy_apply']"
    )
    try:
        page.wait_for_selector(combined, state="visible", timeout=15_000)
    except PlaywrightTimeout:
        log.warning("Easy Apply target never rendered after 15s — leaving page open for manual apply")
        return False

    selectors = [
        # SDUI anchor — most specific first
        "a[aria-label='Easy Apply to this job']",
        "a[aria-label^='Easy Apply']",
        "a[aria-label*='Easy Apply']",
        # Legacy button variants
        "button.jobs-apply-button:has-text('Easy Apply')",
        "button[aria-label^='Easy Apply']",
        "button[aria-label*='Easy Apply']",
        ".jobs-apply-button--top-card button",
        "button.jobs-apply-button",
        # Last-resort text match (covers either a or button)
        "button:has-text('Easy Apply'), a:has-text('Easy Apply')",
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if btn.count() == 0:
                continue
            btn.scroll_into_view_if_needed(timeout=3000)
            btn.click(timeout=4000)
            # SDUI anchor navigates to a new URL; legacy button opens a modal.
            # Either way, give the apply form 2.5s to hydrate before we start
            # introspecting questions.
            page.wait_for_timeout(2500)
            log.info("clicked Easy Apply via %s", sel)
            return True
        except Exception as e:  # noqa: BLE001 — best-effort
            log.debug("selector %s failed: %s", sel, e)
    log.warning("Easy Apply target rendered but no selector clicked — markup may have shifted again")
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
            label_text = _extract_question_label(grp)
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


# ── question label extraction ────────────────────────────────────────


# Texts that are usually radio OPTION labels, not the question heading.
# When _extract_question_label sees these as the "first label", it should
# look further up for the real question.
_OPTION_TEXT_TOKENS = {
    "yes", "no", "decline to self-identify", "prefer not to answer",
    "i prefer not to answer", "i decline", "decline",
}


def _extract_question_label(grp: Any) -> str:
    """Pull the actual question heading from a form-section grouping.

    Prior version grabbed `label, span.fb-form-element-label__text` first
    — for radio groups (`Do you have X? [Yes / No]`) that returns the
    radio option's text ("Yes"), not the question. This caused 8 questions
    on the WaferWire run to be skipped as `unrecognised question`.

    Strategy (most reliable first):
      1. <legend> inside a <fieldset> (semantic HTML for radio groups)
      2. LinkedIn's own heading classes (t-bold / form-element heading)
      3. Element with role='heading' or any <h*>
      4. First <label> / <span> whose text is NOT a known option token
    """
    # 1. legend (the most reliable heading element for radio groups)
    try:
        legend = grp.locator("legend").first
        if legend.count() > 0:
            text = legend.inner_text(timeout=800).strip()
            if text and text.lower() not in _OPTION_TEXT_TOKENS:
                return text
    except Exception:  # noqa: BLE001
        pass

    # 2. LinkedIn's heading class hooks (stable on Easy Apply since at
    # least 2024). t-bold is the typography utility for question headings.
    for sel in (
        "span.fb-form-element-label__text",
        "span.t-bold",
        "[data-test-text-selectable-option__label]",
        "[role='heading']",
        "h3, h4",
    ):
        try:
            el = grp.locator(sel).first
            if el.count() > 0:
                text = el.inner_text(timeout=800).strip()
                if text and text.lower() not in _OPTION_TEXT_TOKENS and len(text) > 2:
                    return text
        except Exception:  # noqa: BLE001
            continue

    # 3. Last resort: first non-trivial label or span. Skip option tokens
    # ("Yes", "No"). Also skip very short strings that are likely options.
    try:
        for sel in ("label", "span"):
            elements = grp.locator(sel)
            n = elements.count()
            for i in range(min(n, 6)):
                try:
                    text = elements.nth(i).inner_text(timeout=500).strip()
                except Exception:  # noqa: BLE001
                    continue
                if not text or text.lower() in _OPTION_TEXT_TOKENS:
                    continue
                if len(text) <= 2:
                    continue
                return text
    except Exception:  # noqa: BLE001
        pass
    return ""


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

    # Yes/No: "Do you have experience with X?" / "Are you familiar with X?"
    # / "Strong exp on Y" / "Have you worked with Z?". Cross-reference the
    # question against the user's `skills_yes_keywords` (positive claims)
    # and `skills_no_keywords` (explicit no-answers). Anything ambiguous
    # falls through to None so the user fills it manually.
    yes_no_signals = ("experience" in q or "familiar" in q or "worked with" in q
                       or "ability to" in q or "strong exp" in q or "knowledge of" in q
                       or "exposure to" in q or "comfortable with" in q
                       or "have you" in q or "do you have" in q)
    if yes_no_signals:
        skills_yes = [s.lower() for s in (p.raw.get("skills_yes_keywords") or [])]
        skills_no = [s.lower() for s in (p.raw.get("skills_no_keywords") or [])]
        if any(skill in q for skill in skills_yes):
            return "Yes"
        if any(skill in q for skill in skills_no):
            return "No"
        # No skill match — let the user answer manually rather than guess.

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
