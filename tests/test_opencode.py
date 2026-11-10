from __future__ import annotations

import json
import os

import httpx
import pytest

from zaya.config import Config
from zaya.opencode.client import OpenCodeClient, OpenCodeError
from zaya.opencode.events import (
    assistant_text,
    event_payload,
    event_session_id,
    event_type,
    iter_sse_json,
    message_error,
    message_id,
    message_role,
    structured_error_text,
    text_delta,
    tool_activity,
    tool_diff,
)
from zaya.opencode.models import ModelRef


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


async def test_sse_parser_v2_envelope():
    async def lines():
        for line in [
            'data: {"id":"evt_1","type":"session.text.delta",'
            '"data":{"sessionID":"ses_1","assistantMessageID":"msg_1","delta":"oi"}}',
            "",
        ]:
            yield line

    events = [event async for event in iter_sse_json(lines())]
    assert len(events) == 1
    assert event_type(events[0]) == "session.text.delta"
    assert event_payload(events[0])["delta"] == "oi"
    assert event_session_id(events[0]) == "ses_1"


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


def test_v2_message_helpers():
    message = {
        "id": "msg_2",
        "type": "assistant",
        "content": [
            {"type": "text", "text": "Pronto."},
            {"type": "tool", "name": "read"},
        ],
    }
    assert assistant_text(message) == "Pronto."
    assert message_id(message) == "msg_2"
    assert message_role(message) == "assistant"
    assert structured_error_text({"type": "provider.no-route", "message": "sem rota"}) == (
        "provider.no-route: sem rota"
    )


async def test_client_v2_with_mock_transport(tmp_path, isolated_env):
    cfg = Config.load(
        config_path=isolated_env / "missing.toml", env={"ZAYA_DATA_DIR": str(tmp_path / "data")}
    )
    cfg.opencode.model = "opencode/deepseek-v4.1-flash-free"
    client = OpenCodeClient(cfg, tmp_path / "proj")
    captured: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        if request.method == "GET" and path == "/api/location":
            return httpx.Response(200, json={"directory": str(tmp_path / "proj")})
        if request.method == "POST" and path == "/api/session":
            captured["create"] = body
            return httpx.Response(200, json={"data": {"id": "ses_test", "title": "teste"}})
        if request.method == "POST" and path == "/api/session/ses_test/model":
            captured["model"] = body
            return httpx.Response(200, json=True)
        if request.method == "POST" and path == "/api/session/ses_test/agent":
            captured["agent"] = body
            return httpx.Response(200, json=True)
        if request.method == "POST" and path == "/api/session/ses_test/prompt":
            captured["prompt"] = body
            return httpx.Response(200, json={"data": {"id": "msg_3"}})
        if request.method == "GET" and path == "/api/session/ses_test/message":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "msg_2",
                            "type": "assistant",
                            "time": {"created": 2},
                            "content": [{"type": "text", "text": "resposta real"}],
                        },
                        {
                            "id": "msg_1",
                            "type": "user",
                            "time": {"created": 1},
                            "text": "olá",
                        },
                    ]
                },
            )
        if request.method == "POST" and path == ("/api/session/ses_test/permission/per_v2/reply"):
            captured["permission_v2"] = body
            return httpx.Response(200, json=True)
        if request.method == "POST" and path.startswith("/api/session/ses_test/permissions/"):
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
            "id": "deepseek-v4.1-flash-free",
            "providerID": "opencode",
        }
        assert captured["create"]["location"] == {"directory": str(tmp_path / "proj")}

        cfg.opencode.model = "opencode/big-pickle"
        await client.prompt_async("ses_test", "olá")
        assert captured["model"] == {"model": {"id": "big-pickle", "providerID": "opencode"}}
        assert captured["agent"] == {"agent": "build"}
        assert captured["prompt"] == {"text": "olá", "delivery": "queue"}

        text = await client.latest_assistant_text("ses_test")
        assert text == "resposta real"

        await client.reply_permission("ses_test", "per_v2", "always", v2=True)
        assert captured["permission_v2"] == {"decision": "always"}
    finally:
        await client.aclose()


async def test_client_v1_with_mock_transport(tmp_path, isolated_env):
    cfg = Config.load(
        config_path=isolated_env / "missing.toml", env={"ZAYA_DATA_DIR": str(tmp_path / "data")}
    )
    cfg.opencode.model = "deepseek/deepseek-flash"
    client = OpenCodeClient(cfg, tmp_path / "proj")
    captured: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        if request.method == "GET" and path == "/global/health":
            return httpx.Response(200, json={"healthy": True})
        if request.method == "POST" and path == "/session":
            captured["create"] = body
            return httpx.Response(200, json={"id": "ses_test", "title": body.get("title")})
        if request.method == "POST" and path == "/session/ses_test/prompt_async":
            captured["prompt"] = body
            return httpx.Response(204)
        if request.method == "GET" and path == "/session/ses_test/message":
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
        if request.method == "POST" and path.startswith("/session/ses_test/permissions/"):
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
    finally:
        await client.aclose()


async def test_detection_retries_while_server_starts(tmp_path, isolated_env):
    cfg = Config.load(
        config_path=isolated_env / "missing.toml", env={"ZAYA_DATA_DIR": str(tmp_path / "data")}
    )
    client = OpenCodeClient(cfg, tmp_path / "proj")
    calls = {"location": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/location":
            calls["location"] += 1
            if calls["location"] < 3:
                return httpx.Response(503)
            return httpx.Response(200, json={"directory": str(tmp_path / "proj")})
        return httpx.Response(404, text="not found")

    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    )
    try:
        assert await client.health() is None
        assert client.api_version is None
        assert await client.health() is None
        assert client.api_version is None
        data = await client.health()
        assert data == {"directory": str(tmp_path / "proj")}
        assert client.api_version == 2
    finally:
        await client.aclose()


def _client(tmp_path, isolated_env) -> OpenCodeClient:
    cfg = Config.load(
        config_path=isolated_env / "missing.toml", env={"ZAYA_DATA_DIR": str(tmp_path / "data")}
    )
    return OpenCodeClient(cfg, tmp_path / "proj")


def test_payload_de_erro_da_api_v2():
    assert OpenCodeClient._is_error_payload({"_tag": "UnauthorizedError"})
    assert not OpenCodeClient._is_error_payload({"directory": "/tmp/proj"})
    assert not OpenCodeClient._is_error_payload(None)


async def test_health_trata_unauthorized_200_como_falha(tmp_path, isolated_env):
    client = _client(tmp_path, isolated_env)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/location":
            return httpx.Response(
                200,
                json={"_tag": "UnauthorizedError", "message": "Authentication required"},
            )
        return httpx.Response(404, text="not found")

    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    )
    try:
        assert await client.health() is None
        assert client.api_version == 2
        assert client._auth_required is True
    finally:
        await client.aclose()


def test_estado_do_servidor_ignora_pid_morto(tmp_path, isolated_env):
    client = _client(tmp_path, isolated_env)
    morto = 2**31 - 1
    client._write_server_state({"pid": morto, "port": client.port})

    assert client._pid_alive(morto) is False
    assert client._read_server_state()["pid"] == morto

    client._clear_server_state(4243)  # pid diferente: preserva o registro
    assert client._read_server_state()["pid"] == morto

    client._clear_server_state(morto)
    assert client._read_server_state() == {}


def test_reclaim_port_encerra_opencode_orfao(tmp_path, isolated_env, monkeypatch):
    client = _client(tmp_path, isolated_env)
    in_use = {"value": True}
    killed: list[int] = []

    def terminate(pid: int) -> None:
        killed.append(pid)
        in_use["value"] = False

    monkeypatch.setattr(client, "_listener_pids", lambda port: [4242])
    monkeypatch.setattr(client, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(client, "_is_managed_server", lambda pid: True)
    monkeypatch.setattr(client, "_terminate", terminate)
    monkeypatch.setattr(client, "_port_in_use", lambda: in_use["value"])
    client._write_server_state({"pid": 4242, "port": client.port})

    client._reclaim_port()

    assert killed == [4242]
    assert client._read_server_state() == {}


def test_lock_de_inicializacao_impede_duas_instancias(tmp_path, isolated_env):
    c1 = _client(tmp_path, isolated_env)
    c2 = _client(tmp_path, isolated_env)

    fd1 = c1._startup_lock()
    assert fd1 is not None
    try:
        assert c2._startup_lock() is None
    finally:
        os.close(fd1)

    fd2 = c2._startup_lock()
    assert fd2 is not None
    os.close(fd2)


async def test_spawn_avisa_quando_outra_zaya_esta_iniciando(tmp_path, isolated_env, monkeypatch):
    client = _client(tmp_path, isolated_env)
    monkeypatch.setattr("zaya.opencode.client.shutil.which", lambda _: "/bin/true")
    monkeypatch.setattr(client, "_startup_lock", lambda: None)

    with pytest.raises(OpenCodeError, match="Outra Zaya esta iniciando"):
        await client._spawn_server()



def test_reclaim_port_nao_mata_processo_alheio(tmp_path, isolated_env, monkeypatch):
    client = _client(tmp_path, isolated_env)
    monkeypatch.setattr(client, "_listener_pids", lambda port: [4242])
    monkeypatch.setattr(client, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(client, "_is_managed_server", lambda pid: False)
    monkeypatch.setattr(client, "_port_in_use", lambda: True)

    with pytest.raises(OpenCodeError, match="nao gerencia"):
        client._reclaim_port()


def test_reclaim_port_recupera_orfao_nao_registrado_do_mesmo_projeto(
    tmp_path, isolated_env, monkeypatch
):
    client = _client(tmp_path, isolated_env)
    in_use = {"value": True}
    killed: list[int] = []

    def terminate(pid: int) -> None:
        killed.append(pid)
        in_use["value"] = False

    monkeypatch.setattr(client, "_listener_pids", lambda port: [4242])
    monkeypatch.setattr(client, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(client, "_is_managed_server", lambda pid: True)
    monkeypatch.setattr(client, "_process_cwd", lambda pid: str(client.directory))
    monkeypatch.setattr(client, "_terminate", terminate)
    monkeypatch.setattr(client, "_port_in_use", lambda: in_use["value"])

    client._reclaim_port()

    assert killed == [4242]


def test_reclaim_port_nao_mata_opencode_serve_de_outro_projeto(tmp_path, isolated_env, monkeypatch):
    client = _client(tmp_path, isolated_env)
    killed: list[int] = []

    monkeypatch.setattr(client, "_listener_pids", lambda port: [4242])
    monkeypatch.setattr(client, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(client, "_is_managed_server", lambda pid: True)
    monkeypatch.setattr(client, "_process_cwd", lambda pid: "/outro/projeto")
    monkeypatch.setattr(client, "_terminate", lambda pid: killed.append(pid))
    monkeypatch.setattr(client, "_port_in_use", lambda: True)

    with pytest.raises(OpenCodeError, match="nao gerencia"):
        client._reclaim_port()

    assert killed == []


def test_reclaim_port_ignora_registro_de_outro_diretorio(tmp_path, isolated_env, monkeypatch):
    client = _client(tmp_path, isolated_env)
    killed: list[int] = []
    client._write_server_state({"pid": 4242, "directory": "/outro/projeto"})

    monkeypatch.setattr(client, "_listener_pids", lambda port: [])
    monkeypatch.setattr(client, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(client, "_is_managed_server", lambda pid: True)
    monkeypatch.setattr(client, "_process_cwd", lambda pid: "/outro/projeto")
    monkeypatch.setattr(client, "_terminate", lambda pid: killed.append(pid))
    monkeypatch.setattr(client, "_port_in_use", lambda: True)

    with pytest.raises(OpenCodeError, match="nao gerencia"):
        client._reclaim_port()

    assert killed == []


def test_estado_do_servidor_guarda_senha_com_permissao_privada(tmp_path, isolated_env):
    client = _client(tmp_path, isolated_env)

    client._write_server_state({"pid": 4242, "password": "segredo"})

    path = client._server_state_path()
    assert json.loads(path.read_text(encoding="utf-8"))["password"] == "segredo"
    assert path.stat().st_mode & 0o777 == 0o600


class _FakeProcess:
    def __init__(self, pid: int, returncode: int | None = None):
        self.pid = pid
        self.returncode = returncode

    def poll(self) -> int | None:
        return self.returncode


async def test_spawn_persiste_senha_e_pid(tmp_path, isolated_env, monkeypatch):
    client = _client(tmp_path, isolated_env)
    client.cfg.opencode.server_password = "senha-fixa"
    client._password = "senha-fixa"
    fake = _FakeProcess(os.getpid())

    monkeypatch.setattr("zaya.opencode.client.shutil.which", lambda _: "/bin/true")
    monkeypatch.setattr("zaya.opencode.client.subprocess.Popen", lambda *a, **k: fake)
    monkeypatch.setattr(client, "_port_in_use", lambda: False)

    async def healthy() -> dict:
        client.api_version = 2
        return {"directory": str(client.directory)}

    monkeypatch.setattr(client, "health", healthy)

    await client._spawn_server()

    state = json.loads(client._server_state_path().read_text(encoding="utf-8"))
    assert state["pid"] == os.getpid()
    assert state["password"] == "senha-fixa"
    assert client._spawned is fake


async def test_spawn_que_falha_nao_registra_pid(tmp_path, isolated_env, monkeypatch):
    client = _client(tmp_path, isolated_env)
    dead = _FakeProcess(999_999, returncode=1)

    monkeypatch.setattr("zaya.opencode.client.shutil.which", lambda _: "/bin/true")
    monkeypatch.setattr("zaya.opencode.client.subprocess.Popen", lambda *a, **k: dead)
    monkeypatch.setattr(client, "_port_in_use", lambda: False)
    monkeypatch.setattr(client, "_terminate", lambda pid: None)

    with pytest.raises(OpenCodeError, match="codigo 1"):
        await client._spawn_server()

    assert not client._server_state_path().exists()
