"""Stacker-list preset and remaining-operator calculation.

The old lineup/scoring logic is intentionally not used by the UI anymore.  A
preset is just an ordered set of stacking operators ("叠层手"); after a scan we
filter that set by the inferred ban rules and the user's manual exclusions.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .game import GameData, Operator, Recognition, banned_operators_by_rule

STACKER_TRAIT = "持续叠加"
TIER_ROMAN = {1: "Ⅰ", 2: "Ⅱ", 3: "Ⅲ", 4: "Ⅳ", 5: "Ⅴ", 6: "Ⅵ", 7: "Ⅶ"}
DEFAULT_RULE = "anyCoreBanned"


def tier_roman(tier: int) -> str:
    return TIER_ROMAN.get(int(tier), str(tier))


def all_stackers(game: GameData) -> list[Operator]:
    return sorted(
        (op for op in game.operators.values() if op.trait_type == STACKER_TRAIT),
        key=lambda op: (-op.tier, op.name),
    )


@dataclass
class StackerPreset:
    path: Path
    names: list[str] = field(default_factory=list)
    availability_rule: str = DEFAULT_RULE

    @classmethod
    def load(cls, path: Path, game: GameData) -> "StackerPreset":
        path = Path(path)
        if not path.exists():
            return cls(path, [op.name for op in all_stackers(game)])
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            return cls(path, [op.name for op in all_stackers(game)])

        if isinstance(data.get("stackers"), list):
            names = [str(x) for x in data["stackers"] if str(x).strip()]
            rule = str(data.get("availabilityRule") or data.get("scope", {}).get("availabilityRule") or DEFAULT_RULE)
            return cls(path, names, rule)

        # Old lineup preset: the old data model is intentionally discarded.
        # Seed the new list with every stacking operator so the panel is useful
        # immediately; the user can uncheck those they do not care about.
        return cls(path, [op.name for op in all_stackers(game)])

    def save(self, path: Path | None = None) -> Path:
        target = Path(path) if path is not None else self.path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.copy2(target, target.with_suffix(target.suffix + ".bak"))
        payload = {
            "schemaVersion": "0.2",
            "availabilityRule": self.availability_rule,
            "stackers": self.names,
        }
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
        self.path = target
        return target


def group_by_tier(operators: Iterable[Operator]) -> list[tuple[int, list[Operator]]]:
    groups: dict[int, list[Operator]] = {}
    for op in operators:
        groups.setdefault(op.tier, []).append(op)
    return [(tier, sorted(groups[tier], key=lambda op: op.name)) for tier in sorted(groups, reverse=True)]


def remaining(
    game: GameData,
    rec: Recognition,
    preset: StackerPreset,
    manual_banned: Iterable[str] = (),
) -> tuple[list[Operator], set[str], list[str]]:
    """Return (remaining, inferred_banned_in_preset, unknown_names)."""
    manual = set(manual_banned)
    inferred = banned_operators_by_rule(game, rec, preset.availability_rule)
    rows: list[Operator] = []
    banned: set[str] = set()
    unknown: list[str] = []
    seen: set[str] = set()
    for name in preset.names:
        if name in seen:
            continue
        seen.add(name)
        op = game.operators.get(name)
        if op is None:
            unknown.append(name)
            continue
        if op.trait_type != STACKER_TRAIT:
            unknown.append(name)
            continue
        if name in manual:
            banned.add(name)
            continue
        if name in inferred:
            banned.add(name)
            continue
        rows.append(op)
    rows.sort(key=lambda op: (-op.tier, op.name))
    return rows, banned, unknown
