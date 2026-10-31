from __future__ import annotations

import os

TRUE_VALUES = {"1", "true", "yes", "on"}


def legacy_declaration_mirror_enabled() -> bool:
    """Keep the old per-document Tờ khai mirror for reference comparison only.

    The mirror is last-write-wins per column across every declaration file of a
    shipment, so it mixes separate declarations (`...960`, `...961`, a screenshot).
    A new draft must be written by `ShipmentResolutionService` instead; the legacy
    path stays reachable only when this flag is on, and never for NEW_DRAFT export.
    """
    return (os.environ.get("IMPORTDOC_LEGACY_DECLARATION_MIRROR") or "").strip().lower() in TRUE_VALUES


def new_draft_resolution_enabled(template_family: str | None = None) -> bool:
    """Feature flag by template family, defaulting to on for the TKN family."""
    raw = os.environ.get("IMPORTDOC_NEW_DRAFT_DISABLED") or ""
    disabled = {item.strip() for item in raw.split(",") if item.strip()}
    if template_family is None:
        return not disabled
    return template_family not in disabled
