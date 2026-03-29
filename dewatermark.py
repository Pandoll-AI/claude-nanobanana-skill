#!/usr/bin/env python3
"""
Gemini 이미지 워터마크 제거 — GeminiWatermarkTool wrapper.
allenk/GeminiWatermarkTool v0.2.6 바이너리를 subprocess로 호출.
"""

import subprocess, sys, time, shutil
from pathlib import Path

# GWT 바이너리 경로 (스킬 디렉토리 기준)
_GWT_PATHS = [
    Path(__file__).parent / "tools" / "GeminiWatermarkTool",
    Path.home() / ".claude" / "skills" / "nanobanana-skill" / "tools" / "GeminiWatermarkTool",
]


def _find_gwt() -> Path | None:
    for p in _GWT_PATHS:
        if p.exists() and p.is_file():
            return p
    # PATH에서 검색
    found = shutil.which("GeminiWatermarkTool")
    return Path(found) if found else None


def remove_watermark(
    image_path: str | Path,
    output_path: str | Path | None = None,
    denoise: str = "soft",
    force: bool = False,
) -> dict:
    """GWT를 사용하여 워터마크 제거.

    Args:
        image_path: 입력 이미지 경로
        output_path: 출력 경로 (None이면 in-place)
        denoise: 후처리 방식 (soft, ns, telea, off)
        force: True면 감지 스킵하고 강제 제거

    Returns:
        dict with success, elapsed_ms, message
    """
    t0 = time.time()
    image_path = Path(image_path)

    gwt = _find_gwt()
    if gwt is None:
        return {
            "success": False,
            "error": "GeminiWatermarkTool 바이너리를 찾을 수 없습니다",
            "elapsed_ms": 0,
        }

    if output_path is None:
        output_path = image_path

    output_path = Path(output_path)

    # in-place 처리: 임시 파일로 출력 후 교체
    if output_path == image_path:
        tmp_out = image_path.parent / f".{image_path.stem}_gwt_tmp{image_path.suffix}"
    else:
        tmp_out = output_path

    cmd = [
        str(gwt),
        "-i", str(image_path),
        "-o", str(tmp_out),
        "--denoise", denoise,
        "--no-banner",
        "-q",
        # Gemini 웹 UI 이미지는 40x40/margin 21 비표준 크기
        # GWT auto-detect가 안 되므로 강제 지정
        "--force",
        "--region", "br:21,21,40,40",
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "GWT 타임아웃 (30초)", "elapsed_ms": round((time.time()-t0)*1000, 1)}

    elapsed = round((time.time() - t0) * 1000, 1)

    if tmp_out.exists():
        if output_path == image_path:
            shutil.move(str(tmp_out), str(output_path))
        return {
            "success": True,
            "elapsed_ms": elapsed,
            "message": result.stdout.strip() or "OK",
        }
    else:
        # GWT가 SKIP한 경우 (워터마크 미감지)
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        msg = stderr or stdout or "워터마크 미감지 (skipped)"
        return {
            "success": False,
            "error": msg,
            "elapsed_ms": elapsed,
        }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Gemini 워터마크 제거 (GWT)")
    parser.add_argument("image", nargs="?")
    parser.add_argument("output", nargs="?", default=None)
    parser.add_argument("--dir", help="디렉토리 일괄 처리")
    parser.add_argument("--denoise", default="soft", choices=["soft", "ns", "telea", "ai", "off"])
    parser.add_argument("--force", action="store_true", help="감지 스킵, 강제 제거")
    args = parser.parse_args()

    if args.dir:
        for img in sorted(Path(args.dir).glob("*.png")):
            if any(x in img.stem for x in ("_clean", "_exp", "_gwt", "_ours")):
                continue
            out = img.parent / f"{img.stem}_clean{img.suffix}"
            r = remove_watermark(img, out, denoise=args.denoise, force=args.force)
            status = "✓" if r["success"] else "✗"
            print(f"  {status} {img.name} ({r.get('elapsed_ms',0):.0f}ms) {r.get('error','')}")
    elif args.image:
        r = remove_watermark(args.image, args.output, denoise=args.denoise, force=args.force)
        if r["success"]:
            print(f"완료: {args.output or args.image} ({r['elapsed_ms']:.0f}ms)")
        else:
            print(f"ERROR: {r['error']}", file=sys.stderr)
            sys.exit(1)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
