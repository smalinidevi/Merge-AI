"""
Vision adapter: the universal fallback that works on anything with no
native adapter — games, images, non-native/scanned PDFs, browser pages,
arbitrary apps. Read-only by construction: there is no "write back into
a screenshot."

  1. capture() — screenshot a window (X11 via Gdk or mss)
  2. describe() — Azure OpenAI (preferred) or Anthropic vision API
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
from typing import Any, Optional

from adapters.base_adapter import BaseAdapter


def _load_dotenv() -> None:
    """Load project .env once if python-dotenv is available."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    load_dotenv(os.path.join(root, ".env"), override=False)


_load_dotenv()


class VisionAdapter(BaseAdapter):
    kind = "vision"
    can_write = False

    def __init__(self, source: str = "active-window"):
        super().__init__(source)
        self._last_image_path: Optional[str] = None
        self._last_description: Optional[str] = None
        self._last_window_id: Optional[str] = None

    # ---------- capture ----------

    def capture(
        self,
        output_path: str = "/tmp/merge_ai_capture.png",
        window_id: Optional[str] = None,
    ) -> str:
        """
        Screenshot one window (default: currently active) and save as PNG.
        Prefer capturing an explicit window_id from the overlay so we don't
        accidentally grab the Merge AI window after the user clicks Merge.
        """
        wid = window_id or self._resolve_active_window_id()
        geo = self._window_geometry(wid)
        os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)

        # Prefer capturing the *window itself* (not a root-desktop crop).
        # Overlapping maximized apps share the same geometry — root crops grab
        # whatever is visually on top (often Cursor), which is the wrong page.
        if self._capture_gdk_window(wid, geo, output_path):
            pass
        elif self._capture_import_window(wid, output_path):
            pass
        elif self._capture_gdk(geo, output_path):
            pass
        elif self._capture_mss(geo, output_path):
            pass
        elif self._capture_import(geo, output_path):
            pass
        else:
            raise RuntimeError(
                "Screenshot failed — need GTK/Gdk, mss, or ImageMagick `import` "
                "on an X11 session."
            )

        self._last_image_path = output_path
        self._last_window_id = wid
        return output_path

    def _resolve_active_window_id(self) -> str:
        binary = self._xdotool_bin()
        if not os.environ.get("DISPLAY"):
            raise RuntimeError("DISPLAY unset — need a live X11 session.")
        return subprocess.check_output(
            [binary, "getactivewindow"], text=True
        ).strip()

    def _xdotool_bin(self) -> str:
        binary = shutil.which("xdotool")
        if binary:
            return binary
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        local = os.path.join(root, ".local", "bin", "xdotool")
        if os.path.isfile(local):
            lib = os.path.join(root, ".local", "lib")
            if os.path.isdir(lib):
                os.environ["LD_LIBRARY_PATH"] = (
                    lib + os.pathsep + os.environ.get("LD_LIBRARY_PATH", "")
                ).rstrip(os.pathsep)
            return local
        raise RuntimeError("xdotool not found.")

    def _window_geometry(self, window_id: str) -> dict:
        binary = self._xdotool_bin()
        raw = subprocess.check_output(
            [binary, "getwindowgeometry", "--shell", window_id],
            text=True,
        )
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
        raw = subprocess.check_output(
            [binary, "getwindowgeometry", window_id], text=True
        )
        pos = re.search(r"Position:\s*(-?\d+),(-?\d+)", raw)
        geo = re.search(r"Geometry:\s*(\d+)x(\d+)", raw)
        if not pos or not geo:
            raise RuntimeError(f"Could not parse geometry for window {window_id}")
        return {
            "x": int(pos.group(1)),
            "y": int(pos.group(2)),
            "width": int(geo.group(1)),
            "height": int(geo.group(2)),
        }

    def _capture_gdk_window(self, window_id: str, geo: dict, output_path: str) -> bool:
        """Capture pixels from the specific X11 window (handles overlaps)."""
        try:
            import gi

            gi.require_version("Gdk", "3.0")
            gi.require_version("GdkX11", "3.0")
            from gi.repository import Gdk, GdkX11
        except (ImportError, ValueError):
            return False
        display = Gdk.Display.get_default()
        if display is None:
            return False
        try:
            xid = int(window_id)
        except ValueError:
            return False
        w = geo.get("width", 0)
        h = geo.get("height", 0)
        if w <= 0 or h <= 0:
            return False
        try:
            gdk_win = GdkX11.X11Window.foreign_new_for_display(display, xid)
        except Exception:
            return False
        if gdk_win is None:
            return False
        pb = Gdk.pixbuf_get_from_window(gdk_win, 0, 0, w, h)
        if pb is None:
            return False
        pb.savev(output_path, "png", [], [])
        return os.path.isfile(output_path) and os.path.getsize(output_path) > 0

    def _capture_import_window(self, window_id: str, output_path: str) -> bool:
        """ImageMagick: import pixels of one window by id."""
        binary = shutil.which("import")
        if not binary:
            return False
        try:
            xid = hex(int(window_id))
        except ValueError:
            xid = window_id
        try:
            subprocess.check_call(
                [binary, "-window", xid, output_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError:
            return False
        return os.path.isfile(output_path) and os.path.getsize(output_path) > 0

    def _capture_gdk(self, geo: dict, output_path: str) -> bool:
        try:
            import gi

            gi.require_version("Gdk", "3.0")
            from gi.repository import Gdk
        except (ImportError, ValueError):
            return False
        root = Gdk.get_default_root_window()
        if root is None:
            return False
        x, y, w, h = geo["x"], geo["y"], geo["width"], geo["height"]
        if w <= 0 or h <= 0:
            return False
        pb = Gdk.pixbuf_get_from_window(root, x, y, w, h)
        if pb is None:
            return False
        pb.savev(output_path, "png", [], [])
        return os.path.isfile(output_path)

    def _capture_mss(self, geo: dict, output_path: str) -> bool:
        try:
            import mss
            from PIL import Image
        except ImportError:
            return False
        monitor = {
            "left": geo["x"],
            "top": geo["y"],
            "width": geo["width"],
            "height": geo["height"],
        }
        with mss.mss() as sct:
            shot = sct.grab(monitor)
            Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX").save(output_path)
        return os.path.isfile(output_path)

    def _capture_import(self, geo: dict, output_path: str) -> bool:
        """ImageMagick `import` fallback."""
        binary = shutil.which("import")
        if not binary:
            return False
        geom = f"{geo['width']}x{geo['height']}+{geo['x']}+{geo['y']}"
        try:
            subprocess.check_call(
                [binary, "-window", "root", "-crop", geom, output_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError:
            return False
        return os.path.isfile(output_path)

    # ---------- describe ----------

    def describe(self, image_path: str, question: str) -> str:
        """
        Prefer Azure OpenAI (DAI_AZURE_OPENAI_* from .env). Falls back to
        Anthropic if ANTHROPIC_API_KEY is set.

        Images are resized/compressed before upload so vision tokens stay
        under the deployment context limit.
        """
        _load_dotenv()
        image_b64, mime = self._encode_image_for_model(image_path)

        endpoint = (
            os.environ.get("DAI_AZURE_OPENAI_ENDPOINT")
            or os.environ.get("AZURE_OPENAI_ENDPOINT")
            or ""
        ).rstrip("/")
        if endpoint:
            description = self._describe_azure(
                image_b64, question, endpoint, mime=mime
            )
        elif os.environ.get("ANTHROPIC_API_KEY"):
            description = self._describe_anthropic(image_b64, question)
        else:
            raise RuntimeError(
                "No vision backend configured. Set DAI_AZURE_OPENAI_ENDPOINT "
                "(+.env Azure AD login or API key) or ANTHROPIC_API_KEY."
            )

        self._last_image_path = image_path
        self._last_description = description
        return description

    def _encode_image_for_model(self, image_path: str) -> tuple[str, str]:
        """Return (base64, mime) with optional downscale to limit vision tokens."""
        try:
            max_side = int(os.environ.get("DAI_VISION_MAX_SIDE", "1280"))
        except ValueError:
            max_side = 1280
        max_side = max(512, min(max_side, 2048))

        try:
            from PIL import Image
            import io

            with Image.open(image_path) as im:
                im = im.convert("RGB")
                w, h = im.size
                scale = min(1.0, float(max_side) / float(max(w, h)))
                if scale < 1.0:
                    im = im.resize(
                        (max(1, int(w * scale)), max(1, int(h * scale))),
                        Image.Resampling.LANCZOS,
                    )
                buf = io.BytesIO()
                im.save(buf, format="JPEG", quality=72, optimize=True)
                return base64.b64encode(buf.getvalue()).decode("utf-8"), "image/jpeg"
        except Exception:
            with open(image_path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8"), "image/png"

    def _azure_auth_headers(self) -> dict:
        api_key = (
            os.environ.get("DAI_AZURE_OPENAI_API_KEY")
            or os.environ.get("AZURE_OPENAI_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )
        if api_key:
            return {"api-key": api_key, "Content-Type": "application/json"}

        scope = os.environ.get(
            "DAI_COGNITIVESERVICES_SCOPE",
            "https://cognitiveservices.azure.com/.default",
        )
        # Prefer fast `az` CLI token locally; then DefaultAzureCredential.
        try:
            out = subprocess.check_output(
                [
                    "az",
                    "account",
                    "get-access-token",
                    "--resource",
                    "https://cognitiveservices.azure.com",
                    "--query",
                    "accessToken",
                    "-o",
                    "tsv",
                ],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=15,
            ).strip()
            if out:
                return {
                    "Authorization": f"Bearer {out}",
                    "Content-Type": "application/json",
                }
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            pass

        try:
            from azure.identity import DefaultAzureCredential

            token = DefaultAzureCredential(
                exclude_interactive_browser_credential=True
            ).get_token(scope)
            return {
                "Authorization": f"Bearer {token.token}",
                "Content-Type": "application/json",
            }
        except Exception as e:
            raise RuntimeError(
                "Azure OpenAI auth failed. Run `az login`, or set "
                "DAI_AZURE_OPENAI_API_KEY in .env."
            ) from e

    def ask_text(self, question: str, context: str = "") -> str:
        """Answer a question from text context via Azure OpenAI (no image)."""
        _load_dotenv()
        endpoint = (
            os.environ.get("DAI_AZURE_OPENAI_ENDPOINT")
            or os.environ.get("AZURE_OPENAI_ENDPOINT")
            or ""
        ).rstrip("/")
        if not endpoint:
            raise RuntimeError("DAI_AZURE_OPENAI_ENDPOINT not set.")
        prompt = question
        if context.strip():
            prompt = (
                "Answer the user's question using ONLY the merged source "
                "content below. Be clear and complete enough to be useful "
                "(short paragraphs or bullets). Obey the question exactly — "
                "do not answer a different question. If the sources lack the "
                "answer, say so. Do not invent data.\n\n"
                f"--- MERGED SOURCES ---\n{context}\n--- END ---\n\n"
                f"Question: {question}"
            )
        else:
            prompt = (
                "Answer clearly and completely. No invented facts.\n\n"
                f"Question: {question}"
            )
        return self._chat_azure(
            endpoint,
            [{"role": "user", "content": prompt}],
            max_completion_tokens=800,
        )

    def _describe_azure(
        self,
        image_b64: str,
        question: str,
        endpoint: str,
        *,
        mime: str = "image/jpeg",
    ) -> str:
        content = [
            {
                "type": "text",
                "text": (
                    "Answer the user's question about this screenshot thoroughly "
                    "but grounded in what is visible. Use short paragraphs or "
                    "bullets as needed. Do not invent details that are not visible. "
                    "Ignore unrelated UI chrome unless asked.\n\n"
                    f"Question: {question}"
                ),
            },
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{image_b64}"},
            },
        ]
        return self._chat_azure(
            endpoint,
            [{"role": "user", "content": content}],
            max_completion_tokens=800,
        )

    def _chat_azure(
        self,
        endpoint: str,
        messages: list,
        max_tokens: int = 512,
        *,
        max_completion_tokens: int | None = None,
    ) -> str:
        """Call Azure chat completions using max_completion_tokens only.

        Caps via DAI_MAX_COMPLETION_TOKENS (default 800) and steps down
        if the deployment rejects the requested size.
        """
        import requests

        deployment = (
            os.environ.get("DAI_AZURE_OPENAI_DEPLOYMENT")
            or os.environ.get("AZURE_OPENAI_DEPLOYMENT")
            or "gpt-4o"
        )
        api_version = (
            os.environ.get("DAI_OPENAI_API_VERSION")
            or os.environ.get("OPENAI_API_VERSION")
            or "2024-10-21"
        )
        try:
            hard_cap = int(os.environ.get("DAI_MAX_COMPLETION_TOKENS", "800"))
        except ValueError:
            hard_cap = 800
        hard_cap = max(64, min(hard_cap, 4096))
        requested = max_completion_tokens if max_completion_tokens is not None else max_tokens
        requested = max(64, min(int(requested or 512), hard_cap))

        url = (
            f"{endpoint}/openai/deployments/{deployment}/chat/completions"
            f"?api-version={api_version}"
        )
        headers = self._azure_auth_headers()

        attempt_levels = []
        for t in (requested, min(512, requested), min(256, requested), 128):
            if t not in attempt_levels:
                attempt_levels.append(t)

        last_err = ""
        for tokens in attempt_levels:
            body = {
                "messages": messages,
                "max_completion_tokens": tokens,
            }
            response = requests.post(url, headers=headers, json=body, timeout=90)
            if response.status_code < 400:
                data = response.json()
                choices = data.get("choices") or []
                if not choices:
                    last_err = f"no choices: {data}"
                    continue
                content = choices[0].get("message", {}).get("content") or ""
                return content if isinstance(content, str) else str(content)

            last_err = f"{response.status_code}: {response.text[:400]}"
            err_l = last_err.lower()
            # Token limit too high → try a smaller max_completion_tokens
            if "token" in err_l and (
                "max" in err_l
                or "limit" in err_l
                or "too large" in err_l
                or "invalid" in err_l
            ):
                continue
            raise RuntimeError(f"Azure OpenAI failed ({last_err})")

        raise RuntimeError(f"Azure OpenAI failed ({last_err})")

    def _describe_anthropic(self, image_b64: str, question: str) -> str:
        import requests

        api_key = os.environ["ANTHROPIC_API_KEY"]
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 1000,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": image_b64,
                        }},
                        {"type": "text", "text": question},
                    ],
                }],
            },
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        text_blocks = [
            b["text"] for b in data.get("content", []) if b.get("type") == "text"
        ]
        return "\n".join(text_blocks)

    # ---------- BaseAdapter contract ----------

    def read_all(self) -> str:
        return self._last_description or "(no capture described yet)"

    def search(self, value: Any) -> list:
        """Best-effort substring check against the last description."""
        if self._last_description and str(value).lower() in self._last_description.lower():
            return ["last_description"]
        return []
