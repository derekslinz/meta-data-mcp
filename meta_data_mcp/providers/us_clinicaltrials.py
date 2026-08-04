"""ClinicalTrials.gov v2 API Provider

This module provides interfaces to access the U.S. National Library of
Medicine (NLM) / NIH ClinicalTrials.gov v2 REST API, which exposes the
public registry of clinical studies conducted around the world.

Source: https://clinicaltrials.gov/data-api/api
License: The information in ClinicalTrials.gov is provided by sponsors and
investigators and is in the public domain. See the ClinicalTrials.gov terms
and conditions for fair-use guidance:
https://clinicaltrials.gov/about-site/terms-conditions

Fair-use notes:
- The API does not require an API key.
- Use reasonable request rates; the registry recommends modest pageSize
  values and pagination via pageToken for large result sets.

Features:
- Free-text search across studies
- Detail retrieval by NCT identifier
- Search by condition, intervention, or location
- Overall registry size statistics

Usage:
    The module can be run directly to start a server handling API requests,
    or its components can be imported and used individually.
"""

import logging
from collections.abc import Sequence
from typing import Any

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI
from meta_data_mcp.utils import http_get, serialize_for_llm, to_records_text

# Initialize logging
log = logging.getLogger(__name__)

# Constants
PROVIDER_ID = "us-clinicaltrials"
BASE_URL = "https://clinicaltrials.gov/api/v2"

# Records-shape adapter constants
_MAX_SUMMARY_CHARS = 500

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


###################
# Search Studies
###################


class CtgovSearchStudiesParams(BaseModel):
    """Parameters for searching ClinicalTrials.gov studies by free-text term."""

    query_term: str | None = Field(
        None,
        description="Free-text search term (mapped to query.term)",
    )
    pageSize: int = Field(default=20, description="Number of studies per page")
    pageToken: str | None = Field(
        None,
        description="Pagination token returned by a previous response (nextPageToken)",
    )
    fields: str | None = Field(
        None,
        description="Comma-separated list of field paths to include (e.g. 'NCTId,BriefTitle')",
    )


def fetch_search_studies(params: CtgovSearchStudiesParams) -> dict:
    """Search ClinicalTrials.gov studies via /studies endpoint."""
    query_params: dict[str, Any] = {
        "pageSize": params.pageSize,
        "format": "json",
    }
    if params.query_term:
        query_params["query.term"] = params.query_term
    if params.pageToken:
        query_params["pageToken"] = params.pageToken
    if params.fields:
        query_params["fields"] = params.fields

    response = http_get(
        f"{BASE_URL}/studies",
        params=query_params,
        timeout=30.0,
        provider=PROVIDER_ID,
    )
    return response.json()


def _ctgov_search_to_shape_payload(data: dict) -> dict:
    """Adapt ClinicalTrials.gov v2 ``/studies`` response to the records
    shape primitive's payload.

    Each study nests data under ``protocolSection.{identificationModule,
    statusModule, designModule, descriptionModule, sponsorCollaboratorsModule,
    conditionsModule}``; we hoist the most-useful surface fields to top-level
    columns: nctId, briefTitle, overallStatus, studyType, phase, leadSponsor,
    primary condition, brief summary (truncated).
    """
    raw_rows = data.get("studies", []) if isinstance(data, dict) else []
    rows: list[dict[str, Any]] = []
    for study in raw_rows:
        if not isinstance(study, dict):
            continue
        ps = study.get("protocolSection") or {}
        if not isinstance(ps, dict):
            ps = {}
        ident = ps.get("identificationModule") or {}
        status = ps.get("statusModule") or {}
        design = ps.get("designModule") or {}
        desc = ps.get("descriptionModule") or {}
        sponsor = ps.get("sponsorCollaboratorsModule") or {}
        conditions = ps.get("conditionsModule") or {}
        phases = design.get("phases") or []
        phase_csv = (
            ", ".join(p for p in phases if isinstance(p, str))
            if isinstance(phases, list)
            else ""
        )
        condition_list = conditions.get("conditions") or []
        condition_csv = (
            ", ".join(c for c in condition_list if isinstance(c, str))
            if isinstance(condition_list, list)
            else ""
        )
        lead_sponsor = sponsor.get("leadSponsor") or {}
        lead_sponsor_name = (
            lead_sponsor.get("name") if isinstance(lead_sponsor, dict) else None
        )
        brief_summary = desc.get("briefSummary") or ""
        if isinstance(brief_summary, str) and len(brief_summary) > _MAX_SUMMARY_CHARS:
            brief_summary = brief_summary[:_MAX_SUMMARY_CHARS].rstrip() + "…"
        rows.append(
            {
                "nctId": ident.get("nctId"),
                "briefTitle": ident.get("briefTitle"),
                "overallStatus": status.get("overallStatus"),
                "studyType": design.get("studyType"),
                "phase": phase_csv,
                "leadSponsor": lead_sponsor_name,
                "conditions": condition_csv,
                "startDate": (status.get("startDateStruct") or {}).get("date"),
                "completionDate": (status.get("completionDateStruct") or {}).get(
                    "date",
                ),
                "briefSummary": brief_summary,
            },
        )
    payload: dict[str, Any] = {
        "rows": rows,
        "schema": {
            "columns": [
                {"name": "nctId", "type": "string", "description": "NCT identifier"},
                {"name": "briefTitle", "type": "string", "description": "Brief title"},
                {
                    "name": "overallStatus",
                    "type": "string",
                    "description": "Lifecycle status",
                },
                {
                    "name": "studyType",
                    "type": "string",
                    "description": "Study type (interventional, observational, etc.)",
                },
                {
                    "name": "phase",
                    "type": "string",
                    "description": "Trial phases (csv)",
                },
                {
                    "name": "leadSponsor",
                    "type": "string",
                    "description": "Lead sponsor",
                },
                {
                    "name": "conditions",
                    "type": "string",
                    "description": "Conditions (csv)",
                },
                {
                    "name": "startDate",
                    "type": "date",
                    "description": "Study start date",
                },
                {
                    "name": "completionDate",
                    "type": "date",
                    "description": "Study completion date",
                },
                {
                    "name": "briefSummary",
                    "type": "string",
                    "description": "Brief summary (truncated)",
                },
            ],
        },
        "default_facets": ["overallStatus", "studyType", "phase"],
    }
    if isinstance(data, dict) and "nextPageToken" in data:
        payload["nextPageToken"] = data["nextPageToken"]
    return payload


async def handle_search_studies(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the ctgov-search-studies tool call.

    Returns the response in the records shape primitive payload.
    """
    try:
        params = CtgovSearchStudiesParams(**(arguments or {}))
        data = fetch_search_studies(params)
        payload = _ctgov_search_to_shape_payload(data)
        return [types.TextContent(type="text", text=to_records_text(payload))]
    except Exception as e:
        log.error(f"Error searching ClinicalTrials.gov studies: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="ctgov-search-studies",
        description=(
            "Search ClinicalTrials.gov studies by free-text term. Supports "
            "pagination via pageToken and optional fields filtering."
        ),
        input_schema=CtgovSearchStudiesParams.model_json_schema(),
        _meta={"ui": {"resourceUri": RECORDS_URI}},
    ),
)
TOOLS_HANDLERS["ctgov-search-studies"] = handle_search_studies


###################
# Get Study
###################


class CtgovGetStudyParams(BaseModel):
    """Parameters for fetching a single ClinicalTrials.gov study by NCT ID."""

    nctId: str = Field(
        ...,
        description="NCT identifier of the study (e.g. 'NCT00000001')",
    )
    fields: str | None = Field(
        None,
        description="Comma-separated list of field paths to include",
    )


def fetch_get_study(params: CtgovGetStudyParams) -> dict:
    """Fetch a single study by NCT identifier."""
    query_params: dict[str, Any] = {"format": "json"}
    if params.fields:
        query_params["fields"] = params.fields

    response = http_get(
        f"{BASE_URL}/studies/{params.nctId}",
        params=query_params,
        timeout=30.0,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_get_study(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the ctgov-get-study tool call."""
    try:
        if not arguments or "nctId" not in arguments:
            raise ValueError("nctId is required")
        params = CtgovGetStudyParams(**arguments)
        data = fetch_get_study(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching ClinicalTrials.gov study: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="ctgov-get-study",
        description="Fetch the full record for a ClinicalTrials.gov study by NCT identifier (e.g. NCT00000001).",
        input_schema=CtgovGetStudyParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["ctgov-get-study"] = handle_get_study


###################
# Search by Condition
###################


class CtgovSearchByConditionParams(BaseModel):
    """Parameters for searching studies by condition / disease."""

    condition: str = Field(
        ...,
        description="Condition or disease name (e.g. 'diabetes')",
    )
    pageSize: int = Field(default=20, description="Number of studies per page")


def fetch_search_by_condition(params: CtgovSearchByConditionParams) -> dict:
    """Search studies by condition via query.cond."""
    query_params = {
        "query.cond": params.condition,
        "pageSize": params.pageSize,
        "format": "json",
    }
    response = http_get(
        f"{BASE_URL}/studies",
        params=query_params,
        timeout=30.0,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_search_by_condition(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the ctgov-search-by-condition tool call."""
    try:
        if not arguments or "condition" not in arguments:
            raise ValueError("condition is required")
        params = CtgovSearchByConditionParams(**arguments)
        data = fetch_search_by_condition(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error searching ClinicalTrials.gov by condition: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="ctgov-search-by-condition",
        description="Search ClinicalTrials.gov studies by condition / disease name.",
        input_schema=CtgovSearchByConditionParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["ctgov-search-by-condition"] = handle_search_by_condition


###################
# Search by Intervention
###################


class CtgovSearchByInterventionParams(BaseModel):
    """Parameters for searching studies by intervention / treatment."""

    intervention: str = Field(
        ...,
        description="Intervention or treatment name (e.g. 'metformin')",
    )
    pageSize: int = Field(default=20, description="Number of studies per page")


def fetch_search_by_intervention(
    params: CtgovSearchByInterventionParams,
) -> dict:
    """Search studies by intervention via query.intr."""
    query_params = {
        "query.intr": params.intervention,
        "pageSize": params.pageSize,
        "format": "json",
    }
    response = http_get(
        f"{BASE_URL}/studies",
        params=query_params,
        timeout=30.0,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_search_by_intervention(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the ctgov-search-by-intervention tool call."""
    try:
        if not arguments or "intervention" not in arguments:
            raise ValueError("intervention is required")
        params = CtgovSearchByInterventionParams(**arguments)
        data = fetch_search_by_intervention(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error searching ClinicalTrials.gov by intervention: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="ctgov-search-by-intervention",
        description="Search ClinicalTrials.gov studies by intervention / treatment.",
        input_schema=CtgovSearchByInterventionParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["ctgov-search-by-intervention"] = handle_search_by_intervention


###################
# Search by Location
###################


class CtgovSearchByLocationParams(BaseModel):
    """Parameters for searching studies by location."""

    location: str = Field(
        ...,
        description="Location term (city / state / country / facility); mapped to query.locn",
    )
    pageSize: int = Field(default=20, description="Number of studies per page")


def fetch_search_by_location(params: CtgovSearchByLocationParams) -> dict:
    """Search studies by location via query.locn."""
    query_params = {
        "query.locn": params.location,
        "pageSize": params.pageSize,
        "format": "json",
    }
    response = http_get(
        f"{BASE_URL}/studies",
        params=query_params,
        timeout=30.0,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_search_by_location(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the ctgov-search-by-location tool call."""
    try:
        if not arguments or "location" not in arguments:
            raise ValueError("location is required")
        params = CtgovSearchByLocationParams(**arguments)
        data = fetch_search_by_location(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error searching ClinicalTrials.gov by location: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="ctgov-search-by-location",
        description="Search ClinicalTrials.gov studies by location (city, state, country or facility).",
        input_schema=CtgovSearchByLocationParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["ctgov-search-by-location"] = handle_search_by_location


###################
# List Stats (Overall Size)
###################


class CtgovListStatsParams(BaseModel):
    """Parameters for fetching overall ClinicalTrials.gov registry statistics."""


def fetch_list_stats(_params: CtgovListStatsParams) -> dict:
    """Fetch overall registry size statistics via /stats/size."""
    response = http_get(f"{BASE_URL}/stats/size", timeout=30.0, provider=PROVIDER_ID)
    return response.json()


async def handle_list_stats(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the ctgov-list-stats tool call."""
    try:
        params = CtgovListStatsParams(**(arguments or {}))
        data = fetch_list_stats(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching ClinicalTrials.gov stats: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="ctgov-list-stats",
        description="Get overall ClinicalTrials.gov registry size statistics (/stats/size).",
        input_schema=CtgovListStatsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["ctgov-list-stats"] = handle_list_stats


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "us-clinicaltrials",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
