#!/usr/bin/env python3
"""
Gemini Web Image Generator
CDP 모드 Chrome에 attach해서 gemini.google.com에서 이미지를 자동 생성·저장합니다.

Usage:
  python generate.py "a futuristic city at night" [--out ~/Desktop] [--count 1] [--port 9222]
"""

import argparse
import asyncio
import base64
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("ERROR: playwright가 설치되지 않았습니다.", file=sys.stderr)
    print("설치: pip install playwright && python -m playwright install chromium", file=sys.stderr)
    sys.exit(1)

# ─── 상수 ──────────────────────────────────────────────────────────────────
GEMINI_URL = "https://gemini.google.com/app"
DEFAULT_TIMEOUT = 90_000   # ms
IMAGE_WAIT_POLL = 1_000     # ms
MAX_IMAGES = 8

# Gemini 이미지 응답을 식별하는 URL 패턴들
IMAGE_URL_PATTERNS = [
    r"https://.*\.googleusercontent\.com/.*",
    r"https://lh\d+\.google(usercontent)?\.com/.*",
    r"https://.*\.ggpht\.com/.*",
    r"data:image/",
]

# 텍스트 입력창 셀렉터 (우선순위 순)
INPUT_SELECTORS = [
    "rich-textarea p",
    "[contenteditable='true']",
    "textarea[placeholder]",
    "div[role='textbox']",
]

# 이미지 생성 완료를 나타내는 DOM 셀렉터 (우선순위 순)
IMAGE_DONE_SELECTORS = [
    "img[alt*='AI generated']",       # Gemini 2025+ 실제 확인된 alt 속성
    "img.image.loaded",               # class="image animate loaded"
    "img.image.animate.loaded",
    "model-response img[src^='blob']",
    "model-response img[src^='https']",
    "img[data-request-id]",
    ".image-generation-container img",
    "model-response img[src*='googleusercontent']",
    "message-content img[src*='lh']",
]

# 에러 메시지 패턴 (생성 실패 감지)
ERROR_PATTERNS = [
    "I can't help with that",
    "I'm not able to generate",
    "couldn't create",
    "content policy",
    "이미지를 생성할 수 없",
    "생성할 수 없습니다",
]


# ─── 유틸 ──────────────────────────────────────────────────────────────────

def make_output_path(out_dir: str, index: int, ext: str = "png") -> Path:
    out = Path(out_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    ts = int(time.time() * 1000)
    return out / f"gemini_{ts}_{index:02d}.{ext}"


async def blob_url_to_bytes(page, blob_url: str) -> bytes | None:
    """blob: URL을 canvas에 draw → toDataURL로 base64 추출"""
    try:
        result = await page.evaluate("""(url) => {
            return new Promise((resolve) => {
                const img = new Image();
                img.crossOrigin = 'anonymous';
                img.onload = () => {
                    try {
                        const canvas = document.createElement('canvas');
                        canvas.width = img.naturalWidth || img.width;
                        canvas.height = img.naturalHeight || img.height;
                        const ctx = canvas.getContext('2d');
                        ctx.drawImage(img, 0, 0);
                        const dataUrl = canvas.toDataURL('image/png');
                        resolve(dataUrl.split(',')[1] || null);
                    } catch(e) {
                        resolve(null);
                    }
                };
                img.onerror = () => resolve(null);
                img.src = url;
            });
        }""", blob_url)
        if result:
            return base64.b64decode(result)
    except Exception:
        pass
    return None


async def element_screenshot_to_bytes(page, selector: str) -> bytes | None:
    """이미지 엘리먼트를 Playwright element screenshot으로 캡처"""
    try:
        el = page.locator(selector).first
        return await el.screenshot()
    except Exception:
        pass
    return None


async def save_image_from_src(page, src: str, path: Path, index: int = 0) -> bool:
    """img src로부터 이미지를 저장 (blob/data/http 모두 처리)"""
    try:
        if src.startswith("blob:"):
            # 1차: canvas toDataURL
            data = await blob_url_to_bytes(page, src)
            if data:
                path.write_bytes(data)
                return True
            # 2차: element screenshot (index 기반으로 셀렉터 특정)
            print("  [save] canvas 방식 실패, element screenshot 시도...", file=sys.stderr)
            sel_list = ["img[alt*='AI generated']", "img.image.loaded", "img.image"]
            for sel in sel_list:
                try:
                    els = await page.locator(sel).all()
                    if index < len(els):
                        shot = await els[index].screenshot()
                        if shot:
                            path.write_bytes(shot)
                            return True
                except Exception:
                    continue
            return False
        elif src.startswith("data:image/"):
            # data URL: "data:image/png;base64,xxxxx"
            parts = src.split(",", 1)
            if len(parts) != 2:
                return False
            header, encoded = parts
            ext_match = re.search(r"image/(\w+)", header)
            ext = ext_match.group(1) if ext_match else "png"
            path = path.with_suffix(f".{ext}")
            path.write_bytes(base64.b64decode(encoded + "=="))  # padding 허용
            return True
        elif src.startswith("http"):
            # CDN URL: Playwright로 직접 fetch
            response = await page.context.request.get(src)
            if response.ok:
                path.write_bytes(await response.body())
                return True
    except Exception as e:
        print(f"  [save] {type(e).__name__}: {e}", file=sys.stderr)
    return False


# ─── 메인 로직 ──────────────────────────────────────────────────────────────

async def find_input(page):
    """사용 가능한 텍스트 입력창 찾기"""
    for sel in INPUT_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=2000):
                return loc
        except Exception:
            continue
    return None


async def check_error_message(page) -> str | None:
    """Gemini가 이미지 생성을 거부했는지 확인"""
    try:
        body_text = await page.evaluate("() => document.body.innerText")
        for pat in ERROR_PATTERNS:
            if pat.lower() in body_text.lower():
                return pat
    except Exception:
        pass
    return None


async def wait_for_images(page, timeout_ms: int) -> list[str]:
    """이미지 생성 완료를 기다리고 img src 목록을 반환"""
    deadline = time.time() + timeout_ms / 1000
    stable_count = 0      # 연속으로 같은 수를 관찰한 횟수
    last_srcs: list[str] = []

    while time.time() < deadline:
        # 에러 메시지 먼저 체크
        err = await check_error_message(page)
        if err:
            print(f"ERROR: Gemini가 거부했습니다: {err}", file=sys.stderr)
            return []

        # JS로 한 번에 모든 후보 img를 수집 (셀렉터 우선순위 순)
        found = await page.evaluate("""() => {
            const selectors = [
                "img[alt*='AI generated']",
                "img.image.loaded",
                "img.image",
                "model-response img",
            ];
            const seen = new Set();
            const results = [];
            for (const sel of selectors) {
                for (const img of document.querySelectorAll(sel)) {
                    const src = img.src || '';
                    if (src && !seen.has(src) && img.width > 50) {
                        seen.add(src);
                        results.push(src);
                    }
                }
            }
            return results;
        }""")

        if found:
            if found == last_srcs:
                stable_count += 1
            else:
                stable_count = 0
                last_srcs = found
                print(f"  [wait] 이미지 {len(found)}개 감지, 안정화 대기...", file=sys.stderr)

            # 2회 연속 동일한 목록이면 완료로 판단
            if stable_count >= 2:
                return last_srcs
        else:
            stable_count = 0
            # DOM 깜빡임으로 일시적으로 비어도 기존 목록 유지

        await asyncio.sleep(IMAGE_WAIT_POLL / 1000)

    # 타임아웃 전 마지막으로 발견된 것 반환
    return last_srcs


async def screenshot_fallback(page, out_dir: str, count: int) -> list[Path]:
    """이미지 src 추출 실패 시 전체 화면 스크린샷 저장 (fallback)"""
    paths = []
    for i in range(count):
        p = make_output_path(out_dir, i, "png")
        await page.screenshot(path=str(p), full_page=False)
        paths.append(p)
        print(f"[fallback] 스크린샷 저장: {p}", file=sys.stderr)
    return paths


async def generate(prompt: str, out_dir: str, count: int, port: int) -> list[str]:
    """메인 이미지 생성 함수. 저장된 파일 경로 목록을 반환.
    실패 시 RuntimeError를 raise합니다 (sys.exit 대신)."""

    async with async_playwright() as pw:
        # CDP 모드로 기존 Chrome에 attach
        try:
            browser = await pw.chromium.connect_over_cdp(f"http://localhost:{port}")
        except Exception as e:
            raise RuntimeError(
                f"Chrome CDP 연결 실패 (port {port}): {e}\n"
                "launch_chrome.sh를 먼저 실행하고 로그인을 완료하세요."
            )

        try:
            # 기존 컨텍스트/탭 사용
            contexts = browser.contexts
            if not contexts:
                raise RuntimeError("Chrome에 열린 탭이 없습니다.")

            ctx = contexts[0]
            pages = ctx.pages
            page = None

            # Gemini 탭 찾기
            for p in pages:
                if "gemini.google.com" in p.url:
                    page = p
                    break

            # Gemini 탭 없으면 새 탭
            if page is None:
                page = await ctx.new_page()

            # Gemini 앱 페이지인지 확인
            if "gemini.google.com/app" not in page.url:
                await page.goto(GEMINI_URL, wait_until="networkidle")

            # 로그인 상태 확인
            if "accounts.google.com" in page.url or "signin" in page.url.lower():
                raise RuntimeError("로그인이 필요합니다. Chrome에서 Gemini에 로그인 후 재실행하세요.")

            print(f"[✓] Gemini 페이지 확인: {page.url}")

            # 입력창 찾기
            inp = await find_input(page)
            if inp is None:
                raise RuntimeError("텍스트 입력창을 찾을 수 없습니다. Gemini 페이지가 완전히 로드됐는지 확인하세요.")

            # 이전 대화 내용으로 인한 오염 방지 — 새 대화 시작 시도
            try:
                new_chat_btn = page.locator("a[href='/app'], button[aria-label*='New chat'], button[aria-label*='새 대화']").first
                if await new_chat_btn.is_visible(timeout=2000):
                    await new_chat_btn.click()
                    await page.wait_for_load_state("networkidle", timeout=5000)
                    inp = await find_input(page)
            except Exception:
                pass

            # 프롬프트 입력 (fill로 직접 삽입 — keyboard.type 개행 주입 방지)
            print(f"[→] 프롬프트 전송: {prompt!r}")
            await inp.click()
            await inp.fill(prompt)
            await page.keyboard.press("Enter")

            # 이미지 생성 대기
            print(f"[…] 이미지 생성 대기 중 (최대 {DEFAULT_TIMEOUT//1000}초)")
            srcs = await wait_for_images(page, DEFAULT_TIMEOUT)

            saved_paths = []

            if srcs:
                print(f"[✓] 이미지 {len(srcs)}개 발견")
                for i, src in enumerate(srcs[:count]):
                    ext = "png"
                    if "jpeg" in src or "jpg" in src:
                        ext = "jpg"
                    elif "webp" in src:
                        ext = "webp"
                    out_path = make_output_path(out_dir, i, ext)
                    ok = await save_image_from_src(page, src, out_path, index=i)
                    if ok:
                        print(f"[✓] 저장 완료: {out_path}")
                        saved_paths.append(str(out_path))
                    else:
                        print(f"[!] 저장 실패 (src={src[:60]}...)", file=sys.stderr)
            else:
                print("[!] 이미지를 찾지 못했습니다. 스크린샷 fallback 실행...", file=sys.stderr)
                fb = await screenshot_fallback(page, out_dir, 1)
                saved_paths = [str(p) for p in fb]

            return saved_paths

        finally:
            # CDP attach한 브라우저는 disconnect (close하면 Chrome이 종료됨)
            await browser.disconnect()


# ─── CLI 진입점 ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Gemini 웹에서 이미지를 생성하고 저장합니다."
    )
    parser.add_argument("prompt", help="이미지 생성 프롬프트")
    parser.add_argument("--out", default="~/Desktop", help="저장 디렉토리 (기본: ~/Desktop)")
    parser.add_argument("--count", type=int, default=1, help="저장할 이미지 수 (기본: 1)")
    parser.add_argument("--port", type=int, default=9222, help="Chrome CDP 포트 (기본: 9222)")
    args = parser.parse_args()

    if args.count < 1 or args.count > MAX_IMAGES:
        print(f"ERROR: --count는 1~{MAX_IMAGES} 사이여야 합니다.", file=sys.stderr)
        sys.exit(1)

    try:
        paths = asyncio.run(generate(args.prompt, args.out, args.count, args.port))
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if paths:
        print("\n=== 생성 완료 ===")
        for p in paths:
            print(p)
        sys.exit(0)
    else:
        print("ERROR: 이미지를 저장하지 못했습니다.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
