"""
GTK overlay shell — Phase 2.

Compact mode: small docked window.
Maximized mode: ChatGPT/Claude-style layout — sidebar of named chats +
main conversation thread.
"""

from __future__ import annotations

import os
import re
import sys
import threading
from typing import Optional

from session.session_manager import Session, SessionManager
from overlay.window_utils import (
    ActiveWindowInfo,
    WindowUtilsError,
    classify_window,
    friendly_window_name,
    get_active_window,
    get_window_geometry,
    guess_file_path_for_window,
    is_document_window,
    is_overlay_window,
)

try:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GLib, Gtk, Pango
except (ImportError, ValueError) as e:
    gi = None  # type: ignore
    Gdk = None  # type: ignore
    GLib = None  # type: ignore
    Gtk = None  # type: ignore
    Pango = None  # type: ignore
    _GI_IMPORT_ERROR = e
else:
    _GI_IMPORT_ERROR = None


POLL_MS = 400
OVERLAY_MARGIN = 12
COMPACT_SIZE = (360, 480)
SIDEBAR_W = 260


class MergeOverlayApp(Gtk.Window if Gtk is not None else object):
    """Always-available Merge AI overlay window."""

    def __init__(self):
        if Gtk is None:
            raise RuntimeError(
                "PyGObject/GTK3 is required for the overlay. "
                "Install: sudo apt-get install -y python3-gi gir1.2-gtk-3.0\n"
                f"Import error: {_GI_IMPORT_ERROR}"
            )

        super().__init__(title="Merge AI")
        self.session_manager = SessionManager()
        self.session_manager.new_session("New chat")
        self.pinned = False
        self._own_xids: set[str] = set()
        self._last_external: Optional[ActiveWindowInfo] = None
        self._follow_window: Optional[ActiveWindowInfo] = None
        self._docked_to_id: Optional[str] = None
        self._user_moved = False
        self._toast_timeout_id: Optional[int] = None
        self._agent_busy = False
        self._agent_request_id = 0
        self._agent_session_id: Optional[str] = None

        self.set_default_size(*COMPACT_SIZE)
        self.set_resizable(True)
        self.set_keep_above(False)
        self.set_border_width(0)
        self.set_type_hint(Gdk.WindowTypeHint.NORMAL)

        self.connect("destroy", self._on_destroy)
        self.connect("realize", self._on_realize)
        self.connect("configure-event", self._on_configure)

        # Title bar: system provides close only; our — / □ sit beside it (no duplicates)
        titlebar = Gtk.HeaderBar()
        titlebar.set_show_close_button(True)
        titlebar.set_decoration_layout(":close")
        titlebar.set_title("Merge AI")
        self.set_titlebar(titlebar)

        self.new_chat_button = Gtk.Button(label="+ New chat")
        self.new_chat_button.set_tooltip_text("Start a new chat")
        self.new_chat_button.connect("clicked", self._on_new_chat)
        titlebar.pack_start(self.new_chat_button)

        self.pin_button = Gtk.ToggleButton(label="Pin")
        self.pin_button.set_tooltip_text("Keep overlay always on top")
        self.pin_button.connect("toggled", self._on_pin_toggled)
        titlebar.pack_start(self.pin_button)

        # pack_end: first added is closest to close → [—][□][✕]
        self.max_button = Gtk.Button(label="□")
        self.max_button.set_tooltip_text("Maximize / restore")
        self.max_button.set_relief(Gtk.ReliefStyle.NONE)
        self.max_button.connect("clicked", self._on_maximize_clicked)
        titlebar.pack_end(self.max_button)

        self.min_button = Gtk.Button(label="—")
        self.min_button.set_tooltip_text("Minimize to taskbar")
        self.min_button.set_relief(Gtk.ReliefStyle.NONE)
        self.min_button.connect("clicked", self._on_minimize_clicked)
        titlebar.pack_end(self.min_button)

        self._titlebar = titlebar
        self._normal_size = COMPACT_SIZE
        self._is_maximized = False

        # ---- Body: sidebar (maximized) + main chat ----
        self.body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.add(self.body)

        # Sidebar chat list — visible only when maximized
        self.sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.sidebar.set_size_request(SIDEBAR_W, -1)
        self.sidebar.set_no_show_all(True)
        self.sidebar.set_margin_top(8)
        self.sidebar.set_margin_bottom(8)
        self.sidebar.set_margin_start(8)
        self.sidebar.set_margin_end(4)

        side_label = Gtk.Label(xalign=0.0)
        side_label.set_markup("<b>Chats</b>")
        self.sidebar.pack_start(side_label, False, False, 0)

        side_scroll = Gtk.ScrolledWindow()
        side_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        side_scroll.set_vexpand(True)
        self.chat_list = Gtk.ListBox()
        self.chat_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.chat_list.connect("row-selected", self._on_chat_selected)
        side_scroll.add(self.chat_list)
        self.sidebar.pack_start(side_scroll, True, True, 0)
        self.body.pack_start(self.sidebar, False, False, 0)

        # Main column
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        main.set_margin_top(8)
        main.set_margin_bottom(8)
        main.set_margin_start(8)
        main.set_margin_end(8)
        self.body.pack_start(main, True, True, 0)

        # Top merged-files bar — hidden until the first merge in this chat
        self.merged_files_section = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4
        )
        self.merged_files_section.set_margin_bottom(4)
        merged_hdr = Gtk.Label(label="Merged files:", xalign=0.0)
        merged_hdr.get_style_context().add_class("merged-hdr")
        self.merged_files_section.pack_start(merged_hdr, False, False, 0)
        self.merged_chips = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4
        )
        self.merged_files_section.pack_start(self.merged_chips, False, False, 0)
        main.pack_start(self.merged_files_section, False, False, 0)
        # Start hidden; run() also hides after show_all()
        self.merged_files_section.set_no_show_all(True)
        self.merged_files_section.hide()

        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.scrolled.set_vexpand(True)
        self.messages_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.messages_box.set_margin_top(4)
        self.messages_box.set_margin_bottom(4)
        self.messages_box.set_margin_start(4)
        self.messages_box.set_margin_end(4)
        self.scrolled.add(self.messages_box)
        main.pack_start(self.scrolled, True, True, 0)

        # Compact fallback log (unused visually when bubbles work; keep buffer API)
        self.log_view = Gtk.TextView()
        self.log_buffer = self.log_view.get_buffer()

        self.input_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.chat_entry = Gtk.Entry()
        self.chat_entry.set_placeholder_text("Message Merge AI…")
        self.chat_entry.connect("activate", self._on_send_clicked)
        self.input_row.pack_start(self.chat_entry, True, True, 0)

        self.merge_button = Gtk.Button(label="Merge")
        self.merge_button.set_tooltip_text(
            "Merge the focused window into this chat"
        )
        self.merge_button.connect("clicked", self._on_merge_clicked)
        self.input_row.pack_start(self.merge_button, False, False, 0)

        self.send_button = Gtk.Button(label="Send")
        self.send_button.connect("clicked", self._on_send_clicked)
        self.input_row.pack_start(self.send_button, False, False, 0)

        # Toast sits above the input row so compact mode never clips it
        self.toast_bar = Gtk.EventBox()
        self.toast_bar.set_halign(Gtk.Align.START)
        self.toast_bar.set_valign(Gtk.Align.END)
        self.toast_bar.set_margin_bottom(4)
        self.toast_bar.get_style_context().add_class("toast-bar")
        self.toast_bar.set_no_show_all(True)
        toast_inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toast_inner.set_margin_start(10)
        toast_inner.set_margin_end(6)
        toast_inner.set_margin_top(6)
        toast_inner.set_margin_bottom(6)
        self.toast_label = Gtk.Label(xalign=0.0)
        self.toast_label.set_line_wrap(True)
        self.toast_label.set_max_width_chars(28)
        self.toast_label.get_style_context().add_class("toast-label")
        toast_inner.pack_start(self.toast_label, True, True, 0)
        toast_close = Gtk.Button(label="✕")
        toast_close.set_relief(Gtk.ReliefStyle.NONE)
        toast_close.set_tooltip_text("Dismiss")
        toast_close.get_style_context().add_class("toast-close")
        toast_close.connect("clicked", lambda *_: self._hide_toast())
        toast_inner.pack_end(toast_close, False, False, 0)
        self.toast_bar.add(toast_inner)
        self.toast_bar.hide()
        main.pack_start(self.toast_bar, False, False, 0)
        main.pack_start(self.input_row, False, False, 0)

        self._apply_chat_css()
        self._refresh_chat_list()
        self._reload_messages()
        self.sidebar.hide()  # compact overlay: no chat sidebar

        GLib.timeout_add(POLL_MS, self._poll_active_window)

    def _apply_chat_css(self) -> None:
        css = b"""
        .msg-user {
            background-color: #ececf1;
            border-radius: 14px;
            padding: 10px 14px;
        }
        .msg-assistant {
            background-color: #f7f7f8;
            border-radius: 14px;
            padding: 10px 14px;
            border: 1px solid #e5e5e5;
        }
        .msg-role {
            opacity: 0.55;
            font-size: 11px;
            margin-bottom: 2px;
        }
        .chat-row-active {
            background-color: #e8e8ed;
            border-radius: 8px;
        }
        .merged-hdr {
            font-size: 12px;
            opacity: 0.75;
            margin-bottom: 2px;
        }
        .merged-chip {
            background-color: #e8e8ed;
            background-image: none;
            border-radius: 8px;
            border: 1px solid #d0d0d5;
            padding: 4px 6px 4px 10px;
        }
        .merged-chip-name {
            font-size: 13px;
            font-weight: 600;
        }
        .toast-bar {
            background-color: #2f2f2f;
            background-image: none;
            border-radius: 8px;
            border: 1px solid #1a1a1a;
        }
        .toast-label {
            color: #ffffff;
            font-size: 12px;
        }
        .toast-close {
            color: #ffffff;
            min-width: 22px;
            padding: 0 4px;
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    # ---------- window tracking ----------

    def _on_destroy(self, *_args) -> None:
        if Gtk.main_level() > 0:
            Gtk.main_quit()

    def _on_realize(self, *_args) -> None:
        self._refresh_own_xids()

    def _on_configure(self, _widget, event) -> bool:
        if self._docked_to_id and not self._user_moved and not self._is_maximized:
            try:
                geo = get_window_geometry(self._docked_to_id)
                ow, oh = self.get_size()
                expected_x = geo["x"] + geo["width"] - ow - OVERLAY_MARGIN
                expected_y = geo["y"] + OVERLAY_MARGIN
                if abs(event.x - expected_x) > 40 or abs(event.y - expected_y) > 40:
                    self._user_moved = True
            except WindowUtilsError:
                pass
        return False

    def _refresh_own_xids(self) -> None:
        gdk_win = self.get_window()
        if gdk_win is None:
            return
        try:
            xid = gdk_win.get_xid()
        except Exception:
            return
        if xid:
            self._own_xids.add(str(xid))

    def _is_self(self, info: ActiveWindowInfo) -> bool:
        return info.window_id in self._own_xids or is_overlay_window(info)

    def _poll_active_window(self) -> bool:
        self._refresh_own_xids()
        try:
            info = get_active_window()
        except WindowUtilsError:
            return True

        # Overlay focused — never move/dock here (move() steals Merge/Send clicks
        # in compact mode; maximized already skipped docking).
        if self._is_self(info):
            return True

        self._last_external = info
        changed = (
            self._follow_window is None
            or self._follow_window.window_id != info.window_id
        )
        self._follow_window = info
        # Dock only when the target window changes — not every poll tick
        if not self._is_maximized and changed and not self._user_moved:
            self._set_pinned(True)
            self._dock_to_window(info)
        return True

    def _dock_to_window(self, info: ActiveWindowInfo) -> None:
        if self._is_self(info) or self._is_maximized:
            return
        try:
            geo = get_window_geometry(info.window_id)
        except WindowUtilsError:
            return
        ow, oh = self.get_size()
        if ow <= 1:
            ow = COMPACT_SIZE[0]
        if oh <= 1:
            oh = COMPACT_SIZE[1]
        x = geo["x"] + geo["width"] - ow - OVERLAY_MARGIN
        y = geo["y"] + OVERLAY_MARGIN
        # Keep the compact overlay fully on the visible monitor
        try:
            screen = self.get_screen()
            monitor = screen.get_monitor_at_point(
                geo["x"] + geo["width"] // 2, geo["y"] + geo["height"] // 2
            )
            geo_m = screen.get_monitor_geometry(monitor)
            x = max(geo_m.x, min(x, geo_m.x + geo_m.width - ow))
            y = max(geo_m.y, min(y, geo_m.y + geo_m.height - oh))
        except Exception:
            x = max(0, x)
            y = max(0, y)
        # Avoid noop move jitter that can cancel in-progress clicks
        cx, cy = self.get_position()
        if abs(cx - x) < 2 and abs(cy - y) < 2:
            self._docked_to_id = info.window_id
            return
        self.move(x, y)
        self._docked_to_id = info.window_id

    def _target_window(self) -> Optional[ActiveWindowInfo]:
        for candidate in (self._last_external, self._follow_window):
            if candidate is not None and not self._is_self(candidate):
                return candidate
        return None

    # ---------- sessions / chat UI ----------

    def _on_new_chat(self, *_args) -> None:
        # Keep all prior sessions in memory; sidebar lists them when maximized
        n = len(self.session_manager.sessions) + 1
        self.session_manager.new_session(f"Chat {n}")
        self._refresh_chat_list()
        self._refresh_merged_placeholder()
        self._reload_messages()

    def _refresh_chat_list(self) -> None:
        # Block selection handler while rebuilding so we don't thrash sessions
        self.chat_list.handler_block_by_func(self._on_chat_selected)
        try:
            for child in list(self.chat_list.get_children()):
                self.chat_list.remove(child)

            active_id = self.session_manager.active_session_id
            for sid, title in self.session_manager.list_sessions():
                session = self.session_manager.sessions.get(sid)
                n_msg = len(session.messages) if session else 0
                label_txt = title or "Chat"
                if n_msg:
                    label_txt = f"{label_txt}  ({n_msg} msgs)"
                row = Gtk.ListBoxRow()
                row.session_id = sid  # type: ignore[attr-defined]
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                box.set_margin_top(8)
                box.set_margin_bottom(8)
                box.set_margin_start(10)
                box.set_margin_end(10)
                label = Gtk.Label(label=label_txt, xalign=0.0)
                label.set_ellipsize(Pango.EllipsizeMode.END)
                label.set_max_width_chars(28)
                box.pack_start(label, False, False, 0)
                row.add(box)
                self.chat_list.add(row)
                if sid == active_id:
                    self.chat_list.select_row(row)
            self.chat_list.show_all()
            # Only reveal the sidebar in maximized mode
            if self._is_maximized:
                self.sidebar.set_no_show_all(False)
                self.sidebar.show_all()
        finally:
            self.chat_list.handler_unblock_by_func(self._on_chat_selected)

    def _on_chat_selected(self, _listbox, row) -> None:
        if row is None:
            return
        sid = getattr(row, "session_id", None)
        if not sid:
            return
        if sid == self.session_manager.active_session_id:
            return
        self.session_manager.switch_to(sid)
        self._refresh_merged_placeholder()
        self._reload_messages()

    def _clear_messages_box(self) -> None:
        for child in self.messages_box.get_children():
            self.messages_box.remove(child)

    def _add_bubble(self, role: str, content: str) -> None:
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        if role == "user":
            role_text = "You"
        elif role == "system":
            role_text = "Merged"
        else:
            role_text = "Merge AI"
        role_lbl = Gtk.Label(label=role_text, xalign=0.0)
        role_lbl.get_style_context().add_class("msg-role")
        wrap.pack_start(role_lbl, False, False, 0)

        body = Gtk.Label(label=content, xalign=0.0)
        body.set_line_wrap(True)
        body.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        body.set_selectable(True)
        body.set_max_width_chars(72)
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        frame.pack_start(body, False, False, 0)
        if role == "user":
            frame.get_style_context().add_class("msg-user")
        else:
            frame.get_style_context().add_class("msg-assistant")
        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        if role == "user":
            outer.pack_end(frame, False, False, 0)
        else:
            outer.pack_start(frame, False, False, 0)
        wrap.pack_start(outer, False, False, 0)
        self.messages_box.pack_start(wrap, False, False, 0)
        wrap.show_all()

    def _clear_merged_chips(self) -> None:
        for child in list(self.merged_chips.get_children()):
            self.merged_chips.remove(child)

    def _refresh_merged_placeholder(self) -> None:
        """Show/hide the top Merged files bar for the active chat."""
        session = self.session_manager.get_active()
        self._clear_merged_chips()
        sources = (
            [s for s in session.sources.values() if not s.stale]
            if session and session.sources
            else []
        )
        if not sources:
            self.merged_files_section.set_no_show_all(True)
            self.merged_files_section.hide()
            return

        # Allow show_all / show to work (no_show_all blocks parent show_all)
        self.merged_files_section.set_no_show_all(False)

        for source in sources:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            row.get_style_context().add_class("merged-chip")
            row.set_margin_start(2)
            row.set_margin_end(2)
            row.set_margin_top(2)
            row.set_margin_bottom(2)

            name = Gtk.Label(label=source.name, xalign=0.0)
            name.get_style_context().add_class("merged-chip-name")
            name.set_ellipsize(Pango.EllipsizeMode.END)
            name.set_max_width_chars(40)
            name.set_selectable(True)
            row.pack_start(name, True, True, 0)

            close_btn = Gtk.Button(label="✕")
            close_btn.set_relief(Gtk.ReliefStyle.NONE)
            close_btn.set_tooltip_text(f"Remove {source.name}")
            close_btn.connect(
                "clicked",
                self._on_remove_merged,
                source.identifier,
            )
            row.pack_end(close_btn, False, False, 0)
            self.merged_chips.pack_start(row, False, False, 0)

        self.merged_chips.show_all()
        self.merged_files_section.show_all()
        self.merged_files_section.show()
        self.queue_resize()
        # Re-assert after idle in case a later show_all hid us via no_show_all
        GLib.idle_add(self._ensure_merged_visible)

    def _ensure_merged_visible(self) -> bool:
        session = self.session_manager.get_active()
        if session and session.sources:
            self.merged_files_section.set_no_show_all(False)
            self.merged_files_section.show_all()
            self.merged_files_section.show()
        return False

    def _on_remove_merged(self, _btn, identifier: str) -> None:
        session = self.session_manager.get_active()
        if session is None:
            return
        session.drop(identifier)
        self._refresh_merged_placeholder()
        self._refresh_chat_list()
        self._alert(f"Removed: {os.path.basename(identifier)}")

    def _hide_toast(self) -> None:
        if self._toast_timeout_id is not None:
            try:
                GLib.source_remove(self._toast_timeout_id)
            except Exception:
                pass
            self._toast_timeout_id = None
        self.toast_bar.hide()

    def _alert(self, message: str) -> None:
        """Small bottom-left toast; auto-closes after 5 seconds."""
        self._hide_toast()
        self.toast_label.set_text(message)
        # no_show_all widgets need an explicit show(); show_all paints children
        self.toast_bar.show_all()
        self.toast_bar.show()
        self._toast_timeout_id = GLib.timeout_add_seconds(5, self._on_toast_timeout)
        # Keep toast above chat redraws for this idle cycle
        GLib.idle_add(self.toast_bar.show)

    def _on_toast_timeout(self) -> bool:
        self._toast_timeout_id = None
        self.toast_bar.hide()
        return False

    def _reload_messages(self) -> None:
        self._clear_messages_box()
        session = self.session_manager.get_active()
        self._refresh_merged_placeholder()
        if session is None:
            self._titlebar.set_subtitle("")
            return
        names = [s.name for s in session.sources.values() if not s.stale]
        self._titlebar.set_subtitle(
            ("Merged: " + ", ".join(names)) if names else ""
        )
        for msg in session.messages:
            # Merged status lives in the top placeholder + alerts only
            if msg.get("role") == "system":
                continue
            self._add_bubble(msg["role"], msg["content"])
        adj = self.scrolled.get_vadjustment()
        GLib.idle_add(lambda: adj.set_value(adj.get_upper() - adj.get_page_size()) or False)

    def _append_log(self, message: str) -> None:
        """Compatibility: treat as assistant system line in the active chat."""
        session = self.session_manager.get_active()
        if session is None:
            return
        session.add_message("assistant", message)
        self._reload_messages()

    # ---------- Merge ----------

    def on_merge_clicked(self) -> Optional[Session]:
        """Merge focused window into the current chat. Never opens a file picker —
        Excel/Word/PDF use the file path when known; everything else is screenshotted.
        """
        # Refresh target right before merge (click focuses overlay)
        self._refresh_own_xids()
        try:
            current = get_active_window()
            if not self._is_self(current):
                self._last_external = current
                self._follow_window = current
        except WindowUtilsError:
            pass

        info = self._target_window()
        if info is None:
            self._alert("Focus a window first, then click Merge.")
            return self.session_manager.get_active()

        kind = classify_window(info)
        try:
            # Only use native file merge when we can resolve a real path.
            if kind == "file":
                file_path = guess_file_path_for_window(info)
                if file_path:
                    return self._merge_into_current_chat(file_path=file_path, info=info)
            # Terminal / browser use native adapters when possible
            if kind in ("terminal", "browser"):
                return self._merge_into_current_chat(info=info, kind=kind)
            # Cursor, folders, unknown → screenshot
            return self._merge_into_current_chat(info=info, kind=kind or "vision")
        except Exception as e:
            self._alert(f"Could not merge: {e}")
            return self.session_manager.get_active()

    def _merge_into_current_chat(
        self,
        *,
        file_path: Optional[str] = None,
        info: Optional[ActiveWindowInfo] = None,
        kind: str = "vision",
    ) -> Session:
        # Always use the active chat — Merge stacks sources; New chat starts fresh.
        session = self.session_manager.get_active() or self.session_manager.new_session(
            "New chat"
        )

        already = False
        if file_path:
            abs_path = os.path.abspath(file_path)
            if abs_path in session.sources:
                already = True
                source = session.sources[abs_path]
            else:
                source = session.merge_file(file_path)
        elif kind == "terminal" and info is not None:
            name = friendly_window_name(info)
            if name in session.sources and session.sources[name].adapter and session.sources[name].adapter.kind == "terminal":
                already = True
                source = session.sources[name]
            else:
                source = session.merge_kind("terminal", name=name)
        elif kind == "browser" and info is not None:
            name = friendly_window_name(info)
            # Prefer Chrome bridge; fall back to vision if extension not connected
            try:
                from adapters.browser.bridge_server import ensure_bridge
                from adapters.browser.browser_adapter import BrowserAdapter

                ensure_bridge()
                probe = BrowserAdapter(source=name)
                if probe.is_connected():
                    if name in session.sources and session.sources[name].adapter and session.sources[name].adapter.kind == "browser":
                        already = True
                        source = session.sources[name]
                    else:
                        source = session.merge_kind("browser", name=name)
                else:
                    return self._merge_into_current_chat(info=info, kind="vision")
            except Exception:
                return self._merge_into_current_chat(info=info, kind="vision")
        else:
            assert info is not None
            # "vision" = screenshot method only; label is Cursor / Chrome / Edge…
            name = friendly_window_name(info)
            if name in session.sources:
                already = True
                source = session.sources[name]
                if source.adapter and source.adapter.kind == "vision":
                    safe = re.sub(r"[^\w.-]+", "_", name)[:40] or "window"
                    out = f"/tmp/merge_ai_capture_{info.window_id}_{safe}.png"
                    try:
                        source.adapter.capture(
                            output_path=out, window_id=info.window_id
                        )
                    except Exception:
                        pass
            else:
                source = session.merge_kind("vision", name=name)
                safe = re.sub(r"[^\w.-]+", "_", name)[:40] or "window"
                out = f"/tmp/merge_ai_capture_{info.window_id}_{safe}.png"
                try:
                    source.adapter.capture(output_path=out, window_id=info.window_id)
                except Exception as e:
                    # Still show the chip — capture can be retried on next Merge
                    self._refresh_chat_list()
                    self._refresh_merged_placeholder()
                    self._alert(f"Screenshot failed: {e}")
                    return session

        if already:
            self._alert(f"Already merged: {source.name}")
        else:
            if len(session.sources) == 1:
                session.set_title(source.name)
            self._alert(f"Merged: {source.name}")

        self._set_pinned(True)
        # Do not re-dock on Merge — moving the window cancels the click in compact mode
        if info is not None:
            self._follow_window = info
            self._last_external = info
        self._refresh_chat_list()
        self._refresh_merged_placeholder()
        self._reload_messages()
        if self.toast_label.get_text():
            self.toast_bar.show_all()
            self.toast_bar.show()
            self.queue_resize()
        return session

    def _on_merge_clicked(self, *_args) -> None:
        try:
            self.on_merge_clicked()
        except Exception as e:
            try:
                self._alert(f"Merge error: {e}")
            except Exception:
                print(f"Merge error: {e}", file=sys.stderr)

    # ---------- pin / window chrome ----------

    def _set_pinned(self, pinned: bool) -> None:
        self.pinned = pinned
        self.set_keep_above(pinned)
        if self.pin_button.get_active() != pinned:
            self.pin_button.handler_block_by_func(self._on_pin_toggled)
            self.pin_button.set_active(pinned)
            self.pin_button.handler_unblock_by_func(self._on_pin_toggled)

    def _on_pin_toggled(self, button: Gtk.ToggleButton) -> None:
        self._set_pinned(button.get_active())

    def _on_minimize_clicked(self, *_args) -> None:
        """Real OS minimize → taskbar."""
        self.iconify()

    def _enter_maximized_layout(self) -> None:
        self.sidebar.set_no_show_all(False)
        self.sidebar.set_size_request(SIDEBAR_W, -1)
        self.sidebar.show_all()
        self._refresh_chat_list()
        self._reload_messages()

    def _leave_maximized_layout(self) -> None:
        self.sidebar.hide()
        self.sidebar.set_no_show_all(True)

    def _on_maximize_clicked(self, *_args) -> None:
        if self._is_maximized or self.is_maximized():
            self._leave_maximized_layout()
            self.unmaximize()
            self.resize(*self._normal_size)
            self._is_maximized = False
            self.max_button.set_label("□")
            self.max_button.set_tooltip_text("Maximize")
        else:
            self._normal_size = self.get_size()
            self.maximize()
            self._is_maximized = True
            self.max_button.set_label("❐")
            self.max_button.set_tooltip_text("Restore")
            self._enter_maximized_layout()

    def confirm_change(self, preview_description: str) -> bool:
        dialog = Gtk.MessageDialog(
            parent=self,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text="Apply this change?",
        )
        dialog.format_secondary_text(preview_description)
        response = dialog.run()
        dialog.destroy()
        return response == Gtk.ResponseType.YES

    def _on_send_clicked(self, *_args) -> None:
        text = self.chat_entry.get_text().strip()
        if not text:
            return
        if self._agent_busy:
            self._alert("Still working on the previous message…")
            return
        self.chat_entry.set_text("")

        session = self.session_manager.get_active()
        if session is None:
            session = self.session_manager.new_session("New chat")

        session.add_message("user", text)
        self._reload_messages()

        if not session.sources:
            session.add_message("assistant", "Merge a window first, then ask.")
            self._reload_messages()
            return

        # Async agent — keep GTK responsive
        self._agent_busy = True
        self._agent_request_id += 1
        req_id = self._agent_request_id
        self._agent_session_id = session.id
        self.send_button.set_sensitive(False)
        self.merge_button.set_sensitive(False)
        session.add_message("assistant", "Thinking…")
        self._reload_messages()

        def worker() -> None:
            err: Optional[BaseException] = None
            result = None
            try:
                from session.agent import run_agent
                from session.edit_engine import ensure_live_bridge
                from adapters.browser.bridge_server import ensure_bridge

                ensure_live_bridge()
                try:
                    ensure_bridge()
                except Exception:
                    pass
                result = run_agent(session, text)
            except BaseException as e:  # noqa: BLE001 — surface to UI
                err = e
            GLib.idle_add(
                self._on_agent_finished, session, text, result, err, req_id
            )

        threading.Thread(target=worker, daemon=True, name="merge-ai-agent").start()

    def _on_agent_finished(self, session, text, result, err, req_id=None) -> bool:
        """Main-thread callback after async agent run."""
        # Ignore stale callbacks (session switched or newer request started)
        if req_id is not None and req_id != self._agent_request_id:
            return False
        if (
            self._agent_session_id is not None
            and getattr(session, "id", None) != self._agent_session_id
        ):
            return False

        self._agent_busy = False
        self.send_button.set_sensitive(True)
        self.merge_button.set_sensitive(True)

        # Drop the temporary Thinking… placeholder
        if (
            session.messages
            and session.messages[-1].get("role") == "assistant"
            and session.messages[-1].get("content") == "Thinking…"
        ):
            session.messages.pop()

        if err is not None:
            session.add_message(
                "assistant", f"Error: {type(err).__name__}: {err}"
            )
            self._refresh_chat_list()
            self._reload_messages()
            return False

        try:
            answer = self._apply_agent_result(session, text, result)
        except Exception as e:
            answer = f"Error: {type(e).__name__}: {e}"

        if not answer or not str(answer).strip():
            answer = "(no answer)"
        preview = answer if len(answer) <= 4000 else answer[:4000] + "…"
        # Avoid spamming the same assistant line (clarify-loop / duplicate idle)
        if (
            session.messages
            and session.messages[-1].get("role") == "assistant"
            and (session.messages[-1].get("content") or "").strip() == preview.strip()
        ):
            self._refresh_chat_list()
            self._reload_messages()
            return False
        session.add_message("assistant", preview)
        self._refresh_chat_list()
        self._reload_messages()
        return False

    def _apply_agent_result(self, session: Session, text: str, result) -> str:
        """Handle confirm + apply on the GTK main thread."""
        from session.edit_engine import apply_previews, reload_writable_adapters

        if result is None:
            return "(no answer)"

        if result.needs_confirm and result.previews:
            summary = "\n".join(f"• {p}" for p in result.previews)
            if result.errors:
                summary += "\n\nNotes:\n" + "\n".join(
                    f"• {e}" for e in result.errors[:8]
                )
            goal = result.user_goal or text
            if not self.confirm_change(
                f"Goal: {goal}\n\nApply these changes?\n\n{summary}"
            ):
                return "Edit cancelled — nothing was changed."
            applied_results = []
            for preview in result.previews:
                applied_results.append(preview.apply())
            notes = [str(p) for p in result.previews]
            reload_writable_adapters(session)
            live_n = sum(1 for n in notes if "(live)" in n)
            self._alert(
                f"Applied {len(notes)} live change(s)"
                if live_n
                else f"Applied {len(notes)} change(s)"
            )
            header = (
                f"Applied {len(notes)} live change(s) in the open app"
                if live_n
                else f"Applied {len(notes)} change(s)"
            )
            body = header + ":\n" + "\n".join(f"• {n}" for n in notes)
            # Surface terminal stdout/stderr after confirm-first run
            for res in applied_results:
                if isinstance(res, dict) and ("stdout" in res or "stderr" in res):
                    out = (res.get("stdout") or "").strip()
                    err = (res.get("stderr") or "").strip()
                    code = res.get("returncode")
                    body += f"\n\n$ {res.get('command', '')}\n"
                    if out:
                        body += out + "\n"
                    if err:
                        body += err + "\n"
                    if code is not None:
                        body += f"(exit {code})\n"
            return body

        return (result.answer or "").strip() or "(no answer)"

    def _nudge_open_document_reload(self) -> None:
        """Deprecated: live UNO edits no longer need a reload nudge."""
        return
    def _answer_question(self, session: Session, question: str) -> str:
        """Answer using all sources merged into this chat (files + screenshots)."""
        from adapters.vision.vision_adapter import VisionAdapter

        chunks: list[str] = []
        vision = None
        for source in session.sources.values():
            adapter = source.adapter
            if not adapter:
                continue
            if adapter.kind in ("excel", "pdf", "word", "text", "ppt"):
                try:
                    if adapter.kind == "excel":
                        body = adapter.read_all(max_rows_per_sheet=80)
                    else:
                        body = adapter.read_all()
                except Exception as e:
                    body = f"(could not read: {e})"
                if len(body) > 12000:
                    body = body[:12000] + "\n…(truncated)"
                chunks.append(f"### {source.name} ({adapter.kind})\n{body}")
            elif (
                adapter.kind == "vision"
                and getattr(adapter, "_last_image_path", None)
            ):
                vision = adapter  # keep last vision for image Q&A
                chunks.append(f"### {source.name} (screenshot)\n(see attached image)")

        if not chunks and vision is None:
            raise RuntimeError("No readable merged sources.")

        # Prefer image+text when a screenshot is in this chat; else text-only.
        if vision is not None:
            ctx = "\n\n".join(chunks)
            prompt = question
            if ctx.strip():
                prompt = (
                    f"Merged sources in this chat:\n{ctx}\n\n"
                    f"Question: {question}"
                )
            return vision.describe(vision._last_image_path, prompt)

        helper = VisionAdapter("chat")
        return helper.ask_text(question, context="\n\n".join(chunks))


def run() -> int:
    if Gtk is None:
        print(
            "PyGObject/GTK3 is required for the overlay.\n"
            "Install: sudo apt-get install -y python3-gi gir1.2-gtk-3.0\n"
            f"Import error: {_GI_IMPORT_ERROR}",
            file=sys.stderr,
        )
        return 1

    app = MergeOverlayApp()
    app.show_all()
    app.sidebar.hide()  # compact overlay: chats sidebar only when maximized
    app.merged_files_section.hide()
    app.toast_bar.hide()
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(run())
