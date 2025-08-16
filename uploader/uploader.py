"""Simple GUI Uploader / Orchestrator

Features:
 - Login button: reuses Blackboard MCP login tool (cached session via Playwright persistent context)
 - Generate Scripts button: uses Anthropic agent (datacq.agent) to invoke MCP tools and save JSON video scripts
 - Build Video button: turns a selected saved JSON script (under transcripts/) into a short video using BrainrotReelGenerator

Prerequisites (expected already in repo environment):
 - Environment vars: ANTHROPIC_API_KEY, ELEVENLABS_API_KEY
 - Python deps installed for: playwright (and chromium installed), mcp, anthropic, moviepy, requests, bs4, dotenv, pysrt
 - Background videos present in video_making/inputs/backgrounds/

This file purposefully keeps logic lightweight and threads blocking work so the Tkinter UI stays responsive.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import tkinter as tk
from tkinter import ttk, messagebox

# --- Ensure project root on sys.path when running as script ---
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
	sys.path.insert(0, str(PROJECT_ROOT))

# --- Internal imports from existing project ---
try:
	from datacq.bb_mcp import tool_login  # direct tool call (threaded inside)
except Exception:  # noqa: BLE001
	tool_login = None  # type: ignore

try:
	from datacq import agent as bb_agent
except Exception:  # noqa: BLE001
	bb_agent = None  # type: ignore



BASE_URL = "https://learn.uq.edu.au/"  # consistent with agent logic
FIXED_TOPIC = (
	"interesting/key topics from lectures presented in engaging ways. "
)  # Hardcoded prompt topic (removed from UI)
TRANSCRIPTS_DIR = Path("transcripts")
SERVER_PATH = str(Path("datacq") / "bb_mcp.py")  # path to MCP server used by agent

# Allowed speaker names now constrained to an explicit small set.
# Requirement change: scripts must be EITHER
#   (a) a single-speaker monologue using only "Speaker A" OR
#   (b) a two-speaker conversation using only "Speaker A" and "Speaker B".
# No other speaker labels are permitted. The agent should decide per script which
# of the two formats best suits the grounded micro-topic (variety across scripts if >1).
VALID_SPEAKERS = ["Speaker A", "Speaker B"]


@dataclass
class GeneratedScript:
	path: Path
	title: str
	description: str
	dialogue_preview: str


def safe_read_json(path: Path) -> Optional[dict]:
	try:
		with open(path, "r", encoding="utf-8") as f:
			return json.load(f)
	except Exception:
		return None


class OrchestratorGUI:
	def __init__(self, root: tk.Tk):
		self.root = root
		self.root.title("Brain Bites Uploader & Generator")
		self.root.geometry("920x640")

		# Topic removed from UI; kept minimal variable for any legacy references
		self.topic_var = tk.StringVar(value=FIXED_TOPIC)
		self.status_var = tk.StringVar(value="Idle")
		self.model_var = tk.StringVar(value="claude-3-5-haiku-latest")
		self.max_scripts_var = tk.IntVar(value=1)
		self.headless_var = tk.BooleanVar(value=True)  # default run headless

		self._worker_queue: "queue.Queue[str]" = queue.Queue()
		self._current_worker: Optional[threading.Thread] = None
		self._known_scripts_before: set[str] = set()
		self.generated_scripts: List[GeneratedScript] = []

		self._build_ui()
		self._poll_queue()

	# ---------------- UI Layout ----------------
	def _build_ui(self):
		top = ttk.Frame(self.root)
		top.pack(fill=tk.X, padx=8, pady=6)

		# Reflow grid now that topic input is removed.
		ttk.Label(top, text="Model:").grid(row=0, column=0, sticky="e")
		ttk.Entry(top, textvariable=self.model_var, width=24).grid(row=0, column=1, padx=4, sticky="w")
		ttk.Label(top, text="Target Scripts:").grid(row=0, column=2, sticky="e")
		ttk.Spinbox(top, from_=1, to=10, textvariable=self.max_scripts_var, width=5).grid(row=0, column=3, padx=4, sticky="w")
		headless_cb = ttk.Checkbutton(top, text="Headless browser", variable=self.headless_var)
		headless_cb.grid(row=0, column=4, padx=8, sticky="w")

		btn_frame = ttk.Frame(self.root)
		btn_frame.pack(fill=tk.X, padx=8, pady=4)

		self.login_btn = ttk.Button(btn_frame, text="Login", command=self.on_login)
		self.login_btn.pack(side=tk.LEFT, padx=4)

		self.generate_btn = ttk.Button(btn_frame, text="Generate Scripts", command=self.on_generate, state=tk.NORMAL)
		self.generate_btn.pack(side=tk.LEFT, padx=4)

		self.refresh_btn = ttk.Button(btn_frame, text="Refresh Script List", command=self.load_existing_scripts)
		self.refresh_btn.pack(side=tk.LEFT, padx=4)

		self.make_video_btn = ttk.Button(btn_frame, text="Build Video from Selected", command=self.on_make_video, state=tk.DISABLED)
		self.make_video_btn.pack(side=tk.LEFT, padx=12)

		ttk.Label(btn_frame, textvariable=self.status_var).pack(side=tk.RIGHT, padx=4)

		middle = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
		middle.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

		# Script list
		left_frame = ttk.Frame(middle)
		middle.add(left_frame, weight=1)
		ttk.Label(left_frame, text="Available JSON Scripts (transcripts/)").pack(anchor="w")
		self.script_list = tk.Listbox(left_frame, height=12)
		self.script_list.pack(fill=tk.BOTH, expand=True)
		self.script_list.bind("<<ListboxSelect>>", lambda e: self._update_video_button_state())

		# Log / Output console
		right_frame = ttk.Frame(middle)
		middle.add(right_frame, weight=2)
		ttk.Label(right_frame, text="Logs / Output").pack(anchor="w")
		self.log_txt = tk.Text(right_frame, height=20, wrap="word")
		self.log_txt.pack(fill=tk.BOTH, expand=True)
		self.log_txt.configure(state=tk.DISABLED)

		# Stretch last row/column to avoid geometry issues
		top.grid_columnconfigure(1, weight=1)
		top.grid_columnconfigure(3, weight=0)
		top.grid_columnconfigure(4, weight=0)

		self.load_existing_scripts()

	# ---------------- Helpers ----------------
	def log(self, msg: str):
		self.log_txt.configure(state=tk.NORMAL)
		self.log_txt.insert(tk.END, f"{time.strftime('%H:%M:%S')} | {msg}\n")
		self.log_txt.see(tk.END)
		self.log_txt.configure(state=tk.DISABLED)

	def set_status(self, msg: str):
		self.status_var.set(msg)

	def _run_in_thread(self, target, *args, **kwargs):
		if self._current_worker and self._current_worker.is_alive():
			messagebox.showwarning("Busy", "A task is already running.")
			return
		def wrapper():
			try:
				result = target(*args, **kwargs)
				self._worker_queue.put(json.dumps({"ok": True, "result": result}))
			except Exception as e:  # noqa: BLE001
				self._worker_queue.put(json.dumps({"ok": False, "error": str(e)}))
		t = threading.Thread(target=wrapper, daemon=True)
		self._current_worker = t
		t.start()

	def _poll_queue(self):
		try:
			while True:
				raw = self._worker_queue.get_nowait()
				data = json.loads(raw)
				if data.get("ok"):
					self.log(f"Task finished: {data.get('result')!r}")
				else:
					self.log(f"ERROR: {data.get('error')}")
				self.set_status("Idle")
				self.login_btn.configure(state=tk.NORMAL)
				self.generate_btn.configure(state=tk.NORMAL)
				self._update_video_button_state()
		except queue.Empty:
			pass
		self.root.after(250, self._poll_queue)

	def _update_video_button_state(self):
		sel = self.script_list.curselection()
		self.make_video_btn.configure(state=(tk.NORMAL if sel else tk.DISABLED))

	def load_existing_scripts(self):
		TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
		self.script_list.delete(0, tk.END)
		self.generated_scripts.clear()
		for path in sorted(TRANSCRIPTS_DIR.glob("*.json")):
			obj = safe_read_json(path)
			if not isinstance(obj, dict):
				continue
			title = obj.get("title") or path.stem
			desc = obj.get("description") or ""
			dialogue = obj.get("dialogue") or []
			if isinstance(dialogue, list):
				preview = " ".join(
					d.get("text", "")[:60] for d in dialogue if isinstance(d, dict)
				)[:120]
			else:
				preview = ""
			gs = GeneratedScript(path=path, title=title, description=desc, dialogue_preview=preview)
			self.generated_scripts.append(gs)
			self.script_list.insert(tk.END, f"{title} | {path.name}")
		self.log(f"Loaded {len(self.generated_scripts)} scripts.")
		self._update_video_button_state()

	# ---------------- Actions ----------------
	def on_login(self):
		if tool_login is None:
			messagebox.showerror("Unavailable", "Could not import bb_mcp.tool_login")
			return
		self.set_status("Logging in…")
		self.login_btn.configure(state=tk.DISABLED)
		self.generate_btn.configure(state=tk.DISABLED)
		def do_login():
			mode = "headless" if self.headless_var.get() else "visible"
			self.log(f"Starting login flow ({mode})…")
			resp = tool_login()  # returns dict
			self.log("Login complete. Logs:")
			for line in resp.get("logs", []):
				self.log(f"  {line}")
			return "login done"
		self._run_in_thread(do_login)

	def on_make_video(self):
		pass # Placeholder for video generation logic

	def on_generate(self):
		if bb_agent is None:
			messagebox.showerror("Unavailable", "Agent module not importable")
			return
		if not os.getenv("ANTHROPIC_API_KEY"):
			messagebox.showerror("Missing Key", "ANTHROPIC_API_KEY env var not set")
			return
		# Use fixed hardcoded topic (ignore any legacy variable edits)
		topic = FIXED_TOPIC
		max_scripts = self.max_scripts_var.get()
		self._known_scripts_before = {p.name for p in TRANSCRIPTS_DIR.glob("*.json")}
		self.set_status("Generating scripts…")
		self.login_btn.configure(state=tk.DISABLED)
		self.generate_btn.configure(state=tk.DISABLED)
		model = self.model_var.get().strip()

		# Concise agent instructions (short to reduce tokens) enforcing:
		# - enumerate first four real courses (after any generic training entry)
		# - gather grounding PDFs for EACH (list_content -> download_pdf -> read_pdf_text until >=400 chars usable text per course)
		# - extract near-term/key topics (e.g. current week, upcoming assessments, major concepts) relevant to requested topic
		# - produce EXACTLY max_scripts JSON scripts (unless truly impossible due to insufficient distinct grounded topics; if so, explain and still output as many grounded scripts as possible) with varied styles while following formatting guide
		# - allowed speakers constrained to VALID_SPEAKERS
		# Format constraint update:
		#   EACH script MUST choose exactly one of two formats:
		#     Format 1: Single-speaker monologue (all dialogue entries have speaker "Speaker A").
		#     Format 2: Two-speaker dialogue (only speakers "Speaker A" and "Speaker B" appear; no others).
		#   Do not mix additional names. Maintain factual grounding; paraphrase evidence (no hallucinations).
		#   JSON schema unchanged: title (3-8 words), description (1-2 sentences citing course/source), dialogue (list of {speaker,text}).
		#   Keep lines concise; for two-speaker dialogues, keep a natural back-and-forth (avoid long monologues inside dialogue format).
		#   If generating multiple scripts, aim to include a mix of Format 1 and Format 2 when feasible.
		# Save each with save_json only AFTER grounding steps complete. Never fabricate content. Do not exceed limit.
		prompt = f"""
Generate EXACTLY {max_scripts} grounded script JSON file(s) about: {topic}.
Follow the tool-described evidence workflow (collect PDFs & read_pdf_text first for distinct topics across first 4 real courses). Only after adequate evidence, create scripts. For EACH script pick ONE format:
  - Monologue: only speaker "Speaker A" used in every line.
  - Two-speaker: only speakers "Speaker A" and "Speaker B" (no others). Keep a balanced exchange.
Never introduce any other speaker names. Use only these exact labels. Each script covers a different grounded micro-topic. If truly impossible to reach {max_scripts}, save all valid ones then state limitation briefly.
""".strip()

		def do_generate():
			mode = "headless" if self.headless_var.get() else "visible"
			self.log(f"Running agent to generate script JSON ({mode})…")
			finals = bb_agent.run_scripted_sync(
				prompts=[prompt],
				server=SERVER_PATH,
				model=model,
				headless=self.headless_var.get(),
				verbose=False,
				tool_logger=lambda name, preview: self.log(f"TOOL {name}: " + preview[:200].replace("\n", " "))
			)
			self.log("Agent natural language reply:")
			for ln in finals[0].splitlines():
				self.log(f"  {ln}")
			# Detect new scripts
			time.sleep(0.5)
			new_files = [p for p in TRANSCRIPTS_DIR.glob("*.json") if p.name not in self._known_scripts_before]
			if new_files:
				self.log(f"New scripts saved: {[p.name for p in new_files]}")
			else:
				self.log("No new scripts detected. Review agent reply above.")
			self.load_existing_scripts()
			return f"generated {len(new_files)} scripts"
		self._run_in_thread(do_generate)


def main():  # pragma: no cover - interactive
	root = tk.Tk()
	OrchestratorGUI(root)
	root.mainloop()


if __name__ == "__main__":  # pragma: no cover
	main()

