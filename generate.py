#!/usr/bin/env python3
"""
Gemini Web Image Generator (nanobanana-skill)
CDP 모드 Chrome에 attach해서 gemini.google.com에서 이미지를 자동 생성·저장합니다.

Usage:
  python generate.py "a futuristic city at night" [--out ~/Desktop] [--count 1] [--port 9222]
"""

import argparse
import asyncio
import base64
import re
import sys
import time
from pathlib import Path

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("ERROR: playwright가 설치되지 않았습니다.", file=sys.stderr)
    print("  pip install playwright && python -m playwright install chromium", file=sys.stderr)
    sys.exit(1)

# ─── 상수 ──────────────────────────────────────────────────────────────────
GEMINI_URL = "https://gemini.google.com/app"
DEFAULT_TIMEOUT = 90_000   # ms
IMAGE_WAIT_POLL_FAST = 500  # ms — 이미지 감지 전 빠른 폴링
IMAGE_WAIT_POLL_SLOW = 1500 # ms — 이미지 감지 후 안정화 대기
MAX_IMAGES = 8

# 텍스트 입력창 셀렉터 (우선순위 순)
INPUT_SELECTORS = [
    "rich-textarea p",
    "[contenteditable='true']",
    "textarea[placeholder]",
    "div[role='textbox']",
]

_t0 = time.time()


# ─── 로깅 ──────────────────────────────────────────────────────────────────

def _elapsed() -> str:
    return f"{time.time() - _t0:.1f}s"

def log(icon: str, msg: str):
    print(f"[{_elapsed():>6}] {icon} {msg}")

def log_err(msg: str):
    print(f"[{_elapsed():>6}] ✗ {msg}", file=sys.stderr)

def log_detail(msg: str):
    print(f"[{_elapsed():>6}]   {msg}", file=sys.stderr)


# ─── 유틸 ──────────────────────────────────────────────────────────────────

def make_output_path(out_dir: str, index: int, ext: str = "png") -> Path:
    out = Path(out_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    ts = int(time.time() * 1000)
    return out / f"gemini_{ts}_{index:02d}.{ext}"


async def save_via_download_button(page, path: Path, index: int = 0) -> bool:
    """Gemini UI의 다운로드 버튼을 클릭하여 표준 이미지 저장.
    이미지 클릭 → 모달 → 'Download full-sized image' 버튼 클릭."""
    t = time.time()
    try:
        # 이미지 요소 찾기
        img_el = None
        for sel in ("img[alt*='AI generated']", "img.image.loaded", "img.image"):
            els = await page.locator(sel).all()
            if index < len(els):
                img_el = els[index]
                break
        if not img_el:
            return False

        # 이미지 클릭 → 모달 열기
        await img_el.click()
        await asyncio.sleep(1.0)

        # 다운로드 버튼 찾기
        dl_btn = page.locator("button[aria-label='Download full-sized image']").first
        if not await dl_btn.is_visible(timeout=3000):
            # 모달 닫기
            await page.keyboard.press("Escape")
            return False

        # 모달 내 full-size 이미지 src 가져오기
        full_src = await page.evaluate("""() => {
            // 모달/오버레이 내의 큰 이미지 찾기
            const overlay = document.querySelector('.cdk-overlay-container');
            if (!overlay) return null;
            const imgs = overlay.querySelectorAll('img');
            let best = null, bestArea = 0;
            for (const img of imgs) {
                const w = img.naturalWidth || img.width;
                const h = img.naturalHeight || img.height;
                if (w * h > bestArea) {
                    bestArea = w * h;
                    best = img.src;
                }
            }
            return best;
        }""")

        if not full_src:
            await page.keyboard.press("Escape")
            return False

        # HTTP로 이미지 다운로드
        if full_src.startswith("http"):
            response = await page.context.request.get(full_src)
            if response.ok:
                body = await response.body()
                path.write_bytes(body)
            else:
                await page.keyboard.press("Escape")
                return False
        elif full_src.startswith("blob:"):
            # blob URL → canvas toDataURL
            data = await page.evaluate("""(url) => {
                return new Promise((resolve) => {
                    const img = new Image();
                    img.crossOrigin = 'anonymous';
                    img.onload = () => {
                        try {
                            const c = document.createElement('canvas');
                            c.width = img.naturalWidth || img.width;
                            c.height = img.naturalHeight || img.height;
                            c.getContext('2d').drawImage(img, 0, 0);
                            resolve(c.toDataURL('image/png').split(',')[1] || null);
                        } catch(e) { resolve(null); }
                    };
                    img.onerror = () => resolve(null);
                    img.src = url;
                });
            }""", full_src)
            if data:
                path.write_bytes(base64.b64decode(data))
            else:
                await page.keyboard.press("Escape")
                return False
        else:
            await page.keyboard.press("Escape")
            return False

        # 모달 닫기
        try:
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.3)
        except Exception:
            pass

        fsize = path.stat().st_size if path.exists() else 0
        log_detail(f"다운로드 버튼 저장 ({time.time()-t:.1f}s, {fsize//1024}KB)")
        return path.exists() and fsize > 1000

    except Exception as e:
        log_detail(f"다운로드 버튼 실패: {type(e).__name__}: {e}")
        # 모달이 열려있으면 닫기
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        return False


async def save_image_from_src(page, src: str, path: Path, index: int = 0) -> bool:
    """img src로부터 이미지를 저장. fallback: canvas → element screenshot → http fetch"""
    t = time.time()
    src_preview = src[:80] + ("..." if len(src) > 80 else "")

    try:
        if src.startswith("blob:"):
            # canvas toDataURL
            data = await page.evaluate("""(url) => {
                return new Promise((resolve) => {
                    const img = new Image();
                    img.crossOrigin = 'anonymous';
                    img.onload = () => {
                        try {
                            const c = document.createElement('canvas');
                            c.width = img.naturalWidth || img.width;
                            c.height = img.naturalHeight || img.height;
                            c.getContext('2d').drawImage(img, 0, 0);
                            resolve(c.toDataURL('image/png').split(',')[1] || null);
                        } catch(e) { resolve(null); }
                    };
                    img.onerror = () => resolve(null);
                    img.src = url;
                });
            }""", src)
            if data:
                path.write_bytes(base64.b64decode(data))
                log_detail(f"canvas fallback 저장 ({time.time()-t:.1f}s, {len(data)//1024}KB b64)")
                return True

            # element screenshot
            log_detail("canvas 실패 → element screenshot fallback")
            for sel in ("img[alt*='AI generated']", "img.image.loaded", "img.image"):
                try:
                    els = await page.locator(sel).all()
                    if index < len(els):
                        shot = await els[index].screenshot()
                        if shot:
                            path.write_bytes(shot)
                            log_detail(f"element screenshot 저장 ({time.time()-t:.1f}s, {len(shot)//1024}KB)")
                            return True
                except Exception:
                    continue
            log_err(f"blob 저장 실패: canvas + screenshot 모두 실패 ({src_preview})")
            return False

        elif src.startswith("data:image/"):
            parts = src.split(",", 1)
            if len(parts) != 2:
                log_err(f"잘못된 data URL 형식 (쉼표 없음)")
                return False
            header, encoded = parts
            ext_match = re.search(r"image/(\w+)", header)
            ext = ext_match.group(1) if ext_match else "png"
            path = path.with_suffix(f".{ext}")
            path.write_bytes(base64.b64decode(encoded + "=="))
            log_detail(f"data URL 저장 ({time.time()-t:.1f}s)")
            return True

        elif src.startswith("http"):
            response = await page.context.request.get(src)
            if response.ok:
                body = await response.body()
                path.write_bytes(body)
                log_detail(f"HTTP 다운로드 ({time.time()-t:.1f}s, {len(body)//1024}KB)")
                return True
            log_err(f"HTTP 다운로드 실패: status={response.status} ({src_preview})")
            return False

        else:
            log_err(f"알 수 없는 URL scheme: {src_preview}")
            return False

    except Exception as e:
        log_err(f"저장 중 예외: {type(e).__name__}: {e}")
    return False


# ─── 메인 로직 ──────────────────────────────────────────────────────────────

async def find_input(page):
    """사용 가능한 텍스트 입력창 찾기 (500ms 타임아웃으로 빠르게 탐색)"""
    for sel in INPUT_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=500):
                return loc
        except Exception:
            continue
    # 빠른 탐색 실패 시 한 번 더 여유 있게
    for sel in INPUT_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=3000):
                return loc
        except Exception:
            continue
    return None


# 이미지 탐색 + 에러 감지를 한 번의 JS evaluate로 수행 (네트워크 왕복 1회)
_POLL_JS = """() => {
    // 이미지 수집
    const selectors = [
        "img[alt*='AI generated']",
        "img.image.loaded",
        "img.image",
        "model-response img",
    ];
    const seen = new Set();
    const images = [];
    for (const sel of selectors) {
        for (const img of document.querySelectorAll(sel)) {
            const src = img.src || '';
            if (src && !seen.has(src) && img.width > 50) {
                seen.add(src);
                images.push(src);
            }
        }
    }

    // 에러 감지 (최근 응답 영역만 검사 — body 전체보다 빠름)
    let error = null;
    const lastResp = document.querySelector('model-response:last-of-type');
    const text = (lastResp || document.body).innerText || '';
    const patterns = [
        "I can't help with that", "I'm not able to generate",
        "couldn't create", "content policy",
        "이미지를 생성할 수 없", "생성할 수 없습니다",
        "safety", "unable to fulfill"
    ];
    const lower = text.toLowerCase();
    for (const p of patterns) {
        if (lower.includes(p.toLowerCase())) { error = p; break; }
    }

    // 로딩 상태 감지
    const loading = document.querySelectorAll(
        '.loading-indicator, [class*=spinner], mat-progress-bar, .thinking-indicator'
    ).length > 0;

    return { images, error, loading };
}"""


async def wait_for_images(page, timeout_ms: int) -> list[str]:
    """이미지 생성 완료를 기다리고 img src 목록을 반환"""
    deadline = time.time() + timeout_ms / 1000
    stable_count = 0
    last_srcs: list[str] = []
    poll_count = 0

    while time.time() < deadline:
        poll_count += 1
        remaining = int(deadline - time.time())

        try:
            result = await page.evaluate(_POLL_JS)
        except Exception as e:
            log_detail(f"폴링 오류 (무시): {type(e).__name__}")
            await asyncio.sleep(0.5)
            continue

        # 에러 감지
        if result.get("error"):
            err = result["error"]
            log_err(f"Gemini 거부: \"{err}\"")
            log_err("  → 프롬프트를 수정하거나 덜 구체적인 표현으로 재시도하세요")
            return []

        found = result.get("images", [])
        loading = result.get("loading", False)

        if found:
            if found == last_srcs:
                stable_count += 1
            else:
                stable_count = 0
                last_srcs = found
                log_detail(f"이미지 {len(found)}개 감지, 안정화 확인 중... (남은 시간 {remaining}s)")

            if stable_count >= 2:
                return last_srcs
            # 이미지 발견 후에는 느린 폴링
            await asyncio.sleep(IMAGE_WAIT_POLL_SLOW / 1000)
        else:
            stable_count = 0
            # 진행 상황 표시 (10초마다)
            if poll_count % 20 == 0:
                status = "생성 중..." if loading else "대기 중..."
                log_detail(f"{status} (남은 시간 {remaining}s)")
            # 이미지 미발견 시 빠른 폴링
            await asyncio.sleep(IMAGE_WAIT_POLL_FAST / 1000)

    if last_srcs:
        log_detail(f"타임아웃이지만 {len(last_srcs)}개 이미지 발견됨 — 반환합니다")
    else:
        log_err(f"이미지 감지 타임아웃 ({timeout_ms//1000}s)")
        log_err("  → 네트워크가 느리면 다시 시도하세요")
        log_err("  → Gemini가 텍스트만 응답했을 수 있습니다 (이미지 프롬프트인지 확인)")
    return last_srcs


async def screenshot_fallback(page, out_dir: str, count: int) -> list[Path]:
    """이미지 src 추출 실패 시 전체 화면 스크린샷 저장 (fallback)"""
    paths = []
    for i in range(count):
        p = make_output_path(out_dir, i, "png")
        await page.screenshot(path=str(p), full_page=False)
        paths.append(p)
        log_err(f"스크린샷 fallback 저장: {p}")
        log_err("  → 이미지 DOM 셀렉터가 변경됐을 수 있습니다. 브라우저에서 직접 확인하세요.")
    return paths


async def generate(prompt: str, out_dir: str, count: int, port: int) -> list[str]:
    """메인 이미지 생성 함수. 저장된 파일 경로 목록을 반환.
    실패 시 RuntimeError를 raise합니다."""

    global _t0
    _t0 = time.time()

    log("◆", f"nanobanana-skill 시작 (port={port}, count={count})")
    log("◆", f"프롬프트: {prompt!r}")

    async with async_playwright() as pw:
        # CDP 모드로 기존 Chrome에 attach
        try:
            browser = await pw.chromium.connect_over_cdp(f"http://localhost:{port}")
        except Exception as e:
            raise RuntimeError(
                f"Chrome CDP 연결 실패 (port {port})\n"
                f"  원인: {e}\n"
                f"  조치: launch_chrome.sh를 먼저 실행하세요\n"
                f"        bash ~/.claude/skills/nanobanana-skill/launch_chrome.sh"
            )

        log("✓", f"CDP 연결 성공")

        try:
            contexts = browser.contexts
            if not contexts:
                raise RuntimeError(
                    "Chrome에 열린 탭이 없습니다.\n"
                    "  조치: Chrome 창에서 아무 페이지나 열어주세요"
                )

            ctx = contexts[0]
            pages = ctx.pages
            page = None

            # Gemini 탭 찾기
            for p in pages:
                if "gemini.google.com" in p.url:
                    page = p
                    break

            if page is None:
                log("→", "Gemini 탭 없음, 새 탭 열기...")
                page = await ctx.new_page()

            # Gemini 앱 페이지인지 확인
            if "gemini.google.com/app" not in page.url:
                log("→", "Gemini 페이지로 이동 중...")
                await page.goto(GEMINI_URL, wait_until="domcontentloaded")
                # networkidle 대신 domcontentloaded + 짧은 대기 (더 빠름)
                await asyncio.sleep(1)

            # 로그인 상태 확인
            if "accounts.google.com" in page.url or "signin" in page.url.lower():
                raise RuntimeError(
                    "Google 로그인이 필요합니다.\n"
                    "  조치: Chrome 창에서 gemini.google.com에 로그인 후 재실행\n"
                    f"  현재 URL: {page.url}"
                )

            log("✓", f"Gemini 페이지 확인")

            # 입력창 찾기
            inp = await find_input(page)
            if inp is None:
                raise RuntimeError(
                    "텍스트 입력창을 찾을 수 없습니다.\n"
                    "  원인: Gemini 페이지가 아직 로딩 중이거나 UI 구조가 변경됨\n"
                    "  조치: 브라우저에서 페이지를 새로고침하고 재시도\n"
                    f"  현재 URL: {page.url}"
                )

            # 새 대화 시작 (이전 대화의 이미지와 혼동 방지)
            try:
                new_chat_btn = page.locator(
                    "a[href='/app'], button[aria-label*='New chat'], button[aria-label*='새 대화']"
                ).first
                if await new_chat_btn.is_visible(timeout=800):
                    await new_chat_btn.click()
                    await asyncio.sleep(1)  # networkidle 대신 간단 대기
                    inp = await find_input(page)
                    log("✓", "새 대화 시작")
            except Exception:
                pass

            # overlay 닫기 (Gemini가 팝업/모달을 띄울 수 있음)
            try:
                await page.evaluate("""() => {
                    document.querySelectorAll('.cdk-overlay-backdrop').forEach(el => el.click());
                }""")
                await asyncio.sleep(0.3)
            except Exception:
                pass

            # 프롬프트 입력
            log("→", "프롬프트 전송...")
            await inp.click(force=True)
            await inp.fill(prompt)
            await page.keyboard.press("Enter")

            # 이미지 생성 대기
            log("…", f"이미지 생성 대기 (최대 {DEFAULT_TIMEOUT//1000}초)")
            gen_start = time.time()
            srcs = await wait_for_images(page, DEFAULT_TIMEOUT)
            gen_dur = time.time() - gen_start

            saved_paths = []

            if srcs:
                log("✓", f"이미지 {len(srcs)}개 발견 ({gen_dur:.1f}s)")
                for i, src in enumerate(srcs[:count]):
                    out_path = make_output_path(out_dir, i, "png")

                    # 1순위: Gemini 다운로드 버튼 (표준 파일)
                    ok = await save_via_download_button(page, out_path, index=i)

                    # 2순위: DOM에서 직접 추출 (fallback)
                    if not ok:
                        log_detail("다운로드 버튼 실패 → DOM 추출 fallback")
                        ok = await save_image_from_src(page, src, out_path, index=i)

                    if ok:
                        fsize = out_path.stat().st_size
                        log("✓", f"저장: {out_path} ({fsize//1024}KB)")
                        saved_paths.append(str(out_path))
                    else:
                        log_err(f"저장 실패 (이미지 {i+1}/{count})")
            else:
                log_err("이미지를 찾지 못했습니다")
                log_err("  스크린샷 fallback으로 현재 화면을 캡처합니다")
                fb = await screenshot_fallback(page, out_dir, 1)
                saved_paths = [str(p) for p in fb]

            total = time.time() - _t0
            log("◆", f"완료 (총 {total:.1f}s)")
            return saved_paths

        finally:
            await browser.close()  # CDP attach 시 close()는 연결만 해제 (Chrome 프로세스 유지)


# ─── CLI 진입점 ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="nanobanana-skill: Gemini 웹에서 이미지를 생성하고 저장합니다."
    )
    parser.add_argument("prompt", help="이미지 생성 프롬프트")
    parser.add_argument("--out", default="~/Desktop", help="저장 디렉토리 (기본: ~/Desktop)")
    parser.add_argument("--count", type=int, default=1, help="저장할 이미지 수 (기본: 1, 최대: 8)")
    parser.add_argument("--port", type=int, default=9222, help="Chrome CDP 포트 (기본: 9222)")
    parser.add_argument("--timeout", type=int, default=90, help="이미지 대기 타임아웃 초 (기본: 90)")
    parser.add_argument("--dewatermark", action="store_true", help="저장 후 Gemini 워터마크 자동 제거")
    args = parser.parse_args()

    if args.count < 1 or args.count > MAX_IMAGES:
        print(f"ERROR: --count는 1~{MAX_IMAGES} 사이여야 합니다.", file=sys.stderr)
        sys.exit(1)

    global DEFAULT_TIMEOUT
    DEFAULT_TIMEOUT = args.timeout * 1000

    try:
        paths = asyncio.run(generate(args.prompt, args.out, args.count, args.port))
    except RuntimeError as e:
        print(f"\n{'='*50}", file=sys.stderr)
        print(f"ERROR: {e}", file=sys.stderr)
        print(f"{'='*50}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n중단됨 (Ctrl+C)", file=sys.stderr)
        sys.exit(130)

    if paths:
        # 워터마크 제거
        if args.dewatermark:
            try:
                from dewatermark import remove_watermark
                for p in paths:
                    r = remove_watermark(p)
                    if r["success"]:
                        print(f"  워터마크 제거: {Path(p).name} ({r['elapsed_ms']:.0f}ms)")
                    else:
                        print(f"  워터마크 제거 실패: {r.get('error', 'unknown')}", file=sys.stderr)
            except ImportError:
                print("WARNING: dewatermark 모듈을 찾을 수 없습니다", file=sys.stderr)

        print(f"\n=== 생성 완료 ({len(paths)}장) ===")
        for p in paths:
            print(p)
        sys.exit(0)
    else:
        print("ERROR: 이미지를 저장하지 못했습니다.", file=sys.stderr)
        print("  → 브라우저에서 Gemini 페이지를 확인하세요", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
