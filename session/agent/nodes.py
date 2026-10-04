"""
Agent nodes for Merge AI.

Pipeline:
  intent → gather → reason → plan → verify → (feedback loop) → prepare_edits | answer
"""

from __future__ import annotations

from typing import Any

from session.agent import llm
from session.agent.state import AgentState
from session.edit_engine import _op_to_preview, writable_sources

CONFIDENCE_THRESHOLD = 0.7
MAX_FEEDBACK_REVISIONS = 3


def _parse_confidence(data: dict, default: float = 0.5) -> float:
    try:
        return max(0.0, min(1.0, float(data.get("confidence", default))))
    except (TypeError, ValueError):
        return default


# ---------- 1. INTENT ----------

def _last_assistant_was_clarify(session) -> bool:
    for msg in reversed(session.messages or []):
        if msg.get("role") != "assistant":
            continue
        content = (msg.get("content") or "").strip()
        if content == "Thinking…":
            continue
        # Prior turn asked the user something
        return content.endswith("?") or content.lower().startswith(
            ("i need", "which ", "what exactly", "can you clarify", "please specify")
        )
    return False


def node_intent(state: AgentState) -> AgentState:
    """Classify user intent; almost never ask the same clarify again."""
    # If we already asked a clarifying question, the user's reply must be acted on
    if _last_assistant_was_clarify(state.session):
        from session.edit_engine import looks_like_edit

        state.intent = "edit" if looks_like_edit(state.user_message) else "answer"
        state.intent_confidence = 0.9
        state.intent_rationale = "Follow-up after prior clarify — do not ask again"
        state.log("intent", f"{state.intent} (forced follow-up, no re-clarify)")
        return state

    names = ", ".join(state.source_names) or "(none yet)"
    data = llm.chat_json(
        system=(
            "You are the Intent node for Merge AI. Classify the user's message.\n"
            "intents:\n"
            '- "edit": user wants to create/change/delete data or sheets/paragraphs\n'
            '- "answer": user wants information, analysis, summary, explanation\n'
            '- "clarify": ONLY if the message is empty/nonsense with zero actionable ask\n'
            "Rules:\n"
            "- DEFAULT to answer. Almost never use clarify.\n"
            "- Prefer answer for what/why/how/summarize/list/explain/analyze.\n"
            "- Prefer edit for clear mutations (create sheet, set cell, delete row, …).\n"
            "- If slightly ambiguous, still choose answer or edit and proceed with "
            "best assumptions — do NOT ask the user a question.\n"
            "- NEVER restate or repeat the user's question back to them.\n"
            "JSON schema: "
            '{"intent":"edit|answer|clarify","confidence":0-1,'
            '"rationale":"short","clarify_question":""}'
        ),
        user=(
            f"Merged sources: {names}\n"
            f"User message: {state.user_message}"
        ),
        max_tokens=300,
    )
    intent = str(data.get("intent") or "answer").lower().strip()
    if intent not in ("edit", "answer", "clarify"):
        intent = "answer"
    # Soft-block clarify unless confidence is very high
    try:
        conf = float(data.get("confidence") or 0.5)
    except (TypeError, ValueError):
        conf = 0.5
    if intent == "clarify" and conf < 0.85:
        intent = "answer"
        data["rationale"] = (str(data.get("rationale") or "") + " → coerced to answer").strip()
    state.intent = intent
    state.intent_confidence = conf
    state.intent_rationale = str(data.get("rationale") or "")
    if intent == "clarify":
        q = str(data.get("clarify_question") or "").strip()
        # One short clarify only — never echo the user message
        if q and q.rstrip("?").strip().lower() != state.user_message.rstrip("?").strip().lower():
            state.final_answer = q
        else:
            state.intent = "answer"
            state.final_answer = ""
            state.log("intent", "clarify rejected (echo/empty) → answer")
            return state
    state.log(
        "intent",
        f"{state.intent} (conf={state.intent_confidence:.2f}) {state.intent_rationale}",
    )
    return state

# ---------- 2. GATHER ----------

def _excel_question_lines(adapter, *, max_rows: int = 40) -> list[str]:
    """Heuristic: cells that look like questions or blank answer neighbors."""
    lines: list[str] = []
    try:
        sheets = adapter.list_sheets()
    except Exception:
        return lines
    for sheet in sheets[:8]:
        try:
            # Prefer structured dump if available
            body = adapter.read_all(max_rows_per_sheet=max_rows)
        except Exception:
            continue
        for raw in body.splitlines():
            t = raw.strip()
            if not t or t.startswith("---") or t.startswith("Sheet"):
                continue
            low = t.lower()
            if "?" in t or "question" in low or "answer" in low:
                lines.append(t)
            elif len(lines) < 40 and len(t) > 12:
                # Keep some longer cells as possible questionnaire prompts
                lines.append(t)
            if len(lines) >= 60:
                return lines
    return lines


def _pdf_citation_chunks(session, queries: list[str], *, max_hits: int = 24) -> list[str]:
    from adapters.pdf.pdf_adapter import PdfAdapter, keywords_from_text

    terms: list[str] = []
    for q in queries:
        q = (q or "").strip()
        if not q:
            continue
        if len(q) <= 80:
            terms.append(q)
        terms.extend(keywords_from_text(q, max_terms=6))
    # de-dupe preserve order
    seen = set()
    uniq: list[str] = []
    for t in terms:
        key = t.lower()
        if key in seen or len(t) < 3:
            continue
        seen.add(key)
        uniq.append(t)

    cite_lines: list[str] = []
    for source in session.sources.values():
        adapter = source.adapter
        if not adapter or adapter.kind != "pdf":
            continue
        if not isinstance(adapter, PdfAdapter):
            # still try duck-typing
            search_hits = getattr(adapter, "search_hits", None)
            if not callable(search_hits):
                continue
        for term in uniq[:12]:
            try:
                hits = adapter.search_hits(term, max_hits=4)
            except Exception:
                continue
            for h in hits:
                line = (
                    f'{source.name} Page {h["page"]} — "{h["snippet"]}"'
                )
                if line not in cite_lines:
                    cite_lines.append(line)
                if len(cite_lines) >= max_hits:
                    return [
                        "### CITATIONS (PDF page + snippet; use these for grounding)\n"
                        + "\n".join(cite_lines)
                    ]
    if not cite_lines:
        return []
    return [
        "### CITATIONS (PDF page + snippet; use these for grounding)\n"
        + "\n".join(cite_lines)
    ]


def node_gather(state: AgentState) -> AgentState:
    """Collect grounded context + PDF citations, capped under Azure limits."""
    from session.agent.context_budget import (
        excel_max_rows,
        fit_chunks,
        max_source_chars,
        truncate_text,
        estimate_tokens,
    )

    chunks: list[str] = []
    names: list[str] = []
    vision_path = None
    per = max_source_chars()
    has_excel = False
    has_pdf = False
    question_lines: list[str] = []

    for source in state.session.sources.values():
        adapter = source.adapter
        if not adapter:
            continue
        names.append(source.name)
        kind = adapter.kind
        try:
            if kind == "excel":
                has_excel = True
                sheets = ", ".join(adapter.list_sheets())
                body = adapter.read_all(max_rows_per_sheet=excel_max_rows())
                body = truncate_text(body, per, source.name)
                chunks.append(
                    f"### FILE: {source.name} (excel)\nSheets: [{sheets}]\n{body}"
                )
                question_lines.extend(_excel_question_lines(adapter))
            elif kind == "pdf":
                has_pdf = True
                body = truncate_text(adapter.read_all(), per, source.name)
                chunks.append(f"### FILE: {source.name} ({kind})\n{body}")
            elif kind in ("word", "text", "ppt"):
                body = truncate_text(adapter.read_all(), per, source.name)
                chunks.append(f"### FILE: {source.name} ({kind})\n{body}")
            elif kind == "terminal":
                body = truncate_text(adapter.read_all(), min(per, 8000), source.name)
                chunks.append(f"### TERMINAL: {source.name}\n{body}")
            elif kind == "browser":
                body = truncate_text(adapter.read_all(), min(per, 8000), source.name)
                chunks.append(f"### BROWSER: {source.name}\n{body}")
            elif kind == "vision" and getattr(adapter, "_last_image_path", None):
                vision_path = adapter._last_image_path
                state.has_vision = True
                chunks.append(
                    f"### SCREENSHOT: {source.name} (vision)\n"
                    "(Image attached separately at reduced resolution.)"
                )
            else:
                chunks.append(f"### SOURCE: {source.name} ({kind})")
        except Exception as e:
            chunks.append(f"### SOURCE: {source.name} ({kind}) — read error: {e}")

    # PDF retrieval from user message + excel questionnaire
    if has_pdf:
        queries = [state.user_message] + question_lines[:20]
        chunks.extend(_pdf_citation_chunks(state.session, queries))

    if has_excel and has_pdf and question_lines:
        chunks.append(
            "### QUESTIONNAIRE (Excel cells that may need PDF answers)\n"
            + "\n".join(question_lines[:40])
            + "\n\nWhen the user asks to fill answers from the PDF, write values "
            "into the Excel target using write_cell/write_range only, grounded in CITATIONS."
        )

    state.source_names = names
    state.context = fit_chunks(chunks)
    state.vision_image_path = vision_path
    state.log(
        "gather",
        f"{len(names)} sources, context_chars={len(state.context)} "
        f"(~{estimate_tokens(state.context)} tokens)",
    )
    return state

# ---------- 3. REASON ----------

def node_reason(state: AgentState) -> AgentState:
    """Extract literal goal + constraints to stop command drift / hallucination."""
    data = llm.chat_json(
        system=(
            "You are the Reason node. Restate the user's request literally.\n"
            "Use ONLY the user message + source names. Do not invent facts from the world.\n"
            "JSON schema:\n"
            "{\n"
            '  "user_goal": "one sentence restating what they asked",\n'
            '  "must_do": ["concrete obligations"],\n'
            '  "must_not": ["things not requested — do not do these"],\n'
            '  "unknowns": ["missing info needed to execute safely"]\n'
            "}\n"
            "If they asked for analysis/summary, must_do includes covering key points with evidence.\n"
            "If they asked to create a sheet named X, must_do includes create_sheet X only — "
            "must_not includes unrelated cell edits."
        ),
        user=(
            f"Sources: {', '.join(state.source_names)}\n"
            f"Intent: {state.intent}\n"
            f"User message: {state.user_message}"
        ),
        max_tokens=400,
    )
    state.user_goal = str(data.get("user_goal") or state.user_message).strip()
    state.must_do = [str(x) for x in (data.get("must_do") or []) if str(x).strip()]
    state.must_not = [str(x) for x in (data.get("must_not") or []) if str(x).strip()]
    state.unknowns = [str(x) for x in (data.get("unknowns") or []) if str(x).strip()]
    state.log("reason", f"goal={state.user_goal!r} must_do={len(state.must_do)}")
    return state


# ---------- 4. PLAN ----------

_EDIT_OP_SPEC = """
Allowed Excel/Calc ops:
- write_cell {op,target,sheet,cell,value}
- write_range {op,target,sheet,start_cell,values:[[...]]}
- clear_range {op,target,sheet,range}
- append_row {op,target,sheet,values:[...]}
- insert_row {op,target,sheet,row,values}
- delete_row {op,target,sheet,row}
- insert_column {op,target,sheet,column}
- delete_column {op,target,sheet,column}
- create_sheet {op,target,name,index?}
- rename_sheet {op,target,old_name,new_name}
- delete_sheet {op,target,name}
- copy_sheet {op,target,name,new_name}
Word: write_paragraph, append_paragraph, delete_paragraph,
  insert_paragraph {op,target,index,value},
  write_table_cell {op,target,table,row,col,value}
Text: replace_text, append_text, write_all
PPT: replace_text, set_slide_text {op,target,slide,value},
  append_slide_text {op,target,slide,value},
  add_slide {op,target,title?}, delete_slide {op,target,slide}
Terminal: run_command {op,target:"local-shell"|terminal name,command}
Browser: navigate {op,target,url}, google_search {op,target,query},
  activate_tab {op,target,tab_id}
target = basename of a merged file OR kind source name (terminal/browser).
When filling Excel from PDF, ONLY use values present in CITATIONS.
"""


def node_plan(state: AgentState) -> AgentState:
    """Plan edits OR an evidence-backed answer outline."""
    if state.intent == "clarify":
        return state

    if state.intent == "edit":
        writable = [s.name for s in writable_sources(state.session)]
        exec_kinds = []
        for source in state.session.sources.values():
            ad = source.adapter
            if ad and ad.kind in ("terminal", "browser"):
                exec_kinds.append(f"{source.name} ({ad.kind})")
        data = llm.chat_json(
            system=(
                "You are the Plan node for document edits / terminal / browser actions.\n"
                "Produce the MINIMUM ops that EXACTLY satisfy must_do.\n"
                "Do NOT add extra ops for things in must_not.\n"
                "If something is unknown, pick the most likely sheet/file from the catalog "
                "and proceed. Set needs_clarify=true ONLY when zero writable/executable "
                "targets exist.\n"
                "Never ask the user a question in plan_summary.\n"
                "If CITATIONS + QUESTIONNAIRE are present, fill Excel answer cells from "
                "PDF citations (write_cell) — do not invent values.\n"
                f"{_EDIT_OP_SPEC}\n"
                "JSON: "
                '{"ops":[...],"needs_clarify":false,"clarify_question":"",'
                '"plan_summary":"one line"}'
            ),
            user=(
                f"User goal: {state.user_goal}\n"
                f"must_do: {state.must_do}\n"
                f"must_not: {state.must_not}\n"
                f"unknowns: {state.unknowns}\n"
                f"Writable files: {writable}\n"
                f"Executable sources: {exec_kinds}\n"
                f"All sources: {state.source_names}\n\n"
                f"--- SOURCE DATA ---\n{state.context}\n--- END ---\n\n"
                f"Original message: {state.user_message}"
            ),
            max_tokens=700,
        )
        if data.get("needs_clarify"):
            # Do not loop clarifies — answer with best effort / say what's missing once
            ops = data.get("ops") or []
            if isinstance(ops, list) and ops:
                state.ops = ops
                state.log("plan", "clarify requested but ops present — proceed")
                return state
            if writable or exec_kinds:
                # Have targets — proceed empty ops as gap answer instead of re-ask
                state.intent = "answer"
                state.answer_outline = [
                    "Explain what is missing to perform the edit, using source names.",
                    "Do not ask the same question again.",
                ]
                state.log("plan", "edit clarify suppressed → answer with gap explanation")
                return state
            state.intent = "answer"
            state.answer_outline = [
                "Explain what is missing to perform the edit, using source names.",
                "Do not ask the same question again.",
            ]
            state.log("plan", "edit clarify suppressed → answer with gap explanation")
            return state
        ops = data.get("ops") or []
        state.ops = ops if isinstance(ops, list) else []
        state.log("plan", f"edit ops={len(state.ops)} {data.get('plan_summary','')}")
        return state

    # answer plan
    data = llm.chat_json(
        system=(
            "You are the Plan node for answering from merged sources.\n"
            "Build an outline that fully addresses the user_goal.\n"
            "Every outline point must be supportable by SOURCE DATA — no outside knowledge.\n"
            "Quote short evidence snippets (cells, lines, PDF pages) in evidence[].\n"
            "For PDF facts use evidence like: Page 3 — \"quote\".\n"
            "If the user asks WHERE something is located, outline must lead with page/location.\n"
            "If the sources lack the answer, put that in the outline as a statement — "
            "do NOT turn this into a question for the user.\n"
            "JSON: "
            '{"answer_outline":["point 1",...],"evidence":["Page N — quote or Sheet!A1=..."],'
            '"cannot_answer":false,"missing":""}'
        ),
        user=(
            f"User goal: {state.user_goal}\n"
            f"must_do: {state.must_do}\n"
            f"must_not: {state.must_not}\n\n"
            f"--- SOURCE DATA ---\n{state.context}\n--- END ---\n\n"
            f"Question: {state.user_message}"
        ),
        max_tokens=700,
    )
    state.answer_outline = [str(x) for x in (data.get("answer_outline") or []) if str(x).strip()]
    state.evidence = [str(x) for x in (data.get("evidence") or []) if str(x).strip()]
    if data.get("cannot_answer") and not state.answer_outline:
        missing = str(data.get("missing") or "the merged sources do not contain that information")
        # Statement, not a repeated question
        state.final_answer = (
            f"I can't find that in the merged sources ({missing}). "
            "Merge the relevant file/window or rephrase using what is already merged."
        )
        state.intent = "answer"
    state.log(
        "plan",
        f"outline={len(state.answer_outline)} evidence={len(state.evidence)}",
    )
    return state

# ---------- 5. VERIFY ----------

def node_verify(state: AgentState) -> AgentState:
    """Check the plan obeys the command; confidence-gated (no golden answers)."""
    if state.intent == "clarify" and state.final_answer:
        state.verified = True
        state.answer_confidence = 1.0
        return state

    if state.intent == "edit":
        data = llm.chat_json(
            system=(
                "You are the Verify node. Decide if the proposed ops OBEDIENCE-check pass.\n"
                "PASS only if:\n"
                "1) Every must_do item is covered by at least one op\n"
                "2) No op pursues must_not items\n"
                "3) Ops match the literal user request (no surprise sheets/cells)\n"
                "4) Ops use valid targets from writable files\n"
                "5) If filling from PDF/CITATIONS, each written value must appear in that evidence\n"
                "Also set confidence 0-1 for how sure you are the ops are correct and grounded.\n"
                "JSON: "
                '{"pass":true|false,"confidence":0.0,"issues":["..."],'
                '"suggested_ops":[]}'
            ),
            user=(
                f"User message: {state.user_message}\n"
                f"Goal: {state.user_goal}\n"
                f"must_do: {state.must_do}\n"
                f"must_not: {state.must_not}\n"
                f"Proposed ops: {state.ops}\n"
                f"Writable: {[s.name for s in writable_sources(state.session)]}\n\n"
                f"--- SOURCE / CITATIONS (excerpt) ---\n{state.context[:12000]}\n--- END ---"
            ),
            max_tokens=500,
        )
        conf = _parse_confidence(data, 0.5)
        state.answer_confidence = conf
        raw_pass = bool(data.get("pass"))
        state.verified = raw_pass and conf >= CONFIDENCE_THRESHOLD
        state.verify_issues = [str(x) for x in (data.get("issues") or [])]
        if conf < CONFIDENCE_THRESHOLD and not state.verify_issues:
            state.verify_issues.append(
                f"confidence {conf:.2f} below {CONFIDENCE_THRESHOLD}"
            )
        if not state.verified:
            suggested = data.get("suggested_ops")
            if isinstance(suggested, list) and suggested:
                state.ops = suggested
                state.log(
                    "verify",
                    f"FAIL→revised ops ({len(state.ops)}) conf={conf:.2f}: "
                    f"{state.verify_issues}",
                )
            else:
                state.log("verify", f"FAIL conf={conf:.2f}: {state.verify_issues}")
        else:
            state.log("verify", f"PASS edit plan conf={conf:.2f}")
        return state

    # answer verify
    data = llm.chat_json(
        system=(
            "You are the Verify node for answers. Check the outline against sources.\n"
            "PASS if outline addresses the user_goal and evidence snippets are plausible "
            "from the provided SOURCE DATA / CITATIONS. FAIL if outline invents "
            "numbers/names not in sources or ignores the asked question.\n"
            "Set confidence 0-1 for grounding quality.\n"
            "JSON: "
            '{"pass":true|false,"confidence":0.0,"issues":["..."],'
            '"revised_outline":[],"revised_evidence":[]}'
        ),
        user=(
            f"User message: {state.user_message}\n"
            f"Goal: {state.user_goal}\n"
            f"Outline: {state.answer_outline}\n"
            f"Evidence: {state.evidence}\n\n"
            f"--- SOURCE DATA (excerpt) ---\n{state.context[:12000]}\n--- END ---"
        ),
        max_tokens=500,
    )
    conf = _parse_confidence(data, 0.5)
    state.answer_confidence = conf
    raw_pass = bool(data.get("pass"))
    state.verified = raw_pass and conf >= CONFIDENCE_THRESHOLD
    state.verify_issues = [str(x) for x in (data.get("issues") or [])]
    if conf < CONFIDENCE_THRESHOLD and not state.verify_issues:
        state.verify_issues.append(
            f"confidence {conf:.2f} below {CONFIDENCE_THRESHOLD}"
        )
    if not state.verified:
        rev_o = data.get("revised_outline")
        rev_e = data.get("revised_evidence")
        if isinstance(rev_o, list) and rev_o:
            state.answer_outline = [str(x) for x in rev_o]
        if isinstance(rev_e, list) and rev_e:
            state.evidence = [str(x) for x in rev_e]
        state.needs_answer_revision = True
        state.log("verify", f"FAIL→revised outline conf={conf:.2f}: {state.verify_issues}")
    else:
        state.needs_answer_revision = False
        state.log("verify", f"PASS answer plan conf={conf:.2f}")
    return state


# ---------- 6. FEEDBACK ----------

def node_feedback(state: AgentState) -> AgentState:
    """Up to MAX_FEEDBACK_REVISIONS cycles when verify fails or confidence is low."""
    if state.intent == "clarify":
        return state

    while not state.verified and state.revision_count < MAX_FEEDBACK_REVISIONS:
        state.revision_count += 1
        state.log(
            "feedback",
            f"revision #{state.revision_count} "
            f"(conf={state.answer_confidence:.2f})",
        )
        issues = "; ".join(state.verify_issues) or "plan did not obey the command"
        state.must_do = list(state.must_do) + [
            f"Fix verify issues (revision {state.revision_count}): {issues}"
        ]
        node_plan(state)
        node_verify(state)

    if not state.verified:
        state.log(
            "feedback",
            f"max revisions reached ({MAX_FEEDBACK_REVISIONS}); "
            f"proceeding with best effort conf={state.answer_confidence:.2f}",
        )
        # Still regenerate answer once with issues if answer path is weak
        if state.intent == "answer":
            state.needs_answer_revision = True
    return state


# ---------- 7. PREPARE EDITS ----------

def node_prepare_edits(state: AgentState) -> AgentState:
    """Turn verified ops into ChangePreview objects."""
    if state.intent != "edit":
        return state
    previews = []
    errors = []
    for op in state.ops:
        try:
            if not isinstance(op, dict):
                errors.append(f"invalid op: {op}")
                continue
            # normalize aliases the model sometimes emits
            op = dict(op)
            if op.get("op") == "new_sheet":
                op["op"] = "create_sheet"
            previews.append(_op_to_preview(state.session, op))
        except Exception as e:
            errors.append(f"{op}: {e}")
    state.previews = previews
    state.preview_errors = errors
    if not previews and errors:
        state.final_answer = "Could not build edits:\n" + "\n".join(errors)
        state.intent = "answer"
    state.log("prepare_edits", f"previews={len(previews)} errors={len(errors)}")
    return state


def _strip_clarifying_questions(text: str) -> str:
    """Turn question-shaped assistant replies into statements (stop chat loops)."""
    t = (text or "").strip()
    if not t:
        return t
    # If the whole reply is one/few clarifying questions, rewrite as a statement
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    if not lines:
        return t
    question_lines = sum(1 for ln in lines if ln.endswith("?"))
    if question_lines >= max(1, len(lines) - 1) and t.endswith("?"):
        # Collapse to a non-question note
        core = t.rstrip("?").strip()
        if core.lower().startswith(("which ", "what ", "can you", "could you", "please specify")):
            return (
                "I'll proceed with the best match from the merged sources. "
                "If that isn't right, name the exact sheet/file in your next message."
            )
        return core + "."
    # Drop a trailing clarifying question paragraph
    if len(lines) >= 2 and lines[-1].endswith("?") and lines[-1].lower().startswith(
        ("which ", "what ", "can you", "could you", "please ", "do you ", "should i ")
    ):
        return "\n".join(lines[:-1]).strip()
    return t


# ---------- 8. ANSWER ----------

def node_answer(state: AgentState) -> AgentState:
    """Compose a proper, grounded answer (not a 2-sentence stub)."""
    if state.intent == "edit":
        if state.previews:
            # Overlay will confirm; provide a clear summary
            lines = [f"Ready to apply {len(state.previews)} change(s) for: {state.user_goal}"]
            for p in state.previews:
                lines.append(f"• {p}")
            if state.preview_errors:
                lines.append("Skipped:")
                lines.extend(f"• {e}" for e in state.preview_errors)
            state.final_answer = "\n".join(lines)
        return state

    if state.intent == "clarify" and state.final_answer:
        # Still sanitize — never leave a repeated question loop seed
        state.final_answer = _strip_clarifying_questions(state.final_answer)
        return state

    follow_up = _last_assistant_was_clarify(state.session)
    no_ask_rule = (
        "CRITICAL: Do NOT ask the user any questions. End with a period, never '?'.\n"
        "If information is missing, state the assumption you used and what you found.\n"
    )
    if follow_up:
        no_ask_rule += (
            "The previous assistant message already asked for clarification; "
            "the user has now replied. Act on their reply — never re-ask.\n"
        )

    # Vision path: compressed image + short text context (avoids context_length_exceeded)
    if state.has_vision and state.vision_image_path:
        from adapters.vision.vision_adapter import VisionAdapter

        vision = VisionAdapter("agent-vision")
        prompt = (
            "Answer thoroughly using the screenshot and text context. "
            "Obey the user_goal. Ground claims in what is visible/in context. "
            "Do not invent. Ignore unrelated UI chrome.\n"
            f"{no_ask_rule}\n"
            f"User goal: {state.user_goal}\n"
            f"must_do: {state.must_do}\n"
            f"Outline: {state.answer_outline}\n"
            f"Evidence: {state.evidence}\n\n"
            f"Text context:\n{state.context[:6000]}\n\n"
            f"Question: {state.user_message}"
        )
        state.final_answer = _strip_clarifying_questions(
            vision.describe(state.vision_image_path, prompt).strip()
        )
        state.log("answer", f"vision answer chars={len(state.final_answer)}")
        return state

    state.final_answer = _strip_clarifying_questions(
        llm.chat_text(
            system=(
                "You are Merge AI's Answer node.\n"
                "Write a clear, complete answer to the user using ONLY the source data "
                "and the verified outline/evidence.\n"
                "Rules:\n"
                "1. Obey the user_goal — answer what was asked, not a different question.\n"
                "2. Use evidence (sheet names, cells, quotes). If data is missing, say so once.\n"
                "3. Do NOT invent numbers, names, or rows that are not in the sources.\n"
                "4. For PDF: cite as Page N — \"snippet\". If asked where something is, "
                "lead with the page and quote.\n"
                "5. Structure: short intro, then bullets/sections as needed, then a brief takeaway.\n"
                "6. Be thorough enough to be useful — not one terse sentence unless the ask is tiny.\n"
                "7. No preamble like 'Sure!' or 'Based on the documents'.\n"
                "8. Do NOT ask the user clarifying questions. Do NOT repeat their question back.\n"
                "9. Never end with a question mark.\n"
                f"{no_ask_rule}"
            ),
            user=(
                f"User goal: {state.user_goal}\n"
                f"must_do: {state.must_do}\n"
                f"must_not: {state.must_not}\n"
                f"Outline: {state.answer_outline}\n"
                f"Evidence: {state.evidence}\n"
                f"Verify issues (if any): {state.verify_issues}\n\n"
                f"--- SOURCE DATA ---\n{state.context[:24000]}\n--- END ---\n\n"
                f"Question: {state.user_message}"
            ),
            max_tokens=800,
        )
    )
    state.log("answer", f"text answer chars={len(state.final_answer)}")
    return state
