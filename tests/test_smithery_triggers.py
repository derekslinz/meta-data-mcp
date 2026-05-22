import pytest

from meta_data_mcp.smithery_triggers import SmitheryTriggersMiddleware


@pytest.mark.anyio
async def test_oversized_request_replays_buffered_prefix_without_draining(monkeypatch):
    events = [
        {"type": "http.request", "body": b"abcd", "more_body": True},
        {"type": "http.request", "body": b"ef", "more_body": False},
    ]
    state = {"receive_calls": 0, "calls_before_app_read": None}
    sent_events = []

    async def receive():
        index = state["receive_calls"]
        state["receive_calls"] += 1
        return events[index]

    async def send(event):
        sent_events.append(event)

    async def app(_scope, downstream_receive, downstream_send):
        state["calls_before_app_read"] = state["receive_calls"]
        assert await downstream_receive() == events[0]
        assert await downstream_receive() == events[1]
        await downstream_send(
            {"type": "http.response.start", "status": 200, "headers": []}
        )
        await downstream_send({"type": "http.response.body", "body": b""})

    middleware = SmitheryTriggersMiddleware(app)
    monkeypatch.setattr(middleware, "_MAX_INSPECT_BYTES", 4)

    await middleware({"type": "http", "method": "POST"}, receive, send)

    assert state["calls_before_app_read"] == 1
    assert [event["type"] for event in sent_events] == [
        "http.response.start",
        "http.response.body",
    ]


@pytest.mark.anyio
async def test_oversized_request_preserves_disconnect(monkeypatch):
    events = [
        {"type": "http.request", "body": b"abcd", "more_body": True},
        {"type": "http.disconnect"},
    ]
    state = {"receive_calls": 0}
    sent_events = []

    async def receive():
        index = state["receive_calls"]
        state["receive_calls"] += 1
        return events[index]

    async def send(event):
        sent_events.append(event)

    async def app(_scope, downstream_receive, _downstream_send):
        assert await downstream_receive() == events[0]
        assert await downstream_receive() == events[1]

    middleware = SmitheryTriggersMiddleware(app)
    monkeypatch.setattr(middleware, "_MAX_INSPECT_BYTES", 4)

    await middleware({"type": "http", "method": "POST"}, receive, send)

    assert sent_events == []
