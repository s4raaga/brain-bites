# Brain Bites — AI‑Powered Educational Content Pipeline

**Blackboard** → **Claude AI + MCP** → **ElevenLabs TTS + MoviePy** → **AWS S3** → **React Native (Expo)** mobile app

> **Complete automated pipeline:** University course materials → AI-generated educational videos → TikTok-style mobile streaming app

---

## Table of Contents
- [Overview](#overview)
- [System Architecture](#system-architecture)
- [Current Project Structure](#current-project-structure)
- [Quick Start](#quick-start)
  - [1) Environment Setup](#1-environment-setup)
  - [2) Mobile App (React Native + Expo)](#2-mobile-app-react-native--expo)
  - [3) Backend API (Express + TypeScript)](#3-backend-api-express--typescript)
  - [4) Content Generation Pipeline (Python)](#4-content-generation-pipeline-python)
- [Features](#features)
- [Development Workflow](#development-workflow)
- [Generated Content Examples](#generated-content-examples)
- [Troubleshooting](#troubleshooting)
- [Technical Details](#technical-details)


---

## Overview

**Brain Bites** is a fully functional educational content pipeline that:

1. **Extracts content** from Blackboard courses using Model Context Protocol (MCP)
2. **Generates scripts** using Claude AI with course-specific context
3. **Creates videos** with ElevenLabs voices, dynamic character positioning, and synchronized captions
4. **Uploads to S3** for CDN delivery
5. **Streams in mobile app** with TikTok-style vertical scrolling interface

**Current Status: ✅ Fully Operational**
- 9 educational videos successfully generated and deployed
- Mobile app with single-tab navigation and BrainBites branding
- Complete development environment with tunneling for device testing
- Dynamic character system supporting any 2-character conversations

**Tech Stack**
- **Mobile:** React Native + Expo, expo-video, vertical TikTok-style feed
- **Backend:** Express + TypeScript, S3 integration, tunnel-enabled development
- **Pipeline:** Python with Playwright, MCP, Claude API, ElevenLabs, MoviePy
- **Storage:** AWS S3 (ap-southeast-2) with direct CDN streaming
- **Development:** LocalTunnel for mobile device testing


## System Architecture

Brain Bites implements a complete 8-stage pipeline:

```
[MCP Layer] → [Agent Processing] → [GUI Control] → [Video Generation] → [S3 Storage] → [API Server] → [Tunneling] → [Mobile App]
```

Key innovations:
- **Model Context Protocol (MCP)** for Blackboard integration
- **Dynamic character positioning** for any 2-speaker combinations
- **Word-level caption synchronization** using ElevenLabs alignment
- **Direct S3 streaming** with fallback CDN URLs
- **Tunnel-based development** for real device testing

## Current Project Structure

```
brain-bites/
├── brain-bites/                    # React Native Expo app
│   ├── app/
│   │   ├── (tabs)/
│   │   │   ├── _layout.tsx         # Single tab with BrainBites logo
│   │   │   └── index.tsx           # Main video feed
│   │   └── VideoFeed.tsx           # TikTok-style vertical video component
│   ├── assets/images/branding/     # BrainBites logo assets
│   └── server.ts                   # Express API server
├── video_making/                   # Python video generation pipeline
│   ├── uploader/
│   │   └── uploader.py             # Tkinter GUI controller
│   ├── agent.py                    # Claude AI + MCP bridge
│   ├── bb_mcp.py                   # Blackboard MCP server
│   ├── batch_video_generator.py    # Video composition pipeline
│   ├── inputs/
│   │   ├── dialogues/              # Generated JSON scripts (9 videos)
│   │   └── assets/
│   │       ├── backgrounds/        # Background videos
│   │       └── characters/         # Character images (Alex, Sam, Zain, Cielo)
│   └── outputs/                    # Local video storage
├── SYSTEM_ARCHITECTURE.md          # Complete technical documentation
└── README.md                       # This file
```

## Quick Start

### 1) Environment Setup

**Prerequisites:**
- Node.js ≥ 18, Python ≥ 3.10, ffmpeg
- AWS S3 bucket: `brain-bite-bucket` (ap-southeast-2)
- API Keys: Anthropic Claude, ElevenLabs

**Environment Variables:**
Create `.env` in `video_making/` directory:
```bash
ANTHROPIC_API_KEY=your_anthropic_key_here
ELEVENLABS_API_KEY=your_elevenlabs_key_here
AWS_ACCESS_KEY_ID=your_aws_access_key
AWS_SECRET_ACCESS_KEY=your_aws_secret_key
S3_BUCKET_NAME=brain-bite-bucket
S3_REGION=ap-southeast-2
```

### 2) Mobile App (React Native + Expo)

```bash
cd brain-bites
npm install
npm start
# Scan QR code with Expo Go app or press 'i' for iOS simulator
```

**Key Features:**
- Single-tab navigation with BrainBites logo
- TikTok-style vertical scrolling video feed
- Auto-play with tap-to-unmute functionality
- Direct S3 streaming with fallback URLs
- Reaction system with animations

### 3) Backend API (Express + TypeScript)

```bash
cd brain-bites
npm run dev:server
# Starts Express server on localhost:3001
```

**For device testing (tunnel setup):**
```bash
# Terminal 1: Start API server
npm run dev:server

# Terminal 2: Create tunnel
npx localtunnel --port 3001 --subdomain brain-bites-api
# Or use: npx expo start --tunnel
```

### 4) Content Generation Pipeline (Python)

```bash
cd video_making
pip install -r requirements.txt
python -m playwright install
```

**Generate videos from existing dialogues:**
```bash
python batch_video_generator.py
# Processes all 9 JSON scripts in inputs/dialogues/
# Automatically uploads to S3
```

**Create new content from Blackboard:**
```bash
python uploader/uploader.py
# GUI interface for:
# 1. Blackboard login
# 2. Script generation
# 3. Video creation
```

## Features

### 🎬 Video Generation System
- **Dynamic Character System**: 4 characters (Alex, Sam, Zain, Cielo) with automatic left-right positioning
- **ElevenLabs TTS Integration**: High-quality voices with word-level alignment for perfect caption sync
- **Background Videos**: Minecraft, Subway Surfers, Kinetic Sand gameplay for engagement
- **Automated Batch Processing**: Generate multiple videos from JSON dialogue files
- **S3 Auto-Upload**: Direct upload to CDN with public URL generation

### 📱 Mobile App Experience
- **TikTok-Style Interface**: Vertical scrolling, auto-play, swipe navigation
- **Single Tab Design**: Clean navigation with centered BrainBites logo
- **Reaction System**: Animated emoji reactions (🤩, 😵‍💫)
- **Smart Controls**: Tap-to-unmute, pull-to-refresh, seamless looping
- **Responsive Design**: Optimized for both iOS and Android devices

### 🤖 AI Content Pipeline
- **Model Context Protocol (MCP)**: Direct Blackboard integration for course content
- **Claude AI Integration**: Context-aware script generation with course materials
- **Educational Focus**: Generates content for computer science, mathematics, and engineering topics
- **Quality Assurance**: Factual verification against source PDFs

### 🔧 Development Tools
- **Tkinter GUI**: Visual interface for content generation workflow
- **Tunnel Development**: LocalTunnel integration for real device testing
- **Hot Reload**: Live updates during development
- **Comprehensive Logging**: Detailed logs for debugging and monitoring

## Development Workflow

### Current Generated Content (9 Videos)
All videos are live and accessible in the mobile app:

1. **Big-O Notation** (COMP3506: Data Structures and Algorithms)
2. **Eigenvalues and Eigenvectors** (MATH1051: Calculus and Linear Algebra 1)
3. **Gradient Vectors** (MATH1052: Multivariate Calculus)
4. **Hash Tables** (COMP3506: Data Structures and Algorithms)
5. **Linked Lists** (COMP3506: Data Structures and Algorithms)
6. **Proof by Contradiction** (MATH1061: Discrete Maths)
7. **Recurrence Relations** (COMP3506: Data Structures and Algorithms)
8. **Set Theory Basics** (MATH1061: Discrete Maths)
9. **Taylor Series** (MATH1051: Calculus and Linear Algebra 1)

### Development Commands
```bash
# Start complete development environment
cd brain-bites && npm run dev:server                    # Terminal 1: API
npx localtunnel --port 3001 --subdomain brain-bites-api # Terminal 2: Tunnel  
npm start                                                # Terminal 3: Expo

# Generate new content
cd video_making && python uploader/uploader.py          # GUI for new videos
python batch_video_generator.py                         # Batch process existing dialogues
```

## Generated Content Examples

Each video follows this format with synchronized captions and character animations:

**Example: "Big-O Notation" (COMP3506)**
```json
{
  "title": "Big-O Notation",
  "description": "COMP3506: Data Structures and Algorithms",
  "dialogue": [
    {
      "character": "character1",
      "text": "Yo, why do computer scientists keep saying O(n) like it's some TikTok trend?"
    },
    {
      "character": "character3", 
      "text": "That's Big-O notation fam. It's like rating how sigma or cringe your algorithm is when data gets massive."
    }
    // ... more dialogue
  ]
}
```

**Video Output Features:**
- 1080x1920 vertical format (TikTok-style)
- Character images with jiggle animation during speech
- Word-synchronized captions with character-specific colors
- Educational background gameplay (Minecraft, Subway Surfers)
- Auto-upload to S3 with descriptive filenames

## Troubleshooting

### Mobile App Issues
- **App won't load videos**: Check tunnel URL in VideoFeed.tsx API_BASE constant
- **Videos don't play on mobile data**: Use Expo tunnel mode: `npx expo start --tunnel`
- **App crashes on device**: Check Expo Go app compatibility and clear cache

### Video Generation Issues
- **ElevenLabs API errors**: Check voice IDs in batch_video_generator.py character configs
- **MoviePy fails**: Ensure ffmpeg is installed and background videos exist in inputs/assets/backgrounds/
- **S3 upload fails**: Verify AWS credentials and S3_BUCKET_NAME in .env

### Development Setup
- **Tunnel not accessible**: Try different subdomain or use ngrok instead of localtunnel
- **MCP connection errors**: Check bb_mcp.py for Blackboard authentication issues
- **GUI won't start**: Install tkinter: `sudo apt-get install python3-tk` (Linux)

### Common Commands for Fixes
```bash
# Reset Expo cache
npx expo start --clear

# Reinstall Python dependencies
cd video_making && pip install -r requirements.txt --force-reinstall

# Check S3 bucket access
aws s3 ls s3://brain-bite-bucket/videos/ --region ap-southeast-2

# Restart development servers
cd brain-bites && npm run dev:server
npx localtunnel --port 3001 --subdomain brain-bites-api
```

## Technical Details

### Performance Metrics
- **Video Generation**: 2-3 minutes per 15-20 second video
- **Voice Synthesis**: 1-2 seconds per dialogue line
- **S3 Upload**: 10-30 seconds per video
- **Mobile Streaming**: Instant playback from S3 CDN
- **Total Pipeline**: 5-7 minutes from script to mobile delivery

### S3 Configuration
- **Bucket**: brain-bite-bucket (ap-southeast-2)
- **Structure**: `videos/{title}_{description}.mp4`
- **Access**: Public read for direct streaming
- **CDN**: Direct S3 URLs for optimal performance


---

## AI Usage Declaration

**This project used AI assistance for development and code generation:**

- **Code Development**: Significant portions of the codebase were generated and enhanced using AI
- **Content Generation**: Educational video scripts and dialogue are generated using Claude AI with course material context
- **Voice Synthesis**: All character voices generated using ElevenLabs AI text-to-speech technology

**Human Contributions**: Coding, project vision, requirements definition, API key management, testing, deployment decisions, and overall system integration.

**AI Tools Used:**
- **Claude AI (Anthropic)**: Code generation, documentation, debugging
- **Github CoPiot (GPT 5)**: Code generation, documentation, debugging
- **Cursor (GPT 5)**: Code generation, documentation, debugging
- **ElevenLabs**: Voice synthesis for video characters
- **Model Context Protocol (MCP)**: AI-powered integration with Blackboard systems

This project demonstrates the effective collaboration between human creativity and AI capabilities in building a complete educational technology solution.

---

**🚀 Brain Bites is fully operational and ready for educational content creation!**

