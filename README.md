# Merge AI

**Merge AI** is a personal, general-purpose AI overlay assistant for Linux (X11).  
It “merges” with whatever you are working on — spreadsheets, Word docs, PowerPoint, plain text, PDFs, browsers, IDEs, folders, terminals — and lets you:

- Ask questions about merged content (files + screenshots)
- Apply natural-language **CRUD edits** to editable documents
- Prefer **live in-app edits** via LibreOffice UNO (no File → Reload) when the file is open
- Fall back to screenshot + vision for anything without a native file adapter

Multiple sources can live in one chat. Each chat is independent; **Merge** stacks sources into the current chat; **+ New chat** starts fresh.

---

## Table of contents

1. [What it does](#what-it-does)
2. [Technologies](#technologies)
3. [HLD — High Level Design](#hld--high-level-design)
4. [LLD — Low Level Design](#lld--low-level-design)
5. [Architecture](#architecture)
6. [Adapters](#adapters)
7. [Confirm-first writes](#confirm-first-writes)
8. [Live LibreOffice editing (no reload)](#live-libreoffice-editing-no-reload)
9. [Overlay UX](#overlay-ux)
10. [Natural-language edit engine](#natural-language-edit-engine)
11. [Install](#install)
12. [Configuration (`.env`)](#configuration-env)
13. [Quick start](#quick-start)
14. [CLI reference](#cli-reference)
15. [Tests](#tests)
16. [Project layout](#project-layout)
17. [Design rules](#design-rules)
18. [Troubleshooting](#troubleshooting)
19. [Roadmap](#roadmap)

---

## What it does

| Capability | How it works |
|---|---|
| **Merge a document** | Focus Excel/Calc/Word/Writer/PPT/text → click **Merge**. Path is guessed from the window title / process; file is opened with the matching adapter. |
| **Merge anything else** | Cursor, Chrome, Edge, folders, terminals, unknown apps → **screenshot** of that window + vision Q&A. No file picker. |
| **Ask questions** | Type in chat + **Send**. Answers use merged file text and/or the latest screenshot via Azure OpenAI (or Anthropic). |
| **Edit documents** | Instructions like `Set Sheet1!B2 to 150` or `Replace "Draft" with "Final"`. Model proposes JSON ops → confirm dialog → apply. |
| **Live edits** | If the same file is open in LibreOffice, writes go through **PyUNO** into the open document so changes appear immediately. |
| **Multi-chat** | Maximized layout shows a sidebar of named chats. Compact floating window docks next to the focused app. |

---

## Technologies

### Language & runtime

| Technology | Role |
|---|---|
| **Python 3** | Entire application (CLI, overlay, adapters, sessions) |
| **venv / pip** | Dependency isolation (`requirements.txt`, `requirements-dev.txt`) |
| **python-dotenv** | Load secrets and Azure settings from `.env` |

### UI & desktop (overlay)

| Technology | Role |
|---|---|
| **GTK 3** via **PyGObject** (`gi`, `Gtk`, `Gdk`, `GLib`, `Pango`) | Floating / maximized overlay window, HeaderBar, chat bubbles, toasts, confirm dialogs |
| **X11** | Window focus, geometry, docking (`DISPLAY`, `$XDG_SESSION_TYPE=x11`) |
| **xdotool** | Active window id, title, class, geometry |
| **wmctrl** | Window class fallback, activate document window |
| **GdkX11** | Foreign-window screenshots (capture the real target, not whatever is on top) |

### Document / native APIs

| Technology | Role |
|---|---|
| **openpyxl** | Excel `.xlsx` / `.xlsm` read + CRUD on disk |
| **python-docx** | Word `.docx` paragraphs |
| **python-pptx** | PowerPoint `.pptx` text replace |
| **pypdf** | PDF read + search (no in-place write) |
| **LibreOffice PyUNO** (`uno`, `unohelper` from LO `program/`) | Live edit of open Calc/Writer docs over socket `127.0.0.1:2002` |
| **soffice** | Host process for open documents + UNO listener |

### AI / cloud

| Technology | Role |
|---|---|
| **Azure OpenAI** (chat completions HTTP API) | Q&A over merged text; vision describe; NL → JSON edit plans |
| **azure-identity** / **Azure CLI (`az`)** | AAD token when no API key |
| **Anthropic Messages API** (optional) | Vision describe fallback |
| **requests** | HTTP client for model calls |

### Capture / vision helpers

| Technology | Role |
|---|---|
| **Gdk pixbuf** | Primary window / screen capture path |
| **mss** + **Pillow** (optional) | Screenshot fallback |
| **ImageMagick `import`** (optional) | Screenshot fallback |

### Tooling

| Technology | Role |
|---|---|
| Bash (`scripts/run_overlay.sh`) | Launch overlay with local `PATH` / `LD_LIBRARY_PATH` |
| Unit tests (`tests/*.py`) | Adapters, window utils, edit-engine mapping |

---

## HLD — High Level Design

### Goals

1. **Merge context, don’t scrape the world** — only user-selected windows/files enter a session.  
2. **One contract for every source** — file, screenshot, live LO, terminal all look like adapters.  
3. **Safe mutation** — every write is preview → confirm → apply.  
4. **Live when possible** — prefer editing the open LibreOffice document; fall back to disk.  
5. **Graceful degradation** — no native adapter → screenshot + vision.  

### System context

```
┌──────────────┐     focus / geometry      ┌─────────────────┐
│  User apps   │◄──────────────────────────►│  X11 (xdotool / │
│ Calc,Writer, │   screenshot by window id │  wmctrl / Gdk)  │
│ Chrome,IDE…  │                           └────────┬────────┘
└──────▲───────┘                                    │
       │ UNO live edits                             │
       │ (optional)                                 ▼
┌──────┴───────┐                           ┌─────────────────┐
│ LibreOffice  │◄── socket :2002 ──────────┤  Merge AI       │
│ soffice.bin  │                           │  Overlay + CLI  │
└──────────────┘                           └────────┬────────┘
                                                    │
                           read/write files         │  chat / edit JSON
                           (openpyxl, docx, …)      ▼
                                           ┌─────────────────┐
                                           │ Azure OpenAI /  │
                                           │ Anthropic       │
                                           └─────────────────┘
```

### Logical layers

| Layer | Responsibility | Main modules |
|---|---|---|
| **Presentation** | GTK overlay, CLI prompts | `overlay/overlay_app.py`, `main.py` |
| **Application** | Sessions, chats, merge orchestration, NL edit planning | `session/session_manager.py`, `session/edit_engine.py` |
| **Domain adapters** | Per-source read/search/write | `adapters/*` |
| **Infrastructure** | Window utils, UNO bridge, HTTP to models, `.env` | `overlay/window_utils.py`, `adapters/libreoffice/uno_bridge.py`, `adapters/vision/vision_adapter.py` |

### Core use-case flows (HLD)

**A. Merge**

```
User focuses window → Overlay remembers last external window
→ Merge click → classify (file | browser | terminal | vision | self)
→ if file + path known → Session.merge_file(path)
→ else → VisionAdapter.capture(window_id) + merge_kind("vision")
→ update Merged files chips + toast
```

**B. Ask**

```
User Send(question)
→ gather read_all() / screenshot from session.sources
→ VisionAdapter.ask_text or describe(image)
→ assistant bubble
```

**C. Edit**

```
User Send(instruction) with edit keywords
→ ensure_live_bridge()
→ Azure returns JSON ops
→ each op → ChangePreview (live UNO if file open, else disk adapter)
→ GTK confirm → apply → chat summary
```

### Key HLD decisions

| Decision | Choice | Rationale |
|---|---|---|
| UI toolkit | GTK3 / PyGObject | Native Linux, always-on-top, HeaderBar, works on X11 |
| Mutation model | `ChangePreview` | Structural confirm-first; CLI and overlay share one pattern |
| Multi-source | Session dict of adapters | Mix Excel + screenshot + PDF in one chat |
| Live edits | PyUNO to running soffice | True visible updates without File → Reload |
| Unknown apps | Screenshot + vision | Universal fallback without per-app plugins |
| Model hosting | Azure OpenAI first | Existing enterprise `.env` / `az login` path |

### Non-goals (current)

- Wayland-first support  
- Silent background capture of all windows  
- Automatic writes without confirm  
- Full browser DOM control (stub only)  
- Persisting chat history to disk across process restarts (runtime sessions only)

---

## LLD — Low Level Design

### Module map

```
main.py
  ├─ cmd_overlay → overlay.overlay_app.run()
  ├─ cmd_merge / read / write / search / run → SessionManager + registry
  │
overlay/
  ├─ overlay_app.MergeOverlayApp     # Gtk.Window: UI + merge/send handlers
  └─ window_utils                    # ActiveWindowInfo, classify, guess path, friendly name
  │
session/
  ├─ session_manager
  │    ├─ MergedSource               # identifier, adapter, stale, name
  │    ├─ Session                    # sources{}, messages[], title
  │    └─ SessionManager             # multi-chat, active id
  └─ edit_engine
       ├─ EditPlan                   # mode, ops, previews, errors
       ├─ plan_from_instruction()
       ├─ _op_to_preview()           # live-first then disk
       └─ apply_previews()
  │
adapters/
  ├─ base_adapter.BaseAdapter
  ├─ registry.get_adapter_for_path / get_adapter_for_kind
  ├─ common.change_preview.ChangePreview
  ├─ excel / word / text / ppt / pdf / terminal / vision / browser
  └─ libreoffice/
       ├─ uno_bridge                 # bootstrap_uno, connect, find_document_for_path
       └─ LibreOfficeAdapter         # write_cell / paragraphs on live doc
```

### Important types

**`ActiveWindowInfo`** (`window_utils`)

| Field | Meaning |
|---|---|
| `window_id` | X11 window id (string, from xdotool) |
| `title` | Window title |
| `window_class` | WM_CLASS |

**`classify_window(info) → str`**

| Return | Meaning |
|---|---|
| `file` | Document app / title has supported extension |
| `browser` | Chrome / Edge / Firefox / … |
| `terminal` | gnome-terminal / kitty / … |
| `vision` | Everything else (Cursor, folders, …) |
| `self` | Merge AI overlay — never merge |

**`MergedSource`**

| Field | Meaning |
|---|---|
| `identifier` | Abs path (files) or display name (vision) |
| `adapter` | `BaseAdapter` instance |
| `is_file` | Path-backed vs logical |
| `stale` | File missing on disk |

**`ChangePreview`**

| Field | Meaning |
|---|---|
| `description` | e.g. `write_cell (live)` |
| `source` | File path or logical source |
| `location` | `Sheet1!B2`, `paragraph[0]`, … |
| `old_value` / `new_value` | For confirm UI |
| `_apply_fn` | Closure that performs the mutation |
| `apply()` | Runs once; sets `applied=True` |

**`EditPlan`**

| Field | Meaning |
|---|---|
| `mode` | `edit` or `answer` |
| `ops` | Raw JSON ops from the model |
| `previews` | Built `ChangePreview` list |
| `errors` | Ops that failed to map |
| `answer` | Text when mode is answer / plan failed |

### Sequence: Merge click (LLD)

```
MergeOverlayApp._on_merge_clicked
  → on_merge_clicked
       → refresh last_external if active ≠ self
       → info = _target_window()  # last_external / follow_window
       → kind = classify_window(info)
       → if kind == file and guess_file_path_for_window(info):
              _merge_into_current_chat(file_path=..., info=...)
         else:
              _merge_into_current_chat(info=..., kind=...)
                 → session.merge_file OR merge_kind("vision")
                 → VisionAdapter.capture(output_path, window_id=...)
                 → _alert toast + _refresh_merged_placeholder (chips)
```

Path guessing (`guess_file_path_for_window`): title tokens → absolute/cwd/Desktop search → `/proc/<pid>/cwd` and open fds when needed.

### Sequence: Send edit (LLD)

```
_on_send_clicked
  → session.add_message("user", text)
  → _handle_user_message(session, text)
       → if writable_sources and looks_like_edit(text):
            ensure_live_bridge()                    # open UNO :2002 if needed
            plan = plan_from_instruction(...)       # Azure JSON
            if plan.mode == edit and plan.previews:
                confirm_change(summary)             # Gtk.MessageDialog
                apply_previews(plan.previews)
                reload_writable_adapters(session)
                return "Applied N live/file change(s)…"
       → else _answer_question(...)                 # ask_text / describe
```

**`_op_to_preview` resolution order**

1. Resolve disk adapter by `target` basename / path among `writable_sources`.  
2. `LibreOfficeAdapter.for_open_file(path)` if UNO port open and URL matches.  
3. If live Calc → `write_cell` / `append_row` / `delete_row` on UNO component.  
4. If live Writer → paragraph ops on UNO text.  
5. Else disk adapter methods (`ExcelAdapter`, `WordAdapter`, …).  

### Sequence: Live UNO write (LLD)

```
uno_bridge.ensure_uno_listening(port=2002)
  → soffice --accept="socket,host=127.0.0.1,port=2002;urp;"
uno_bridge.bootstrap_uno()
  → sys.path += LO program/; URE_BOOTSTRAP; LD_LIBRARY_PATH
uno_bridge.connect()
  → UnoUrlResolver → Desktop
find_document_for_path(desktop, abs_path)
  → match component.URL file://…
LibreOfficeAdapter.write_cell("B2", value, sheet)
  → sheet.getCellRangeByName → setValue / setString  (visible immediately)
```

### Overlay UI structure (LLD)

```
Gtk.Window (MergeOverlayApp)
├─ HeaderBar  [+ New chat] [Pin]  …  [—] [□] [✕]
└─ body (HBox)
   ├─ sidebar (VBox, maximized only) — ListBox of chats
   └─ main (VBox)
      ├─ merged_files_section (hidden until sources) — chips + ✕
      ├─ scrolled — messages_box (user / assistant bubbles)
      ├─ toast_bar (EventBox, auto-hide 5s)
      └─ input_row — Entry | Merge | Send
```

Polling: `GLib.timeout_add(400ms, _poll_active_window)` updates `_last_external` / `_follow_window`; docks only when the **external** target window **changes**, never while overlay is focused.

### Registry LLD

```python
_EXTENSION_MAP = {
  ".xlsx"/".xlsm" → ExcelAdapter,
  ".docx" → WordAdapter,
  ".pptx" → PptAdapter,
  ".pdf" → PdfAdapter,
  ".txt"/".md"/".csv"/".log" → TextAdapter,
}
_KIND_MAP = {
  "terminal" → TerminalAdapter,
  "vision" → VisionAdapter,
  "libreoffice_live" → LibreOfficeAdapter,
  "browser" → BrowserAdapter,  # stub
}
```

### Error & safety LLD

| Case | Behavior |
|---|---|
| No target window on Merge | Toast: focus a window first |
| Screenshot failure | Toast; chip may still exist for retry |
| Model returns bad JSON | `EditPlan.mode=answer` with parse error text |
| User declines confirm | No `apply()`; chat says cancelled |
| UNO port down / file not open | Silent fallthrough to disk adapter |
| PDF write requested | `can_write=False`; not in writable edit path |
| Overlay classified as target | `self` — ignored |

### Extension points (LLD)

| To add… | Do this |
|---|---|
| New file type | Implement `BaseAdapter`, register in `_EXTENSION_MAP`, add ops in `edit_engine` if writable |
| New live app bridge | New adapter + prefer it in `_op_to_preview` like UNO |
| New model provider | Extend `VisionAdapter._chat_azure` / add parallel client; keep JSON schema stable |
| Persist chats | Serialize `Session` metadata (+ optional message log); keep adapters reconstructed on load |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Overlay (GTK3)  ·  CLI (main.py)                           │
├─────────────────────────────────────────────────────────────┤
│  SessionManager  — chats, sources, messages, banner         │
│  EditEngine      — NL → JSON ops → ChangePreview → apply    │
├─────────────────────────────────────────────────────────────┤
│  Adapter registry  — extension / kind → adapter class       │
├──────────┬──────────┬──────────┬──────────┬─────────────────┤
│ Excel    │ Word     │ Text/PPT │ PDF      │ Vision          │
│ openpyxl │ python-  │ disk     │ read-    │ screenshot +    │
│ (+ live  │ docx     │ CRUD     │ only     │ Azure / Claude  │
│  UNO)    │ (+ live) │          │          │                 │
└──────────┴──────────┴──────────┴──────────┴─────────────────┘
                         ▲
                         │ when file is open in soffice
              LibreOffice live (PyUNO bridge)
```

Every adapter implements one contract (`adapters/base_adapter.py`):

- `read_all()` — text dump for AI context  
- `search(value)` — locate matches  
- `close()` — release handles  
- Write/execute methods **never** mutate immediately; they return a `ChangePreview` that must be `.apply()`’d after confirm  

`adapters/registry.py` maps file extensions and logical “kinds” to classes so the session layer never hard-codes adapter types.

For full HLD/LLD detail, see the sections above.

---


## Adapters

| Adapter | Extensions / kind | Status | Read | Write | Notes |
|---|---|---|---|---|---|
| **Excel** | `.xlsx`, `.xlsm` | Working | ✓ | ✓ | `write_cell`, `append_row`, `delete_row` via openpyxl |
| **Word** | `.docx` | Working | ✓ | ✓ | `write_paragraph`, `append_paragraph`, `delete_paragraph` |
| **Text** | `.txt`, `.md`, `.csv`, `.log` | Working | ✓ | ✓ | `replace_text`, `append_text`, `write_all` |
| **PowerPoint** | `.pptx` | Working | ✓ | ✓ | `replace_text` in slide runs |
| **PDF** | `.pdf` | Working | ✓ | — | Read + search only (by design) |
| **Vision** | kind `vision` | Working | ✓ | — | Window screenshot; Azure / Anthropic describe |
| **LibreOffice live** | kind `libreoffice_live` | Working | ✓ | ✓ | UNO edits to the **open** Calc/Writer doc |
| **Terminal** | kind `terminal` | Working | — | execute | Confirm-first shell commands (CLI) |
| **Browser** | kind `browser` | Stub | — | — | Needs Chrome extension + bridge (future) |

“Working” = used by overlay/CLI/tests. “Stub” = method shapes reserved for later.

---

## Confirm-first writes

All mutations go through `adapters/common/change_preview.py`:

1. Adapter method builds a `ChangePreview` (description, location, old → new, apply fn)  
2. CLI asks `[y/N]`; overlay shows a GTK **Apply these changes?** dialog  
3. Only after confirm does `.apply()` run  

There are **no** silent writes — including terminal `run`.

---

## Live LibreOffice editing (no reload)

**Goal:** when you say “change cell B2”, the value appears in the open Calc/Writer window immediately — not only on disk after File → Reload.

### How it works

1. Edit engine resolves the merged file path.  
2. It checks whether LibreOffice is listening on UNO (`127.0.0.1:2002`) and whether that file URL is open.  
3. If yes → ops use `LibreOfficeAdapter` (`write_cell (live)`, etc.) against the live document.  
4. If no → fallback to on-disk Excel/Word/Text/PPT adapters (file save; app may need reload).

### Bridge modules

| File | Role |
|---|---|
| `adapters/libreoffice/uno_bridge.py` | Find LO `program/`, bootstrap `uno`, open/ensure UNO socket, find open docs by path |
| `adapters/libreoffice/libreoffice_adapter.py` | Calc + Writer live CRUD |

### Environment (optional)

| Variable | Purpose |
|---|---|
| `MERGE_AI_LO_PROGRAM` | Path to LibreOffice `program/` (contains `uno.py`) |
| `MERGE_AI_SOFFICE` | Path to `soffice` / `soffice.bin` |
| `LIBREOFFICE_PROGRAM` | Alternate for `MERGE_AI_LO_PROGRAM` |

Portable installs under `~/libreoffice-portable/opt/libreoffice*/program` are auto-detected.

### Enabling the UNO socket

When you send an edit instruction, the overlay calls `ensure_live_bridge()`, which asks the running soffice to listen:

```text
soffice --accept="socket,host=127.0.0.1,port=2002;urp;" --norestore
```

If LibreOffice is already running (single-instance), that usually enables listening on the existing process. Keep the spreadsheet/document **open** while editing for live updates.

---

## Overlay UX

Launch:

```bash
python3 main.py overlay
# or
./scripts/run_overlay.sh
```

### Compact (floating) mode

- Small docked window (~360×480)  
- Follows the last focused external app (docks to its top-right)  
- Does **not** re-dock while you are clicking Merge/Send (avoids stolen clicks)  
- **Pin** keeps it above other windows  

### Maximized mode

- ChatGPT-style layout: left **Chats** sidebar + main thread  
- **+ New chat** in the header  
- Title bar subtitle shows `Merged: …` when sources exist  

### Merge behavior

1. Focus the target window (Excel, Cursor, Edge, folder, …).  
2. Click **Merge** on the overlay.  
3. **Documents with a resolvable path** → native file merge (Excel/Word/…).  
4. **Everything else** (Cursor, browsers, folders, unknown) → screenshot + friendly name (`Cursor window`, `Chrome`, `Edge`, `Terminal`, …).  
5. **No file picker** on Merge.  
6. Top **Merged files:** chips appear only after the first merge; each chip has **✕** to remove.  
7. Small bottom toast confirms merge / already-merged / apply (auto-closes ~5s).  

### Chat / Send

- Q&A over all merged sources  
- Edit-like instructions → plan → confirm → apply (live UNO preferred)  

---

## Natural-language edit engine

Implemented in `session/edit_engine.py`.

### Flow

1. Detect edit intent (keywords: set, change, update, delete, append, replace, …).  
2. Ensure LibreOffice UNO bridge if possible.  
3. Ask Azure for **JSON only**:

```json
{
  "mode": "edit",
  "ops": [
    {
      "op": "write_cell",
      "target": "ledger.xlsx",
      "sheet": "Sheet1",
      "cell": "B2",
      "value": 150
    }
  ]
}
```

4. Map each op to a `ChangePreview` (live UNO if the file is open).  
5. Confirm in GTK → apply → reload in-memory adapters → reply in chat.  

### Supported ops

| Kind | Ops |
|---|---|
| Excel / live Calc | `write_cell`, `append_row`, `delete_row` |
| Word / live Writer | `write_paragraph`, `append_paragraph`, `delete_paragraph` |
| Text | `replace_text`, `append_text`, `write_all` |
| PPT | `replace_text` |

Example prompts:

- `Set Summary!A1 to "Done"`  
- `Append a row [Alice, 42] on Sheet1`  
- `Delete row 3 on Citation Audit`  
- `Replace "Draft" with "Final" in notes.docx`  
- `Append "Reviewed." to the text file`  

---

## Install

### Python

```bash
cd Merge_AI
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt          # runtime
pip install -r requirements-dev.txt      # optional: pytest, fixtures
```

**Runtime packages** (`requirements.txt`):

- `openpyxl` — Excel  
- `pypdf` — PDF  
- `python-docx` — Word  
- `python-pptx` — PowerPoint  
- `requests`, `python-dotenv`, `azure-identity` — Azure / config  

### Overlay system deps (X11)

```bash
# Ubuntu / Debian
sudo apt-get install -y python3-gi gir1.2-gtk-3.0 xdotool wmctrl
echo "$XDG_SESSION_TYPE"   # expect: x11
echo "$DISPLAY"            # e.g. :0 or :1
```

If you cannot install system packages, the project can use binaries under `.local/bin` and `.local/lib` (see `scripts/run_overlay.sh`).

### LibreOffice (for live edits)

- System or **portable** LibreOffice with a `program/` directory containing `uno.py`  
- Open the `.xlsx` / `.docx` in Calc/Writer before applying edits for live updates  

PyUNO is **not** pip-installable; Merge AI bootstraps it from the LO install.

---

## Configuration (`.env`)

Copy the example and fill in values (never commit secrets):

```bash
cp .env.example .env
```

| Variable | Required | Description |
|---|---|---|
| `DAI_AZURE_OPENAI_ENDPOINT` | Yes* | Azure OpenAI resource URL |
| `DAI_AZURE_OPENAI_DEPLOYMENT` | Yes* | Chat deployment name (e.g. `gpt-4o`) |
| `DAI_OPENAI_API_VERSION` | No | Default `2024-10-21` |
| `DAI_AZURE_OPENAI_API_KEY` | No | API key; if unset, uses `az login` / `DefaultAzureCredential` |
| `DAI_COGNITIVESERVICES_SCOPE` | No | Token scope for AAD auth |
| `ANTHROPIC_API_KEY` | No | Fallback for vision describe |
| `MERGE_AI_LO_PROGRAM` | No | LibreOffice `program/` path |
| `MERGE_AI_SOFFICE` | No | `soffice` binary path |

\*Required for chat Q&A and NL edit planning. Vision can also use Anthropic.

Auth order for Azure: API key → `az account get-access-token` → `DefaultAzureCredential`.

---

## Quick start

### 1. Overlay

```bash
source venv/bin/activate   # if using a venv
./scripts/run_overlay.sh
# or: python3 main.py overlay
```

1. Open a spreadsheet in LibreOffice Calc.  
2. Focus that window, click **Merge** on Merge AI.  
3. Ask: `What is in A1?`  
4. Edit: `Set ZZ1 on Summary to Hello` → confirm → watch Calc update **live**.  
5. Merge Cursor or Edge → ask about the screenshot.  

### 2. CLI

```bash
python3 main.py merge report.pdf ledger.xlsx notes.docx
python3 main.py read ledger.xlsx --sheet Sheet1 --cell A1
python3 main.py write ledger.xlsx --sheet Sheet1 --cell A1 --value "New Value"
python3 main.py search report.pdf ledger.xlsx --value "Revenue"
python3 main.py run "ls -la"
```

---

## CLI reference

| Command | Purpose |
|---|---|
| `overlay` | Launch the GTK floating / maximized shell |
| `merge FILE…` | Merge files into a new session; print banner |
| `read FILE [--sheet S] [--cell C]` | Read cell / page / paragraph / full text |
| `write FILE --cell C --value V [--sheet S]` | Confirm-first write (Excel/Word) |
| `search FILE… --value V` | Search across merged files |
| `run "CMD"` | Confirm-first terminal command |

---

## Tests

```bash
python3 tests/test_excel_adapter.py
python3 tests/test_all_adapters.py
python3 tests/test_window_utils.py
python3 tests/test_edit_engine.py
```

| Suite | Covers |
|---|---|
| `test_excel_adapter` | Excel CRUD + confirm-first |
| `test_all_adapters` | Word/PDF/terminal smoke |
| `test_window_utils` | Path guessing, classify, friendly names |
| `test_edit_engine` | NL op mapping + apply (no Azure) |

Live UNO tests need a running LibreOffice with UNO listening and the file open; unit tests do not require that.

---

## Project layout

```
Merge_AI/
├── main.py                          # CLI + overlay entry
├── requirements.txt
├── requirements-dev.txt
├── .env.example
├── README.md
├── ROADMAP.md
├── scripts/
│   └── run_overlay.sh               # PATH/LD_LIBRARY_PATH + overlay
├── adapters/
│   ├── base_adapter.py              # Shared contract
│   ├── registry.py                  # Extension / kind → class
│   ├── common/
│   │   └── change_preview.py        # Confirm-first apply
│   ├── excel/excel_adapter.py
│   ├── word/word_adapter.py
│   ├── text/text_adapter.py
│   ├── ppt/ppt_adapter.py
│   ├── pdf/pdf_adapter.py
│   ├── vision/vision_adapter.py     # Capture + Azure/Anthropic
│   ├── libreoffice/
│   │   ├── uno_bridge.py            # PyUNO bootstrap + connect
│   │   └── libreoffice_adapter.py   # Live Calc/Writer
│   ├── terminal/terminal_adapter.py
│   └── browser/browser_adapter.py   # Stub
├── session/
│   ├── session_manager.py           # Multi-chat, sources, messages
│   └── edit_engine.py               # NL → ops → live/disk apply
├── overlay/
│   ├── overlay_app.py               # GTK UI, Merge, chat, confirm
│   └── window_utils.py              # Active window, classify, names
└── tests/
    ├── test_excel_adapter.py
    ├── test_all_adapters.py
    ├── test_window_utils.py
    └── test_edit_engine.py
```

---

## Design rules

These apply to every current and future adapter:

1. **Auto vs confirm** — Reads run immediately. Writes / deletes / sends / executes always return a `ChangePreview` and require confirm.  
2. **Explicit scope** — Only sources the user merged are read or edited. No silent full-desktop capture or whole-disk scans.  
3. **No hidden persistence** — Session metadata (titles, source list) is separate from document content; content is not cached beyond the session lifetime.  
4. **Live preferred, disk fallback** — If LibreOffice has the file open, edit live; otherwise write the file on disk.  
5. **X11 first** — Overlay targets X11 (`$XDG_SESSION_TYPE=x11`). Wayland is a later, best-effort target.  
6. **Vision is a method, not a label** — Screenshots use the vision adapter; UI names show `Cursor window`, `Chrome`, `Edge`, etc.  

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Overlay won’t start | Missing GTK / no DISPLAY | Install `python3-gi`, `gir1.2-gtk-3.0`; use X11 session |
| Merge does nothing / click ignored | Compact mode was re-docking under the cursor | Current build avoids dock-while-focused; restart overlay |
| No **Merged files:** bar | Section hidden until first successful merge | Merge a window; check toast for errors |
| Chat / edit fails with endpoint error | `.env` missing Azure settings | Set `DAI_AZURE_OPENAI_*`; `az login` or API key |
| Edit applies but Calc looks unchanged | File write path used (doc not open / UNO down) | Keep file open in LibreOffice; check port `2002`; set `MERGE_AI_LO_PROGRAM` |
| `uno` import fails | Wrong LO program path | Point `MERGE_AI_LO_PROGRAM` at `…/libreoffice*/program` |
| Screenshot grabs wrong window | Overlapping maximized apps | Overlay captures by window id (Gdk foreign window), not desktop crop |
| xdotool / wmctrl missing | Not installed | `apt install xdotool wmctrl` or use `.local/` fallback via `run_overlay.sh` |

Check UNO listening:

```bash
ss -ltn | grep 2002
# or
python3 -c "from adapters.libreoffice import uno_bridge; print(uno_bridge.port_is_open())"
```

---

## Roadmap

See [ROADMAP.md](./ROADMAP.md) for phase status. High level:

| Phase | Status |
|---|---|
| 1 — Adapters + session + NL edit layer | Mostly done |
| 2 — GTK overlay shell | Done |
| 3 — Vision capture + describe | Done |
| 4 — Live LibreOffice UNO | Done for Calc/Writer |
| Next — Browser bridge, VS Code adapter, richer Q&A routing | Planned |

---

## License / notes

Personal research / prototype project. Azure credentials and `.env` must stay local (see `.gitignore`). Do not commit secrets.
