from __future__ import annotations

import argparse
import contextlib
import json
import os
import pathlib
import re
import sys
import time
from typing import Iterable, List, Tuple, Optional, Dict
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup  # type: ignore
from playwright.sync_api import sync_playwright, BrowserContext, Page  # type: ignore

DEFAULT_PROFILE = os.path.join(os.path.expanduser("~"), ".bb_py_profile")
DEFAULT_TIMEOUT = 30_000  # ms

# ---------- helpers ----------

def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)

def ensure_dir(path: str | os.PathLike) -> str:
    p = pathlib.Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return str(p)

def normspace(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()

def is_download_like(href: str) -> bool:
    """
    Heuristics to decide if an <a href> points to downloadable content.
    Works with classic Blackboard endpoints such as:
      • /bbcswebdav/... (common for file attachments)
      • .../xid-... (Ultra file backing store)
      • .../download?attachment_id=...
    • .../edit/document (Ultra item edit/document pages that often encapsulate a file)
    """
    href_l = href.lower()
    return any(
        token in href_l
        for token in [
            "/bbcswebdav/",
            "xid-",
            "download?attachment_id=",
        "download?",
        "/edit/document"
        ]
    )

def hostname(url: str) -> str:
    return urlparse(url).hostname or ""

# ---------- web storage helpers ----------

def _inject_stored_web_storage(page: Page, base_url: str, profile_dir: str) -> None:
    """Before the first navigation, inject any previously saved localStorage/sessionStorage
    key-value pairs for the Blackboard host. This helps SSO / SP flows that rely on
    ephemeral storage established during the initial interactive login.
    """
    host = hostname(base_url)
    ls_path = os.path.join(profile_dir, "local_storage.json")
    ss_path = os.path.join(profile_dir, "session_storage.json")
    ls_data: Dict[str, str] = {}
    ss_data: Dict[str, str] = {}
    # Load files (ignore errors)
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

    # Build init script; only run on matching host.
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
    eprint(f"[web-storage] Injected {len(ls_data)} localStorage + {len(ss_data)} sessionStorage key(s).")

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
        # Playwright returns -1 or 0 for session cookies; normalize
        if isinstance(exp, (int, float)) and exp > 0:
            ttl = int(exp - now)
            if ttl < 0:
                ttl_s = 'expired'
            else:
                ttl_s = f"ttl={ttl//3600}h"
        else:
            ttl_s = 'session'
        if target_host and target_host not in str(domain):
            continue
        secure = 'S' if c.get('secure') else '-'
        http_only = 'H' if c.get('httpOnly') else '-'
        lines.append(f"{domain} | {name} | {ttl_s} | {secure}{http_only}")
    return lines

def _rehydrate_session_cookies(ctx: BrowserContext, profile_dir: str, base_url: str) -> None:
    """Some institutions mark critical auth cookies as session-only (no expiry) so Chromium
    doesn't persist them to the profile between launches. We captured them in storage_state.json
    after login; re-add them explicitly on subsequent runs so we can skip a fresh SSO flow.
    """
    state_path = os.path.join(profile_dir, "storage_state.json")
    host = hostname(base_url)
    if not os.path.exists(state_path):
        return
    try:
        data = json.loads(pathlib.Path(state_path).read_text(encoding="utf-8"))
        cookies = data.get("cookies", []) if isinstance(data, dict) else []
        if not isinstance(cookies, list) or not cookies:
            return
        # Filter only target host cookies missing currently
        current = { (c.get('domain'), c.get('name')) for c in ctx.cookies() }
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
                continue  # already present (maybe persistent)
            # Playwright add_cookies expects either url or domain/path; we already have domain+path
            needed.append(c)
        if needed:
            try:
                ctx.add_cookies(needed)  # type: ignore[arg-type]
                eprint(f"[rehydrate] Injected {len(needed)} previously saved session cookie(s) for {host}.")
            except Exception as e:  # noqa: BLE001
                eprint(f"[rehydrate] Could not add cookies: {e}")
    except Exception as e:  # noqa: BLE001
        eprint(f"[rehydrate] Failed reading storage_state.json: {e}")

# ---------- Playwright wrappers ----------

@contextlib.contextmanager
def launch_context(headless: bool, profile_dir: str, tracing: bool = False):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=ensure_dir(profile_dir),
            headless=headless,
            # Accepting downloads no longer needed (download command removed), keeping default.
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
    """
    We consider "logged in" when either:
    • We detect a known Blackboard shell element; or
    • We see at least one course link; or
    • The page has "Ultra" shell region present.
    """
    start = time.time()
    attempt = 0
    # After this many attempts without success, proactively visit courses hub.
    fallback_navigate_after = 15  # ~15 seconds
    fallback_done = False
    base_host = hostname(base_url)
    while time.time() - start < max_wait_s:
        attempt += 1
        print(f"[debug] Attempt {attempt}: Current URL: {page.url}")
        page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT)
        try:
            html = page.content()
        except Exception:
            print("[debug] Page still navigating, retrying...")
            time.sleep(1)
            continue
        # Common shells / markers:
        def success(reason: str):
            print(f"[debug] Login heuristic satisfied: {reason}")
            return True

        cur_host = hostname(page.url)
        # If we are NOT yet on the Blackboard base host, we must not claim success.
        if require_same_host and cur_host and base_host and cur_host != base_host:
            low = html.lower()
            # Duo / MFA specific hint
            if 'duo' in cur_host or 'duosecurity' in cur_host:
                print(f"[debug] On Duo/MFA host '{cur_host}', complete MFA in the browser...")
            elif 'password' in low or 'login' in low:
                print(f"[debug] On identity provider host '{cur_host}', waiting for user authentication...")
            time.sleep(1)
            continue  # do NOT evaluate success heuristics yet

        # From here on cur_host == base_host (or host requirement disabled) → evaluate success.
        if cur_host == base_host and ("BbRouterOutlet" in html) and success("BbRouterOutlet"):
            return
        if cur_host == base_host and ("data-automation-id" in html) and success("data-automation-id attr present"):
            return
        if cur_host == base_host and ("course_id=" in html) and success("course_id pattern"):
            return
        # Ultra SPA markers
        if cur_host == base_host and ("window.publicPath" in html or "publicPath =" in html) and ("BbRouterOutlet" in html or "ultra" in page.url):
            if success("publicPath + ultra context"):
                return
        if cur_host == base_host and ("id=\"main-content-inner\"" in html or "base-courses-container" in html) and success("main content container"):
            return
        if cur_host == base_host and "/ultra/course" in page.url and len(html) > 20_000 and success("ultra/course + large DOM"):
            return
        if cur_host == base_host and ("ultra" in page.url or ('.' in base_host and base_host.split('.')[-2] in page.url)):
            print("[debug] URL contains 'ultra' or institution key. Checking for course anchors...")
            with contextlib.suppress(Exception):
                anchors = page.locator("a").all()
                for a in anchors:
                    try:
                        href = a.get_attribute("href") or ""
                        if "course_id=" in href or "/ultra/course/" in href:
                            print(f"[debug] Found direct course anchor: {href}")
                            return
                    except Exception:
                        pass
        try:
            if cur_host == base_host:
                auth_ping = page.evaluate("() => !!document.cookie && document.cookie.length > 20")
                if auth_ping and len(html) > 18_000:
                    print("[debug] Cookie + substantial DOM => treating as logged in.")
                    return
        except Exception:
            pass

        # Fallback navigation if we still haven't matched anything.
        if cur_host != base_host and attempt >= 5 and not headless:
            print("[debug] Still on IdP/MFA. Complete authentication in the browser window (we'll keep waiting)...")
        if attempt >= fallback_navigate_after and not fallback_done and cur_host == base_host:
            fallback_done = True
            target = urljoin(base_url, "ultra/course")
            if not page.url.rstrip('/').endswith('/ultra/course'):
                print(f"[debug] Navigating proactively to courses hub: {target}")
                with contextlib.suppress(Exception):
                    page.goto(target, wait_until="domcontentloaded")
                    time.sleep(2)
        time.sleep(1)

    raise TimeoutError(
        "Login not detected within timeout. If your SSO uses MFA, keep the window focused and retry."
    )

# ---------- Core functions ----------

def cmd_login(base_url: str, profile_dir: str, headless: bool, stay_open: bool = False) -> None:
    eprint(f"[login] Opening browser to {base_url} ...")
    with launch_context(headless=headless, profile_dir=profile_dir) as ctx:
        try:
            existing = ctx.cookies()
            doms = sorted({c['domain'] for c in existing})
            eprint(f"[login] Pre-existing cookie domains: {', '.join(doms) or '(none)'}")
        except Exception:
            pass
        page = ctx.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT)
        page.goto(base_url, wait_until="domcontentloaded")
        wait_until_logged_in(page, base_url, headless=headless)
        # Record final host actually used (may differ if user provided a portal URL that redirected)
        _write_final_host(profile_dir, page.url)
        # Capture localStorage for the base origin (helps some SSO flows that stash state there)
        try:
            ls_dump = page.evaluate("() => { const o={}; try { for (let i=0;i<localStorage.length;i++){ const k=localStorage.key(i); o[k]=localStorage.getItem(k); } } catch(e){} return o; }")
            if isinstance(ls_dump, dict) and ls_dump:
                with open(os.path.join(profile_dir, "local_storage.json"), "w", encoding="utf-8") as f:
                    json.dump(ls_dump, f, indent=2, sort_keys=True)
                eprint(f"[login] Saved localStorage keys: {len(ls_dump)}")
            else:
                eprint("[login] No localStorage keys captured (may not be needed).")
        except Exception as e:  # noqa: BLE001
            eprint(f"[login] Could not capture localStorage: {e}")
        # Capture sessionStorage (ephemeral but sometimes needed to resume SSO-less flows)
        try:
            ss_dump = page.evaluate("() => { const o={}; try { for (let i=0;i<sessionStorage.length;i++){ const k=sessionStorage.key(i); o[k]=sessionStorage.getItem(k); } } catch(e){} return o; }")
            if isinstance(ss_dump, dict) and ss_dump:
                with open(os.path.join(profile_dir, "session_storage.json"), "w", encoding="utf-8") as f:
                    json.dump(ss_dump, f, indent=2, sort_keys=True)
                eprint(f"[login] Saved sessionStorage keys: {len(ss_dump)}")
            else:
                eprint("[login] No sessionStorage keys captured (may not be needed).")
        except Exception as e:  # noqa: BLE001
            eprint(f"[login] Could not capture sessionStorage: {e}")
        # Persisted profile contains cookies/tokens already.
        # Force a storage snapshot for diagnostics (optional, persistent context already writes profile).
        try:
            state_path = os.path.join(profile_dir, "storage_state.json")
            ctx.storage_state(path=state_path)
            eprint(f"[login] Wrote storage_state snapshot → {state_path}")
        except Exception as e:  # noqa: BLE001
            eprint(f"[login] Warning: could not write storage_state.json ({e})")
        # Basic cookie stats
        try:
            cookies = ctx.cookies()
            eprint(f"[login] Captured {len(cookies)} cookie(s).")
            host_hint = _read_final_host(profile_dir) or hostname(base_url)
            brief = _dump_cookie_brief(cookies, target_host=host_hint)
            if brief:
                eprint("[login] Host cookie summary:")
                for line in brief[:12]:  # cap output
                    eprint(f"  {line}")
                if len(brief) > 12:
                    eprint(f"  ... ({len(brief)-12} more)")
        except Exception:
            pass
        eprint(f"[login] Profile dir: {os.path.abspath(profile_dir)}")
        eprint("[login] Looks good — session should persist.")
        if stay_open and not headless:
            eprint("[login] Press Enter here after you confirm you can see your courses (this keeps the browser open).")
            try:
                input()
            except KeyboardInterrupt:
                pass
        eprint("[login] Done.")

def cmd_session_info(profile_dir: str) -> None:
    """Print diagnostic information about the stored Chromium profile (persistent context)."""
    p = pathlib.Path(profile_dir)
    if not p.exists():
        eprint(f"[session-info] Profile directory does not exist: {p}")
        return
    # Gather some stats
    total_files = 0
    total_bytes = 0
    for root, _, files in os.walk(p):
        for f in files:
            total_files += 1
            try:
                total_bytes += (pathlib.Path(root) / f).stat().st_size
            except Exception:
                pass
    eprint(f"[session-info] Profile path: {p.resolve()}")
    eprint(f"[session-info] Files: {total_files}  Size: {total_bytes/1024:.1f} KiB")
    # Look for typical Chromium artifacts
    candidates = [
        p / "Default" / "Network" / "Cookies",
        p / "Default" / "Preferences",
        p / "storage_state.json",
    ]
    for c in candidates:
        if c.exists():
            eprint(f"[session-info] Found: {c.relative_to(p)} ({c.stat().st_size} bytes)")
    # If storage_state exists, show domains summary
    state_file = p / "storage_state.json"
    if state_file.exists():
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            domains = sorted({ck.get('domain','') for ck in data.get('cookies', []) if ck.get('domain')})
            eprint(f"[session-info] storage_state domains: {', '.join(domains) or '(none)'}")
        except Exception as e:  # noqa: BLE001
            eprint(f"[session-info] Could not parse storage_state.json: {e}")

def _extract_courses_from_dom(html: str, base_url: str) -> List[Tuple[str, str]]:
    """
    Best-effort detection of course cards and "Original View" course links.
    Returns list of (course_name, course_url).
    """
    soup = BeautifulSoup(html, "html.parser")
    courses: List[Tuple[str, str]] = []

    # 1) Original View: course links with course_id=
    for a in soup.find_all("a", href=True):
        href = a["href"]
        txt = normspace(a.get_text(" ")) or a.get("title") or ""
        if "course_id=" in href:
            courses.append((txt or "(untitled course)", urljoin(base_url, href)))

    # 2) Ultra cards (very heuristic): data-automation-id="course-card-title"
    for el in soup.select('[data-automation-id="course-card-title"] a[href]'):
        href = el.get("href", "")
        txt = normspace(el.get_text(" "))
        if href and txt:
            courses.append((txt, urljoin(base_url, href)))

    # 3) Some Ultra deployments render cards without nested anchors initially; try data attributes
    for card in soup.select('[data-course-id], [data-course-id-short]'):
        course_id = card.get('data-course-id') or card.get('data-course-id-short')
        if course_id:
            # Attempt to find a text label inside the card
            txt = normspace(card.get_text(" "))[:200] or course_id
            # Ultra outline URL pattern (works for most institutions)
            rel = f"ultra/courses/{course_id}/outline"
            courses.append((txt, urljoin(base_url, rel)))

    # De-dup by URL
    seen = set()
    uniq: List[Tuple[str, str]] = []
    for name, url in courses:
        if url not in seen:
            uniq.append((name, url))
            seen.add(url)
    return uniq

def _fallback_discover_courses_via_api(page: Page, base_url: str) -> List[Tuple[str, str]]:
    """Attempt to use Blackboard public REST endpoints from within the authenticated
    browser context. This handles Ultra pages that lazy-render cards with no <a href> yet.

    Returns list of (name, full_url).
    """
    # We run JS in page context so cookies / auth headers are automatically applied.
    js = r"""
    () => new Promise(async resolve => {
        const tried = [];
        const endpoints = [
            '/learn/api/public/v1/courses?limit=200&offset=0&availability.available=Yes',
            '/learn/api/public/v3/courses?limit=200&offset=0&availability.available=Yes',
            '/learn/api/v1/courses?limit=200&offset=0'
        ];
        for (const ep of endpoints) {
            try {
                const r = await fetch(ep, { credentials: 'include' });
                tried.push({ep, status: r.status});
                if (!r.ok) continue;
                const j = await r.json();
                const arr = j.results || j.data || j.courses || [];
                if (Array.isArray(arr) && arr.length) {
                    const mapped = arr.map(c => ({
                        name: c.name || c.courseName || c.displayName || c.id || c.courseId,
                        id: c.id || c.courseId || c.uuid || c.pk1 || null
                    })).filter(o => o.id);
                    if (mapped.length) {
                        return resolve({ok:true, courses:mapped, tried});
                    }
                }
            } catch (e) { /* ignore */ }
        }
        // As a *last* resort scan inline scripts for course_id patterns
        const ids = new Set();
        const nameMap = new Map();
        for (const script of document.querySelectorAll('script')) {
            const txt = script.textContent || '';
            const re = /(course_id|COURSE_ID)["'=:\s]+([a-z0-9_:-]+)/ig;
            let m;
            while ((m = re.exec(txt))) {
                const cid = m[2];
                if (cid && cid.length < 120) ids.add(cid);
            }
            // Try simple JSON objects with name
            const nameRe = /"name"\s*:\s*"([^"]{3,120})"/g;
            while ((m = nameRe.exec(txt))) {
                const n = m[1];
                if (!nameMap.has(n)) nameMap.set(n,n);
            }
        }
        const list = Array.from(ids).map((id,i) => ({id, name: Array.from(nameMap.keys())[i] || id}));
        resolve({ok:false, courses:list, tried});
    })
    """
    try:
        result = page.evaluate(js)
    except Exception as e:  # noqa: BLE001
        eprint(f"[courses:fallback] JS evaluation failed: {e}")
        return []
    if not isinstance(result, dict):
        return []
    out: List[Tuple[str, str]] = []
    for c in result.get("courses", []):
        cid = c.get("id")
        name = normspace(c.get("name") or cid or "(unnamed course)")
        if not cid:
            continue
        # Construct typical Ultra outline URL
        url = urljoin(base_url, f"ultra/course/{cid}/outline")
        out.append((name, url))
    if out:
        eprint(f"[courses:fallback] collected {len(out)} course(s) via API/heuristics")
    else:
        eprint("[courses:fallback] no courses discovered via API endpoints")
    return out

def cmd_list_courses(base_url: str, profile_dir: str, headless: bool) -> None:
    with launch_context(headless=headless, profile_dir=profile_dir) as ctx:
        try:
            existing = ctx.cookies()
            doms = sorted({c['domain'] for c in existing})
            eprint(f"[list-courses] Pre-existing cookie domains: {', '.join(doms) or '(none)'}")
            host_hint = _read_final_host(profile_dir) or hostname(base_url)
            brief = _dump_cookie_brief(existing, target_host=host_hint)
            if brief:
                eprint("[list-courses] Host cookie summary:")
                for line in brief[:12]:
                    eprint(f"  {line}")
                if len(brief) > 12:
                    eprint(f"  ... ({len(brief)-12} more)")
        except Exception:
            pass
        # Rehydrate any missing session-only cookies
        _rehydrate_session_cookies(ctx, profile_dir, base_url)
        page = ctx.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT)
        # Restore any saved web storage (local + session)
        _inject_stored_web_storage(page, base_url, profile_dir)
        # Try going directly to courses hub first; this sometimes bypasses new SAML initiation
        direct_ultra = urljoin(base_url, 'ultra/course')
        try:
            page.goto(direct_ultra, wait_until="domcontentloaded")
            eprint("[list-courses] Navigated directly to /ultra/course first.")
        except Exception:
            page.goto(base_url, wait_until="domcontentloaded")
        page.goto(base_url, wait_until="domcontentloaded")
        wait_until_logged_in(page, base_url, headless=headless)
        html = page.content()
        courses = _extract_courses_from_dom(html, base_url)
        if not courses:
            eprint("[list-courses] No static anchors found; trying dynamic API discovery...")
            # Navigate explicitly to the Ultra courses hub if user gave a root portal URL
            if not page.url.rstrip('/').endswith('/ultra/course'):
                with contextlib.suppress(Exception):
                    page.goto(urljoin(base_url, 'ultra/course'), wait_until="domcontentloaded")
                    time.sleep(2)
            courses = _fallback_discover_courses_via_api(page, base_url)
        if not courses:
            print("No courses detected on the landing page. Navigate into 'Courses' then re-run list-courses.", file=sys.stderr)
        for name, url in courses:
            print(f"{name}\n  {url}\n")

def _collect_downloadables_from_course(page: Page, base_url: str) -> List[Tuple[str, str]]:
    """
    From the current course page, collect (title, href) pairs that look downloadable.
    """
    html = page.content()
    soup = BeautifulSoup(html, "html.parser")
    items: List[Tuple[str, str]] = []

    # General approach: gather all anchors that *look* like file endpoints
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if is_download_like(href):
            title = normspace(a.get_text(" ")) or pathlib.Path(urlparse(href).path).name
            items.append((title, urljoin(base_url, href)))

    # Some Ultra pages render item cards where the file link is nested:
    # try finding buttons with download aria-labels.
    for btn in soup.select("button[aria-label*='Download' i]"):
        parent_a = btn.find_parent("a", href=True)
        if parent_a:
            href = parent_a["href"]
            title = btn.get("aria-label", "Download")
            items.append((title, urljoin(base_url, href)))

    # De-dup by (href)
    seen = set()
    uniq: List[Tuple[str, str]] = []
    for title, href in items:
        if href not in seen:
            uniq.append((title, href))
            seen.add(href)
    return uniq

def _expand_course_sections(page: Page, rounds: int = 12) -> None:
    """Expand ALL expandable elements we can find in one straightforward sweep.

    Simplified behavior (per user request):
    - Click every element with aria-expanded="false".
    - Open all <details> that are not yet open.
    - Click any button/anchor/summary whose label/text suggests it expands content
      (expand/show/more/folder/module/section).
    - Repeat until a pass produces 0 new clicks or we hit a safety cap.
    - Also apply the same inside any same‑origin iframes.
    """
    js_snippet = r"""
        () => {
            let clicked = 0;
            // 1. aria-expanded="false"
            document.querySelectorAll('[aria-expanded="false"][aria-controls*="folder-contents"]').forEach(el => {
                try { el.click(); clicked++; } catch(e){}
            });
            return clicked;
        }
    """
    # Main page loop
    for _ in range(50):  # safety cap
        try:
            c = page.evaluate(js_snippet)
            if not isinstance(c, int) or c == 0:
                pass
            time.sleep(0.2)
        except Exception:
            pass
    # Same-origin iframes
    with contextlib.suppress(Exception):
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            f_url = frame.url or ""
            if hostname(f_url) and hostname(f_url) != hostname(page.url):  # cross-origin -> skip
                continue
            with contextlib.suppress(Exception):
                for _ in range(50):
                    c = frame.evaluate(js_snippet)
                    if not isinstance(c, int) or c == 0:
                        pass
                    time.sleep(0.05)

def cmd_list_content(base_url: str, profile_dir: str, course_url: str, headless: bool) -> None:
    with launch_context(headless=headless, profile_dir=profile_dir) as ctx:
        _rehydrate_session_cookies(ctx, profile_dir, base_url)
        page = ctx.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT)
        _inject_stored_web_storage(page, base_url, profile_dir)
        page.goto(course_url, wait_until="domcontentloaded")
        # Give any lazy content a moment to render
        with contextlib.suppress(Exception):
            page.wait_for_load_state("networkidle", timeout=5_000)
        # Expand collapsible sections to surface hidden file links
        _expand_course_sections(page)
        items = _collect_downloadables_from_course(page, base_url)
        for title, href in items:
            print(f"{title}\n  {href}\n")
        if not items:
            print("No obvious downloadable items found. Try expanding content sections or switching to 'Original View' content areas and re-run.", file=sys.stderr)

def _matches(name: str, pattern: Optional[str], regex: Optional[str]) -> bool:
    if pattern:
        # Simple glob-like match: convert * -> .* and ? -> .
        pat = "^" + re.escape(pattern).replace("\*", ".*").replace("\?", ".") + "$"
        return re.search(pat, name, flags=re.IGNORECASE) is not None
    if regex:
        return re.search(regex, name, flags=re.IGNORECASE) is not None
    return True

def _sanitize_filename(name: str) -> str:
    name = name.strip().replace("\0", "")[:180]
    # Remove characters not allowed in Windows filenames
    # Pattern chars: < > : \ " / | ? *
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    # Collapse whitespace / underscores
    name = re.sub(r"[\s_]+", "_", name).strip("._")
    return name or "file"

def _guess_filename_from_url(url: str) -> str:
    parsed = urlparse(url)
    fname = pathlib.Path(parsed.path).name
    if not fname or len(fname) < 3:
        # Try query parameters
        m = re.search(r"(?:file|name|filename)=([^&]+)", parsed.query, flags=re.I)
        if m:
            fname = m.group(1)
    if not fname.lower().endswith('.pdf'):
        fname += '.pdf'
    return _sanitize_filename(fname)

def cmd_download(base_url: str, profile_dir: str, course_url: str, out_dir: str, headless: bool, pattern: Optional[str], regex: Optional[str]) -> None:
    """Download (currently PDF-focused) resources from a course page.

    Behavior:
    - Navigates to course_url using stored session (cookies + web storage rehydrated).
    - DOES NOT expand sections (per user request).
    - Collects only elements bearing the attribute data-ally-file-preview-url and treats the value as the PDF URL.
    - Filters by --pattern or --regex if supplied.
    - Streams each PDF via Playwright's authenticated request context → writes to out_dir.
    """
    ensure_dir(out_dir)
    with launch_context(headless=headless, profile_dir=profile_dir) as ctx:
        _rehydrate_session_cookies(ctx, profile_dir, base_url)
        page = ctx.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT)
        _inject_stored_web_storage(page, base_url, profile_dir)
        pdf_urls: set[str] = set()
        page.goto(course_url, wait_until="domcontentloaded")
        with contextlib.suppress(Exception):
            page.wait_for_load_state("networkidle", timeout=5_000)
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        # Find any element with data-ally-file-preview-url attribute
        for el in soup.find_all(attrs={"data-ally-file-preview-url": True}):
            raw = el.get("data-ally-file-preview-url")
            if not raw:
                continue
            full = urljoin(base_url, raw)
            pdf_urls.add(full)

        if not pdf_urls:
            eprint("[download] No data-ally-file-preview-url PDF URLs discovered.")
            return

        # Apply filters & dedupe
        selected: List[str] = []
        for u in sorted(pdf_urls):
            fname = _guess_filename_from_url(u)
            if _matches(fname, pattern, regex):
                selected.append(u)
        if not selected:
            eprint("[download] No PDFs matched the provided pattern/regex.")
            return

        existing_names: set[str] = set()
        success = 0
        client = ctx.request
        for idx, u in enumerate(selected, start=1):
            fname = _guess_filename_from_url(u)
            base_fname = fname
            # Avoid overwriting duplicates
            counter = 1
            while fname.lower() in existing_names or os.path.exists(os.path.join(out_dir, fname)):
                stem, ext = os.path.splitext(base_fname)
                fname = f"{stem}_{counter}{ext}"
                counter += 1
            target_path = os.path.join(out_dir, fname)
            eprint(f"[download] ({idx}/{len(selected)}) GET {u} -> {fname}")
            try:
                resp = client.get(u, timeout=DEFAULT_TIMEOUT/1000 * 3)  # seconds
                if resp.ok:
                    data = resp.body()
                    if not data:
                        raise ValueError("empty body")
                    with open(target_path, 'wb') as f:
                        f.write(data)
                    success += 1
                    existing_names.add(fname.lower())
                else:
                    eprint(f"[download]   ! HTTP {resp.status} {u}")
            except Exception as e:  # noqa: BLE001
                eprint(f"[download]   ! Failed: {e}")
        eprint(f"[download] Done. Saved {success}/{len(selected)} PDF(s) to {os.path.abspath(out_dir)}")

# ---------- CLI ----------

def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="bb_dat_acq",
    )
    p.add_argument("--base-url", required=True, help="Your Blackboard landing URL (e.g., https://learn.uq.edu.au/).")
    p.add_argument("--profile-dir", default=DEFAULT_PROFILE, help=f"Where to store the browser profile (default: {DEFAULT_PROFILE!r}).")
    p.add_argument("--headless", action="store_true", help="Run browser headless (only if already logged in).")

    sub = p.add_subparsers(dest="cmd", required=True)

    sp_login = sub.add_parser("login", help="Open a browser window and log in. Session is cached in --profile-dir.")
    sp_login.add_argument("--stay-open", action="store_true", help="Keep the login browser open until you press Enter (interactive only).")

    sub.add_parser("list-courses", help="List courses detected on the landing page.")

    sub.add_parser("session-info", help="Show diagnostic info about the saved browser session profile.")

    sp_list = sub.add_parser("list-content", help="List downloadable items within a given course.")
    sp_list.add_argument("--course-url", required=True, help="Open a specific course URL (from list-courses output).")

    sp_dl = sub.add_parser("download", help="Download items from a course with optional filters.")
    sp_dl.add_argument("--course-url", required=True, help="Course URL to open (from list-courses output).")
    sp_dl.add_argument("--out", default='downloads', required=False, help="Folder to save downloads.")
    g = sp_dl.add_mutually_exclusive_group()
    g.add_argument("--pattern", help="Filename glob-like pattern (e.g., *.pdf).")
    g.add_argument("--regex", help="Python regex to match filenames.")

    args = p.parse_args(argv)

    base_url = args.base_url.rstrip("/") + "/"
    if args.cmd == "login":
        cmd_login(base_url, args.profile_dir, args.headless, getattr(args, "stay_open", False))
    elif args.cmd == "list-courses":
        cmd_list_courses(base_url, args.profile_dir, args.headless)
    elif args.cmd == "session-info":
        cmd_session_info(args.profile_dir)
    elif args.cmd == "list-content":
        cmd_list_content(base_url, args.profile_dir, args.course_url, args.headless)
    elif args.cmd == "download":
        cmd_download(
            base_url,
            args.profile_dir,
            args.course_url,
            args.out,
            args.headless,
            getattr(args, 'pattern', None),
            getattr(args, 'regex', None),
        )
    else:
        p.print_help()
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
