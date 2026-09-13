"""Storage, file resolution, serialization, and index management."""
from context_forge.store.paths import find_repo_root, brain_paths, atomic_write, read_text
from context_forge.store.markdown import parse_markdown_record, serialize_markdown_record
from context_forge.store.registry import write_registry, sync_routing_index
from context_forge.store.audit import append_audit_log

__all__ = [
    "find_repo_root",
    "brain_paths",
    "atomic_write",
    "read_text",
    "parse_markdown_record",
    "serialize_markdown_record",
    "write_registry",
    "sync_routing_index",
    "append_audit_log",
]
