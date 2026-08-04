import pkgutil

from meta_data_mcp import providers


def test_real_provider_discovery():
    """Verify that all new providers are actually discoverable by pkgutil."""
    discovered = [name for _, name, _ in pkgutil.iter_modules(providers.__path__)]

    expected_new_providers = [
        "eu_copernicus",
        "us_nasa",
        "de_db",
        "global_open_meteo",
        "global_dbnomics",
        "global_pubchem",
        "global_rcsb_pdb",
        "us_noaa_awc",
        "nl_rechtspraak",
    ]

    for provider in expected_new_providers:
        assert provider in discovered, (
            f"Provider {provider} not found in discovered modules"
        )


def test_provider_docs_present():
    """Verify that each new provider has a docstring for the CLI 'info' command."""
    import importlib

    expected_new_providers = [
        "eu_copernicus",
        "us_nasa",
        "de_db",
        "global_open_meteo",
        "global_dbnomics",
        "global_pubchem",
        "global_rcsb_pdb",
        "us_noaa_awc",
    ]

    for name in expected_new_providers:
        module = importlib.import_module(f"meta_data_mcp.providers.{name}")
        assert module.__doc__ is not None, f"Provider {name} is missing a docstring"
        assert len(module.__doc__.strip()) > 0
