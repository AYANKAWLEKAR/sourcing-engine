"""EntityResolver — the off-market bridge (plan §6).

Turns a scraped record (name + postcode, NO ABN) into one anchored to the ASIC
spine. It name-matches via the live ABN Lookup API, re-ranks candidates by
``0.60·name_sim + 0.25·postcode + 0.15·state``, accepts at RC ≥ 0.85 (keeps the
0.60–0.85 band flagged ``abn_match_uncertain``), then merges spine fields from
ASIC (which carries the ABN at 100% coverage).

Adapted to this repo's RawRecord shape: ABN Lookup name-match candidates expose
``org_name`` / ``state`` / ``postcode`` / ``abn`` (Score lives in ``raw``); the
ASIC spine row exposes ``acn`` / ``org_name`` / ``status_effective_from``.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..models.company import CompanyRecord

# Company-name suffixes stripped before fuzzy matching (register vs display names).
_SUFFIX = re.compile(r"\b(pty\s*ltd|pty\s*limited|limited|ltd|& co|and co|inc|corp)\b", re.I)

ACCEPT_THRESHOLD = 0.85
KEEP_THRESHOLD = 0.60


class EntityResolver:
    def __init__(self, api: Any = None, asic: Any = None) -> None:
        # Lazy construction so importing this module needs no credentials.
        if api is None:
            from ..connectors.abn.lookup import ABNLookupAPIConnector

            api = ABNLookupAPIConnector.from_settings()
        if asic is None:
            from ..connectors.asic_bulk import ASICBulkConnector

            asic = ASICBulkConnector.from_settings()
        self.api = api
        self.asic = asic

    # ------------------------------------------------------------------
    # Name → ABN resolution
    # ------------------------------------------------------------------

    def resolve(
        self, name: str | None, postcode: str | None, state: str | None
    ) -> tuple[str | None, float, dict]:
        """Return ``(abn, confidence, best_candidate)``.

        ``abn`` is None below the keep threshold. ``best_candidate`` is the raw
        candidate dict (state/postcode for backfill) — callers should not rely on
        the instance's internal state between calls.

        Fix 5: the best candidate is now returned directly instead of being stored
        in ``self._last_match``. The stash was a single-slot, thread-unsafe pattern:
        two concurrent calls would overwrite each other's candidate before ``enrich()``
        could read it.
        """
        if not name:
            return None, 0.0, {}
        norm = _SUFFIX.sub("", name).strip().lower()
        candidates = self.api.fetch({"name": norm or name, "state": state})
        if not candidates:
            return None, 0.0, {}

        scored: list[tuple[dict, float]] = []
        for c in candidates:
            cand_name = (c.get("org_name") or "").lower()
            name_sim = SequenceMatcher(None, norm, cand_name).ratio()
            pc = 1.0 if (postcode and c.get("postcode") == postcode) else 0.3
            st = 1.0 if (state and (c.get("state") or "").upper() == state.upper()) else 0.2
            scored.append((c, 0.60 * name_sim + 0.25 * pc + 0.15 * st))

        best, rc = max(scored, key=lambda x: x[1])
        if rc >= KEEP_THRESHOLD:
            return best.get("abn"), rc, best  # accept (≥0.85) or keep-uncertain (0.60–0.85)
        return None, rc, best  # unresolved but return candidate for diagnostics

    # ------------------------------------------------------------------
    # Anchor a scraped record to the spine
    # ------------------------------------------------------------------

    def enrich(self, record: CompanyRecord) -> CompanyRecord:
        """Resolve + merge spine fields onto ``record``. Mutates and returns it.

        Fix 5: consumes the 3-tuple from ``resolve()`` directly — no shared
        ``_last_match`` stash, so concurrent ``enrich()`` calls are safe.
        """
        from ..models.company import Provenance

        if record.abn:
            return record

        abn, rc, cand = self.resolve(
            record.legal_name, record.location.postcode, record.location.state
        )
        record.resolution_confidence = rc
        if not abn:
            record.flags.append("unresolved_abn")
            return record

        record.abn = abn
        if rc < ACCEPT_THRESHOLD:
            record.flags.append("abn_match_uncertain")

        # Backfill state/postcode from the matched ABN-register candidate (the
        # register address is authoritative when the scraped record lacks them).
        if not record.location.state and cand.get("state"):
            record.location.state = cand["state"]
        if not record.location.postcode and cand.get("postcode"):
            record.location.postcode = cand["postcode"]

        spine = self.asic.lookup_abn(abn)  # ASIC has the ABN column at 100% coverage
        if spine:
            record.acn = spine.get("acn")
            # Register name is the source of truth over the scraped display name.
            record.legal_name = spine.get("org_name") or record.legal_name
            record.age.asic_registered = spine.get("status_effective_from")
            record.provenance.append(
                Provenance(field="abn", source="abn_lookup_api", confidence=round(rc, 3))
            )
            record.provenance.append(
                Provenance(field="legal_name", source="asic_company_dataset", confidence=0.95)
            )
        return record
