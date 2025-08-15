#!/usr/bin/env python3
"""
Brainrot Reel Generator
Automated script for creating short-form vertical videos with AI narration and captions.
"""

import os
import sys
import json
import random
import logging
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional, List

import requests
from dotenv import load_dotenv
from openai import OpenAI
from moviepy.editor import VideoFileClip, AudioFileClip, CompositeVideoClip, TextClip
import pysrt


class BrainrotReelGenerator:
    def __init__(self):
        self.base_dir = Path(__file__).parent
        self.inputs_dir = self.base_dir / "inputs"
        self.outputs_dir = self.base_dir / "outputs" 
        self.temp_dir = self.base_dir / "temp"
        self.backgrounds_dir = self.inputs_dir / "backgrounds"
        
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(self.base_dir / 'error.log'),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger(__name__)
        
        # Load environment variables
        load_dotenv(self.base_dir / '.env')
        
        # Load config
        self.config = self.load_config()
        
        # Initialize APIs
        self.elevenlabs_api_key = os.getenv('ELEVENLABS_API_KEY')
        self.openai_api_key = os.getenv('OPENAI_API_KEY')
        
        if not self.elevenlabs_api_key:
            raise ValueError("ELEVENLABS_API_KEY not found in .env file")
        if not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY not found in .env file")
            
        self.openai_client = OpenAI(api_key=self.openai_api_key)

    def load_config(self) -> dict:
        """Load configuration from config.json"""
        config_path = self.base_dir / "config.json"
        default_config = {
            "voice_id": "21m00Tcm4TlvDq8ikWAM",  # Default ElevenLabs voice
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
        
        if config_path.exists():
            try:
                with open(config_path, 'r') as f:
                    user_config = json.load(f)
                default_config.update(user_config)
            except Exception as e:
                self.logger.warning(f"Error loading config.json, using defaults: {e}")
        
        return default_config

    def read_script(self) -> str:
        """Read the script from inputs/script.txt"""
        script_path = self.inputs_dir / "script.txt"
        
        if not script_path.exists():
            raise FileNotFoundError(f"Script file not found: {script_path}")
        
        with open(script_path, 'r', encoding='utf-8') as f:
            script = f.read().strip()
        
        if not script:
            raise ValueError("Script file is empty")
        
        self.logger.info(f"Script loaded: {len(script)} characters")
        return script

    def generate_voice(self, text: str) -> str:
        """Generate AI voice using ElevenLabs API"""
        voice_path = self.temp_dir / "voice.mp3"
        
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.config['voice_id']}"
        
        headers = {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": self.elevenlabs_api_key
        }
        
        data = {
            "text": text,
            "model_id": "eleven_monolingual_v1",
            "voice_settings": {
                "stability": self.config['voice_stability'],
                "similarity_boost": self.config['voice_similarity_boost']
            }
        }
        
        self.logger.info("Generating AI voice...")
        response = requests.post(url, json=data, headers=headers)
        
        if response.status_code != 200:
            raise Exception(f"ElevenLabs API error: {response.status_code} - {response.text}")
        
        with open(voice_path, 'wb') as f:
            f.write(response.content)
        
        self.logger.info(f"Voice generated: {voice_path}")
        return str(voice_path)

    def generate_captions(self, audio_path: str) -> str:
        """Generate captions using OpenAI Whisper"""
        captions_path = self.temp_dir / "captions.srt"
        
        self.logger.info("Generating captions with Whisper...")
        
        with open(audio_path, "rb") as audio_file:
            transcript = self.openai_client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="srt"
            )
        
        with open(captions_path, 'w', encoding='utf-8') as f:
            f.write(transcript)
        
        self.logger.info(f"Captions generated: {captions_path}")
        return str(captions_path)

    def select_background_video(self) -> str:
        """Select a random background video from inputs/backgrounds/"""
        video_extensions = ['.mp4', '.avi', '.mov', '.mkv']
        background_videos = []
        
        for ext in video_extensions:
            background_videos.extend(self.backgrounds_dir.glob(f"*{ext}"))
        
        if not background_videos:
            raise FileNotFoundError(f"No background videos found in {self.backgrounds_dir}")
        
        selected_video = random.choice(background_videos)
        self.logger.info(f"Selected background video: {selected_video.name}")
        return str(selected_video)

    def create_video(self, script: str, voice_path: str, captions_path: str, background_path: str) -> str:
        """Create the final video with all components"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = self.outputs_dir / f"final_{timestamp}.mp4"
        
        self.logger.info("Creating final video...")
        
        # Load audio to get duration
        audio_clip = AudioFileClip(voice_path)
        audio_duration = audio_clip.duration
        
        # Load and process background video
        background_clip = VideoFileClip(background_path)
        
        # Loop background if shorter than audio
        if background_clip.duration < audio_duration:
            loop_count = int(audio_duration / background_clip.duration) + 1
            background_clip = background_clip.loop(n=loop_count)
        
        # Trim to audio duration
        background_clip = background_clip.subclip(0, audio_duration)
        
        # Resize to 9:16 vertical format
        target_width = self.config['video_width']
        target_height = self.config['video_height']
        
        # Calculate crop dimensions to maintain aspect ratio
        bg_aspect = background_clip.w / background_clip.h
        target_aspect = target_width / target_height
        
        if bg_aspect > target_aspect:
            # Background is wider, crop horizontally
            new_width = int(background_clip.h * target_aspect)
            background_clip = background_clip.crop(
                x_center=background_clip.w/2,
                width=new_width
            )
        else:
            # Background is taller, crop vertically
            new_height = int(background_clip.w / target_aspect)
            background_clip = background_clip.crop(
                y_center=background_clip.h/2,
                height=new_height
            )
        
        # Resize to target resolution
        background_clip = background_clip.resize((target_width, target_height))
        
        # Add audio
        background_clip = background_clip.set_audio(audio_clip)
        
        # Load and create caption clips
        caption_clips = []
        if os.path.exists(captions_path):
            subtitles = pysrt.open(captions_path)
            
            for subtitle in subtitles:
                start_time = subtitle.start.total_seconds()
                end_time = subtitle.end.total_seconds()
                text = subtitle.text.replace('\n', ' ')
                
                # Create text clip
                txt_clip = TextClip(
                    text,
                    fontsize=self.config['caption_fontsize'],
                    font=self.config['caption_font'],
                    color=self.config['caption_color'],
                    stroke_color=self.config['caption_stroke_color'],
                    stroke_width=self.config['caption_stroke_width'],
                    method='caption',
                    size=(target_width * 0.9, None)
                ).set_position(('center', target_height * 0.75)).set_duration(
                    end_time - start_time
                ).set_start(start_time)
                
                caption_clips.append(txt_clip)
        
        # Composite final video
        if caption_clips:
            final_clip = CompositeVideoClip([background_clip] + caption_clips)
        else:
            final_clip = background_clip
        
        # Write final video
        final_clip.write_videofile(
            str(output_path),
            fps=24,
            codec='libx264',
            audio_codec='aac',
            temp_audiofile=str(self.temp_dir / "temp_audio.m4a"),
            remove_temp=True
        )
        
        # Close clips to free memory
        audio_clip.close()
        background_clip.close()
        final_clip.close()
        for clip in caption_clips:
            clip.close()
        
        self.logger.info(f"Final video created: {output_path}")
        return str(output_path)

    def cleanup_temp_files(self):
        """Clean up temporary files"""
        for temp_file in self.temp_dir.glob("*"):
            if temp_file.is_file():
                temp_file.unlink()
        self.logger.info("Temporary files cleaned up")

    def run(self):
        """Main execution workflow"""
        try:
            self.logger.info("Starting Brainrot Reel Generator...")
            
            # Step 1: Read script
            script = self.read_script()
            
            # Step 2: Generate AI voice
            voice_path = self.generate_voice(script)
            
            # Step 3: Generate captions
            captions_path = self.generate_captions(voice_path)
            
            # Step 4: Select background video
            background_path = self.select_background_video()
            
            # Step 5: Create final video
            output_path = self.create_video(script, voice_path, captions_path, background_path)
            
            # Step 6: Cleanup
            self.cleanup_temp_files()
            
            self.logger.info(f"✅ SUCCESS! Video created: {output_path}")
            return output_path
            
        except Exception as e:
            self.logger.error(f"❌ ERROR: {str(e)}")
            self.logger.error(traceback.format_exc())
            sys.exit(1)


if __name__ == "__main__":
    generator = BrainrotReelGenerator()
    generator.run()