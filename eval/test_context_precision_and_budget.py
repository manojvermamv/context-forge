#!/usr/bin/env python3
"""Benchmark: Context Precision, Recall, Budget Adherence & Determinism.

Evaluates:
- Context Precision: Percentage of included context directly relevant to the task.
- Context Recall: Retrieval of known-gold authoritative records and source files.
- Budget Adherence: Strict character ceiling enforcement without overflow.
- Provenance Completeness: Preservation of full commit anchors and authority metadata.
- Determinism: Byte-for-byte identical compilation across repeated runs.
- Traceability Verification: Typed edges correctly linked from REQ -> ADR -> Source -> Test.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from context_forge.cli.commands import cmd_init, cmd_scan, cmd_index
from context_forge.compiler.pack import compile_context_pack
from context_forge.knowledge.update import create_knowledge_record
from context_forge.store.paths import brain_paths, read_text
from context_forge.traceability.resolver import resolve_traceability_graph


def evaluate_precision_and_budget() -> dict[str, float | int | bool]:
    tmp = Path(tempfile.mkdtemp())
    try:
        cmd_init(tmp)
        p = brain_paths(tmp)

        # 1. Create source trees: Task-relevant auth/ files and unrelated billing/ files
        (tmp / "auth").mkdir(parents=True, exist_ok=True)
        (tmp / "tests").mkdir(parents=True, exist_ok=True)
        (tmp / "billing").mkdir(parents=True, exist_ok=True)

        (tmp / "auth" / "tokens.py").write_text("class RefreshTokenValidator:\n    def validate(self): pass\n", encoding="utf-8")
        (tmp / "auth" / "middleware.py").write_text("class AuthMiddleware:\n    def process(self): pass\n", encoding="utf-8")
        (tmp / "tests" / "test_refresh.py").write_text("def test_refresh_token_validation(): pass\n", encoding="utf-8")

        # Unrelated corpus files
        (tmp / "billing" / "invoice.py").write_text("class InvoiceGenerator:\n    def export_pdf(self): pass\n", encoding="utf-8")
        (tmp / "billing" / "stripe_client.py").write_text("class StripeClient:\n    def charge(self): pass\n", encoding="utf-8")
        (tmp / "tests" / "test_invoice.py").write_text("def test_invoice_export(): pass\n", encoding="utf-8")

        # 2. Add Known-Gold relevant knowledge records
        create_knowledge_record(
            repo=tmp,
            kind="requirement",
            title="JWT Refresh Token Policy",
            body="Refresh tokens must rotate on every use and expire after 7 days.",
            authority="user_explicit",
            evidence="PRD Section 4.2",
            scope=["auth/tokens.py", "tests/test_refresh.py"],
            accept=True,
        )
        create_knowledge_record(
            repo=tmp,
            kind="decision",
            title="Refresh Token Rotation Strategy",
            body="Implement single-use refresh token rotation to mitigate token theft.",
            authority="user_explicit",
            evidence="Security review ADR",
            scope=["auth/tokens.py", "auth/middleware.py"],
            accept=True,
        )
        create_knowledge_record(
            repo=tmp,
            kind="technical",
            title="Token Refresh Handler Internals",
            body="RefreshTokenValidator inspects hash in persistent store and revokes family on reuse.",
            authority="code_observed",
            evidence="Code audit of auth/tokens.py",
            scope=["auth/tokens.py"],
            accept=True,
        )
        create_knowledge_record(
            repo=tmp,
            kind="question",
            title="Refresh Token Revocation Latency",
            body="What is the allowable latency window for propagating token revocation across regions?",
            authority="unresolved",
            evidence="Team discussion",
            scope=["auth/tokens.py"],
            accept=True,
        )

        # 3. Add Unrelated knowledge records
        create_knowledge_record(
            repo=tmp,
            kind="requirement",
            title="Billing Invoice Export to PDF",
            body="Monthly invoices must render as PDF and be sent to customer email.",
            authority="user_explicit",
            evidence="Finance requirements",
            scope=["billing/invoice.py", "tests/test_invoice.py"],
            accept=True,
        )
        create_knowledge_record(
            repo=tmp,
            kind="decision",
            title="Payment Gateway Integration with Stripe",
            body="Use Stripe SDK for card processing.",
            authority="user_explicit",
            evidence="Finance ADR",
            scope=["billing/stripe_client.py"],
            accept=True,
        )

        cmd_scan(tmp)
        cmd_index(tmp)

        # 4. Target Task Compilation
        task_query = "Modify JWT refresh token validation."
        target_budget = 3500

        pack = compile_context_pack(
            repo=tmp,
            task_query=task_query,
            paths=["auth/tokens.py", "auth/middleware.py", "tests/test_refresh.py"],
            budget_chars=target_budget,
        )

        rendered = pack.to_text()

        # Check budget adherence
        budget_adherence = len(rendered) <= target_budget

        # Check precision & recall
        known_gold_titles = [
            "JWT Refresh Token Policy",
            "Refresh Token Rotation Strategy",
            "Token Refresh Handler Internals",
            "Refresh Token Revocation Latency",
        ]
        unrelated_titles = [
            "Billing Invoice Export to PDF",
            "Payment Gateway Integration with Stripe",
        ]

        gold_hits = sum(1 for title in known_gold_titles if title in rendered)
        recall = gold_hits / len(known_gold_titles)

        unrelated_hits = sum(1 for title in unrelated_titles if title in rendered)
        # Precision: proportion of matched topic titles that were actually relevant
        total_matched_titles = gold_hits + unrelated_hits
        precision = (gold_hits / total_matched_titles) if total_matched_titles > 0 else 1.0

        # Check Next-Reading prioritization: auth files must appear before generic map or unrelated files
        next_reading = pack.next_reading
        auth_tokens_present = any("tokens.py" in p for p in next_reading)
        unrelated_absent_or_lower = True
        if "billing/invoice.py" in next_reading and auth_tokens_present:
            auth_idx = [i for i, p in enumerate(next_reading) if "tokens.py" in p][0]
            bill_idx = [i for i, p in enumerate(next_reading) if "billing/invoice.py" in p][0]
            unrelated_absent_or_lower = auth_idx < bill_idx

        # Check Traceability graph
        trace_graph = resolve_traceability_graph(tmp)
        has_typed_trace = len(trace_graph) >= 1 and all("edges" in t for t in trace_graph)

        # Check Determinism (content and section ranking must be byte-for-byte repeatable)
        pack2 = compile_context_pack(
            repo=tmp,
            task_query=task_query,
            paths=["auth/tokens.py", "auth/middleware.py", "tests/test_refresh.py"],
            budget_chars=target_budget,
        )
        pack2.compiled_at = pack.compiled_at
        deterministic = (rendered == pack2.to_text())

        # Check Provenance completeness
        records = list(p["requirements"].glob("*.md")) + list(p["decisions"].glob("*.md"))
        has_provenance = all(
            ("authority:" in read_text(r) and "status:" in read_text(r) and "updated:" in read_text(r))
            for r in records
        )

        return {
            "budget_adherence": budget_adherence,
            "rendered_chars": len(rendered),
            "target_budget": target_budget,
            "recall": recall,
            "precision": precision,
            "next_reading_prioritized": auth_tokens_present and unrelated_absent_or_lower,
            "deterministic": deterministic,
            "has_typed_trace": has_typed_trace,
            "has_provenance": has_provenance,
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    res = evaluate_precision_and_budget()
    print(f"Budget adherence: {res['budget_adherence']} ({res['rendered_chars']}/{res['target_budget']} chars)")
    print(f"Recall: {res['recall']*100:.1f}%")
    print(f"Precision: {res['precision']*100:.1f}%")
    print(f"Next reading prioritized: {res['next_reading_prioritized']}")
    print(f"Deterministic: {res['deterministic']}")
    print(f"Typed traceability: {res['has_typed_trace']}")
    print(f"Provenance completeness: {res['has_provenance']}")
