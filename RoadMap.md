# Roadmap

## Phase 1 — Core adapters + session model (this repo, mostly done)
- [x] Shared `BaseAdapter` contract + `ChangePreview` confirm-first mechanism
- [x] Adapter registry (extension/kind -> adapter class)
- [x] Excel adapter — full CRUD
- [x] PDF adapter — read + search + page/snippet citations (no in-place edit)
- [x] Word adapter — paragraphs + table cells + insert
- [x] Terminal adapter — confirm-first execution
- [x] Session manager — multi-source, mixed adapter types, banner, staleness
- [x] CLI harness exercising all of the above
- [x] Natural-language edit layer (chat → JSON CRUD ops → ChangePreview confirm → apply)
- [x] Confidence-gated verify/feedback loop (no golden answers; re-plan/re-answer)
- [x] Richer NL routing across mixed adapters (Excel↔PDF fill, citations in gather)

## Phase 2 — Overlay shell (needs a real X11 desktop to build/test)
- [x] `overlay/window_utils.py`: implement `get_active_window()` /
      `get_window_geometry()` for real via xdotool/wmctrl
- [x] `overlay/overlay_app.py`: real GTK widget tree (PyGObject), always-on-top
      pinning, drag/resize
- [x] Wire "Merge" button -> `guess_file_path_for_window()` (or screenshot fallback)
      -> `Session.merge_file()` / `merge_kind()`
- [x] Render `Session.banner_text()` in a GTK label above the chat input
- [x] Confirm dialog for `ChangePreview.apply()` (GTK modal, replacing the
      CLI's `input()` prompt)
- [x] Async Send (worker thread + idle callback; stale-request guard)
- [x] Terminal merge + confirm-first `run_command` from overlay

## Phase 3 — Vision fallback (partially done)
- [x] `VisionAdapter.describe()` — real HTTP call to a vision model
- [x] `VisionAdapter.capture()` — screenshot of a window (Gdk / mss /
      ImageMagick; overlay uses this for unknown apps)
- Universal fallback for anything without a native adapter

## Phase 4 — Remaining adapters
- [x] LibreOffice live-doc adapter (PyUNO) — Calc + Writer paragraph/table ops
- [x] Browser adapter — Chrome MV3 extension + local HTTP bridge (`127.0.0.1:8765`)
- [ ] Windows Excel / Google Sheets bridges (future enhancement)
- [ ] VS Code / Cursor native adapters — **not in scope** (vision screenshot is enough)

## Explicitly out of scope (for now)
- Live PDF viewer blue-box highlight (citations in chat are enough; viewer bridge later)
- Silent system-wide capture (only explicitly merged sources)
- Perfect answers without grounding (rejected — always cite merged sources)

## Cross-cutting, always
- Auto-execute only for read-only/advisory actions
- Confirm-first for anything that writes/deletes/sends/submits/executes —
  no per-adapter exceptions, including terminal
- Only explicitly merged sources are ever read — no silent system-wide capture
- X11 first; Wayland is a later, best-effort addition
