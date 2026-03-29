#!/usr/bin/env python3
"""우리 dewatermark vs GeminiWatermarkTool 비교 스크립트."""

import shutil, subprocess, sys
import numpy as np
from PIL import Image, ImageDraw
from pathlib import Path
from dewatermark import remove_watermark

BATCH = Path("research/batch")
GWT = Path("tools/GeminiWatermarkTool")
ICLOUD = Path("/Users/sjlee/Library/Mobile Documents/com~apple~CloudDocs/Downloads")


def run_comparison(save_grid=True):
    crops_before = []
    crops_ours = []
    crops_gwt = []

    for i in range(1, 11):
        src = BATCH / f"test_{i:02d}.png"
        if not src.exists():
            continue

        # --- 우리 알고리즘 ---
        out_ours = BATCH / f"test_{i:02d}_ours.png"
        shutil.copy2(src, out_ours)
        r = remove_watermark(str(out_ours))

        # --- GWT ---
        out_gwt = BATCH / f"test_{i:02d}_gwt.png"
        # 우리 이미지는 canvas screenshot이라 auto-detect 안 됨 → force + explicit region
        subprocess.run(
            [str(GWT), "-i", str(src), "-o", str(out_gwt),
             "--force", "--region", "br:21,21,40,40", "--denoise", "soft"],
            capture_output=True, text=True
        )

        # 크롭 수집
        orig = np.array(Image.open(src).convert("RGB"))
        h, w = orig.shape[:2]
        px, py = r["position"]
        ls = r["logo_size"]
        pad = 8
        cx, cy = max(0, px - pad), max(0, py - pad)
        cs = ls + pad * 2

        crops_before.append(orig[cy:cy+cs, cx:cx+cs])

        ours_arr = np.array(Image.open(out_ours).convert("RGB"))
        crops_ours.append(ours_arr[cy:cy+cs, cx:cx+cs])

        if out_gwt.exists():
            gwt_arr = np.array(Image.open(out_gwt).convert("RGB"))
            # GWT가 이미지 크기를 바꿀 수 있으므로 체크
            if gwt_arr.shape[0] >= cy+cs and gwt_arr.shape[1] >= cx+cs:
                crops_gwt.append(gwt_arr[cy:cy+cs, cx:cx+cs])
            else:
                crops_gwt.append(orig[cy:cy+cs, cx:cx+cs])  # fallback
        else:
            crops_gwt.append(orig[cy:cy+cs, cx:cx+cs])

        print(f"test_{i:02d}: ours={r['elapsed_ms']:.0f}ms, gwt={'OK' if out_gwt.exists() else 'FAIL'}")

    if save_grid and crops_before:
        _save_comparison_grid(crops_before, crops_ours, crops_gwt)


def _save_comparison_grid(before, ours, gwt, scale=5):
    cell = before[0].shape[0] * scale
    gap = 4
    ml, hdr = 40, 25
    rows = len(before)
    cw = ml + 3 * cell + 2 * gap
    ch = hdr + rows * (cell + gap)
    canvas = Image.new("RGB", (cw, ch), (240, 240, 240))
    draw = ImageDraw.Draw(canvas)

    # 헤더
    col_x = [ml, ml + cell + gap, ml + 2 * (cell + gap)]
    labels = [("Before", (120, 120, 120)), ("Ours", (0, 100, 180)), ("GWT", (180, 60, 0))]
    for (label, color), cx in zip(labels, col_x):
        draw.text((cx + cell // 2 - 20, 5), label, fill=color)

    for row, (co, cu, cg) in enumerate(zip(before, ours, gwt)):
        y = hdr + row * (cell + gap)
        draw.text((5, y + cell // 2 - 5), f"{row+1:02d}", fill=(80, 80, 80))
        canvas.paste(Image.fromarray(co).resize((cell, cell), Image.NEAREST), (col_x[0], y))
        canvas.paste(Image.fromarray(cu).resize((cell, cell), Image.NEAREST), (col_x[1], y))
        canvas.paste(Image.fromarray(cg).resize((cell, cell), Image.NEAREST), (col_x[2], y))

    out_path = "research/compare_grid.png"
    canvas.save(out_path)
    shutil.copy2(out_path, str(ICLOUD / "compare_grid.png"))
    print(f"Grid saved: {out_path} → iCloud")


if __name__ == "__main__":
    run_comparison()
