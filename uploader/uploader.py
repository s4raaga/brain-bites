"""Tkinter GUI Orchestrator for Brain-Bites

Features:
1. Login to Blackboard (re-uses existing session unless forced)
2. Generate: invokes Anthropic via MCP agent to:
   - list courses
   - (future) fetch/download content if needed
   - produce 20 topic JSON script files (title, description, dialogue[])
3. For each produced transcript JSON (in transcripts/), optionally create a video via video_making module.

Simplifications / Assumptions:
 - Assumes ANTHROPIC_API_KEY, OPENAI_API_KEY, ELEVENLABS_API_KEY env vars are set.
 - Relies on existing bb_mcp.py tools (login, list_courses, save_json). We craft prompts directly instead of interactive loop.
 - Uses Anthropic Messages API directly (no streaming) for automation.
 - Topics selection heuristic: ask model to infer 20 distinct key learning topics from available course names only (can be refined later by pulling content pages / downloaded PDFs).

This GUI is intentionally minimal to keep footprint small; enhancements (progress bars, cancelation) can follow.
"""

from __future__ import annotations

import json
import os
import threading
import time
import traceback
from pathlib import Path
from typing import List, Dict, Any

import tkinter as tk
from tkinter import ttk, messagebox

# External deps expected already in environment: anthropic, mcp, playwright, etc.
try:
	from anthropic import Anthropic
except ImportError:  # pragma: no cover
	Anthropic = None  # type: ignore

# Local imports
import subprocess
import sys

# Video making
try:
	from video_making.main import BrainrotReelGenerator  # type: ignore
except Exception:  # noqa: BLE001
	BrainrotReelGenerator = None  # type: ignore

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "datacq"
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"
MCP_SERVER = DATA_DIR / "bb_mcp.py"

DEFAULT_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-latest")


class GuiState:
	def __init__(self):
		self.logged_in = False
		self.generating = False
		self.stop_flag = False
		self.course_cache: List[Dict[str, str]] = []


class BrainBitesApp(tk.Tk):
	def __init__(self):
		super().__init__()
		self.title("Brain-Bites Uploader")
		self.geometry("760x520")
		self.state = GuiState()
		self._build_widgets()
		self._log("Ready. Set env vars and click Login.")

	# ---------- UI ----------
	def _build_widgets(self):
		top_frame = ttk.Frame(self)
		top_frame.pack(fill=tk.X, padx=10, pady=6)

		# Base URL & model row
		self.base_url_var = tk.StringVar(value="https://learn.uq.edu.au/")
		self.model_var = tk.StringVar(value=DEFAULT_MODEL)
		self.anthropic_key_var = tk.StringVar()
		self.openai_key_var = tk.StringVar()
		self.eleven_key_var = tk.StringVar()

		form = ttk.LabelFrame(self, text="Configuration")
		form.pack(fill=tk.X, padx=10, pady=4)

		def add_row(label, var, show=None, width=60):
			row = ttk.Frame(form)
			row.pack(fill=tk.X, pady=2)
			ttk.Label(row, text=label, width=18, anchor='w').pack(side=tk.LEFT)
			entry = ttk.Entry(row, textvariable=var, width=width, show=show)
			entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
			return entry

		add_row("Blackboard Base URL", self.base_url_var)
		add_row("Anthropic Model", self.model_var)
		add_row("ANTHROPIC_API_KEY", self.anthropic_key_var, show='*')
		add_row("OPENAI_API_KEY", self.openai_key_var, show='*')
		add_row("ELEVENLABS_API_KEY", self.eleven_key_var, show='*')

		buttons = ttk.Frame(self)
		buttons.pack(fill=tk.X, padx=10, pady=4)
		self.login_btn = ttk.Button(buttons, text="Login", command=self._on_login)
		self.login_btn.pack(side=tk.LEFT)
		self.generate_btn = ttk.Button(buttons, text="Generate 20 Scripts", command=self._on_generate, state=tk.DISABLED)
		self.generate_btn.pack(side=tk.LEFT, padx=8)
		self.make_videos_var = tk.BooleanVar(value=False)
		ttk.Checkbutton(buttons, text="Auto make videos", variable=self.make_videos_var).pack(side=tk.LEFT, padx=8)
		self.status_var = tk.StringVar(value="Idle")
		ttk.Label(buttons, textvariable=self.status_var).pack(side=tk.RIGHT)

		# Progress
		self.progress = ttk.Progressbar(self, mode="determinate", maximum=20)
		self.progress.pack(fill=tk.X, padx=10, pady=4)

		# Log panel
		self.log_text = tk.Text(self, wrap="word", height=22)
		self.log_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)
		self.log_text.configure(state="disabled")

	def _log(self, msg: str):
		self.log_text.configure(state="normal")
		self.log_text.insert(tk.END, time.strftime("[%H:%M:%S] ") + msg + "\n")
		self.log_text.see(tk.END)
		self.log_text.configure(state="disabled")

	def _set_status(self, s: str):
		self.status_var.set(s)
		self.update_idletasks()

	# ---------- Actions ----------
	def _on_login(self):
		# Login does not require API keys; only Blackboard base URL
		self.login_btn.config(state=tk.DISABLED)
		threading.Thread(target=self._login_worker, daemon=True).start()

	def _login_worker(self):
		try:
			self._set_status("Logging in…")
			self._log("Launching login flow…")
			base_url = self.base_url_var.get().strip().rstrip('/') + '/'
			cmd = [sys.executable, str(DATA_DIR / "bb_dat_acq.py"), "--base-url", base_url, "login"]
			self._log("Running: " + " ".join(cmd))
			proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
			for line in proc.stdout:  # type: ignore[attr-defined]
				self._log(line.rstrip())
			rc = proc.wait()
			if rc != 0:
				raise RuntimeError(f"Login command exited {rc}")
			self.state.logged_in = True
			self._log("Login completed (cached session).")
			self._set_status("Logged in")
			self.generate_btn.config(state=tk.NORMAL)
		except Exception as e:  # noqa: BLE001
			self._log(f"ERROR during login: {e}")
			self._set_status("Login failed")
			self.login_btn.config(state=tk.NORMAL)

	def _on_generate(self):
		if self.state.generating:
			return
		self.state.generating = True
		self.generate_btn.config(state=tk.DISABLED)
		self.progress['value'] = 0
		threading.Thread(target=self._generate_worker, daemon=True).start()

	# ---------- Generation logic ----------
	def _generate_worker(self):
		try:
			self._set_status("Listing courses…")
			self._log("Listing courses via bb_dat_acq…")
			base_url = self.base_url_var.get().strip().rstrip('/') + '/'
			list_cmd = [sys.executable, str(DATA_DIR / "bb_dat_acq.py"), "--base-url", base_url, "list-courses"]
			proc = subprocess.Popen(list_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
			stdout, stderr = proc.communicate(timeout=400)
			if proc.returncode != 0:
				self._log(stderr)
				raise RuntimeError("list-courses failed")
			# Parse naive: lines pattern name then URL
			lines = [l.strip() for l in stdout.splitlines() if l.strip()]
			courses: List[Dict[str, str]] = []
			for i in range(0, len(lines), 2):
				name = lines[i]
				url = lines[i+1] if i+1 < len(lines) else ''
				if url.startswith('http'):
					courses.append({"name": name, "url": url})
			self.state.course_cache = courses
			self._log(f"Detected {len(courses)} course(s).")
			course_names = [c['name'] for c in courses]
			if not course_names:
				raise RuntimeError("No courses found; cannot derive topics.")

			self._set_status("Generating topics…")
			self._log("Calling Anthropic to produce 20 topic statements…")
			if Anthropic is None:
				raise RuntimeError("anthropic package not installed")
			anthropic_key = self.anthropic_key_var.get().strip()
			if not anthropic_key:
				raise RuntimeError("Enter Anthropic API key above")
			client = Anthropic(api_key=anthropic_key)
			model = self.model_var.get().strip() or DEFAULT_MODEL
			topic_prompt = (
				"You are an educational summarizer. Given these course titles:\n" +
				"\n".join(f"- {n}" for n in course_names) +
				"\n\nProduce exactly 20 distinct, high-value, exam-relevant topic SENTENCES (concise, single sentence each) spanning the breadth of the courses. Return JSON array of strings ONLY."  # noqa: E501
			)
			resp = client.messages.create(
				model=model,
				max_tokens=1200,
				messages=[{"role": "user", "content": topic_prompt}],
			)
			raw_topics = []
			for block in resp.content:
				if getattr(block, "type", "") == "text":
					raw_topics.append(block.text)
			joined = "\n".join(raw_topics)
			try:
				topics = json.loads(joined)
				if not isinstance(topics, list):
					raise ValueError("not list")
			except Exception:
				# Fallback: naive line split
				topics = [t.strip(" -•\t") for t in joined.splitlines() if t.strip()][:20]
			topics = topics[:20]
			self._log(f"Got {len(topics)} topic candidates.")

			# For each topic generate a script JSON and save.
			self._set_status("Writing scripts…")
			ensure_dir = TRANSCRIPTS_DIR.mkdir(exist_ok=True, parents=True)  # noqa: F841
			for idx, topic in enumerate(topics, start=1):
				self.progress['value'] = idx
				self._set_status(f"Script {idx}/20")
				self._log(f"Generating script {idx}: {topic[:70]}")
				script_prompt = (
					"Create a JSON video script with keys: title, description, dialogue. "
					"Title: 3-8 words. Description: 1-2 sentences, cite concepts. Dialogue: 6-10 short lines, 1 or 2 speakers labeled 'A' and 'B' (or 'Narrator' if one speaker). "
					"Tone: engaging, slight brainrot meme energy but still educational, no profanity. \nTopic: " + topic
				)
				model = self.model_var.get().strip() or DEFAULT_MODEL
				script_resp = client.messages.create(
					model=model,
					max_tokens=1000,
					messages=[{"role": "user", "content": script_prompt}],
				)
				text_blocks = []
				for b in script_resp.content:
					if getattr(b, "type", "") == "text":
						text_blocks.append(b.text)
				combined = "\n".join(text_blocks).strip()
				# Attempt JSON parse, else wrap.
				try:
					obj = json.loads(combined)
				except Exception:
					# Attempt extract fenced code
					if '```' in combined:
						parts = combined.split('```')
						for p in parts:
							p = p.strip()
							if p.startswith('{') and p.endswith('}'):
								try:
									obj = json.loads(p)
									break
								except Exception:
									pass
						else:
							obj = {"title": topic[:50], "description": combined[:180], "dialogue": []}
					else:
						obj = {"title": topic[:50], "description": combined[:180], "dialogue": []}
				# Minimal normalization
				if not isinstance(obj, dict):
					obj = {"title": topic[:50], "description": str(obj)[:180], "dialogue": []}
				obj.setdefault("title", topic[:60])
				obj.setdefault("description", topic[:160])
				if "dialogue" not in obj or not isinstance(obj["dialogue"], list):
					obj["dialogue"] = []
				# Save
				safe_stem = f"auto_{idx:02d}"  # simple prefix
				out_path = TRANSCRIPTS_DIR / f"{safe_stem}.json"
				with open(out_path, 'w', encoding='utf-8') as f:
					json.dump(obj, f, ensure_ascii=False, indent=2)
				self._log(f"Saved {out_path.name}")
				self.update_idletasks()

			self._log("All scripts generated.")
			self._set_status("Done")

			# Optional video generation
			if self.make_videos_var.get():
				if BrainrotReelGenerator is None:
					self._log("Video module not available; skipping video generation.")
				else:
					# Apply keys to environment for downstream library (BrainrotReelGenerator reads env)
					openai_key = self.openai_key_var.get().strip()
					eleven_key = self.eleven_key_var.get().strip()
					if not openai_key or not eleven_key:
						self._log("Missing OpenAI or ElevenLabs key; skipping videos.")
					else:
						os.environ['OPENAI_API_KEY'] = openai_key
						os.environ['ELEVENLABS_API_KEY'] = eleven_key
						self._set_status("Making videos…")
						gen = BrainrotReelGenerator()
						for idx, jp in enumerate(sorted(TRANSCRIPTS_DIR.glob('auto_*.json'))):
							try:
								with open(jp, 'r', encoding='utf-8') as f:
									jd = json.load(f)
								lines = []
								for d in jd.get('dialogue', []):
									if isinstance(d, dict):
										lines.append(d.get('text', ''))
								script_text = jd.get('description', '') + '\n' + ' '.join(lines)
								self._log(f"Video {idx+1}: {jp.name}")
								gen.run_with_script(script_text)
							except Exception as ve:  # noqa: BLE001
								self._log(f"Video generation failed for {jp.name}: {ve}")
						self._set_status("Videos done")
		except Exception as e:  # noqa: BLE001
			self._log(f"ERROR: {e}\n{traceback.format_exc()}")
			self._set_status("Error")
		finally:
			self.state.generating = False
			self.generate_btn.config(state=tk.NORMAL)


def main():  # pragma: no cover
	app = BrainBitesApp()
	app.mainloop()


if __name__ == "__main__":  # pragma: no cover
	main()
