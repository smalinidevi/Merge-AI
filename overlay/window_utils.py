"""
Thin wrappers around xdotool/wmctrl for X11 active-window detection.
Requires: a running X11 session (echo $XDG_SESSION_TYPE == "x11"),
and the `xdotool` and `wmctrl` binaries installed.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional

from adapters.registry import supported_extensions


@dataclass
class ActiveWindowInfo:
    window_id: str
    title: str
    window_class: str


class WindowUtilsError(RuntimeError):
    """Raised when X11 / xdotool prerequisites are missing or a query fails."""


# Window classes / title fragments that usually mean a spreadsheet or document app.
# Document apps (Excel/Calc/Word/Writer/Impress/PowerPoint/text editors)
_DOC_CLASS_HINTS = (
    "calc",
    "soffice",
    "libreoffice",
    "excel",
    "et",  # WPS Spreadsheets
    "wps",
    "onlyoffice",
    "gnumeric",
    "writer",
    "impress",
    "evince",
    "atril",
    "okular",
    "foxit",
    "pdf",
    "word",
    "winword",
    "powerpnt",
    "powerpoint",
)


def _ensure_x11_env() -> None:
    if not os.environ.get("DISPLAY"):
        raise WindowUtilsError(
            "DISPLAY is unset — need a live X11 session. "
            "Check: echo $XDG_SESSION_TYPE (expect x11)."
        )


def _require_binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        # Prefer project-local binaries from scripts/run_overlay.sh packaging.
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        local = os.path.join(root, ".local", "bin", name)
        if os.path.isfile(local) and os.access(local, os.X_OK):
            lib_dir = os.path.join(root, ".local", "lib")
            if os.path.isdir(lib_dir):
                os.environ["LD_LIBRARY_PATH"] = (
                    lib_dir + os.pathsep + os.environ.get("LD_LIBRARY_PATH", "")
                ).rstrip(os.pathsep)
            return local
        raise WindowUtilsError(
            f"'{name}' not found on PATH. Install with: "
            f"sudo apt-get install -y xdotool wmctrl"
        )
    return path


def _xdotool(*args: str) -> str:
    _ensure_x11_env()
    binary = _require_binary("xdotool")
    try:
        out = subprocess.check_output(
            [binary, *args],
            stderr=subprocess.STDOUT,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        detail = (e.output or "").strip() or str(e)
        raise WindowUtilsError(f"xdotool {' '.join(args)} failed: {detail}") from e
    return out.strip()


def get_active_window() -> ActiveWindowInfo:
    """Return the currently focused X11 window via xdotool."""
    window_id = _xdotool("getactivewindow")
    title = _xdotool("getwindowname", window_id)
    try:
        window_class = _xdotool("getwindowclassname", window_id)
    except WindowUtilsError:
        # Older xdotool builds may lack getwindowclassname; fall back to wmctrl.
        window_class = _wmctrl_class(window_id) or ""
    return ActiveWindowInfo(window_id=window_id, title=title, window_class=window_class)


def get_window_pid(window_id: str) -> Optional[int]:
    """Return the PID owning the window, or None if unavailable."""
    try:
        raw = _xdotool("getwindowpid", window_id)
        return int(raw.strip())
    except (WindowUtilsError, ValueError):
        return None


def _wmctrl_class(window_id: str) -> Optional[str]:
    """Best-effort WM_CLASS lookup via `wmctrl -lx` (hex window ids)."""
    try:
        binary = _require_binary("wmctrl")
        _ensure_x11_env()
    except WindowUtilsError:
        return None
    try:
        listing = subprocess.check_output(
            [binary, "-lx"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

    try:
        target = int(window_id)
    except ValueError:
        return None

    for line in listing.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 3:
            continue
        try:
            wid = int(parts[0], 16)
        except ValueError:
            continue
        if wid == target:
            return parts[2]
    return None


def get_window_geometry(window_id: str) -> dict:
    """
    Return {"x", "y", "width", "height"} for the given window id.
    Parses `xdotool getwindowgeometry` output.
    """
    raw = _xdotool("getwindowgeometry", "--shell", window_id)
    # --shell prints: WINDOW=... X=... Y=... WIDTH=... HEIGHT=... SCREEN=...
    vals: dict[str, int] = {}
    for line in raw.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            try:
                vals[key.strip().upper()] = int(value.strip())
            except ValueError:
                continue
    if all(k in vals for k in ("X", "Y", "WIDTH", "HEIGHT")):
        return {
            "x": vals["X"],
            "y": vals["Y"],
            "width": vals["WIDTH"],
            "height": vals["HEIGHT"],
        }

    # Fallback for older xdotool without useful --shell output
    raw = _xdotool("getwindowgeometry", window_id)
    pos = re.search(r"Position:\s*(-?\d+),(-?\d+)", raw)
    geo = re.search(r"Geometry:\s*(\d+)x(\d+)", raw)
    if not pos or not geo:
        raise WindowUtilsError(f"Could not parse geometry for window {window_id}:\n{raw}")
    return {
        "x": int(pos.group(1)),
        "y": int(pos.group(2)),
        "width": int(geo.group(1)),
        "height": int(geo.group(2)),
    }


_BROWSER_CLASS_HINTS = (
    "chrome",
    "chromium",
    "firefox",
    "brave",
    "opera",
    "vivaldi",
    "msedge",
    "edge",
    "navigator",  # Firefox WM_CLASS
    "google-chrome",
    "brave-browser",
)

_TERMINAL_CLASS_HINTS = (
    "terminal",
    "konsole",
    "xterm",
    "alacritty",
    "kitty",
    "wezterm",
    "tilix",
    "gnome-terminal",
)


def is_document_window(info: ActiveWindowInfo) -> bool:
    """True if the window looks like Excel/Calc/Word/PDF/etc."""
    blob = f"{info.window_class} {info.title}".lower()
    if any(hint in blob for hint in _DOC_CLASS_HINTS):
        return True
    # Title contains a supported file extension
    for ext in supported_extensions():
        if ext in blob:
            return True
    return False


def is_browser_window(info: ActiveWindowInfo) -> bool:
    blob = f"{info.window_class} {info.title}".lower()
    return any(hint in blob for hint in _BROWSER_CLASS_HINTS)


def is_terminal_window(info: ActiveWindowInfo) -> bool:
    blob = f"{info.window_class} {info.title}".lower()
    return any(hint in blob for hint in _TERMINAL_CLASS_HINTS)


def is_overlay_window(info: ActiveWindowInfo) -> bool:
    """True if this is the Merge AI overlay itself — never a merge target."""
    blob = f"{info.window_class} {info.title}".lower()
    if info.title.strip() == "Merge AI":
        return True
    if "main.py" in info.window_class.lower() and "merge" in blob:
        return True
    return False


def classify_window(info: ActiveWindowInfo) -> str:
    """
    How Merge should attach to this window:
      - "file"     → resolve path + file adapter (Excel/Word/PDF)
      - "browser"  → screenshot + vision (webpage)
      - "terminal" → screenshot + vision (or terminal adapter later)
      - "vision"   → screenshot + vision for anything else
      - "self"     → Merge AI overlay (ignore)
    """
    if is_overlay_window(info):
        return "self"
    if is_document_window(info):
        return "file"
    if is_browser_window(info):
        return "browser"
    if is_terminal_window(info):
        return "terminal"
    return "vision"


def friendly_window_name(info: ActiveWindowInfo) -> str:
    """Short human label for a screenshot merge (not the raw WM title).

    "vision" is only the capture method — users see names like Cursor window,
    Chrome, Edge. Document file merges keep their basename separately.
    """
    cls = (info.window_class or "").lower()
    title = (info.title or "").strip()
    blob = f"{cls} {title}".lower()

    # IDEs / editors
    if "cursor" in blob:
        return "Cursor window"
    if "code" == cls.split(".")[-1] or "visual studio code" in blob or "vscode" in blob:
        return "VS Code"

    # Browsers (check Edge before generic "chrome"/"edge" noise in titles)
    if "msedge" in blob or "microsoft-edge" in blob or re.search(
        r"(^|[\s.])edge([\s.]|$)", cls
    ):
        return "Edge"
    if "brave" in blob:
        return "Brave"
    if "firefox" in blob or "navigator" in cls:
        return "Firefox"
    if "opera" in blob:
        return "Opera"
    if "vivaldi" in blob:
        return "Vivaldi"
    if "chromium" in blob or "google-chrome" in blob or "chrome" in cls:
        return "Chrome"

    # File managers / folders
    if any(
        h in blob
        for h in (
            "nautilus",
            "org.gnome.nautilus",
            "org.gnome.files",
            "dolphin",
            "thunar",
            "nemo",
            "caja",
            "pcmanfm",
        )
    ) or cls.endswith(".files") or cls == "files":
        folder = title
        for sep in _TITLE_SEPARATORS:
            if sep in folder:
                folder = folder.split(sep, 1)[0].strip()
                break
        folder = _strip_dirty_marker(folder)
        if folder and folder.lower() not in ("files", "home", "file manager"):
            if len(folder) > 40:
                folder = folder[:37] + "…"
            return folder
        return "Files"

    # Terminals
    if is_terminal_window(info):
        return "Terminal"

    # Fallback: last WM_CLASS segment, title-cased (e.g. slack → Slack)
    short = (info.window_class or "").split(".")[-1].strip()
    if short and short.lower() not in ("main", "python", "python3"):
        nice = re.sub(r"[-_]+", " ", short).strip()
        if nice:
            return nice[:1].upper() + nice[1:] if len(nice) > 1 else nice.upper()

    if title:
        for sep in _TITLE_SEPARATORS:
            if sep in title:
                left = _strip_dirty_marker(title.split(sep, 1)[0])
                if left:
                    return left[:40] + ("…" if len(left) > 40 else "")
        cleaned = _strip_dirty_marker(title)
        return cleaned[:40] + ("…" if len(cleaned) > 40 else "")

    return "Window"


_TITLE_SEPARATORS = (" — ", " – ", " - ", " | ", " · ")


def _strip_dirty_marker(name: str) -> str:
    """Remove unsaved markers like ' *' / '[*]' from window titles."""
    name = name.strip()
    name = re.sub(r"\s+\*$", "", name)
    name = re.sub(r"\s*\[\*\]\s*$", "", name)
    return name.strip()


def _candidate_tokens_from_title(title: str) -> list[str]:
    """Pull plausible file-name / path fragments out of a window title."""
    tokens: list[str] = []
    if not title or not title.strip():
        return tokens

    cleaned = _strip_dirty_marker(title.strip())
    tokens.append(cleaned)

    for sep in _TITLE_SEPARATORS:
        if sep in cleaned:
            left, right = cleaned.split(sep, 1)
            tokens.append(_strip_dirty_marker(left))
            tokens.append(_strip_dirty_marker(right))

    # Bracketed names: [report.pdf], (notes.docx)
    for match in re.finditer(r"[\[(]([^\[\])]+?\.\w{2,5})[\])]", cleaned):
        tokens.append(_strip_dirty_marker(match.group(1)))

    # Any path-like substring ending in a supported extension
    ext_alt = "|".join(re.escape(e.lstrip(".")) for e in supported_extensions())
    if ext_alt:
        for match in re.finditer(
            rf"(?:~|/)?[\w./\\ -]+\.(?:{ext_alt})\b",
            cleaned,
            flags=re.IGNORECASE,
        ):
            tokens.append(_strip_dirty_marker(match.group(0)))

    # Dedupe while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for t in tokens:
        if t and t not in seen:
            seen.add(t)
            unique.append(t)
    return unique


def _has_supported_extension(path: str) -> bool:
    _, ext = os.path.splitext(path)
    return ext.lower() in supported_extensions()


def _resolve_existing_path(candidate: str, extra_dirs: Optional[list[str]] = None) -> Optional[str]:
    """Return an absolute path if candidate resolves to an existing file."""
    expanded = os.path.expanduser(candidate.strip().strip("\"'"))
    if not expanded or not _has_supported_extension(expanded):
        return None

    if os.path.isabs(expanded) and os.path.isfile(expanded):
        return os.path.abspath(expanded)

    home = os.path.expanduser("~")
    search_dirs = [
        os.getcwd(),
        home,
        os.path.join(home, "Desktop"),
        os.path.join(home, "Documents"),
        os.path.join(home, "Downloads"),
    ]
    if extra_dirs:
        search_dirs = list(extra_dirs) + search_dirs

    base = os.path.basename(expanded)
    seen_dirs: set[str] = set()
    for directory in search_dirs:
        if not directory or directory in seen_dirs:
            continue
        seen_dirs.add(directory)
        # Relative path as given
        joined = os.path.join(directory, expanded)
        if os.path.isfile(joined):
            return os.path.abspath(joined)
        # Basename only
        joined = os.path.join(directory, base)
        if os.path.isfile(joined):
            return os.path.abspath(joined)

    # Shallow search under Desktop/* (one level) for basename — LibreOffice
    # often only puts the filename in the title, not the folder.
    if _has_supported_extension(base):
        desktop = os.path.join(home, "Desktop")
        if os.path.isdir(desktop):
            try:
                for entry in os.listdir(desktop):
                    folder = os.path.join(desktop, entry)
                    if not os.path.isdir(folder):
                        continue
                    candidate = os.path.join(folder, base)
                    if os.path.isfile(candidate):
                        return os.path.abspath(candidate)
            except OSError:
                pass

    return None


def _proc_cwd(pid: int) -> Optional[str]:
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return None


def _open_document_paths_for_pid(pid: int) -> list[str]:
    """List supported document files currently open by the process (via /proc/fd)."""
    fd_dir = f"/proc/{pid}/fd"
    found: list[str] = []
    try:
        names = os.listdir(fd_dir)
    except OSError:
        return found

    for name in names:
        try:
            target = os.readlink(os.path.join(fd_dir, name))
        except OSError:
            continue
        if not target.startswith("/"):
            continue
        # Skip deleted / pipe / socket markers
        if " (deleted)" in target:
            target = target.replace(" (deleted)", "")
        if os.path.isfile(target) and _has_supported_extension(target):
            abs_path = os.path.abspath(target)
            if abs_path not in found:
                found.append(abs_path)
    return found


def guess_file_path_for_window(info: ActiveWindowInfo) -> Optional[str]:
    """
    Best-effort path for the document shown in a window.

    Order:
      1. Title heuristics (LibreOffice / Excel / PDF viewer style titles)
      2. Files open by the window's process (/proc/<pid>/fd) — critical for Excel/Calc
      3. Basename from title resolved against the process cwd
    """
    pid = None
    try:
        if info.window_id and info.window_id.isdigit():
            pid = get_window_pid(info.window_id)
    except WindowUtilsError:
        pid = None

    cwd = _proc_cwd(pid) if pid else None
    extra_dirs = [cwd] if cwd else []

    title_candidates: list[str] = []
    for token in _candidate_tokens_from_title(info.title):
        resolved = _resolve_existing_path(token, extra_dirs=extra_dirs)
        if resolved:
            return resolved
        title_candidates.append(os.path.basename(token.strip().strip("\"'")))

    # Process open-file descriptors (Excel / LibreOffice often keep the .xlsx open)
    if pid:
        open_docs = _open_document_paths_for_pid(pid)
        if open_docs:
            # Prefer a file whose basename appears in the window title
            title_l = info.title.lower()
            for path in open_docs:
                if os.path.basename(path).lower() in title_l:
                    return path
            # Prefer spreadsheets if the window looks like Excel/Calc
            blob = f"{info.window_class} {info.title}".lower()
            if any(h in blob for h in ("calc", "excel", "et", "gnumeric", "spreadsheet")):
                for path in open_docs:
                    if path.lower().endswith((".xlsx", ".xlsm")):
                        return path
            return open_docs[0]

    # Last resort: basename from title + process cwd
    for base in title_candidates:
        if not base or not _has_supported_extension(base):
            continue
        resolved = _resolve_existing_path(base, extra_dirs=extra_dirs)
        if resolved:
            return resolved

    return None
