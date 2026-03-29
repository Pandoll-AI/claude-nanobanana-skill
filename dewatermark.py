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

    # Step 3: 주변 배경 평균 추정 (ROI 외곽 ring에서)
    pad = 5
    ey1, ey2 = max(0, y-pad), min(height, y+ls+pad)
    ex1, ex2 = max(0, x-pad), min(width, x+ls+pad)
    expanded = img_array[ey1:ey2, ex1:ex2, :].copy()
    oy, ox = y - ey1, x - ex1

    # 4변 외곽 strip에서 배경색 추정 (per-row/col 보간용)
    # 상변 배경 (y-1 row)
    bg_top = expanded[max(0,oy-1), ox:ox+ls, :] if oy > 0 else roi[0, :, :]
    # 하변 배경 (y+ls row)
    bg_bot_y = min(oy+ls, expanded.shape[0]-1)
    bg_bot = expanded[bg_bot_y, ox:ox+ls, :] if oy+ls < expanded.shape[0] else roi[-1, :, :]
    # 좌변 배경
    bg_left = expanded[oy:oy+ls, max(0,ox-1), :] if ox > 0 else roi[:, 0, :]
    # 우변 배경
    bg_right_x = min(ox+ls, expanded.shape[1]-1)
    bg_right = expanded[oy:oy+ls, bg_right_x, :] if ox+ls < expanded.shape[1] else roi[:, -1, :]

    # 각 픽셀의 "추정 배경" = 4변까지의 거리 역가중 평균
    yy, xx = np.mgrid[0:ls, 0:ls]
    d_top = (yy + 1).astype(np.float32)
    d_bot = (ls - yy).astype(np.float32)
    d_left = (xx + 1).astype(np.float32)
    d_right = (ls - xx).astype(np.float32)

    w_top = 1.0 / d_top
    w_bot = 1.0 / d_bot
    w_left = 1.0 / d_left
    w_right = 1.0 / d_right
    w_sum = w_top + w_bot + w_left + w_right

    bg_estimate = (
        w_top[:,:,np.newaxis] * bg_top[np.newaxis,:,:] +
        w_bot[:,:,np.newaxis] * bg_bot[np.newaxis,:,:] +
        w_left[:,:,np.newaxis] * bg_left[:,np.newaxis,:] +
        w_right[:,:,np.newaxis] * bg_right[:,np.newaxis,:]
    ) / w_sum[:,:,np.newaxis]

    # Step 4: 배경 균일성 판별 후 전략 선택
    alpha_2d = alpha_map[:ls, :ls]

    # 4변 배경의 표준편차 → 균일한지 판별
    bg_all = np.concatenate([
        bg_top.reshape(-1, 3), bg_bot.reshape(-1, 3),
        bg_left.reshape(-1, 3), bg_right.reshape(-1, 3)
    ], axis=0)
    bg_std = np.std(bg_all, axis=0).mean()

    if bg_std < 3.0:
        # 균일 배경: 외곽 평균색으로 flat fill (reverse alpha noise 방지)
        flat_bg = np.mean(bg_all, axis=0).reshape(1, 1, 3)
        blended = np.broadcast_to(flat_bg, (ls, ls, 3)).copy().astype(np.float32)
    else:
        # 복잡한 배경: alpha^2 비례 블렌딩
        blend_factor = (alpha_2d ** 2)[:, :, np.newaxis]
        blended = restored * (1.0 - blend_factor) + bg_estimate * blend_factor
        # needs_interp 영역은 100% 배경 추정 사용
        needs_interp_3d = needs_interp[:, :, np.newaxis]
        blended = np.where(needs_interp_3d, bg_estimate, blended)

    # valid 영역만 적용
    valid_3d = valid[:, :, np.newaxis]
    img_array[y:y+ls, x:x+ls, :] = np.where(valid_3d, blended, roi)

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
