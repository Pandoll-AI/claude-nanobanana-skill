# Changelog

## [0.2.1] - 2026-03-29

### Fixed
- `waitForImages`: `page.evaluate()` 결과가 `undefined`일 때 `result.error` 접근 크래시 방지 (null guard 추가)
- `saveViaDownloadButton`: 이미지 요소 탐색/클릭 시 try-catch 누락으로 인한 크래시 수정

### Improved
- 기본 타임아웃 90초 → 180초로 변경 (Gemini 이미지 생성이 느린 경우 대응)
- 로그인 리다이렉트 감지: 네비게이션 후 1초 대기 → 3초 + 최대 5회(10초) 재시도 루프로 개선
- 다운로드 버튼 셀렉터: 1개 → 7개로 확장 (영문/한글 aria-label, data-tooltip, role 포함)
- 이미지 클릭 실패 시 graceful fallback 처리

## [0.2.0] - 2026-03-29

### Added
- TypeScript 전환 (generate.ts)
- 워터마크 제거 기능 (`--dewatermark` 플래그)
- 다중 이미지 저장 (`--count` 최대 8)
- 3단계 fallback 저장: 다운로드 버튼 → canvas 추출 → element screenshot
