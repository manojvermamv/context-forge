"""Knowledge lifecycle, candidate staging, approval, updating, and consolidation."""
from context_forge.knowledge.candidate import stage_candidate, pending_candidates
from context_forge.knowledge.approval import approve_candidate, validate_pending_candidate
from context_forge.knowledge.update import create_knowledge_record
from context_forge.knowledge.supersession import supersede_record
from context_forge.knowledge.consolidate import plan_or_apply_consolidation

__all__ = [
    "stage_candidate",
    "pending_candidates",
    "approve_candidate",
    "validate_pending_candidate",
    "create_knowledge_record",
    "supersede_record",
    "plan_or_apply_consolidation",
]
