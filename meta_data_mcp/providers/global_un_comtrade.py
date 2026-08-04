"""global-un-comtrade provider.

UN Comtrade — the world's largest depository of international trade
statistics, reported by 200+ national statistical authorities to the UN
Statistics Division. Covers annual and monthly bilateral merchandise
trade by commodity (HS, SITC, BEC) and services (EBOPS) since 1962.

Homepage: https://comtradeplus.un.org/
API docs: https://uncomtrade.org/docs/api-getting-started/
License: UN Comtrade data is free for non-commercial use; cite "UN Comtrade".
Auth: free anonymous tier (capped at 500 records per call, 100 calls/hour).
Higher tiers require a subscription key via ``UN_COMTRADE_API_KEY`` env var
(sent as ``Ocp-Apim-Subscription-Key`` header).
"""

import logging
import os
from collections.abc import Sequence
from typing import Any

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.ui_resources.app_trade_flows_v1 import URI as TRADE_FLOWS_APP_URI
from meta_data_mcp.utils import (
    MAX_RESPONSE_CHARS,
    create_mcp_server,
    http_get,
    run_server,
    to_json_text,
)

log = logging.getLogger(__name__)

PROVIDER_ID = "global-un-comtrade"
# The "preview" host serves the free anonymous tier and the paid tier
# transparently; the only difference is the optional API key header.
BASE_URL = "https://comtradeapi.un.org/data/v1"

RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


def _auth_headers() -> dict[str, str]:
    """Send subscription key when UN_COMTRADE_API_KEY is set."""
    token = os.getenv("UN_COMTRADE_API_KEY")
    if token:
        return {"Ocp-Apim-Subscription-Key": token}
    return {}


###################
# comtrade-trade-data
###################


class ComtradeTradeDataParams(BaseModel):
    """Parameters for comtrade-trade-data."""

    type_code: str = Field(
        default="C",
        description=(
            "Trade type: 'C' = goods (default), 'S' = services. "
            "Required by the upstream API."
        ),
    )
    freq_code: str = Field(
        default="A",
        description="Frequency: 'A' = annual (default), 'M' = monthly.",
    )
    cl_code: str = Field(
        default="HS",
        description=(
            "Commodity classification: 'HS' (Harmonized System, default), "
            "'SITC', 'BEC', 'EB02' (services)."
        ),
    )
    period: str = Field(
        ...,
        min_length=4,
        description=(
            "Comma-separated periods. For annual: '2022,2023'. For monthly: "
            "'202301,202302'. Max 12 periods per call on the free tier."
        ),
    )
    reporter_code: str = Field(
        ...,
        min_length=1,
        description=(
            "Reporter country M49 numeric code(s), comma-separated. "
            "'all' is allowed but heavy. Examples: '840' = USA, '156' = China, "
            "'276' = Germany."
        ),
    )
    partner_code: str | None = Field(
        None,
        description=(
            "Partner country M49 code(s). '0' = World aggregate. Omit to use "
            "the API default."
        ),
    )
    cmd_code: str | None = Field(
        None,
        description=(
            "Commodity code(s) under the selected classification. For HS use "
            "2/4/6-digit codes (e.g. '10' for cereals, '1001' for wheat). "
            "Omit for top-level totals."
        ),
    )
    flow_code: str | None = Field(
        None,
        description=(
            "Trade flow: 'M' = imports, 'X' = exports, 'RX' = re-exports, "
            "'RM' = re-imports. Comma-separated to combine."
        ),
    )
    max_records: int = Field(
        default=500,
        ge=1,
        le=500,
        description="Records per call (free tier caps at 500).",
    )


def fetch_comtrade_trade_data(params: ComtradeTradeDataParams) -> Any:
    """Query UN Comtrade for bilateral trade rows.

    The Comtrade public-data API expects ``typeCode``, ``freqCode`` and
    ``clCode`` as URL path segments — e.g.
    ``/data/v1/get/C/A/HS?period=2022&reporterCode=840`` — not as query
    parameters. Sending them as query params returns HTTP 404.
    """
    query: dict[str, Any] = {
        "period": params.period,
        "reporterCode": params.reporter_code,
        "maxRecords": params.max_records,
    }
    if params.partner_code is not None:
        query["partnerCode"] = params.partner_code
    if params.cmd_code is not None:
        query["cmdCode"] = params.cmd_code
    if params.flow_code is not None:
        query["flowCode"] = params.flow_code
    response = http_get(
        f"{BASE_URL}/get/{params.type_code}/{params.freq_code}/{params.cl_code}",
        params=query,
        headers=_auth_headers() or None,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_comtrade_trade_data(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the comtrade-trade-data tool call."""
    params = ComtradeTradeDataParams(**(arguments or {}))
    data = fetch_comtrade_trade_data(params)
    return [
        types.TextContent(
            type="text",
            text=to_json_text(data, max_chars=MAX_RESPONSE_CHARS),
        ),
    ]


TOOLS.append(
    types.Tool(
        name="comtrade-trade-data",
        description=(
            "Query UN Comtrade for bilateral merchandise or services trade. "
            "Required: period(s), reporterCode (M49). Optional filters: "
            "partnerCode, cmdCode (HS/SITC/BEC), flowCode (M/X/RX/RM), "
            "typeCode (C goods / S services), freqCode (A annual / M monthly), "
            "clCode (classification). Free tier returns up to 500 rows per call."
        ),
        input_schema=ComtradeTradeDataParams.model_json_schema(),
        # MCP Apps binding: render via the trade-flows app (Sankey + treemap).
        _meta={"ui": {"resourceUri": TRADE_FLOWS_APP_URI}},
    ),
)
TOOLS_HANDLERS["comtrade-trade-data"] = handle_comtrade_trade_data


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    server = create_mcp_server(
        "global-un-comtrade",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )
    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
