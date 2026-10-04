#!/usr/bin/env python3
"""
CLI + overlay entry for Merge AI.

Examples:
    python main.py overlay                          # floating GTK Merge window
    python main.py merge report.pdf ledger.xlsx notes.docx
    python main.py read ledger.xlsx --sheet Sheet1 --cell A1
    python main.py write ledger.xlsx --sheet Sheet1 --cell A1 --value "New Value"
    python main.py search report.pdf ledger.xlsx --value "Revenue"
    python main.py run "ls -la"                     # terminal adapter, confirm-first
"""

import argparse
import os
import sys

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

from session.session_manager import SessionManager


def cmd_merge(args):
    manager = SessionManager()
    session = manager.new_session()
    for path in args.files:
        try:
            source = session.merge_file(path)
            print(f"Merged: {source.name}  ({source.adapter.kind})")
        except (FileNotFoundError, ValueError) as e:
            print(f"Could not merge {path}: {e}")
    print()
    print(session.banner_text())


def cmd_read(args):
    from adapters.registry import get_adapter_for_path

    adapter = get_adapter_for_path(args.file)
    if adapter.kind == "excel":
        print(adapter.read_cell(args.cell, sheet_name=args.sheet))
    elif adapter.kind == "pdf":
        page = int(args.cell) if args.cell else 1
        print(adapter.read_page(page))
    elif adapter.kind == "word":
        idx = int(args.cell) if args.cell else 0
        print(adapter.read_paragraph(idx))
    else:
        print(adapter.read_all())


def cmd_write(args):
    from adapters.registry import get_adapter_for_path

    adapter = get_adapter_for_path(args.file)
    if not adapter.can_write:
        print(f"{adapter.kind} adapter is read-only — cannot write.")
        return

    if adapter.kind == "excel":
        preview = adapter.write_cell(args.cell, args.value, sheet_name=args.sheet)
    elif adapter.kind == "word":
        preview = adapter.write_paragraph(int(args.cell), args.value)
    else:
        print(f"No write path wired up in CLI for {adapter.kind} yet.")
        return

    print("Preview of change:")
    print(f"  {preview}")
    confirm = input("Apply this change? [y/N] ").strip().lower()
    if confirm == "y":
        preview.apply()
        print("Applied.")
    else:
        print("Cancelled — no changes made.")


def cmd_search(args):
    manager = SessionManager()
    session = manager.new_session()
    for path in args.files:
        try:
            session.merge_file(path)
        except (FileNotFoundError, ValueError) as e:
            print(f"Could not merge {path}: {e}")

    value = args.value
    for cast in (int, float):
        try:
            value = cast(args.value)
            break
        except ValueError:
            continue

    results = session.search_all(value)
    if not results:
        print(f"No matches for {args.value!r} across merged sources.")
        return
    for name, hits in results.items():
        print(f"{name}: {hits}")


def cmd_run(args):
    """Terminal adapter demo — always confirm-first, no exceptions."""
    from adapters.registry import get_adapter_for_kind

    adapter = get_adapter_for_kind("terminal")
    preview = adapter.run_command(args.command)
    print("Preview of command to run:")
    print(f"  {preview}")
    confirm = input("Execute this command? [y/N] ").strip().lower()
    if confirm == "y":
        result = preview.apply()
        print("--- stdout ---")
        print(result["stdout"])
        if result["stderr"]:
            print("--- stderr ---")
            print(result["stderr"])
        print(f"(exit code {result['returncode']})")
    else:
        print("Cancelled — command not run.")


def cmd_overlay(_args):
    """Launch the Phase 2 GTK overlay shell."""
    from overlay.overlay_app import run

    return run()


def build_parser():
    parser = argparse.ArgumentParser(description="Merge AI — full-adapter CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_overlay = sub.add_parser("overlay", help="Launch the floating GTK Merge overlay")
    p_overlay.set_defaults(func=cmd_overlay)

    p_merge = sub.add_parser("merge", help="Merge one or more files into a session")
    p_merge.add_argument("files", nargs="+")
    p_merge.set_defaults(func=cmd_merge)

    p_read = sub.add_parser("read", help="Read a cell/page/paragraph")
    p_read.add_argument("file")
    p_read.add_argument("--sheet", default=None)
    p_read.add_argument("--cell", default=None, help="cell ref (excel), page # (pdf), paragraph # (word)")
    p_read.set_defaults(func=cmd_read)

    p_write = sub.add_parser("write", help="Write a cell/paragraph (confirm-first)")
    p_write.add_argument("file")
    p_write.add_argument("--sheet", default=None)
    p_write.add_argument("--cell", required=True)
    p_write.add_argument("--value", required=True)
    p_write.set_defaults(func=cmd_write)

    p_search = sub.add_parser("search", help="Search a value across merged sources")
    p_search.add_argument("files", nargs="+")
    p_search.add_argument("--value", required=True)
    p_search.set_defaults(func=cmd_search)

    p_run = sub.add_parser("run", help="Run a shell command via the terminal adapter (confirm-first)")
    p_run.add_argument("command")
    p_run.set_defaults(func=cmd_run)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    result = args.func(args)
    return 0 if result is None else result


if __name__ == "__main__":
    sys.exit(main())
