/**
 * Dewatermark API thin client.
 * geminiwatermarkfree.vercel.app에 이미지를 전송하고 처리된 결과를 받아 저장한다.
 */
import { readFileSync, writeFileSync } from "fs";

const API_BASE = "https://geminiwatermarkfree.vercel.app";

export interface DewatermarkResult {
  success: boolean;
  detected: boolean;
  elapsedMs: number;
}

export async function dewatermark(imagePath: string): Promise<DewatermarkResult> {
  const imageBuffer = readFileSync(imagePath);

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 30_000);

  const response = await fetch(`${API_BASE}/api/dewatermark`, {
    method: "POST",
    headers: { "Content-Type": "image/png" },
    body: imageBuffer,
    signal: controller.signal,
  });
  clearTimeout(timeoutId);

  if (!response.ok) {
    throw new Error(`API error: ${response.status} ${response.statusText}`);
  }

  const detected = response.headers.get("X-Watermark-Detected") === "true";
  const elapsedMs = parseInt(response.headers.get("X-Processing-Time-Ms") || "0");

  if (detected) {
    const contentType = response.headers.get("Content-Type") || "";
    if (!contentType.startsWith("image/")) {
      throw new Error(`Unexpected content-type from API: ${contentType}`);
    }
    const resultBuffer = Buffer.from(await response.arrayBuffer());
    if (resultBuffer.length < 1000) {
      throw new Error(`API response too small: ${resultBuffer.length} bytes`);
    }
    writeFileSync(imagePath, resultBuffer);
  }

  return { success: true, detected, elapsedMs };
}
