from __future__ import annotations

import asyncio
import json
from datetime import datetime

import httpx

from zaya.agent.providers.anthropic import AnthropicProvider
from zaya.agent.providers.base import (
    ChatRequest,
    Finish,
    TextDelta,
    ToolCallDelta,
    Usage,
    UsageDelta,
)
from zaya.agent.providers.openai_compat import OpenAICompatibleProvider
from zaya.agent.runtime import AgentClient
from zaya.agent.session import AgentSession
from zaya.agent.tools import ToolContext
from zaya.agent.tools.filesystem import read
from zaya.agent.types import ChatMessage, ModelRef, ToolCall
from zaya.config import Config
from zaya.controller.intents import format_context, needs_second_brain_context
from zaya.controller.modes import PERSONAS
from zaya.controller.prompt import (
    clean_preferences,
    compose_system,
    initial_tools,
    route_model,
    tools_for_turn,
)


# --------------------------------------------------------------------- prompt
def test_prefixo_estatico_nao_muda_com_a_hora():
    persona = PERSONAS["assistente"]
    manha = compose_system(
        persona, "Prefiro respostas curtas.", now=datetime(2026, 11, 11, 9, 0)
    )
    tarde = compose_system(
        persona, "Prefiro respostas curtas.", now=datetime(2026, 11, 11, 15, 30)
    )

    assert manha.static == tarde.static
    assert manha.dynamic != tarde.dynamic
    assert "Agora" in manha.dynamic
    assert "Agora" not in manha.static
    assert manha.text.startswith(manha.static)
    assert manha.text.endswith(manha.dynamic)


def test_preferencia_que_repete_a_persona_e_removida():
    persona = PERSONAS["trabalhadora"]
    prefs = (
        f"- {persona.directive}\n"
        "- Prefiro respostas curtas.\n"
        "## Aprendido com o tempo\n"
        "- prefiro respostas curtas.\n"
    )

    cleaned = clean_preferences(prefs, persona.directive)
    assert "respostas curtas" in cleaned
    assert persona.directive not in cleaned
    assert cleaned.lower().count("respostas curtas") == 1


def test_diretriz_concisa_entra_no_prefixo_e_pedido_curto_no_fim():
    composed = compose_system(
        PERSONAS["assistente"],
        concise=True,
        short_prompt=True,
        fast=True,
        now=datetime(2026, 11, 11, 9, 0),
    )
    assert "seja conciso" in composed.static
    assert "Pedido curto" in composed.dynamic
    assert "Modo rápido" in composed.dynamic


# ----------------------------------------------------------------- ferramentas
def test_ferramentas_do_turno_so_incluem_o_necessario():
    leitura = set(tools_for_turn("assistente", "qual a capital da França?"))
    assert leitura == {"read", "list", "glob", "grep"}
    assert "bash" not in leitura

    acao = set(tools_for_turn("assistente", "edite o README e rode os testes"))
    assert {"write", "edit", "bash"} <= acao

    pesquisa = set(tools_for_turn("pesquisadora", "resuma este texto"))
    assert {"write", "edit"} <= pesquisa
    assert "bash" not in pesquisa

    assert tools_for_turn("assistente", "oi", enabled=False) is None


def test_catalogo_inicial_por_modo():
    assert initial_tools("assistente") == {"read", "list", "glob", "grep"}
    assert initial_tools("investidora") == {"read", "list", "glob", "grep"}
    assert initial_tools("pesquisadora") == {
        "read",
        "list",
        "glob",
        "grep",
        "write",
        "edit",
    }


def test_roteamento_para_modelo_pequeno():
    assert (
        route_model("me lembre de beber água", "deepseek/deepseek-chat", "deepseek")
        == ModelRef("deepseek", "deepseek-chat")
    )
    assert route_model("me lembre", "tiny", "deepseek") == ModelRef("deepseek", "tiny")
    assert route_model("pesquise sobre currículo", "tiny", "deepseek") is None
    assert route_model("monte uma agenda de estudos", "tiny", "deepseek") is None
    assert route_model("resuma o texto", "tiny", "deepseek") is None
    assert route_model("qualquer coisa", "", "deepseek") is None
    assert route_model("palavra " * 20, "tiny", "deepseek") is None


# -------------------------------------------------------------------- contexto
def test_contexto_so_busca_quando_ha_pista_de_memoria():
    assert needs_second_brain_context("continue aquele projeto de automação")
    assert needs_second_brain_context("qual o status do projeto de automação")
    assert not needs_second_brain_context("preciso emitir uma nota fiscal")
    assert not needs_second_brain_context("escreva uma função fibonacci")


def test_contexto_respeita_teto_total_e_numero_de_trechos():
    class Hit:
        def as_context(self, max_chars=480):
            return "- **Nota**: " + ("x" * max_chars)

    text = format_context(
        [Hit() for _ in range(6)],
        max_chars=100,
        max_total_chars=250,
        max_items=3,
    )
    assert "Nota" in text
    assert text.count("x") <= 250


# ------------------------------------------------------------------ historico
def test_compactacao_troca_o_inicio_por_resumo():
    session = AgentSession("ses")
    for index in range(10):
        session.messages.append(ChatMessage("user", f"pergunta {index}"))
        session.messages.append(ChatMessage("assistant", f"resposta {index}"))

    assert session.compact(max_messages=6, summary_chars=400)

    assert len(session.messages) == 6
    assert session.messages[0].role == "user"
    assert session.messages[0].content == "pergunta 7"
    assert "pergunta 0" in session.summary
    assert "resposta 6" in session.summary


def test_compactacao_nao_deixa_resultado_de_ferramenta_orfao():
    session = AgentSession("ses")
    session.messages = [
        ChatMessage("user", "leia a.txt"),
        ChatMessage(
            "assistant",
            content="",
            tool_calls=[ToolCall("c1", "read", {"path": "a.txt"})],
        ),
        ChatMessage("tool", "conteudo", tool_call_id="c1"),
        ChatMessage("assistant", "li o arquivo"),
        ChatMessage("user", "obrigada"),
        ChatMessage("assistant", "de nada"),
    ]

    assert session.compact(max_messages=3, summary_chars=400)

    assert session.messages[0].role == "user"
    assert session.messages[0].content == "obrigada"
    assert all(message.role != "tool" for message in session.messages)


# ------------------------------------------------------------------- provedor
async def test_anthropic_marca_o_prefixo_estavel_para_cache():
    provider = AnthropicProvider(base_url="http://fake", api_key="k")
    request = ChatRequest(
        model="claude-teste",
        messages=[ChatMessage("user", "oi")],
        system="PERSONA E PREFERENCIAS\n\n---\n\n[Agora: hoje]",
        cache_prefix="PERSONA E PREFERENCIAS",
    )
    try:
        body = provider._body(request)
    finally:
        await provider.aclose()

    assert isinstance(body["system"], list)
    assert body["system"][0]["text"] == "PERSONA E PREFERENCIAS"
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "[Agora: hoje]" in body["system"][1]["text"]


async def test_anthropic_cacheia_o_prefixo_da_conversa():
    provider = AnthropicProvider(base_url="http://fake", api_key="k")
    request = ChatRequest(
        model="m",
        messages=[
            ChatMessage("user", "primeira"),
            ChatMessage("assistant", "resposta"),
            ChatMessage("user", "segunda"),
        ],
        cache_messages=True,
    )
    try:
        body = provider._body(request)
    finally:
        await provider.aclose()

    last = body["messages"][-1]["content"][-1]
    assert last["cache_control"] == {"type": "ephemeral"}


async def test_anthropic_extrai_uso_e_cache():
    provider = AnthropicProvider(base_url="http://fake", api_key="k")
    events = [
        {
            "type": "message_start",
            "message": {
                "usage": {
                    "input_tokens": 100,
                    "cache_read_input_tokens": 40,
                    "cache_creation_input_tokens": 10,
                    "output_tokens": 1,
                }
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "ok"},
        },
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 20},
        },
        {"type": "message_stop"},
    ]
    body = ("\n\n".join(f"data: {json.dumps(e)}" for e in events) + "\n\n").encode()

    provider._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body)),
        base_url="http://fake",
        headers=provider._headers,
    )
    try:
        deltas = [
            delta
            async for delta in provider.stream(
                ChatRequest(model="m", messages=[ChatMessage("user", "oi")])
            )
        ]
    finally:
        await provider.aclose()

    usage = next(d.usage for d in deltas if isinstance(d, UsageDelta))
    assert usage.input_tokens == 150  # 100 + 40 de cache + 10 de escrita
    assert usage.cached_tokens == 40
    assert usage.cache_write_tokens == 10
    assert usage.output_tokens == 20
    assert usage.total_tokens == 170


def _sse(*payloads: str) -> bytes:
    return ("\n\n".join(f"data: {payload}" for payload in payloads) + "\n\n").encode()


async def test_openai_pede_e_extrai_uso():
    provider = OpenAICompatibleProvider(name="deepseek", base_url="http://fake")
    payloads = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            content=_sse(
                json.dumps({"choices": [{"delta": {"content": "oi"}}]}),
                json.dumps(
                    {
                        "choices": [],
                        "usage": {
                            "prompt_tokens": 100,
                            "completion_tokens": 20,
                            "total_tokens": 120,
                            "prompt_tokens_details": {"cached_tokens": 30},
                        },
                    }
                ),
                "[DONE]",
            ),
        )

    provider._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://fake",
        headers=provider._headers,
    )
    try:
        deltas = [
            delta
            async for delta in provider.stream(
                ChatRequest(model="m", messages=[ChatMessage("user", "oi")])
            )
        ]
    finally:
        await provider.aclose()

    assert payloads[0]["stream_options"] == {"include_usage": True}
    usage = next(d.usage for d in deltas if isinstance(d, UsageDelta))
    assert usage.input_tokens == 100
    assert usage.output_tokens == 20
    assert usage.cached_tokens == 30
    assert usage.total_tokens == 120


async def test_openai_repete_sem_usage_se_endpoint_recusar():
    provider = OpenAICompatibleProvider(name="custom", base_url="http://fake")
    payloads = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        payloads.append(payload)
        if "stream_options" in payload:
            return httpx.Response(
                400,
                json={"error": {"message": "unknown field: stream_options"}},
            )
        return httpx.Response(
            200,
            content=_sse(
                json.dumps({"choices": [{"delta": {"content": "ok"}}]}),
                "[DONE]",
            ),
        )

    provider._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://fake",
        headers=provider._headers,
    )
    try:
        deltas = [
            delta
            async for delta in provider.stream(
                ChatRequest(model="m", messages=[ChatMessage("user", "oi")])
            )
        ]
    finally:
        await provider.aclose()

    assert payloads[0]["stream_options"] == {"include_usage": True}
    assert "stream_options" not in payloads[1]
    assert any(isinstance(d, TextDelta) and d.text == "ok" for d in deltas)


# --------------------------------------------------------------------- leitura
def test_read_grande_sem_limit_mostra_so_o_inicio(tmp_path):
    path = tmp_path / "grande.txt"
    path.write_text("\n".join(f"linha {i}" for i in range(1, 801)), encoding="utf-8")

    result = read({"path": "grande.txt"}, ToolContext(tmp_path))

    assert result.ok
    assert "linhas 1-400 de 800" in result.content
    assert "linha 400" in result.content
    assert "linha 401" not in result.content


# --------------------------------------------------------------------- runtime
class ScriptedProvider:
    name = "fake"
    base_url = "http://fake"

    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.requests: list[ChatRequest] = []
        self.snapshots: list[list[tuple[str, str]]] = []

    async def stream(self, request: ChatRequest):
        self.requests.append(request)
        self.snapshots.append([(m.role, m.content) for m in request.messages])
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


async def test_runtime_enxuga_argumentos_de_tool_call(tmp_path, isolated_env):
    client = _client(tmp_path, isolated_env)
    client.cfg.agent.tool_result_chars = 100
    provider = ScriptedProvider([[TextDelta("ok"), Finish("stop")]])
    client._provider = provider
    info = await client.create_session("teste")
    session = client._sessions[info.id]
    grande = "y" * 5000
    session.messages.append(ChatMessage("user", "crie o arquivo"))
    session.messages.append(
        ChatMessage(
            "assistant",
            content="",
            tool_calls=[
                ToolCall("c1", "write", {"path": "a.txt", "content": grande})
            ],
        )
    )
    session.messages.append(
        ChatMessage("tool", "criado", tool_call_id="c1")
    )

    try:
        await client.prompt_sync(info.id, "ok")

        stored = next(
            message
            for message in client._sessions[info.id].messages
            if message.role == "assistant" and message.tool_calls
        )
        arguments = stored.tool_calls[0].arguments
        assert arguments["path"] == "a.txt"
        assert len(arguments["content"]) < 1000
        assert "truncado no histórico" in arguments["content"]
    finally:
        await client.aclose()


async def test_runtime_compacta_por_tamanho_do_historico(tmp_path, isolated_env):
    client = _client(tmp_path, isolated_env)
    client.cfg.agent.max_history_messages = 20
    client.cfg.agent.max_history_chars = 500
    client.cfg.agent.summary_chars = 5000
    client.cfg.agent.history_summary = "local"
    provider = ScriptedProvider([[TextDelta("ok"), Finish("stop")]])
    client._provider = provider
    info = await client.create_session("teste")
    session = client._sessions[info.id]
    for index in range(4):
        session.messages.append(
            ChatMessage("user", f"mensagem {index} " + ("x" * 150))
        )
        session.messages.append(ChatMessage("assistant", "resposta " + ("z" * 150)))

    try:
        await client.prompt_sync(info.id, "continue")

        assert "mensagem 0" in session.summary
        assert len(session.messages) <= 4
    finally:
        await client.aclose()


async def test_runtime_resumo_via_llm_e_semantico(tmp_path, isolated_env):
    client = _client(tmp_path, isolated_env)
    client.cfg.agent.max_history_messages = 4
    client.cfg.agent.history_summary = "llm"
    provider = ScriptedProvider(
        [
            [
                UsageDelta(Usage(input_tokens=80, output_tokens=20, total_tokens=100)),
                TextDelta("Decisão: usar o plano X. Pendência: revisar o CV."),
                Finish("stop"),
            ],
            [
                UsageDelta(Usage(input_tokens=50, output_tokens=5, total_tokens=55)),
                TextDelta("ok"),
                Finish("stop"),
            ],
        ]
    )
    client._provider = provider
    info = await client.create_session("teste")
    session = client._sessions[info.id]
    for index in range(6):
        session.messages.append(ChatMessage("user", f"pergunta {index}"))
        session.messages.append(ChatMessage("assistant", f"resposta {index}"))

    try:
        message = await client.prompt_sync(info.id, "continue")

        assert message.text == "ok"
        assert "Resuma a conversa" in provider.snapshots[0][-1][1]
        assert provider.requests[0].tools == []
        assert session.summary == "Decisão: usar o plano X. Pendência: revisar o CV."
        assert len(session.messages) <= 6

        usage = client.usage(info.id)
        assert usage["total"]["input_tokens"] == 130  # resumo + turno
        assert usage["total"]["output_tokens"] == 25
        assert len(usage["history"]) == 1
    finally:
        await client.aclose()


async def test_runtime_resumo_llm_vazio_cai_no_local(tmp_path, isolated_env):
    client = _client(tmp_path, isolated_env)
    client.cfg.agent.max_history_messages = 4
    client.cfg.agent.history_summary = "llm"
    provider = ScriptedProvider(
        [[], [TextDelta("ok"), Finish("stop")]]
    )
    client._provider = provider
    info = await client.create_session("teste")
    session = client._sessions[info.id]
    for index in range(6):
        session.messages.append(ChatMessage("user", f"pergunta {index}"))
        session.messages.append(ChatMessage("assistant", f"resposta {index}"))

    try:
        await client.prompt_sync(info.id, "continue")

        assert "pergunta 0" in session.summary
        assert len(session.messages) <= 6
    finally:
        await client.aclose()


async def test_runtime_envia_resumo_em_vez_do_historico_inteiro(tmp_path, isolated_env):
    client = _client(tmp_path, isolated_env)
    client.cfg.agent.max_history_messages = 4
    provider = ScriptedProvider([[TextDelta("ok"), Finish("stop")]])
    client._provider = provider
    info = await client.create_session("teste")
    session = client._sessions[info.id]
    for index in range(6):
        session.messages.append(ChatMessage("user", f"pergunta {index}"))
        session.messages.append(ChatMessage("assistant", f"resposta {index}"))

    try:
        message = await client.prompt_sync(info.id, "continue")

        request = provider.requests[0]
        assert message.text == "ok"
        assert len(request.messages) <= 5
        assert "Resumo do histórico anterior" in request.system
        assert "pergunta 0" in request.system
    finally:
        await client.aclose()


async def test_runtime_guarda_apenas_a_mensagem_original_no_historico(
    tmp_path, isolated_env
):
    client = _client(tmp_path, isolated_env)
    provider = ScriptedProvider([[TextDelta("ok"), Finish("stop")]])
    client._provider = provider
    info = await client.create_session("teste")

    try:
        await client.prompt_sync(
            info.id,
            "PROMPT COM CONTEXTO RECUPERADO",
            history_text="pedido original",
        )

        assert provider.snapshots[0][-1] == ("user", "PROMPT COM CONTEXTO RECUPERADO")
        stored = client._sessions[info.id].messages[0]
        assert stored.role == "user"
        assert stored.content == "pedido original"
    finally:
        await client.aclose()


async def test_runtime_enxuga_resultado_de_ferramenta_no_historico(
    tmp_path, isolated_env
):
    client = _client(tmp_path, isolated_env)
    client.cfg.agent.tool_result_chars = 100
    workspace = tmp_path / "proj"
    workspace.mkdir()
    (workspace / "grande.txt").write_text("x" * 6000, encoding="utf-8")
    provider = ScriptedProvider(
        [
            [
                ToolCallDelta(0, "c1", "read", '{"path": "grande.txt"}'),
                Finish("tool_calls"),
            ],
            [TextDelta("ok"), Finish("stop")],
        ]
    )
    client._provider = provider
    info = await client.create_session("teste")

    try:
        await client.prompt_sync(info.id, "leia o arquivo", allow_tools=True)

        # O modelo viu o resultado completo no turno em que a ferramenta rodou.
        assert len(provider.snapshots[1][-1][1]) >= 6000
        assert "truncado no histórico" not in provider.snapshots[1][-1][1]

        # No historico persistido, o resultado grande foi enxugado.
        stored = next(
            message
            for message in client._sessions[info.id].messages
            if message.role == "tool"
        )
        assert len(stored.content) < 1000
        assert "truncado no histórico" in stored.content
    finally:
        await client.aclose()


async def test_runtime_acumula_e_persiste_tokens(tmp_path, isolated_env):
    client = _client(tmp_path, isolated_env)
    provider = ScriptedProvider(
        [
            [
                UsageDelta(
                    Usage(
                        input_tokens=100,
                        output_tokens=10,
                        cached_tokens=40,
                        total_tokens=110,
                    )
                ),
                TextDelta("ok"),
                Finish("stop"),
            ],
            [
                UsageDelta(
                    Usage(input_tokens=50, output_tokens=5, total_tokens=55)
                ),
                TextDelta("ok2"),
                Finish("stop"),
            ],
        ]
    )
    client._provider = provider
    info = await client.create_session("teste")

    try:
        await client.prompt_sync(info.id, "primeiro")
        await client.prompt_sync(info.id, "segundo")

        usage = client.usage(info.id)
        assert usage["last"]["input_tokens"] == 50
        assert usage["total"]["input_tokens"] == 150
        assert usage["total"]["output_tokens"] == 15
        assert usage["total"]["cached_tokens"] == 40
        assert len(usage["history"]) == 2
        assert usage["history"][0]["input_tokens"] == 100
    finally:
        await client.aclose()

    outro = _client(tmp_path, isolated_env)
    try:
        assert outro.usage(info.id)["total"]["input_tokens"] == 150
    finally:
        await outro.aclose()


async def test_runtime_usa_modelo_pequeno_e_filtra_ferramentas(tmp_path, isolated_env):
    client = _client(tmp_path, isolated_env)
    provider = ScriptedProvider([[TextDelta("ok"), Finish("stop")]])
    client._provider = provider
    session = await client.create_session("teste")

    try:
        await client.prompt_async(
            session.id,
            "me lembre de beber água",
            model=ModelRef("deepseek", "modelo-pequeno"),
            tool_names=("read",),
        )
        while True:
            event = await asyncio.wait_for(client._events.get(), timeout=2)
            if type(event).__name__ in {"TurnCompleted", "TurnFailed"}:
                break

        request = provider.requests[0]
        assert request.model == "modelo-pequeno"
        assert [tool["name"] for tool in request.tools] == ["read"]
    finally:
        await client.aclose()
