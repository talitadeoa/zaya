from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from zaya.agent.errors import AgentError, ToolError
from zaya.agent.providers.anthropic import AnthropicProvider
from zaya.agent.providers.base import (
    ChatRequest,
    Finish,
    ProviderError,
    ReasoningDelta,
    TextDelta,
    ToolCallDelta,
)
from zaya.agent.providers.openai_compat import OpenAICompatibleProvider
from zaya.agent.runtime import AgentClient
from zaya.agent.tools import ToolContext, build_tools
from zaya.agent.tools.filesystem import edit, grep, read, write
from zaya.agent.types import ChatMessage, ModelRef, ToolCall
from zaya.config import Config


# --------------------------------------------------------------------- modelos
def test_model_ref_roundtrip():
    model = ModelRef.parse("deepseek/deepseek-flash")
    assert model.provider_id == "deepseek"
    assert model.model_id == "deepseek-flash"
    assert model.slug == "deepseek/deepseek-flash"
    with pytest.raises(ValueError):
        ModelRef.parse("sem-barra")


# ------------------------------------------------------------------ ferramentas
@pytest.fixture
def tool_ctx(tmp_path):
    return ToolContext(tmp_path, allow_outside_project=False)


def test_read_write_edit_gera_diff(tool_ctx):
    created = write({"path": "a.txt", "content": "um\ndois"}, tool_ctx)
    assert created.ok
    assert "+um" in created.diff

    assert read({"path": "a.txt"}, tool_ctx).content == "um\ndois"

    edited = edit(
        {"path": "a.txt", "old_string": "dois", "new_string": "três"}, tool_ctx
    )
    assert edited.ok
    assert "-dois" in edited.diff and "+três" in edited.diff
    assert read({"path": "a.txt"}, tool_ctx).content == "um\ntrês"


def test_edit_exige_trecho_unico(tool_ctx):
    write({"path": "a.txt", "content": "x x"}, tool_ctx)
    result = edit({"path": "a.txt", "old_string": "x", "new_string": "y"}, tool_ctx)
    assert not result.ok
    assert "2 vezes" in result.content

    result = edit(
        {"path": "a.txt", "old_string": "x", "new_string": "y", "replace_all": True},
        tool_ctx,
    )
    assert result.ok
    assert read({"path": "a.txt"}, tool_ctx).content == "y y"


def test_read_com_offset_e_limit(tool_ctx):
    write({"path": "a.txt", "content": "1\n2\n3\n4"}, tool_ctx)
    result = read({"path": "a.txt", "offset": 2, "limit": 2}, tool_ctx)
    assert "2\n3" in result.content
    assert "linhas 2-3" in result.content


def test_path_guard_bloqueia_fora_do_projeto(tool_ctx):
    with pytest.raises(ToolError, match="acesso negado"):
        read({"path": "/etc/hosts"}, tool_ctx)


def test_grep_e_glob(tool_ctx):
    write({"path": "src/a.py", "content": "def alvo():\n    pass"}, tool_ctx)
    write({"path": "src/b.md", "content": "alvo em texto"}, tool_ctx)

    found = grep({"pattern": r"def \w+", "path": ".", "glob": "*.py"}, tool_ctx)
    assert found.ok
    assert "src/a.py:1" in found.content
    assert "src/b.md" not in found.content

    globbed = glob_for(tool_ctx)
    assert "src/a.py" in globbed.content
    assert "src/b.md" in globbed.content


def glob_for(ctx):
    tool = build_tools(ctx).get("glob")
    assert tool is not None
    return tool.handler({"pattern": "**/*"}, ctx)


# -------------------------------------------------------------------- providers
def _sse(*payloads: str) -> bytes:
    return ("\n\n".join(f"data: {payload}" for payload in payloads) + "\n\n").encode()


async def test_openai_provider_stream_texto_e_tool_call():
    provider = OpenAICompatibleProvider(
        name="deepseek", base_url="http://fake", api_key="k"
    )
    body = _sse(
        json.dumps({"choices": [{"delta": {"content": "Olá"}}]}),
        json.dumps(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "function": {
                                        "name": "read",
                                        "arguments": '{"path": "a.txt"}',
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        ),
        json.dumps({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        "[DONE]",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["stream"] is True
        assert payload["model"] == "deepseek-flash"
        return httpx.Response(200, content=body)

    provider._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://fake",
        headers=provider._headers,
    )
    try:
        request = ChatRequest(model="deepseek-flash", messages=[ChatMessage("user", "oi")])
        deltas = [delta async for delta in provider.stream(request)]
    finally:
        await provider.aclose()

    assert any(isinstance(d, TextDelta) and d.text == "Olá" for d in deltas)
    calls = [d for d in deltas if isinstance(d, ToolCallDelta)]
    assert calls and calls[0].id == "call_1"
    assert calls[0].name == "read"
    assert calls[0].arguments == '{"path": "a.txt"}'
    assert any(isinstance(d, Finish) and d.reason == "tool_calls" for d in deltas)


async def test_openai_provider_erro_http_vira_provider_error():
    provider = OpenAICompatibleProvider(name="openai", base_url="http://fake")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"error": {"message": "invalid api key"}}
        )

    provider._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://fake",
        headers=provider._headers,
    )
    try:
        request = ChatRequest(model="m", messages=[ChatMessage("user", "oi")])
        with pytest.raises(ProviderError, match="invalid api key"):
            [delta async for delta in provider.stream(request)]
    finally:
        await provider.aclose()


async def test_anthropic_provider_stream():
    provider = AnthropicProvider(base_url="http://fake", api_key="k")
    events = [
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Olá"},
        },
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "tool_use", "id": "tu_1", "name": "read"},
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '{"path": "a"}'},
        },
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
        {"type": "message_stop"},
    ]
    body = ("\n\n".join(f"data: {json.dumps(e)}" for e in events) + "\n\n").encode()

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["model"] == "claude-teste"
        assert payload["max_tokens"] == 4096
        assert request.headers["x-api-key"] == "k"
        return httpx.Response(200, content=body)

    provider._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://fake",
        headers=provider._headers,
    )
    try:
        request = ChatRequest(model="claude-teste", messages=[ChatMessage("user", "oi")])
        deltas = [delta async for delta in provider.stream(request)]
    finally:
        await provider.aclose()

    assert any(isinstance(d, TextDelta) and d.text == "Olá" for d in deltas)
    calls = [d for d in deltas if isinstance(d, ToolCallDelta)]
    assert calls[0].id == "tu_1" and calls[0].name == "read"
    assert "".join(call.arguments for call in calls) == '{"path": "a"}'
    assert any(isinstance(d, Finish) and d.reason == "tool_use" for d in deltas)


# --------------------------------------------------------------------- runtime
class ScriptedProvider:
    name = "fake"
    base_url = "http://fake"

    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.requests: list[ChatRequest] = []

    async def stream(self, request: ChatRequest):
        self.requests.append(request)
        for item in self.scripts.pop(0) if self.scripts else []:
            yield item

    async def list_models(self):
        return ["fake-model"]

    async def ping(self):
        return {"provider": "fake"}

    async def aclose(self):
        return None


def _client(tmp_path, isolated_env) -> AgentClient:
    cfg = Config.load(
        config_path=isolated_env / "missing.toml",
        env={"ZAYA_DATA_DIR": str(tmp_path / "data")},
    )
    cfg.agent.tools = True
    cfg.agent.auto_approve = True
    return AgentClient(cfg, tmp_path / "proj")


async def test_runtime_executa_ferramenta_e_responde(tmp_path, isolated_env):
    client = _client(tmp_path, isolated_env)
    workspace = tmp_path / "proj"
    workspace.mkdir()
    (workspace / "a.txt").write_text("conteudo", encoding="utf-8")
    provider = ScriptedProvider(
        [
            [
                ToolCallDelta(0, "call_1", "read", '{"path": "a.txt"}'),
                Finish("tool_calls"),
            ],
            [TextDelta("li o arquivo"), Finish("stop")],
        ]
    )
    client._provider = provider
    session = await client.create_session("teste")
    try:
        await client.prompt_async(session.id, "leia a.txt")
        events = []
        while True:
            event = await asyncio.wait_for(client._events.get(), timeout=2)
            events.append(event)
            if type(event).__name__ in {"TurnCompleted", "TurnFailed"}:
                break

        assert provider.requests[0].model == "deepseek-flash"
        assert provider.requests[1].messages[-1].role == "tool"
        completed = events[-1]
        assert completed.text == "li o arquivo"
        assert any(type(e).__name__ == "ToolStarted" for e in events)
        assert any(type(e).__name__ == "ToolFinished" for e in events)
    finally:
        await client.aclose()


async def test_runtime_pede_permissao_para_escrita(tmp_path, isolated_env):
    client = _client(tmp_path, isolated_env)
    workspace = tmp_path / "proj"
    workspace.mkdir()
    client.cfg.agent.auto_approve = False
    provider = ScriptedProvider(
        [
            [
                ToolCallDelta(0, "call_1", "write", '{"path": "novo.txt", "content": "oi"}'),
                Finish("tool_calls"),
            ],
            [TextDelta("pronto"), Finish("stop")],
        ]
    )
    client._provider = provider
    session = await client.create_session("teste")

    async def consume():
        async for event in client.stream_events():
            if type(event).__name__ == "PermissionAsked":
                assert event.tool == "write"
                await client.reply_permission(session.id, event.request_id, "once")
            if type(event).__name__ in {"TurnCompleted", "TurnFailed"}:
                return event
        return None

    try:
        consumer = asyncio.create_task(consume())
        await client.prompt_async(session.id, "crie novo.txt")
        completed = await asyncio.wait_for(consumer, timeout=3)
        assert completed is not None and completed.text == "pronto"
        assert (workspace / "novo.txt").read_text(encoding="utf-8") == "oi"
    finally:
        await client.aclose()


async def test_runtime_nega_permissao_e_avisa_o_modelo(tmp_path, isolated_env):
    client = _client(tmp_path, isolated_env)
    (tmp_path / "proj").mkdir()
    client.cfg.agent.auto_approve = False
    provider = ScriptedProvider(
        [
            [ToolCallDelta(0, "call_1", "write", '{"path": "x", "content": "y"}')],
            [TextDelta("ok"), Finish("stop")],
        ]
    )
    client._provider = provider
    session = await client.create_session("teste")

    async def consume():
        async for event in client.stream_events():
            if type(event).__name__ == "PermissionAsked":
                await client.reply_permission(session.id, event.request_id, "reject")
            if type(event).__name__ == "TurnCompleted":
                return event
        return None

    try:
        consumer = asyncio.create_task(consume())
        await client.prompt_async(session.id, "crie x")
        await asyncio.wait_for(consumer, timeout=3)
        tool_message = provider.requests[1].messages[-1]
        assert tool_message.role == "tool"
        assert "negou" in tool_message.content
        assert not (tmp_path / "proj" / "x").exists()
    finally:
        await client.aclose()


async def test_prompt_sync_sem_ferramentas(tmp_path, isolated_env):
    client = _client(tmp_path, isolated_env)
    provider = ScriptedProvider(
        [
            [ToolCallDelta(0, "call_1", "bash", '{"command": "touch x"}'), Finish("tool_calls")],
            [TextDelta("resposta"), Finish("stop")],
        ]
    )
    client._provider = provider
    session = await client.create_session("teste")
    try:
        message = await client.prompt_sync(session.id, "resuma")
        assert message.text == "resposta"
        assert provider.requests[0].tools == []
        tool_message = provider.requests[1].messages[-1]
        assert tool_message.role == "tool"
        assert "indisponivel" in tool_message.content
    finally:
        await client.aclose()


async def test_sessao_persiste_entre_clientes(tmp_path, isolated_env):
    client = _client(tmp_path, isolated_env)
    client._provider = ScriptedProvider([[TextDelta("oi"), Finish("stop")]])
    session = await client.create_session("persistente")
    await client.prompt_sync(session.id, "olá")
    await client.aclose()

    outro = _client(tmp_path, isolated_env)
    try:
        loaded = await outro.get_session(session.id)
        assert loaded is not None
        assert loaded.title == "persistente"
        assert (await outro.latest_assistant_text(session.id)) == "oi"
        assert (await outro.session_status(session.id))["type"] == "idle"
    finally:
        await outro.aclose()


async def test_abort_derruba_turno(tmp_path, isolated_env):
    client = _client(tmp_path, isolated_env)

    class SlowProvider(ScriptedProvider):
        async def stream(self, request):
            self.requests.append(request)
            yield TextDelta("começando")
            await asyncio.sleep(10)
            yield TextDelta("fim")

    client._provider = SlowProvider([[]])
    session = await client.create_session("teste")
    try:
        await client.prompt_async(session.id, "demora")
        await asyncio.sleep(0.05)
        await client.abort(session.id)
        await asyncio.sleep(0.1)
        assert (await client.session_status(session.id))["type"] == "idle"
    finally:
        await client.aclose()


def test_chat_message_roundtrip():
    message = ChatMessage(
        role="assistant",
        content="vou ler",
        tool_calls=[ToolCall("c1", "read", {"path": "a"})],
    )
    restored = ChatMessage.from_dict(message.to_dict())
    assert restored.tool_calls[0].name == "read"
    assert restored.tool_calls[0].arguments == {"path": "a"}


def test_config_exige_chave_para_provedor_remoto(tmp_path, isolated_env, monkeypatch):
    monkeypatch.delenv("ZAYA_AGENT_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    cfg = Config.load(
        config_path=isolated_env / "missing.toml",
        env={"ZAYA_DATA_DIR": str(tmp_path / "data")},
    )
    client = AgentClient(cfg, tmp_path / "proj")
    with pytest.raises(AgentError, match="chave de API"):
        client._ensure_provider()


def test_provider_local_nao_exige_chave(tmp_path, isolated_env):
    cfg = Config.load(
        config_path=isolated_env / "missing.toml",
        env={"ZAYA_DATA_DIR": str(tmp_path / "data")},
    )
    cfg.agent.provider = "ollama"
    client = AgentClient(cfg, tmp_path / "proj")
    assert client._ensure_provider().name == "ollama"


def test_health_usa_ping_do_provider(tmp_path, isolated_env):
    client = _client(tmp_path, isolated_env)
    client._provider = ScriptedProvider([])

    async def run():
        return await client.health()

    assert asyncio.run(run()) == {
        "provider": "deepseek",
        "model": "deepseek-flash",
        "models": None,
    }


def test_agent_tool_set_completo():
    ctx = ToolContext(Path(".").resolve())
    tools = build_tools(ctx)
    assert set(tools.tools) == {"read", "write", "edit", "list", "glob", "grep", "bash"}
    assert tools.get("write").requires_approval is True
    assert tools.get("bash").requires_approval is True
    assert tools.get("read").requires_approval is False


def test_reasoning_delta_tipo_existe():
    delta = ReasoningDelta("pensando")
    assert delta.text == "pensando"
