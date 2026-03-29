#!/usr/bin/env python3
"""
Gemini 이미지 워터마크 제거.

알고리즘:
1. Reverse alpha blending으로 워터마크 제거
2. Alpha map의 Sobel gradient → 경계 마스크 생성
3. 경계에서 bilateral filter로 아티팩트 스무딩
4. 균일 배경은 flat fill로 처리
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


def _build_gradient_mask(alpha_2d: np.ndarray, strength: float = 1.2) -> np.ndarray:
    """Alpha map의 Sobel gradient → soft edge mask."""
    alpha_u8 = (alpha_2d * 255).astype(np.uint8)
    gx = cv2.Sobel(alpha_u8, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(alpha_u8, cv2.CV_64F, 0, 1, ksize=3)
    grad = np.sqrt(gx**2 + gy**2)
    if grad.max() > 0:
        grad = grad / grad.max()
    grad = np.sqrt(grad) * strength
    grad = np.clip(grad, 0, 1)
    grad_u8 = (grad * 255).astype(np.uint8)
    kernel = np.ones((3, 3), np.uint8)
    grad_u8 = cv2.dilate(grad_u8, kernel, iterations=1)
    grad_smooth = cv2.GaussianBlur(grad_u8, (5, 5), 1.5).astype(np.float32) / 255.0
    return grad_smooth


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
    valid_3d = valid[:, :, np.newaxis]

    # --- 배경 균일성 판별 ---
    bg_samples = []
    if y > 0:
        bg_samples.append(img_array[y-1, x:x+ls, :])
    if y+ls < height:
        bg_samples.append(img_array[y+ls, x:x+ls, :])
    if x > 0:
        bg_samples.append(img_array[y:y+ls, x-1, :])
    if x+ls < width:
        bg_samples.append(img_array[y:y+ls, x+ls, :])
    bg_all = np.concatenate(bg_samples, axis=0) if bg_samples else roi.reshape(-1, 3)
    bg_std = np.std(bg_all, axis=0).mean()

    if bg_std < 5.0:
        # 균일 배경: 외곽 평균색으로 flat fill
        flat_bg = np.mean(bg_all, axis=0).reshape(1, 1, 3)
        img_array[y:y+ls, x:x+ls, :] = np.where(
            valid_3d,
            np.broadcast_to(flat_bg, (ls, ls, 3)).astype(np.float32),
            roi
        )
    else:
        # --- Step 1: Reverse Alpha Blending ---
        alpha_clamped = np.clip(alpha_3d, 0, 0.99)
        raw_restored = (roi - alpha_clamped * LOGO_VALUE) / (1.0 - alpha_clamped)
        restored = np.clip(raw_restored, 0, 255)
        ra_result = np.where(valid_3d, restored, roi)

        # --- Step 2: Clipped 픽셀 inpainting ---
        needs_fix = ((raw_restored < -5).any(axis=2) | (raw_restored > 260).any(axis=2)) & valid
        n_fix = int(np.sum(needs_fix))

        if n_fix > 0:
            fix_mask = needs_fix.astype(np.uint8) * 255
            kernel = np.ones((3, 3), np.uint8)
            fix_mask = cv2.dilate(fix_mask, kernel, iterations=1)

            # 확장 영역에서 inpainting
            pad = 6
            ey1, ey2 = max(0, y-pad), min(height, y+ls+pad)
            ex1, ex2 = max(0, x-pad), min(width, x+ls+pad)
            oy, ox = y - ey1, x - ex1

            # ra_result를 먼저 적용한 상태에서 inpainting
            temp = img_array.copy()
            temp[y:y+ls, x:x+ls, :] = ra_result
            exp_bgr = cv2.cvtColor(temp[ey1:ey2, ex1:ex2, :].astype(np.uint8), cv2.COLOR_RGB2BGR)
            exp_mask = np.zeros(exp_bgr.shape[:2], dtype=np.uint8)
            exp_mask[oy:oy+ls, ox:ox+ls] = fix_mask

            inpainted = cv2.cvtColor(
                cv2.inpaint(exp_bgr, exp_mask, 3, cv2.INPAINT_TELEA),
                cv2.COLOR_BGR2RGB
            ).astype(np.float32)

            # inpainted 결과를 ra_result에 병합
            inpainted_roi = inpainted[oy:oy+ls, ox:ox+ls, :]
            fix_3d = fix_mask[:, :, np.newaxis] > 0
            ra_result = np.where(fix_3d, inpainted_roi, ra_result)

        # --- Step 3: Gradient mask + bilateral filter ---
        grad_mask = _build_gradient_mask(alpha_2d, strength=1.2)
        grad_mask_3d = grad_mask[:, :, np.newaxis]

        # Bilateral filter: edge-preserving blur
        ra_u8 = np.clip(ra_result, 0, 255).astype(np.uint8)
        smoothed = cv2.bilateralFilter(ra_u8, 9, 30, 30).astype(np.float32)

        # Gradient-masked soft blending
        # 내부(mask≈0): reverse alpha 그대로
        # 경계(mask≈1): bilateral smoothed
        blended = ra_result * (1.0 - grad_mask_3d) + smoothed * grad_mask_3d

        img_array[y:y+ls, x:x+ls, :] = blended

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
            if "_clean" in img.stem or "_exp" in img.stem: continue
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
