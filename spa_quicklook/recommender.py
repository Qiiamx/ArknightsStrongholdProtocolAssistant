#!/usr/bin/env python
"""SPA QuickLook — reference recommender for 卫戍协议·规程.

This is the *novel layer* of issue #19366: given
  (a) the prep-screen recognition result (banned/available covenants, enemy tags,
      optionally the banned-operator list), and
  (b) a user-authored preset JSON,
rank the user's lineups and produce a "main + secondary covenant" plan.

It deliberately does NOT capture the screen or OCR. The production plan
(docs/PLAN.md) puts this logic behind the existing Apache-2.0
StrongholdAssistant recognition layer, or behind a standalone WGC+RapidOCR
capture layer if a separate tool is preferred.

Run:
    python prototype/recommender.py \
        --preset prototype/presets.example.json \
        --recognition prototype/recognition.example.json \
        --game-data research/data/covenants_operators_regulation.json \
        --counter-data research/data/enemy_counter_matrix.json

Tests:
    python -m unittest prototype/test_recommender.py -v
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

SEASON = "卫戍协议·规程"
SEQUENCE = "序列Ⅰ"


# ---------------------------------------------------------------------------
# static game data
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# preset
# ---------------------------------------------------------------------------


@dataclass
class Lineup:
    id: str
    name: str
    modes: list[str]
    core: dict[str, int]
    minor: dict[str, int]
    required_operators: list[str]
    core_operators: list[str]
    enemy_fit: dict[str, float]
    priority: float
    notes: str = ""
    primary_core: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Lineup":
        ops = d.get("operators") or {}
        # "optional" was removed from the editor; old presets are migrated into
        # core operators so existing files keep their intended roster.
        core_operators = list(ops.get("core") or ())
        for name in ops.get("optional") or ():
            if name not in core_operators:
                core_operators.append(name)
        return cls(
            id=d["id"],
            name=d.get("name") or d["id"],
            modes=list(d.get("modes") or ()),
            core=dict(d.get("core") or {}),
            minor=dict(d.get("minor") or {}),
            required_operators=list(ops.get("required") or ()),
            core_operators=core_operators,
            enemy_fit=dict(d.get("enemyFit") or {}),
            priority=float(d.get("priority") or 0),
            notes=d.get("notes") or "",
            primary_core=d.get("primaryCore"),
        )


@dataclass
class Preset:
    scope: dict[str, Any]
    lineups: list[Lineup]
    weights: dict[str, float]
    synergy_pairs: list[list[str]]
    availability_rule: str

    @classmethod
    def load(cls, path: Path) -> "Preset":
        d = json.loads(path.read_text(encoding="utf-8"))
        scope = d.get("scope") or {}
        return cls(
            scope=scope,
            lineups=[Lineup.from_dict(x) for x in d.get("lineups", [])],
            weights={
                "priority": 1.0,
                "enemy": 2.0,
                "activation": 1.5,
                "banResilience": 0.5,
                **{k: float(v) for k, v in (d.get("weights") or {}).items()},
            },
            synergy_pairs=[list(p) for p in (d.get("synergyPairs") or ())],
            availability_rule=scope.get("availabilityRule") or "anyCoreBanned",
        )


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------


@dataclass
class LineupResult:
    lineup: Lineup
    score: float
    feasible: bool
    core_plan: list[dict[str, Any]]
    minor_plan: list[dict[str, Any]]
    reasons: list[str]
    warnings: list[str]
    rejected_because: str | None = None


def _threshold(game: GameData, name: str, default: int = 2) -> int:
    c = game.covenants.get(name)
    return int(c.activate_count or default) if c else default


def _available_operator_names(
    game: GameData, rec: Recognition, rule: str
) -> tuple[set[str], set[str]]:
    """Return (available, banned) names. The recognized banned list wins if present."""
    by_rule_banned = banned_operators_by_rule(game, rec, rule)
    if rec.banned_operators:
        # Directly recognized list is authoritative; add it to the rule-based set
        # for operators we can see, but keep unknown names visible as warnings.
        banned = set(rec.banned_operators)
        available = set(game.operators) - banned
        return available, banned
    return set(game.operators) - by_rule_banned, by_rule_banned


def evaluate_lineup(
    game: GameData,
    rec: Recognition,
    preset: Preset,
    lineup: Lineup,
    available_ops: set[str],
    banned_ops: set[str],
) -> LineupResult:
    reasons: list[str] = []
    warnings: list[str] = []

    if lineup.modes and rec.mode and rec.mode not in lineup.modes:
        return LineupResult(lineup, 0, False, [], [], [], [], "模式不匹配")

    # 1. Hard gate: declared core covenants must still be available.
    #    A lineup may declare several cores; the primary one is the anchor.
    primary = lineup.primary_core or next(iter(lineup.core), None)
    banned_core = [c for c in lineup.core if c in rec.major_banned]
    if primary in rec.major_banned:
        return LineupResult(lineup, 0, False, [], [], [], [], f"主盟约 {primary} 被禁")
    if banned_core:
        warnings.append("核心盟约被禁：" + "、".join(banned_core))
        if len(banned_core) == len(lineup.core):
            return LineupResult(lineup, 0, False, [], [], [], [], "核心盟约全部被禁")

    # 2. Hard gate: declared minor covenants.
    banned_minor = [m for m in lineup.minor if m in rec.minor_banned]
    if banned_minor:
        warnings.append("附加盟约被禁：" + "、".join(banned_minor))
        if len(banned_minor) == len(lineup.minor):
            return LineupResult(lineup, 0, False, [], [], [], [], "附加盟约全部被禁")

    # 3. Hard gate: required operators.
    missing_required = [o for o in lineup.required_operators if o in banned_ops]
    if missing_required:
        return LineupResult(
            lineup,
            0,
            False,
            [],
            [],
            [],
            [],
            "必需干员被禁：" + "、".join(missing_required),
        )
    missing_core = [o for o in lineup.core_operators if o in banned_ops]
    if missing_core:
        warnings.append("核心干员被禁：" + "、".join(missing_core))

    # 4. Activation feasibility from the remaining pool.
    core_plan: list[dict[str, Any]] = []
    for name, want in lineup.core.items():
        if name in rec.major_banned:
            continue
        pool = [
            n
            for n in available_ops
            if name in game.operators[n].majors
        ]
        threshold = max(_threshold(game, name, 3), 3)
        feasible_count = len(pool)
        ok = feasible_count >= min(threshold, want) if want else feasible_count >= threshold
        core_plan.append(
            {
                "covenant": name,
                "desired": want,
                "threshold": threshold,
                "pool": feasible_count,
                "ok": ok,
                "operators": sorted(pool)[:12],
            }
        )
        if not ok:
            warnings.append(f"{name} 可用干员仅 {feasible_count}，低于激活门槛 {threshold}")

    minor_plan: list[dict[str, Any]] = []
    for name, want in lineup.minor.items():
        if name in rec.minor_banned:
            continue
        pool = [
            n
            for n in available_ops
            if name in game.operators[n].minors
        ]
        threshold = _threshold(game, name, 2)
        ok = len(pool) >= min(threshold, want) if want else len(pool) >= threshold
        minor_plan.append(
            {
                "covenant": name,
                "desired": want,
                "threshold": threshold,
                "pool": len(pool),
                "ok": ok,
                "operators": sorted(pool)[:12],
            }
        )
        if not ok:
            warnings.append(f"{name} 可用干员仅 {len(pool)}，低于激活门槛 {threshold}")

    hard_fail = any(not x["ok"] for x in core_plan + minor_plan)
    if hard_fail:
        # Not a rejection: the pool may still contain the lineup's own operators.
        # But it must not be presented as a confident recommendation.
        warnings.append("按当前卡池推算，部分盟约可能无法激活")

    # 5. Enemy fit.
    enemy_score = 0.0
    matched_tags: list[str] = []
    for tag in rec.enemy_tags:
        if tag in lineup.enemy_fit:
            value = float(lineup.enemy_fit[tag])
        else:
            row = game.counter.get(tag) or {}
            value = max(
                (row.get(c, 0.0) for c in set(lineup.core) | set(lineup.minor)),
                default=0.0,
            )
        if value:
            enemy_score += value
            matched_tags.append(f"{tag}{'+' if value > 0 else ''}{value:g}")
    if matched_tags:
        reasons.append("敌人词条匹配：" + "、".join(matched_tags))

    # 6. Ban resilience: prefer operators whose covenant identity survives a ban.
    core_names = [n for n in lineup.core_operators if n in game.operators]
    if core_names:
        multi = sum(1 for n in core_names if len(game.operators[n].majors) >= 2)
        ban_resilience = multi / len(core_names)
    else:
        ban_resilience = 0.0

    # 7. Activation quality: prefer 持续叠加 operators in the planned pool.
    wanted_covenants = set(lineup.core) | set(lineup.minor)
    stackers = sum(
        1
        for n in available_ops
        if game.operators[n].trait_type == "持续叠加"
        and wanted_covenants.intersection(game.operators[n].covenants)
    )
    activation_quality = min(1.0, stackers / 10.0)

    score = (
        preset.weights["priority"] * lineup.priority
        + preset.weights["enemy"] * enemy_score
        + preset.weights["activation"] * (activation_quality if not hard_fail else 0.0)
        + preset.weights["banResilience"] * ban_resilience
        - 2.0 * len(banned_core)
        - 1.0 * len(banned_minor)
        - 2.0 * len(missing_core)
    )
    reasons.insert(0, f"主盟约 {primary or '—'} 可用")
    if primary:
        reasons.append(f"卡池内可激活 {primary}：{next((x['pool'] for x in core_plan if x['covenant'] == primary), 0)} 人")
    if minor_plan:
        reasons.append("附加盟约：" + "、".join(f"{x['covenant']}({x['pool']})" for x in minor_plan))

    return LineupResult(
        lineup=lineup,
        score=round(score, 3),
        feasible=not hard_fail and not missing_core,
        core_plan=core_plan,
        minor_plan=minor_plan,
        reasons=reasons,
        warnings=warnings,
    )


def recommend(
    game: GameData,
    rec: Recognition,
    preset: Preset,
) -> dict[str, Any]:
    warnings = validate_recognition(rec, game)

    available_ops, banned_ops = _available_operator_names(game, rec, preset.availability_rule)
    unknown_banned = sorted(rec.banned_operators - set(game.operators))
    if unknown_banned:
        warnings.append("禁用干员名单中有未知名字：" + "、".join(unknown_banned))

    results = [
        evaluate_lineup(game, rec, preset, lineup, available_ops, banned_ops)
        for lineup in preset.lineups
    ]
    results.sort(key=lambda r: (r.feasible, r.score), reverse=True)

    # Covenant-only plan when no lineup is feasible, or as a compact summary.
    core_rank = []
    for c in game.majors:
        if c.name in rec.major_banned:
            continue
        pool = [n for n in available_ops if c.name in game.operators[n].majors]
        if len(pool) < 3:
            continue
        core_rank.append(
            {
                "covenant": c.name,
                "pool": len(pool),
                "score": len(pool) + _enemy_bonus_for(game, rec, {c.name}),
            }
        )
    core_rank.sort(key=lambda x: x["score"], reverse=True)

    minor_rank = []
    for c in game.minors:
        if c.name in rec.minor_banned or not c.bannable:
            continue
        pool = [n for n in available_ops if c.name in game.operators[n].minors]
        threshold = _threshold(game, c.name, 2)
        if len(pool) < threshold:
            continue
        minor_rank.append(
            {
                "covenant": c.name,
                "pool": len(pool),
                "score": len(pool) + _enemy_bonus_for(game, rec, {c.name}),
            }
        )
    minor_rank.sort(key=lambda x: x["score"], reverse=True)

    return {
        "ok": True,
        "recognition": {
            "mode": rec.mode,
            "major_banned": sorted(rec.major_banned),
            "minor_banned": sorted(rec.minor_banned),
            "enemy_tags": rec.enemy_tags,
            "enemy_leader": rec.enemy_leader,
            "confidence": rec.confidence,
        },
        "availability_rule": preset.availability_rule,
        "banned_operators_effective": sorted(banned_ops)[:200],
        "recommendations": [
            {
                "id": r.lineup.id,
                "name": r.lineup.name,
                "score": r.score,
                "feasible": r.feasible,
                "core_plan": r.core_plan,
                "minor_plan": r.minor_plan,
                "reasons": r.reasons,
                "warnings": r.warnings,
                "rejected_because": r.rejected_because,
                "notes": r.lineup.notes,
            }
            for r in results
        ],
        "covenant_plan": {
            "core": core_rank[:3],
            "minor": minor_rank[:5],
            "synergy_pairs": preset.synergy_pairs,
        },
        "warnings": warnings,
    }


def _enemy_bonus_for(game: GameData, rec: Recognition, covenants: set[str]) -> float:
    total = 0.0
    for tag in rec.enemy_tags:
        row = game.counter.get(tag) or {}
        total += max((row.get(c, 0.0) for c in covenants), default=0.0)
    return total * 2.0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _default_paths() -> tuple[Path, Path, Path]:
    return (
        HERE / "presets.example.json",
        HERE / "recognition.example.json",
        ROOT / "research" / "data" / "covenants_operators_regulation.json",
    )


def main(argv: list[str] | None = None) -> int:
    preset_default, rec_default, game_default = _default_paths()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset", type=Path, default=preset_default)
    parser.add_argument("--recognition", type=Path, default=rec_default)
    parser.add_argument("--game-data", type=Path, default=game_default)
    parser.add_argument(
        "--counter-data",
        type=Path,
        default=ROOT / "research" / "data" / "enemy_counter_matrix.json",
    )
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args(argv)

    game = GameData(args.game_data, args.counter_data)
    preset = Preset.load(args.preset)
    rec = Recognition.from_dict(json.loads(args.recognition.read_text(encoding="utf-8")))
    out = recommend(game, rec, preset)

    if args.json:
        json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0

    print(f"识别：{rec.mode} · 敌人 {'/'.join(rec.enemy_tags) or '未知'} · 置信度 {rec.confidence:.2f}")
    if out["warnings"]:
        print("⚠ " + "；".join(out["warnings"]))
    print("\n阵容推荐：")
    for i, r in enumerate(out["recommendations"], 1):
        mark = "✓" if r["feasible"] else "✗"
        print(f"  {i}. {mark} {r['name']}  score={r['score']}")
        if r["rejected_because"]:
            print(f"     拒绝：{r['rejected_because']}")
        for reason in r["reasons"]:
            print(f"     · {reason}")
        for warning in r["warnings"]:
            print(f"     ! {warning}")
    print("\n盟约备选（大盟约）：")
    for x in out["covenant_plan"]["core"]:
        print(f"  - {x['covenant']}  卡池 {x['pool']}  score={x['score']:.2f}")
    print("盟约备选（小盟约）：")
    for x in out["covenant_plan"]["minor"]:
        print(f"  - {x['covenant']}  卡池 {x['pool']}  score={x['score']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
