#!/usr/bin/env python3
"""
Gemini 이미지 워터마크 제거 — Reverse Alpha Blending.
수식: original = (watermarked - α × 255) / (1 - α)
"""

import sys, time
from pathlib import Path
import numpy as np
from PIL import Image

ASSETS_DIR = Path(__file__).parent / "assets"
ALPHA_THRESHOLD = 0.002
MAX_ALPHA = 0.99
LOGO_VALUE = 255.0

_ALPHA_MAPS: dict[str, np.ndarray] = {}

def _load_alpha_map(name: str) -> np.ndarray:
    path = ASSETS_DIR / f"{name}.png"
    if not path.exists():
        raise FileNotFoundError(f"Alpha map 없음: {path}")
    bg = np.array(Image.open(path).convert("RGB"), dtype=np.float32)
    return np.max(bg, axis=2) / 255.0

def _get_alpha_map(name: str) -> np.ndarray:
    if name not in _ALPHA_MAPS:
        _ALPHA_MAPS[name] = _load_alpha_map(name)
    return _ALPHA_MAPS[name]


def remove_watermark(image_path: str | Path, output_path: str | Path | None = None) -> dict:
    t0 = time.time()
    image_path = Path(image_path)
    if output_path is None:
        output_path = image_path

    img = Image.open(image_path).convert("RGB")
    width, height = img.size

    custom = ASSETS_DIR / "bg_custom.png"
    if custom.exists():
        alpha_map = _get_alpha_map("bg_custom")
        ls = alpha_map.shape[0]
        mg = 21
        src_name = "custom"
    elif width > 1024 and height > 1024:
        alpha_map = _get_alpha_map("bg_96")
        ls, mg, src_name = 96, 64, "bg_96"
    else:
        alpha_map = _get_alpha_map("bg_48")
        ls, mg, src_name = 48, 32, "bg_48"

    x = width - mg - ls
    y = height - mg - ls
    if x < 0 or y < 0:
        return {"success": False, "error": f"이미지가 너무 작음", "elapsed_ms": 0}

    img_array = np.array(img, dtype=np.float32)

    roi = img_array[y:y+ls, x:x+ls, :]
    alpha = alpha_map[:ls, :ls, np.newaxis]
    valid = alpha_map[:ls, :ls] > ALPHA_THRESHOLD
    alpha_clamped = np.clip(alpha, 0, MAX_ALPHA)

    # Reverse alpha blending
    restored = (roi - alpha_clamped * LOGO_VALUE) / (1.0 - alpha_clamped)
    restored = np.clip(restored, 0, 255)

    img_array[y:y+ls, x:x+ls, :] = np.where(valid[:, :, np.newaxis], restored, roi)

    # Stage 2: 잔상 보정 — 복원된 영역 중 주변과 차이가 큰 픽셀을 주변 평균으로 교체
    pad = 4
    ey1, ey2 = max(0, y-pad), min(height, y+ls+pad)
    ex1, ex2 = max(0, x-pad), min(width, x+ls+pad)
    region = img_array[ey1:ey2, ex1:ex2, :].copy()
    oy, ox = y - ey1, x - ex1

    # 각 valid 픽셀에 대해 3x3 주변 평균과의 차이가 크면 주변 평균으로 블렌딩
    for dy in range(ls):
        for dx in range(ls):
            if not valid[dy, dx]:
                continue
            ry, rx = oy + dy, ox + dx
            # 3x3 이웃 평균 (자기 자신 제외)
            ny1, ny2 = max(0, ry-1), min(region.shape[0], ry+2)
            nx1, nx2 = max(0, rx-1), min(region.shape[1], rx+2)
            neighbors = region[ny1:ny2, nx1:nx2, :].reshape(-1, 3)
            me = region[ry, rx, :]
            avg = (neighbors.sum(axis=0) - me) / max(len(neighbors) - 1, 1)
            diff = np.abs(me - avg).mean()
            if diff > 30:  # 주변과 크게 다르면 보정
                blend = 0.6
                region[ry, rx, :] = me * (1-blend) + avg * blend

    img_array[ey1:ey2, ex1:ex2, :] = region

    Image.fromarray(img_array.astype(np.uint8), "RGB").save(str(output_path))

    return {
        "success": True,
        "elapsed_ms": round((time.time() - t0) * 1000, 1),
        "pixels_modified": int(np.sum(valid)),
        "logo_size": ls,
        "position": (x, y),
        "alpha_src": src_name,
    }

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Gemini 워터마크 제거")
    parser.add_argument("image", nargs="?")
    parser.add_argument("output", nargs="?", default=None)
    parser.add_argument("--dir", help="디렉토리 일괄 처리")
    args = parser.parse_args()
    if args.dir:
        for img in sorted(Path(args.dir).glob("*.png")):
            if "_clean" in img.stem: continue
            out = img.parent / f"{img.stem}_clean{img.suffix}"
            r = remove_watermark(img, out)
            print(f"  {'✓' if r['success'] else '✗'} {img.name} ({r.get('elapsed_ms',0):.0f}ms)")
    elif args.image:
        r = remove_watermark(args.image, args.output)
        if r["success"]:
            print(f"완료: {args.output or args.image} ({r['pixels_modified']}px, {r['elapsed_ms']:.0f}ms)")
        else:
            print(f"ERROR: {r['error']}", file=sys.stderr); sys.exit(1)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
