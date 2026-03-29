#!/usr/bin/env python3
"""
Gemini 이미지 워터마크 제거 — 하이브리드: Reverse Alpha + OpenCV Inpainting.
저-alpha 경계: reverse alpha blending (수학적으로 정확)
고-alpha 중심: OpenCV NS inpainting (텍스처 기반 복원)
"""

import sys, time
from pathlib import Path
import numpy as np
import cv2
from PIL import Image

ASSETS_DIR = Path(__file__).parent / "assets"
ALPHA_THRESHOLD = 0.002
INPAINT_ALPHA_THRESHOLD = 0.15  # 이 이상은 inpainting으로 처리
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

    # --- Phase 1: OpenCV Inpainting으로 고-alpha 영역 복원 ---
    # inpainting mask: alpha > threshold인 영역
    inpaint_mask = (alpha_2d > INPAINT_ALPHA_THRESHOLD).astype(np.uint8) * 255

    # 확장 영역에서 inpainting (경계 참조를 위해 padding)
    pad = 8
    ey1, ey2 = max(0, y-pad), min(height, y+ls+pad)
    ex1, ex2 = max(0, x-pad), min(width, x+ls+pad)
    oy, ox = y - ey1, x - ex1

    # 확장 영역 BGR (OpenCV 형식)
    expanded_bgr = cv2.cvtColor(
        img_array[ey1:ey2, ex1:ex2, :].astype(np.uint8),
        cv2.COLOR_RGB2BGR
    )

    # 확장 영역 크기의 마스크 (padding 영역은 0)
    expanded_mask = np.zeros(expanded_bgr.shape[:2], dtype=np.uint8)
    expanded_mask[oy:oy+ls, ox:ox+ls] = inpaint_mask

    # NS inpainting
    inpainted_bgr = cv2.inpaint(expanded_bgr, expanded_mask, 5, cv2.INPAINT_NS)
    inpainted_rgb = cv2.cvtColor(inpainted_bgr, cv2.COLOR_BGR2RGB).astype(np.float32)
    inpainted_roi = inpainted_rgb[oy:oy+ls, ox:ox+ls, :]

    # --- Phase 2: Reverse Alpha로 저-alpha 경계 복원 ---
    alpha_clamped = np.clip(alpha_3d, 0, 0.99)
    raw_restored = (roi - alpha_clamped * LOGO_VALUE) / (1.0 - alpha_clamped)
    restored = np.clip(raw_restored, 0, 255)

    # --- Phase 3: 블렌딩 ---
    # 저-alpha: reverse alpha 사용 (정확)
    # 고-alpha: inpainting 사용 (텍스처 기반)
    # 중간: 부드러운 전환
    # blend_weight: 0 = reverse alpha, 1 = inpainting
    # alpha < 0.05: 100% reverse alpha
    # alpha > 0.25: 100% inpainting
    # 사이: 선형 보간
    blend_weight = np.clip((alpha_2d - 0.05) / 0.20, 0, 1)[:, :, np.newaxis]

    blended = restored * (1.0 - blend_weight) + inpainted_roi * blend_weight

    # valid 영역만 적용
    valid_3d = valid[:, :, np.newaxis]
    img_array[y:y+ls, x:x+ls, :] = np.where(valid_3d, blended, roi)

    Image.fromarray(img_array.astype(np.uint8), "RGB").save(str(output_path))

    n_inpaint = int(np.sum(alpha_2d > INPAINT_ALPHA_THRESHOLD))
    return {
        "success": True,
        "elapsed_ms": round((time.time() - t0) * 1000, 1),
        "pixels_modified": int(np.sum(valid)),
        "pixels_inpainted": n_inpaint,
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
