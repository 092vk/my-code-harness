#!/usr/bin/env python3
"""Installer and uninstaller for opencode-graph-harness.

The shell entry points (scripts/setup.sh and scripts/uninstall.sh) are thin
wrappers around this module so that file edits, backups, JSONC merging, and the
manifest all behave consistently.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from opencode_config import (
    SCHEMA_URL,
    JsoncError,
    build_servers,
    remove_servers,
    upsert_servers,
)

VERSION = "1.0.0"
STATE_DIR = Path(".opencode") / "graph-harness"
MANIFEST_NAME = "manifest.json"
SKILL_REL = Path(".opencode") / "skills" / "code-navigation" / "SKILL.md"
AGENTS_REL = Path("AGENTS.md")
BEGIN_MARK = (
    "<!-- BEGIN opencode-graph-harness "
    "(managed; edits inside this block are overwritten) -->"
)
END_MARK = "<!-- END opencode-graph-harness -->"
BLOCK_RE = re.compile(
    re.escape(BEGIN_MARK) + r".*?" + re.escape(END_MARK) + r"\n?",
    re.DOTALL,
)
CONFIG_CANDIDATES = (
    Path(".opencode") / "opencode.jsonc",
    Path(".opencode") / "opencode.json",
    Path("opencode.jsonc"),
    Path("opencode.json"),
)


@dataclass
class Action:
    label: str
    detail: str


@dataclass
class Plan:
    target: Path
    backend: str
    agents_text: str
    agents_existed: bool
    skill_text: str
    skill_existed: bool
    config_path: Path
    config_text: str
    config_existed: bool
    config_created: bool
    config_report: Dict[str, Any]
    stale_servers: List[str] = field(default_factory=list)
    actions: List[Action] = field(default_factory=list)


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode if path.exists() else None
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8", newline="")
    if mode is not None:
        os.chmod(temp, mode)
    os.replace(temp, path)


def resolve_target(raw: str) -> Path:
    target = Path(raw).expanduser()
    if not target.exists():
        raise SystemExit(f"setup: target directory does not exist: {target}")
    if not target.is_dir():
        raise SystemExit(f"setup: target is not a directory: {target}")
    return target.resolve()


def pick_config_path(target: Path) -> Path:
    for candidate in CONFIG_CANDIDATES:
        path = target / candidate
        if path.exists():
            return path
    return target / "opencode.json"


def compose_agents(existing: str, harness_text: str) -> str:
    block = f"{BEGIN_MARK}\n{harness_text.rstrip()}\n{END_MARK}\n"
    if BLOCK_RE.search(existing):
        return BLOCK_RE.sub(block, existing, count=1)
    if existing.strip():
        return existing.rstrip() + "\n\n" + block
    return block


def load_manifest(target: Path) -> Optional[Dict[str, Any]]:
    path = target / STATE_DIR / MANIFEST_NAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def servers_for_backend(backend: str) -> List[str]:
    return list(build_servers(backend).keys())


def make_plan(
    target: Path,
    harness: Path,
    backend: str,
    force: bool,
    previous: Optional[Dict[str, Any]],
) -> Plan:
    if target == harness:
        raise SystemExit(
            "setup: refusing to install the harness into its own checkout. "
            "Pass the repository you want to index instead."
        )

    harness_agents = harness / AGENTS_REL
    harness_skill = harness / SKILL_REL
    for required in (harness_agents, harness_skill):
        if not required.exists():
            raise SystemExit(f"setup: harness file is missing: {required}")

    agents_path = target / AGENTS_REL
    agents_existed = agents_path.exists()
    existing_agents = read_text(agents_path)
    agents_text = compose_agents(existing_agents, read_text(harness_agents))

    skill_path = target / SKILL_REL
    skill_existed = skill_path.exists()
    skill_text = read_text(harness_skill)

    config_path = pick_config_path(target)
    config_existed = config_path.exists()
    config_was_empty = config_existed and not read_text(config_path).strip()
    config_created = (not config_existed) or config_was_empty
    if previous and previous.get("config", {}).get("created"):
        config_created = True
    servers = build_servers(backend)

    if not config_existed or not read_text(config_path).strip():
        config_text = (
            json.dumps(
                {"$schema": SCHEMA_URL, "mcp": {"servers": servers}},
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        )
        config_report = {
            "added": list(servers.keys()),
            "updated": [],
            "unchanged": [],
            "conflicts": [],
        }
    else:
        try:
            config_text, config_report = upsert_servers(
                read_text(config_path), servers, force=force
            )
        except JsoncError as exc:
            raise SystemExit(f"setup: {exc}") from exc

    # If the backend changed, remove servers that this harness installed before
    # but that are no longer selected. Never touch servers the manifest does not
    # claim.
    stale: List[str] = []
    if previous and previous.get("servers"):
        try:
            from opencode_config import Document

            parsed_config = Document(config_text).value
            current_servers = (
                parsed_config.get("mcp", {}).get("servers", {})
                if isinstance(parsed_config, dict)
                else {}
            )
        except (JsoncError, ValueError):
            current_servers = {}
        stale = [
            name
            for name in previous["servers"]
            if name not in servers and name in current_servers
        ]
    if stale:
        try:
            config_text, removed = remove_servers(config_text, stale)
            config_report.setdefault("removed", []).extend(removed)
            stale = removed
        except JsoncError as exc:
            raise SystemExit(f"setup: {exc}") from exc

    plan = Plan(
        target=target,
        backend=backend,
        agents_text=agents_text,
        agents_existed=agents_existed,
        skill_text=skill_text,
        skill_existed=skill_existed,
        config_path=config_path,
        config_text=config_text,
        config_existed=config_existed,
        config_created=config_created,
        config_report=config_report,
        stale_servers=stale,
    )

    if agents_text != existing_agents:
        plan.actions.append(
            Action("AGENTS.md", "update managed graph-first rules block")
        )
    else:
        plan.actions.append(Action("AGENTS.md", "already up to date"))
    if skill_text != read_text(skill_path):
        plan.actions.append(Action(str(SKILL_REL), "install/update skill"))
    else:
        plan.actions.append(Action(str(SKILL_REL), "already up to date"))
    if plan.config_text != read_text(config_path):
        plan.actions.append(
            Action(
                str(config_path.relative_to(target)),
                "add/update MCP servers: " + ", ".join(sorted(servers)),
            )
        )
    else:
        plan.actions.append(
            Action(str(config_path.relative_to(target)), "already up to date")
        )
    if stale:
        plan.actions.append(
            Action("MCP config", "remove unused harness servers: " + ", ".join(stale))
        )
    return plan


def backup_files(target: Path, paths: Iterable[Path], stamp: str) -> Optional[Path]:
    existing = [path for path in paths if path.exists()]
    if not existing:
        return None
    backup_dir = target / STATE_DIR / "backups" / stamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    for path in existing:
        relative = path.relative_to(target)
        flattened = str(relative).replace(os.sep, "__")
        shutil.copy2(path, backup_dir / flattened)
    return backup_dir


def write_state_gitignore(target: Path) -> None:
    state = target / STATE_DIR
    state.mkdir(parents=True, exist_ok=True)
    path = state / ".gitignore"
    if not path.exists():
        atomic_write(path, "backups/\n")


def relative_config(target: Path, path: Path) -> str:
    try:
        return str(path.relative_to(target))
    except ValueError:
        return str(path)


def install(
    target: Path,
    harness: Path,
    backend: str,
    force: bool,
    dry_run: bool,
    run_index: bool,
) -> int:
    previous = load_manifest(target)
    plan = make_plan(target, harness, backend, force, previous)

    print(f"opencode-graph-harness v{VERSION}")
    print(f"  target:  {target}")
    print(f"  backend: {backend}")
    print()

    if dry_run:
        print("Dry run: no files were written.")
        for action in plan.actions:
            print(f"  - {action.label}: {action.detail}")
        return 0

    stamp = utc_timestamp()
    changed_paths: List[Path] = []
    if plan.agents_text != read_text(target / AGENTS_REL):
        changed_paths.append(target / AGENTS_REL)
    if plan.skill_text != read_text(target / SKILL_REL):
        changed_paths.append(target / SKILL_REL)
    if plan.config_text != read_text(plan.config_path):
        changed_paths.append(plan.config_path)
    backup_dir = backup_files(target, changed_paths, stamp)

    if plan.agents_text != read_text(target / AGENTS_REL):
        atomic_write(target / AGENTS_REL, plan.agents_text)
    if plan.skill_text != read_text(target / SKILL_REL):
        atomic_write(target / SKILL_REL, plan.skill_text)
    if plan.config_text != read_text(plan.config_path):
        atomic_write(plan.config_path, plan.config_text)

    write_state_gitignore(target)

    manifest = {
        "harness": "opencode-graph-harness",
        "version": VERSION,
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "harness_dir": str(harness),
        "backend": backend,
        "servers": servers_for_backend(backend),
        "agents": {
            "path": str(AGENTS_REL),
            "preexisting": plan.agents_existed,
        },
        "skill": {
            "path": str(SKILL_REL),
            "preexisting": plan.skill_existed,
        },
        "config": {
            "path": relative_config(target, plan.config_path),
            "created": plan.config_created,
        },
        "backup": str(backup_dir.relative_to(target)) if backup_dir else None,
    }
    atomic_write(
        target / STATE_DIR / MANIFEST_NAME,
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
    )

    for action in plan.actions:
        print(f"  - {action.label}: {action.detail}")
    if backup_dir:
        print(f"  - backup: {backup_dir.relative_to(target)}")
    print(flush=True)

    if run_index:
        index_script = harness / "scripts" / "index.sh"
        print(f"Indexing {target} ...", flush=True)
        result = subprocess.run(
            ["bash", str(index_script), "--project", str(target), "--backend", backend],
            check=False,
        )
        print()
        if result.returncode != 0:
            print(
                "Indexing did not complete. Re-run "
                f"{index_script} --project {target} after fixing the error.",
                file=sys.stderr,
            )
            return result.returncode

    print("Next steps")
    if run_index:
        print("  1. Index built successfully.")
    else:
        print(f"  1. Build the index: {harness}/scripts/index.sh --project {target}")
    print("  2. Open OpenCode in the target and reload if needed (/reload).")
    print("  3. Ask a structural question, for example: 'Who calls <symbol>?'")
    print()
    print("The graph-first rules are in AGENTS.md; the workflow guide is")
    print(f"  {SKILL_REL}")
    return 0


def remove_agents_block(target: Path, manifest: Optional[Dict[str, Any]]) -> List[str]:
    path = target / AGENTS_REL
    if not path.exists():
        return []
    text = read_text(path)
    if not BLOCK_RE.search(text):
        return ["AGENTS.md: no managed block found; left unchanged"]
    new_text = BLOCK_RE.sub("", text, count=1)
    new_text = new_text.strip()
    if not new_text:
        path.unlink()
        return ["AGENTS.md: removed (contained only the managed block)"]
    atomic_write(path, new_text + "\n")
    return ["AGENTS.md: removed managed block"]


def remove_skill(target: Path, manifest: Optional[Dict[str, Any]]) -> List[str]:
    path = target / SKILL_REL
    if not path.exists():
        return []
    text = read_text(path)
    managed = "managed by opencode-graph-harness" in text
    claimed = bool(
        manifest
        and manifest.get("skill", {}).get("path") == str(SKILL_REL)
    )
    if not managed and not claimed:
        return [f"{SKILL_REL}: not managed by the harness; left unchanged"]
    path.unlink()
    # Remove now-empty skill directories, stopping before .opencode itself.
    directory = path.parent
    stop = target / ".opencode"
    while directory != stop and stop in directory.parents:
        try:
            directory.rmdir()
        except OSError:
            break
        directory = directory.parent
    return [f"{SKILL_REL}: removed"]


def config_is_harness_only(text: str) -> bool:
    try:
        from opencode_config import Document

        value = Document(text).value
    except (JsoncError, ValueError):
        return False
    if not isinstance(value, dict):
        return False
    allowed = {"$schema", "mcp"}
    if set(value) - allowed:
        return False
    mcp = value.get("mcp", {})
    if not isinstance(mcp, dict):
        return False
    if set(mcp) != {"servers"}:
        return False
    servers = mcp.get("servers", {})
    return isinstance(servers, dict) and not servers


def remove_config(
    target: Path, manifest: Optional[Dict[str, Any]]
) -> List[str]:
    path = pick_config_path(target)
    if manifest:
        configured = manifest.get("config", {}).get("path")
        if configured:
            path = target / configured
    if not path.exists():
        return []

    names = (
        manifest.get("servers")
        if manifest and manifest.get("servers")
        else ["codebase-memory", "codegraph"]
    )
    text = read_text(path)
    try:
        new_text, removed = remove_servers(text, names)
    except JsoncError as exc:
        return [f"{relative_config(target, path)}: {exc}; left unchanged"]
    if not removed:
        return [f"{relative_config(target, path)}: no harness servers found"]

    created = bool(manifest and manifest.get("config", {}).get("created"))
    if created and config_is_harness_only(new_text):
        path.unlink()
        return [
            f"{relative_config(target, path)}: removed (created by the harness)"
        ]
    atomic_write(path, new_text)
    return [
        f"{relative_config(target, path)}: removed " + ", ".join(removed)
    ]


def uninstall(target: Path, dry_run: bool, keep_backups: bool) -> int:
    manifest = load_manifest(target)
    if manifest is None and not (target / STATE_DIR).exists():
        # Still allow a best-effort cleanup of an interrupted install.
        has_block = (
            (target / AGENTS_REL).exists()
            and BEGIN_MARK in read_text(target / AGENTS_REL)
        )
        if not has_block and not (target / SKILL_REL).exists():
            print("opencode-graph-harness is not installed in this repository.")
            return 0

    print(f"opencode-graph-harness v{VERSION} uninstall")
    print(f"  target: {target}")
    print()

    if dry_run:
        print("Dry run: no files were written.")
        if (target / AGENTS_REL).exists() and BLOCK_RE.search(
            read_text(target / AGENTS_REL)
        ):
            print("  - AGENTS.md: remove managed block")
        if (target / SKILL_REL).exists():
            print(f"  - {SKILL_REL}: remove")
        print("  - MCP config: remove harness servers")
        if keep_backups:
            print(f"  - {STATE_DIR}/: keep state and backups")
        else:
            print(f"  - {STATE_DIR}/: remove state and backups")
        return 0

    messages: List[str] = []
    messages.extend(remove_agents_block(target, manifest))
    messages.extend(remove_skill(target, manifest))
    messages.extend(remove_config(target, manifest))

    state = target / STATE_DIR
    if state.exists():
        if keep_backups:
            messages.append(f"{STATE_DIR}/: kept (manifest and backups)")
        else:
            shutil.rmtree(state)
            messages.append(f"{STATE_DIR}/: removed")

    # Remove an empty .opencode directory, but never one that has other content.
    try:
        (target / ".opencode").rmdir()
    except OSError:
        pass

    for message in messages:
        print(f"  - {message}")
    print()
    print("Indexes were left in place:")
    print("  - codebase-memory-mcp: ~/.cache/codebase-memory-mcp")
    print("  - codegraph: .codegraph/ (remove with 'codegraph uninit')")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="setup.sh",
        description=(
            "Install graph-first navigation (AGENTS.md rules, the "
            "code-navigation skill, and MCP config) into a repository."
        ),
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=".",
        help="target repository (default: current directory)",
    )
    parser.add_argument(
        "--backend",
        choices=["cbm", "codegraph", "both", "codebase-memory", "codebase-memory-mcp"],
        help="graph backend (default: existing manifest, else cbm)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing MCP server entry that differs",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show planned changes without writing",
    )
    parser.add_argument(
        "--index",
        action="store_true",
        help="run scripts/index.sh after installing",
    )
    parser.add_argument(
        "--version", action="version", version=f"opencode-graph-harness {VERSION}"
    )
    # Internal: the shell wrapper supplies the harness checkout path.
    parser.add_argument("--harness-dir", required=True, help=argparse.SUPPRESS)
    return parser


def setup_main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    target = resolve_target(args.target)
    harness = Path(args.harness_dir).expanduser().resolve()

    previous = load_manifest(target)
    backend_raw = args.backend or (previous or {}).get("backend") or "cbm"
    backend = {
        "codebase-memory": "cbm",
        "codebase-memory-mcp": "cbm",
    }.get(backend_raw, backend_raw)
    if backend not in ("cbm", "codegraph", "both"):
        raise SystemExit(f"setup: unsupported backend: {backend_raw}")

    return install(
        target=target,
        harness=harness,
        backend=backend,
        force=args.force,
        dry_run=args.dry_run,
        run_index=args.index,
    )


def uninstall_main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="uninstall.sh",
        description="Remove the harness files, MCP entries, and state from a repository.",
    )
    parser.add_argument("target", nargs="?", default=".")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--keep-backups",
        action="store_true",
        help="keep the manifest and file backups under .opencode/graph-harness/",
    )
    parser.add_argument(
        "--version", action="version", version=f"opencode-graph-harness {VERSION}"
    )
    args = parser.parse_args(argv)
    return uninstall(resolve_target(args.target), args.dry_run, args.keep_backups)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("command", nargs="?", choices=["setup", "uninstall"])
    parser.add_argument("args", nargs=argparse.REMAINDER)
    parsed = parser.parse_args(argv)
    if parsed.command == "setup":
        return setup_main(parsed.args)
    if parsed.command == "uninstall":
        return uninstall_main(parsed.args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
