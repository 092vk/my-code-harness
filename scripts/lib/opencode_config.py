#!/usr/bin/env python3
"""Comment-preserving JSONC editor for opencode-graph-harness.

OpenCode accepts JSON and JSONC. Rewriting a JSONC file with a normal JSON
serializer destroys the user's comments, so this module performs surgical
insertions/removals while preserving all unrelated bytes.

Public CLI:
    opencode_config.py merge  --file PATH --backend cbm|codegraph|both [--force] [--dry-run]
    opencode_config.py remove --file PATH --servers NAME[,NAME...] [--dry-run]

The module has no third-party dependencies and targets Python 3.8+.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCHEMA_URL = "https://opencode.ai/config.json"

# Keep these definitions in sync with templates/opencode.json(c).
SERVER_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "cbm": {
        "codebase-memory": {
            "type": "local",
            "command": ["npx", "-y", "codebase-memory-mcp"],
            "codemode": False,
            "timeout": {
                "startup": 120000,
                "catalog": 60000,
            },
        }
    },
    "codegraph": {
        "codegraph": {
            "type": "local",
            "command": [
                "npx",
                "-y",
                "@colbymchenry/codegraph",
                "serve",
                "--mcp",
            ],
            "codemode": False,
            "environment": {"CODEGRAPH_TELEMETRY": "off"},
            "timeout": {
                "startup": 120000,
                "catalog": 60000,
            },
        }
    },
}

BACKEND_ALIASES = {
    "cbm": "cbm",
    "codebase-memory": "cbm",
    "codebase-memory-mcp": "cbm",
    "codegraph": "codegraph",
    "both": "both",
}


class JsoncError(ValueError):
    """Raised when a file is not valid JSON/JSONC or has an unsupported shape."""


@dataclass
class Member:
    key: str
    key_start: int
    key_end: int
    value_start: int
    value_end: int
    value: Any
    comma_pos: Optional[int] = None
    node: Any = None


@dataclass
class ParsedObject:
    start: int
    end: int
    members: List[Member] = field(default_factory=list)
    by_key: Dict[str, Member] = field(default_factory=dict)


class Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.n = len(text)

    def error(self, pos: int, message: str) -> JsoncError:
        line = self.text.count("\n", 0, pos) + 1
        col = pos - self.text.rfind("\n", 0, pos)
        return JsoncError(f"{message} at line {line}, column {col}")

    def skip_trivia(self, i: int) -> int:
        text = self.text
        while i < self.n:
            ch = text[i]
            if ch in " \t\r\n\ufeff":
                i += 1
                continue
            if text.startswith("//", i):
                newline = text.find("\n", i + 2)
                i = self.n if newline == -1 else newline + 1
                continue
            if text.startswith("/*", i):
                end = text.find("*/", i + 2)
                if end == -1:
                    raise self.error(i, "unterminated block comment")
                i = end + 2
                continue
            break
        return i

    def parse_string(self, i: int) -> Tuple[str, int]:
        text = self.text
        if i >= self.n or text[i] != '"':
            raise self.error(i, "expected a string")
        j = i + 1
        chunks: List[str] = []
        while j < self.n:
            ch = text[j]
            if ch == '"':
                chunks.append(text[i + 1 : j])
                raw = text[i : j + 1]
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError as exc:  # pragma: no cover - defensive
                    raise self.error(i, f"invalid string: {exc.msg}") from exc
                return value, j + 1
            if ch == "\\":
                esc = text[j + 1 : j + 2]
                if esc in ('"', "\\", "/", "b", "f", "n", "r", "t"):
                    j += 2
                    continue
                if esc == "u":
                    if j + 6 > self.n or not re.fullmatch(
                        r"[0-9a-fA-F]{4}", text[j + 2 : j + 6]
                    ):
                        raise self.error(j, "invalid unicode escape")
                    j += 6
                    continue
                raise self.error(j, "invalid escape sequence")
            if ord(ch) < 0x20:
                raise self.error(j, "control character in string")
            j += 1
        raise self.error(i, "unterminated string")

    def parse_value(self, i: int) -> Tuple[Any, int, Any]:
        text = self.text
        i = self.skip_trivia(i)
        if i >= self.n:
            raise self.error(i, "unexpected end of file")
        ch = text[i]
        if ch == "{":
            return self.parse_object(i)
        if ch == "[":
            return self.parse_array(i)
        if ch == '"':
            value, end = self.parse_string(i)
            return value, end, None
        for literal, value in (("true", True), ("false", False), ("null", None)):
            if text.startswith(literal, i):
                return value, i + len(literal), None
        number = re.match(
            r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?", text[i:]
        )
        if number:
            raw = number.group(0)
            return json.loads(raw), i + len(raw), None
        raise self.error(i, "expected a JSON value")

    def parse_array(self, i: int) -> Tuple[List[Any], int, Any]:
        text = self.text
        start = i
        i += 1
        values: List[Any] = []
        i = self.skip_trivia(i)
        if i < self.n and text[i] == "]":
            return values, i + 1, None
        while True:
            value, i, _ = self.parse_value(i)
            values.append(value)
            i = self.skip_trivia(i)
            if i < self.n and text[i] == ",":
                i += 1
                i = self.skip_trivia(i)
                if i < self.n and text[i] == "]":
                    return values, i + 1, None
                continue
            if i < self.n and text[i] == "]":
                return values, i + 1, None
            raise self.error(i, "expected ',' or ']'")
        return values, i, None  # pragma: no cover

    def parse_object(self, i: int) -> Tuple[Dict[str, Any], int, ParsedObject]:
        text = self.text
        start = i
        i += 1
        obj = ParsedObject(start=start, end=start)
        value: Dict[str, Any] = {}
        i = self.skip_trivia(i)
        if i < self.n and text[i] == "}":
            obj.end = i + 1
            return value, obj.end, obj
        while True:
            i = self.skip_trivia(i)
            key_start = i
            key, i = self.parse_string(i)
            key_end = i
            i = self.skip_trivia(i)
            if i >= self.n or text[i] != ":":
                raise self.error(i, "expected ':'")
            i += 1
            i = self.skip_trivia(i)
            value_start = i
            member_value, i, node = self.parse_value(i)
            value_end = i
            i = self.skip_trivia(i)
            comma_pos: Optional[int] = None
            if i < self.n and text[i] == ",":
                comma_pos = i
                i += 1
                i = self.skip_trivia(i)
            member = Member(
                key=key,
                key_start=key_start,
                key_end=key_end,
                value_start=value_start,
                value_end=value_end,
                value=member_value,
                comma_pos=comma_pos,
                node=node,
            )
            if key in obj.by_key:
                raise self.error(key_start, f"duplicate key {key!r}")
            obj.members.append(member)
            obj.by_key[key] = member
            value[key] = member_value
            if i < self.n and text[i] == "}":
                obj.end = i + 1
                return value, obj.end, obj
            if comma_pos is None:
                raise self.error(i, "expected ',' or '}'")
        return value, i, obj  # pragma: no cover


class Document:
    def __init__(self, text: str) -> None:
        self.text = text
        parser = Parser(text)
        i = parser.skip_trivia(0)
        value, i, node = parser.parse_value(i)
        i = parser.skip_trivia(i)
        if i != len(text):
            raise parser.error(i, "unexpected trailing content")
        self.value = value
        self.root = node

    def require_root_object(self) -> ParsedObject:
        if not isinstance(self.root, ParsedObject):
            raise JsoncError("the configuration root must be a JSON object")
        return self.root


def line_indent(text: str, pos: int) -> str:
    start = text.rfind("\n", 0, pos) + 1
    prefix = text[start:pos]
    return prefix if prefix.strip() == "" else ""


def render_value(value: Any, indent: str) -> str:
    raw = json.dumps(value, indent=2, ensure_ascii=False)
    lines = raw.split("\n")
    return lines[0] + "".join("\n" + indent + line for line in lines[1:])


def render_member(key: str, value: Any, indent: str) -> str:
    return f"{json.dumps(key)}: {render_value(value, indent)}"


def _apply_edits(text: str, edits: Iterable[Tuple[int, int, str]]) -> str:
    for start, end, replacement in sorted(edits, key=lambda item: item[0], reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


def _insert_member_text(
    text: str,
    obj: ParsedObject,
    key: str,
    value: Any,
    member_indent: str,
    close_indent: str,
) -> str:
    member = render_member(key, value, member_indent)
    if not obj.members:
        inner = text[obj.start + 1 : obj.end - 1]
        if inner.strip():
            raise JsoncError(
                "cannot insert into an empty object that contains comments; "
                "add the entry manually"
            )
        replacement = f"\n{member_indent}{member}\n{close_indent}"
        return text[: obj.start + 1] + replacement + text[obj.end - 1 :]

    last = obj.members[-1]
    close_line_start = text.rfind("\n", 0, obj.end - 1) + 1
    close_on_own_line = (
        close_line_start > last.value_end
        and text[close_line_start : obj.end - 1].strip() == ""
    )

    if close_on_own_line:
        edits: List[Tuple[int, int, str]] = [
            (
                close_line_start,
                obj.end - 1,
                member_indent + member + "\n" + close_indent,
            )
        ]
        if last.comma_pos is None:
            edits.append((last.value_end, last.value_end, ","))
        return _apply_edits(text, edits)

    # One-line / unusual object: rewrite only the trivia before the closing brace.
    base = (last.comma_pos + 1) if last.comma_pos is not None else last.value_end
    trivia = text[base : obj.end - 1]
    if trivia.strip():
        raise JsoncError(
            "cannot safely insert into a one-line object containing comments; "
            "add the entry manually"
        )
    prefix = "" if last.comma_pos is not None else ","
    replacement = f"{prefix}\n{member_indent}{member}\n{close_indent}"
    return text[:base] + replacement + text[obj.end - 1 :]


def _replace_member_value(doc: Document, member: Member, value: Any) -> str:
    indent = line_indent(doc.text, member.key_start)
    replacement = render_value(value, indent)
    return doc.text[: member.value_start] + replacement + doc.text[member.value_end :]


def upsert_servers(
    text: str,
    servers: Dict[str, Any],
    force: bool = False,
) -> Tuple[str, Dict[str, Any]]:
    """Insert/update MCP server entries, preserving unrelated JSONC text.

    Returns (new_text, report). Raises JsoncError on an unsupported shape or an
    unforced conflict with an existing server of the same name.
    """

    if not text.strip():
        text = "{}\n"

    doc = Document(text)
    root = doc.require_root_object()
    report: Dict[str, Any] = {
        "added": [],
        "updated": [],
        "unchanged": [],
        "conflicts": [],
    }

    mcp_member = root.by_key.get("mcp")
    if mcp_member is None:
        indent = (
            line_indent(doc.text, root.members[0].key_start) if root.members else "  "
        ) or "  "
        close_indent = line_indent(doc.text, root.end - 1) if root.members else ""
        new_text = _insert_member_text(
            doc.text,
            root,
            "mcp",
            {"servers": servers},
            indent,
            close_indent,
        )
        report["added"].extend(servers.keys())
        return new_text, report

    if not isinstance(mcp_member.node, ParsedObject):
        raise JsoncError("the existing 'mcp' key is not an object")

    mcp = mcp_member.node
    servers_member = mcp.by_key.get("servers")
    if servers_member is None and mcp.members:
        looks_like_v1 = any(
            isinstance(member.value, dict)
            and ("type" in member.value or "command" in member.value)
            for member in mcp.members
        )
        if looks_like_v1:
            raise JsoncError(
                "the existing config looks like OpenCode V1 (MCP servers directly "
                "under 'mcp'). This harness writes V2 ('mcp.servers'). Migrate the "
                "config to V2 or add the server entry manually."
            )
    if servers_member is None:
        mcp_indent = line_indent(doc.text, mcp_member.key_start)
        if mcp.members:
            indent = line_indent(doc.text, mcp.members[0].key_start) or (mcp_indent + "  ")
            close_indent = line_indent(doc.text, mcp.end - 1) or mcp_indent
        else:
            indent = mcp_indent + "  "
            close_indent = mcp_indent
        new_text = _insert_member_text(
            doc.text, mcp, "servers", servers, indent, close_indent
        )
        report["added"].extend(servers.keys())
        return new_text, report

    if not isinstance(servers_member.node, ParsedObject):
        raise JsoncError("the existing 'mcp.servers' key is not an object")

    servers_obj = servers_member.node
    servers_indent = line_indent(doc.text, servers_member.key_start)
    if servers_obj.members:
        member_indent = (
            line_indent(doc.text, servers_obj.members[0].key_start)
            or (servers_indent + "  ")
        )
        close_indent = line_indent(doc.text, servers_obj.end - 1) or servers_indent
    else:
        member_indent = servers_indent + "  "
        close_indent = servers_indent

    current_text = doc.text
    for name, definition in servers.items():
        existing = servers_obj.by_key.get(name)
        if existing is None:
            current_text = _insert_member_text(
                current_text,
                servers_obj,
                name,
                definition,
                member_indent,
                close_indent,
            )
            report["added"].append(name)
            # Reparse so subsequent insertions see the updated spans.
            doc = Document(current_text)
            root = doc.require_root_object()
            servers_obj = root.by_key["mcp"].node.by_key["servers"].node
            continue

        if existing.value == definition:
            report["unchanged"].append(name)
            continue

        if not force:
            report["conflicts"].append(name)
            continue

        doc = Document(current_text)
        root = doc.require_root_object()
        servers_obj = root.by_key["mcp"].node.by_key["servers"].node
        existing = servers_obj.by_key[name]
        current_text = _replace_member_value(doc, existing, definition)
        report["updated"].append(name)
        doc = Document(current_text)
        root = doc.require_root_object()
        servers_obj = root.by_key["mcp"].node.by_key["servers"].node

    if report["conflicts"]:
        names = ", ".join(report["conflicts"])
        raise JsoncError(
            f"existing MCP server(s) differ from the harness configuration: {names}. "
            "Re-run with --force to replace them."
        )

    return current_text, report


def remove_servers(text: str, names: Iterable[str]) -> Tuple[str, List[str]]:
    """Remove named servers. Returns (new_text, removed_names)."""

    if not text.strip():
        return text, []

    removed: List[str] = []
    current_text = text
    for name in names:
        doc = Document(current_text)
        root = doc.require_root_object()
        mcp_member = root.by_key.get("mcp")
        if mcp_member is None or not isinstance(mcp_member.node, ParsedObject):
            break
        servers_member = mcp_member.node.by_key.get("servers")
        if servers_member is None or not isinstance(servers_member.node, ParsedObject):
            break
        servers_obj = servers_member.node
        member = servers_obj.by_key.get(name)
        if member is None:
            continue

        if len(servers_obj.members) == 1:
            current_text = (
                current_text[: servers_obj.start + 1]
                + current_text[servers_obj.end - 1 :]
            )
        elif member.comma_pos is not None:
            current_text = (
                current_text[: member.key_start]
                + current_text[member.comma_pos + 1 :]
            )
        else:
            index = next(
                i for i, item in enumerate(servers_obj.members) if item.key == name
            )
            if index == 0:
                # A non-last member without a comma is invalid JSONC; the parser
                # would already have rejected it. This branch is defensive.
                current_text = (
                    current_text[: member.key_start]
                    + current_text[member.value_end :]
                )
            else:
                previous = servers_obj.members[index - 1]
                if previous.comma_pos is None:
                    raise JsoncError("malformed server list: missing comma")
                current_text = (
                    current_text[: previous.comma_pos]
                    + current_text[member.value_end :]
                )
        removed.append(name)

    return current_text, removed


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = None
    if path.exists():
        mode = path.stat().st_mode
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        if mode is not None:
            os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def build_servers(backend: str) -> Dict[str, Any]:
    normalized = BACKEND_ALIASES.get(backend)
    if normalized is None:
        raise ValueError(f"unknown backend: {backend}")
    if normalized == "both":
        result: Dict[str, Any] = {}
        result.update(SERVER_DEFINITIONS["cbm"])
        result.update(SERVER_DEFINITIONS["codegraph"])
        return result
    return dict(SERVER_DEFINITIONS[normalized])


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def cmd_merge(args: argparse.Namespace) -> int:
    path = Path(args.file)
    servers = build_servers(args.backend)
    original = _read(path)
    report: Dict[str, Any] = {}
    try:
        if not original.strip():
            content = {
                "$schema": SCHEMA_URL,
                "mcp": {"servers": servers},
            }
            new_text = json.dumps(content, indent=2, ensure_ascii=False) + "\n"
            report = {
                "added": list(servers.keys()),
                "updated": [],
                "unchanged": [],
                "conflicts": [],
            }
        else:
            new_text, report = upsert_servers(original, servers, force=args.force)
    except JsoncError as exc:
        print(f"opencode-config: {exc}", file=sys.stderr)
        return 2

    changed = new_text != original
    result = {"file": str(path), "changed": changed, **report}
    if args.dry_run:
        result["dry_run"] = True
        print(json.dumps(result, ensure_ascii=False))
        return 0

    if changed:
        atomic_write(path, new_text)
    print(json.dumps(result, ensure_ascii=False))
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    path = Path(args.file)
    names = [name for name in args.servers.split(",") if name]
    original = _read(path)
    if not original.strip():
        print(json.dumps({"file": str(path), "changed": False, "removed": []}))
        return 0
    try:
        new_text, removed = remove_servers(original, names)
    except JsoncError as exc:
        print(f"opencode-config: {exc}", file=sys.stderr)
        return 2

    changed = new_text != original
    result = {
        "file": str(path),
        "changed": changed,
        "removed": removed,
    }
    if args.dry_run:
        result["dry_run"] = True
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if changed:
        atomic_write(path, new_text)
    print(json.dumps(result, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opencode_config.py",
        description="Comment-preserving OpenCode JSONC editor for the harness.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    merge = subparsers.add_parser("merge", help="add or update MCP server entries")
    merge.add_argument("--file", required=True)
    merge.add_argument("--backend", required=True, choices=sorted(BACKEND_ALIASES))
    merge.add_argument("--force", action="store_true")
    merge.add_argument("--dry-run", action="store_true")
    merge.set_defaults(func=cmd_merge)

    remove = subparsers.add_parser("remove", help="remove MCP server entries")
    remove.add_argument("--file", required=True)
    remove.add_argument("--servers", required=True)
    remove.add_argument("--dry-run", action="store_true")
    remove.set_defaults(func=cmd_remove)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
