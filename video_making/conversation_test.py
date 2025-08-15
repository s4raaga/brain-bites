#!/usr/bin/env python3
"""
Two-Character Conversation Test
Creates a video with two characters having a conversation, with:
- Different colored caption outlines (blue vs pink)
- Character images that appear when speaking
- ElevenLabs voice synthesis for each character
"""

import os
import json
import requests
import base64
from pathlib import Path
from moviepy.editor import VideoFileClip, TextClip, CompositeVideoClip, AudioFileClip, ImageClip, concatenate_audioclips
from dotenv import load_dotenv

# Fix PIL compatibility issue
from PIL import Image
if not hasattr(Image, 'ANTIALIAS'):
    Image.ANTIALIAS = Image.LANCZOS


def generate_character_voice(text: str, voice_id: str, elevenlabs_api_key: str, character_name: str):
    """Generate AI voice with timestamps for a specific character"""
    
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps"
    
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "xi-api-key": elevenlabs_api_key
    }
    
    data = {
        "text": text,
        "model_id": "eleven_monolingual_v1",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75
        }
    }
    
    print(f"Generating {character_name} voice: '{text[:50]}...'")
    response = requests.post(url, json=data, headers=headers)
    
    if response.status_code != 200:
        raise Exception(f"ElevenLabs API error: {response.status_code} - {response.text}")
    
    result = response.json()
    
    # Save audio file
    audio_data = base64.b64decode(result['audio_base64'])
    
    base_dir = Path(__file__).parent
    temp_dir = base_dir / "temp"
    temp_dir.mkdir(exist_ok=True)
    
    voice_path = temp_dir / f"voice_{character_name}.mp3"
    with open(voice_path, 'wb') as f:
        f.write(audio_data)
    
    print(f"✓ {character_name} voice generated: {voice_path}")
    
    return str(voice_path), result['alignment']


def words_from_alignment(alignment_data, original_text, time_offset=0):
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


def create_conversation_video():
    base_dir = Path(__file__).parent
    inputs_dir = base_dir / "inputs"
    outputs_dir = base_dir / "outputs"
    assets_dir = inputs_dir / "assets"
    backgrounds_dir = inputs_dir / "backgrounds"
    temp_dir = base_dir / "temp"
    temp_dir.mkdir(exist_ok=True)
    
    # Load environment variables
    load_dotenv(base_dir / '.env')
    elevenlabs_api_key = os.getenv('ELEVENLABS_API_KEY')
    if not elevenlabs_api_key:
        raise ValueError("ELEVENLABS_API_KEY not found in .env file")
    
    # Load dialogue configuration
    dialogue_path = inputs_dir / "dialogue.json"
    with open(dialogue_path, 'r', encoding='utf-8') as f:
        dialogue_config = json.load(f)
    
    characters = dialogue_config['characters']
    dialogue = dialogue_config['dialogue']
    
    # Hardcoded positioning and animation settings
    CHARACTER_IMAGE_SIZE = [300, 300]
    CHARACTER_POSITIONS = {
        'character1': [50, 1250],     # Left side with margin
        'character2': [730, 1250]     # Right side with margin
    }
    JIGGLE_INTENSITY = 5
    JIGGLE_FREQUENCY = 8
    
    print(f"Loaded {len(dialogue)} dialogue lines between {len(characters)} characters")
    
    # Check if character images exist
    for char_id, char_info in characters.items():
        image_path = assets_dir / char_info['image_file']
        if not image_path.exists():
            print(f"⚠️  Character image not found: {image_path}")
            print(f"   Please add {char_info['image_file']} to the assets folder before running")
            return None
    
    # Generate voices for each dialogue line
    audio_clips = []
    all_words_with_timing = []
    current_time_offset = 0
    
    for i, line in enumerate(dialogue):
        character_id = line['character']
        text = line['text']
        character_info = characters[character_id]
        
        # Generate voice with alignment
        voice_path, alignment_data = generate_character_voice(
            text, 
            character_info['voice_id'],
            elevenlabs_api_key,
            character_info['name']
        )
        
        # Load audio clip to get duration
        audio_clip = AudioFileClip(voice_path)
        audio_duration = audio_clip.duration
        
        # Extract words with timing (adjusted for sequence)
        words = words_from_alignment(alignment_data, text, current_time_offset)
        
        # Add character info to each word
        for word in words:
            word['character'] = character_id
            word['character_info'] = character_info
        
        all_words_with_timing.extend(words)
        audio_clips.append(audio_clip)
        
        print(f"  Line {i+1}: {character_info['name']} - {len(words)} words, {audio_duration:.1f}s")
        current_time_offset += audio_duration + 0.3  # Add small pause between lines
    
    # Combine all audio clips with pauses
    if len(audio_clips) > 1:
        # Create a simple silence clip using numpy
        import numpy as np
        from moviepy.audio.AudioClip import AudioArrayClip
        
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
        print("No audio generated!")
        return None
    
    total_duration = final_audio.duration
    print(f"\nTotal conversation duration: {total_duration:.1f} seconds")
    print(f"Total words: {len(all_words_with_timing)}")
    
    # Find background video
    background_video = None
    for video_file in backgrounds_dir.glob("*.MP4"):
        background_video = str(video_file)
        break
    
    if not background_video:
        print("No background video found!")
        return None
    
    # Load and prepare background video
    background_clip = VideoFileClip(background_video)
    
    if background_clip.duration < total_duration:
        loop_count = int(total_duration / background_clip.duration) + 1
        background_clip = background_clip.loop(n=loop_count)
    
    background_clip = background_clip.subclip(0, total_duration)
    
    # Resize to vertical format
    target_width = 1080
    target_height = 1920
    
    bg_aspect = background_clip.w / background_clip.h
    target_aspect = target_width / target_height
    
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
    
    background_clip = background_clip.resize((target_width, target_height))
    background_clip = background_clip.set_audio(final_audio)
    
    # Create caption clips with character-specific colors
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
            size=(target_width * 0.9, None)
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
                char_info = characters[current_speaker]
                image_path = assets_dir / char_info['image_file']
                
                if image_path.exists():
                    char_img = ImageClip(str(image_path))
                    char_img = char_img.resize(CHARACTER_IMAGE_SIZE)
                    
                    # Use EXACT same positioning as working tests - capture position in local variable
                    char_position = CHARACTER_POSITIONS[current_speaker]  # Capture position immediately
                    
                    # Add jiggle animation with captured position
                    def make_jiggle_position(base_pos):  # Closure to capture position
                        def jiggle_position(t):
                            import math
                            jiggle_x = JIGGLE_INTENSITY * math.sin(t * JIGGLE_FREQUENCY)
                            jiggle_y = JIGGLE_INTENSITY * math.cos(t * JIGGLE_FREQUENCY * 1.2)
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
            char_info = characters[current_speaker]
            image_path = assets_dir / char_info['image_file']
            
            if image_path.exists():
                char_img = ImageClip(str(image_path))
                char_img = char_img.resize(CHARACTER_IMAGE_SIZE)
                
                # Use EXACT same positioning as working tests - capture position in local variable
                char_position = CHARACTER_POSITIONS[current_speaker]  # Capture position immediately
                
                # Add jiggle animation with captured position
                def make_jiggle_position_final(base_pos):  # Closure to capture position
                    def jiggle_position_final(t):
                        import math
                        jiggle_x = JIGGLE_INTENSITY * math.sin(t * JIGGLE_FREQUENCY)
                        jiggle_y = JIGGLE_INTENSITY * math.cos(t * JIGGLE_FREQUENCY * 1.2)
                        return (base_pos[0] + jiggle_x, base_pos[1] + jiggle_y)
                    return jiggle_position_final
                
                char_img = char_img.set_position(make_jiggle_position_final(char_position)).set_duration(
                    speaker_end_time - speaker_start_time
                ).set_start(speaker_start_time)
                
                character_image_clips.append(char_img)
    
    print(f"Created {len(caption_clips)} caption clips")
    print(f"Created {len(character_image_clips)} character image clips")
    
    # Composite final video
    all_clips = [background_clip] + character_image_clips + caption_clips
    final_clip = CompositeVideoClip(all_clips)
    
    # Output path
    output_path = outputs_dir / "conversation_test.mp4"
    print(f"\nCreating conversation video: {output_path}")
    
    # Write video
    final_clip.write_videofile(
        str(output_path),
        fps=24,
        codec='libx264',
        audio_codec='aac',
        temp_audiofile=str(temp_dir / "temp_audio.m4a"),
        remove_temp=True,
        verbose=False,
        logger=None
    )
    
    # Clean up
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
    
    print(f"\n✅ Conversation video created: {output_path}")
    return str(output_path)


if __name__ == "__main__":
    create_conversation_video()