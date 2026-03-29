#!/usr/bin/env npx tsx
/**
 * Gemini Web Image Generator (nanobanana-skill v0.2.3)
 * CDP 모드 Chrome에 attach해서 gemini.google.com에서 이미지를 자동 생성·저장합니다.
 *
 * Usage:
 *   npx tsx generate.ts "a futuristic city at night" [--out ~/Desktop] [--count 1] [--port 9222]
 */
import { mkdirSync, writeFileSync, statSync, existsSync } from "fs";
import { join, resolve } from "path";
import { homedir } from "os";
import { parseArgs } from "util";
import { dewatermark } from "./dewatermark-client.js";

// playwright 동적 임포트 — 없으면 명확한 안내 후 종료
let chromium: any;
try {
  const pw = await import("playwright");
  chromium = pw.chromium;
} catch {
  console.error(`${"=".repeat(50)}`);
  console.error("ERROR: playwright (Node.js) 모듈을 찾을 수 없습니다.");
  console.error("");
  console.error("  ⚠️  이 스킬은 TypeScript/Node.js입니다. Python이 아닙니다!");
  console.error("  ⚠️  pip install playwright가 아니라 npm install이 필요합니다.");
  console.error("");
  console.error("  조치: 스킬 디렉토리에서 npm 의존성을 설치하세요:");
  console.error("    cd ~/.claude/skills/nanobanana-skill && npm install");
  console.error(`${"=".repeat(50)}`);
  process.exit(1);
}

// ─── 상수 ──────────────────────────────────────────────────────────────────
const GEMINI_URL = "https://gemini.google.com/app";
let DEFAULT_TIMEOUT = 180_000;
const IMAGE_WAIT_POLL_FAST = 500;
const IMAGE_WAIT_POLL_SLOW = 1500;
const MAX_IMAGES = 8;

const INPUT_SELECTORS = [
  "rich-textarea p",
  "[contenteditable='true']",
  "textarea[placeholder]",
  "div[role='textbox']",
];

let _t0 = Date.now();

// ─── 로깅 ──────────────────────────────────────────────────────────────────
function elapsed(): string {
  return `${((Date.now() - _t0) / 1000).toFixed(1)}s`;
}

function log(icon: string, msg: string) {
  console.log(`[${elapsed().padStart(6)}] ${icon} ${msg}`);
}

function logErr(msg: string) {
  console.error(`[${elapsed().padStart(6)}] ✗ ${msg}`);
}

function logDetail(msg: string) {
  console.error(`[${elapsed().padStart(6)}]   ${msg}`);
}

// ─── 유틸 ──────────────────────────────────────────────────────────────────
function expandPath(p: string): string {
  if (p.startsWith("~")) return join(homedir(), p.slice(1));
  return resolve(p);
}

function makeOutputPath(outDir: string, index: number, ext = "png"): string {
  const dir = expandPath(outDir);
  mkdirSync(dir, { recursive: true });
  const ts = Date.now();
  return join(dir, `gemini_${ts}_${String(index).padStart(2, "0")}.${ext}`);
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

// ─── 이미지 폴링 JS ───────────────────────────────────────────────────────
const POLL_JS = `(() => {
    // 1단계: 명시적 셀렉터로 이미지 탐색
    const selectors = [
        "img[alt*='AI generated']",
        "img[alt*='AI로 생성']",
        "img[alt*='Generated image']",
        "img.image.loaded",
        "img.image",
        "model-response img",
        ".response-container img",
        "message-content img",
        ".image-button img",
        "[class*='generated'] img",
    ];
    const seen = new Set();
    const images = [];
    for (const sel of selectors) {
        try {
            for (const img of document.querySelectorAll(sel)) {
                const src = img.src || '';
                if (src && !seen.has(src) && img.width > 50) {
                    seen.add(src);
                    images.push(src);
                }
            }
        } catch {}
    }

    // 2단계: 셀렉터 매치 0이면 model-response 내 큰 이미지 전부 수집 (UI 변경 대비)
    if (images.length === 0) {
        const resp = document.querySelector('model-response:last-of-type') || document.querySelector('.response-container:last-of-type');
        if (resp) {
            for (const img of resp.querySelectorAll('img')) {
                const src = img.src || '';
                if (src && !seen.has(src) && img.width > 100 && img.height > 100) {
                    seen.add(src);
                    images.push(src);
                }
            }
        }
    }

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

    const loading = document.querySelectorAll(
        '.loading-indicator, [class*=spinner], mat-progress-bar, .thinking-indicator'
    ).length > 0;

    return { images, error, loading };
})()`;

// ─── 입력창 찾기 ──────────────────────────────────────────────────────────
async function findInput(page: any) {
  for (const sel of INPUT_SELECTORS) {
    try {
      const loc = page.locator(sel).first();
      if (await loc.isVisible({ timeout: 500 })) return loc;
    } catch {}
  }
  for (const sel of INPUT_SELECTORS) {
    try {
      const loc = page.locator(sel).first();
      if (await loc.isVisible({ timeout: 3000 })) return loc;
    } catch {}
  }
  return null;
}

// ─── 다운로드 버튼 저장 ──────────────────────────────────────────────────
async function saveViaDownloadButton(
  page: any,
  path: string,
  index = 0
): Promise<boolean> {
  const t = Date.now();
  try {
    let imgEl = null;
    for (const sel of [
      "img[alt*='AI generated']",
      "img[alt*='Generated image']",
      "img.image.loaded",
      "img.image",
      ".image-button img",
      "model-response img",
    ]) {
      try {
        const els = await page.locator(sel).all();
        // 큰 이미지만 필터 (아이콘/아바타 제외)
        const bigEls = [];
        for (const el of els) {
          try {
            const box = await el.boundingBox();
            if (box && box.width > 80 && box.height > 80) bigEls.push(el);
          } catch { bigEls.push(el); }
        }
        if (index < bigEls.length) {
          imgEl = bigEls[index];
          break;
        }
      } catch {
        continue;
      }
    }
    if (!imgEl) return false;

    try {
      await imgEl.click({ timeout: 3000 });
    } catch {
      logDetail("이미지 클릭 실패 — 다운로드 버튼 방식 스킵");
      return false;
    }
    await sleep(1500);

    // 다운로드 버튼을 여러 셀렉터로 탐색
    const DL_SELECTORS = [
      "button[aria-label='Download full-sized image']",
      "button[aria-label*='Download']",
      "button[aria-label*='download']",
      "button[aria-label*='다운로드']",
      "button[data-tooltip*='Download']",
      "button[data-tooltip*='다운로드']",
      "[role='button'][aria-label*='ownload']",
    ];

    let dlBtn = null;
    for (const sel of DL_SELECTORS) {
      try {
        const loc = page.locator(sel).first();
        if (await loc.isVisible({ timeout: 1000 })) {
          dlBtn = loc;
          break;
        }
      } catch {
        continue;
      }
    }

    if (!dlBtn) {
      logDetail("다운로드 버튼을 찾을 수 없음");
      try { await page.keyboard.press("Escape"); } catch {}
      return false;
    }

    const fullSrc: string | null = await page.evaluate(`() => {
      const overlay = document.querySelector('.cdk-overlay-container');
      if (!overlay) return null;
      const imgs = overlay.querySelectorAll('img');
      let best = null, bestArea = 0;
      for (const img of imgs) {
        const w = img.naturalWidth || img.width;
        const h = img.naturalHeight || img.height;
        if (w * h > bestArea) { bestArea = w * h; best = img.src; }
      }
      return best;
    }`);

    if (!fullSrc) {
      await page.keyboard.press("Escape");
      return false;
    }

    if (fullSrc.startsWith("http")) {
      const response = await page.context().request.get(fullSrc);
      if (response.ok()) {
        const body = await response.body();
        writeFileSync(path, body);
      } else {
        await page.keyboard.press("Escape");
        return false;
      }
    } else if (fullSrc.startsWith("blob:")) {
      const data: string | null = await page.evaluate(
        `(url) => {
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
        }`,
        fullSrc
      );
      if (data) {
        writeFileSync(path, Buffer.from(data, "base64"));
      } else {
        await page.keyboard.press("Escape");
        return false;
      }
    } else {
      await page.keyboard.press("Escape");
      return false;
    }

    try {
      await page.keyboard.press("Escape");
      await sleep(300);
    } catch {}

    const fsize = existsSync(path) ? statSync(path).size : 0;
    logDetail(
      `다운로드 버튼 저장 (${((Date.now() - t) / 1000).toFixed(1)}s, ${Math.floor(fsize / 1024)}KB)`
    );
    return existsSync(path) && fsize > 1000;
  } catch (e: any) {
    logDetail(`다운로드 버튼 실패: ${e.message || e}`);
    try {
      await page.keyboard.press("Escape");
    } catch {}
    return false;
  }
}

// ─── src 기반 이미지 저장 ─────────────────────────────────────────────────
async function saveImageFromSrc(
  page: any,
  src: string,
  path: string,
  index = 0
): Promise<string | null> {
  const t = Date.now();

  try {
    if (src.startsWith("blob:")) {
      const data: string | null = await page.evaluate(
        `(url) => {
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
        }`,
        src
      );
      if (data) {
        writeFileSync(path, Buffer.from(data, "base64"));
        logDetail(
          `canvas fallback 저장 (${((Date.now() - t) / 1000).toFixed(1)}s)`
        );
        return path;
      }

      // element screenshot fallback
      logDetail("canvas 실패 → element screenshot fallback");
      for (const sel of [
        "img[alt*='AI generated']",
        "img[alt*='Generated image']",
        "img.image.loaded",
        "img.image",
        ".image-button img",
        "model-response img",
      ]) {
        try {
          const els = await page.locator(sel).all();
          if (index < els.length) {
            const shot = await els[index].screenshot();
            if (shot) {
              writeFileSync(path, shot);
              logDetail(
                `element screenshot 저장 (${((Date.now() - t) / 1000).toFixed(1)}s)`
              );
              return path;
            }
          }
        } catch {}
      }
      logErr(`blob 저장 실패: canvas + screenshot 모두 실패`);
      return null;
    } else if (src.startsWith("data:image/")) {
      const parts = src.split(",", 2);
      if (parts.length !== 2) {
        logErr("잘못된 data URL 형식");
        return null;
      }
      const encoded = parts[1];
      const padded = encoded + "=".repeat((4 - (encoded.length % 4)) % 4);
      writeFileSync(path, Buffer.from(padded, "base64"));
      logDetail(`data URL 저장 (${((Date.now() - t) / 1000).toFixed(1)}s)`);
      return path;
    } else if (src.startsWith("http")) {
      const response = await page.context().request.get(src);
      if (response.ok()) {
        const body = await response.body();
        writeFileSync(path, body);
        logDetail(
          `HTTP 다운로드 (${((Date.now() - t) / 1000).toFixed(1)}s, ${Math.floor(body.length / 1024)}KB)`
        );
        return path;
      }
      logErr(`HTTP 다운로드 실패: status=${response.status()}`);
      return null;
    } else {
      logErr(`알 수 없는 URL scheme: ${src.slice(0, 80)}`);
      return null;
    }
  } catch (e: any) {
    logErr(`저장 중 예외: ${e.message || e}`);
    return null;
  }
}

// ─── 이미지 대기 ──────────────────────────────────────────────────────────
async function waitForImages(
  page: any,
  timeoutMs: number
): Promise<string[]> {
  const deadline = Date.now() + timeoutMs;
  let stableCount = 0;
  let lastSrcs: string[] = [];
  let pollCount = 0;

  while (Date.now() < deadline) {
    pollCount++;
    const remaining = Math.round((deadline - Date.now()) / 1000);

    let result: any;
    try {
      result = await page.evaluate(POLL_JS);
    } catch {
      logDetail("폴링 오류 (무시)");
      await sleep(500);
      continue;
    }

    if (!result) {
      logDetail("폴링 결과 없음 (무시)");
      await sleep(500);
      continue;
    }

    if (result.error) {
      logErr(`Gemini 거부: "${result.error}"`);
      logErr("  → 프롬프트를 수정하거나 덜 구체적인 표현으로 재시도하세요");
      return [];
    }

    const found: string[] = result.images || [];

    if (found.length > 0) {
      if (JSON.stringify(found) === JSON.stringify(lastSrcs)) {
        stableCount++;
      } else {
        stableCount = 0;
        lastSrcs = found;
        logDetail(
          `이미지 ${found.length}개 감지, 안정화 확인 중... (남은 시간 ${remaining}s)`
        );
      }

      if (stableCount >= 2) return lastSrcs;
      await sleep(IMAGE_WAIT_POLL_SLOW);
    } else {
      stableCount = 0;
      if (pollCount % 20 === 0) {
        const status = result.loading ? "생성 중..." : "대기 중...";
        logDetail(`${status} (남은 시간 ${remaining}s)`);
      }
      await sleep(IMAGE_WAIT_POLL_FAST);
    }
  }

  if (lastSrcs.length > 0) {
    logDetail(
      `타임아웃이지만 ${lastSrcs.length}개 이미지 발견됨 — 반환합니다`
    );
  } else {
    logErr(`이미지 감지 타임아웃 (${timeoutMs / 1000}s)`);
  }
  return lastSrcs;
}

// ─── 메인 생성 함수 ──────────────────────────────────────────────────────
async function generate(
  prompt: string,
  outDir: string,
  count: number,
  port: number
): Promise<string[]> {
  _t0 = Date.now();

  log("◆", `nanobanana-skill v0.2.3 시작 (port=${port}, count=${count})`);
  log("◆", `프롬프트: "${prompt}"`);

  const browser = await chromium
    .connectOverCDP(`http://localhost:${port}`)
    .catch((e: Error) => {
      throw new Error(
        `Chrome CDP 연결 실패 (port ${port})\n` +
          `  원인: ${e.message}\n` +
          `  조치: launch_chrome.sh를 먼저 실행하세요`
      );
    });

  log("✓", "CDP 연결 성공");

  try {
    const contexts = browser.contexts();
    if (contexts.length === 0) {
      throw new Error("Chrome에 열린 탭이 없습니다.");
    }

    const ctx = contexts[0];
    const pages = ctx.pages();
    let page = pages.find((p) => p.url().includes("gemini.google.com")) ?? null;

    if (!page) {
      log("→", "Gemini 탭 없음, 새 탭 열기...");
      page = await ctx.newPage();
    }

    if (!page.url().includes("gemini.google.com/app")) {
      log("→", "Gemini 페이지로 이동 중...");
      await page.goto(GEMINI_URL, { waitUntil: "domcontentloaded" });
      await sleep(3000);
    }

    // 로그인 리다이렉트 감지 (최대 5초 추가 대기)
    for (let i = 0; i < 5; i++) {
      const currentUrl = page.url();
      if (
        !currentUrl.includes("accounts.google.com") &&
        !currentUrl.toLowerCase().includes("signin")
      ) {
        break;
      }
      if (i === 4) {
        throw new Error(
          "Google 로그인이 필요합니다.\n" +
            "  조치: Chrome 창에서 gemini.google.com에 로그인 후 재실행"
        );
      }
      logDetail(`로그인 페이지 감지, 대기 중... (${i + 1}/5)`);
      await sleep(2000);
    }

    log("✓", "Gemini 페이지 확인");

    let inp = await findInput(page);
    if (!inp) {
      throw new Error(
        "텍스트 입력창을 찾을 수 없습니다.\n" +
          "  조치: 브라우저에서 페이지를 새로고침하고 재시도"
      );
    }

    // 새 대화 시작
    try {
      const newChatBtn = page
        .locator(
          "a[href='/app'], button[aria-label*='New chat'], button[aria-label*='새 대화']"
        )
        .first();
      if (await newChatBtn.isVisible({ timeout: 800 })) {
        await newChatBtn.click();
        await sleep(1000);
        inp = await findInput(page);
        log("✓", "새 대화 시작");
      }
    } catch {}

    // overlay 닫기
    try {
      await page.evaluate(`() => {
        document.querySelectorAll('.cdk-overlay-backdrop').forEach(el => el.click());
      }`);
      await sleep(300);
    } catch {}

    // inp null 재검증 (새 대화 시작 후 null 가능)
    if (!inp) {
      throw new Error(
        "새 대화 시작 후 입력창을 찾을 수 없습니다.\n" +
          "  조치: 브라우저에서 페이지를 새로고침하고 재시도"
      );
    }

    // 프롬프트 입력
    log("→", "프롬프트 전송...");
    await inp.click({ force: true });
    await inp.fill(prompt);
    await page.keyboard.press("Enter");

    // 이미지 생성 대기
    log("…", `이미지 생성 대기 (최대 ${DEFAULT_TIMEOUT / 1000}초)`);
    const genStart = Date.now();
    const srcs = await waitForImages(page, DEFAULT_TIMEOUT);
    const genDur = ((Date.now() - genStart) / 1000).toFixed(1);

    const savedPaths: string[] = [];

    if (srcs.length > 0) {
      log("✓", `이미지 ${srcs.length}개 발견 (${genDur}s)`);

      for (let i = 0; i < Math.min(srcs.length, count); i++) {
        let outPath = makeOutputPath(outDir, i);

        // 1순위: 다운로드 버튼
        let ok = await saveViaDownloadButton(page, outPath, i);

        // 2순위: DOM 추출
        if (!ok) {
          logDetail("다운로드 버튼 실패 → DOM 추출 fallback");
          const actualPath = await saveImageFromSrc(page, srcs[i], outPath, i);
          ok = actualPath !== null;
          if (actualPath) outPath = actualPath;
        }

        if (ok && existsSync(outPath)) {
          const fsize = statSync(outPath).size;
          log("✓", `저장: ${outPath} (${Math.floor(fsize / 1024)}KB)`);
          savedPaths.push(outPath);
        } else {
          logErr(`저장 실패 (이미지 ${i + 1}/${count})`);
        }
      }
    } else {
      logErr("이미지를 찾지 못했습니다");
      // screenshot fallback (디버그 전용, dewatermark 대상 아님)
      const fbPath = makeOutputPath(outDir, 0);
      await page.screenshot({ path: fbPath, fullPage: false });
      logErr(`스크린샷 fallback 저장 (디버그 전용): ${fbPath}`);
      // savedPaths에 추가하지 않음 — Playwright screenshot은 dewatermark 대상이 아님
    }

    const total = ((Date.now() - _t0) / 1000).toFixed(1);
    log("◆", `완료 (총 ${total}s)`);
    return savedPaths;
  } finally {
    await browser.close();
  }
}

// ─── CLI 진입점 ──────────────────────────────────────────────────────────
async function main() {
  const { values, positionals } = parseArgs({
    args: process.argv.slice(2),
    options: {
      out: { type: "string", default: "~/Desktop" },
      count: { type: "string", default: "1" },
      port: { type: "string", default: "9222" },
      timeout: { type: "string", default: "180" },
      dewatermark: { type: "boolean", default: false },
    },
    allowPositionals: true,
    strict: false,
  });

  const prompt = positionals[0];
  if (!prompt) {
    console.error(
      "Usage: npx tsx generate.ts <PROMPT> [--out DIR] [--count N] [--port N] [--dewatermark]"
    );
    process.exit(1);
  }

  const count = parseInt(values.count as string) || 1;
  if (count < 1 || count > MAX_IMAGES) {
    console.error(`ERROR: --count는 1~${MAX_IMAGES} 사이여야 합니다.`);
    process.exit(1);
  }

  DEFAULT_TIMEOUT = (parseInt(values.timeout as string) || 90) * 1000;

  try {
    const paths = await generate(
      prompt,
      values.out as string,
      count,
      parseInt(values.port as string) || 9222
    );

    if (paths.length > 0) {
      // 워터마크 제거
      if (values.dewatermark) {
        for (const p of paths) {
          try {
            const r = await dewatermark(p);
            if (r.detected) {
              console.log(
                `  워터마크 제거: ${p.split("/").pop()} (${r.elapsedMs}ms)`
              );
            } else {
              console.log(
                `  워터마크 미감지: ${p.split("/").pop()}`
              );
            }
          } catch (e: any) {
            console.error(`  워터마크 제거 실패: ${e.message}`);
          }
        }
      }

      console.log(`\n=== 생성 완료 (${paths.length}장) ===`);
      for (const p of paths) console.log(p);
      process.exit(0);
    } else {
      console.error("ERROR: 이미지를 저장하지 못했습니다.");
      process.exit(1);
    }
  } catch (e: any) {
    console.error(`\n${"=".repeat(50)}`);
    console.error(`ERROR: ${e.message}`);
    console.error(`${"=".repeat(50)}`);
    process.exit(1);
  }
}

main();
