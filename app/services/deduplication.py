"""Duplicate detection.

Three layers, cheapest first:
  1. Article ID  = hash of the canonical URL (tracking params, AMP, www, etc.
     removed). Same link seen before -> ignored.
  2. Title fingerprint = hash of the headline's significant words, sorted.
     Same headline from another link -> duplicate.
  3. Similarity = Jaccard overlap of headline words against stories from the
     last 48h. "Sensex jumps 800 points as RBI cuts repo rate" vs
     "RBI cuts repo rate; Sensex jumps 800 pts" -> duplicate.

Duplicates are still stored (status 'duplicate') so their URLs are never
re-processed, and they increase the original story's `coverage_count` -
a story many outlets cover is a stronger "trending" signal.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable

from app.collectors.base import RawArticle
from app.database.db import Database
from app.database.models import utcnow
from app.utils.text import article_id_for, canonical_url, jaccard, title_fingerprint, title_tokens

log = logging.getLogger(__name__)


@dataclass
class DedupCandidate:
    raw: RawArticle
    id: str
    canonical: str
    fingerprint: str
    duplicate_of: str | None = None


def is_similar(a: set[str], b: set[str], threshold: float = 0.6) -> bool:
    if len(a) < 3 or len(b) < 3:
        return a == b and bool(a)
    smaller = min(len(a), len(b))
    t = threshold if smaller >= 6 else max(threshold, 0.7)
    if jaccard(a, b) >= t:
        return True
    # short headline fully contained in a longer one ("Nvidia unveils AI chip" vs
    # "Nvidia unveils AI chip for data centres")
    return smaller >= 4 and len(a & b) / smaller >= 0.9


class Deduplicator:
    def __init__(self, db: Database, threshold: float = 0.6, window_hours: int = 48):
        self.db = db
        self.threshold = threshold
        self.window_hours = window_hours

    def run(self, raws: Iterable[RawArticle]) -> tuple[list[DedupCandidate], list[DedupCandidate]]:
        """Split into (unique new stories, duplicates of known stories). URLs
        already in the database are dropped entirely."""
        # Best sources first, so they become the "original" of a story.
        ordered = sorted(raws, key=lambda r: (not r.official, -r.weight, r.published_at))
        cands: list[DedupCandidate] = []
        seen_ids: set[str] = set()
        for r in ordered:
            canon = canonical_url(r.url)
            aid = article_id_for(r.url)
            if aid in seen_ids:
                continue
            seen_ids.add(aid)
            cands.append(DedupCandidate(r, aid, canon, title_fingerprint(r.title)))

        known = self.db.existing_ids(c.id for c in cands)
        cands = [c for c in cands if c.id not in known]

        recent = self.db.recent_titles(utcnow() - timedelta(hours=self.window_hours))
        fp_index: dict[str, str] = {}
        token_index: list[tuple[str, set[str]]] = []
        for rid, fp, title in recent:
            fp_index.setdefault(fp, rid)
            token_index.append((rid, title_tokens(title)))

        unique: list[DedupCandidate] = []
        dups: list[DedupCandidate] = []
        for c in cands:
            toks = title_tokens(c.raw.title)
            original = fp_index.get(c.fingerprint) if toks else None
            if original is None:
                for rid, other in token_index:
                    if is_similar(toks, other, self.threshold):
                        original = rid
                        break
            if original:
                c.duplicate_of = original
                dups.append(c)
            else:
                unique.append(c)
                fp_index.setdefault(c.fingerprint, c.id)
                token_index.append((c.id, toks))
        log.info("dedup", extra={"new": len(unique), "duplicates": len(dups), "already_known": len(known)})
        return unique, dups
