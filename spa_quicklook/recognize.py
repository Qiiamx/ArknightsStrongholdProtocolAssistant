"""Prep-screen recognition: covenant states + enemy tags.

This module deliberately knows nothing about the recommendation layer. It
returns a small, versioned dict; the stacker panel consumes it independently.

Target screen: 「确认本局信息」 (prep briefing). The covenant strip is read by
OCR names + icon colour; the enemy panel is read by OCR. The long banned-operator
avatar grid is NOT read here (that needs avatar template matching and is a
separate problem; see PLAN.md §3).

Coordinate basis: the client area is 16:9; all maths are normalised, so 1280x720,
1920x1080 and 2560x1440 work without hard-coded pixel bands.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from .ocr import OcrEngine, OcrLine

MAJORS = [
    "炎",
    "萨尔贡",
    "维多利亚",
    "谢拉格",
    "拉特兰",
    "阿戈尔",
    "叙拉古",
    "卡西米尔",
    "乌萨斯",
    "伊比利亚",
    "莱塔尼亚",
]
MINORS = [
    "精准",
    "迅捷",
    "灵巧",
    "坚守",
    "助力",
    "远见",
    "奇迹",
    "投资人",
    "突袭",
    "不屈",
    "调和",
    "协防干员",
    "独行",
    "绝技",
    "聚合",
    "奥术",
]
ENEMY_TAGS = ["特异", "飞行", "频次", "元素", "持续", "隐匿", "折射", "致命", "复苏"]
ALIASES = {
    "协防干员": ["协防干员", "协防"],
    "投资人": ["投资人"],
    "绝技": ["绝技"],
}


@dataclass(frozen=True)
class Grid:
    first: float
    spacing: float
    tolerance: float
    icon_to_label: float
    core_label_to_add: float
    first_add_label: float
    row_spacing: float

    @classmethod
    def preparation(cls, w: int, h: int) -> "Grid":
        return cls(
            first=w * 0.046875,
            spacing=w * 0.0671875,
            tolerance=w * 0.025,
            icon_to_label=h * 0.075,
            core_label_to_add=h * 0.07,
            first_add_label=h * 0.219,
            row_spacing=h * 0.20,
        )

    @classmethod
    def from_titles(cls, w: int, h: int, core: OcrLine, additional: OcrLine) -> "Grid | None":
        section = additional.cy - core.cy
        if not (0.18 * h <= section <= 0.38 * h):
            return None
        title_left = max(core.x, additional.x)
        return cls(
            first=title_left - section * 0.05,
            spacing=section * 0.41,
            tolerance=section * 0.15,
            icon_to_label=section * 0.25,
            core_label_to_add=section * 0.25,
            first_add_label=section * 0.75,
            row_spacing=section * 0.69,
        )

    def column_center(self, col: int) -> float:
        return self.first + col * self.spacing

    def nearest_column(self, x: float) -> int:
        return int(round((x - self.first) / self.spacing))


def _compact(text: str) -> str:
    return "".join(text.split())


def read_image(path: Path | str) -> np.ndarray | None:
    """cv2.imread replacement that works with non-ASCII Windows paths."""
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except Exception:
        return None


def write_image(path: Path | str, image: np.ndarray) -> None:
    ok, buf = cv2.imencode(".png", image)
    if ok:
        buf.tofile(str(path))


def _find_name(text: str, name: str) -> int:
    idx = text.find(name)
    if idx >= 0:
        return idx
    for alias in ALIASES.get(name, ()):
        idx = text.find(alias)
        if idx >= 0:
            return idx
    if len(name) >= 3:
        # OCR sometimes drops/inserts one character; only accept very close matches.
        best = difflib.SequenceMatcher(None, text, name).ratio()
        if best >= 0.80:
            return text.find(name[:2]) if name[:2] in text else -1
    return -1


def _classify_icon(image: np.ndarray, cx: float, cy: float, w: int, h: int) -> str:
    """Return 'available' | 'disabled' | 'unknown' (BGR image)."""
    half_w = int(w * 0.022)
    half_h = int(h * 0.039)
    x0, x1 = int(cx) - half_w, int(cx) + half_w
    y0, y1 = int(cy) - half_h, int(cy) + half_h
    if x1 <= x0 or y1 <= y0 or x0 < 0 or y0 < 0 or x1 > image.shape[1] or y1 > image.shape[0]:
        return "unknown"
    icon = image[y0:y1, x0:x1]
    b, g, r = icon[:, :, 0].astype(int), icon[:, :, 1].astype(int), icon[:, :, 2].astype(int)
    cyan = float(
        np.mean((g > 100) & (g - r > 45) & (b - r > 25))
    )
    gray = float(
        np.mean(
            (np.minimum(np.minimum(b, g), r) > 80)
            & (np.maximum(np.maximum(b, g), r) - np.minimum(np.minimum(b, g), r) < 65)
        )
    )
    bx0, bx1 = int(cx + w * 0.013), int(cx + w * 0.034)
    by0, by1 = int(cy - h * 0.060), int(cy - h * 0.019)
    if bx1 <= bx0 or by1 <= by0 or bx0 < 0 or by0 < 0 or bx1 > image.shape[1] or by1 > image.shape[0]:
        white = 0.0
    else:
        badge = image[by0:by1, bx0:bx1]
        bb, bg, br = badge[:, :, 0].astype(int), badge[:, :, 1].astype(int), badge[:, :, 2].astype(int)
        white = float(
            np.mean(
                (np.minimum(np.minimum(bb, bg), br) > 140)
                & (np.maximum(np.maximum(bb, bg), br) - np.minimum(np.minimum(bb, bg), br) < 50)
            )
        )
    if cyan >= 0.04:
        # 规程's available icon is a thin cyan ring (~8-10% of the 84x84 box),
        # not a filled block; 0.04 catches it while ignoring occasional blue UI.
        return "available"
    if cyan < 0.015 and gray > 0.08:
        # Unavailable icons are grey discs.  The white roster badge is not always
        # visible in scaled/compressed captures, so grey alone is enough.
        return "disabled"
    return "unknown"


def _detect_icon_row(image: np.ndarray, y0: int, y1: int) -> float | None:
    """Find the vertical centre of the icon row between two text anchors.

    The prep screen's icon row is a band of cyan/grey pixels.  Text labels are
    outside [y0, y1], so a simple colour mask is enough; this removes the need
    to hard-code the icon-to-label offset (which differs between the 确认本局信息
    page and the in-game top bar).
    """
    h, w = image.shape[:2]
    y0, y1 = max(0, int(y0)), min(h, int(y1))
    if y1 - y0 < 20:
        return None
    crop = image[y0:y1, : int(w * 0.88)]
    b, g, r = crop[:, :, 0].astype(int), crop[:, :, 1].astype(int), crop[:, :, 2].astype(int)
    cyan = (g > 100) & (g - r > 45) & (b - r > 25)
    gray = (
        (np.minimum(np.minimum(b, g), r) > 80)
        & (np.maximum(np.maximum(b, g), r) - np.minimum(np.minimum(b, g), r) < 65)
    )
    scores = (cyan | gray).sum(axis=1)
    if scores.size == 0 or scores.max() < max(8, int(w * 0.02)):
        return None
    peak = int(scores.argmax())
    threshold = max(scores.max() * 0.35, 8)
    top = peak
    while top > 0 and scores[top - 1] >= threshold:
        top -= 1
    bottom = peak
    while bottom + 1 < scores.size and scores[bottom + 1] >= threshold:
        bottom += 1
    return y0 + (top + bottom) / 2.0


def _median(values: list[float]) -> float:
    values = sorted(values)
    n = len(values)
    return values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2.0


def _read_slots_by_labels(
    image: np.ndarray,
    lines: list[OcrLine],
    names: list[str],
    label_y: float,
    icon_y: float,
    rows: int,
    row_spacing: float,
    band: float,
) -> tuple[list[str], list[str], list[str]] | None:
    """Bind OCR names directly to their x positions and classify the icon there.

    This is the real-prep path: columns are *measured* from the labels, so a
    changed UI scale/pitch (e.g. 116 px instead of a fixed 129 px) still works.
    Returns None when there are too few names to infer a column axis.
    """
    h, w = image.shape[:2]
    candidates: list[tuple[int, str, float]] = []
    for line in lines:
        if not (label_y - band <= line.cy <= label_y + (rows - 1) * row_spacing + band):
            continue
        text = _compact(line.text)
        if not text or "盟约" in text:
            continue
        found: list[tuple[int, str]] = []
        for name in sorted(names, key=len, reverse=True):
            idx = _find_name(text, name)
            if idx >= 0:
                found.append((idx, name))
        found.sort()
        chosen: list[tuple[int, str]] = []
        for idx, name in found:
            if any(abs(idx - i) < len(n) for i, n in chosen):
                continue
            chosen.append((idx, name))
        if not chosen:
            continue
        row = int(round((line.cy - label_y) / row_spacing)) if rows > 1 else 0
        if row < 0 or row >= rows:
            continue
        for idx, name in chosen:
            x = line.x + line.w * (idx + len(name) / 2.0) / max(len(text), 1)
            candidates.append((row, name, x))
    if len(candidates) < 4:
        return None

    available: list[str] = []
    disabled: list[str] = []
    unknown: list[str] = []
    for row in range(rows):
        group = [(name, x) for r, name, x in candidates if r == row]
        if len(group) < 2:
            continue
        xs = sorted(x for _, x in group)
        diffs = [b - a for a, b in zip(xs, xs[1:]) if b - a > w * 0.02]
        if len(diffs) < 2:
            continue
        pitch = _median(diffs)
        if not (w * 0.03 <= pitch <= w * 0.12):
            continue
        # Cluster x estimates into columns.  Merged OCR boxes give approximate
        # centres; clustering recovers the actual icon centre.
        xs_sorted = sorted(x for _, x in group)
        clusters: list[list[float]] = [[xs_sorted[0]]]
        for x in xs_sorted[1:]:
            if x - clusters[-1][-1] <= pitch * 0.55:
                clusters[-1].append(x)
            else:
                clusters.append([x])
        centers = [sum(c) / len(c) for c in clusters]
        for name, x in group:
            center = min(centers, key=lambda c: abs(c - x))
            if abs(center - x) > pitch * 0.65:
                unknown.append(name)
                continue
            cy = icon_y + row * row_spacing
            state = _classify_icon(image, center, cy, w, h)
            if state == "available":
                available.append(name)
            elif state == "disabled":
                disabled.append(name)
            else:
                unknown.append(name)

    if len(available) + len(disabled) < 3:
        return None
    for name in names:
        if name not in available and name not in disabled and name not in unknown:
            unknown.append(name)
    return available, disabled, unknown


def _first_label_x(
    lines: list[OcrLine], names: list[str], label_y: float, band: float
) -> float | None:
    xs: list[float] = []
    for line in lines:
        if abs(line.cy - label_y) > band:
            continue
        text = _compact(line.text)
        for name in sorted(names, key=len, reverse=True):
            idx = _find_name(text, name)
            if idx >= 0:
                xs.append(line.x + line.w * (idx + len(name) / 2.0) / max(len(text), 1))
    return min(xs) if xs else None


def _read_slots(
    image: np.ndarray,
    lines: list[OcrLine],
    names: list[str],
    label_y: float,
    grid: Grid,
    rows: int,
    band: float,
    columns: int = 10,
) -> tuple[dict[int, str], list[str], list[str], list[str]]:
    """Read names + icon states for a covenant section.

    Returns (slot->name, available, disabled, unknown). OCR often merges adjacent
    names into one box; when that happens we anchor the run on the box's right
    edge and assign consecutive columns instead of trusting proportional x.
    """
    height, width = image.shape[:2]
    slots: dict[int, str] = {}
    ambiguous: list[str] = []
    for line in lines:
        if not (label_y - band <= line.cy <= label_y + (rows - 1) * grid.row_spacing + band):
            continue
        text = _compact(line.text)
        if not text or "盟约" in text:
            continue
        found: list[tuple[int, str]] = []
        for name in sorted(names, key=len, reverse=True):
            idx = _find_name(text, name)
            if idx >= 0:
                found.append((idx, name))
        found.sort()
        chosen: list[tuple[int, str]] = []
        for idx, name in found:
            if any(abs(idx - i) < len(n) for i, n in chosen):
                continue
            chosen.append((idx, name))
        if not chosen:
            continue

        row = int(round((line.cy - label_y) / grid.row_spacing)) if rows > 1 else 0
        if row < 0 or row >= rows:
            continue

        estimates = [
            line.x + line.w * (idx + len(name) / 2.0) / max(len(text), 1)
            for idx, name in chosen
        ]
        cols = [grid.nearest_column(x) for x in estimates]
        valid = all(0 <= c < columns for c in cols) and len(set(cols)) == len(cols)
        if valid:
            valid = all(
                abs(x - grid.column_center(c)) <= grid.tolerance
                for x, c in zip(estimates, cols)
            )
        if not valid and len(chosen) > 1:
            # Merged box: anchor the run on the right edge, where the last name's
            # centre is close to a column centre.
            char_w = line.w / max(len(text), 1)
            last_col = grid.nearest_column(line.x + line.w - char_w / 2.0)
            start = last_col - len(chosen) + 1
            if 0 <= start and last_col < columns:
                cols = list(range(start, last_col + 1))
                valid = True
        if not valid:
            continue

        for (idx, name), col in zip(chosen, cols):
            slot = row * columns + col
            if slot >= len(names):
                continue
            if slot in slots:
                ambiguous.append(name)
                continue
            slots[slot] = name

    available: list[str] = []
    disabled: list[str] = []
    unknown: list[str] = []
    for slot, name in slots.items():
        row = slot // columns
        col = slot % columns
        cy = label_y + row * grid.row_spacing - grid.icon_to_label
        state = _classify_icon(image, grid.column_center(col), cy, width, height)
        if state == "available":
            available.append(name)
        elif state == "disabled":
            disabled.append(name)
        else:
            unknown.append(name)
    for name in names:
        if name not in slots.values() and name not in ambiguous:
            unknown.append(name)
    return slots, available, disabled, unknown


def _read_grouped_core(
    image: np.ndarray,
    lines: list[OcrLine],
    add_title: OcrLine | None,
    h: int,
) -> tuple[list[str], list[str], list[str]] | None:
    """Read the core strip when it is split into two rows/groups.

    个人模拟 / 规程高等级 splits the core covenants into two groups (often two
    rows); there is no reliable 「核心盟约」 text anchor.  We find label rows by
    OCR, measure each row's icon band from pixels, and classify each name.
    """
    limit = add_title.y if add_title is not None else h * 0.68
    items: list[tuple[str, float, float]] = []
    for line in lines:
        if line.cy < h * 0.08 or line.cy > limit:
            continue
        text = _compact(line.text)
        if not text or "盟约" in text:
            continue
        # One OCR box can hold several labels ("伊比利亚莱塔尼亚维多利亚");
        # keep every name it contains, not just the first match.
        found: list[tuple[int, str]] = []
        for name in sorted(MAJORS, key=len, reverse=True):
            idx = _find_name(text, name)
            if idx >= 0:
                found.append((idx, name))
        found.sort()
        chosen: list[tuple[int, str]] = []
        for idx, name in found:
            if any(abs(idx - i) < len(n) for i, n in chosen):
                continue
            chosen.append((idx, name))
        for idx, name in chosen:
            x = line.x + line.w * (idx + len(name) / 2.0) / max(len(text), 1)
            items.append((name, x, line.cy))
    if len(items) < 4:
        return None

    # Group labels into rows by y; split into separate groups only by vertical
    # position (left/right core groups share a row but that is fine).
    items.sort(key=lambda item: item[2])
    rows: list[list[tuple[str, float, float]]] = []
    for item in items:
        if not rows or abs(item[2] - rows[-1][-1][2]) > h * 0.025:
            rows.append([item])
        else:
            rows[-1].append(item)

    available: list[str] = []
    disabled: list[str] = []
    matched: set[str] = set()
    for row in rows:
        if len(row) < 2:
            continue
        label_y = sum(item[2] for item in row) / len(row)
        icon_y = _detect_icon_row(
            image,
            int(label_y - h * 0.24),
            int(label_y - h * 0.02),
        )
        if icon_y is None:
            continue
        names = [item[0] for item in sorted(row, key=lambda item: item[1])]
        measured = _read_slots_by_labels(
            image,
            lines,
            names,
            label_y,
            icon_y,
            rows=1,
            row_spacing=0,
            band=h * 0.032,
        )
        if not measured:
            continue
        a, d, _u = measured
        available.extend(x for x in a if x not in available)
        disabled.extend(x for x in d if x not in disabled)
        matched.update(a)
        matched.update(d)

    if not matched:
        return None
    unknown = [name for name in MAJORS if name not in matched]
    return available, disabled, unknown


def _canonical_boss(text: str) -> str:
    text = text.replace(":", "：").strip()
    bare = text.strip("“”\"'‘’ ")
    mapping = {
        "假想敌：统": "假想敌：铳",
        "假想敌：銃": "假想敌：铳",
        "假想敌：鰭": "假想敌：鳍",
        "萨米的意志": "“萨米的意志”",
        "阿利斯泰尔,帝国余晖": "阿利斯泰尔，帝国余晖",
    }
    if bare in mapping:
        return mapping[bare]
    return mapping.get(text, text)


def _read_enemy(lines: list[OcrLine], w: int, h: int) -> dict[str, Any]:
    """Read the 本局遭遇敌方 block.

    Layout differs from the in-game top bar: the boss name is above the
    「敌方领袖」 label, and 特训敌人 tags sit on the right of the same block.
    Anchor on the section header instead of assuming a right-hand column.
    """
    ordered = sorted(lines, key=lambda x: (x.y, x.x))
    section = next((x for x in ordered if "本局遭遇敌方" in _compact(x.text) or "本局遭遇" in _compact(x.text)), None)
    leader = None
    tags: list[str] = []

    if section is not None:
        region = [
            x
            for x in ordered
            if section.y + h * 0.01 < x.y < section.y + h * 0.40 and x.x > w * 0.08
        ]
        for line in region:
            text = _compact(line.text)
            if not text or "敌方领袖" in text or "特训" in text:
                continue
            if line.y <= section.y + 1:
                continue
            if any(k in text for k in ("确认本局信息", "本局遭遇", "部分盟约", "当前延迟")):
                continue
            if len(text) < 3 or len(text) > 18:
                continue
            if any(k in text for k in ("混入", "自己", "无法", "话说", "？", "?", "，", "；")):
                continue
            leader = _canonical_boss(text)
            break
        for line in region:
            text = _compact(line.text)
            if "特训" not in text:
                continue
            for tag in ENEMY_TAGS:
                if tag in text and tag not in tags:
                    tags.append(tag)
    else:
        # Fallback: the in-game top bar uses an 「敌方领袖」 label.
        for line in ordered:
            text = _compact(line.text)
            if "敌方领袖" not in text:
                continue
            above = [x for x in ordered if x.cy < line.cy and x.cy > line.cy - h * 0.08]
            if above:
                leader = _canonical_boss(_compact(above[-1].text))
            else:
                below = [
                    x
                    for x in ordered
                    if line.cy < x.cy < line.cy + h * 0.10
                    and 2 <= len(_compact(x.text)) <= 16
                    and not any(tag in _compact(x.text) for tag in ENEMY_TAGS)
                    and not any(_find_name(_compact(x.text), n) >= 0 for n in MAJORS + MINORS)
                ]
                if below:
                    leader = _canonical_boss(_compact(below[0].text))
            break
        for line in ordered:
            text = _compact(line.text)
            for tag in ENEMY_TAGS:
                if tag in text and ("特训" in text or len(text) <= 8) and tag not in tags:
                    tags.append(tag)

    tags.sort(key=ENEMY_TAGS.index)
    return {"leader": leader, "tags": tags}


def recognize(image_bgr: np.ndarray, ocr: OcrEngine, debug_path: Path | str | None = None) -> dict[str, Any]:
    h, w = image_bgr.shape[:2]
    lines = ocr.read(image_bgr)
    joined = "".join(_compact(x.text) for x in lines)
    warnings: list[str] = []

    if "确认本局信息" not in joined and not ("核心盟约" in joined and "附加盟约" in joined):
        return {
            "ok": False,
            "screen": "unknown",
            "warnings": ["未识别到『确认本局信息』准备页，请停在准备界面再识别"],
            "texts": [x.text for x in lines],
        }

    core_title = next((x for x in lines if "核心盟约" in _compact(x.text) or "核心盟" in _compact(x.text)), None)
    add_title = next((x for x in lines if "附加盟约" in _compact(x.text) or "附加盟" in _compact(x.text)), None)
    if core_title is None and add_title is None:
        return {
            "ok": False,
            "screen": "prep",
            "warnings": ["未找到『核心盟约』/『附加盟约』标题；请显示完整盟约区域"],
            "texts": [x.text for x in lines],
        }

    band = h * 0.030
    grid = (
        (Grid.from_titles(w, h, core_title, add_title) if core_title is not None and add_title is not None else None)
        or Grid.preparation(w, h)
    )
    core_label_y = (add_title.y - grid.core_label_to_add) if (core_title and add_title) else 0.0
    core_icon_y = 0.0

    core_available: list[str] = []
    core_disabled: list[str] = []
    core_unknown: list[str] = list(MAJORS)
    core_icon_y = 0.0
    # Two ways to read the core strip; keep both and take the one that explains
    # more slots.  OCR merges neighbouring labels into one box fairly often
    # (e.g. 「伊比利亚莱塔尼亚维多利亚」), so the title-anchored path that splits
    # a merged box by column is preferred whenever the title is on screen.
    measured_core: tuple[list[str], list[str], list[str]] | None = None
    if core_title is not None and add_title is not None:
        # The real prep page indents the covenant strip; title-derived x is only
        # a fallback.  Measure the icon row from pixels and columns from names.
        core_icon_y = (
            _detect_icon_row(image_bgr, int(core_title.cy + h * 0.015), int(core_label_y - h * 0.025))
            or core_label_y - grid.icon_to_label
        )
        core_measured = None
        first_core_x = _first_label_x(lines, MAJORS, core_label_y, band)
        if first_core_x is not None and first_core_x > core_title.x + w * 0.03:
            core_measured = _read_slots_by_labels(
                image_bgr, lines, MAJORS, core_label_y, core_icon_y, rows=1, row_spacing=grid.row_spacing, band=band
            )
        if core_measured:
            measured_core = core_measured
        else:
            _, core_available, core_disabled, core_unknown = _read_slots(
                image_bgr, lines, MAJORS, core_label_y, grid, rows=1, band=band, columns=len(MAJORS)
            )
            measured_core = (core_available, core_disabled, core_unknown)
    grouped_core = _read_grouped_core(image_bgr, lines, add_title, h)
    choices = [c for c in (measured_core, grouped_core) if c is not None]
    if choices:
        # max() keeps the first of equal scores, i.e. the title-anchored reading.
        core_available, core_disabled, core_unknown = max(
            choices, key=lambda c: len(c[0]) + len(c[1])
        )

    minor_available: list[str] = []
    minor_disabled: list[str] = []
    minor_unknown: list[str] = list(MINORS)
    add_label_y = 0.0
    add_icon_y = 0.0
    if add_title is not None:
        # The briefing scrolls: when the user scrolls to 附加盟约 the core title
        # may be off-screen.  Find the first additional label row from OCR.
        add_y_candidates: list[float] = []
        for line in lines:
            if line.cy <= add_title.cy + h * 0.01:
                continue
            text = _compact(line.text)
            if any(_find_name(text, name) >= 0 for name in MINORS):
                add_y_candidates.append(line.cy)
        add_label_y = min(add_y_candidates) if add_y_candidates else add_title.y + grid.first_add_label
        add_icon_y = (
            _detect_icon_row(
                image_bgr,
                int(add_title.cy + h * 0.015),
                int(max(add_title.cy + h * 0.03, add_label_y - h * 0.025)),
            )
            or add_label_y - grid.icon_to_label
        )
        minor_measured = None
        first_minor_x = _first_label_x(lines, MINORS, add_label_y, band)
        if first_minor_x is not None and first_minor_x > add_title.x + w * 0.03:
            minor_measured = _read_slots_by_labels(
                image_bgr,
                lines,
                MINORS,
                add_label_y,
                add_icon_y,
                rows=(len(MINORS) + 9) // 10,
                row_spacing=grid.row_spacing,
                band=band,
            )
        if minor_measured:
            minor_available, minor_disabled, minor_unknown = minor_measured
        else:
            _, minor_available, minor_disabled, minor_unknown = _read_slots(
                image_bgr, lines, MINORS, add_label_y, grid, rows=(len(MINORS) + 9) // 10, band=band
            )

    if len(core_unknown) > 4:
        warnings.append(f"核心盟约 {len(core_unknown)} 格未识别，建议重新识别/手动核对")
    if len(minor_unknown) > 6:
        warnings.append(f"附加盟约 {len(minor_unknown)} 格未识别，建议重新识别/手动核对")

    enemy = _read_enemy(lines, w, h)
    if not enemy["tags"]:
        warnings.append("未读到敌人词条；请确认右侧『敌方领袖/特训敌人』区域可见")

    known = len(core_available) + len(core_disabled) + len(minor_available) + len(minor_disabled)
    total = len(MAJORS) + len(MINORS)
    confidence = round(known / total, 3)
    core_known = len(core_available) + len(core_disabled)
    knowledge = core_known + len(minor_available) + len(minor_disabled)
    # A scrollable briefing may show only one section; that is still useful.
    ok = knowledge >= 5 and bool(enemy["tags"])

    result: dict[str, Any] = {
        "ok": ok,
        "screen": "prep",
        "covenants": {
            "major": {
                "available": core_available,
                "disabled": core_disabled,
                "unknown": core_unknown,
            },
            "minor": {
                "available": minor_available,
                "disabled": minor_disabled,
                "unknown": minor_unknown,
            },
        },
        "enemy": enemy,
        "confidence": confidence,
        "warnings": warnings,
        "grid": {
            "first": round(grid.first, 1),
            "spacing": round(grid.spacing, 1),
            "tolerance": round(grid.tolerance, 1),
            "core_label_y": round(core_label_y, 1),
            "add_label_y": round(add_label_y, 1),
        },
        "texts": [x.text for x in lines],
    }

    if debug_path:
        debug = image_bgr.copy()
        # Draw the measured rows and label centres, not the title-derived grid:
        # the real briefing indents the covenant strip, so grid boxes would lie.
        if core_label_y:
            cv2.line(debug, (0, int(core_icon_y)), (w, int(core_icon_y)), (0, 255, 0), 2)
            cv2.line(debug, (0, int(core_label_y)), (w, int(core_label_y)), (255, 128, 0), 1)
        for line in lines:
            if core_label_y and abs(line.cy - core_label_y) <= band:
                cv2.circle(debug, (int(line.cx), int(core_icon_y)), 7, (255, 128, 0), 2)
            if add_label_y and abs(line.cy - add_label_y) <= band:
                cv2.circle(debug, (int(line.cx), int(add_icon_y)), 7, (0, 255, 0), 2)
        for line in lines:
            cv2.rectangle(debug, (line.x, line.y), (line.x + line.w, line.y + line.h), (0, 200, 255), 1)
        write_image(debug_path, debug)

    return result
