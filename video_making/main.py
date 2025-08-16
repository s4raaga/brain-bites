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
import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv
from moviepy.editor import VideoFileClip, AudioFileClip, CompositeVideoClip, TextClip
import pysrt

# Fix PIL compatibility issue
from PIL import Image
if not hasattr(Image, 'ANTIALIAS'):
    Image.ANTIALIAS = Image.LANCZOS


class BrainrotReelGenerator:
    def __init__(self):
        self.base_dir = Path(__file__).parent
        self.inputs_dir = self.base_dir / "inputs"
        self.outputs_dir = self.base_dir / "outputs" 
        self.temp_dir = self.base_dir / "temp"
        self.backgrounds_dir = self.inputs_dir / "assets" / "backgrounds"
        
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
        
        if not self.elevenlabs_api_key:
            raise ValueError("ELEVENLABS_API_KEY not found in .env file")
            
        # Initialize S3 client
        self.aws_access_key = os.getenv('AWS_ACCESS_KEY_ID')
        self.aws_secret_key = os.getenv('AWS_SECRET_ACCESS_KEY')
        self.s3_bucket = os.getenv('S3_BUCKET_NAME')
        self.s3_region = os.getenv('S3_REGION', 'us-east-1')
        
        if all([self.aws_access_key, self.aws_secret_key, self.s3_bucket]):
            self.s3_client = boto3.client(
                's3',
                aws_access_key_id=self.aws_access_key,
                aws_secret_access_key=self.aws_secret_key,
                region_name=self.s3_region
            )
            self.logger.info("S3 client initialized")
        else:
            self.s3_client = None
            self.logger.warning("S3 credentials not found, videos will only be saved locally")

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

    def read_script(self) -> tuple[str, str, str]:
        """Read the script from inputs/script.txt or dialogue JSON"""
        script_path = self.inputs_dir / "script.txt"
        
        if script_path.exists():
            with open(script_path, 'r', encoding='utf-8') as f:
                script = f.read().strip()
            
            if not script:
                raise ValueError("Script file is empty")
            
            self.logger.info(f"Script loaded: {len(script)} characters")
            return script, None, None
        
        # If no script.txt, look for dialogue JSON files
        dialogues_dir = self.inputs_dir / "dialogues"
        if dialogues_dir.exists():
            json_files = list(dialogues_dir.glob("*.json"))
            if json_files:
                # Use the first JSON file found
                dialogue_file = json_files[0]
                with open(dialogue_file, 'r', encoding='utf-8') as f:
                    dialogue_data = json.load(f)
                
                # Extract script from dialogue
                dialogue_lines = dialogue_data.get('dialogue', [])
                script_parts = []
                for line in dialogue_lines:
                    text = line.get('text', '')
                    script_parts.append(text)
                
                script = ' '.join(script_parts)
                title = dialogue_data.get('title', None)
                description = dialogue_data.get('description', None)
                
                self.logger.info(f"Script loaded from dialogue JSON: {len(script)} characters")
                return script, title, description
        
        raise FileNotFoundError("No script.txt or dialogue JSON files found")

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

    def generate_captions_from_script(self, script: str, audio_duration: float) -> str:
        """Generate simple captions from script text with estimated timing"""
        captions_path = self.temp_dir / "captions.srt"
        
        self.logger.info("Generating captions from script...")
        
        # Split script into words and create simple timing
        words = script.split()
        words_per_second = len(words) / audio_duration
        
        # Create SRT content
        srt_content = ""
        for i, word in enumerate(words):
            start_time = i / words_per_second
            end_time = (i + 1) / words_per_second
            
            # Convert to SRT time format
            start_srt = self._seconds_to_srt_time(start_time)
            end_srt = self._seconds_to_srt_time(end_time)
            
            srt_content += f"{i+1}\n{start_srt} --> {end_srt}\n{word}\n\n"
        
        with open(captions_path, 'w', encoding='utf-8') as f:
            f.write(srt_content)
        
        self.logger.info(f"Captions generated: {captions_path}")
        return str(captions_path)
    
    def _seconds_to_srt_time(self, seconds: float) -> str:
        """Convert seconds to SRT time format (HH:MM:SS,mmm)"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millisecs = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millisecs:03d}"
    
    def _create_filename_from_title_description(self, title: str, description: str) -> str:
        """Create a filename from title and description"""
        import re
        
        # Combine title and description
        combined = f"{title}_{description}"
        
        # Replace spaces and special characters with hyphens
        filename = re.sub(r'[^\w\s-]', '', combined)  # Remove special chars except spaces and hyphens
        filename = re.sub(r'\s+', '-', filename)      # Replace spaces with hyphens
        filename = re.sub(r'-+', '-', filename)       # Replace multiple hyphens with single
        filename = filename.strip('-')                # Remove leading/trailing hyphens
        
        # Limit length and convert to lowercase
        filename = filename[:100].lower()
        
        return filename

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

    def create_video(self, script: str, voice_path: str, captions_path: str, background_path: str, title: str = None, description: str = None) -> str:
        """Create the final video with all components"""
        if title and description:
            # Create filename from title and description
            filename = self._create_filename_from_title_description(title, description)
        else:
            # Fallback to timestamp if no title/description
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"final_{timestamp}"
        
        output_path = self.outputs_dir / f"{filename}.mp4"
        
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
                start_time = subtitle.start.hours * 3600 + subtitle.start.minutes * 60 + subtitle.start.seconds + subtitle.start.milliseconds / 1000
                end_time = subtitle.end.hours * 3600 + subtitle.end.minutes * 60 + subtitle.end.seconds + subtitle.end.milliseconds / 1000
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
        
        # Upload to S3 if configured
        if self.s3_client:
            s3_url = self.upload_to_s3(str(output_path))
            if s3_url:
                self.logger.info(f"Video uploaded to S3: {s3_url}")
                return s3_url
        
        return str(output_path)

    def upload_to_s3(self, file_path: str) -> Optional[str]:
        """Upload video file to S3 bucket"""
        try:
            file_name = Path(file_path).name
            s3_key = f"videos/{file_name}"
            
            self.logger.info(f"Uploading {file_name} to S3 bucket {self.s3_bucket}...")
            
            # Upload file
            self.s3_client.upload_file(
                file_path, 
                self.s3_bucket, 
                s3_key,
                ExtraArgs={'ContentType': 'video/mp4'}
            )
            
            # Generate S3 URL
            s3_url = f"https://{self.s3_bucket}.s3.{self.s3_region}.amazonaws.com/{s3_key}"
            return s3_url
            
        except ClientError as e:
            self.logger.error(f"Failed to upload to S3: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Unexpected error uploading to S3: {e}")
            return None

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
            script, title, description = self.read_script()
            
            # Step 2: Generate AI voice
            voice_path = self.generate_voice(script)
            
            # Step 3: Generate captions from script
            audio_clip = AudioFileClip(voice_path)
            audio_duration = audio_clip.duration
            audio_clip.close()
            captions_path = self.generate_captions_from_script(script, audio_duration)
            
            # Step 4: Select background video
            background_path = self.select_background_video()
            
            # Step 5: Create final video
            output_path = self.create_video(script, voice_path, captions_path, background_path, title, description)
            
            # Step 6: Cleanup
            self.cleanup_temp_files()
            
            self.logger.info(f"✅ SUCCESS! Video created: {output_path}")
            return output_path
            
        except Exception as e:
            self.logger.error(f"❌ ERROR: {str(e)}")
            self.logger.error(traceback.format_exc())
            sys.exit(1)

    # --- Added helper for external callers (e.g., Tkinter GUI) to supply script text directly ---
    def run_with_script(self, script_text: str) -> str:
        """End-to-end generation given an in-memory script string.

        Mirrors run(), but skips reading from inputs/script.txt so GUI orchestrators
        can pass dynamic script text.
        """
        try:
            if not script_text or not script_text.strip():
                raise ValueError("Provided script_text is empty")
            script = script_text.strip()
            self.logger.info("Starting Brainrot Reel Generator (external script)...")
            voice_path = self.generate_voice(script)
            audio_clip = AudioFileClip(voice_path)
            audio_duration = audio_clip.duration
            audio_clip.close()
            captions_path = self.generate_captions_from_script(script, audio_duration)
            background_path = self.select_background_video()
            output_path = self.create_video(script, voice_path, captions_path, background_path)
            self.cleanup_temp_files()
            self.logger.info(f"✅ SUCCESS! Video created: {output_path}")
            return output_path
        except Exception as e:  # noqa: BLE001
            self.logger.error(f"❌ ERROR: {e}")
            self.logger.error(traceback.format_exc())
            raise


if __name__ == "__main__":
    generator = BrainrotReelGenerator()
    generator.run()