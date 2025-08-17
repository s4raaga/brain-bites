# Brainrot Reel Generator 🧠

Automated Python script for creating short-form "brainrot" reels with AI narration, background gameplay, and perfectly synced captions.

## Features

- 🎙️ **AI Voice Generation** using ElevenLabs API
- 📝 **Auto Caption Generation** using ElevenLabs alignment timestamps (no Whisper dependency)
- 🎮 **Background Gameplay** support with automatic looping
- 📱 **Vertical Format** optimized for TikTok/Instagram Reels (9:16)
- ⚙️ **Configurable Settings** for voice, captions, and video quality
- 🔄 **Automatic Processing** from script to final video

## Setup Instructions

### 1. Install Dependencies

```bash
cd video_making
pip install -r requirements.txt
```

### 2. API Keys Setup

Create a `.env` file in the `video_making` directory:

```env
ELEVENLABS_API_KEY=your_elevenlabs_api_key_here
```

**Getting API Keys:**
- **ElevenLabs**: Sign up at [elevenlabs.io](https://elevenlabs.io) and get your API key from the profile page

### 3. Prepare Content

#### Script File
Create `inputs/script.txt` with your narration text:

```
Did you know that the human brain processes visual information 60,000 times faster than text? 
That's why short-form videos are so addictive! Your brain gets instant dopamine hits from rapid visual changes, 
making it nearly impossible to scroll away. This is the science behind brainrot content!
```

#### Background Videos
Place your background gameplay videos in `inputs/backgrounds/`:
- Supported formats: `.mp4`, `.avi`, `.mov`, `.mkv`
- Recommended: Minecraft parkour, Subway Surfers, or similar gameplay
- Videos will be automatically cropped to 9:16 vertical format

Example structure:
```
video_making/
├── inputs/
│   ├── script.txt
│   └── backgrounds/
│       ├── minecraft_parkour.mp4
│       ├── subway_surfers.mp4
│       └── satisfying_gameplay.mp4
├── outputs/          # Final videos appear here
└── temp/            # Temporary files (auto-cleaned)
```

## Usage

Run the script:

```bash
python video_making/main.py
```

The script will:
1. ✅ Read your script from `inputs/script.txt`
2. 🎙️ Generate AI narration using ElevenLabs
3. 📝 Create word-level captions using ElevenLabs alignment
4. 🎮 Select and process a random background video
5. 🎬 Compose the final vertical video with captions
6. 💾 Save the result in `outputs/final_[timestamp].mp4`
7. 🧹 Clean up temporary files

## Configuration

Edit `config.json` to customize:

```json
{
  "voice_id": "21m00Tcm4TlvDq8ikWAM",
  "voice_stability": 0.5,
  "voice_similarity_boost": 0.75,
  "caption_font": "Impact",
  "caption_fontsize": 70,
  "caption_color": "white",
  "caption_stroke_color": "black",
  "caption_stroke_width": 3,
  "video_width": 1080,
  "video_height": 1920
}
```

### Voice Settings
- **voice_id**: ElevenLabs voice ID (find more at elevenlabs.io)
- **voice_stability**: 0.0-1.0 (lower = more expressive)
- **voice_similarity_boost**: 0.0-1.0 (higher = more consistent)

### Caption Settings
- **caption_font**: Font family (Impact, Arial, etc.)
- **caption_fontsize**: Size in pixels
- **caption_color**: Text color
- **caption_stroke_color**: Outline color
- **caption_stroke_width**: Outline thickness

## Example Output

Your final video will have:
- ✅ Vertical 9:16 aspect ratio (1080x1920)
- ✅ Background gameplay (auto-cropped and looped)
- ✅ AI-generated narration audio
- ✅ Auto-synced captions with bold Impact font
- ✅ Professional black stroke on white text
- ✅ Captions positioned at bottom 25% of screen

## Troubleshooting

### Common Issues

**"Script file not found"**
- Ensure `inputs/script.txt` exists and has content

**"No background videos found"**
- Add .mp4 files to `inputs/backgrounds/` directory

**"API key not found"**
- Check your `.env` file has the correct API keys
- Ensure no extra spaces around the = sign

**"ElevenLabs API error"**
- Verify your API key is valid and has credits
- Check if the voice_id in config.json exists

**"Captions missing"**
- ElevenLabs alignment endpoint may have failed—check logs for alignment warnings
- Ensure you didn't exceed rate limits

### Logs

Check `error.log` for detailed error information if the script fails.

## File Structure

```
video_making/
├── main.py              # Main script
├── config.json          # Configuration settings
├── requirements.txt     # Python dependencies
├── .env                # API keys (create this)
├── README.md           # This file
├── error.log           # Error logs (auto-generated)
├── inputs/
│   ├── script.txt      # Your narration script
│   └── backgrounds/    # Background gameplay videos
├── outputs/            # Final rendered videos
└── temp/              # Temporary files (auto-cleaned)
```

## Tips for Best Results

1. **Script Length**: Keep scripts 30-60 seconds for optimal engagement
2. **Background Videos**: Use high-quality, visually interesting gameplay
3. **Voice Selection**: Test different ElevenLabs voices to find your style
4. **Caption Timing**: Alignment-based word timestamps generate precise captions
5. **File Naming**: Output files include timestamps for easy organization

---

**Ready to create viral brainrot content? Run the script and watch the magic happen! 🚀**

## Library / Package Usage (New)

You can now import the generator directly instead of invoking the batch script. Example:

```python
from pathlib import Path
from video_making import BrainBitesVideoGenerator

gen = BrainBitesVideoGenerator()
output_path = gen.generate_from_file(Path('transcripts/my_script.json'))
print('Video saved at', output_path)
```

The transcript JSON should contain: `title`, `description`, and a `dialogue` list with entries like:

```json
{
  "title": "Integration Basics",
  "description": "Grounded explainer referencing MATH101 lecture notes",
  "dialogue": [
    {"speaker": "Speaker A", "text": "Integrals accumulate infinitely small areas."},
    {"speaker": "Speaker B", "text": "Think of slicing the area into strips."}
  ]
}
```

This same package API is used by the GUI uploader to convert generated scripts into videos.