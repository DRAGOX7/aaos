"""Side-effect-free runtime path configuration for document ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DocumentRuntimePaths:
    database_path: Path
    content_store_root: Path
    ingestion_lock_path: Path


def resolve_document_runtime_paths(data_root: str | Path) -> DocumentRuntimePaths:
    """Resolve confined Phase 3 paths without creating filesystem state."""
    root = Path(data_root).expanduser()
    if not root.is_absolute() or root.resolve(strict=False) != root:
        raise ValueError("document data root must be an absolute normalized path")
    return DocumentRuntimePaths(
        database_path=root / "database" / "aaos.db",
        content_store_root=root / "documents",
        ingestion_lock_path=root / "temporary" / "phase3-ingestion.lock",
    )
