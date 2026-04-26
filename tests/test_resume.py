"""Resume skill-extractor + match scorer tests."""
from __future__ import annotations

from scout.resume import (
    MatchResult,
    ResumeProfile,
    extract_skills,
    is_strong,
    match,
)


_RESUME = """
# DEEPESH RATHOD
Founding Engineer • Tech Lead • Backend & Cloud Architecture

## TECHNICAL SKILLS
- Languages: TypeScript, JavaScript, Python, C++, Java
- Backend: Node.js, NestJS, Express, Django, FastAPI
- Frontend: React, Redux
- Cloud: AWS (ECS, Fargate, EC2, SES, SQS), Docker, CI/CD
- Databases: PostgreSQL, MongoDB, Redis
- AI: Voice AI integration, AI agent observability, LLM, Anthropic, OpenAI
- Architecture: Multi-tenant SaaS, microservices, monorepo, SOC2, HIPAA
"""

_PROFILE = ResumeProfile(raw_text=_RESUME, skills=extract_skills(_RESUME))


def test_extract_skills_finds_canonical_set():
    assert "typescript" in _PROFILE.skills
    assert "python" in _PROFILE.skills
    assert "node.js" in _PROFILE.skills
    assert "postgres" in _PROFILE.skills
    assert "soc2" in _PROFILE.skills


def test_skills_are_canonical_no_duplicates():
    # nodejs/node.js/node should all collapse to one entry
    assert "nodejs" not in _PROFILE.skills
    assert "node" not in _PROFILE.skills
    assert "node.js" in _PROFILE.skills


def test_strong_match_high_overlap():
    jd = """
    Senior Backend Engineer at Acme.

    Requirements:
    - 5+ years with TypeScript and Node.js
    - Production AWS experience (ECS, Lambda)
    - PostgreSQL, Redis
    - Experience building multi-tenant SaaS
    """
    r = match(jd, _PROFILE)
    assert r.count >= 5
    assert r.score >= 0.5
    assert is_strong(r)


def test_weak_match_low_overlap():
    jd = """
    Senior Rust Systems Engineer at Acme.

    Requirements:
    - 5+ years writing Rust in production
    - Distributed systems experience
    - Kubernetes, gRPC, Kafka
    """
    r = match(jd, _PROFILE)
    assert r.count == 0
    assert r.score == 0.0
    assert not is_strong(r)


def test_partial_match_below_threshold():
    jd = """
    iOS Engineer at Acme.

    Requirements:
    - Swift, Objective-C
    - Xcode
    - Some Python scripting nice to have
    """
    r = match(jd, _PROFILE)
    assert r.count <= 2
    assert not is_strong(r)


def test_match_returns_sorted_deterministic_tokens():
    jd = "We use Python, TypeScript, React, Postgres."
    r = match(jd, _PROFILE)
    assert list(r.matched) == sorted(r.matched)


def test_empty_jd():
    r = match("", _PROFILE)
    assert r.count == 0
    assert r.score == 0.0
    assert r.matched == ()


def test_empty_resume():
    empty = ResumeProfile(raw_text="", skills=frozenset())
    r = match("Senior Python developer wanted", empty)
    assert r.count == 0


def test_acronym_word_boundary():
    """'go' must not match inside 'going' / 'going to'."""
    text = "We're going to be busy this year"
    skills = extract_skills(text)
    assert "go" not in skills
