// FilterTube API — yt-dlp wrapper running on Oracle VPS
// Endpoints:
//   GET /health                  → status
//   GET /stream/:videoId         → JSON עם video/audio URLs + metadata
//   GET /download/:videoId       → קובץ mp4 (best quality)
//   GET /audio/:videoId          → קובץ mp3 (לניגון ברקע)
//   GET /proxy?url=...           → reverse proxy ל-stream URL (IP match)

import express from "express";
import { spawn } from "node:child_process";
import { createReadStream } from "node:fs";
import { unlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomUUID } from "node:crypto";

const app = express();
const PORT = process.env.PORT || 4000;

// ─── Cache (in-memory, simple TTL) ──────────────────────────────────────────
const cache = new Map();
const STREAM_CACHE_TTL = 5 * 60 * 1000; // 5 דק' — stream URLs פגים אחרי כ-6 שעות

function getCached(key) {
  const e = cache.get(key);
  if (!e) return null;
  if (Date.now() - e.at > STREAM_CACHE_TTL) { cache.delete(key); return null; }
  return e.data;
}
function setCached(key, data) { cache.set(key, { at: Date.now(), data }); }

// ─── CORS — allow our Cloudflare Worker ─────────────────────────────────────
app.use((req, res, next) => {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Range, Content-Type");
  res.setHeader("Access-Control-Expose-Headers", "Content-Length, Content-Range, Accept-Ranges");
  if (req.method === "OPTIONS") return res.status(204).end();
  next();
});

// ─── Helper: run yt-dlp with JSON output ────────────────────────────────────
function ytdlpJson(videoId, extraArgs = []) {
  return new Promise((resolve, reject) => {
    const args = [
      "--no-warnings",
      "--no-playlist",
      "--dump-single-json",
      "--no-check-certificate",
      ...extraArgs,
      `https://www.youtube.com/watch?v=${videoId}`,
    ];
    const proc = spawn("yt-dlp", args);
    let stdout = "", stderr = "";
    proc.stdout.on("data", (d) => { stdout += d; });
    proc.stderr.on("data", (d) => { stderr += d; });
    proc.on("close", (code) => {
      if (code !== 0) return reject(new Error(`yt-dlp exit ${code}: ${stderr.slice(-300)}`));
      try { resolve(JSON.parse(stdout)); } catch (e) { reject(e); }
    });
    proc.on("error", reject);
  });
}

// ─── GET /health ────────────────────────────────────────────────────────────
app.get("/health", (req, res) => {
  res.json({ ok: true, time: new Date().toISOString(), cache_size: cache.size });
});

// ─── GET /stream/:videoId ───────────────────────────────────────────────────
// מחזיר JSON עם video/audio URLs לפלייר
app.get("/stream/:videoId", async (req, res) => {
  const { videoId } = req.params;
  if (!/^[A-Za-z0-9_-]{11}$/.test(videoId)) {
    return res.status(400).json({ error: "invalid videoId" });
  }

  const cached = getCached(`stream:${videoId}`);
  if (cached) return res.json(cached);

  try {
    const info = await ytdlpJson(videoId);
    const formats = info.formats || [];

    // Muxed (video+audio in one file) — נוח לפלייר HTML5
    const muxed = formats
      .filter((f) => f.vcodec !== "none" && f.acodec !== "none" && f.url)
      .sort((a, b) => (b.height || 0) - (a.height || 0));

    // Video-only (high quality) — אם רוצים DASH
    const videoOnly = formats
      .filter((f) => f.vcodec !== "none" && f.acodec === "none" && f.url && f.ext === "mp4")
      .sort((a, b) => (b.height || 0) - (a.height || 0));

    // Audio-only — לניגון ברקע
    const audioOnly = formats
      .filter((f) => f.vcodec === "none" && f.acodec !== "none" && f.url)
      .sort((a, b) => (b.abr || 0) - (a.abr || 0));

    const result = {
      videoId,
      title: info.title,
      description: info.description,
      duration: info.duration,
      uploader: info.uploader,
      uploadDate: info.upload_date,
      viewCount: info.view_count,
      likeCount: info.like_count,
      thumbnail: info.thumbnail,
      channelId: info.channel_id,
      // streams (URLs נוצרו בעזרת ה-IP של ה-VPS שלנו)
      muxed: muxed.map((f) => ({
        url: f.url,
        quality: f.format_note || f.format_id,
        ext: f.ext,
        height: f.height,
        width: f.width,
        filesize: f.filesize || f.filesize_approx,
        mimeType: `${f.ext === "webm" ? "video/webm" : "video/mp4"}`,
      })),
      videoOnly: videoOnly.slice(0, 4).map((f) => ({
        url: f.url, quality: f.format_note, ext: f.ext,
        height: f.height, filesize: f.filesize || f.filesize_approx,
      })),
      audioOnly: audioOnly.slice(0, 3).map((f) => ({
        url: f.url, quality: f.format_note, ext: f.ext,
        abr: f.abr, filesize: f.filesize || f.filesize_approx,
      })),
    };

    setCached(`stream:${videoId}`, result);
    res.json(result);
  } catch (e) {
    console.error("[stream]", videoId, e.message);
    res.status(500).json({ error: e.message });
  }
});

// ─── GET /proxy?url=... ─────────────────────────────────────────────────────
// Reverse proxy לstream URL כדי לפתור IP lock.
// הbrowser קורא ל-VPS שלנו, ה-VPS מביא מ-googlevideo עם אותו ה-IP שיצר את ה-URL.
const ALLOWED_HOSTS = ["googlevideo.com", "youtube.com", "ytimg.com"];

app.get("/proxy", async (req, res) => {
  const { url } = req.query;
  if (!url || typeof url !== "string") return res.status(400).send("Missing url");

  let target;
  try { target = new URL(url); } catch { return res.status(400).send("Bad url"); }
  if (!ALLOWED_HOSTS.some((h) => target.hostname === h || target.hostname.endsWith("." + h))) {
    return res.status(403).send("Forbidden host");
  }

  const headers = { "User-Agent": "Mozilla/5.0 (compatible; FilterTube/1.0)" };
  if (req.headers.range) headers["range"] = req.headers.range;

  try {
    const upstream = await fetch(url, { headers, redirect: "follow" });
    res.status(upstream.status);
    // העברת headers חשובים
    for (const h of ["content-type", "content-length", "content-range", "accept-ranges", "last-modified"]) {
      const v = upstream.headers.get(h);
      if (v) res.setHeader(h, v);
    }
    res.setHeader("accept-ranges", "bytes");
    res.setHeader("cache-control", "private, no-store");

    // Stream the response
    const reader = upstream.body?.getReader();
    if (!reader) return res.end();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      res.write(value);
    }
    res.end();
  } catch (e) {
    console.error("[proxy]", e.message);
    if (!res.headersSent) res.status(502).send("Upstream error");
  }
});

// ─── GET /download/:videoId ──────────────────────────────────────────────────
// מוריד את הוידאו לקובץ זמני ומגיש להורדה
app.get("/download/:videoId", async (req, res) => {
  const { videoId } = req.params;
  if (!/^[A-Za-z0-9_-]{11}$/.test(videoId)) return res.status(400).send("invalid videoId");

  const tmpFile = join(tmpdir(), `ft-${randomUUID()}.mp4`);
  const proc = spawn("yt-dlp", [
    "--no-warnings",
    "--no-playlist",
    "-f", "best[ext=mp4]/best",
    "--merge-output-format", "mp4",
    "-o", tmpFile,
    `https://www.youtube.com/watch?v=${videoId}`,
  ]);

  proc.on("close", async (code) => {
    if (code !== 0) {
      if (!res.headersSent) res.status(500).send("Download failed");
      try { await unlink(tmpFile); } catch {}
      return;
    }
    res.setHeader("Content-Type", "video/mp4");
    res.setHeader("Content-Disposition", `attachment; filename="${videoId}.mp4"`);
    const stream = createReadStream(tmpFile);
    stream.pipe(res);
    stream.on("close", async () => { try { await unlink(tmpFile); } catch {} });
  });
});

// ─── GET /audio/:videoId ─────────────────────────────────────────────────────
// מוריד את האודיו כ-mp3 (לניגון ברקע + שמירה)
app.get("/audio/:videoId", async (req, res) => {
  const { videoId } = req.params;
  if (!/^[A-Za-z0-9_-]{11}$/.test(videoId)) return res.status(400).send("invalid videoId");

  const tmpFile = join(tmpdir(), `ft-${randomUUID()}.mp3`);
  const proc = spawn("yt-dlp", [
    "--no-warnings",
    "--no-playlist",
    "-f", "bestaudio",
    "-x", "--audio-format", "mp3", "--audio-quality", "0",
    "-o", tmpFile.replace(".mp3", ".%(ext)s"),
    `https://www.youtube.com/watch?v=${videoId}`,
  ]);

  proc.on("close", async (code) => {
    if (code !== 0) {
      if (!res.headersSent) res.status(500).send("Audio extract failed");
      try { await unlink(tmpFile); } catch {}
      return;
    }
    res.setHeader("Content-Type", "audio/mpeg");
    res.setHeader("Content-Disposition", `attachment; filename="${videoId}.mp3"`);
    const stream = createReadStream(tmpFile);
    stream.pipe(res);
    stream.on("close", async () => { try { await unlink(tmpFile); } catch {} });
  });
});

app.listen(PORT, "0.0.0.0", () => {
  console.log(`FilterTube API listening on port ${PORT}`);
});
