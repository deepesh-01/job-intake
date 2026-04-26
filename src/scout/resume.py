"""Resume-aware match scoring.

Reads the user's base resume once at scout start. Per JD: deterministic
skill-overlap against a curated tech vocabulary. No embeddings, no API
calls, no $.

The vocabulary is biased toward what the user's resume actually contains
(see TECH_VOCAB) plus common adjacencies — the goal is high precision on
"these are tools the user knows," not exhaustive industry coverage.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


# Curated tech vocab — multi-word phrases first so they match before their
# constituent tokens. Match is case-insensitive, anchored on word boundaries
# (or whitespace for phrases). Keep this list intentional: every token here
# should be something a JD might require AND that the user has demonstrated
# in resume / projects.
TECH_VOCAB: frozenset[str] = frozenset(
    {
        # Languages
        "typescript", "javascript", "python", "java", "c++", "c#",
        "go", "golang", "rust", "ruby", "php",
        # Node ecosystem
        "node.js", "nodejs", "node js", "node",
        "nestjs", "express", "express.js",
        "next.js", "nextjs", "deno", "bun", "hono",
        # React stack
        "react", "react.js", "redux", "tanstack", "react query",
        "tailwind", "shadcn",
        # Python ecosystem
        "django", "django rest framework", "drf", "flask", "fastapi",
        "pydantic", "asyncio", "celery",
        # Cloud / Infra
        "aws", "ecs", "fargate", "ec2", "lambda", "s3", "ses", "sqs",
        "cloudtrail", "iam", "rds", "dynamodb", "cloudfront", "route53",
        "gcp", "azure", "kubernetes", "k8s", "docker", "terraform",
        "cloudflare", "ci/cd", "github actions", "circleci",
        # Databases
        "postgres", "postgresql", "mongodb", "mongo", "mysql",
        "redis", "elasticsearch", "clickhouse", "snowflake",
        # Messaging / Streaming
        "kafka", "rabbitmq", "nats", "pubsub", "kinesis",
        # AI / ML
        "llm", "openai", "anthropic", "claude", "gpt",
        "langchain", "langgraph", "vector database", "rag",
        "embeddings", "ai agent", "agentic", "voice ai", "retell",
        "selenium", "playwright",
        # Practices / architecture
        "microservices", "monorepo", "multi-tenant", "saas",
        "soc2", "hipaa", "gdpr", "rbac", "audit log",
        "rest api", "graphql", "grpc", "websocket", "webhook",
        "zero-downtime", "blue-green", "feature flag",
        # Roles / shape (so JDs that mention them score positively)
        "founding engineer", "tech lead", "staff engineer",
        "full-stack", "fullstack", "full stack", "backend",
        "frontend", "devops", "platform",
        # Tools
        "git", "linux", "bash", "ffmpeg", "stripe", "twilio",
        "slack api", "telegram",
    }
)


@dataclass(frozen=True)
class ResumeProfile:
    raw_text: str
    skills: frozenset[str]


@dataclass(frozen=True)
class MatchResult:
    score: float           # 0.0 - 1.0; fraction of jd_skills that user has
    count: int             # number of overlapping skills
    matched: tuple[str, ...]  # the actual matched tokens (sorted, deterministic)


def _build_skill_pattern(token: str) -> re.Pattern[str]:
    """Compile a case-insensitive whole-token match. Multi-word tokens
    use whitespace-flexible matching; single-word use word boundaries."""
    if " " in token:
        # multi-word: split on space, allow flexible whitespace between
        parts = [re.escape(p) for p in token.split()]
        body = r"\s+".join(parts)
        return re.compile(rf"(?<![A-Za-z]){body}(?![A-Za-z])", re.IGNORECASE)
    if any(c in token for c in "+#./"):
        # has punctuation that breaks word boundary; use lookarounds
        return re.compile(
            rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])",
            re.IGNORECASE,
        )
    return re.compile(rf"\b{re.escape(token)}\b", re.IGNORECASE)


# Pre-compile once
_PATTERNS: dict[str, re.Pattern[str]] = {tok: _build_skill_pattern(tok) for tok in TECH_VOCAB}


def extract_skills(text: str) -> frozenset[str]:
    """Return the subset of TECH_VOCAB that appears anywhere in `text`."""
    if not text:
        return frozenset()
    found: set[str] = set()
    for tok, pat in _PATTERNS.items():
        if pat.search(text):
            found.add(tok)
    return frozenset(_canonicalize(found))


def _canonicalize(skills: set[str]) -> set[str]:
    """Collapse near-synonyms so the count isn't inflated:
    - 'node.js'/'nodejs'/'node js'/'node' → 'node.js'
    - 'postgres'/'postgresql' → 'postgres'
    - 'fullstack'/'full stack'/'full-stack' → 'full-stack'
    - 'mongodb'/'mongo' → 'mongodb'
    - 'k8s'/'kubernetes' → 'kubernetes'
    """
    aliases: dict[str, str] = {
        "nodejs": "node.js", "node js": "node.js", "node": "node.js",
        "postgresql": "postgres",
        "fullstack": "full-stack", "full stack": "full-stack",
        "mongo": "mongodb",
        "k8s": "kubernetes",
        "golang": "go",
        "react.js": "react",
        "express.js": "express",
        "nextjs": "next.js",
        "drf": "django rest framework",
    }
    return {aliases.get(s, s) for s in skills}


def load_profile(resume_path: Path) -> ResumeProfile:
    if not resume_path.is_file():
        raise FileNotFoundError(
            f"base resume not found at {resume_path} — set SYSTEM_A_BASE_RESUME"
        )
    text = resume_path.read_text(encoding="utf-8")
    return ResumeProfile(raw_text=text, skills=extract_skills(text))


# ── Match scoring ──


_STRONG_THRESHOLD = 3   # absolute count of overlapping skills → resume_strong tag


def match(jd_text: str, profile: ResumeProfile) -> MatchResult:
    """Compute resume↔JD skill overlap.

    score = |intersection| / max(|jd_skills|, 1), capped at 1.0.
    Falls back to a small bonus for short JDs with at least one match.
    """
    jd_skills = extract_skills(jd_text)
    if not jd_skills:
        return MatchResult(0.0, 0, ())
    inter = jd_skills & profile.skills
    count = len(inter)
    denom = max(len(jd_skills), 1)
    score = min(1.0, count / denom)
    return MatchResult(score=round(score, 3), count=count, matched=tuple(sorted(inter)))


def is_strong(result: MatchResult) -> bool:
    return result.count >= _STRONG_THRESHOLD
