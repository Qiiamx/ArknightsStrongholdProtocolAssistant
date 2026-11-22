"""Stacker-list preset and remaining-operator calculation.

A preset is just a set of stacking operators plus optional per-operator
covenant overrides.  After a scan we filter the list with one fixed rule:

    an operator is banned iff
      - it has majors and ALL majors are banned, or
      - it has no majors and ALL minors are banned.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable

from .game import GameData, Operator, Recognition

STACKER_TRAIT = "持续叠加"
TIER_ROMAN = {1: "Ⅰ", 2: "Ⅱ", 3: "Ⅲ", 4: "Ⅳ", 5: "Ⅴ", 6: "Ⅵ", 7: "Ⅶ"}


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
    overrides: dict[str, dict[str, list[str]]] = field(default_factory=dict)

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
            overrides: dict[str, dict[str, list[str]]] = {}
            for name, raw in (data.get("operators") or {}).items():
                if not isinstance(raw, dict):
                    continue
                major = raw.get("major") or raw.get("majors") or []
                minor = raw.get("minor") or raw.get("minors") or []
                overrides[str(name)] = {
                    "major": [str(x) for x in major],
                    "minor": [str(x) for x in minor],
                }
            return cls(path, names, overrides)

        # Old lineup preset: discard the old model and seed every stacker.
        return cls(path, [op.name for op in all_stackers(game)])

    def save(self, path: Path | None = None) -> Path:
        target = Path(path) if path is not None else self.path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.copy2(target, target.with_suffix(target.suffix + ".bak"))
        payload = {
            "schemaVersion": "0.3",
            "stackers": self.names,
            "operators": self.overrides,
        }
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
        self.path = target
        return target


def effective_operator(game: GameData, name: str, preset: StackerPreset) -> Operator | None:
    op = game.operators.get(name)
    if op is None:
        return None
    override = preset.overrides.get(name)
    if not override:
        return op
    majors = override.get("major")
    minors = override.get("minor")
    return replace(
        op,
        majors=tuple(majors) if majors is not None else op.majors,
        minors=tuple(minors) if minors is not None else op.minors,
    )


def operator_available(op: Operator, rec: Recognition) -> bool:
    """The only ban rule used by the stacker feature."""
    majors = set(op.majors)
    minors = set(op.minors)
    if majors:
        return bool(majors - rec.major_banned)
    return bool(minors - rec.minor_banned)


def group_by_tier(operators: Iterable[Operator]) -> list[tuple[int, list[Operator]]]:
    groups: dict[int, list[Operator]] = {}
    for op in operators:
        groups.setdefault(op.tier, []).append(op)
    return [(tier, sorted(groups[tier], key=lambda op: op.name)) for tier in sorted(groups, reverse=True)]


def remaining(
    game: GameData,
    rec: Recognition,
    preset: StackerPreset,
) -> tuple[list[Operator], set[str], list[str]]:
    """Return (remaining, inferred_banned_in_preset, unknown_names)."""
    rows: list[Operator] = []
    banned: set[str] = set()
    unknown: list[str] = []
    seen: set[str] = set()
    for name in preset.names:
        if name in seen:
            continue
        seen.add(name)
        op = effective_operator(game, name, preset)
        if op is None or op.trait_type != STACKER_TRAIT:
            unknown.append(name)
            continue
        if not operator_available(op, rec):
            banned.add(name)
            continue
        rows.append(op)
    rows.sort(key=lambda op: (-op.tier, op.name))
    return rows, banned, unknown
