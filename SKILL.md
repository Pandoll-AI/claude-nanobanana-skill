---
name: nanobanana-skill
description: |
  Gemini 웹에서 이미지를 생성하고 로컬에 저장합니다.
  "gemini로 이미지 생성", "Imagen", "gemini image gen", "이미지 만들어줘 gemini" 등의 요청 시 사용.
  Chrome CDP 세션에 attach해서 gemini.google.com을 자동 조작합니다.
allowed-tools:
  - Bash
  - AskUserQuestion
---

# Gemini Web Image Generator

## 개요

이 스킬은 사용자가 Gemini 웹 UI를 통해 이미지를 생성하도록 자동화합니다.
Chrome을 CDP 모드로 실행하고, 사용자가 직접 로그인한 후 Playwright가 attach해서
프롬프트 입력 → 이미지 생성 대기 → 파일 저장을 수행합니다.

---

## 선행 조건 확인

스킬 시작 전 반드시 확인:

```bash
# 1. Python 확인
python3 --version

# 2. playwright 설치 여부
python3 -c "import playwright; print('playwright OK')" 2>&1

# 3. CDP 포트 사용 중인지 확인
lsof -i :9222 -sTCP:LISTEN -t 2>/dev/null && echo "ALREADY_RUNNING" || echo "NOT_RUNNING"
```

playwright가 없으면:
```bash
pip install playwright && python3 -m playwright install chromium
```

---

## Step 1 — Chrome 실행

```bash
bash ~/.claude/skills/nanobanana-skill/launch_chrome.sh
```

출력에서 `READY port=9222` 또는 `ALREADY_RUNNING port=9222` 확인.
`ERROR`가 나오면 사용자에게 Chrome 설치 여부 문의.

---

## Step 2 — 사용자 로그인 대기

AskUserQuestion으로 다음 메시지를 표시하고 대기:

> Chrome 창이 열렸습니다. **gemini.google.com/app**에서 Google 계정으로 로그인을 완료한 후 "완료"를 선택하세요.
>
> (이미 로그인되어 있으면 바로 완료 선택)

옵션:
- A) 로그인 완료, 이미지 생성 시작
- B) Chrome을 못 찾겠음 / 오류 발생

B인 경우: `lsof -i :9222` 실행 후 포트 상태를 확인하고 안내.

---

## Step 3 — 이미지 생성

```bash
SKILL_DIR=~/.claude/skills/nanobanana-skill

python3 "$SKILL_DIR/generate.py" \
  "{PROMPT}" \
  --out "{OUT_DIR}" \
  --count {COUNT} \
  --port 9222 \
  --timeout 90
```

변수:
- `{PROMPT}`: 사용자가 요청한 이미지 설명 (영어로 번역하면 품질 향상)
- `{OUT_DIR}`: 저장 경로 (사용자가 지정 없으면 `~/Desktop`)
- `{COUNT}`: 저장할 이미지 수 (기본 1, 최대 8)

---

## Step 4 — 결과 전달

스크립트가 `=== 생성 완료 ===` 이후 파일 경로를 출력합니다.
사용자에게 저장된 경로를 알려주고, macOS라면:

```bash
open "{저장된_경로}"
```

---

## 에러 처리

| 증상 | 원인 | 조치 |
|------|------|------|
| `CDP 연결 실패` | Chrome이 안 열려 있음 | Step 1 재실행 |
| `로그인이 필요합니다` | 세션 만료 | Step 2 재실행 (재로그인 요청) |
| `텍스트 입력창을 찾을 수 없음` | 페이지 로딩 미완료 | 몇 초 후 `generate.py` 재실행 |
| `Gemini가 거부` | 콘텐츠 정책 필터링 | 프롬프트 수정 후 재시도 |
| `이미지를 찾지 못했음` | UI 셀렉터 변경 | fallback 스크린샷 확인, 셀렉터 업데이트 필요 |
| `timeout` | 네트워크 느림 or Gemini 부하 | 재시도 |

---

## 프롬프트 팁

- 영어 프롬프트가 품질이 높습니다. 한국어 요청이면 영어로 번역해서 전달하세요.
- 구체적일수록 좋습니다: `"a photorealistic red fox sitting in snow, golden hour lighting"`
- Gemini 이미지 생성은 **Gemini Advanced (유료)** 또는 **특정 무료 한도** 내에서 동작합니다.

---

## 선택적: 저장 디렉토리 커스텀

사용자가 저장 위치를 지정하지 않으면 `~/Desktop`을 기본으로 사용.
지정 시 `--out` 인자로 전달:

```bash
python3 generate.py "sunset over mountains" --out ~/Pictures/gemini
```
