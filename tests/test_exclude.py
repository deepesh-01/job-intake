from __future__ import annotations

from pathlib import Path

import yaml

from scout import exclude


_ROOT = Path(__file__).resolve().parent.parent
RAW = yaml.safe_load((_ROOT / "exclude.yaml").read_text())
RULES = exclude.compile_rules(RAW)


def test_excludes_big_tech():
    assert exclude.excluded_reason("Google", "good role", RULES) is not None
    assert exclude.excluded_reason("Meta Platforms", "x", RULES) is not None


def test_excludes_indian_services():
    assert exclude.excluded_reason("Infosys Limited", "x", RULES) is not None
    assert exclude.excluded_reason("Tata Consultancy Services", "x", RULES) is not None


def test_excludes_crypto_keyword_in_jd():
    assert exclude.excluded_reason("Acme", "We build smart contracts on Ethereum", RULES) is not None


def test_excludes_defense_keyword():
    assert exclude.excluded_reason("Acme", "We work on weapons systems", RULES) is not None


def test_pattern_match_consultancies():
    assert exclude.excluded_reason("Foo IT Services Pvt Ltd", "x", RULES) is not None


def test_clean_company_passes():
    assert exclude.excluded_reason("Anthropic", "We build helpful AI", RULES) is None
    assert exclude.excluded_reason("Stripe", "Payments infra in Ruby and Java", RULES) is None
