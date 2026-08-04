import pkgutil

import meta_data_mcp.providers as providers_pkg
from meta_data_mcp.registry import (
    DOMAINS,
    REGIONS,
    REGISTRY,
    ProviderEntry,
    _check_registry_vocabulary,
    all_ids,
    find_providers,
    get_provider,
    list_domains,
    list_regions,
)


def test_registry_is_non_empty():
    assert len(REGISTRY) >= 50


def test_registry_ids_are_unique():
    ids = [entry.id for entry in REGISTRY]
    assert len(ids) == len(set(ids))


def test_registry_server_names_are_unique():
    names = [entry.server_name for entry in REGISTRY]
    assert len(names) == len(set(names))


def test_registry_uses_known_vocabulary():
    warnings = list(_check_registry_vocabulary())
    assert warnings == [], f"Registry has out-of-vocabulary terms: {warnings}"


def test_every_registry_id_matches_a_provider_module():
    discovered = {
        name
        for _, name, _ in pkgutil.iter_modules(providers_pkg.__path__)
        if not name.startswith("__")
    }
    missing = [entry.id for entry in REGISTRY if entry.id not in discovered]
    assert missing == [], f"Registry references missing provider modules: {missing}"


def test_every_provider_module_has_a_registry_entry():
    """Every provider module SHOULD have a registry entry. New providers
    must be added to the registry so they're discoverable via the meta
    aggregator.
    """
    discovered = {
        name
        for _, name, _ in pkgutil.iter_modules(providers_pkg.__path__)
        if not name.startswith("__")
        # The discovery layer lives in `meta_data_mcp` itself — it's not
        # a data plugin and must not appear in the registry.
        and name != "meta_data_mcp"
    }
    registry_ids = set(all_ids())
    orphaned = sorted(discovered - registry_ids)
    assert orphaned == [], (
        f"Provider modules missing from registry: {orphaned}. "
        "Add an entry to meta_data_mcp/registry.py REGISTRY tuple."
    )


def test_find_providers_query_match():
    results = find_providers(query="earthquake")
    assert any(entry.id == "us_usgs_earthquake" for entry in results)


def test_find_providers_keyword_bonus_outranks_general_match():
    """A keyword exact-match should rank higher than a substring hit elsewhere."""
    results = find_providers(query="fx")
    # Frankfurter and ECB both have 'fx' in keywords; they should be at the top.
    top_ids = {entry.id for entry in results[:3]}
    assert "global_frankfurter" in top_ids


def test_find_providers_domain_filter():
    results = find_providers(domain="health")
    assert all("health" in entry.domains for entry in results)


def test_find_providers_region_filter():
    results = find_providers(region="uk")
    assert all("uk" in entry.regions for entry in results)


def test_find_providers_unknown_query_returns_empty():
    assert find_providers(query="zzzzzz_unknown") == []


def test_find_providers_no_filters_returns_full_list_up_to_limit():
    results = find_providers(limit=200)
    assert len(results) == len(REGISTRY)


def test_get_provider_known():
    entry = get_provider("us_nasa")
    assert entry is not None
    assert entry.title.startswith("NASA")


def test_get_provider_unknown_returns_none():
    assert get_provider("not_a_provider") is None


def test_list_domains_is_subset_of_controlled_vocabulary():
    assert set(list_domains()).issubset(set(DOMAINS))


def test_list_regions_is_subset_of_controlled_vocabulary():
    assert set(list_regions()).issubset(set(REGIONS))


# ---------------------------------------------------------------------------
# Registry.remove() must keep _static_count consistent so dynamic() never
# silently drops newly added entries. Regression for PR #58 review comment
# discussion_r3249823414 — the original implementation updated _entries and
# _by_id but never touched _static_count, so removing a static then adding
# a dynamic produced an empty dynamic() result.
# ---------------------------------------------------------------------------


def _make_entry(eid: str, server_name: str | None = None) -> ProviderEntry:
    return ProviderEntry(
        id=eid,
        server_name=server_name or eid.replace("_", "-"),
        title=f"Test entry {eid}",
        description="Used only by the registry-isolation tests.",
        domains=("statistics",),
        regions=("global",),
        keywords=(eid,),
        homepage="https://example.invalid/",
    )


def test_remove_static_then_add_dynamic_remains_visible():
    """After removing a *static* entry, a subsequently added dynamic entry
    must still appear in ``dynamic()`` — i.e. ``_static_count`` was
    decremented so the slice frontier didn't lap the new entry.
    """
    snap = REGISTRY.snapshot()
    try:
        # Pick a real static id (alphabetically first) to remove.
        victim_id = next(iter(REGISTRY)).id
        assert REGISTRY.remove(victim_id) is True

        sentinel = _make_entry("_v2_phase0_sentinel")
        assert REGISTRY.add(sentinel) is True

        dyn = REGISTRY.dynamic()
        assert any(e.id == sentinel.id for e in dyn), (
            "Newly added dynamic entry disappeared from dynamic() — "
            "Registry.remove() failed to decrement _static_count."
        )
    finally:
        REGISTRY.restore(snap)


def test_remove_static_keeps_dynamic_slice_correct_with_existing_dynamics():
    """Same invariant when there's already a dynamic entry: removing a
    static must not shift the dynamic frontier so as to swallow the
    pre-existing dynamic.
    """
    snap = REGISTRY.snapshot()
    try:
        existing_dyn = _make_entry("_v2_phase0_pre_existing_dyn")
        REGISTRY.add(existing_dyn)
        assert any(e.id == existing_dyn.id for e in REGISTRY.dynamic())

        victim_id = next(iter(REGISTRY)).id
        REGISTRY.remove(victim_id)

        dyn_after = REGISTRY.dynamic()
        ids_after = {e.id for e in dyn_after}
        assert existing_dyn.id in ids_after, (
            "Pre-existing dynamic entry vanished from dynamic() after a "
            "static removal — _static_count drifted past it."
        )
    finally:
        REGISTRY.restore(snap)


def test_remove_dynamic_leaves_static_count_alone():
    """Removing a dynamic entry must NOT decrement _static_count."""
    snap = REGISTRY.snapshot()
    try:
        before = REGISTRY.snapshot()[2]  # _static_count
        sentinel = _make_entry("_v2_phase0_dyn_only")
        REGISTRY.add(sentinel)
        REGISTRY.remove(sentinel.id)
        after = REGISTRY.snapshot()[2]
        assert after == before, (
            f"Removing a dynamic entry shifted _static_count: {before} -> {after}"
        )
    finally:
        REGISTRY.restore(snap)
