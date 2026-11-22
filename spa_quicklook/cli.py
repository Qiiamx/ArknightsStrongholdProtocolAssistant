"""SPA QuickLook CLI: capture -> recognize -> recommend (read-only)."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from .paths import counter_data_path, default_preset, game_data_path  # noqa: E402
from .recommender import GameData, Preset, Recognition, recommend  # noqa: E402
from spa_quicklook.capture import capture_client_area, find_game  # noqa: E402
from spa_quicklook.ocr import OcrEngine  # noqa: E402
from spa_quicklook.recognize import read_image, recognize  # noqa: E402


def _to_recognition_dict(rec: dict) -> tuple[dict, list[str]]:
    cov = rec.get("covenants") or {}
    major = cov.get("major") or {}
    minor = cov.get("minor") or {}
    unknown = list(major.get("unknown") or []) + list(minor.get("unknown") or [])
    minor_visible = len(minor.get("available") or []) + len(minor.get("disabled") or [])
    if minor_visible == 0:
        # Additional covenants are below the fold; do not turn "not visible" into
        # "banned" or every preset would be rejected.
        minor_banned = []
        unknown = [u for u in unknown if u not in (minor.get("unknown") or [])]
    else:
        minor_banned = list(minor.get("disabled") or []) + list(minor.get("unknown") or [])
    out = {
        "mode": "",  # deliberately mode-independent; the preset can override
        "season": "规程",
        "sequence": "序列Ⅰ",
        "major": {
            "available": major.get("available") or [],
            # Unknown slots stay unknown; only measured-disabled ones gate the
            # recommender. The recognition output exposes unknown separately.
            "banned": list(major.get("disabled") or []),
        },
        "minor": {
            "available": minor.get("available") or [],
            "banned": minor_banned,
        },
        "enemy": rec.get("enemy") or {},
        "bannedOperators": [],  # avatar-grid reading is a separate phase
        "uncertain": unknown,
        "confidence": rec.get("confidence", 0.0),
    }
    return out, unknown


def _human(result: dict, rec: dict) -> str:
    lines = []
    lines.append(f"识别：{rec.get('screen')} · 置信度 {rec.get('confidence')}")
    enemy = rec.get("enemy") or {}
    lines.append(f"敌人：{enemy.get('leader') or '—'} · {'/'.join(enemy.get('tags') or []) or '未读到'}")
    cov = rec.get("covenants") or {}
    for cls, label in (("major", "大盟约"), ("minor", "小盟约")):
        part = cov.get(cls) or {}
        lines.append(
            f"{label}可用：{'、'.join(part.get('available') or []) or '—'}"
        )
        lines.append(
            f"{label}禁用：{'、'.join(part.get('disabled') or []) or '—'}"
        )
        if part.get("unknown"):
            lines.append(f"{label}未识别：{'、'.join(part['unknown'])}")
    for warning in rec.get("warnings") or []:
        lines.append(f"[!] {warning}")
    lines.append("")
    lines.append("阵容推荐：")
    for i, item in enumerate((result.get("recommendations") or [])[:5], 1):
        mark = "[OK]" if item["feasible"] else "[X]"
        lines.append(f"  {i}. {mark} {item['name']}  score={item['score']}")
        if item.get("rejected_because"):
            lines.append(f"     拒绝：{item['rejected_because']}")
        for reason in item.get("reasons") or []:
            lines.append(f"     - {reason}")
        for warning in item.get("warnings") or []:
            lines.append(f"     ! {warning}")
    plan = result.get("covenant_plan") or {}
    if plan.get("core"):
        lines.append("")
        lines.append("大盟约备选：" + "、".join(f"{x['covenant']}({x['pool']})" for x in plan["core"]))
    if plan.get("minor"):
        lines.append("小盟约备选：" + "、".join(f"{x['covenant']}({x['pool']})" for x in plan["minor"]))
    for warning in result.get("warnings") or []:
        lines.append(f"[!] {warning}")
    return "\n".join(lines)


def _configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, help="offline prep-screen image")
    parser.add_argument("--live", action="store_true", help="capture the running game (read-only)")
    parser.add_argument("--ui", action="store_true", help="open the sticky side panel")
    parser.add_argument("--pid", type=int, default=None)
    parser.add_argument("--debug", type=Path, help="write a debug overlay PNG")
    parser.add_argument(
        "--preset",
        type=Path,
        default=default_preset(),
    )
    parser.add_argument(
        "--game-data",
        type=Path,
        default=game_data_path(),
    )
    parser.add_argument(
        "--counter-data",
        type=Path,
        default=counter_data_path(),
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-recommend", action="store_true", help="only recognize")
    args = parser.parse_args(argv)

    if args.ui or (not args.image and not args.live):
        from .ui import launch_ui

        return launch_ui(args.preset, pid=args.pid)
    if not args.image and not args.live:
        parser.error("pass --image PATH or --live")

    tmp = None
    try:
        if args.live:
            if args.pid is None:
                try:
                    args.pid = int(find_game()["pid"])
                except Exception as exc:
                    print(f"找不到游戏窗口：{exc}", file=sys.stderr)
                    return 4
            tmp = Path(tempfile.mkdtemp()) / "capture.png"
            try:
                capture_client_area(tmp, args.pid)
            except Exception as exc:
                print(f"截图失败：{exc}", file=sys.stderr)
                return 4
            image_path = tmp
        else:
            image_path = args.image
        image = read_image(image_path)
        if image is None:
            print(f"无法读取图片：{image_path}", file=sys.stderr)
            return 2
        rec = recognize(image, OcrEngine(), debug_path=args.debug)
        if not rec.get("ok"):
            print(json.dumps(rec, ensure_ascii=False, indent=2) if args.json else "\n".join(
                ["识别失败：", *rec.get("warnings", [])]
            ))
            return 3
        if args.no_recommend:
            print(json.dumps(rec, ensure_ascii=False, indent=2))
            return 0

        rec_dict, _ = _to_recognition_dict(rec)
        game = GameData(args.game_data, args.counter_data)
        preset = Preset.load(args.preset)
        result = recommend(game, Recognition.from_dict(rec_dict), preset)
        payload = {"recognition": rec, "recommendation": result}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(_human(result, rec))
        return 0
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
                tmp.parent.rmdir()
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
