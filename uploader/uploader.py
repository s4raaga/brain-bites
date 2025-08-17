"""Simple GUI Uploader / Orchestrator (Scraping Edition)

Features:
 - Login button: launches scraping MCP login tool (bb_mcp.tool_login) which opens a browser (headless configurable inside agent) so you can authenticate; session cookies persisted.
 - Generate Scripts button: uses Anthropic agent (datacq.agent) to drive scraping tools and save grounded JSON scripts.
 - Build Video button: converts saved JSON scripts (transcripts/) into short videos.

Prerequisites:
 - Environment vars: ANTHROPIC_API_KEY, ELEVENLABS_API_KEY
 - Python deps: mcp, anthropic, playwright, bs4, pypdf, moviepy, dotenv, pysrt
 - Install browser: run: playwright install chromium
 - Background videos in video_making/inputs/backgrounds/

Notes:
 - Blackboard REST OAuth flow deprecated for this workflow; we rely on Playwright-controlled user login.
 - The login tool returns log lines summarizing actions and ensures persistent profile storage for subsequent tool calls.
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
	from datacq.bb_mcp import tool_login  # Scraping login tool
except Exception as _import_exc:  # noqa: BLE001
	tool_login = None  # type: ignore
	TOOL_LOGIN_IMPORT_ERROR = _import_exc  # type: ignore

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

		btn_frame = ttk.Frame(self.root)
		btn_frame.pack(fill=tk.X, padx=8, pady=4)

		self.login_btn = ttk.Button(btn_frame, text="Login", command=self.on_login)
		self.login_btn.pack(side=tk.LEFT, padx=4)

		self.generate_btn = ttk.Button(btn_frame, text="Generate Scripts", command=self.on_generate, state=tk.NORMAL)
		self.generate_btn.pack(side=tk.LEFT, padx=4)

		self.refresh_btn = ttk.Button(btn_frame, text="Reload Scripts", command=self.load_existing_scripts)
		self.refresh_btn.pack(side=tk.LEFT, padx=4)

		self.make_video_btn = ttk.Button(btn_frame, text="Build Video from Scripts", command=self.on_make_video, state=tk.DISABLED)
		self.make_video_btn.pack(side=tk.LEFT, padx=12)

		ttk.Label(btn_frame, textvariable=self.status_var).pack(side=tk.RIGHT, padx=4)

		middle = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
		middle.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

		# Left panel (script info only now; selection removed)
		left_frame = ttk.Frame(middle)
		middle.add(left_frame, weight=1)
		ttk.Label(left_frame, text="Generated Scripts (auto-managed)").pack(anchor="w")
		self.script_info_lbl = ttk.Label(left_frame, text="(None yet)")
		self.script_info_lbl.pack(anchor="w", pady=(4,0))

		# Right panel (logs unchanged)
		right_frame = ttk.Frame(middle)
		middle.add(right_frame, weight=2)
		ttk.Label(right_frame, text="Logs / Output").pack(anchor="w")
		self.log_txt = tk.Text(right_frame, height=20, wrap="word")
		self.log_txt.pack(fill=tk.BOTH, expand=True)
		self.log_txt.configure(state=tk.DISABLED)

		# Stretch last row/column to avoid geometry issues
		top.grid_columnconfigure(1, weight=1)
		top.grid_columnconfigure(3, weight=0)

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
		# Enable if any scripts exist
		self.make_video_btn.configure(state=(tk.NORMAL if self.generated_scripts else tk.DISABLED))

	def load_existing_scripts(self):
		TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
		self.generated_scripts.clear()
		count = 0
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
			count += 1
		self.script_info_lbl.configure(text=f"{count} script(s) detected")
		self.log(f"Loaded {len(self.generated_scripts)} scripts.")
		self._update_video_button_state()

	# ---------------- Actions ----------------
	def on_login(self):
		if tool_login is None:
			detail = globals().get("TOOL_LOGIN_IMPORT_ERROR")
			msg = "Could not import bb_mcp.tool_login"
			if detail:
				msg += f"\n\nRoot cause: {detail.__class__.__name__}: {detail}"
			msg += "\n\nInstall required deps (mcp, playwright, bs4, pypdf, anthropic). After installing run: playwright install chromium"
			messagebox.showerror("Unavailable", msg)
			return
		self.set_status("Logging in…")
		self.login_btn.configure(state=tk.DISABLED)
		self.generate_btn.configure(state=tk.DISABLED)
		def do_login():
			self.log("Starting scraping login flow…")
			resp = tool_login()
			logs = resp.get("logs") if isinstance(resp, dict) else None
			if logs:
				for line in logs:
					self.log(f"  {line}")
			return "login done"
		self._run_in_thread(do_login)

	def on_make_video(self):
		# Batch-generate videos for all detected scripts (sequentially, threaded)
		if not self.generated_scripts:
			messagebox.showinfo("No Scripts", "No transcript JSON scripts are available.")
			return
		try:
			from video_making import BrainBitesVideoGenerator  # type: ignore
		except Exception as e:  # noqa: BLE001
			messagebox.showerror("Import Error", f"Could not import generator: {e}")
			return

		def do_build():
			self.set_status("Rendering videos…")
			self.log("Initializing video generator…")
			try:
				gen = BrainBitesVideoGenerator()
			except Exception as e:  # noqa: BLE001
				self.log(f"Failed to init generator: {e}")
				return "generator init failed"
			count = 0
			deleted = 0
			for script in list(self.generated_scripts):  # iterate over copy
				self.log(f"Building video for: {script.path.name}")
				try:
					meta = gen.generate_from_file(script.path, return_meta=True)
					if isinstance(meta, dict):
						out_path = meta.get("uploaded_url") or meta.get("local_path")
					else:
						out_path = meta
					self.log(f"  ✔ Output: {out_path}")
					count += 1
					# Delete script file after success
					try:
						script.path.unlink()
						deleted += 1
						self.log(f"  🗑 Deleted script {script.path.name}")
					except Exception as del_e:  # noqa: BLE001
						self.log(f"  ⚠ Could not delete script: {del_e}")
				except Exception as e:  # noqa: BLE001
					self.log(f"  ✖ Failed: {e}")
			# Refresh list after deletions
			self.load_existing_scripts()
			return f"built {count} video(s), deleted {deleted} script(s)"

		self._run_in_thread(do_build)

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
Never introduce any other speaker names. Use only these exact labels. Each script covers a different grounded micro-topic. If truly impossible to reach {max_scripts}, save all valid ones then state limitation briefly. Hook style directive: In some scripts (not all), begin with ONE very short, absurd hook line). Immediately (next 1-2 lines) pivot to the real grounded topic and clarify the hook as a comedic language.""".strip()

		def do_generate():
			self.log("Running agent to generate script JSON…")
			finals = bb_agent.run_scripted_sync(
				prompts=[prompt],
				server=SERVER_PATH,
				model=model,
				verbose=False,
				tool_logger=lambda name, preview: self.log(f"TOOL {name}: " + preview[:200].replace('\n', ' '))
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

