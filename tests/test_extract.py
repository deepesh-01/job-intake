"""Comp parser tests — the most fragile and highest-value code in the repo."""
from __future__ import annotations

from scout import extract


FX = {"GBP": 1.20, "EUR": 1.05, "SGD": 0.74, "AUD": 0.65, "CAD": 0.72}


def parse(text, explicit=None):
    return extract.parse_comp(text, fx_rates=FX, explicit_comp_string=explicit)


# ── INR ──


def test_inr_lpa_range():
    c = parse("Compensation: ₹35-50 LPA depending on experience")
    assert c.currency == "INR"
    assert c.low == 3_500_000
    assert c.high == 5_000_000


def test_inr_lakh_word_range():
    c = parse("We offer 30 to 45 lakhs base")
    assert c.currency == "INR"
    assert c.low == 3_000_000
    assert c.high == 4_500_000


def test_inr_crore_single():
    c = parse("Up to ₹1.5 cr total comp")
    assert c.currency == "INR"
    assert c.low is None
    assert c.high == 15_000_000


def test_inr_with_l_shorthand():
    c = parse("Cash 30L + ESOPs")
    assert c.currency == "INR"
    assert c.high == 3_000_000


# ── USD ──


def test_usd_k_range():
    c = parse("Salary: $150k - $220k")
    assert c.currency == "USD"
    assert c.low == 150_000
    assert c.high == 220_000


def test_usd_comma_range():
    c = parse("Range: $150,000 - $220,000")
    assert c.currency == "USD"
    assert c.low == 150_000
    assert c.high == 220_000


def test_usd_up_to():
    c = parse("Up to $180k for the right candidate")
    assert c.currency == "USD"
    assert c.low is None
    assert c.high == 180_000


def test_usd_plus():
    c = parse("$140k+ depending on level")
    assert c.currency == "USD"
    assert c.low == 140_000
    assert c.high == 140_000


# ── Other currencies via FX ──


def test_gbp_with_fx():
    c = parse("£60k - £90k base")
    assert c.currency == "GBP"
    assert c.low == 60_000
    assert c.high == 90_000
    assert c.low_usd == 72_000   # 60k * 1.20
    assert c.high_usd == 108_000


def test_eur_range():
    c = parse("€80,000 - €120,000")
    assert c.currency == "EUR"
    assert c.high == 120_000
    assert c.high_usd == 126_000  # 120k * 1.05


# ── Negative cases ──


def test_no_currency_returns_null():
    c = parse("Competitive salary based on experience.")
    assert c.currency is None
    assert c.low is None
    assert c.high is None


def test_currency_present_but_no_number():
    c = parse("Compensation in USD, see offer letter.")
    # USD detected; no parseable number → low/high remain None
    assert c.currency == "USD"
    assert c.low is None and c.high is None


def test_equity_only():
    c = parse("Equity only at this stage.")
    # No currency, but equity-only path keeps the snippet
    assert c.currency is None
    assert c.comp_string is not None


def test_explicit_comp_string_short_circuits():
    c = parse("body has no money", explicit="$100k - $130k")
    assert c.currency == "USD"
    assert c.high == 130_000


# ── Regression coverage for first-run bugs ──


def test_em_dash_separator():
    c = parse("Annual Salary: $116,480 — $165,000 USD")
    assert c.currency == "USD"
    assert c.low == 116_480
    assert c.high == 165_000


def test_double_encoded_html_unescapes():
    from scout.extract import html_to_text
    raw = (
        "&lt;div class=&quot;pay-range&quot;&gt;&lt;span&gt;$120,000&lt;/span&gt;"
        "&mdash;&lt;span&gt;$200,000 USD&lt;/span&gt;&lt;/div&gt;"
    )
    text = html_to_text(raw)
    assert "$120,000" in text and "$200,000" in text
    assert "<div" not in text and "&lt;" not in text
    c = parse(text)
    assert c.currency == "USD"
    assert c.low == 120_000


def test_usd_takes_priority_over_inr_l_shorthand():
    c = parse("Salary $120,000 — $200,000. Some unrelated 30L token elsewhere.")
    assert c.currency == "USD"
    assert c.low == 120_000


def test_inr_shorthand_still_works_when_alone():
    c = parse("Cash 30L + ESOPs (no other currency mentioned)")
    assert c.currency == "INR"


def test_no_inr_false_positive_inside_english_words():
    """Regression: 'Rs' inside 'stakeholders', 'engineers', 'users' must not
    fire INR. Same for 'INR' inside ALL-CAPS abbreviations."""
    text = (
        "Work with stakeholders, engineers, and users to deliver. "
        "We're looking for someone who enjoys cross-functional partnership."
    )
    from scout.extract import detect_currency
    assert detect_currency(text) is None


def test_no_inr_false_positive_for_user_counts():
    """Indian product JDs commonly say '5cr+ users' / '1L users'. Without
    salary context, this must not trigger INR detection."""
    from scout.extract import detect_currency
    assert detect_currency("Our app serves 5cr+ users monthly.") is None
    assert detect_currency("We onboard 1L users per quarter.") is None


def test_inr_cr_with_comp_context_still_fires():
    from scout.extract import detect_currency
    assert detect_currency("Compensation: up to 1.5cr per annum.") == "INR"


# ── Normalization ──


def test_normalize_company_strips_suffix():
    assert extract.normalize_company("Acme Corp") == "acme"
    assert extract.normalize_company("FooBar Pvt Ltd") == "foobar"
    assert extract.normalize_company("MyCo Inc.") == "myco"


def test_normalize_role_drops_brackets():
    assert extract.normalize_role("Senior Engineer (Remote)") == "senior engineer"
    assert extract.normalize_role("Backend Engineer [US]") == "backend engineer"
