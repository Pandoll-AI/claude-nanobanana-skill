#!/usr/bin/env python3
"""
Gemini 이미지 워터마크 제거 — 2-pass 하이브리드.
Pass 1: Reverse alpha blending (모든 valid 픽셀)
Pass 2: Clipping 아티팩트만 OpenCV inpainting으로 수정
"""

import sys, time
from pathlib import Path
import numpy as np
import cv2
from PIL import Image

ASSETS_DIR = Path(__file__).parent / "assets"
ALPHA_THRESHOLD = 0.002
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
    alpha_2d = alpha_map[:ls, :ls]
    alpha_3d = alpha_2d[:, :, np.newaxis]
    valid = alpha_2d > ALPHA_THRESHOLD

    # --- 배경 균일성 판별 ---
    # ROI 외곽 1px ring에서 배경 std 체크
    pad = 3
    ey1, ey2 = max(0, y-pad), min(height, y+ls+pad)
    ex1, ex2 = max(0, x-pad), min(width, x+ls+pad)
    oy, ox = y - ey1, x - ex1
    expanded = img_array[ey1:ey2, ex1:ex2, :].copy()

    bg_samples = []
    if oy > 0:
        bg_samples.append(expanded[oy-1, ox:ox+ls, :])
    if oy+ls < expanded.shape[0]:
        bg_samples.append(expanded[oy+ls, ox:ox+ls, :])
    if ox > 0:
        bg_samples.append(expanded[oy:oy+ls, ox-1, :])
    if ox+ls < expanded.shape[1]:
        bg_samples.append(expanded[oy:oy+ls, ox+ls, :])
    bg_all = np.concatenate(bg_samples, axis=0) if bg_samples else roi.reshape(-1, 3)
    bg_std = np.std(bg_all, axis=0).mean()

    # --- Pass 1: Reverse Alpha Blending ---
    alpha_clamped = np.clip(alpha_3d, 0, 0.99)
    raw_restored = (roi - alpha_clamped * LOGO_VALUE) / (1.0 - alpha_clamped)

    # Clipping이 필요한 픽셀 감지
    needs_fix = ((raw_restored < -5).any(axis=2) | (raw_restored > 260).any(axis=2)) & valid

    restored = np.clip(raw_restored, 0, 255)

    valid_3d = valid[:, :, np.newaxis]

    if bg_std < 5.0:
        # 균일 배경: 외곽 평균색으로 flat fill
        flat_bg = np.mean(bg_all, axis=0).reshape(1, 1, 3)
        img_array[y:y+ls, x:x+ls, :] = np.where(
            valid_3d,
            np.broadcast_to(flat_bg, (ls, ls, 3)).astype(np.float32),
            roi
        )
    else:
        # 복잡한 배경: reverse alpha + feathered blending
        # reverse alpha 적용 + 경계 후처리
        img_array[y:y+ls, x:x+ls, :] = np.where(valid_3d, restored, roi)

    # --- Pass 2: Clipping 아티팩트만 inpainting으로 수정 ---
    n_fix = int(np.sum(needs_fix))
    if n_fix > 0 and bg_std >= 5.0:
        # 균일 배경은 flat fill로 이미 처리됨
        fix_mask = needs_fix.astype(np.uint8) * 255
        kernel = np.ones((3, 3), np.uint8)
        fix_mask = cv2.dilate(fix_mask, kernel, iterations=1)

        expanded_bgr = cv2.cvtColor(
            img_array[ey1:ey2, ex1:ex2, :].astype(np.uint8),
            cv2.COLOR_RGB2BGR
        )

        expanded_mask = np.zeros(expanded_bgr.shape[:2], dtype=np.uint8)
        expanded_mask[oy:oy+ls, ox:ox+ls] = fix_mask

        inpainted_bgr = cv2.inpaint(expanded_bgr, expanded_mask, 3, cv2.INPAINT_TELEA)
        inpainted_rgb = cv2.cvtColor(inpainted_bgr, cv2.COLOR_BGR2RGB).astype(np.float32)

        img_array[ey1:ey2, ex1:ex2, :] = np.where(
            expanded_mask[:, :, np.newaxis] > 0,
            inpainted_rgb,
            img_array[ey1:ey2, ex1:ex2, :]
        )

    Image.fromarray(img_array.astype(np.uint8), "RGB").save(str(output_path))

    return {
        "success": True,
        "elapsed_ms": round((time.time() - t0) * 1000, 1),
        "pixels_modified": int(np.sum(valid)),
        "pixels_inpainted": n_fix,
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
            if "_clean" in img.stem or "_exp" in img.stem: continue
            out = img.parent / f"{img.stem}_clean{img.suffix}"
            r = remove_watermark(img, out)
            print(f"  {'✓' if r['success'] else '✗'} {img.name} ({r.get('elapsed_ms',0):.0f}ms, fix={r.get('pixels_inpainted',0)})")
    elif args.image:
        r = remove_watermark(args.image, args.output)
        if r["success"]:
            print(f"완료: {args.output or args.image} ({r['pixels_modified']}px, fix={r['pixels_inpainted']}px, {r['elapsed_ms']:.0f}ms)")
        else:
            print(f"ERROR: {r['error']}", file=sys.stderr); sys.exit(1)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
