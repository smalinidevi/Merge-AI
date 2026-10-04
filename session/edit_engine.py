"""
Natural-language → confirm-first CRUD on writable merged sources.

Flow:
  1. Ask Azure for a JSON plan (edit ops or plain answer)
  2. Map ops onto Excel / Word / Text adapters → ChangePreview list
  3. Caller confirms, then apply_previews()
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from adapters.common.change_preview import ChangePreview
from adapters.vision.vision_adapter import VisionAdapter
from session.session_manager import Session


_EDIT_HINT = re.compile(
    r"\b("
    r"set|change|update|write|delete|remove|append|add|replace|modify|"
    r"insert|clear|put|fill|rename|edit|overwrite|create|crud|"
    r"sheet|column|row|copy|duplicate|new\s+sheet|"
    r"run|execute|command|terminal|navigate|google|search\s+for|open\s+tab|"
    r"switch\s+tab|activate\s+tab"
    r")\b",
    re.IGNORECASE,
)

_WORD_OPS = frozenset(
    {
        "write_paragraph",
        "append_paragraph",
        "delete_paragraph",
        "insert_paragraph",
        "write_table_cell",
    }
)

_PPT_OPS = frozenset(
    {
        "replace_text",
        "set_slide_text",
        "append_slide_text",
        "add_slide",
        "delete_slide",
    }
)

_TERMINAL_OPS = frozenset({"run_command"})
_BROWSER_OPS = frozenset({"navigate", "google_search", "activate_tab"})

_EXCEL_OPS = frozenset(
    {
        "write_cell",
        "write_range",
        "clear_range",
        "append_row",
        "insert_row",
        "delete_row",
        "insert_column",
        "delete_column",
        "create_sheet",
        "rename_sheet",
        "delete_sheet",
        "copy_sheet",
    }
)

_WRITABLE_KINDS = frozenset({"excel", "word", "text", "ppt"})


@dataclass
class EditPlan:
    mode: str  # "edit" | "answer"
    answer: str = ""
    ops: list[dict[str, Any]] = field(default_factory=list)
    previews: list[ChangePreview] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def writable_sources(session: Session) -> list:
    return [
        s
        for s in session.sources.values()
        if s.adapter and getattr(s.adapter, "can_write", False) and s.adapter.kind in _WRITABLE_KINDS
    ]


def looks_like_edit(text: str) -> bool:
    return bool(_EDIT_HINT.search(text or ""))


def _source_catalog(session: Session) -> str:
    lines = []
    for source in writable_sources(session):
        adapter = source.adapter
        kind = adapter.kind
        name = source.name
        path = getattr(adapter, "file_path", source.identifier)
        try:
            if kind == "excel":
                body = adapter.read_all(max_rows_per_sheet=40)
                sheets = ", ".join(adapter.list_sheets())
                lines.append(
                    f"- name={name!r} kind=excel path={path!r} sheets=[{sheets}]\n{body}"
                )
            else:
                body = adapter.read_all()
                if len(body) > 6000:
                    body = body[:6000] + "\n…(truncated)"
                lines.append(f"- name={name!r} kind={kind} path={path!r}\n{body}")
        except Exception as e:
            lines.append(f"- name={name!r} kind={kind} path={path!r} (read error: {e})")
    return "\n\n".join(lines)


def _parse_json_blob(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def plan_from_instruction(session: Session, instruction: str) -> EditPlan:
    """Ask the model for edit ops or an answer; build ChangePreviews for edits."""
    sources = writable_sources(session)
    if not sources:
        return EditPlan(mode="answer", answer="")

    catalog = _source_catalog(session)
    prompt = (
        "You are Merge AI's document editor. Given writable merged sources and a "
        "user instruction, reply with ONLY valid JSON (no markdown fences).\n\n"
        'Schema:\n'
        '{\n'
        '  "mode": "edit" | "answer",\n'
        '  "answer": "short reply if mode=answer",\n'
        '  "ops": [ ... ]  // if mode=edit\n'
        '}\n\n'
        "Allowed ops by kind:\n"
        "EXCEL / Calc (use these for create sheet, rows, columns, cells):\n"
        '- {"op":"write_cell","target":"<file>","sheet":"<name|null>","cell":"A1","value":any}\n'
        '- {"op":"write_range","target":"<file>","sheet":...,"start_cell":"A1","values":[[...],...]}\n'
        '- {"op":"clear_range","target":"<file>","sheet":...,"range":"A1:C3"}\n'
        '- {"op":"append_row","target":"<file>","sheet":...,"values":[...]}\n'
        '- {"op":"insert_row","target":"<file>","sheet":...,"row":1,"values":[...]|null}\n'
        '- {"op":"delete_row","target":"<file>","sheet":...,"row":1}\n'
        '- {"op":"insert_column","target":"<file>","sheet":...,"column":"B"|2}\n'
        '- {"op":"delete_column","target":"<file>","sheet":...,"column":"B"|2}\n'
        '- {"op":"create_sheet","target":"<file>","name":"NewSheet","index":null|0}\n'
        '- {"op":"rename_sheet","target":"<file>","old_name":"Sheet1","new_name":"Data"}\n'
        '- {"op":"delete_sheet","target":"<file>","name":"OldSheet"}\n'
        '- {"op":"copy_sheet","target":"<file>","name":"Sheet1","new_name":"Sheet1 Copy"}\n'
        "WORD:\n"
        '- {"op":"write_paragraph","target":"<file>","index":0,"value":"..."}\n'
        '- {"op":"append_paragraph","target":"<file>","value":"..."}\n'
        '- {"op":"delete_paragraph","target":"<file>","index":0}\n'
        "TEXT:\n"
        '- {"op":"replace_text","target":"<file>","old":"...","new":"..."}\n'
        '- {"op":"append_text","target":"<file>","value":"..."}\n'
        '- {"op":"write_all","target":"<file>","value":"..."}\n'
        "PPT:\n"
        '- {"op":"replace_text","target":"<file>","old":"...","new":"..."}\n\n'
        "`target` must match a source filename (basename) from the catalog.\n"
        "For 'create a new sheet named X', ALWAYS use create_sheet (not write_cell).\n"
        "If the user is only asking a question (no modification), use mode=answer.\n"
        "Prefer the smallest correct set of ops. Do not invent cell values.\n"
        "You MAY create new sheets that do not yet exist.\n\n"
        f"--- WRITABLE SOURCES ---\n{catalog}\n--- END ---\n\n"
        f"User instruction: {instruction}"
    )

    helper = VisionAdapter("edit-planner")
    raw = _ask_json(helper, prompt)

    try:
        data = _parse_json_blob(raw)
    except Exception as e:
        return EditPlan(
            mode="answer",
            answer=f"Could not parse edit plan: {e}\nRaw: {raw[:300]}",
        )

    mode = str(data.get("mode") or "answer").lower()
    if mode != "edit":
        return EditPlan(mode="answer", answer=str(data.get("answer") or raw))

    ops = data.get("ops") or []
    if not isinstance(ops, list) or not ops:
        return EditPlan(mode="answer", answer=str(data.get("answer") or "No edit operations proposed."))

    plan = EditPlan(mode="edit", ops=ops)
    for op in ops:
        try:
            preview = _op_to_preview(session, op)
            plan.previews.append(preview)
        except Exception as e:
            plan.errors.append(f"{op}: {e}")
    if not plan.previews:
        plan.mode = "answer"
        plan.answer = "Could not build edits:\n" + "\n".join(plan.errors)
    return plan


def _ask_json(helper: VisionAdapter, prompt: str) -> str:
    from adapters.vision.vision_adapter import _load_dotenv

    _load_dotenv()
    endpoint = (
        os.environ.get("DAI_AZURE_OPENAI_ENDPOINT")
        or os.environ.get("AZURE_OPENAI_ENDPOINT")
        or ""
    ).rstrip("/")
    if not endpoint:
        raise RuntimeError("DAI_AZURE_OPENAI_ENDPOINT not set.")
    return helper._chat_azure(
        endpoint,
        [
            {
                "role": "system",
                "content": "You output only compact JSON. No markdown. No commentary.",
            },
            {"role": "user", "content": prompt},
        ],
        max_completion_tokens=500,
    )


def _resolve_any_adapter(session: Session, target: str, *, kinds: Optional[set] = None):
    """Resolve a merged source by name/path/identifier, optionally filtered by kind."""
    target = (target or "").strip()
    base = os.path.basename(target)
    for source in session.sources.values():
        adapter = source.adapter
        if not adapter:
            continue
        if kinds is not None and adapter.kind not in kinds:
            continue
        path = getattr(adapter, "file_path", source.identifier)
        if source.name == target or source.name == base:
            return adapter
        if os.path.basename(str(path)) == base or str(path) == target:
            return adapter
        if source.identifier == target:
            return adapter
    raise KeyError(f"No merged source matching {target!r}")


def _resolve_adapter(session: Session, target: str):
    target = (target or "").strip()
    base = os.path.basename(target)
    for source in writable_sources(session):
        path = getattr(source.adapter, "file_path", source.identifier)
        if source.name == target or source.name == base:
            return source.adapter
        if os.path.basename(str(path)) == base or str(path) == target:
            return source.adapter
        if source.identifier == target:
            return source.adapter
    raise KeyError(f"No writable merged source matching {target!r}")


def _try_live_adapter(file_path: Optional[str]):
    """If LibreOffice has this file open (UNO listening), return a live adapter."""
    if not file_path:
        return None
    try:
        from adapters.libreoffice.libreoffice_adapter import LibreOfficeAdapter

        return LibreOfficeAdapter.for_open_file(file_path)
    except Exception:
        return None


def ensure_live_bridge() -> bool:
    """Best-effort: open UNO socket on the running LibreOffice. True if listening."""
    try:
        from adapters.libreoffice import uno_bridge

        if uno_bridge.port_is_open():
            return True
        uno_bridge.ensure_uno_listening()
        return uno_bridge.port_is_open()
    except Exception:
        return False


def _dispatch_excel_op(adapter, op: dict[str, Any], name: str) -> ChangePreview:
    """Map a JSON op onto ExcelAdapter or LibreOfficeAdapter (Calc)."""
    sheet = op.get("sheet") or None

    if name == "write_cell":
        return adapter.write_cell(str(op["cell"]), op.get("value"), sheet_name=sheet)
    if name == "write_range":
        values = op.get("values")
        if not isinstance(values, list):
            raise ValueError("write_range needs values: [[...]]")
        start = op.get("start_cell") or op.get("cell") or "A1"
        return adapter.write_range(str(start), values, sheet_name=sheet)
    if name == "clear_range":
        rng = op.get("range") or op.get("cell_range") or op.get("cell")
        if not rng:
            raise ValueError("clear_range needs range")
        return adapter.clear_range(str(rng), sheet_name=sheet)
    if name == "append_row":
        values = op.get("values")
        if not isinstance(values, list):
            raise ValueError("append_row needs values: []")
        return adapter.append_row(values, sheet_name=sheet)
    if name == "insert_row":
        return adapter.insert_row(
            int(op["row"]),
            values=op.get("values"),
            sheet_name=sheet,
        )
    if name == "delete_row":
        return adapter.delete_row(int(op["row"]), sheet_name=sheet)
    if name == "insert_column":
        col = op.get("column") if op.get("column") is not None else op.get("col")
        if col is None:
            raise ValueError("insert_column needs column")
        return adapter.insert_column(col, sheet_name=sheet)
    if name == "delete_column":
        col = op.get("column") if op.get("column") is not None else op.get("col")
        if col is None:
            raise ValueError("delete_column needs column")
        return adapter.delete_column(col, sheet_name=sheet)
    if name == "create_sheet":
        sheet_name = op.get("name") or op.get("sheet_name") or op.get("new_name")
        if not sheet_name:
            raise ValueError("create_sheet needs name")
        return adapter.create_sheet(str(sheet_name), index=op.get("index"))
    if name == "rename_sheet":
        old_name = op.get("old_name") or op.get("sheet") or op.get("name")
        new_name = op.get("new_name") or op.get("to")
        if not old_name or not new_name:
            raise ValueError("rename_sheet needs old_name and new_name")
        return adapter.rename_sheet(str(old_name), str(new_name))
    if name == "delete_sheet":
        sheet_name = op.get("name") or op.get("sheet_name") or op.get("sheet")
        if not sheet_name:
            raise ValueError("delete_sheet needs name")
        return adapter.delete_sheet(str(sheet_name))
    if name == "copy_sheet":
        src = op.get("name") or op.get("sheet") or op.get("old_name")
        new_name = op.get("new_name") or op.get("to")
        if not src or not new_name:
            raise ValueError("copy_sheet needs name and new_name")
        return adapter.copy_sheet(str(src), str(new_name))
    raise ValueError(f"Unsupported excel op: {name}")


def _dispatch_word_op(adapter, op: dict[str, Any], name: str) -> ChangePreview:
    if name == "write_paragraph":
        return adapter.write_paragraph(int(op["index"]), str(op.get("value") or ""))
    if name == "append_paragraph":
        return adapter.append_paragraph(str(op.get("value") or ""))
    if name == "delete_paragraph":
        return adapter.delete_paragraph(int(op["index"]))
    if name == "insert_paragraph":
        return adapter.insert_paragraph(int(op["index"]), str(op.get("value") or ""))
    if name == "write_table_cell":
        return adapter.write_table_cell(
            int(op.get("table", 0)),
            int(op.get("row", 0)),
            int(op.get("col", op.get("column", 0))),
            str(op.get("value") or ""),
        )
    raise ValueError(f"Unsupported word op: {name}")


def _dispatch_ppt_op(adapter, op: dict[str, Any], name: str) -> ChangePreview:
    if name == "replace_text":
        return adapter.replace_text(str(op.get("old") or ""), str(op.get("new") or ""))
    if name == "set_slide_text":
        return adapter.set_slide_text(int(op.get("slide", 1)), str(op.get("value") or ""))
    if name == "append_slide_text":
        return adapter.append_slide_text(
            int(op.get("slide", 1)), str(op.get("value") or "")
        )
    if name == "add_slide":
        return adapter.add_slide(op.get("title") or op.get("value"))
    if name == "delete_slide":
        return adapter.delete_slide(int(op.get("slide", op.get("index", 1))))
    raise ValueError(f"Unsupported ppt op: {name}")


def _op_to_preview(session: Session, op: dict[str, Any]) -> ChangePreview:
    if not isinstance(op, dict):
        raise TypeError("op must be an object")
    name = str(op.get("op") or "").strip()
    target = str(op.get("target") or "")

    if name in _TERMINAL_OPS:
        adapter = _resolve_any_adapter(session, target or "local-shell", kinds={"terminal"})
        if name == "run_command":
            return adapter.run_command(str(op.get("command") or op.get("value") or ""))
        raise ValueError(f"Unsupported terminal op: {name}")

    if name in _BROWSER_OPS:
        adapter = _resolve_any_adapter(session, target or "active-tab", kinds={"browser"})
        if name == "navigate":
            return adapter.navigate(str(op.get("url") or op.get("value") or ""))
        if name == "google_search":
            return adapter.google_search(str(op.get("query") or op.get("value") or ""))
        if name == "activate_tab":
            return adapter.activate_tab(op.get("tab_id") or op.get("value"))
        raise ValueError(f"Unsupported browser op: {name}")

    file_adapter = _resolve_adapter(session, target)
    path = getattr(file_adapter, "file_path", None) or getattr(file_adapter, "source", None)

    live = _try_live_adapter(path)
    if live is not None:
        from adapters.libreoffice import uno_bridge

        doc = live._ensure_doc()
        if uno_bridge.is_calc(doc) and name in _EXCEL_OPS:
            return _dispatch_excel_op(live, op, name)
        if uno_bridge.is_writer(doc) and name in _WORD_OPS:
            return _dispatch_word_op(live, op, name)

    # Fall back to on-disk adapters (still confirm-first)
    adapter = file_adapter
    kind = adapter.kind

    if kind == "excel":
        if name not in _EXCEL_OPS:
            raise ValueError(f"Unsupported excel op: {name}")
        return _dispatch_excel_op(adapter, op, name)

    if kind == "word":
        if name not in _WORD_OPS:
            raise ValueError(f"Unsupported word op: {name}")
        return _dispatch_word_op(adapter, op, name)

    if kind == "text":
        if name == "replace_text":
            return adapter.replace_text(str(op.get("old") or ""), str(op.get("new") or ""))
        if name == "append_text":
            return adapter.append_text(str(op.get("value") or ""))
        if name == "write_all":
            return adapter.write_all(str(op.get("value") or ""))
        raise ValueError(f"Unsupported text op: {name}")

    if kind == "ppt":
        if name not in _PPT_OPS:
            raise ValueError(f"Unsupported ppt op: {name}")
        return _dispatch_ppt_op(adapter, op, name)

    raise ValueError(f"Unsupported kind/op: {kind}/{name}")

def apply_previews(previews: list[ChangePreview]) -> list[str]:
    """Apply each preview; return human-readable notes."""
    notes = []
    for preview in previews:
        preview.apply()
        notes.append(str(preview))
    return notes


def reload_writable_adapters(session: Session) -> None:
    for source in writable_sources(session):
        adapter = source.adapter
        reload_fn = getattr(adapter, "reload", None)
        if callable(reload_fn):
            try:
                reload_fn()
            except Exception:
                pass
