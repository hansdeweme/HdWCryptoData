"""One shared resolver for derived metadata, index and quarantine locations."""
# Copyright (c) 2025, 2026 Hans De Weme
# Licensed under the MIT License (https://opensource.org/licenses/MIT).
# part of the HdW_crypto_data Project
#
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class ArchiveLocations:
    spot: Path
    metadata: Path
    index: Path
    quarantine: Path

def archive_locations(root) -> ArchiveLocations:
    from .archive_manager import resolve_archive_root, _safe_path
    spot = resolve_archive_root(root)
    metadata = _safe_path(spot.parent, spot.parent / ".hdw_archive", must_exist=False)
    index = _safe_path(metadata, metadata / "archive_index.sqlite", must_exist=False)
    legacy = _safe_path(spot.parent, spot.with_name(spot.name + "-quarantine"), must_exist=False)
    # Reuse existing manifests without rewriting or relocating operation history.
    quarantine = legacy if legacy.exists() else _safe_path(metadata, metadata / "quarantine", must_exist=False)
    return ArchiveLocations(spot, metadata, index, quarantine)

def spot_root_for_file(path):
    path = Path(path).absolute()
    return next((p for p in path.parents if p.name.lower() == "spot"), None)
