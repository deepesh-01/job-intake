"""Apply-Copilot profile loader. Reads `data/apply_profile.yaml`."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PROFILE_PATH = Path(__file__).resolve().parents[2] / "data" / "apply_profile.yaml"


@dataclass(frozen=True)
class Profile:
    raw: dict[str, Any]

    def get(self, *keys: str, default: Any = "") -> Any:
        cur: Any = self.raw
        for k in keys:
            if not isinstance(cur, dict):
                return default
            cur = cur.get(k)
            if cur is None:
                return default
        return cur

    def years_for(self, stack: str) -> int:
        exp = self.raw.get("experience") or {}
        return int(exp.get(stack.lower()) or exp.get("default") or 0)


def load_profile() -> Profile:
    if not PROFILE_PATH.exists():
        raise FileNotFoundError(
            f"apply_profile.yaml not found at {PROFILE_PATH}. "
            "Copy data/apply_profile.yaml.example or create it."
        )
    raw = yaml.safe_load(PROFILE_PATH.read_text()) or {}
    return Profile(raw=raw)
