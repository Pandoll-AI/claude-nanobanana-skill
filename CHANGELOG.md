# Changelog

## v0.1.0 (2026-03-29)

### Features
- **Gemini 웹 이미지 생성** — Chrome CDP + Playwright로 gemini.google.com 자동 조작
- **워터마크 자동 제거** — `--dewatermark` 플래그로 저장 직후 Gemini 별 모양 워터마크 제거
- **모달 이미지 추출** — Gemini UI 모달에서 full-size 이미지 추출 (canvas/screenshot fallback)

### Dewatermark Algorithm
자체 구현 reverse alpha blending + 5단계 후처리 파이프라인:

1. **NCC Snap Search** — ±2px 범위에서 워터마크 위치 자동 보정 (sub-pixel 정렬)
2. **Reverse Alpha Blending** — `original = (watermarked - α×255) / (1-α)` 수학적 복원
3. **Clipped Pixel Inpainting** — 음수/오버플로 픽셀을 OpenCV Telea로 보간
4. **Gradient Mask + Bilateral Filter** — Sobel gradient 기반 edge-preserving 스무딩
5. **2-Layer Edge Transition** — 외곽 ring 30/70, 내곽 ring 70/30 gradual 블렌딩

### Research (28 experiments)
- Baseline: OpenCV NS inpainting (metric 9.93)
- 자체 reverse alpha + alpha-proportional blending (7.98)
- 균일 배경 flat fill + 경계 스무딩 (0.05)
- GWT(GeminiWatermarkTool) 비교 평가 → 자체 구현이 10개 중 9개에서 우수
- NCC snap search 추가로 위치 드리프트 문제 해결 (test_02: -1,-1 보정)

### Performance
- 평균 처리 시간: **45.5ms** (10장 다양한 배경)
- 글자 위 워터마크: 손상 없이 깔끔하게 복원
- 워터마크 스펙: 40x40px, margin 21px (Gemini 웹 UI 기준)

### Files
```
nanobanana-skill/
├── SKILL.md          — Claude Code 스킬 진입점
├── generate.py       — Playwright CDP 자동화 + --dewatermark
├── dewatermark.py    — 워터마크 제거 (자체 구현)
├── launch_chrome.sh  — OS별 Chrome CDP 실행기
├── requirements.txt  — playwright, opencv, numpy, Pillow
└── assets/
    ├── bg_custom.png — 40x40 alpha map (canvas 렌더링용)
    ├── bg_48.png     — 48x48 alpha map (표준 ≤1024px)
    └── bg_96.png     — 96x96 alpha map (표준 >1024px)
```
