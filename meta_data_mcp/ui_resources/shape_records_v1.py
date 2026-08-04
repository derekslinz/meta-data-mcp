"""``ui://meta-data-mcp/shape/records/v1`` — faceted-table shape primitive.

Dependency-free HTML + vanilla JS bundle. Renders tabular data with a
profile panel (per-column type, null rate, distinct count, top values),
faceted filter sidebar, free-text search, sortable columns, and 50-row
pagination.

Payload contract (the MCP Apps host pushes this into the iframe via
postMessage):

.. code-block:: json

    {
      "rows": [{"<column>": <any>, ...}, ...],
      "schema": {
        "columns": [
          {"name": "...", "type": "string|number|date|boolean",
           "description": "..."},
          ...
        ]
      },
      "default_facets": ["<column_name>", ...]
    }

``schema`` and ``default_facets`` are optional. When schema is absent,
the bundle infers per-column types (date → number → boolean → string).
When ``default_facets`` is absent, every string or boolean column with
cardinality ≤ 50 surfaces as a facet automatically.

The bundle is contrasted with timeseries (Plotly via CDN) and geofeatures
(Leaflet via CDN) on its zero-dependency stance — see
``tests/test_shape_records.py::test_bundle_has_no_external_script_sources``
for the regression that pins it.
"""

from importlib.resources import files

from meta_data_mcp.utils import register_ui_resource

URI = "ui://meta-data-mcp/shape/records/v1"

_HTML = (files("meta_data_mcp.ui_resources") / "shape_records_v1.html").read_text(
    encoding="utf-8",
)


def register(resources, resources_handlers) -> str:
    """Register the records primitive under its canonical URI.

    Returns the URI string for symmetry with ``register_ui_resource``,
    though the caller in :func:`meta_data_mcp.ui_resources.register_shapes`
    discards it.
    """
    return register_ui_resource(
        name="shape/records/v1",
        html=_HTML,
        description=(
            "Records shape primitive: faceted, sortable, paginated table "
            "with per-column profile panel. Dependency-free vanilla JS. "
            "Payload: {rows, schema?, default_facets?}."
        ),
        resources=resources,
        resources_handlers=resources_handlers,
    )
