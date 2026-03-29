#!/usr/bin/env python3
"""
Gemini 이미지 워터마크 제거 — Reverse Alpha Blending + 보간 보정.
"""

import sys, time
from pathlib import Path
import numpy as np
from PIL import Image, ImageFilter

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
        return {"success": False, "error": "이미지가 너무 작음", "elapsed_ms": 0}

    img_array = np.array(img, dtype=np.float32)
    roi = img_array[y:y+ls, x:x+ls, :].copy()
    alpha = alpha_map[:ls, :ls, np.newaxis]
    valid = alpha_map[:ls, :ls] > ALPHA_THRESHOLD
    alpha_clamped = np.clip(alpha, 0, MAX_ALPHA)

    # Step 1: Reverse alpha blending
    raw_restored = (roi - alpha_clamped * LOGO_VALUE) / (1.0 - alpha_clamped)

    # Step 2: 음수로 clamp된 픽셀 감지 (= 복원 불가 영역)
    # 이 픽셀들은 워터마크가 배경보다 밝아서 수학적으로 복원 불가
    needs_interp = (raw_restored < 0).any(axis=2) | (raw_restored > 255).any(axis=2)
    needs_interp = needs_interp & valid  # valid 영역에서만

    restored = np.clip(raw_restored, 0, 255)

    # Step 3: 복원 가능 영역 먼저 적용
    result = roi.copy()
    valid_3d = valid[:, :, np.newaxis]
    result = np.where(valid_3d, restored, result)

    # Step 4: 복원 불가 영역은 주변 non-watermark 픽셀로 보간
    # 확장 영역에서 주변 참조
    pad = 5
    ey1, ey2 = max(0, y-pad), min(height, y+ls+pad)
    ex1, ex2 = max(0, x-pad), min(width, x+ls+pad)
    expanded = img_array[ey1:ey2, ex1:ex2, :].copy()
    oy, ox = y - ey1, x - ex1

    # 복원된 valid 영역을 expanded에 반영
    expanded[oy:oy+ls, ox:ox+ls, :] = result

    # needs_interp 픽셀들을 주변 평균으로 교체
    interp_ys, interp_xs = np.where(needs_interp)
    for dy, dx in zip(interp_ys, interp_xs):
        ry, rx = oy + dy, ox + dx
        # 5x5 이웃 중 valid가 아닌(= 원본 배경) 픽셀의 평균
        ny1, ny2 = max(0, ry-2), min(expanded.shape[0], ry+3)
        nx1, nx2 = max(0, rx-2), min(expanded.shape[1], rx+3)
        patch = expanded[ny1:ny2, nx1:nx2, :]
        # 해당 패치에서 워터마크가 아닌 영역의 마스크
        patch_valid = np.zeros(patch.shape[:2], dtype=bool)
        for py in range(patch.shape[0]):
            for px in range(patch.shape[1]):
                # expanded 좌표를 ROI 좌표로 변환
                roi_y = (ny1 + py) - oy
                roi_x = (nx1 + px) - ox
                if 0 <= roi_y < ls and 0 <= roi_x < ls:
                    if valid[roi_y, roi_x]:
                        continue  # 워터마크 영역 → 제외
                patch_valid[py, px] = True
        if patch_valid.any():
            bg_pixels = patch[patch_valid]
            expanded[ry, rx, :] = bg_pixels.mean(axis=0)

    img_array[ey1:ey2, ex1:ex2, :] = expanded

    # Step 5: 최종 경계 스무딩 (alpha 경계에서만 1px 블러)
    final_img = Image.fromarray(img_array.astype(np.uint8), "RGB")
    final_img.save(str(output_path))

    return {
        "success": True,
        "elapsed_ms": round((time.time() - t0) * 1000, 1),
        "pixels_modified": int(np.sum(valid)),
        "pixels_interpolated": int(np.sum(needs_interp)),
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
            print(f"  {'✓' if r['success'] else '✗'} {img.name} ({r.get('elapsed_ms',0):.0f}ms, interp={r.get('pixels_interpolated',0)})")
    elif args.image:
        r = remove_watermark(args.image, args.output)
        if r["success"]:
            print(f"완료: {args.output or args.image} ({r['pixels_modified']}px, interp={r['pixels_interpolated']}px, {r['elapsed_ms']:.0f}ms)")
        else:
            print(f"ERROR: {r['error']}", file=sys.stderr); sys.exit(1)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
