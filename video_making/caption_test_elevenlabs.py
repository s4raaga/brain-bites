#!/usr/bin/env python3
"""
Caption Test with ElevenLabs Alignment - One word every ~0.1 seconds.
Generates voice plus alignment timestamps to build word-level captions (no external transcription service).
"""

import os
import json
import requests
from pathlib import Path
from moviepy.editor import VideoFileClip, TextClip, CompositeVideoClip, AudioFileClip

# Fix PIL compatibility issue
from PIL import Image
if not hasattr(Image, 'ANTIALIAS'):
    Image.ANTIALIAS = Image.LANCZOS


def generate_voice_with_alignment(text: str, elevenlabs_api_key: str, voice_id: str = "21m00Tcm4TlvDq8ikWAM"):
    """Generate AI voice with character-level timestamps using ElevenLabs"""
    
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
    
    print("Generating AI voice with timestamps...")
    response = requests.post(url, json=data, headers=headers)
    
    if response.status_code != 200:
        raise Exception(f"ElevenLabs API error: {response.status_code} - {response.text}")
    
    result = response.json()
    
    # Save audio file
    import base64
    audio_data = base64.b64decode(result['audio_base64'])
    
    base_dir = Path(__file__).parent
    temp_dir = base_dir / "temp"
    temp_dir.mkdir(exist_ok=True)
    
    voice_path = temp_dir / "voice_with_alignment.mp3"
    with open(voice_path, 'wb') as f:
        f.write(audio_data)
    
    print(f"Voice generated with alignment: {voice_path}")
    
    # Return both audio path and alignment data
    return str(voice_path), result['alignment']


def words_from_alignment(alignment_data, original_text):
    """Extract word-level timestamps from character-level alignment"""
    characters = alignment_data['characters']
    char_start_times = alignment_data['character_start_times_seconds']
    char_end_times = alignment_data['character_end_times_seconds']
    
    print(f"Total characters in alignment: {len(characters)}")
    print(f"First 10 characters: {characters[:10]}")
    print(f"Original text length: {len(original_text)}")
    
    words = []
    current_word = ""
    word_start_time = None
    char_index = 0
    
    # Process each character in the alignment
    for i, char in enumerate(characters):
        if char in [' ', '\n', '\t', '.', '!', '?', ',', ';', ':']:
            # End of word
            if current_word and word_start_time is not None:
                word_end_time = char_end_times[i-1] if i > 0 else char_start_times[i]
                words.append({
                    'word': current_word,
                    'start': word_start_time,
                    'end': word_end_time
                })
                print(f"Word found: '{current_word}' ({word_start_time:.3f}s - {word_end_time:.3f}s)")
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
            'start': word_start_time,
            'end': word_end_time
        })
        print(f"Final word: '{current_word}' ({word_start_time:.3f}s - {word_end_time:.3f}s)")
    
    return words


def create_caption_test_with_elevenlabs():
    base_dir = Path(__file__).parent
    inputs_dir = base_dir / "inputs"
    outputs_dir = base_dir / "outputs"
    backgrounds_dir = inputs_dir / "backgrounds"
    
    # Read script
    script_path = inputs_dir / "script.txt"
    with open(script_path, 'r', encoding='utf-8') as f:
        script_text = f.read().strip()
    
    # Load config
    config_path = base_dir / "config.json"
    with open(config_path, 'r') as f:
        config = json.load(f)
    
    # Load environment variables
    from dotenv import load_dotenv
    load_dotenv(base_dir / '.env')
    
    elevenlabs_api_key = os.getenv('ELEVENLABS_API_KEY')
    if not elevenlabs_api_key:
        raise ValueError("ELEVENLABS_API_KEY not found in .env file")
    
    print(f"Script text: {script_text}")
    print(f"Script has {len(script_text.split())} words")
    
    # Generate voice with alignment using Rachel voice (common ElevenLabs voice)
    # Rachel voice ID: 21m00Tcm4TlvDq8ikWAM
    # You can change this to Miranda's ID if you have it
    miranda_voice_id = "21m00Tcm4TlvDq8ikWAM"  # This is Rachel, change to Miranda's ID if available
    voice_path, alignment_data = generate_voice_with_alignment(
        script_text, 
        elevenlabs_api_key, 
        miranda_voice_id
    )
    
    # Extract word-level timestamps
    words_with_timing = words_from_alignment(alignment_data, script_text)
    
    print(f"Extracted {len(words_with_timing)} words with timing")
    for i, word_data in enumerate(words_with_timing[:5]):  # Show first 5
        print(f"  {i+1}: '{word_data['word']}' ({word_data['start']:.3f}s - {word_data['end']:.3f}s)")
    
    # Find background video
    background_video = None
    for video_file in backgrounds_dir.glob("*.MP4"):
        background_video = str(video_file)
        break
    
    if not background_video:
        print("No background video found!")
        return
    
    print(f"Using background video: {background_video}")
    
    # Load background video and audio
    background_clip = VideoFileClip(background_video)
    audio_clip = AudioFileClip(voice_path)
    audio_duration = audio_clip.duration
    
    print(f"Audio duration: {audio_duration:.1f} seconds")
    
    # Prepare background video
    if background_clip.duration < audio_duration:
        loop_count = int(audio_duration / background_clip.duration) + 1
        background_clip = background_clip.loop(n=loop_count)
    
    background_clip = background_clip.subclip(0, audio_duration)
    
    # Resize to vertical format
    target_width = config['video_width']
    target_height = config['video_height']
    
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
    
    # Add generated audio to background
    background_clip = background_clip.set_audio(audio_clip)
    
    # Create caption clips using ElevenLabs timing
    caption_clips = []
    for i, word_data in enumerate(words_with_timing):
        word = word_data['word']
        start_time = word_data['start']
        end_time = word_data['end']
        duration = end_time - start_time
        
        txt_clip = TextClip(
            word,
            fontsize=100,
            font='Impact',
            color=config['caption_color'],
            stroke_color=config['caption_stroke_color'],
            stroke_width=6,
            method='caption',
            size=(target_width * 0.9, None)
        ).set_position(('center', 'center')).set_duration(
            duration
        ).set_start(start_time)
        
        caption_clips.append(txt_clip)
        
        if i % 10 == 0:
            print(f"Created caption {i+1}/{len(words_with_timing)}: '{word}' at {start_time:.3f}s")
    
    # Composite final video
    final_clip = CompositeVideoClip([background_clip] + caption_clips)
    
    # Output path
    output_path = outputs_dir / "caption_test_elevenlabs.mp4"
    print(f"Creating video: {output_path}")
    
    # Write video with proper audio settings
    final_clip.write_videofile(
        str(output_path),
        fps=24,
        codec='libx264',
        audio_codec='aac',
        temp_audiofile=str(base_dir / "temp" / "temp_audio.m4a"),
        remove_temp=True,
        verbose=False,
        logger=None
    )
    
    # Clean up
    background_clip.close()
    final_clip.close()
    audio_clip.close()
    for clip in caption_clips:
        clip.close()
    
    print(f"✅ Caption test video created with ElevenLabs alignment: {output_path}")
    return str(output_path)


if __name__ == "__main__":
    create_caption_test_with_elevenlabs()