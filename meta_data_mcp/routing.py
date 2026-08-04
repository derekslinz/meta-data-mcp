"""Sophisticated multi-criteria routing engine for provider discovery.

This module provides intelligent ranking of providers based on:
- Token matching (exact matches, partial matches)
- Fuzzy matching (typo tolerance via Levenshtein distance)
- Semantic similarity (Jaccard overlap over query/description terms)
- Metadata matching (domains, regions, keywords)
- Cached results for frequent queries

The RoutingEngine accepts pluggable scoring strategies and combines them
with configurable weights for flexible tuning.
"""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import logging
import re
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from dataclasses import dataclass

from meta_data_mcp.registry import ProviderEntry, iter_registry

log = logging.getLogger(__name__)


def _tokenize(text: str | None) -> set[str]:
    """Tokenize text into lowercase alphanumeric terms."""
    if not text:
        return set()
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _normalize_filter(value: str | None) -> str | None:
    """Normalize domain/region filter values."""
    return value.lower().strip() if value else None


@dataclass(frozen=True)
class ScoredProvider:
    """A provider with its ranking score and scoring breakdown."""

    entry: ProviderEntry
    score: float
    breakdown: dict[str, float] | None = None  # {strategy: score, ...}


class Scorer(ABC):
    """Base class for provider scoring strategies."""

    @abstractmethod
    async def score(self, query: str | None, provider: ProviderEntry) -> float:
        """Return score 0.0-1.0 for how well this provider matches the query."""


class TokenScorer(Scorer):
    """Token-level matching with keyword bonuses.

    Splits query into tokens and matches against provider metadata.
    Keywords receive 3x weight; other matches 1x weight.
    """

    async def score(self, query: str | None, provider: ProviderEntry) -> float:
        if not query:
            return 0.0

        q_tokens = set(query.lower().split())
        if not q_tokens:
            return 0.0

        # Build haystack of all searchable provider text
        haystack = " ".join(
            (
                provider.id,
                provider.title,
                provider.description,
                " ".join(provider.keywords),
                " ".join(provider.domains),
                " ".join(provider.regions),
            ),
        ).lower()

        score = 0.0
        for token in q_tokens:
            if token in haystack:
                score += 1.0
                # Bonus for keyword matches
                if token in provider.keywords:
                    score += 2.0

        # Normalize to 0-1
        return min(score / (len(q_tokens) * 3), 1.0)


class FuzzyScorer(Scorer):
    """Levenshtein distance matching for typo tolerance.

    Uses difflib.SequenceMatcher to find fuzzy matches in provider metadata.
    Threshold: 0.6 similarity required.
    """

    async def score(self, query: str | None, provider: ProviderEntry) -> float:
        if not query:
            return 0.0

        q_lower = query.lower()
        targets = [
            provider.id,
            provider.title.lower(),
        ] + [k.lower() for k in provider.keywords]

        # Find best match
        best_ratio = 0.0
        for target in targets:
            ratio = difflib.SequenceMatcher(None, q_lower, target).ratio()
            best_ratio = max(best_ratio, ratio)

        # Only return if threshold met (>0.6 similarity)
        return max(0.0, best_ratio - 0.4) if best_ratio > 0.6 else 0.0


class MetadataScorer(Scorer):
    """Domain, region, and keyword exact/partial matching.

    Awards points for domain/region matches (hard filter + bonus).
    """

    async def score(self, query: str | None, provider: ProviderEntry) -> float:
        if not query:
            return 0.0

        q_tokens = _tokenize(query)
        if not q_tokens:
            return 0.0
        score = 0.0

        # Check if query contains domain or region tokens
        for domain in provider.domains:
            if _tokenize(domain).issubset(q_tokens):
                score += 0.5

        for region in provider.regions:
            if _tokenize(region).issubset(q_tokens):
                score += 0.3

        return min(score, 1.0)


class SimpleSemanticScorer(Scorer):
    """Simple word-frequency based semantic similarity.

    Compares query against provider description using term frequency.
    No external dependencies (no sklearn yet).
    """

    async def score(self, query: str | None, provider: ProviderEntry) -> float:
        if not query:
            return 0.0

        q_terms = set(query.lower().split())
        desc_text = f"{provider.description} {' '.join(provider.keywords)}".lower()
        desc_terms = set(desc_text.split())

        if not q_terms or not desc_terms:
            return 0.0

        # Jaccard similarity
        intersection = len(q_terms & desc_terms)
        union = len(q_terms | desc_terms)
        return intersection / union if union > 0 else 0.0


class HealthScorer(Scorer):
    """Score provider by recent reliability, from meta_data_mcp.health.

    Returns 1.0 for providers with no recorded failures, scaling down toward
    0.0 as recent failures accumulate. Decays back to 1.0 over time.
    """

    async def score(self, query: str | None, provider: ProviderEntry) -> float:
        # Local import to avoid an import cycle at module load time.
        from meta_data_mcp import health

        return health.health_score(provider.id)


class RoutingEngine:
    """Multi-criteria provider router with caching and explainability.

    Combines multiple scoring strategies with configurable weights.
    Implements LRU caching for frequent queries.
    """

    def __init__(
        self,
        scorers: dict[str, Scorer] | None = None,
        weights: dict[str, float] | None = None,
        cache_size: int = 1000,
        cache_ttl_seconds: int = 3600,
    ):
        """Initialize the routing engine.

        Args:
            scorers: Dict of {strategy_name: Scorer instance}
            weights: Dict of {strategy_name: weight (0-1)}
            cache_size: Maximum cached queries
            cache_ttl_seconds: Cache expiration time

        """
        self.scorers = scorers or {
            "token": TokenScorer(),
            "fuzzy": FuzzyScorer(),
            "metadata": MetadataScorer(),
            "semantic": SimpleSemanticScorer(),
            # Health is fed by ``http_get`` / ``http_post`` whenever callers
            # pass ``provider=``. After the Phase 4 sweep every shipped
            # provider routes through the kernel, so the unrecorded-provider
            # 1.0 baseline is now an accurate "no known failures" signal
            # rather than a no-data fallback. Phase 3 raises the default
            # weight from 0.0 to a small non-zero value so routing can
            # de-prioritise providers in active failure without dominating
            # the score. Callers can still override via ``weights``.
            "health": HealthScorer(),
        }

        self.weights = weights or {
            "token": 0.3,
            "fuzzy": 0.2,
            "metadata": 0.25,
            "semantic": 0.25,
            # Phase 3: bumped from 0.0 → 0.05. Pinned by
            # tests/test_health.py::test_default_engine_health_weight_is_nonzero
            # so a future revert is intentional.
            "health": 0.05,
        }

        # Normalize weights
        total_weight = sum(self.weights.values())
        if total_weight > 0:
            self.weights = {k: v / total_weight for k, v in self.weights.items()}

        self.cache: OrderedDict[str, tuple[list[ScoredProvider], float]] = OrderedDict()
        self.cache_size = cache_size
        self.cache_ttl = cache_ttl_seconds
        self._cache_lock = asyncio.Lock()

    def _cache_key(
        self,
        query: str | None,
        domain: str | None,
        region: str | None,
        explain: bool,
    ) -> str:
        """Generate cache key from query parameters."""
        parts = [query or "", domain or "", region or "", str(explain)]
        key_str = "|".join(parts).lower().strip()
        return hashlib.md5(key_str.encode()).hexdigest()

    async def route(
        self,
        query: str | None = None,
        domain: str | None = None,
        region: str | None = None,
        limit: int = 20,
        explain: bool = False,
    ) -> list[ScoredProvider]:
        """Route a query to matching providers.

        Applies hard filters (domain/region), then scores remaining providers,
        finally limits results.

        Args:
            query: Free-text search query
            domain: Filter by domain (hard filter)
            region: Filter by region (hard filter)
            limit: Maximum results to return
            explain: Include scoring breakdown in results

        Returns:
            List of ScoredProvider ranked by score.

        """
        cache_key = self._cache_key(query, domain, region, explain)

        async with self._cache_lock:
            if cache_key in self.cache:
                cached_results, cached_time = self.cache[cache_key]
                if time.monotonic() - cached_time < self.cache_ttl:
                    log.debug(f"Cache hit for key {cache_key}")
                    self.cache.move_to_end(cache_key)
                    return cached_results[:limit]
                del self.cache[cache_key]

        # Apply hard filters (outside lock — read-only registry access).
        # iter_registry() yields both the static REGISTRY and the in-memory
        # DYNAMIC_REGISTRY, so plugins hot-loaded via `opendata_plugins_create`
        # show up here without a server restart.
        filtered = [
            p for p in iter_registry() if self._passes_filters(p, domain, region)
        ]

        # Score each provider. ``has_relevance`` tracks whether any *non-health*
        # scorer produced a positive signal; this is the inclusion gate. Without
        # it the Phase-3 health-weight bump (0.0 → 0.05) would lift no-match
        # queries above zero (every provider gets a 1.0 health baseline, so
        # combined ≈ 0.05 * health_weight_normalized > 0). That regressed the
        # "find-providers returns 0 for nonsense queries" contract — pinned by
        # tests/providers/test_meta_data_mcp.py::test_find_providers_no_match_returns_empty.
        scored: list[ScoredProvider] = []
        for provider in filtered:
            if query and query.strip():
                score, breakdown, has_relevance = await self._combined_score(
                    query,
                    provider,
                    explain,
                )
            else:
                score = 1.0
                breakdown = dict.fromkeys(self.scorers, 0.0) if explain else None
                # No query means "list everything" — every provider is relevant.
                has_relevance = True
            if score > 0 and has_relevance:
                scored.append(
                    ScoredProvider(
                        entry=provider,
                        score=score,
                        breakdown=breakdown if explain else None,
                    ),
                )

        # Sort by score descending, then by id for stability
        scored.sort(key=lambda x: (-x.score, x.entry.id))

        async with self._cache_lock:
            if len(self.cache) >= self.cache_size:
                self.cache.popitem(last=False)
            self.cache[cache_key] = (scored, time.monotonic())

        return scored[:limit]

    def _passes_filters(
        self,
        provider: ProviderEntry,
        domain: str | None,
        region: str | None,
    ) -> bool:
        """Check if provider passes domain/region filters."""
        normalized_domain = _normalize_filter(domain)
        normalized_region = _normalize_filter(region)

        provider_domains = {item.lower().strip() for item in provider.domains}
        provider_regions = {item.lower().strip() for item in provider.regions}

        if normalized_domain and normalized_domain not in provider_domains:
            return False
        return not (normalized_region and normalized_region not in provider_regions)

    async def _combined_score(
        self,
        query: str | None,
        provider: ProviderEntry,
        explain: bool,
    ) -> tuple[float, dict[str, float] | None, bool]:
        """Compute weighted combined score across all strategies.

        Returns ``(score, breakdown, has_relevance)`` where ``has_relevance``
        is True iff at least one *non-health* scorer produced a positive
        score. Health is excluded from the relevance signal because its
        baseline for unrecorded providers is 1.0 — it carries no information
        about whether the query matches a provider.
        """
        breakdown = {} if explain else None
        combined = 0.0
        has_relevance = False

        for strategy_name, scorer in self.scorers.items():
            weight = self.weights.get(strategy_name, 0.0)
            # Skip zero-weight scorers entirely: they don't affect the combined
            # score and would add noise to the explain breakdown.
            if weight == 0.0:
                continue
            strategy_score = await scorer.score(query, provider)
            combined += strategy_score * weight

            if explain:
                breakdown[strategy_name] = strategy_score

            # Health is the baseline-positive scorer; don't let it forge
            # relevance. Every other scorer's positive output counts.
            if strategy_name != "health" and strategy_score > 0.0:
                has_relevance = True

        return combined, breakdown, has_relevance


# Backward-compatible wrapper
async def find_providers_sophisticated(
    query: str | None = None,
    domain: str | None = None,
    region: str | None = None,
    limit: int = 20,
    explain: bool = False,
) -> list[ProviderEntry]:
    """Route query using sophisticated engine and return ProviderEntry values."""
    engine = RoutingEngine()
    results = await engine.route(
        query=query,
        domain=domain,
        region=region,
        limit=limit,
        explain=explain,
    )
    # Return entries for backward compatibility
    return [r.entry for r in results]
