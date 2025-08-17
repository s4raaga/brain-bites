"""Reusable Brain Bites video generator package (two-speaker version).

Supports exactly two roles labelled 'Speaker A' and 'Speaker B'. On each generation
the two logical speaker labels can be randomly (or deterministically) mapped to
the two character definitions (voice, image, caption colors, volume).

Transcripts accepted schema (flexible):
{
  "title": str,
  "description": str,
  "dialogue": [ {"speaker"|"character": "Speaker A"|"Speaker B"|"Speaker C", "text": str}, ... ]
}

Public usage:
    from video_making.generator import BrainBitesVideoGenerator
    gen = BrainBitesVideoGenerator(randomize_roles=True)
    out_path = gen.generate_from_file(Path('transcripts/my_script.json'))

Environment variables respected:
 - ELEVENLABS_API_KEY (required)
 - AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / S3_BUCKET_NAME (optional)

Lightweight dependencies: moviepy, requests, boto3, numpy, python-dotenv.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import sys
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

import boto3
import numpy as np
import requests
from botocore.exceptions import ClientError
from dotenv import load_dotenv
from moviepy.audio.AudioClip import AudioArrayClip
from moviepy.editor import (
    AudioFileClip,
    CompositeVideoClip,
    ImageClip,
    TextClip,
    VideoFileClip,
    concatenate_audioclips,
)
from PIL import Image  # noqa: E402

if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.LANCZOS  # type: ignore[attr-defined]


@dataclass
class TranscriptLine:
    speaker: str
    text: str


class BrainBitesVideoGenerator:
    # Default character pool (extend with characters.json). volume=multiplier.
    DEFAULT_CHARACTER_POOL = [
        {
            'key': 'zain',
            'name': 'Zain',
            'voice_id': 'xsYLmpc5eDTXRiJZcK0f',
            'voice_description': 'Zain - Custom voice',
            'caption_color': 'white',
            'caption_stroke_color': 'green',
            'image_file': 'characters/zain.png',
            'volume': 2.0,
        },
        {
            'key': 'sara',
            'name': 'Sara',
            'voice_id': 'EXAVITQu4vr4xnSDxMaL',
            'voice_description': 'Bella - Female voice',
            'caption_color': 'white',
            'caption_stroke_color': 'hotpink',
            'image_file': 'characters/sara.png',
            'volume': 1.0,
        },
        {
            'key': 'nikita',
            'name': 'Nikita',
            'voice_id': '2nzji8yPQooBwG4eQO4s',
            'voice_description': 'Alex - Male voice',
            'caption_color': 'white',
            'caption_stroke_color': 'blue',
            'image_file': 'characters/nikita.png',
            'volume': 1.0,
        },
        {
            'key': 'mike',
            'name': 'Mike',
            'voice_id': 'pNInz6obpgDQGcFmaJgB',
            'voice_description': 'Mike - Male voice',
            'caption_color': 'white',
            'caption_stroke_color': 'black',
            'image_file': 'characters/mike.png',
            'volume': 1.0,
        },
        {
            'key': 'daniel',
            'name': 'Daniel',
            'voice_id': 'oaGwHLz3csUaSnc2NBD4',
            'voice_description': 'Alex - Male voice',
            'caption_color': 'white',
            'caption_stroke_color': 'black',
            'image_file': 'characters/daniel.png',
            'volume': 1.0,
        },
        {
            'key': 'nerd',
            'name': 'Nerd',
            'voice_id': 'Rmv8zCb2IRE895dK1qWB',
            'voice_description': 'Nerd - Male voice',
            'caption_color': 'white',
            'caption_stroke_color': 'yellow',
            'image_file': 'characters/nerd.png',
            'volume': 1.0,
        },
    ]

    CHARACTER_IMAGE_SIZE = [400, 400]
    CHARACTER_POSITIONS_TEMPLATE = [  # Only two speaker layout (bottom left/right)
        [50, 1250],
        [630, 1250],
    ]
    JIGGLE_INTENSITY = 5
    JIGGLE_FREQUENCY = 8
    TARGET_W = 1080
    TARGET_H = 1920

    def __init__(
        self,
        project_root: Optional[Path] = None,
        backgrounds_dir: Optional[Path] = None,
        outputs_dir: Optional[Path] = None,
        temp_dir: Optional[Path] = None,
        assets_dir: Optional[Path] = None,
        enable_s3: bool = True,
        logger: Optional[logging.Logger] = None,
        randomize_roles: bool = True,
        random_seed: Optional[int] = None,
    ) -> None:
        self.project_root = (
            project_root if project_root is not None else Path(__file__).resolve().parents[1]
        )
        base_video_dir = self.project_root / "video_making"
        self.backgrounds_dir = (
            backgrounds_dir
            if backgrounds_dir is not None
            else base_video_dir / "inputs" / "assets" / "backgrounds"
        )
        self.outputs_dir = outputs_dir if outputs_dir is not None else base_video_dir / "outputs"
        self.temp_dir = temp_dir if temp_dir is not None else base_video_dir / "temp"
        self.assets_dir = assets_dir if assets_dir is not None else base_video_dir / "inputs" / "assets"

        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        load_dotenv(self.project_root / "video_making" / ".env")
        self.eleven_key = os.getenv("ELEVENLABS_API_KEY")
        if not self.eleven_key:
            raise ValueError("ELEVENLABS_API_KEY environment variable not set")

        self.logger = logger or self._configure_logger()
        self.enable_s3 = enable_s3
        self._init_s3_if_available()

        self.randomize_roles = randomize_roles
        if random_seed is not None:
            random.seed(random_seed)

        self.character_pool = self._load_character_pool()
        self.role_characters: dict[str, dict] = {}
        self.CHARACTER_POSITIONS: dict[str, list[int]] = {}

    def _configure_logger(self) -> logging.Logger:
        logger = logging.getLogger("brain_bites.video")
        if not logger.handlers:
            logger.setLevel(logging.INFO)
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
            logger.addHandler(handler)
        return logger

    def _init_s3_if_available(self) -> None:
        self.s3_client = None
        if not self.enable_s3:
            return
        aws_id = os.getenv("AWS_ACCESS_KEY_ID")
        aws_secret = os.getenv("AWS_SECRET_ACCESS_KEY")
        self.s3_bucket = os.getenv("S3_BUCKET_NAME")
        self.s3_region = os.getenv("S3_REGION", "us-east-1")
        if aws_id and aws_secret and self.s3_bucket:
            try:
                self.s3_client = boto3.client(
                    "s3",
                    aws_access_key_id=aws_id,
                    aws_secret_access_key=aws_secret,
                    region_name=self.s3_region,
                )
                self.logger.info("S3 client initialized")
            except Exception as e:  # noqa: BLE001
                self.logger.warning(f"Failed to init S3 client: {e}")
        else:
            self.logger.info("S3 disabled or credentials missing; skipping upload.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def generate_from_file(
        self,
        transcript_path: Path,
        background_video: Optional[Path] = None,
        return_meta: bool = False,
    ) -> Path | dict:
        with open(transcript_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return self.generate(
            data,
            source_file=transcript_path,
            background_video=background_video,
            return_meta=return_meta,
        )

    def generate(
        self,
        transcript: dict,
        source_file: Optional[Path] = None,
        background_video: Optional[Path] = None,
        return_meta: bool = False,
    ) -> Path | dict:
        title = transcript.get("title") or (source_file.stem if source_file else "video")
        description = transcript.get("description") or "Generated video"
        raw_dialogue = transcript.get("dialogue") or []
        lines: List[TranscriptLine] = []
        roles_used: set[str] = set()
        for entry in raw_dialogue:
            if not isinstance(entry, dict):
                continue
            speaker = entry.get("speaker") or entry.get("character")
            text = entry.get("text")
            if isinstance(speaker, str) and isinstance(text, str):
                if not re.match(r"^Speaker [A-Z]$", speaker):
                    raise ValueError(
                        f"Unsupported speaker label: {speaker}. Use 'Speaker A', 'Speaker B', ..."
                    )
                roles_used.add(speaker)
                lines.append(TranscriptLine(speaker=speaker, text=text))
        if not lines:
            raise ValueError("Transcript contains no valid dialogue lines")

        # Validate only two speakers max
        if len(roles_used) > 2:
            raise ValueError(f"Found speakers {sorted(roles_used)} but only two speakers are supported.")
        self._assign_roles(sorted(roles_used))

        if background_video is None:
            background_video = self._pick_background_video()
        self.logger.info(f"Creating video: {title} ({len(lines)} lines) -> {background_video}")

        audio_clips: List[AudioFileClip] = []
        all_words: list[dict] = []
        current_offset = 0.0
        for idx, line in enumerate(lines):
            voice_path, alignment = self._generate_voice(line.text, line.speaker)
            audio_clip = AudioFileClip(voice_path)
            vol = self.role_characters[line.speaker].get('volume', 1.0)
            if vol != 1.0:
                try:
                    audio_clip = audio_clip.volumex(vol)
                except Exception:  # noqa: BLE001
                    self.logger.warning(f"Failed to apply volume {vol} for {line.speaker}")
            words = self._words_from_alignment(alignment, current_offset, line.speaker)
            audio_clips.append(audio_clip)
            all_words.extend(words)
            current_offset += audio_clip.duration + 0.3
            self.logger.info(
                f"  Line {idx+1}: {line.speaker} {len(words)} words {audio_clip.duration:.1f}s"
            )

        final_audio, silence_clip = self._concat_with_silences(audio_clips)
        total_duration = final_audio.duration
        background_clip = self._prepare_background(background_video, total_duration)
        background_clip = background_clip.set_audio(final_audio)

        caption_clips = self._build_word_captions(all_words)
        character_image_clips = self._build_character_image_sequence(all_words)

        final = CompositeVideoClip([background_clip] + character_image_clips + caption_clips)
        filename = self._make_filename(title, description)
        out_path = self.outputs_dir / f"{filename}.mp4"
        self.logger.info(f"Writing video -> {out_path}")
        final.write_videofile(
            str(out_path),
            fps=24,
            codec="libx264",
            audio_codec="aac",
            temp_audiofile=str(self.temp_dir / "temp_audio.m4a"),
            remove_temp=True,
            verbose=False,
            logger=None,
        )

        background_clip.close()
        final.close()
        final_audio.close()
        if silence_clip:
            silence_clip.close()
        for c in audio_clips + caption_clips + character_image_clips:
            c.close()
        self._cleanup_temp()

        uploaded_url: Optional[str] = None
        if self.s3_client:
            try:
                uploaded_url = self._upload_to_s3(out_path)
                if uploaded_url:
                    self.logger.info(f"Uploaded to S3: {uploaded_url}")
            except Exception as e:  # noqa: BLE001
                self.logger.warning(f"S3 upload failed: {e}")

        if return_meta:
            return {
                "local_path": out_path,
                "uploaded_url": uploaded_url,
                "title": title,
                "description": description,
            }
        return out_path

    def _pick_background_video(self) -> Path:
        if not self.backgrounds_dir.exists():
            raise FileNotFoundError(f"Backgrounds dir not found: {self.backgrounds_dir}")
        videos = [p for p in self.backgrounds_dir.iterdir() if p.suffix.lower() in {'.mp4', '.mov', '.mkv', '.avi'}]
        if not videos:
            raise FileNotFoundError(f"No background videos in {self.backgrounds_dir}")
        videos.sort()
        return videos[hash(os.times()) % len(videos)]

    def _make_filename(self, title: str, description: str) -> str:
        combined = f"{title}_{description}"
        safe = re.sub(r"[^\w\s-]", "", combined)
        safe = re.sub(r"\s+", "-", safe)
        safe = re.sub(r"-+", "-", safe).strip("-").lower()[:100]
        return safe or "video"

    def _generate_voice(self, text: str, speaker: str):
        char = self.role_characters[speaker]
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{char['voice_id']}/with-timestamps"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "xi-api-key": self.eleven_key,
        }
        # Added speed control (requested). ElevenLabs supports a 'speed' field at top-level.
        data = {
            "text": text,
            "model_id": "eleven_monolingual_v1",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
            "speed": 1.2,
        }
        r = requests.post(url, json=data, headers=headers, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"ElevenLabs error {r.status_code}: {r.text[:200]}")
        payload = r.json()
        audio_bytes = base64.b64decode(payload["audio_base64"])  # noqa: S113
        voice_path = self.temp_dir / f"voice_{speaker}_{abs(hash(text)) % 10000}.mp3"
        with open(voice_path, "wb") as f:
            f.write(audio_bytes)
        return str(voice_path), payload["alignment"]

    def _words_from_alignment(self, alignment: dict, time_offset: float, speaker: str):
        chars = alignment["characters"]
        starts = alignment["character_start_times_seconds"]
        ends = alignment["character_end_times_seconds"]
        words = []
        cur = ""
        word_start: Optional[float] = None
        for i, ch in enumerate(chars):
            if ch in {" ", "\n", "\t", ".", "!", "?", ",", ";", ":"}:
                if cur and word_start is not None:
                    words.append({"word": cur, "start": word_start + time_offset, "end": (ends[i-1] if i>0 else starts[i]) + time_offset, "speaker": speaker})
                    cur = ""
                    word_start = None
            else:
                if word_start is None:
                    word_start = starts[i]
                cur += ch
        if cur and word_start is not None:
            words.append({"word": cur, "start": word_start + time_offset, "end": ends[-1] + time_offset, "speaker": speaker})
        return words

    def _concat_with_silences(self, audio_clips: Iterable[AudioFileClip]):
        audio_clips = list(audio_clips)
        if not audio_clips:
            raise ValueError("No audio clips to combine")
        if len(audio_clips) == 1:
            return audio_clips[0], None
        silence_duration = 0.3
        sample_rate = 44100
        silence_array = np.zeros((int(silence_duration * sample_rate), 2))
        silence = AudioArrayClip(silence_array, fps=sample_rate)
        combined: List = []
        for i, clip in enumerate(audio_clips):
            combined.append(clip)
            if i < len(audio_clips) - 1:
                combined.append(silence)
        final_audio = concatenate_audioclips(combined)
        return final_audio, silence

    def _prepare_background(self, path: Path, needed_duration: float):
        clip = VideoFileClip(str(path))
        if clip.duration < needed_duration:
            loops = int(needed_duration / clip.duration) + 1
            clip = clip.loop(n=loops)
        clip = clip.subclip(0, needed_duration)
        bg_aspect = clip.w / clip.h
        target_aspect = self.TARGET_W / self.TARGET_H
        if bg_aspect > target_aspect:
            new_width = int(clip.h * target_aspect)
            clip = clip.crop(x_center=clip.w / 2, width=new_width)
        else:
            new_height = int(clip.w / target_aspect)
            clip = clip.crop(y_center=clip.h / 2, height=new_height)
        return clip.resize((self.TARGET_W, self.TARGET_H))

    def _build_word_captions(self, words: List[dict]):
        clips = []
        for wd in words:
            char_info = self.role_characters[wd["speaker"]]
            duration = wd["end"] - wd["start"]
            txt = TextClip(
                wd["word"],
                fontsize=100,
                font="Impact",
                color=char_info["caption_color"],
                stroke_color=char_info["caption_stroke_color"],
                stroke_width=6,
                method="caption",
                size=(int(self.TARGET_W * 0.9), None),
            ).set_position(("center", "center")).set_start(wd["start"]).set_duration(duration)
            clips.append(txt)
        return clips

    def _build_character_image_sequence(self, words: List[dict]):
        clips = []
        if not words:
            return clips
        current_speaker = words[0]["speaker"]
        start_time = words[0]["start"]
        for wd in words[1:]:
            if wd["speaker"] != current_speaker:
                end_time = wd["start"]
                clips.append(self._make_character_clip(current_speaker, start_time, end_time))
                current_speaker = wd["speaker"]
                start_time = wd["start"]
        clips.append(self._make_character_clip(current_speaker, start_time, words[-1]["end"]))
        return clips

    def _make_character_clip(self, speaker: str, start: float, end: float):
        info = self.role_characters[speaker]
        image_path = self.assets_dir / info["image_file"]
        if not image_path.exists():
            raise FileNotFoundError(f"Missing character image: {image_path}")
        img = ImageClip(str(image_path)).resize(self.CHARACTER_IMAGE_SIZE)
        base_pos = self.CHARACTER_POSITIONS.get(speaker, [50, 1250])

        def jiggle(t):  # noqa: ANN001
            import math
            return (
                base_pos[0] + self.JIGGLE_INTENSITY * math.sin(t * self.JIGGLE_FREQUENCY),
                base_pos[1] + self.JIGGLE_INTENSITY * math.cos(t * self.JIGGLE_FREQUENCY * 1.2),
            )

        return img.set_position(jiggle).set_start(start).set_duration(end - start)

    def _cleanup_temp(self):
        for p in self.temp_dir.glob("*"):
            try:
                if p.is_file():
                    p.unlink()
            except Exception:  # noqa: BLE001
                pass

    def _upload_to_s3(self, file_path: Path) -> Optional[str]:
        if not self.s3_client:
            return None
        key = f"videos/{file_path.name}"
        try:
            self.s3_client.upload_file(
                str(file_path),
                self.s3_bucket,
                key,
                ExtraArgs={"ContentType": "video/mp4"},
            )
            return f"https://{self.s3_bucket}.s3.{self.s3_region}.amazonaws.com/{key}"
        except ClientError as e:
            self.logger.warning(f"S3 upload error: {e}")
            return None

    # ---------------- Character pool & role assignment -----------------
    def _load_character_pool(self) -> list[dict]:
        config_file = self.assets_dir / 'characters.json'
        if config_file.exists():
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, list) and data:
                    self.logger.info(f"Loaded {len(data)} characters from characters.json")
                    return data
            except Exception as e:  # noqa: BLE001
                self.logger.warning(f"characters.json load failed: {e}; using defaults")
        return list(self.DEFAULT_CHARACTER_POOL)

    def _assign_roles(self, roles: list[str]):
        """Assign exactly up to two roles (Speaker A/B) to character pool entries."""
        if not roles:
            return
        if len(roles) > 2:
            raise ValueError("Only two speakers supported (Speaker A, Speaker B)")
        if len(self.character_pool) < 2:
            raise ValueError("Character pool must contain at least two characters")
        if self.randomize_roles and len(roles) == 2:
            chosen = random.sample(self.character_pool, 2)
        else:
            chosen = self.character_pool[: len(roles)]
        self.role_characters = {r: dict(ch) for r, ch in zip(roles, chosen)}
        # Fixed positions (bottom left/right)
        self.CHARACTER_POSITIONS = {}
        for idx, r in enumerate(roles):
            self.CHARACTER_POSITIONS[r] = self.CHARACTER_POSITIONS_TEMPLATE[idx]
        mapping = ", ".join(f"{r}->{self.role_characters[r]['name']}" for r in roles)
        self.logger.info(f"Assigned roles: {mapping}")


__all__ = ["BrainBitesVideoGenerator", "TranscriptLine"]
