# Brain Bites 🧠

A TikTok-style educational video app built with React Native and Expo. Brain Bites delivers engaging short-form educational content with seamless video streaming from S3.

## 🚀 Quick Setup

### Prerequisites
- Node.js (v18 or higher)
- npm or yarn
- Expo CLI (`npm install -g @expo/cli`)
- Mobile device with Expo Go app installed

### 1. Install Dependencies

```bash
npm install
```

### 2. Set Up Environment Variables

Create a `.env` file in the root directory:

```env
S3_BUCKET_NAME=your-bucket-name
S3_REGION=ap-southeast-2
PORT=3001
```

### 3. Start the Backend Server

Open a new terminal and start the Express API server:

```bash
cd backend
npm install
npm run dev
```

The backend will run on `http://localhost:3001`

### 4. Set Up Tunneling with ngrok

**Option A: Using localtunnel (Recommended)**

Install localtunnel globally:
```bash
npm install -g localtunnel
```

Start the tunnel:
```bash
lt --port 3001 --subdomain your-custom-name
```

**Option B: Using ngrok**

Install ngrok and start tunnel:
```bash
# Install ngrok (if not already installed)
brew install ngrok  # macOS
# or download from https://ngrok.com/download

# Start tunnel
ngrok http 3001
```

### 5. Update API Endpoint

Copy the tunnel URL (e.g., `https://your-custom-name.loca.lt`) and update the API endpoint in:

**File: `app/VideoFeed.tsx`**
```typescript
const API_BASE = 'https://your-tunnel-url-here.loca.lt';
```

### 6. Start the Expo App

```bash
npx expo start
```

### 7. Open on Your Device

1. Open the Expo Go app on your phone
2. Scan the QR code displayed in terminal
3. The app will load and start streaming videos from S3

## 📱 App Features

- **TikTok-style vertical video feed** - Swipe up/down to navigate
- **Auto-play with mute toggle** - Tap to unmute videos
- **Pull-to-refresh** - Refresh video feed
- **S3 video streaming** - Videos hosted on AWS S3
- **Responsive design** - Works on iOS and Android

## 🛠 Architecture

### Frontend (React Native + Expo)
- **VideoFeed.tsx** - Main TikTok-style video component
- **expo-video** - Video player with streaming support
- **FlatList** - Efficient vertical scrolling
- **expo-linear-gradient** - UI gradients

### Backend (Express API)
- **server.ts** - REST API for video metadata
- **videos.ts** - S3 integration for video listings
- **CORS enabled** - Cross-origin support for mobile app

### Infrastructure
- **AWS S3** - Video storage and CDN
- **Tunneling** - Local development with public access
- **Environment configs** - Secure credential management

## 🔧 Development Workflow

### Adding New Videos

1. Upload videos to your S3 bucket in the `videos/` folder
2. The backend automatically discovers new videos
3. Pull-to-refresh in the app to load new content

### Updating Tunnel URL

If your tunnel URL changes:

1. Update `API_BASE` in `app/VideoFeed.tsx`
2. Restart the Expo development server
3. Refresh the app on your device

### Backend Development

The backend runs independently and can be developed separately:

```bash
cd backend
npm run dev    # Start with hot reload
npm start      # Production mode
```

## 📋 Troubleshooting

### "Network request failed"
- Check that backend server is running on port 3001
- Verify tunnel URL is correct and accessible
- Ensure S3 bucket permissions allow public read access

### Videos not loading
- Verify S3 bucket name and region in environment variables
- Check that videos are in `videos/` folder in S3
- Confirm video files are in MP4 format

### Expo Go connection issues
- Ensure phone and computer are on same network
- Try using tunnel mode: `npx expo start --tunnel`
- Clear Expo Go cache in app settings

## 🌐 Deployment

### Production Backend
Deploy the Express API to your preferred hosting platform:

- **Heroku**: `git push heroku main`
- **Vercel**: `vercel deploy`
- **AWS EC2/ECS**: Use Docker deployment

### Production App
Build for app stores:

```bash
expo build:ios     # iOS App Store
expo build:android # Google Play Store
```

## 📚 Tech Stack

- **React Native** - Mobile framework
- **Expo** - Development platform
- **TypeScript** - Type safety
- **Express.js** - Backend API
- **AWS S3** - Video storage
- **expo-video** - Video playback
- **Tunneling** - Development connectivity

---

*Need help? Check the troubleshooting section or contact the development team.*
