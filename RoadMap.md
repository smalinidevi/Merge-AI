Roadmap

Phase 1 — Core adapters + session model (this repo, mostly done)





Shared BaseAdapter contract + ChangePreview confirm-first mechanism



Adapter registry (extension/kind -> adapter class)



Excel adapter — full CRUD



PDF adapter — read + search



Word adapter — read/write paragraphs



Terminal adapter — confirm-first execution



Session manager — multi-source, mixed adapter types, banner, staleness



CLI harness exercising all of the above



Natural-language edit layer (chat → JSON CRUD ops → ChangePreview confirm → apply)



Richer NL query routing for pure Q&A across mixed adapters



Phase 2 — Overlay shell (needs a real X11 desktop to build/test)





overlay/window_utils.py: implement get_active_window() /

`get_window_geometry()` for real via xdotool/wmctrl



overlay/overlay_app.py: real GTK widget tree (PyGObject), always-on-top

pinning, drag/resize



Wire "Merge" button -> guess_file_path_for_window() (or manual file

picker fallback) -> existing `Session.merge_file()` (no changes needed there)



Render Session.banner_text() in a GTK label above the chat input



Confirm dialog for ChangePreview.apply() (GTK modal, replacing the

CLI's `input()` prompt)



Phase 3 — Vision fallback (partially done)





VisionAdapter.describe() — real HTTP call to a vision model



VisionAdapter.capture() — screenshot of a window (Gdk / mss /

ImageMagick; overlay uses this for browser & non-file Merge)





Universal fallback for anything without a native adapter (games, images,
scanned/non-native PDFs)



Phase 4 — Remaining adapters (each needs an external runtime this repo doesn't have)





LibreOffice live-doc adapter (PyUNO) — edits the open Calc/Writer doc

in place (no File→Reload); auto-enables UNO socket on portable/system LO



Browser adapter — needs a companion Chrome extension + local bridge

server (native messaging host or local WebSocket)



VS Code adapter — via VS Code's own extension API



Cross-cutting, always





Auto-execute only for read-only/advisory actions



Confirm-first for anything that writes/deletes/sends/submits/executes —
no per-adapter exceptions, including terminal



Only explicitly merged sources are ever read — no silent system-wide capture



X11 first; Wayland is a later, best-effort addition

