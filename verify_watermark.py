#!/usr/bin/env python3
"""
워터마크 제거 검증 스크립트.
실제 Gemini 이미지로 before/after 비교 + PSNR 측정 + diff 이미지 생성.

Usage:
  python verify_watermark.py --image research/test_bright.png [--mask research/watermark_mask_full.png]
  python verify_watermark.py --dir research/  # 디렉토리 내 모든 이미지
"""

import argparse
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np


def psnr_roi(img1, img2, roi_bbox):
    """ROI 영역의 PSNR 계산"""
    x, y, w, h = roi_bbox
    r1 = img1[y:y+h, x:x+w].astype(np.float64)
    r2 = img2[y:y+h, x:x+w].astype(np.float64)
    mse = np.mean((r1 - r2) ** 2)
    if mse == 0:
        return float("inf")
    return 10 * np.log10(255.0**2 / mse)


def verify_single(image_path: str, mask_path: str | None, out_dir: str,
                   method: str = "ns", radius: int = 5) -> dict:
    """단일 이미지 검증"""
    from dewatermark import remove_watermark

    image_path = Path(image_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    stem = image_path.stem

    # 원본 복사
    original_path = out / f"{stem}_original.png"
    processed_path = out / f"{stem}_processed.png"
    shutil.copy2(image_path, original_path)
    shutil.copy2(image_path, processed_path)

    # 워터마크 제거
    result = remove_watermark(
        str(processed_path),
        mask_path=mask_path,
        inpaint_radius=radius,
        method=method,
    )

    if not result["success"]:
        return {"pass": False, "error": result.get("error"), "image": str(image_path)}

    # 이미지 비교
    orig = cv2.imread(str(original_path))
    proc = cv2.imread(str(processed_path))
    h, w = orig.shape[:2]

    roi = result["roi_bbox"]

    # PSNR (ROI 영역)
    roi_psnr = psnr_roi(orig, proc, roi)

    # diff 이미지 (10x 증폭)
    diff = cv2.absdiff(orig, proc)
    diff_amplified = np.clip(diff.astype(np.int16) * 10, 0, 255).astype(np.uint8)
    cv2.imwrite(str(out / f"{stem}_diff.png"), diff_amplified)

    # ROI 외 영역 변경 확인
    mask_outside = np.ones((h, w), dtype=np.uint8) * 255
    rx, ry, rw, rh = roi
    mask_outside[ry:ry+rh, rx:rx+rw] = 0
    diff_gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    outside_changed = int(np.sum((diff_gray > 0) & (mask_outside > 0)))

    # 판정
    passed = (
        result["changed_pixels"] > 50 and  # 워터마크 영역에 실제 변화
        outside_changed == 0 and            # ROI 외 변경 없음
        roi_psnr < 50                       # 무한대가 아닌 실제 변화 (inf = 변화 없음)
    )

    report = {
        "pass": passed,
        "image": str(image_path),
        "method": method,
        "radius": radius,
        "roi_psnr": round(roi_psnr, 2),
        "changed_pixels": result["changed_pixels"],
        "outside_changed": outside_changed,
        "elapsed_ms": result["elapsed_ms"],
        "roi_bbox": roi,
        "files": {
            "original": str(original_path),
            "processed": str(processed_path),
            "diff": str(out / f"{stem}_diff.png"),
        },
    }

    # 리포트 저장
    report_path = out / f"{stem}_report.txt"
    with open(report_path, "w") as f:
        f.write(f"=== 워터마크 제거 검증 ===\n")
        f.write(f"이미지: {image_path}\n")
        f.write(f"판정: {'PASS' if passed else 'FAIL'}\n")
        f.write(f"방법: {method}, 반경: {radius}\n")
        f.write(f"ROI PSNR: {roi_psnr:.2f} dB\n")
        f.write(f"변경 픽셀 (ROI 내): {result['changed_pixels']}\n")
        f.write(f"변경 픽셀 (ROI 외): {outside_changed}\n")
        f.write(f"처리 시간: {result['elapsed_ms']:.0f}ms\n")
        f.write(f"ROI bbox: {roi}\n")
    report["report_file"] = str(report_path)

    return report


def main():
    parser = argparse.ArgumentParser(description="워터마크 제거 검증")
    parser.add_argument("--image", help="검증할 이미지 경로")
    parser.add_argument("--dir", help="검증할 이미지 디렉토리")
    parser.add_argument("--mask", default=None, help="워터마크 마스크 경로")
    parser.add_argument("--out", default="research/verify", help="결과 저장 디렉토리")
    parser.add_argument("--method", choices=["ns", "telea"], default="ns")
    parser.add_argument("--radius", type=int, default=5)
    args = parser.parse_args()

    images = []
    if args.image:
        images = [args.image]
    elif args.dir:
        d = Path(args.dir)
        images = [str(p) for p in d.glob("*.png")
                  if not any(x in p.stem for x in ("_original", "_processed", "_diff", "mask", "verify"))]
    else:
        print("--image 또는 --dir을 지정하세요", file=sys.stderr)
        sys.exit(1)

    results = []
    for img in images:
        print(f"\n--- 검증: {img} ---")
        r = verify_single(img, args.mask, args.out, args.method, args.radius)
        results.append(r)
        status = "PASS" if r["pass"] else "FAIL"
        print(f"  [{status}] PSNR={r.get('roi_psnr', 'N/A')}dB, "
              f"변경={r.get('changed_pixels', 0)}px, "
              f"외부변경={r.get('outside_changed', 'N/A')}px, "
              f"시간={r.get('elapsed_ms', 0):.0f}ms")

    # 요약
    passed = sum(1 for r in results if r["pass"])
    print(f"\n=== 요약: {passed}/{len(results)} PASS ===")

    # 메트릭 출력 (autoresearch용)
    if results:
        avg_psnr = np.mean([r["roi_psnr"] for r in results if r.get("roi_psnr")])
        avg_time = np.mean([r["elapsed_ms"] for r in results if r.get("elapsed_ms")])
        all_outside_zero = all(r.get("outside_changed", 1) == 0 for r in results)
        print(f"METRIC: psnr={avg_psnr:.2f} time={avg_time:.0f}ms outside_zero={all_outside_zero}")


if __name__ == "__main__":
    main()
