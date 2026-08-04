"""Pins the ``URIS`` aggregator against the actual register_* surfaces.

Architecture review §M1 (v2.1 hygiene): ``meta_data_mcp.ui_resources.URIS``
is a flat mapping of every ui:// resource the package ships. Three
parallel surfaces exist:

1. ``URIS`` — the declarative catalog (this PR added it).
2. ``register_shapes`` — returns the URIs it actually registered.
3. ``register_apps`` — same.

A half-wired addition (register_* updated but URIS forgotten, or vice
versa) leaves the catalog out of sync with the live resource handlers.
This test enforces three-way agreement so that drift fails CI rather
than going unnoticed.

The cost of the test is one boot of the registration surface against
empty in-memory collections. No I/O, no network.
"""

from __future__ import annotations

from meta_data_mcp.ui_resources import URIS, register_apps, register_shapes


def test_uris_matches_register_shapes_plus_apps() -> None:
    """URIS keys/values agree with the live register_* return values."""
    resources: list[object] = []
    handlers: dict[str, object] = {}
    registered: dict[str, str] = {}
    registered.update(
        {f"shape/{k}": v for k, v in register_shapes(resources, handlers).items()},  # type: ignore[arg-type]
    )
    registered.update(
        {f"app/{k}": v for k, v in register_apps(resources, handlers).items()},  # type: ignore[arg-type]
    )

    assert registered == URIS, (
        "ui_resources.URIS is out of sync with register_shapes + register_apps. "
        "Either the catalog or one of the register_* functions was updated "
        "without the other. Diff: "
        f"URIS-only={sorted(set(URIS) - set(registered))}, "
        f"register-only={sorted(set(registered) - set(URIS))}"
    )


def test_uris_scheme_and_namespace() -> None:
    """Every URI in the catalog uses the expected scheme and prefix."""
    for key, uri in URIS.items():
        assert uri.startswith("ui://meta-data-mcp/"), (
            f"URI for {key!r} does not use the ui://meta-data-mcp/ prefix: {uri!r}"
        )
        # The class/name/version segments in the key should reflect the URI's
        # path segments verbatim — keeping the catalog readable as a
        # one-line "what's there" overview.
        assert uri.endswith(f"/{key}"), (
            f"URI {uri!r} does not end with its catalog key {key!r}; "
            "the catalog will mislead anyone reading the keys"
        )


def test_uris_has_no_duplicate_values() -> None:
    """No two catalog entries point at the same URI."""
    values = list(URIS.values())
    assert len(values) == len(set(values)), (
        f"Duplicate URI in URIS catalog: {[u for u in values if values.count(u) > 1]}"
    )
