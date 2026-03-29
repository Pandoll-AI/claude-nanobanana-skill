# Changelog

## [0.2.4] - 2026-03-29

### Added
- **Gemini 모델 선택** (`--model fast|thinking|pro|auto`)
  - Claude가 SKILL.md 기준 테이블을 보고 직접 판단하여 지정하는 것이 기본
  - `auto`는 키워드 자동 분류 fallback (정확도 낮음)
- `classifyPrompt()`: 키워드 매칭 + 단어 수 기반 경량 분류 함수
- `selectGeminiModel()`: DOM 모드 피커 조작 (실패 시 silent skip)

### Refactored (review)
- `IMG_SELECTORS` 상수 추출 — 3곳에 중복되던 이미지 셀렉터 배열을 1곳으로 통합
- `page.evaluate` 비-IIFE 패턴 2곳 추가 수정 (fullSrc 추출, overlay dismiss)
- 다운로드 버튼 셀렉터 dead code 제거 (exact match가 contains match에 포함됨)
- usage 문자열에 `--model` 옵션 추가

## [0.2.3] - 2026-03-29

### Fixed
- SKILL.md에 "Node.js/TypeScript 프로젝트" 명시 — 다른 AI가 Python으로 실행하는 혼동 방지
- playwright 에러 메시지에 "pip이 아니라 npm" 경고 추가
- 에러 처리 테이블에 playwright 미설치 항목 추가

## [0.2.2] - 2026-03-29

### Fixed (Critical)
- **POLL_JS IIFE 버그**: `page.evaluate("() => { ... }")` → `page.evaluate("(() => { ... })()")` 변경. 문자열로 된 화살표 함수가 Playwright에서 정의만 되고 호출되지 않아 항상 `undefined` 반환 → 이미지 감지 불가의 근본 원인
- playwright 모듈 미설치 시 `Cannot find module` 크래시 대신 명확한 설치 안내 메시지 출력 후 종료

### Improved
- 한국어 Gemini UI 대응: `img[alt*='AI로 생성']` 셀렉터 추가 (기존 영어 `AI generated`만 지원)
- POLL_JS 2단계 fallback: 명시적 셀렉터 매치 실패 시 `model-response` 내 큰 이미지 자동 수집
- `saveViaDownloadButton`: 아이콘/아바타 제외를 위한 boundingBox 크기 필터 추가
- playwright를 dynamic import로 변경하여 모듈 부재 시 graceful 종료

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
