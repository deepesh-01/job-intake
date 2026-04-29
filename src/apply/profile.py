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

    def years_for(self, stack: str) -> int | float:
        """Years of experience for a stack. Returns int when whole, float when
        half-years are configured (e.g. AWS=3.5). Falls back to `default` if
        the stack isn't enumerated; 0 if there's no default either.

        The caller stringifies the value for form fills — `3` and `3.5` both
        render correctly in a number input.
        """
        exp = self.raw.get("experience") or {}
        raw = exp.get(stack.lower())
        if raw is None:
            raw = exp.get("default")
        if raw is None:
            return 0
        try:
            f = float(raw)
        except (TypeError, ValueError):
            return 0
        # Strip the .0 when it's a whole number so the form gets "3" not "3.0".
        return int(f) if f.is_integer() else f


def load_profile() -> Profile:
    if not PROFILE_PATH.exists():
        raise FileNotFoundError(
            f"apply_profile.yaml not found at {PROFILE_PATH}. "
            "Copy data/apply_profile.yaml.example or create it."
        )
    raw = yaml.safe_load(PROFILE_PATH.read_text()) or {}
    return Profile(raw=raw)
