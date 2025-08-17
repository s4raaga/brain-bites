# Brain Bites — AI‑Powered Educational Content Pipeline

Blackboard → Claude AI → MoviePy/ElevenLabs → **AWS S3** → **React Native (Expo)** mobile/web app.

> **One‑liner:** A fully automated pipeline that turns university course materials into short, TikTok‑style videos and streams them to a cross‑platform app.

---

## Table of Contents
- [Overview](#overview)
- [Repo Layout (suggested)](#repo-layout-suggested)
- [Prerequisites](#prerequisites)
- [Environment Variables](#environment-variables)
- [Quick Start](#quick-start)
  - [1) Provision AWS S3 & IAM](#1-provision-aws-s3--iam)
  - [2) Backend API (Node + Express)](#2-backend-api-node--express)
  - [3) Mobile/Web App (Expo + React Native)](#3-mobileweb-app-expo--react-native)
  - [4) Content Pipeline (Python)](#4-content-pipeline-python)
  - [5) (Optional) CDN via CloudFront](#5-optional-cdn-via-cloudfront)
- [How It Works (End‑to‑End)](#how-it-works-end-to-end)
- [API Contract](#api-contract)
- [S3 CORS & Headers](#s3-cors--headers)
- [Security Notes](#security-notes)
- [Troubleshooting](#troubleshooting)
- [Roadmap](#roadmap)
- [License](#license)

---

## Overview

**Brain Bites** ingests course files from a learning system (Blackboard), uses an AI agent to generate short lesson scripts, synthesizes voices and composes vertical videos, uploads to **S3**, and streams them in a **React Native (Expo)** app. A small **Express** API lists videos and (optionally) returns presigned URLs for secure streaming.

**Core stack**
- **Frontend:** React Native + Expo (`expo-av`/`react-native-video`), Expo Router.
- **Backend:** Node.js + Express (TypeScript), AWS SDK v3.
- **Pipeline:** Python (Playwright/PDF extract, MCP bridge, Anthropic Claude, ElevenLabs TTS, MoviePy), Tkinter GUI for batch control.
- **Storage:** AWS S3 (optionally behind CloudFront CDN).
- **Dev:** `.env`, Git, Expo Go, LocalTunnel/Ngrok for device testing.

> The architecture SVG lives at `docs/architecture.svg` (also attached in this release).

---

## Repo Layout (suggested)

> Adjust paths as needed if your repo differs.

```
brain-bites/
├─ apps/
│  ├─ mobile/                  # Expo / React Native app
│  └─ api/                     # Node + Express (TypeScript) API
├─ services/
│  └─ pipeline/                # Python content generation pipeline
│     ├─ stage1_bb_mcp.py      # Blackboard MCP / scraping & PDF extract
│     ├─ stage2_agent.py       # Claude agent: script generation
│     ├─ stage3_uploader.py    # Tkinter GUI controller (queues/threads)
│     ├─ stage4_batch_video_generator.py  # ElevenLabs + MoviePy compositor
│     └─ requirements.txt
├─ docs/
│  └─ architecture.svg
├─ .env.example
└─ README.md
```

---

## Prerequisites

- **Node.js** ≥ 18 (LTS) and **npm**/**yarn**/**pnpm**
- **Python** ≥ 3.10 and `pip`
- **ffmpeg** (required by MoviePy)
- **AWS account** with an S3 bucket (e.g., `brain-bite`) in `ap-southeast-2` (Sydney)
- **API keys**:
  - Anthropic **Claude** (`ANTHROPIC_API_KEY`)
  - **ElevenLabs** TTS (`ELEVENLABS_API_KEY`)
- (Optional) **CloudFront** distribution for CDN
- (Optional) **LocalTunnel/Ngrok** to expose the API for real devices

---

## Environment Variables

Create `.env` files for each component or a single root `.env` and load per service.

**Common**
```
AWS_REGION=ap-southeast-2
S3_BUCKET=brain-bite
S3_PREFIX=public/videos/
```

**Backend/API**
```
PORT=3001
CORS_ORIGIN=*

# AWS creds (keep server-side only)
AWS_ACCESS_KEY_ID=xxxxx
AWS_SECRET_ACCESS_KEY=yyyyy

# Presigned URL TTL (seconds)
S3_URL_TTL_SECONDS=3600
```

**Mobile (Expo)**
```
# Use a tunnel/base URL reachable by your phone
EXPO_PUBLIC_API_BASE_URL=http://localhost:3001
# Or: https://<your-subdomain>.loca.lt  / https://<id>.ngrok.app
EXPO_PUBLIC_CDN_BASE_URL=   # optional if using CloudFront
```

**Pipeline (Python)**
```
ANTHROPIC_API_KEY=sk-...
ELEVENLABS_API_KEY=el-...
BLACKBOARD_BASE_URL=https://your-bb.example.edu
BLACKBOARD_USERNAME=student-id
BLACKBOARD_PASSWORD=••••••••
```

> Tip: Check `.env.example` into git, never real secrets.

---

## Quick Start

### 1) Provision AWS S3 & IAM

1. Create an S3 bucket, e.g., `brain-bite` in `ap-southeast-2`.
2. Create an IAM user with **programmatic access** and an inline policy (replace bucket name/prefix):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": ["arn:aws:s3:::brain-bite"],
      "Condition": {"StringLike": {"s3:prefix": ["public/videos/*"]}}
    },
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject"],
      "Resource": ["arn:aws:s3:::brain-bite/public/videos/*"]
    }
  ]
}
```

3. Save the access key & secret (used by the **API** and **pipeline**—**never** the mobile app).

---

### 2) Backend API (Node + Express)

From `apps/api`:

```bash
# install
npm i
# or: yarn / pnpm i

# dev
npm run dev
# expects .env with AWS + S3 config
```

**What it does**
- `GET /api/videos` → lists S3 objects under `S3_PREFIX` and returns JSON:
  - **Option A (public bucket):** returns `https://s3.amazonaws.com/...` URLs.
  - **Option B (private bucket):** returns **presigned URLs** with TTL.

> Ensure your API is reachable by the phone: use `localhost` on web, or a tunnel URL on device.

---

### 3) Mobile/Web App (Expo + React Native)

From `apps/mobile`:

```bash
npm i
npm run start   # opens Expo Dev Tools
# press 'i' for iOS simulator, 'a' for Android, or scan QR with Expo Go
```

Config:
- `EXPO_PUBLIC_API_BASE_URL` must point to your API (tunnel for real device).
- Uses `expo-av`/`react-native-video` to stream video from S3/CDN.
- `FlatList` renders a vertical, TikTok‑style, auto‑play feed.

---

### 4) Content Pipeline (Python)

From `services/pipeline`:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Optional: Playwright for web automation
python -m playwright install

# Run stages (examples)
python stage1_bb_mcp.py        # Acquire course content (PDF/text) from Blackboard
python stage2_agent.py         # Generate lesson scripts with Claude
python stage4_batch_video_generator.py --out ./out --w 1080 --h 1920
python stage3_uploader.py      # GUI to review and upload to S3
```

Outputs land in `public/videos/` locally, then are uploaded to S3 (same prefix the app reads).

> Ensure `ffmpeg` is installed and in PATH for MoviePy.

---

### 5) (Optional) CDN via CloudFront

Point a CloudFront distribution at your S3 bucket. Put the domain in `EXPO_PUBLIC_CDN_BASE_URL` and serve HLS/MP4 from the CDN for lower latency and better caching.

---

## How It Works (End‑to‑End)

1. **Content Acquisition** — Blackboard → scrape or API → extract PDFs/text (`stage1_bb_mcp.py`).
2. **AI Agent** — Claude converts content to bite‑sized scripts (`stage2_agent.py`).
3. **Controller GUI** — Tkinter queues jobs and coordinates TTS/video render (`stage3_uploader.py`).
4. **Video Generation** — MoviePy composes 1080×1920 videos; ElevenLabs provides voices (`stage4_batch_video_generator.py`).
5. **Upload to S3** — Artifacts are pushed to `s3://$S3_BUCKET/public/videos/…`.
6. **API** — Express lists keys (and generates presigned URLs if private).
7. **Mobile App** — Expo app requests `/api/videos`, builds sources, and streams.

**Mermaid flowchart (copy into GitHub/Notion with Mermaid enabled):**
```mermaid
flowchart LR
  U[User] -->|Opens app| RN[React Native (Expo)]
  RN -->|GET /api/videos| API[Node + Express API]
  API -->|listObjectsV2| S3[(Amazon S3)]
  S3 --> API
  API -->|JSON: keys + presigned URLs| RN
  RN -->|FlatList builds sources| AV[expo-av / RN Video]
  AV -->|HTTP Range GET| EDGE[(S3 or CloudFront)]
  EDGE --> AV
  AV -->|Playback| U
```

---

## API Contract

**GET `/api/videos` → 200 OK**
```json
[
  {
    "key": "public/videos/clip1.mp4",
    "url": "https://dxxxxx.cloudfront.net/public/videos/clip1.mp4?X-Amz-SignedHeaders=...",
    "size": 12345678,
    "lastModified": "2025-08-15T02:34:56Z"
  }
]
```

- Pagination may be supported via `?cursor=` (continuation token).
- Server decides whether to return public URLs or **presigned** URLs.

---

## S3 CORS & Headers

To allow web/mobile playback and seeking (range requests), set S3 CORS like:

```xml
<CORSConfiguration>
  <CORSRule>
    <AllowedOrigin>*</AllowedOrigin>
    <AllowedMethod>GET</AllowedMethod>
    <AllowedHeader>*</AllowedHeader>
    <ExposeHeader>Accept-Ranges, Content-Range, Content-Length, ETag, Last-Modified</ExposeHeader>
  </CORSRule>
</CORSConfiguration>
```

Ensure video objects have correct `Content-Type` (e.g., `video/mp4`).

---

## Security Notes

- Keep **AWS credentials server-side** only (API/pipeline). The mobile app should never contain AWS keys.
- Prefer **presigned URLs** (short TTL) with private buckets.
- Apply **least-privilege IAM** (only `ListBucket` and `GetObject` on the video prefix).
- Don’t commit real `.env` files; use an `.env.example` template.

---

## Troubleshooting

- **Videos don’t play / seek:** CORS or missing `Accept-Ranges`/`Content-Range`. Check S3/CloudFront headers.
- **403 from S3:** Presigned URL expired or bucket policy blocks `GetObject`.
- **Works on web, not on phone:** `EXPO_PUBLIC_API_BASE_URL` must be a **tunnel URL** reachable by the device.
- **MoviePy errors:** Install `ffmpeg` and ensure it’s on PATH; check input asset dimensions/codecs.
- **Blackboard login fails:** Verify credentials, update Playwright browser, handle MFA flows if enabled.

---

## Roadmap

- HLS/DASH via MediaConvert for adaptive bitrate.
- Upload flow in the app with chunked multipart.
- Auth (Clerk/Cognito) for protected feeds.
- Moderation (Rekognition/Lambda) on S3 events.
- Observability: player error telemetry + CloudWatch logs.

---
