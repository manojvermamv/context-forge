#!/usr/bin/env python3
"""
brain.py — the context-forge v2 engine.

A single, zero-dependency (stdlib-only) CLI that implements a portable,
token-budgeted project intelligence and knowledge control plane.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure src/ package is in Python module search path
_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if _SRC_DIR.exists() and str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from context_forge.core.budgets import Budgets
from context_forge.core.identity import build_identity_envelope
from context_forge.knowledge.update import create_knowledge_record
from context_forge.knowledge.consolidate import plan_or_apply_consolidation
from context_forge.traceability.sync import reconcile_sync
from context_forge.hooks.guard import handle_guard
from context_forge.hooks.inject import handle_session_start, handle_turn_inject
from context_forge.hooks.capture import handle_capture
from context_forge.cli.commands import (
    cmd_init,
    cmd_map,
    cmd_index,
    cmd_scan,
    cmd_context,
    cmd_review,
    cmd_search,
    cmd_maintain,
)
from context_forge.cli.doctor import cmd_doctor, cmd_lint, cmd_status


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="brain.py",
        description="Context Forge v2 — Project Intelligence & Governance Control Plane",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init")
    p_init.add_argument("repo", type=Path)
    p_init.add_argument("--force", action="store_true")

    p_map = sub.add_parser("map")
    p_map.add_argument("repo", type=Path)

    p_index = sub.add_parser("index")
    p_index.add_argument("repo", type=Path)

    p_scan = sub.add_parser("scan")
    p_scan.add_argument("repo", type=Path)

    p_context = sub.add_parser("context")
    p_context.add_argument("repo", type=Path)
    p_context.add_argument("query", nargs="*", help="task words for a routed reading list")
    p_context.add_argument("--path", dest="paths", action="append", default=[], help="affected source path")
    p_context.add_argument("--pointers-only", action="store_true", help="output only routed file reading pointers")
    p_context.add_argument("--format", dest="output_format", choices=["text", "json"], default="text", help="output format (text or json)")
    p_context.add_argument("--explain", action="store_true", help="include provider diagnostics explanation")

    p_update = sub.add_parser("update")
    p_update.add_argument("repo", type=Path)
    p_update.add_argument("--kind", choices=["decision", "requirement", "technical", "question", "policy", "invariant"], required=True)
    p_update.add_argument("--title", required=True)
    p_update.add_argument("--body", required=True)
    p_update.add_argument(
        "--authority",
        required=True,
        choices=["user_explicit", "policy_mandate", "code_observed", "unresolved", "agent_inference", "external_source"],
    )
    p_update.add_argument("--evidence", default="")
    p_update.add_argument("--scope", action="append", default=[])
    p_update.add_argument("--accept", action="store_true", help="publish explicit user-approved intent")
    p_update.add_argument("--agent-id", default="", help="multi-agent author ID")
    p_update.add_argument("--agent-role", default="", help="multi-agent role")

    p_sync = sub.add_parser("sync")
    p_sync.add_argument("repo", type=Path)
    p_sync.add_argument("--path", dest="paths", action="append", default=[], help="changed path when Git cannot report it")
    p_sync.add_argument("--apply", action="store_true", help="write a code-observed technical record")

    p_maintain = sub.add_parser("maintain")
    p_maintain.add_argument("repo", type=Path)
    p_maintain.add_argument("--fix", action="store_true", help="regenerate map, routes, and registry before validation")

    sub.add_parser("session-start")
    sub.add_parser("turn-inject")
    sub.add_parser("guard")

    p_capture = sub.add_parser("capture")
    p_capture.add_argument("--event", choices=["stop", "precompact", "subagentstop", "sessionend"], required=True)

    p_lint = sub.add_parser("lint")
    p_lint.add_argument("repo", type=Path)

    p_review = sub.add_parser("review")
    p_review.add_argument("repo", type=Path)
    p_review.add_argument("--if-due", action="store_true")
    p_review.add_argument("--pending", action="store_true", help="list staged capture candidates")
    p_review.add_argument("--approve", metavar="CANDIDATE_ID", help="explicitly promote one reviewed candidate")

    p_consolidate = sub.add_parser("consolidate")
    p_consolidate.add_argument("repo", type=Path)
    p_consolidate.add_argument("--apply", action="store_true", help="explicitly apply a reviewed consolidation plan")

    p_doctor = sub.add_parser("doctor")
    p_doctor.add_argument("repo", type=Path)

    p_status = sub.add_parser("status")
    p_status.add_argument("repo", type=Path)

    p_search = sub.add_parser("search")
    p_search.add_argument("repo", type=Path)
    p_search.add_argument("query")

    args = ap.parse_args(argv)

    if args.cmd == "init":
        cmd_init(args.repo, args.force)
    elif args.cmd == "scan":
        return cmd_scan(args.repo)
    elif args.cmd == "map":
        cmd_map(args.repo)
    elif args.cmd == "index":
        cmd_index(args.repo)
    elif args.cmd == "context":
        cmd_context(
            args.repo,
            " ".join(args.query),
            args.paths,
            pointers_only=getattr(args, "pointers_only", False),
            output_format=getattr(args, "output_format", "text"),
            explain=getattr(args, "explain", False),
        )
    elif args.cmd == "update":
        identity = build_identity_envelope(repo=args.repo, agent_id=args.agent_id, agent_role=args.agent_role)
        return create_knowledge_record(
            args.repo,
            args.kind,
            args.title,
            args.body,
            args.authority,
            args.evidence,
            args.scope,
            args.accept,
            identity=identity,
        )
    elif args.cmd == "sync":
        return reconcile_sync(args.repo, args.paths, args.apply)
    elif args.cmd == "maintain":
        return cmd_maintain(args.repo, args.fix)
    elif args.cmd == "session-start":
        handle_session_start()
    elif args.cmd == "turn-inject":
        handle_turn_inject()
    elif args.cmd == "guard":
        handle_guard()
    elif args.cmd == "capture":
        handle_capture(args.event)
    elif args.cmd == "lint":
        for line in cmd_lint(args.repo):
            print(f"- {line}")
    elif args.cmd == "review":
        return cmd_review(args.repo, args.if_due, args.pending, args.approve)
    elif args.cmd == "consolidate":
        return plan_or_apply_consolidation(args.repo, args.apply)
    elif args.cmd == "doctor":
        cmd_doctor(args.repo)
    elif args.cmd == "status":
        cmd_status(args.repo)
    elif args.cmd == "search":
        cmd_search(args.repo, args.query)
    return 0


if __name__ == "__main__":
    sys.exit(main())
