# Dewatermark Research Log

## 목표
Gemini 이미지의 오른쪽 하단 반투명 별 모양 워터마크를 자동 제거.

## 워터마크 스펙
- 모양: 4포인트 별 (star), alpha composited
- Gemini 웹 UI 렌더링: 40x40px, margin 21px (bottom-right)
- Gemini API 표준: 48x48 (≤1024px) / 96x96 (>1024px), margin 32/64
- Alpha range: 0 ~ 0.54 (중심부 최대)

## 실험 기록 (28회)

### Phase 1: 접근법 탐색 (exp 0-7)
| # | 방법 | Metric | 결과 |
|---|------|--------|------|
| 0 | Reverse alpha v2 (baseline) | 9.93 | 기본 reverse alpha |
| 1 | Alpha-weighted blend + edge feathering | 9.92 | 개선 없음 |
| 2 | Outlier pixel correction (diff>30) | 9.31 | **keep** |
| 3 | Interpolate clamp-neg from bg neighbors | 8.46 | **keep** |
| 4 | Gaussian bg estimate | 9.92 | 너무 blurry |
| 5 | High-alpha interp + distance weight | 8.79 | worse |
| 6 | Onion-peeling fill | 8.66 | worse |
| 7 | Boundary gaussian blend | 9.73 | 경계 뭉개짐 |

### Phase 2: Alpha-proportional blending (exp 8-15)
| # | 방법 | Metric | 결과 |
|---|------|--------|------|
| 8 | **Alpha-proportional blend RA+bg estimate** | **7.98** | **핵심 돌파구** |
| 9 | Blend factor alpha^1.5 | 7.98 | 동일 |
| 10 | Force bg for high-alpha | 7.98 | 동일 |
| 11 | Adaptive exponent (brightness) | 0.13* | 개선 없음 |
| 12 | Dark bg pure bg estimate | 0.13* | 개선 없음 |
| 13 | **Uniform bg flat fill (std<3)** | **0.05*** | **큰 개선** |
| 14 | 3px strip bg avg | 0.07* | worse |
| 15 | Boundary 1px smoothing | 0.00* | metric 완벽이지만 내부 깨짐 |

*\* exp 11부터 delta metric (cleaned - original) 사용*

### Phase 3: 하이브리드 + OpenCV (exp 16-21)
| # | 방법 | Metric | 결과 |
|---|------|--------|------|
| 16 | Hybrid RA + full inpaint blend | 0.20 | 내부 blur 심함 |
| 17 | Tighter inpaint mask | 0.20 | 여전히 blurry |
| 18 | **2-pass: RA → inpaint clipped only** | **0.17** | **keep** |
| 19 | + Uniform bg flat fill | 0.08 | **keep** |
| 20 | Feathered blend alpha/0.15 | 0.07 | 시각적 개선 없음 |
| 21 | Straight RA + inpaint clipped | 0.08 | clean |

### Phase 4: Gradient mask + edge transition (exp 22-28)
| # | 방법 | Metric | 결과 |
|---|------|--------|------|
| 22 | Gradient mask + NLM + Poisson | 0.00 | 흰색 별 아티팩트 |
| 23 | Gradient mask + NLM only | 0.64 | 너무 blurry |
| 24 | **RA + gradient bilateral + flat fill** | **0.08** | **keep** |
| 25 | Bilateral on original (wrong) | 0.05 | 워터마크 잔상 |
| 26 | Bilateral on RA + threshold 0.0005 | 0.08 | keep |
| 27 | Outer ring 50/50 single layer | 0.06 | 개선 |
| 28 | **2-layer edge transition** | **0.03** | **최종 채택** |

## 핵심 발견

### 1. Alpha map 불일치 문제
- GWT는 96x96 alpha를 40x40으로 area 보간 → 우리 bg_custom.png와 35% 픽셀 차이
- 하이브리드(GWT+우리 후처리)가 단독보다 나빴던 원인

### 2. NCC Snap Search
- test_02 (572x1024, 세로형)에서 워터마크가 (-1,-1) offset
- ±2px NCC 탐색으로 0.8987 → 0.9838로 개선, ghost star 제거

### 3. 균일 배경 flat fill
- bg_std < 5.0인 균일 배경에서는 reverse alpha보다 외곽 평균색 flat fill이 더 정확
- Alpha map 캘리브레이션 오차를 완전 회피

### 4. Gemini 웹 UI 이미지 특성
- 모달 이미지 = 다운로드 이미지 = 동일 blob (서버 별도 요청 없음)
- 워터마크 크기/위치는 브라우저 렌더링 기준 (표준과 다름)

## SOTA 비교 (GeminiWatermarkTool)
- Allen Kuo의 GeminiWatermarkTool v0.2.6 (C++ + NCNN FDnCNN)
- Vulkan 없이 `--denoise soft`만 사용 가능 (macOS)
- 10장 비교: 우리 구현(C)이 9/10에서 더 자연스러운 결과
- GWT는 alpha map 보간 불일치로 경계 아티팩트 발생

## 최종 알고리즘
```
1. NCC snap search (±2px)
2. bg_std 판별 → uniform이면 flat fill, 아니면:
   a. Reverse alpha blending
   b. Clipped pixel inpainting (Telea)
   c. Gradient mask (Sobel) + bilateral filter
   d. 2-layer edge transition (30/70, 70/30)
```
평균 처리 시간: 45.5ms (1024x559 이미지)
