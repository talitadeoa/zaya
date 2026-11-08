from __future__ import annotations

import json

import httpx

from zora.config import Config
from zora.opencode.client import OpenCodeClient
from zora.opencode.events import (
    assistant_text,
    event_session_id,
    event_type,
    iter_sse_json,
    message_error,
    text_delta,
    tool_activity,
    tool_diff,
)
from zora.opencode.models import ModelRef


def test_tool_diff_edit():
    part = {
        "type": "tool",
        "tool": "edit",
        "state": {
            "status": "completed",
            "input": {"filePath": "a.py", "oldString": "a\nb", "newString": "a\nc"},
        },
    }
    result = tool_diff(part)
    assert result is not None
    name, patch = result
    assert name == "a.py"
    assert "-b" in patch
    assert "+c" in patch


def test_tool_diff_metadata():
    part = {
        "type": "tool",
        "tool": "write",
        "state": {
            "status": "completed",
            "input": {"filePath": "x"},
            "metadata": {"diff": "--- a\n+++ b"},
        },
    }
    assert tool_diff(part) == ("x", "--- a\n+++ b")


def test_tool_diff_ignora_nao_ferramenta():
    assert tool_diff({"type": "text"}) is None


async def _lines():
    for line in [
        'data: {"type":"server.connected","properties":{}}',
        "",
        ": comentario",
        'data: {"type":"message.part.updated",',
        'data: "properties":{"sessionID":"ses_1"}}',
        "",
    ]:
        yield line


async def test_sse_parser_multiline():
    events = [event async for event in iter_sse_json(_lines())]
    assert len(events) == 2
    assert event_type(events[0]) == "server.connected"
    assert event_session_id(events[1]) == "ses_1"


def test_model_ref():
    model = ModelRef.parse("deepseek/deepseek-flash")
    assert model.slug == "deepseek/deepseek-flash"
    assert model.prompt_payload() == {"providerID": "deepseek", "modelID": "deepseek-flash"}
    assert model.create_payload() == {"id": "deepseek-flash", "providerID": "deepseek"}
    try:
        ModelRef.parse("sem-barra")
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("deveria falhar")


def test_text_delta_cumulative_and_fragment():
    delta, current = text_delta({"text": "Olá"}, "")
    assert (delta, current) == ("Olá", "Olá")
    delta, current = text_delta({"text": "Olá mundo"}, "Olá")
    assert delta == " mundo"
    delta, current = text_delta({"text": "novo"}, "texto antigo")
    assert delta == "novo"


def test_tool_activity_and_text_extraction():
    activity = tool_activity(
        {
            "type": "tool",
            "callID": "call_1",
            "tool": "bash",
            "state": {"status": "running", "title": "ls"},
        }
    )
    assert activity == ("call_1", "bash", "ls")

    message = {
        "info": {"role": "assistant"},
        "parts": [
            {"type": "text", "text": "Visível"},
            {"type": "text", "text": "sintético", "synthetic": True},
            {"type": "tool", "tool": "read"},
            {"type": "text", "text": "final"},
        ],
    }
    assert assistant_text(message) == "Visível\nfinal"
    assert message_error(
        {"info": {"error": {"name": "APIError", "data": {"message": "boom"}}}}
    ) == ("APIError: boom")


async def test_client_payloads_with_mock_transport(tmp_path, isolated_env):
    cfg = Config.load(
        config_path=isolated_env / "missing.toml", env={"ZORA_DATA_DIR": str(tmp_path / "data")}
    )
    cfg.opencode.model = "deepseek/deepseek-flash"
    client = OpenCodeClient(cfg, tmp_path / "proj")
    captured: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        if request.method == "POST" and request.url.path == "/session":
            captured["create"] = body
            return httpx.Response(200, json={"id": "ses_test", "title": body.get("title")})
        if request.method == "POST" and request.url.path == "/session/ses_test/prompt_async":
            captured["prompt"] = body
            return httpx.Response(204)
        if request.method == "GET" and request.url.path == "/session/ses_test/message":
            return httpx.Response(
                200,
                json=[
                    {"info": {"id": "msg_1", "role": "user"}, "parts": []},
                    {
                        "info": {"id": "msg_2", "role": "assistant"},
                        "parts": [{"type": "text", "text": "resposta real"}],
                    },
                ],
            )
        if request.method == "POST" and request.url.path == (
            "/api/session/ses_test/permission/per_v2/reply"
        ):
            captured["permission_v2"] = body
            return httpx.Response(200, json=True)
        if request.method == "POST" and request.url.path.endswith("/per_fallback/reply"):
            return httpx.Response(404, json={"error": "not found"})
        if request.method == "POST" and request.url.path.startswith(
            "/session/ses_test/permissions/"
        ):
            captured["permission"] = body
            return httpx.Response(200, json=True)
        return httpx.Response(404, json={"error": "not found"})

    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    )
    try:
        session = await client.create_session("teste", client.model)
        assert session.id == "ses_test"
        assert captured["create"]["model"] == {
            "id": "deepseek-flash",
            "providerID": "deepseek",
        }

        await client.prompt_async("ses_test", "olá")
        assert captured["prompt"]["parts"] == [{"type": "text", "text": "olá"}]
        assert captured["prompt"]["model"] == {
            "providerID": "deepseek",
            "modelID": "deepseek-flash",
        }
        assert captured["prompt"]["agent"] == "build"

        text = await client.latest_assistant_text("ses_test")
        assert text == "resposta real"

        await client.reply_permission("ses_test", "per_1", "once")
        assert captured["permission"] == {"response": "once"}

        await client.reply_permission("ses_test", "per_v2", "always", v2=True)
        assert captured["permission_v2"] == {"reply": "always"}

        await client.reply_permission("ses_test", "per_fallback", "reject", v2=True)
        assert captured["permission"] == {"response": "reject"}
    finally:
        await client.aclose()
