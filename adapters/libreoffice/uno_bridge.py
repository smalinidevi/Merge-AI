"""
Bootstrap PyUNO against a LibreOffice install and talk to open documents.

LibreOffice ships `uno` / `unohelper` (not on pip). We add its `program/`
dir to sys.path and set URE_BOOTSTRAP so system Python can import them.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from typing import Any, Optional
from urllib.parse import unquote, urlparse


DEFAULT_PORT = 2002

_PORTABLE_CANDIDATES = (
    os.path.expanduser("~/libreoffice-portable/opt/libreoffice26.2"),
    os.path.expanduser("~/libreoffice-portable/opt/libreoffice25.2"),
    "/opt/libreoffice26.2",
    "/opt/libreoffice25.8",
    "/usr/lib/libreoffice",
)


def find_libreoffice_program() -> Optional[str]:
    env = os.environ.get("MERGE_AI_LO_PROGRAM") or os.environ.get("LIBREOFFICE_PROGRAM")
    if env and os.path.isdir(env):
        return env
    for root in _PORTABLE_CANDIDATES:
        prog = os.path.join(root, "program") if not root.endswith("program") else root
        if os.path.isfile(os.path.join(prog, "uno.py")):
            return prog
        # /usr/lib/libreoffice/program
        alt = os.path.join(root, "program")
        if os.path.isfile(os.path.join(alt, "uno.py")):
            return alt
    return None


def find_soffice_bin() -> Optional[str]:
    env = os.environ.get("MERGE_AI_SOFFICE")
    if env and os.path.isfile(env):
        return env
    prog = find_libreoffice_program()
    if prog:
        for name in ("soffice", "soffice.bin"):
            path = os.path.join(prog, name)
            if os.path.isfile(path):
                return path
    import shutil

    return shutil.which("soffice") or shutil.which("libreoffice")


def bootstrap_uno() -> None:
    """Make `import uno` work from system Python."""
    if "uno" in sys.modules:
        return
    prog = find_libreoffice_program()
    if not prog:
        raise RuntimeError(
            "LibreOffice program/ with uno.py not found. "
            "Set MERGE_AI_LO_PROGRAM to …/libreoffice*/program"
        )
    os.environ.setdefault(
        "URE_BOOTSTRAP",
        "vnd.sun.star.pathname:" + os.path.join(prog, "fundamentalrc"),
    )
    # LO shared libs must resolve
    os.environ["LD_LIBRARY_PATH"] = (
        prog + os.pathsep + os.environ.get("LD_LIBRARY_PATH", "")
    ).rstrip(os.pathsep)
    if prog not in sys.path:
        sys.path.insert(0, prog)


def port_is_open(host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


def ensure_uno_listening(port: int = DEFAULT_PORT) -> None:
    """Ask the running (or newly started) soffice to listen for UNO."""
    if port_is_open(port=port):
        return
    soffice = find_soffice_bin()
    if not soffice:
        raise RuntimeError("soffice not found — cannot enable live UNO bridge.")
    accept = f"socket,host=127.0.0.1,port={port};urp;"
    subprocess.Popen(
        [soffice, f"--accept={accept}", "--norestore"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(40):
        if port_is_open(port=port):
            return
        time.sleep(0.25)
    raise RuntimeError(
        f"LibreOffice did not open UNO port {port}. "
        "Start Calc/Writer, then retry."
    )


def connect(port: int = DEFAULT_PORT, *, start_if_needed: bool = False):
    """Return (ctx, desktop) connected to the live LibreOffice instance."""
    bootstrap_uno()
    if start_if_needed:
        ensure_uno_listening(port=port)
    elif not port_is_open(port=port):
        raise RuntimeError(
            f"LibreOffice UNO port {port} is not listening. "
            "Open a document in LibreOffice (live bridge auto-enables on edit)."
        )
    import uno  # noqa: WPS433 — after bootstrap

    local = uno.getComponentContext()
    resolver = local.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver", local
    )
    ctx = resolver.resolve(
        f"uno:socket,host=127.0.0.1,port={port};urp;StarOffice.ComponentContext"
    )
    desktop = ctx.ServiceManager.createInstanceWithContext(
        "com.sun.star.frame.Desktop", ctx
    )
    return ctx, desktop

def file_url_to_path(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme != "file":
        return unquote(url)
    path = unquote(parsed.path)
    # Windows-style file:///C:/...
    if os.name == "nt" and path.startswith("/"):
        path = path.lstrip("/")
    return os.path.abspath(path)


def path_to_file_url(path: str) -> str:
    abs_path = os.path.abspath(path)
    return "file://" + abs_path


def iter_components(desktop) -> list[Any]:
    comps = []
    enum = desktop.getComponents().createEnumeration()
    while enum.hasMoreElements():
        comps.append(enum.nextElement())
    return comps


def find_document_for_path(desktop, file_path: str) -> Any:
    """Return the open UNO component whose URL matches file_path, or None."""
    want = os.path.abspath(file_path)
    for doc in iter_components(desktop):
        url = str(getattr(doc, "URL", "") or "")
        if not url:
            continue
        if file_url_to_path(url) == want:
            return doc
    # Basename fallback when path casing / symlink differs
    base = os.path.basename(want).lower()
    for doc in iter_components(desktop):
        url = str(getattr(doc, "URL", "") or "")
        if os.path.basename(file_url_to_path(url)).lower() == base:
            return doc
    return None


def is_calc(doc) -> bool:
    try:
        return doc.supportsService("com.sun.star.sheet.SpreadsheetDocument")
    except Exception:
        return False


def is_writer(doc) -> bool:
    try:
        return doc.supportsService("com.sun.star.text.TextDocument")
    except Exception:
        return False


def is_impress(doc) -> bool:
    try:
        return doc.supportsService("com.sun.star.presentation.PresentationDocument")
    except Exception:
        return False
