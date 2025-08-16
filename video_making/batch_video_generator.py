#!/usr/bin/env python3
"""
Batch Video Generator
Production script for creating brainrot videos from all dialogue JSON files
- Processes all dialogue JSON files in inputs/dialogues/
- Cycles through background videos 
- Uploads all videos to S3
- Uses title_description naming convention
"""

import os
import json
import requests
import base64
import boto3
from botocore.exceptions import ClientError
from pathlib import Path
from moviepy.editor import VideoFileClip, TextClip, CompositeVideoClip, AudioFileClip, ImageClip, concatenate_audioclips
from dotenv import load_dotenv
import numpy as np
from moviepy.audio.AudioClip import AudioArrayClip
import logging
import sys
from datetime import datetime

# Fix PIL compatibility issue
from PIL import Image
if not hasattr(Image, 'ANTIALIAS'):
    Image.ANTIALIAS = Image.LANCZOS


class BatchVideoGenerator:
    def __init__(self):
        self.base_dir = Path(__file__).parent
        self.inputs_dir = self.base_dir / "inputs"
        self.outputs_dir = self.base_dir / "outputs"
        self.assets_dir = self.inputs_dir / "assets"
        self.temp_dir = self.base_dir / "temp"
        self.dialogues_dir = self.inputs_dir / "dialogues"
        
        # Ensure directories exist
        self.outputs_dir.mkdir(exist_ok=True)
        self.temp_dir.mkdir(exist_ok=True)
        
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(self.base_dir / 'batch_generator.log'),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger(__name__)
        
        # Load environment variables
        load_dotenv(self.base_dir / '.env')
        self.elevenlabs_api_key = os.getenv('ELEVENLABS_API_KEY')
        if not self.elevenlabs_api_key:
            raise ValueError("ELEVENLABS_API_KEY not found in .env file")
        
        # Initialize S3 client
        self.setup_s3()
        
        # Predefined character options
        self.PREDEFINED_CHARACTERS = {
            'character1': {
                'name': 'Alex',
                'voice_id': 'pNInz6obpgDQGcFmaJgB',  # Adam - Clear male voice
                'voice_description': 'Adam - Clear male voice',
                'caption_color': 'white',
                'caption_stroke_color': 'blue',
                'image_file': 'characters/alex_head.PNG'
            },
            'character2': {
                'name': 'Sam', 
                'voice_id': 'EXAVITQu4vr4xnSDxMaL',  # Bella - Female voice
                'voice_description': 'Bella - Female voice',
                'caption_color': 'white',
                'caption_stroke_color': 'hotpink', 
                'image_file': 'characters/sam_head.PNG'
            }
        }
        
        # Animation settings
        self.CHARACTER_IMAGE_SIZE = [300, 300]
        self.CHARACTER_POSITIONS = {
            'character1': [50, 1250],     # Left side with margin
            'character2': [730, 1250]     # Right side with margin
        }
        self.JIGGLE_INTENSITY = 5
        self.JIGGLE_FREQUENCY = 8
        
        # Video settings
        self.target_width = 1080
        self.target_height = 1920
        
    def setup_s3(self):
        """Initialize S3 client if credentials are available"""
        aws_access_key = os.getenv('AWS_ACCESS_KEY_ID')
        aws_secret_key = os.getenv('AWS_SECRET_ACCESS_KEY')
        self.s3_bucket = os.getenv('S3_BUCKET_NAME')
        self.s3_region = os.getenv('S3_REGION', 'us-east-1')
        
        self.s3_client = None
        if all([aws_access_key, aws_secret_key, self.s3_bucket]):
            self.s3_client = boto3.client(
                's3',
                aws_access_key_id=aws_access_key,
                aws_secret_access_key=aws_secret_key,
                region_name=self.s3_region
            )
            self.logger.info("🔗 S3 client initialized")
        else:
            self.logger.warning("⚠️  S3 credentials not found, videos will only be saved locally")
    
    def get_background_videos(self):
        """Get list of available background videos"""
        video_extensions = ['.mp4', '.avi', '.mov', '.mkv']
        backgrounds_dir = self.assets_dir / "backgrounds"
        background_videos = []
        
        for ext in video_extensions:
            background_videos.extend(backgrounds_dir.glob(f"*{ext}"))
        
        if not background_videos:
            raise FileNotFoundError(f"No background videos found in {backgrounds_dir}")
        
        return [str(bg) for bg in background_videos]
    
    def create_filename_from_title_description(self, title: str, description: str) -> str:
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
    
    def generate_character_voice(self, text: str, voice_id: str, character_name: str):
        """Generate AI voice with timestamps for a specific character"""
        
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps"
        
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "xi-api-key": self.elevenlabs_api_key
        }
        
        data = {
            "text": text,
            "model_id": "eleven_monolingual_v1",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75
            }
        }
        
        self.logger.info(f"  Generating {character_name} voice: '{text[:50]}...'")
        response = requests.post(url, json=data, headers=headers)
        
        if response.status_code != 200:
            raise Exception(f"ElevenLabs API error: {response.status_code} - {response.text}")
        
        result = response.json()
        
        # Save audio file with unique name
        audio_data = base64.b64decode(result['audio_base64'])
        
        voice_path = self.temp_dir / f"voice_{character_name}_{hash(text) % 10000}.mp3"
        with open(voice_path, 'wb') as f:
            f.write(audio_data)
        
        return str(voice_path), result['alignment']
    
    def words_from_alignment(self, alignment_data, original_text, time_offset=0):
        """Extract word-level timestamps from character-level alignment with time offset"""
        characters = alignment_data['characters']
        char_start_times = alignment_data['character_start_times_seconds']
        char_end_times = alignment_data['character_end_times_seconds']
        
        words = []
        current_word = ""
        word_start_time = None
        
        # Process each character in the alignment
        for i, char in enumerate(characters):
            if char in [' ', '\n', '\t', '.', '!', '?', ',', ';', ':']:
                # End of word
                if current_word and word_start_time is not None:
                    word_end_time = char_end_times[i-1] if i > 0 else char_start_times[i]
                    words.append({
                        'word': current_word,
                        'start': word_start_time + time_offset,
                        'end': word_end_time + time_offset
                    })
                    current_word = ""
                    word_start_time = None
            else:
                # Add character to current word
                if word_start_time is None:
                    word_start_time = char_start_times[i]
                current_word += char
        
        # Add final word if exists
        if current_word and word_start_time is not None:
            word_end_time = char_end_times[-1]
            words.append({
                'word': current_word,
                'start': word_start_time + time_offset,
                'end': word_end_time + time_offset
            })
        
        return words
    
    def upload_to_s3(self, file_path: str) -> str:
        """Upload video file to S3 bucket"""
        if not self.s3_client:
            return None
            
        try:
            file_name = Path(file_path).name
            s3_key = f"videos/{file_name}"
            
            self.logger.info(f"  📤 Uploading {file_name} to S3 bucket {self.s3_bucket}...")
            
            # Upload file
            self.s3_client.upload_file(
                file_path, 
                self.s3_bucket, 
                s3_key,
                ExtraArgs={'ContentType': 'video/mp4'}
            )
            
            # Generate S3 URL
            s3_url = f"https://{self.s3_bucket}.s3.{self.s3_region}.amazonaws.com/{s3_key}"
            self.logger.info(f"  ✅ Upload successful: {s3_url}")
            return s3_url
            
        except ClientError as e:
            self.logger.error(f"  ❌ Failed to upload to S3: {e}")
            return None
        except Exception as e:
            self.logger.error(f"  ❌ Unexpected error uploading to S3: {e}")
            return None
    
    def create_video_from_dialogue(self, dialogue_file: Path, background_video: str) -> str:
        """Create a video from a dialogue JSON file"""
        
        # Load dialogue
        with open(dialogue_file, 'r', encoding='utf-8') as f:
            dialogue_config = json.load(f)
        
        title = dialogue_config.get('title', dialogue_file.stem)
        description = dialogue_config.get('description', 'Generated video')
        dialogue = dialogue_config['dialogue']
        
        self.logger.info(f"🎬 Creating video: {title}")
        self.logger.info(f"  📝 Loaded {len(dialogue)} dialogue lines")
        
        # Generate voices for each dialogue line
        audio_clips = []
        all_words_with_timing = []
        current_time_offset = 0
        
        for i, line in enumerate(dialogue):
            character_id = line['character']
            text = line['text']
            character_info = self.PREDEFINED_CHARACTERS[character_id]
            
            # Generate voice with alignment
            voice_path, alignment_data = self.generate_character_voice(
                text, 
                character_info['voice_id'],
                character_info['name']
            )
            
            # Load audio clip to get duration
            audio_clip = AudioFileClip(voice_path)
            audio_duration = audio_clip.duration
            
            # Extract words with timing (adjusted for sequence)
            words = self.words_from_alignment(alignment_data, text, current_time_offset)
            
            # Add character info to each word
            for word in words:
                word['character'] = character_id
                word['character_info'] = character_info
            
            all_words_with_timing.extend(words)
            audio_clips.append(audio_clip)
            
            self.logger.info(f"    Line {i+1}: {character_info['name']} - {len(words)} words, {audio_duration:.1f}s")
            current_time_offset += audio_duration + 0.3  # Add small pause between lines
        
        # Combine all audio clips with pauses
        if len(audio_clips) > 1:
            silence_duration = 0.3
            sample_rate = 44100
            silence_array = np.zeros((int(silence_duration * sample_rate), 2))
            silence_clip = AudioArrayClip(silence_array, fps=sample_rate)
            
            combined_audio_clips = []
            for i, clip in enumerate(audio_clips):
                combined_audio_clips.append(clip)
                if i < len(audio_clips) - 1:  # Don't add silence after last clip
                    combined_audio_clips.append(silence_clip)
            final_audio = concatenate_audioclips(combined_audio_clips)
        else:
            final_audio = audio_clips[0] if audio_clips else None
            silence_clip = None
        
        if not final_audio:
            self.logger.error("❌ No audio generated!")
            return None
        
        total_duration = final_audio.duration
        self.logger.info(f"  🎵 Total duration: {total_duration:.1f} seconds")
        
        # Process background video
        background_clip = VideoFileClip(background_video)
        
        if background_clip.duration < total_duration:
            loop_count = int(total_duration / background_clip.duration) + 1
            background_clip = background_clip.loop(n=loop_count)
        
        background_clip = background_clip.subclip(0, total_duration)
        
        # Resize to vertical format
        bg_aspect = background_clip.w / background_clip.h
        target_aspect = self.target_width / self.target_height
        
        if bg_aspect > target_aspect:
            new_width = int(background_clip.h * target_aspect)
            background_clip = background_clip.crop(
                x_center=background_clip.w/2,
                width=new_width
            )
        else:
            new_height = int(background_clip.w / target_aspect)
            background_clip = background_clip.crop(
                y_center=background_clip.h/2,
                height=new_height
            )
        
        background_clip = background_clip.resize((self.target_width, self.target_height))
        background_clip = background_clip.set_audio(final_audio)
        
        # Create caption clips
        caption_clips = []
        character_image_clips = []
        
        for word_data in all_words_with_timing:
            word = word_data['word']
            start_time = word_data['start']
            end_time = word_data['end']
            duration = end_time - start_time
            char_info = word_data['character_info']
            
            # Create caption with character-specific colors
            txt_clip = TextClip(
                word,
                fontsize=100,
                font='Impact',
                color=char_info['caption_color'],
                stroke_color=char_info['caption_stroke_color'],
                stroke_width=6,
                method='caption',
                size=(self.target_width * 0.9, None)
            ).set_position(('center', 'center')).set_duration(
                duration
            ).set_start(start_time)
            
            caption_clips.append(txt_clip)
        
        # Create character image clips
        current_speaker = None
        speaker_start_time = 0
        
        for i, word_data in enumerate(all_words_with_timing):
            character_id = word_data['character']
            
            if current_speaker != character_id:
                # End previous speaker's image
                if current_speaker is not None:
                    speaker_end_time = word_data['start']
                    char_info = self.PREDEFINED_CHARACTERS[current_speaker]
                    image_path = self.assets_dir / char_info['image_file']
                    
                    if image_path.exists():
                        char_img = ImageClip(str(image_path))
                        char_img = char_img.resize(self.CHARACTER_IMAGE_SIZE)
                        
                        # Capture position immediately for proper scoping
                        char_position = self.CHARACTER_POSITIONS[current_speaker]
                        
                        # Add jiggle animation with captured position
                        def make_jiggle_position(base_pos):
                            def jiggle_position(t):
                                import math
                                jiggle_x = self.JIGGLE_INTENSITY * math.sin(t * self.JIGGLE_FREQUENCY)
                                jiggle_y = self.JIGGLE_INTENSITY * math.cos(t * self.JIGGLE_FREQUENCY * 1.2)
                                return (base_pos[0] + jiggle_x, base_pos[1] + jiggle_y)
                            return jiggle_position
                        
                        char_img = char_img.set_position(make_jiggle_position(char_position)).set_duration(
                            speaker_end_time - speaker_start_time
                        ).set_start(speaker_start_time)
                        
                        character_image_clips.append(char_img)
                
                # Start new speaker
                current_speaker = character_id
                speaker_start_time = word_data['start']
            
            # Handle last speaker
            if i == len(all_words_with_timing) - 1:
                speaker_end_time = word_data['end']
                char_info = self.PREDEFINED_CHARACTERS[current_speaker]
                image_path = self.assets_dir / char_info['image_file']
                
                if image_path.exists():
                    char_img = ImageClip(str(image_path))
                    char_img = char_img.resize(self.CHARACTER_IMAGE_SIZE)
                    
                    # Capture position immediately for proper scoping
                    char_position = self.CHARACTER_POSITIONS[current_speaker]
                    
                    # Add jiggle animation with captured position
                    def make_jiggle_position_final(base_pos):
                        def jiggle_position_final(t):
                            import math
                            jiggle_x = self.JIGGLE_INTENSITY * math.sin(t * self.JIGGLE_FREQUENCY)
                            jiggle_y = self.JIGGLE_INTENSITY * math.cos(t * self.JIGGLE_FREQUENCY * 1.2)
                            return (base_pos[0] + jiggle_x, base_pos[1] + jiggle_y)
                        return jiggle_position_final
                    
                    char_img = char_img.set_position(make_jiggle_position_final(char_position)).set_duration(
                        speaker_end_time - speaker_start_time
                    ).set_start(speaker_start_time)
                    
                    character_image_clips.append(char_img)
        
        # Composite final video
        all_clips = [background_clip] + character_image_clips + caption_clips
        final_clip = CompositeVideoClip(all_clips)
        
        # Output path using title and description
        filename = self.create_filename_from_title_description(title, description)
        output_path = self.outputs_dir / f"{filename}.mp4"
        self.logger.info(f"  💾 Creating video: {output_path}")
        
        # Write video
        final_clip.write_videofile(
            str(output_path),
            fps=24,
            codec='libx264',
            audio_codec='aac',
            temp_audiofile=str(self.temp_dir / "temp_audio.m4a"),
            remove_temp=True,
            verbose=False,
            logger=None
        )
        
        # Clean up clips
        background_clip.close()
        final_clip.close()
        final_audio.close()
        if silence_clip:
            silence_clip.close()
        for clip in audio_clips:
            clip.close()
        for clip in caption_clips:
            clip.close()
        for clip in character_image_clips:
            clip.close()
        
        self.logger.info(f"  ✅ Video created: {output_path}")
        
        # Upload to S3 if configured
        if self.s3_client:
            s3_url = self.upload_to_s3(str(output_path))
            if s3_url:
                self.logger.info(f"  🌐 Video available at: {s3_url}")
                return s3_url
        
        return str(output_path)
    
    def cleanup_temp_files(self):
        """Clean up temporary files"""
        for temp_file in self.temp_dir.glob("*"):
            if temp_file.is_file():
                temp_file.unlink()
        self.logger.info("🧹 Temporary files cleaned up")
    
    def run(self):
        """Main execution - process all dialogue JSON files"""
        try:
            self.logger.info("🚀 Starting Batch Video Generator...")
            
            # Get all dialogue JSON files
            json_files = list(self.dialogues_dir.glob("*.json"))
            if not json_files:
                self.logger.error("❌ No dialogue JSON files found in inputs/dialogues/")
                return
            
            # Get all background videos
            background_videos = self.get_background_videos()
            self.logger.info(f"📂 Found {len(json_files)} dialogue files and {len(background_videos)} background videos")
            
            # Process each dialogue file
            created_videos = []
            for i, dialogue_file in enumerate(json_files):
                # Cycle through background videos
                background_video = background_videos[i % len(background_videos)]
                bg_name = Path(background_video).name
                
                self.logger.info(f"📹 Processing {dialogue_file.name} with background {bg_name}")
                
                try:
                    video_path = self.create_video_from_dialogue(dialogue_file, background_video)
                    if video_path:
                        created_videos.append(video_path)
                    
                    # Clean up temp files after each video
                    self.cleanup_temp_files()
                    
                except Exception as e:
                    self.logger.error(f"❌ Error processing {dialogue_file.name}: {e}")
                    continue
            
            self.logger.info(f"✅ Batch processing complete! Created {len(created_videos)} videos")
            for video in created_videos:
                self.logger.info(f"   📽️  {video}")
            
        except Exception as e:
            self.logger.error(f"❌ Batch generation failed: {e}")
            import traceback
            self.logger.error(traceback.format_exc())


if __name__ == "__main__":
    generator = BatchVideoGenerator()
    generator.run()