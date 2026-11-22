"""Static game data, recognition state and operator-ban rules.

This is the only part of the old recommender retained: the stacker-list
feature needs operator data and the covenant-based availability rules, not
lineup scoring.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Covenant:
    name: str
    cls: str  # "major" | "minor"
    bannable: bool
    activate_count: int | None
    operator_count: int
    effect_short: str = ""


@dataclass(frozen=True)
class Operator:
    name: str
    tier: int
    majors: tuple[str, ...]
    minors: tuple[str, ...]
    trait_type: str = ""
    trait_base: str = ""
    trait_elite: str = ""

    @property
    def covenants(self) -> tuple[str, ...]:
        return self.majors + self.minors


class GameData:
    def __init__(self, path: Path, counter_path: Path | None = None):
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.covenants: dict[str, Covenant] = {}
        for c in raw["covenants"]:
            self.covenants[c["name"]] = Covenant(
                name=c["name"],
                cls=c["class"],
                bannable=bool(c.get("bannable", True)),
                activate_count=c.get("activateCount"),
                operator_count=int(c.get("operatorCount") or 0),
                effect_short=c.get("effectShort", "") or "",
            )
        self.operators: dict[str, Operator] = {}
        for o in raw["operators"]:
            self.operators[o["name"]] = Operator(
                name=o["name"],
                tier=int(o.get("tier") or 1),
                majors=tuple(o.get("majorCovenants") or ()),
                minors=tuple(o.get("minorCovenants") or ()),
                trait_type=o.get("traitType") or "",
                trait_base=o.get("traitBase") or "",
                trait_elite=o.get("traitElite") or "",
            )
        self.majors = [c for c in self.covenants.values() if c.cls == "major"]
        self.minors = [c for c in self.covenants.values() if c.cls == "minor"]
        self.counter: dict[str, dict[str, float]] = {}
        self.counter_confidence: dict[str, str] = {}
        if counter_path and counter_path.exists():
            matrix = json.loads(counter_path.read_text(encoding="utf-8"))
            for row in matrix.get("matrix", []):
                tag = row["tag"]
                weights: dict[str, float] = {}
                for item in row.get("counters", []):
                    weights[item["covenant"]] = max(weights.get(item["covenant"], 0.0), 0.8)
                for item in row.get("suffers", []):
                    weights[item["covenant"]] = min(weights.get(item["covenant"], 0.0), -0.8)
                self.counter[tag] = weights
                self.counter_confidence[tag] = row.get("confidence") or "low"

    def operators_with(self, covenant: str) -> list[Operator]:
        return [o for o in self.operators.values() if covenant in o.covenants]


# ---------------------------------------------------------------------------
# recognition + preset
# ---------------------------------------------------------------------------


@dataclass
class Recognition:
    mode: str = ""
    major_available: set[str] = field(default_factory=set)
    major_banned: set[str] = field(default_factory=set)
    minor_available: set[str] = field(default_factory=set)
    minor_banned: set[str] = field(default_factory=set)
    enemy_tags: list[str] = field(default_factory=list)
    enemy_leader: str | None = None
    map_name: str | None = None
    banned_operators: set[str] = field(default_factory=set)
    uncertain: list[str] = field(default_factory=list)
    confidence: float = 1.0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Recognition":
        major = d.get("major") or {}
        minor = d.get("minor") or {}
        enemy = d.get("enemy") or {}
        return cls(
            mode=d.get("mode") or "",
            major_available=set(major.get("available") or ()),
            major_banned=set(major.get("banned") or ()),
            minor_available=set(minor.get("available") or ()),
            minor_banned=set(minor.get("banned") or ()),
            enemy_tags=list(enemy.get("tags") or ()),
            enemy_leader=enemy.get("leader"),
            map_name=enemy.get("map"),
            banned_operators=set(d.get("bannedOperators") or ()),
            uncertain=list(d.get("uncertain") or ()),
            confidence=float(d.get("confidence") or 1.0),
        )


def validate_recognition(rec: Recognition, game: GameData | None = None) -> list[str]:
    """Return warnings; never silently accept an impossible 5+5 state."""
    warnings: list[str] = []
    if len(rec.major_banned) != 5:
        warnings.append(f"核心盟约禁用应为 5 个，实际 {len(rec.major_banned)}")
    if len(rec.minor_banned) != 5:
        warnings.append(f"附加盟约禁用应为 5 个，实际 {len(rec.minor_banned)}")
    if "绝技" in rec.minor_banned:
        warnings.append("绝技不可被禁用，识别结果可能误读")
    if game is not None:
        unknown = (rec.major_available | rec.major_banned | rec.minor_available | rec.minor_banned) - set(
            game.covenants
        )
        if unknown:
            warnings.append("识别出未知盟约名：" + "、".join(sorted(unknown)))
    if rec.uncertain:
        warnings.append("识别存在不确定格：" + "、".join(rec.uncertain))
    if rec.confidence < 0.8:
        warnings.append(f"识别置信度较低：{rec.confidence:.2f}")
    return warnings


# ---------------------------------------------------------------------------
# availability rules (the contested part — keep pluggable)
# ---------------------------------------------------------------------------


def operator_available(op: Operator, rec: Recognition, rule: str) -> bool:
    """Is this operator still buyable under the run's bans?

    Three rules are found in the wild; see research/stronghold_research.md §3.1.
    - anyCoreBanned (default, matches the named screenshot evidence):
        has majors -> banned if ANY major is banned; no majors -> banned if ALL minors banned.
    - allCoresBanned:
        has majors -> banned if ALL majors banned; no majors -> banned if ALL minors banned.
    - coreAndAdditionalBanned:
        banned iff (a major is banned) AND (an additional is banned); no minors -> banned
        if exactly one major is banned (ASP's latest written rule, still unverified).
    """
    majors = set(op.majors)
    minors = set(op.minors)
    banned_major = rec.major_banned
    banned_minor = rec.minor_banned

    if rule == "allCoresBanned":
        if majors:
            return bool(majors - banned_major)
        return bool(minors - banned_minor)
    if rule == "coreAndAdditionalBanned":
        if majors and minors:
            return not (majors & banned_major and minors & banned_minor)
        if majors:
            return len(majors & banned_major) != 1
        return bool(minors - banned_minor)
    # anyCoreBanned
    if majors:
        return not (majors & banned_major)
    return bool(minors - banned_minor)


def banned_operators_by_rule(game: GameData, rec: Recognition, rule: str) -> set[str]:
    return {
        name
        for name, op in game.operators.items()
        if not operator_available(op, rec, rule)
    }


