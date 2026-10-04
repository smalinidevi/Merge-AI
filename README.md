# Merge AI

### Your work. One overlay. Grounded answers. Live edits.

**Merge AI** is a Linux (X11) AI overlay that attaches to whatever you’re already doing — Calc, Writer, Excel files, PDFs, PowerPoint, Chrome, terminals, IDEs — and turns that context into chat you can trust.

No silent desktop scraping. No mystery edits. You **Merge** a window, ask in plain language, and when something should change, you **confirm** first. If LibreOffice has the file open, the change appears **live** in the app — not after File → Reload.

```text
Focus a window  →  Merge  →  Ask or instruct  →  Confirm writes  →  Done
```

---



## Why it feels different


|                            |                                                                                                   |
| -------------------------- | ------------------------------------------------------------------------------------------------- |
| **You choose the context** | Only explicitly merged sources enter the chat.                                                    |
| **Multi-source by design** | Excel + PDF + screenshot in one thread. Fill answers from a PDF into a sheet.                     |
| **Live when it matters**   | LibreOffice Calc/Writer updates in place via PyUNO.                                               |
| **Safe by default**        | Every write / run / navigate is preview → confirm → apply.                                        |
| **Grounded agent**         | Multi-node pipeline with confidence-gated verify/feedback — no golden answers, no invented facts. |
| **Universal fallback**     | Unknown apps become a screenshot + vision Q&A.                                                    |


---



## Demo flow (2 minutes)

```bash
./scripts/run_overlay.sh
# or: python3 main.py overlay
```

1. Open a spreadsheet in **LibreOffice Calc**.
2. Focus it → click **Merge** on the overlay.
3. Ask: `What is in A1?`
4. Instruct: `Set Summary!ZZ1 to Hello` → confirm → watch Calc update live.
5. Merge a **PDF** + the same Excel → `Fill the answer column from the PDF`.
6. Merge **Chrome** (with the extension) → `Search Google for libreoffice calc`.

---



## What you can merge


| Source                                           | What Merge AI does                                                        |
| ------------------------------------------------ | ------------------------------------------------------------------------- |
| **Excel / Calc** (`.xlsx`, `.xlsm`)              | Full CRUD — cells, ranges, rows, columns, sheets. Live via UNO when open. |
| **Word / Writer** (`.docx`)                      | Paragraphs, insert, table cells. Live Writer ops when open.               |
| **PowerPoint** (`.pptx`)                         | Replace text, set/append slide text, add/delete slides.                   |
| **Text / notes** (`.txt`, `.md`, `.csv`, `.log`) | Notepad-style replace / append / rewrite.                                 |
| **PDF**                                          | Read + **page/snippet citations** (RAG-style). No PDF rewrite.            |
| **Terminal**                                     | Confirm-first `run_command`; stdout/stderr back in chat.                  |
| **Chrome**                                       | Tab text + navigate / Google search / switch tab via local bridge.        |
| **Anything else**                                | Screenshot + vision (Cursor, folders, Edge without bridge, …).            |


---



## Architecture at a glance

```text
┌────────────────────────────────────────────────────────────┐
|  GTK Overlay  ·  CLI (main.py)                             |
├────────────────────────────────────────────────────────────┤
|  SessionManager   multi-chat · sources · messages          |
|  Agent graph      gather → intent → reason → plan →        |
|                   verify → feedback → answer / edits       |
|  EditEngine       JSON ops → ChangePreview → apply         |
├────────────────────────────────────────────────────────────┤
|  Adapters (one contract)                                   |
|  Excel  Word  Text  PPT  PDF  Terminal  Browser  Vision    |
|           └── LibreOffice live (PyUNO :2002) ──┘           |
└────────────────────────────────────────────────────────────┘
         ▲                              ▲
    X11 window focus              Azure OpenAI / Anthropic
    xdotool · wmctrl · Gdk        chat · vision · plans
```



### Agent pipeline

```text
gather → intent → reason → plan → verify → feedback → prepare_edits | answer
```

- **gather** loads files, PDF citations, terminal/browser text, screenshots  
- **verify** returns `pass` + `confidence` (threshold **0.7**)  
- **feedback** re-plans up to **3** times when confidence is low  
- answers stay grounded in merged sources — never “perfect without evidence”



### Confirm-first mutation

```text
Adapter builds ChangePreview  →  UI / CLI confirm  →  .apply()
```

No silent writes. Terminal commands and browser navigation use the same rule.

---



## Overlay UX


| Mode          | Behavior                                                                   |
| ------------- | -------------------------------------------------------------------------- |
| **Compact**   | ~360×480 floating panel; docks beside the focused app; Pin keeps it on top |
| **Maximized** | Sidebar of chats + main thread (ChatGPT-style)                             |


**Merge** never opens a file picker:

- Resolvable document path → native adapter  
- Terminal / Chrome (bridge up) → native kind  
- Otherwise → screenshot + friendly name (`Chrome`, `Cursor window`, …)

**Merged files** chips appear after the first merge; ✕ removes a source. Toasts auto-hide in ~5s.

**Send** is asynchronous — the UI stays responsive while the agent runs.

---



## Natural-language ops


| Kind               | Ops                                                                                               |
| ------------------ | ------------------------------------------------------------------------------------------------- |
| Excel / live Calc  | `write_cell`, `write_range`, `clear_range`, row/column/sheet CRUD                                 |
| Word / live Writer | `write_paragraph`, `append_paragraph`, `delete_paragraph`, `insert_paragraph`, `write_table_cell` |
| Text               | `replace_text`, `append_text`, `write_all`                                                        |
| PPT                | `replace_text`, `set_slide_text`, `append_slide_text`, `add_slide`, `delete_slide`                |
| Terminal           | `run_command`                                                                                     |
| Browser            | `navigate`, `google_search`, `activate_tab`                                                       |


**Try saying:**

- `Set Summary!A1 to "Done"`
- `Append a row [Alice, 42] on Sheet1`
- `Fill the answer column from the PDF`
- `Where is "net assets" in the PDF?` → `Page 3 — "…"`
- `Run: ls -la`
- `Search Google for merge ai`

---



## Install



### 1. Python

```bash
cd Merge_AI
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt   # optional
```

Runtime: `openpyxl`, `pypdf`, `python-docx`, `python-pptx`, `requests`, `python-dotenv`, `azure-identity`.

### 2. Overlay (X11)

```bash
sudo apt-get install -y python3-gi gir1.2-gtk-3.0 xdotool wmctrl
echo "$XDG_SESSION_TYPE"   # expect: x11
echo "$DISPLAY"            # e.g. :0 or :1
```

Or use project-local tools via `./scripts/run_overlay.sh` (`.local/bin`, `.local/lib`).

### 3. LibreOffice (live edits)

System or portable LO with `program/uno.py`. Keep the `.xlsx` / `.docx` **open** in Calc/Writer for live UNO. Optional:


| Variable              | Purpose                        |
| --------------------- | ------------------------------ |
| `MERGE_AI_LO_PROGRAM` | Path to LibreOffice `program/` |
| `MERGE_AI_SOFFICE`    | Path to `soffice`              |




### 4. Chrome bridge (optional)

1. Start the overlay (bridge listens on `http://127.0.0.1:8765`).
2. Chrome → `chrome://extensions` → Developer mode → **Load unpacked** → `[chrome_extension/](chrome_extension/)`.
3. Focus Chrome → **Merge** → ask about the tab or search Google.

Without the extension, Chrome still merges as a **vision** screenshot.

---



## Configuration

```bash
cp .env.example .env
```


| Variable                                   | Required | Description                                      |
| ------------------------------------------ | -------- | ------------------------------------------------ |
| `DAI_AZURE_OPENAI_ENDPOINT`                | Yes*     | Azure OpenAI resource URL                        |
| `DAI_AZURE_OPENAI_DEPLOYMENT`              | Yes*     | Chat deployment (e.g. `gpt-4o`)                  |
| `DAI_OPENAI_API_VERSION`                   | No       | Default `2024-10-21`                             |
| `DAI_AZURE_OPENAI_API_KEY`                 | No       | If unset → `az login` / `DefaultAzureCredential` |
| `ANTHROPIC_API_KEY`                        | No       | Vision describe fallback                         |
| `MERGE_AI_LO_PROGRAM` / `MERGE_AI_SOFFICE` | No       | LibreOffice paths                                |


Needed for chat + NL edit planning. Auth order: API key → `az account get-access-token` → `DefaultAzureCredential`.

---



## Quick start



### Overlay

```bash
source venv/bin/activate
./scripts/run_overlay.sh
```



### CLI

```bash
python3 main.py merge report.pdf ledger.xlsx notes.docx
python3 main.py read ledger.xlsx --sheet Sheet1 --cell A1
python3 main.py write ledger.xlsx --sheet Sheet1 --cell A1 --value "New Value"
python3 main.py search report.pdf ledger.xlsx --value "Revenue"
python3 main.py run "ls -la"
```


| Command                     | Purpose                             |
| --------------------------- | ----------------------------------- |
| `overlay`                   | GTK floating / maximized shell      |
| `merge FILE…`               | Merge files; print banner           |
| `read` / `write` / `search` | Document I/O (writes confirm-first) |
| `run "CMD"`                 | Confirm-first shell command         |


---



## Technologies


| Layer     | Stack                                                      |
| --------- | ---------------------------------------------------------- |
| App       | Python 3, GTK3 / PyGObject, X11 (`xdotool`, `wmctrl`, Gdk) |
| Documents | openpyxl, python-docx, python-pptx, pypdf                  |
| Live      | LibreOffice PyUNO (`127.0.0.1:2002`)                       |
| AI        | Azure OpenAI (+ optional Anthropic vision)                 |
| Browser   | MV3 extension + stdlib HTTP bridge                         |
| Capture   | Gdk foreign-window screenshot (± mss / ImageMagick)        |


---



## Design rules

1. **Reads free, writes gated** — mutations always return a `ChangePreview`.
2. **Explicit scope** — only merged sources; no silent system-wide capture.
3. **Live preferred, disk fallback** — UNO when the file is open.
4. **Grounded answers** — cite cells, pages, snippets; never invent.
5. **X11 first** — Wayland later.
6. **Vision is a method** — UI labels stay friendly (`Chrome`, `Terminal`, …).

---



## Project layout

```text
Merge_AI/
├── main.py
├── requirements.txt · requirements-dev.txt · .env.example
├── README.md · ROADMAP.md
├── scripts/run_overlay.sh
├── chrome_extension/              # MV3 ↔ local bridge
├── adapters/
│   ├── base_adapter.py · registry.py
│   ├── common/change_preview.py
│   ├── excel/ word/ text/ ppt/ pdf/
│   ├── vision/ terminal/
│   ├── browser/                   # adapter + bridge_server
│   └── libreoffice/               # uno_bridge + live adapter
├── session/
│   ├── session_manager.py
│   ├── edit_engine.py
│   └── agent/                     # multi-node orchestrator
├── overlay/
│   ├── overlay_app.py
│   └── window_utils.py
└── tests/
```

---



## Tests

```bash
python3 tests/test_agent_orchestrator.py
python3 tests/test_pdf_citations.py
python3 tests/test_browser_bridge.py
python3 tests/test_word_ppt_ops.py
python3 tests/test_async_send.py
python3 tests/test_all_adapters.py
python3 tests/test_edit_engine.py
python3 tests/test_excel_adapter.py
python3 tests/test_window_utils.py
```

Unit suites cover adapters, citations, Chrome bridge (no real Chrome required), agent feedback, async Send, and edit mapping. Live UNO needs LibreOffice open with port `2002`.

---



## Troubleshooting


| Symptom                   | Fix                                                                |
| ------------------------- | ------------------------------------------------------------------ |
| Overlay won’t start       | Install `python3-gi` + GTK3; use an X11 session with `DISPLAY` set |
| Merge click ignored       | Restart overlay (current build avoids dock-while-focused)          |
| No Merged files bar       | Appears after first successful merge — check toast                 |
| Azure / chat errors       | Fill `DAI_AZURE_OPENAI_*` in `.env`; `az login` or API key         |
| Calc doesn’t update live  | Keep file open; `ss -ltn | grep 2002`; set `MERGE_AI_LO_PROGRAM`   |
| Browser stays vision-only | Load `chrome_extension/` unpacked; confirm bridge on `:8765`       |
| Wrong screenshot          | Capture uses window id (Gdk), not full-desktop crop                |


```bash
ss -ltn | grep 2002
python3 -c "from adapters.libreoffice import uno_bridge; print(uno_bridge.port_is_open())"
```

---




| Phase                                      | Status   |
| ------------------------------------------ | -------- |
| Adapters + sessions + NL edits             | Done     |
| GTK overlay + async Send                   | Done     |
| Vision capture + describe                  | Done     |
| Live LibreOffice UNO                       | Done     |
| Confidence feedback + PDF citations        | Done     |
| Chrome bridge + terminal overlay           | Done     |
| Windows Excel / Google Sheets              | Later    |
| PDF viewer highlight / IDE native adapters | Deferred |


