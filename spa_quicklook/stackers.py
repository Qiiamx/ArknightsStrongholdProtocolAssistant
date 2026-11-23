"""Watch-list preset and remaining-operator calculation.

A preset is a set of operators to display, plus optional per-operator covenant
overrides.  The editor lists every operator in the season pool; the checkbox
only decides whether the side panel shows that operator.  After a scan we
filter the list with one fixed rule:

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

DEFAULT_TRAIT = "持续叠加"
TIER_ROMAN = {1: "Ⅰ", 2: "Ⅱ", 3: "Ⅲ", 4: "Ⅳ", 5: "Ⅴ", 6: "Ⅵ", 7: "Ⅶ"}


def tier_roman(tier: int) -> str:
    return TIER_ROMAN.get(int(tier), str(tier))


def all_operators(game: GameData) -> list[Operator]:
    """Every operator in the pool, by tier from high to low (editor roster)."""
    return sorted(game.operators.values(), key=lambda op: (-op.tier, op.name))


def all_stackers(game: GameData) -> list[Operator]:
    """持续叠加的叠层手：新建名单时默认勾选的就是这些人。"""
    return [op for op in all_operators(game) if op.trait_type == DEFAULT_TRAIT]


def default_names(game: GameData) -> list[str]:
    return [op.name for op in all_stackers(game)]


@dataclass
class StackerPreset:
    path: Path
    names: list[str] = field(default_factory=list)
    overrides: dict[str, dict[str, list[str]]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path, game: GameData) -> "StackerPreset":
        path = Path(path)
        if not path.exists():
            return cls(path, default_names(game))
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            return cls(path, default_names(game))

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

        # Old lineup preset: discard the old model and seed the default watch-list.
        return cls(path, default_names(game))

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


def matches_covenant_filter(op: Operator, covenants: Iterable[str]) -> bool:
    """筛选区勾选的盟约之间是「且」：干员要同时拥有勾上的每个盟约。

    不勾任何盟约时不算命中（默认不显示干员）。
    """
    wanted = set(covenants)
    return bool(wanted) and wanted.issubset(op.covenants)


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
    """Return (remaining, banned_in_preset, unknown_names).

    名单现在可以放任何干员（编辑器列出全部），所以这里只看盟约禁用，
    不再按特质筛人；数据里没有的名字才算 unknown。
    """
    rows: list[Operator] = []
    banned: set[str] = set()
    unknown: list[str] = []
    seen: set[str] = set()
    for name in preset.names:
        if name in seen:
            continue
        seen.add(name)
        op = effective_operator(game, name, preset)
        if op is None:
            unknown.append(name)
            continue
        if not operator_available(op, rec):
            banned.add(name)
            continue
        rows.append(op)
    rows.sort(key=lambda op: (-op.tier, op.name))
    return rows, banned, unknown
