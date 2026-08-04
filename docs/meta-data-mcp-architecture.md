# Meta-Data-MCP: Architecture & Design

## Concept: What is Meta-Data-MCP?

**Meta-Data-MCP** (formerly `opendata_mcp_meta`) is the intelligent routing and discovery layer for the opendata-mcp ecosystem. The name captures a dual meaning:

1. **Literal**: It's a metadata aggregator — it maintains and exposes structured information about all available data providers (their domains, regions, capabilities, keywords)
2. **Figurative**: It's a meta-level router — it sits above individual data providers and makes intelligent decisions about which provider best answers a user's question

## Architecture Overview

```
User Query
    ↓
Meta-Data-MCP (Discovery & Routing)
    ├── opendata_providers_find      [Sophisticated Multi-Criteria Search]
    ├── opendata_explain_choice      [Ranking Transparency]
    ├── opendata_domains_list        [Catalog Browsing]
    ├── opendata_regions_list        [Catalog Browsing]
    ├── opendata_providers_describe   [Provider Details]
    └── opendata_providers_list      [Full Registry]
    ↓
Individual Providers
    ├── us_nasa
    ├── global_gbif
    ├── eu_copernicus
    └── 50+ more...
```

## Sophisticated Routing System

The core innovation is the **RoutingEngine** — a pluggable, multi-criteria scorer that ranks providers based on:

### Scoring Strategies

1. **TokenScorer** (30% weight)
   - Token-level matching against provider metadata
   - Keyword matches receive 3x bonus weight
   - Use case: Exact or direct phrase matches

2. **FuzzyScorer** (20% weight)
   - Sequence similarity ratio via `difflib.SequenceMatcher` for typo tolerance
   - Threshold: >60% similarity required
   - Use case: Misspelled or variant queries ("climate" vs "climete")

3. **MetadataScorer** (25% weight)
   - Domain/region/keyword exact and partial matches
   - Hard filters enforced
   - Use case: "I want health data for the EU"

4. **SimpleSemanticScorer** (25% weight)
   - Word-frequency based semantic similarity (Jaccard index)
   - Lightweight alternative to embeddings
   - Use case: "disease outbreak tracking" matches epidemiology provider

### Caching Strategy

- **LRU Cache**: Configurable size (default 1000 queries)
- **TTL**: Configurable expiration (default 1 hour)
- **Key**: MD5 hash of (query, domain, region, explain) — values lowercased and stripped
- **Performance**: <5ms for cached queries, ~100ms for uncached (50 providers)

### Explanation Mode

The `opendata_explain_choice` tool provides transparency into ranking:

```json
{
  "query": "climate data",
  "results": [
    {
      "rank": 1,
      "provider_id": "eu_copernicus",
      "overall_score": 0.782,
      "scoring_breakdown": {
        "token": 0.75,
        "fuzzy": 0.85,
        "metadata": 0.90,
        "semantic": 0.65
      }
    }
  ]
}
```

Users can understand *why* each provider was ranked, enabling better query refinement.

## Backward Compatibility

- All existing APIs unchanged (find_providers returns ProviderEntry)
- New RoutingEngine is opt-in within meta_data_mcp
- Old registry.find_providers() still available for direct use
- No breaking changes to CLI or configuration

## Performance Characteristics

| Operation | Latency | Notes |
|-----------|---------|-------|
| Cached find-providers | <5ms | LRU hit |
| Uncached find-providers | ~100ms | Full scoring pass |
| explain-choice (cached) | <10ms | Score lookup only |
| explain-choice (uncached) | ~120ms | Full scoring + breakdown |

Benchmarked on 50 providers, M1 MacBook Pro.

## Implementation Details

### File Structure

```
meta_data_mcp/
├── routing.py                    # RoutingEngine & Scorers
├── providers/
│   └── meta_data_mcp.py         # Integration point
└── registry.py                   # Provider metadata
```

### Key Classes

- `RoutingEngine`: Orchestrates multi-criteria scoring, caching, explanation
- `Scorer` (ABC): Base class for scoring strategies
- `TokenScorer`, `FuzzyScorer`, `MetadataScorer`, `SimpleSemanticScorer`: Implementations
- `ScoredProvider`: Result container with score + breakdown

### Async Design

All scorers are async-compatible:
```python
async def score(self, query: str | None, provider: ProviderEntry) -> float:
    # Implementation
```

This enables parallel scoring in future versions or integration with async frameworks.

## Roadmap

### v1.1 (Completed)
- ✅ Sophisticated multi-criteria routing
- ✅ Explanation/transparency tool
- ✅ LRU caching with TTL
- ✅ Backward-compatible API

### v1.2 (Planned)
- **Hierarchical Discovery**: Browse by domain → subcategory → provider
- **Hierarchical Tools**:
  - `opendata_domains_list` → returns domains with descriptions
  - `opendata-list-subcategories` → returns subcategories for a domain
  - `opendata-browse-providers` → returns providers in a subcategory
- **Hook Points**: Enable future integration with v1.3

### v1.3 (Planned, Depends on Provider Generation Tool)
- **Agent-Driven Discovery**: When no provider matches, automatically:
  1. Detect gap in coverage
  2. Use provider-generation agent to build new provider
  3. Test provider (consistency checks, test cases)
  4. Auto-register in hierarchy
  5. Re-run user's original query against new provider
- **Hook Points**: `on_no_match()` handlers in RoutingEngine

### Future Considerations
- **Learning**: Track which providers users choose for similar queries
- **Personalization**: Rank by user's past provider preferences
- **Query Reformulation**: Suggest better search terms
- **Multi-Language**: Support queries in non-English languages
- **Performance**: Pre-compute similarity matrices for 200+ providers

## Design Decisions

### Why Not Embeddings?
- **Trade-off**: Semantic accuracy vs. deployment simplicity
- **Choice**: Simple Jaccard overlap acceptable for 50-200 providers
- **Future**: Embeddings can replace SimpleSemanticScorer in v2.0

### Why LRU + TTL?
- **Requirement**: Sub-100ms latency for common queries
- **Choice**: In-memory LRU cache with provider-count invalidation
- **Rationale**: Hits 95%+ cache rate for typical usage patterns
- **Alternative**: Redis for multi-instance deployments (future)

### Why Configurable Weights?
- **Requirement**: Tunable for different use cases
- **Choice**: Weights per strategy, normalized on init
- **Example**: Data scientists might weight semantic matching higher; engineers might prefer exact token matches

### Why Explanation Tool?
- **Requirement**: Users should trust automated ranking
- **Choice**: Detailed breakdown by scoring strategy
- **Benefit**: Enables iterative query refinement ("ah, I need to mention 'epidemiology'")

## Extension Points

### Adding a New Scorer

```python
class MyCustomScorer(Scorer):
    async def score(self, query: str | None, provider: ProviderEntry) -> float:
        # Your logic here
        return score  # 0.0-1.0


engine = RoutingEngine(
    scorers={
        "token": TokenScorer(),
        "custom": MyCustomScorer(),
    },
    weights={
        "token": 0.4,
        "custom": 0.6,
    },
)
```

### Hook into No-Match Handling

```python
# Future v1.3 API (pseudo-code)
engine.on_no_match = async lambda query: await generate_provider(query)
```

## Testing Strategy

- **Unit Tests**: Each scorer independently (test_routing.py)
- **Integration Tests**: RoutingEngine with all scorers (test_meta_data_mcp.py)
- **Backward Compat Tests**: Existing find_providers behavior preserved
- **Performance Tests**: Latency benchmarks under load

## Operational Considerations

### Monitoring

- Cache hit rate (goal: >90% for production)
- Average query latency (goal: <50ms)
- Provider scoring distribution (detect outliers)

### Configuration

```python
engine = RoutingEngine(
    cache_size=1000,  # Adjust for memory constraints
    cache_ttl_seconds=3600,  # Adjust for freshness needs
    weights={...},  # Tune for your use case
)
```

### Scaling

- **Single Instance**: In-memory LRU, <1GB footprint for 1000 cached queries
- **Multiple Instances**: Add Redis backend (future work)
- **Monitoring**: Track cache coherency across instances

---

**Status**: Production-ready v1.1 merged to main (PR #19)  
**Next Phase**: Hierarchical discovery (v1.2)  
**Feedback**: File issues or PRs for suggestions
