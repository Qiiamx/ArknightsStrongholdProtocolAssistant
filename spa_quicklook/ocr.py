"""OCR wrapper around RapidOCR."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass
class OcrLine:
    text: str
    score: float
    x: int
    y: int
    w: int
    h: int

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    def __str__(self) -> str:
        return f"{self.text}@{self.x},{self.y}"


class OcrEngine:
    def __init__(self, engine: Any | None = None):
        if engine is None:
            from rapidocr_onnxruntime import RapidOCR

            engine = RapidOCR()
        self.engine = engine

    def read(self, image_bgr: np.ndarray) -> list[OcrLine]:
        result, _ = self.engine(image_bgr)
        lines: list[OcrLine] = []
        for item in result or []:
            box, text, score = item
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            lines.append(
                OcrLine(
                    text=str(text),
                    score=float(score),
                    x=int(min(xs)),
                    y=int(min(ys)),
                    w=int(max(xs) - min(xs)),
                    h=int(max(ys) - min(ys)),
                )
            )
        return lines
