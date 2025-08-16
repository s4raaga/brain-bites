# bb_blackboard_mcp.py
from __future__ import annotations

import contextlib
import json
import os
import pathlib
import re
import sys
import time
import threading
import traceback
from queue import Queue
from typing import Dict, List, Optional, Tuple, Any, Union
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup  # type: ignore
from pypdf import PdfReader  # type: ignore
from playwright.sync_api import BrowserContext, Page, sync_playwright  # type: ignore

# --- MCP server wrapper ---
# pip install mcp
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("bb_blackboard")

# ---------- fixed locations ----------
PROFILE_DIR = os.path.abspath("profile")
# NOTE: repo folder is 'downloads'; align constant (was previously 'download').
DOWNLOAD_DIR = os.path.abspath("downloads")
# Transcripts (video script JSON outputs) directory – fixed relative path
TRANSCRIPTS_DIR = os.path.abspath("transcripts")

DEFAULT_TIMEOUT = 30_000  # ms
BASE_URL = "https://learn.uq.edu.au/"  # Hard-coded per user requirement

# ---------- helpers ----------

def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)

def ensure_dir(path: str | os.PathLike) -> str:
    p = pathlib.Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return str(p)

def normspace(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()

def is_content_like(href: str) -> bool:
    href_l = href.lower()
    return "/edit/document" in href_l

def hostname(url: str) -> str:
    return urlparse(url).hostname or ""

# ---------- threading helper to run sync Playwright off the event loop ----------

def _run_in_thread(fn, /, *args, **kwargs):
    """Run blocking Playwright code in a fresh thread.

    Prevents Playwright from detecting an active asyncio loop in the caller (e.g. when
    these tools are invoked from an async MCP host). Returns the function result or
    raises a RuntimeError with the original traceback attached.
    """
    q: Queue = Queue(maxsize=1)

    def runner():
        try:
            q.put(("ok", fn(*args, **kwargs)))
        except Exception as e:  # noqa: BLE001
            q.put(("err", (e, traceback.format_exc())))

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    t.join()
    status, payload = q.get()
    if status == "ok":
        return payload
    err, tb = payload
    raise RuntimeError(f"[threaded] {err}\n{tb}")

# ---------- web storage helpers ----------

def _inject_stored_web_storage(page: Page, base_url: str, profile_dir: str) -> None:
    host = hostname(base_url)
    ls_path = os.path.join(profile_dir, "local_storage.json")
    ss_path = os.path.join(profile_dir, "session_storage.json")
    ls_data: Dict[str, str] = {}
    ss_data: Dict[str, str] = {}
    try:
        if os.path.exists(ls_path):
            with open(ls_path, "r", encoding="utf-8") as f:
                obj = json.load(f)
                if isinstance(obj, dict):
                    ls_data = {str(k): str(v) for k, v in obj.items() if isinstance(k, str) and isinstance(v, str)}
    except Exception as e:  # noqa: BLE001
        eprint(f"[web-storage] Could not read local_storage.json: {e}")
    try:
        if os.path.exists(ss_path):
            with open(ss_path, "r", encoding="utf-8") as f:
                obj = json.load(f)
                if isinstance(obj, dict):
                    ss_data = {str(k): str(v) for k, v in obj.items() if isinstance(k, str) and isinstance(v, str)}
    except Exception as e:  # noqa: BLE001
        eprint(f"[web-storage] Could not read session_storage.json: {e}")

    if not ls_data and not ss_data:
        return

    lines: List[str] = [
        "try {",
        f"  if (location.host.includes('{host}')) {{"
    ]
    for k, v in ls_data.items():
        ek = k.replace("\\", "\\\\").replace("'", "\\'")
        ev = v.replace("\\", "\\\\").replace("'", "\\'")
        lines.append(f"    localStorage.setItem('{ek}','{ev}');")
    for k, v in ss_data.items():
        ek = k.replace("\\", "\\\\").replace("'", "\\'")
        ev = v.replace("\\", "\\\\").replace("'", "\\'")
        lines.append(f"    try {{ sessionStorage.setItem('{ek}','{ev}'); }} catch(e) {{}}")
    lines.append("  }")
    lines.append("} catch(e) {}")
    page.add_init_script("\n".join(lines))

def _write_final_host(profile_dir: str, url: str) -> None:
    try:
        h = hostname(url)
        if h:
            with open(os.path.join(profile_dir, "base_host.txt"), "w", encoding="utf-8") as f:
                f.write(h + "\n")
    except Exception:
        pass

def _read_final_host(profile_dir: str) -> Optional[str]:
    try:
        p = os.path.join(profile_dir, "base_host.txt")
        if os.path.exists(p):
            return pathlib.Path(p).read_text(encoding="utf-8").strip()
    except Exception:
        return None
    return None

def _dump_cookie_brief(cookies: List[Dict[str, object]], target_host: Optional[str] = None) -> List[str]:
    lines: List[str] = []
    now = time.time()
    for c in cookies:
        domain = c.get('domain', '')
        name = c.get('name', '')
        exp = c.get('expires') or c.get('expiry') or None
        if isinstance(exp, (int, float)) and exp > 0:
            ttl = int(exp - now)
            ttl_s = 'expired' if ttl < 0 else f"ttl={ttl//3600}h"
        else:
            ttl_s = 'session'
        if target_host and target_host not in str(domain):
            continue
        secure = 'S' if c.get('secure') else '-'
        http_only = 'H' if c.get('httpOnly') else '-'
        lines.append(f"{domain} | {name} | {ttl_s} | {secure}{http_only}")
    return lines

def _rehydrate_session_cookies(ctx: BrowserContext, profile_dir: str, base_url: str) -> None:
    state_path = os.path.join(profile_dir, "storage_state.json")
    host = hostname(base_url)
    if not os.path.exists(state_path):
        return
    try:
        data = json.loads(pathlib.Path(state_path).read_text(encoding="utf-8"))
        cookies = data.get("cookies", []) if isinstance(data, dict) else []
        if not isinstance(cookies, list) or not cookies:
            return
        current = {(c.get('domain'), c.get('name')) for c in ctx.cookies()}
        needed = []
        for c in cookies:
            if not isinstance(c, dict):
                continue
            dom = c.get('domain')
            name = c.get('name')
            if not dom or not name:
                continue
            if host not in str(dom):
                continue
            if (dom, name) in current:
                continue
            needed.append(c)
        if needed:
            try:
                ctx.add_cookies(needed)  # type: ignore[arg-type]
            except Exception as e:  # noqa: BLE001
                eprint(f"[rehydrate] Could not add cookies: {e}")
    except Exception as e:  # noqa: BLE001
        eprint(f"[rehydrate] Failed reading storage_state.json: {e}")

# ---------- Playwright wrappers ----------

@contextlib.contextmanager
def launch_context(headless: bool, profile_dir: str, tracing: bool = False):
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=ensure_dir(profile_dir),
            headless=headless,
        )
        try:
            if tracing:
                ctx.tracing.start(screenshots=True, snapshots=True, sources=False)
            yield ctx
        finally:
            if tracing:
                with open("trace.zip", "wb") as f:
                    f.write(ctx.tracing.stop())
            ctx.close()

def wait_until_logged_in(page: Page, base_url: str, max_wait_s: int = 180, require_same_host: bool = True, headless: bool = False) -> None:
    start = time.time()
    attempt = 0
    fallback_navigate_after = 15
    fallback_done = False
    base_host = hostname(base_url)
    while time.time() - start < max_wait_s:
        attempt += 1
        page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT)
        try:
            html = page.content()
        except Exception:
            time.sleep(1)
            continue

        def success(_: str):
            return True

        cur_host = hostname(page.url)
        if require_same_host and cur_host and base_host and cur_host != base_host:
            low = html.lower()
            if 'duo' in cur_host or 'duosecurity' in cur_host:
                pass
            elif 'password' in low or 'login' in low:
                pass
            time.sleep(1)
            continue

        if cur_host == base_host and ("BbRouterOutlet" in html) and success("BbRouterOutlet"):
            return
        if cur_host == base_host and ("data-automation-id" in html) and success("data-automation-id attr present"):
            return
        if cur_host == base_host and ("course_id=" in html) and success("course_id pattern"):
            return
        if cur_host == base_host and ("window.publicPath" in html or "publicPath =" in html) and ("BbRouterOutlet" in html or "ultra" in page.url):
            if success("publicPath + ultra context"):
                return
        if cur_host == base_host and ("id=\"main-content-inner\"" in html or "base-courses-container" in html) and success("main content container"):
            return
        if cur_host == base_host and "/ultra/course" in page.url and len(html) > 20_000 and success("ultra/course + large DOM"):
            return
        if cur_host == base_host and ("ultra" in page.url or ('.' in base_host and base_host.split('.')[-2] in page.url)):
            with contextlib.suppress(Exception):
                anchors = page.locator("a").all()
                for a in anchors:
                    try:
                        href = a.get_attribute("href") or ""
                        if "course_id=" in href or "/ultra/course/" in href:
                            return
                    except Exception:
                        pass
        try:
            if cur_host == base_host:
                auth_ping = page.evaluate("() => !!document.cookie && document.cookie.length > 20")
                if auth_ping and len(html) > 18_000:
                    return
        except Exception:
            pass

        if attempt >= fallback_navigate_after and not fallback_done and cur_host == base_host:
            fallback_done = True
            target = urljoin(base_url, "ultra/course")
            if not page.url.rstrip('/').endswith('/ultra/course'):
                with contextlib.suppress(Exception):
                    page.goto(target, wait_until="domcontentloaded")
                    time.sleep(2)
        time.sleep(1)

    raise TimeoutError(
        "Login not detected within timeout. If your SSO uses MFA, keep the window focused and retry."
    )

# ---------- Blackboard helpers ----------

def _extract_courses_from_dom(html: str, base_url: str) -> List[Tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    courses: List[Tuple[str, str]] = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        txt = normspace(a.get_text(" ")) or a.get("title") or ""
        if "course_id=" in href:
            courses.append((txt or "(untitled course)", urljoin(base_url, href)))

    for el in soup.select('[data-automation-id="course-card-title"] a[href]'):
        href = el.get("href", "")
        txt = normspace(el.get_text(" "))
        if href and txt:
            courses.append((txt, urljoin(base_url, href)))

    for card in soup.select('[data-course-id], [data-course-id-short]'):
        course_id = card.get('data-course-id') or card.get('data-course-id-short')
        if course_id:
            txt = normspace(card.get_text(" "))[:200] or course_id
            rel = f"ultra/courses/{course_id}/outline"
            courses.append((txt, urljoin(base_url, rel)))

    seen = set()
    uniq: List[Tuple[str, str]] = []
    for name, url in courses:
        if url not in seen:
            uniq.append((name, url))
            seen.add(url)
    return uniq

def _collect_downloadables_from_course(page: Page, base_url: str) -> List[Tuple[str, str]]:
    html = page.content()
    soup = BeautifulSoup(html, "html.parser")
    items: List[Tuple[str, str]] = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if is_content_like(href):
            title = normspace(a.get_text(" ")) or pathlib.Path(urlparse(href).path).name
            items.append((title, urljoin(base_url, href)))

    for btn in soup.select("button[aria-label*='Download' i]"):
        parent_a = btn.find_parent("a", href=True)
        if parent_a:
            href = parent_a["href"]
            title = btn.get("aria-label", "Download")
            items.append((title, urljoin(base_url, href)))

    seen = set()
    uniq: List[Tuple[str, str]] = []
    for title, href in items:
        if href not in seen:
            uniq.append((title, href))
            seen.add(href)
    return uniq

def _expand_course_sections(page: Page) -> None:
    js_snippet = r"""
        () => {
            let clicked = 0;
            document.querySelectorAll('[aria-expanded="false"][aria-controls]').forEach(el => {
                const controls = el.getAttribute('aria-controls') || '';
                if (controls.includes('folder-contents') || controls.includes('learning-module-contents')) {
                    try { el.click(); clicked++; } catch(e){}
                }
            });
            return clicked;
        }
    """
    for _ in range(500):
        try:
            c = page.evaluate(js_snippet)
            if not isinstance(c, int) or c == 0:
                break
        except Exception:
            break
    with contextlib.suppress(Exception):
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            f_url = frame.url or ""
            if hostname(f_url) and hostname(f_url) != hostname(page.url):
                continue
            with contextlib.suppress(Exception):
                for _ in range(50):
                    c = frame.evaluate(js_snippet)
                    if not isinstance(c, int) or c == 0:
                        break

def _sanitize_filename(name: str) -> str:
    name = name.strip().replace("\0", "")[:180]
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = re.sub(r"[\s_]+", "_", name).strip("._")
    return name or "file"

def _guess_filename_from_url(url: str) -> str:
    parsed = urlparse(url)
    fname = pathlib.Path(parsed.path).name
    if not fname or len(fname) < 3:
        m = re.search(r"(?:file|name|filename)=([^&]+)", parsed.query, flags=re.I)
        if m:
            fname = m.group(1)
    if not fname.lower().endswith('.pdf'):
        fname += '.pdf'
    return _sanitize_filename(fname)

# ---------- Resource helpers ----------

def _safe_download_file_path(name: str) -> str:
    """Return an absolute path inside DOWNLOAD_DIR for the given file name.

    Guards against path traversal. Raises FileNotFoundError if the target does not exist.
    """
    # Strip any directory components
    base = os.path.basename(name.strip())
    # Disallow empty or sneaky names
    if not base or base in {'.', '..'}:
        raise FileNotFoundError("invalid file name")
    target = os.path.abspath(os.path.join(DOWNLOAD_DIR, base))
    root = os.path.abspath(DOWNLOAD_DIR)
    if not target.startswith(root + os.sep) and target != root:
        raise FileNotFoundError("path outside downloads dir")
    if not os.path.isfile(target):
        raise FileNotFoundError(f"file not found: {base}")
    return target

# ---------- generic filename helpers ----------

_FNAME_SAFE_PATTERN = re.compile(r'[^A-Za-z0-9._-]+')

def _sanitize_json_filename(name: str) -> str:
    name = (name or "").strip()
    # Remove any path components
    name = os.path.basename(name)
    if not name:
        name = "script.json"
    # Force .json extension
    if not name.lower().endswith('.json'):
        name += '.json'
    # Replace unsafe chars
    stem, ext = os.path.splitext(name)
    stem = _FNAME_SAFE_PATTERN.sub('_', stem)[:120].strip('._') or 'script'
    return stem + ext

def _unique_path(base_dir: str, filename: str) -> str:
    ensure_dir(base_dir)
    target = os.path.join(base_dir, filename)
    if not os.path.exists(target):
        return target
    stem, ext = os.path.splitext(filename)
    counter = 1
    while True:
        cand = os.path.join(base_dir, f"{stem}_{counter}{ext}")
        if not os.path.exists(cand):
            return cand
        counter += 1

# ---------- MCP tools (profile/out dir fixed; no stay_open) ----------

@mcp.tool("login", description="Establish / refresh authenticated Blackboard Chromium session (persisted profile dir). ONLY call if later tools show unauthenticated content. Base URL hard-coded.")
def tool_login(headless: bool = False) -> dict:
    """Returns diagnostics about the saved session."""
    def _do():
        bu = BASE_URL
        logs: List[str] = []
        with launch_context(headless=headless, profile_dir=PROFILE_DIR) as ctx:
            try:
                existing = ctx.cookies()
                doms = sorted({c['domain'] for c in existing})
                logs.append(f"pre_cookies_domains={doms}")
            except Exception:
                pass
            page = ctx.new_page()
            page.set_default_timeout(DEFAULT_TIMEOUT)
            page.goto(bu, wait_until="domcontentloaded")
            wait_until_logged_in(page, bu, headless=headless)
            _write_final_host(PROFILE_DIR, page.url)
            try:
                ls_dump = page.evaluate("() => { const o={}; try { for (let i=0;i<localStorage.length;i++){ const k=localStorage.key(i); o[k]=localStorage.getItem(k); } } catch(e){} return o; }")
                if isinstance(ls_dump, dict) and ls_dump:
                    with open(os.path.join(PROFILE_DIR, "local_storage.json"), "w", encoding="utf-8") as f:
                        json.dump(ls_dump, f, indent=2, sort_keys=True)
                    logs.append(f"local_storage_keys={len(ls_dump)}")
            except Exception as e:
                logs.append(f"local_storage_error={e!s}")
            try:
                ss_dump = page.evaluate("() => { const o={}; try { for (let i=0;i<sessionStorage.length;i++){ const k=sessionStorage.key(i); o[k]=sessionStorage.getItem(k); } } catch(e){} return o; }")
                if isinstance(ss_dump, dict) and ss_dump:
                    with open(os.path.join(PROFILE_DIR, "session_storage.json"), "w", encoding="utf-8") as f:
                        json.dump(ss_dump, f, indent=2, sort_keys=True)
                    logs.append(f"session_storage_keys={len(ss_dump)}")
            except Exception as e:
                logs.append(f"session_storage_error={e!s}")
            state_path = os.path.join(PROFILE_DIR, "storage_state.json")
            try:
                ctx.storage_state(path=state_path)
                logs.append("storage_state_written=True")
            except Exception as e:
                logs.append(f"storage_state_error={e!s}")
            cookie_brief: List[str] = []
            try:
                cookies = ctx.cookies()
                host_hint = _read_final_host(PROFILE_DIR) or hostname(bu)
                cookie_brief = _dump_cookie_brief(cookies, target_host=host_hint)
            except Exception:
                pass
        return {
            "profile_dir": os.path.abspath(PROFILE_DIR),
            "logs": logs,
            "host_cookie_summary": cookie_brief,
        }
    return _run_in_thread(_do)

@mcp.tool("list_courses", description="List available courses. Base URL hard-coded. First actionable step: pick the first 4 REAL courses after any generic training entry; ignore older archived ones.")
def tool_list_courses(headless: bool = False) -> dict:
    def _do():
        bu = BASE_URL
        with launch_context(headless=headless, profile_dir=PROFILE_DIR) as ctx:
            try:
                existing = ctx.cookies()
                host_hint = _read_final_host(PROFILE_DIR) or hostname(bu)
                _ = _dump_cookie_brief(existing, target_host=host_hint)
            except Exception:
                pass
            _rehydrate_session_cookies(ctx, PROFILE_DIR, bu)
            page = ctx.new_page()
            page.set_default_timeout(DEFAULT_TIMEOUT)
            _inject_stored_web_storage(page, bu, PROFILE_DIR)
            direct_ultra = urljoin(bu, 'ultra/course')
            with contextlib.suppress(Exception):
                page.goto(direct_ultra, wait_until="domcontentloaded")
            with contextlib.suppress(Exception):
                page.goto(bu, wait_until="domcontentloaded")
            wait_until_logged_in(page, bu, headless=headless)
            with contextlib.suppress(Exception):
                page.wait_for_selector('[data-automation-id="course-card-title"] a, a[href*="course_id="]', timeout=5_000)
            time.sleep(1)
            html = page.content()
            courses = _extract_courses_from_dom(html, bu)
            if not courses:
                with contextlib.suppress(Exception):
                    page.goto(direct_ultra, wait_until="domcontentloaded")
                    with contextlib.suppress(Exception):
                        page.wait_for_load_state("networkidle", timeout=5_000)
                    with contextlib.suppress(Exception):
                        page.wait_for_selector('[data-automation-id="course-card-title"] a, a[href*="course_id="]', timeout=5_000)
                    time.sleep(1)
                    html = page.content()
                    courses = _extract_courses_from_dom(html, bu)
            debug_path = os.path.join(PROFILE_DIR, "last_courses_page.html")
            try:
                with open(debug_path, "w", encoding="utf-8") as f:
                    f.write(html)
            except Exception:
                debug_path = None
            return {
                "courses": [{"name": n, "url": u} for n, u in courses],
                "debug": {
                    "current_url": page.url,
                    "html_chars": len(html),
                    "dump_path": debug_path,
                }
            }
    return _run_in_thread(_do)

@mcp.tool("list_content", description="List downloadable content pages for a chosen course (base URL hard-coded).")
def tool_list_content(course_url: str = "", headless: bool = False, expand_sections: bool = True) -> dict:
    def _do():
        bu = BASE_URL
        with launch_context(headless=headless, profile_dir=PROFILE_DIR) as ctx:
            _rehydrate_session_cookies(ctx, PROFILE_DIR, bu)
            page = ctx.new_page()
            page.set_default_timeout(DEFAULT_TIMEOUT)
            _inject_stored_web_storage(page, bu, PROFILE_DIR)
            page.goto(course_url, wait_until="domcontentloaded")
            with contextlib.suppress(Exception):
                page.wait_for_load_state("networkidle", timeout=5_000)
            if expand_sections:
                _expand_course_sections(page)
            items = _collect_downloadables_from_course(page, bu)
            return {"items": [{"title": t, "url": u} for t, u in items]}
    return _run_in_thread(_do)

@mcp.tool("download", description="Download PDF(s) for a specific content page (base URL hard-coded). Works BEST for lecture or workbook style items (skip quizzes/discussions). Fetch only what you need per distinct topic; avoid duplicates.")
def tool_download(content_url: str | None = None, course_url: str | None = None, headless: bool = False) -> dict:
    # Backward compatibility shim
    if content_url is None:
        content_url = course_url
    if not content_url:
        return {"error": "Missing required parameter: content_url (formerly course_url)."}

    deprecated_used = course_url is not None and course_url == content_url

    def _do():
        bu = BASE_URL
        ensure_dir(DOWNLOAD_DIR)
        saved: List[Dict[str, str]] = []
        with launch_context(headless=headless, profile_dir=PROFILE_DIR) as ctx:
            _rehydrate_session_cookies(ctx, PROFILE_DIR, bu)
            page = ctx.new_page()
            page.set_default_timeout(DEFAULT_TIMEOUT)
            _inject_stored_web_storage(page, bu, PROFILE_DIR)
            pdf_urls: set[str] = set()
            page.goto(content_url, wait_until="domcontentloaded")
            with contextlib.suppress(Exception):
                page.wait_for_load_state("networkidle", timeout=5_000)
            html = page.content()
            soup = BeautifulSoup(html, "html.parser")
            for el in soup.find_all(attrs={"data-ally-file-preview-url": True}):
                raw = el.get("data-ally-file-preview-url")
                if not raw:
                    continue
                full = urljoin(bu, raw)
                pdf_urls.add(full)
            if not pdf_urls:
                note = "No data-ally-file-preview-url PDF URLs discovered. Did you pass a content page?"
                resp: Dict[str, object] = {"saved_count": 0, "files": [], "out_dir": DOWNLOAD_DIR, "note": note}
                if deprecated_used:
                    resp["deprecation"] = "Parameter 'course_url' is deprecated; use 'content_url'."
                return resp
            selected: List[str] = sorted(pdf_urls)
            existing_names: set[str] = set()
            client = ctx.request
            for u in selected:
                fname = _guess_filename_from_url(u)
                base_fname = fname
                counter = 1
                while fname.lower() in existing_names or os.path.exists(os.path.join(DOWNLOAD_DIR, fname)):
                    stem, ext = os.path.splitext(base_fname)
                    fname = f"{stem}_{counter}{ext}"
                    counter += 1
                target_path = os.path.join(DOWNLOAD_DIR, fname)
                try:
                    resp = client.get(u, timeout=DEFAULT_TIMEOUT/1000 * 3)  # seconds
                    if resp.ok:
                        data = resp.body()
                        if not data:
                            raise ValueError("empty body")
                        with open(target_path, 'wb') as f:
                            f.write(data)
                        existing_names.add(fname.lower())
                        saved.append({"name": fname, "path": os.path.abspath(target_path), "source_url": u})
                    else:
                        saved.append({"name": fname, "path": os.path.abspath(target_path), "source_url": u, "error": f"HTTP {resp.status}"})
                except Exception as e:
                    saved.append({"name": fname, "path": os.path.abspath(target_path), "source_url": u, "error": str(e)})
        result: Dict[str, object] = {
            "saved_count": len([s for s in saved if "error" not in s]),
            "files": saved,
            "out_dir": DOWNLOAD_DIR,
        }
        if deprecated_used:
            result["deprecation"] = "Parameter 'course_url' is deprecated; use 'content_url'."
        return result
    return _run_in_thread(_do)

# ---------- MCP resources (downloads directory) ----------

@mcp.resource("resource://downloads", title="Downloaded Blackboard Files", description="List of files currently in the downloads directory (produced by the download tool).", mime_type="application/json")
def resource_downloads_index() -> dict:
    ensure_dir(DOWNLOAD_DIR)
    entries: List[Dict[str, object]] = []
    for entry in sorted(os.listdir(DOWNLOAD_DIR)):
        path = os.path.join(DOWNLOAD_DIR, entry)
        if not os.path.isfile(path):
            continue
        try:
            st = os.stat(path)
            entries.append({
                "name": entry,
                "bytes": st.st_size,
                "mtime": int(st.st_mtime),
                "path": path,
            })
        except Exception:
            pass
    return {
        "directory": DOWNLOAD_DIR,
        "count": len(entries),
        "files": entries,
    }

@mcp.resource("resource://downloads/{name}", title="Downloaded File", description="Fetch the raw bytes of a previously downloaded file by name.", mime_type="application/octet-stream")
def resource_downloads_file(name: str) -> bytes:
    path = _safe_download_file_path(name)
    with open(path, 'rb') as f:
        return f.read()

# ---------- New tool: save JSON transcript/video script ----------

@mcp.tool("save_json", description="Persist a grounded video script JSON. Fields: title (3-8 words), description (1-2 sentences citing course/source), dialogue (list of {character,text}). characters allowed: Speaker A, Speaker B. Style rotate: dialog, mentor->learner, monologue, anecdote. Only call AFTER sufficient read_pdf_text evidence (≥400 chars per covered course/topic). No hallucinations.")
def tool_save_json(filename: str, data: Union[Dict[str, Any], List[Any], str], overwrite: bool = False, pretty: bool = True) -> dict:  # type: ignore[valid-type]
    """Persist JSON content under the fixed transcripts directory.

    Parameters:
      filename: Desired file name ('.json' appended if missing). Path traversal is prevented.
      data: The JSON-serializable structure OR a JSON string. If a string is provided we attempt to parse it; on parse failure we wrap it as {"text": <string>}.
      overwrite: If false (default) and a file with that name exists, an incremented suffix is added.
      pretty: Write human-readable indented JSON when True; otherwise compact.

    Returns:
      { "saved": true, "path": <absolute>, "bytes": <int>, "filename": <final name>, "object_type": <type info>, "keys": [...optional keys...] }
    """
    ensure_dir(TRANSCRIPTS_DIR)
    safe_name = _sanitize_json_filename(filename)
    target = os.path.join(TRANSCRIPTS_DIR, safe_name)
    if not overwrite and os.path.exists(target):
        target = _unique_path(TRANSCRIPTS_DIR, safe_name)

    # Helper: attempt to repair common malformed script JSON that was previously double-encoded
    def _try_repair_script_string(raw: str) -> Optional[Any]:
        """Attempt to repair a JSON-ish string where dialogue entries look like
        {"character": "Name": "Utterance"} (colon instead of comma) which caused a
        parse failure and got wrapped inside {"text": "..."} previously.

        Strategy:
          1. Regex replace problematic dialogue objects.
          2. Retry json.loads; if success and result looks like a script (has title & dialogue), return it.
        """
        # Fast exit if obvious keys not present
        if '"dialogue"' not in raw:
            return None
        repaired = raw
        # Replace occurrences inside dialogue array. Pattern: {"character": "X": "Y"}
        pattern = re.compile(r'\{\s*"character"\s*:\s*"([^"]+)"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"\s*\}')
        # Loop until no more replacements (avoid catastrophic backtracking by limiting iterations)
        for _ in range(50):  # safety cap
            new_repaired = pattern.sub(lambda m: '{"character": "%s", "text": "%s"}' % (m.group(1), m.group(2)), repaired)
            if new_repaired == repaired:
                break
            repaired = new_repaired
        try:
            obj = json.loads(repaired)
        except Exception:
            return None
        if isinstance(obj, dict) and 'dialogue' in obj and isinstance(obj.get('dialogue'), list):
            return obj
        return None

    # Normalize data
    obj: Any
    if isinstance(data, (dict, list)):
        obj = data
    elif isinstance(data, str):
        s = data.strip()
        if s:
            try:
                obj = json.loads(s)
            except Exception:
                # Try targeted repair of malformed script structure
                repaired = _try_repair_script_string(s)
                if repaired is not None:
                    obj = repaired
                else:
                    obj = {"text": s}
        else:
            obj = {"text": ""}
    else:
        # Attempt to coerce other primitives
        obj = data

    # Basic guard: ensure JSON serializable
    try:
        json.dumps(obj)
    except TypeError:
        # Fallback: convert to string
        obj = {"error": "Non-serializable object coerced to string", "repr": repr(obj)}

    try:
        with open(target, 'w', encoding='utf-8') as f:
            if pretty:
                json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
            else:
                json.dump(obj, f, ensure_ascii=False, separators=(',', ':'))
            f.write('\n')
    except Exception as e:  # noqa: BLE001
        return {"saved": False, "error": str(e), "filename": os.path.basename(target)}

    size = os.path.getsize(target)
    top_keys: Optional[List[str]] = None
    if isinstance(obj, dict):
        top_keys = list(obj.keys())[:25]
    return {
        "saved": True,
        "filename": os.path.basename(target),
        "path": os.path.abspath(target),
        "bytes": size,
        "object_type": type(obj).__name__,
        "keys": top_keys,
    }

# ---------- New tool: read PDF text ----------

@mcp.tool("read_pdf_text", description="Extract truncated text from a downloaded PDF for grounding. Ensure combined extracted text for a topic ≥400 chars before scripting; if short, download another PDF.")
def tool_read_pdf_text(name: str, max_pages: int = 8, max_chars: int = 10_000) -> dict:
    """Return extracted text from the first N pages of a downloaded PDF.

    Parameters:
      name: Exact filename as listed by resource://downloads (case sensitive)
      max_pages: Limit pages parsed to control latency
      max_chars: Truncate output text length (post extraction)
    """
    path = _safe_download_file_path(name)
    try:
        reader = PdfReader(path)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"open_failed: {e}"}
    pages = min(len(reader.pages), max_pages)
    texts: List[str] = []
    for i in range(pages):
        try:
            page = reader.pages[i]
            txt = page.extract_text() or ""
            if txt:
                texts.append(txt.strip())
        except Exception as e:  # noqa: BLE001
            texts.append(f"[page {i+1} extraction error: {e}]")
    joined = "\n\n".join(texts)
    if len(joined) > max_chars:
        joined = joined[:max_chars] + "\n...[truncated]"
    return {
        "ok": True,
        "name": name,
        "pages_parsed": pages,
        "chars": len(joined),
        "text": joined,
    }

# --- run MCP server over stdio ---
if __name__ == "__main__":
    mcp.run()