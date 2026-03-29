#!/usr/bin/env python3
"""
Gemini 이미지 워터마크 제거 모듈.
다운로드된 PNG 파일의 오른쪽 하단 별 모양 워터마크를 OpenCV inpainting으로 제거합니다.

Usage:
  python dewatermark.py image.png [--mask watermark_mask_full.png] [--radius 5]
"""

import sys
import time
from pathlib import Path

import cv2
import numpy as np

# 워터마크 위치 상수 (1024x559 캘리브레이션 기준)
# bbox: x=965, y=500, w=36, h=36, margin_right=23, margin_bottom=23
WM_MARGIN_RIGHT_RATIO = 23 / 1024   # 오른쪽 마진 비율
WM_MARGIN_BOTTOM_RATIO = 23 / 559   # 하단 마진 비율
WM_SIZE_RATIO = 36 / 1024           # 워터마크 크기 비율 (폭 기준)
ROI_PAD = 15                         # ROI 여유 패딩 (px)


def _find_mask(mask_path: Path | str | None) -> Path:
    """마스크 파일 경로 해석. None이면 스킬 디렉토리에서 탐색."""
    if mask_path:
        p = Path(mask_path)
        if p.exists():
            return p
        raise FileNotFoundError(f"마스크 파일 없음: {p}")

    # 기본 경로 탐색
    candidates = [
        Path(__file__).parent / "research" / "watermark_mask_full.png",
        Path(__file__).parent / "watermark_mask_full.png",
        Path.home() / ".claude/skills/nanobanana-skill/watermark_mask_full.png",
    ]
    for c in candidates:
        if c.exists():
            return c

    raise FileNotFoundError(
        "워터마크 마스크 파일을 찾을 수 없습니다.\n"
        "  generate_mask.py를 먼저 실행하거나 --mask 옵션으로 경로를 지정하세요"
    )


def remove_watermark(
    image_path: str | Path,
    mask_path: str | Path | None = None,
    inpaint_radius: int = 5,
    method: str = "ns",
) -> dict:
    """이미지에서 워터마크를 제거합니다.

    Args:
        image_path: 처리할 이미지 경로
        mask_path: 워터마크 마스크 경로 (None이면 자동 탐색)
        inpaint_radius: inpainting 반경 (기본 5)
        method: "ns" (Navier-Stokes) 또는 "telea"

    Returns:
        dict: {success, elapsed_ms, changed_pixels, roi_bbox}
    """
    t0 = time.time()
    image_path = Path(image_path)

    # 이미지 로드
    img = cv2.imread(str(image_path))
    if img is None:
        raise FileNotFoundError(f"이미지를 읽을 수 없음: {image_path}")
    h, w = img.shape[:2]

    # 마스크 로드
    mask_file = _find_mask(mask_path)
    mask_full = cv2.imread(str(mask_file), cv2.IMREAD_GRAYSCALE)
    if mask_full is None:
        raise FileNotFoundError(f"마스크를 읽을 수 없음: {mask_file}")

    # 마스크 크기를 이미지에 맞게 리사이즈
    if mask_full.shape[:2] != (h, w):
        mask_full = cv2.resize(mask_full, (w, h), interpolation=cv2.INTER_NEAREST)

    # 마스크에서 실제 워터마크 영역만 추출 (threshold)
    _, mask_bin = cv2.threshold(mask_full, 127, 255, cv2.THRESH_BINARY)

    # ROI 계산 (마스크의 non-zero 영역 + 패딩)
    nz = cv2.findNonZero(mask_bin)
    if nz is None or len(nz) == 0:
        return {"success": False, "elapsed_ms": 0, "changed_pixels": 0,
                "error": "마스크에 워터마크 영역이 없습니다"}

    bx, by, bw, bh = cv2.boundingRect(nz)
    # ROI 여유 추가
    ry1 = max(0, by - ROI_PAD)
    ry2 = min(h, by + bh + ROI_PAD)
    rx1 = max(0, bx - ROI_PAD)
    rx2 = min(w, bx + bw + ROI_PAD)

    # ROI 추출
    roi_img = img[ry1:ry2, rx1:rx2].copy()
    roi_mask = mask_bin[ry1:ry2, rx1:rx2]

    # Inpainting 수행
    flag = cv2.INPAINT_NS if method == "ns" else cv2.INPAINT_TELEA
    roi_result = cv2.inpaint(roi_img, roi_mask, inpaint_radius, flag)

    # 결과를 원본에 적용
    img[ry1:ry2, rx1:rx2] = roi_result

    # 변경 픽셀 수 계산
    original_roi = cv2.imread(str(image_path))[ry1:ry2, rx1:rx2]
    diff = cv2.absdiff(original_roi, roi_result)
    changed = int(np.sum(diff.max(axis=2) > 2))

    # 저장 (원본 덮어쓰기)
    cv2.imwrite(str(image_path), img)

    elapsed = (time.time() - t0) * 1000
    return {
        "success": True,
        "elapsed_ms": round(elapsed, 1),
        "changed_pixels": changed,
        "roi_bbox": (rx1, ry1, rx2 - rx1, ry2 - ry1),
        "method": method,
        "inpaint_radius": inpaint_radius,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Gemini 이미지 워터마크 제거")
    parser.add_argument("image", help="처리할 이미지 경로")
    parser.add_argument("--mask", default=None, help="워터마크 마스크 경로")
    parser.add_argument("--radius", type=int, default=5, help="inpainting 반경 (기본: 5)")
    parser.add_argument("--method", choices=["ns", "telea"], default="ns", help="inpainting 방법")
    args = parser.parse_args()

    try:
        result = remove_watermark(args.image, args.mask, args.radius, args.method)
        if result["success"]:
            print(f"워터마크 제거 완료: {result['elapsed_ms']:.0f}ms, "
                  f"변경 {result['changed_pixels']}px, "
                  f"방법={result['method']}, 반경={result['inpaint_radius']}")
        else:
            print(f"실패: {result.get('error', 'unknown')}", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
