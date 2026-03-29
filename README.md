# nanobanana-skill

Gemini 웹 UI를 자동 조작해서 이미지를 생성하고 로컬에 저장하는 Claude Code 스킬.

## 동작 원리

```
Chrome (CDP 모드) → Playwright attach → 프롬프트 입력 → 이미지 대기 → 저장
```

사용자가 Chrome에서 Google 로그인만 하면, 이후는 완전 자동.

## 설치

```bash
# 1. 스킬 디렉토리에 복사
cp -r . ~/.claude/skills/nanobanana-skill/

# 2. Playwright 설치 (최초 1회)
pip install playwright && python3 -m playwright install chromium
```

## 사용법

### Claude Code에서 (스킬로)
```
"gemini로 이미지 생성해줘" → /nanobanana-skill 자동 트리거
```

### CLI 직접 실행
```bash
# Chrome CDP 모드 실행
bash launch_chrome.sh

# (Chrome에서 Google 로그인)

# 이미지 생성
python3 generate.py "a fox in snow, golden hour" --out ~/Desktop --count 1
```

## CLI 옵션

| 옵션 | 기본값 | 설명 |
|------|--------|------|
| `prompt` | (필수) | 이미지 생성 프롬프트 |
| `--out` | `~/Desktop` | 저장 디렉토리 |
| `--count` | `1` | 저장할 이미지 수 (최대 8) |
| `--port` | `9222` | Chrome CDP 포트 |
| `--timeout` | `90` | 이미지 대기 타임아웃 (초) |

## 파일 구조

```
nanobanana-skill/
├── SKILL.md          ← Claude Code 스킬 진입점
├── generate.py       ← Playwright CDP 자동화 메인
├── launch_chrome.sh  ← OS별 Chrome CDP 실행기
└── requirements.txt  ← playwright
```

## 이미지 저장 방식

3단계 fallback:
1. **canvas toDataURL** (가장 빠름, CORS taint 시 실패)
2. **element screenshot** (Playwright가 이미지 영역만 캡처)
3. **HTTP fetch** (CDN URL인 경우)

## 로그 출력 예시

```
[  0.0s] ◆ nanobanana-skill 시작 (port=9222, count=1)
[  0.0s] ◆ 프롬프트: 'a fox in snow'
[  0.3s] ✓ CDP 연결 성공
[  0.5s] ✓ Gemini 페이지 확인
[  1.2s] ✓ 새 대화 시작
[  1.5s] → 프롬프트 전송...
[  1.5s] … 이미지 생성 대기 (최대 90초)
[ 12.3s]   이미지 1개 감지, 안정화 확인 중... (남은 시간 78s)
[ 15.1s] ✓ 이미지 1개 발견 (13.6s)
[ 15.4s]   element screenshot 저장 (0.3s, 682KB)
[ 15.4s] ✓ 저장: ~/Desktop/gemini_1774755296000_00.png (682KB)
[ 15.5s] ◆ 완료 (총 15.5s)
```

## 지원 OS

- macOS (Google Chrome, Chromium, Chrome Canary)
- Linux (google-chrome, chromium-browser)
- Windows (WSL/MSYS2)

## 요구사항

- Python 3.10+
- Google Chrome / Chromium
- Gemini 접근 가능한 Google 계정
