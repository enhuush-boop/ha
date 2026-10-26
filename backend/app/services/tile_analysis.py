"""Score an image in tiles to separate a wholly generated picture from an edited one.

A single score for a whole image cannot tell "generated from scratch" apart from
"real photo with one face swapped in": both land somewhere in the middle. Scoring
a grid of tiles separates them, because a fully synthetic image scores high
almost everywhere while an inpainted one scores high only where the paste is.

The caveat is real and belongs in the output: these classifiers were trained on
whole images, so a crop is out of distribution and its score is noisier than the
whole-image score. The *pattern* across tiles carries the signal here, not any
single tile's number.
"""

from __future__ import annotations

from typing import Any

from PIL import Image

# Below this the crop is too small for the classifier to say anything useful.
# Measured, not guessed: on five stock photographs, 170px tiles produced two
# false "AI-like" regions scoring 0.94 and 0.996, while 256px tiles over the
# same photographs peaked at 0.222. Small crops are far enough out of the
# model's training distribution that its output stops meaning anything.
MIN_TILE_PX = 256
# Each tile is a full model inference on the CPU, so the grid stays small.
MAX_GRID = 3

AI_TILE_THRESHOLD = 0.60
REAL_TILE_THRESHOLD = 0.40
# A fully generated image is uniform; this much spread means the image is not
# all one thing, which is the whole point of tiling.
LOCALIZED_SPREAD = 0.35

TILE_LIMITATIONS = [
    "Tiles are crops, and the classifiers were trained on whole images, so each tile's score is noisier than the overall score.",
    "A high-scoring region is a place to look, not a located forgery. This does not segment edits.",
    "Flat areas (sky, walls, blur) carry little texture and drift toward uncertain scores regardless of origin.",
    "Heavy recompression, as applied by every social platform, flattens the differences this relies on.",
]


def _grid_for(width: int, height: int) -> int:
    """Pick the largest grid whose tiles are still big enough to classify."""
    for grid in range(MAX_GRID, 1, -1):
        if min(width // grid, height // grid) >= MIN_TILE_PX:
            return grid
    return 0


def _pattern(scores: list[float]) -> tuple[str, str]:
    high = sum(1 for s in scores if s >= AI_TILE_THRESHOLD)
    low = sum(1 for s in scores if s <= REAL_TILE_THRESHOLD)
    spread = max(scores) - min(scores)
    total = len(scores)

    if high == total:
        return (
            "uniform_ai",
            "Every region scores AI-like. That pattern fits an image generated whole, "
            "rather than a photograph with something pasted into it.",
        )
    if low == total:
        return (
            "uniform_real",
            "Every region scores photo-like, with no area standing out as synthetic.",
        )
    if high and spread >= LOCALIZED_SPREAD:
        return (
            "localized",
            f"{high} of {total} regions score AI-like while others score photo-like "
            f"(spread {spread:.2f}). That is the pattern an edited or composited image "
            "makes: worth inspecting the flagged regions specifically.",
        )
    return (
        "inconclusive",
        "Region scores sit close together in the middle. The tiling does not separate "
        "a generated image from an edited one here.",
    )


def analyze_tiles(
    image: Image.Image,
    models: list[Any],
    whole_score: float | None = None,
) -> dict:
    """Score a grid of crops and describe the pattern they form.

    `whole_score` is the classifier's verdict on the uncropped image. Tiles do
    not decompose it — on one test image the whole scored 1.00 AI while every
    tile scored below 0.10 — so when the two disagree the result says so
    instead of letting the tile pattern quietly override the real verdict.
    """
    if not models:
        return {
            "available": False,
            "reason": "No image classifier is loaded, so tiles cannot be scored.",
        }

    width, height = image.size
    grid = _grid_for(width, height)
    if not grid:
        return {
            "available": False,
            "reason": (
                f"Image is {width}×{height}; tiles would fall below {MIN_TILE_PX}px "
                "and their scores would be meaningless."
            ),
        }

    tile_w, tile_h = width // grid, height // grid
    tiles: list[dict[str, Any]] = []
    scores: list[float] = []

    for row in range(grid):
        for col in range(grid):
            left, upper = col * tile_w, row * tile_h
            # The last row/column takes the remainder so no pixels are dropped.
            right = width if col == grid - 1 else left + tile_w
            lower = height if row == grid - 1 else upper + tile_h
            crop = image.crop((left, upper, right, lower))

            per_model: list[float] = []
            for model in models:
                try:
                    per_model.append(float(model.predict(crop)["ai_probability"]))
                except Exception:
                    continue
            if not per_model:
                continue

            score = sum(per_model) / len(per_model)
            scores.append(score)
            tiles.append(
                {
                    "row": row,
                    "col": col,
                    "box": [left, upper, right, lower],
                    "ai_probability": round(score, 6),
                    "flag": (
                        "ai-like"
                        if score >= AI_TILE_THRESHOLD
                        else "photo-like"
                        if score <= REAL_TILE_THRESHOLD
                        else "uncertain"
                    ),
                }
            )

    if not scores:
        return {
            "available": False,
            "reason": "Every tile failed to score; see model_errors on the parent result.",
        }

    pattern, explanation = _pattern(scores)
    hottest = max(tiles, key=lambda t: t["ai_probability"])
    high = sum(1 for s in scores if s >= AI_TILE_THRESHOLD)

    # Cropping can destroy whatever global cue drove the whole-image verdict,
    # so a disagreement is reported rather than resolved.
    disagreement = None
    if whole_score is not None:
        tile_mean = sum(scores) / len(scores)
        if whole_score >= AI_TILE_THRESHOLD and max(scores) < AI_TILE_THRESHOLD:
            disagreement = (
                f"The whole image scores {whole_score:.2f} AI-like but no region does. "
                "Cropping removed whatever the classifier was reacting to. Trust the "
                "whole-image score here; the tiles add nothing."
            )
        elif whole_score <= REAL_TILE_THRESHOLD and high:
            disagreement = (
                f"The whole image scores only {whole_score:.2f} AI-like, yet {high} "
                "region(s) score high. This is the case tiling exists for: a mostly "
                "real picture with something inserted."
            )
        elif abs(whole_score - tile_mean) >= 0.5:
            disagreement = (
                f"Whole-image score {whole_score:.2f} and mean region score "
                f"{tile_mean:.2f} differ sharply; treat both as uncertain."
            )

    return {
        "whole_image_score": whole_score,
        "disagreement": disagreement,
        "available": True,
        "grid": grid,
        "tile_count": len(tiles),
        "tiles": tiles,
        "mean": round(sum(scores) / len(scores), 6),
        "max": round(max(scores), 6),
        "min": round(min(scores), 6),
        "spread": round(max(scores) - min(scores), 6),
        "ai_like_tiles": high,
        "ai_like_share": round(high / len(scores), 4),
        "pattern": pattern,
        "summary": explanation,
        "hottest_region": {
            "box": hottest["box"],
            "ai_probability": hottest["ai_probability"],
        },
        "limitations": TILE_LIMITATIONS,
    }
