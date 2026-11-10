from __future__ import annotations

import json

import httpx
import pytest

from zora.config import Config, WhatsAppConfig
from zora.whatsapp.client import WhatsAppClient, WhatsAppError
from zora.whatsapp.webhook import parse_webhook


def _cfg(**overrides) -> WhatsAppConfig:
    data = {
        "enabled": True,
        "phone_number_id": "123456",
        "access_token": "token-secreto",
        "recipient": "5584999999999",
        "api_version": "v21.0",
    }
    data.update(overrides)
    return WhatsAppConfig(**data)


@pytest.mark.asyncio
async def test_send_text_monta_payload_e_endpoint():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.123"}]})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = WhatsAppClient(_cfg(), http=http)
    result = await client.send_text("olá")
    await http.aclose()

    assert result["messages"][0]["id"] == "wamid.123"
    request = captured[0]
    assert str(request.url) == "https://graph.facebook.com/v21.0/123456/messages"
    assert request.headers["Authorization"] == "Bearer token-secreto"
    payload = json.loads(request.content)
    assert payload["to"] == "5584999999999"
    assert payload["type"] == "text"
    assert payload["text"]["body"] == "olá"


@pytest.mark.asyncio
async def test_send_audio_faz_upload_e_envia_id(tmp_path):
    audio = tmp_path / "voz.mp3"
    audio.write_bytes(b"fake-mp3")
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/media"):
            return httpx.Response(200, json={"id": "media-1"})
        payload = json.loads(request.content)
        assert payload["type"] == "audio"
        assert payload["audio"]["id"] == "media-1"
        return httpx.Response(200, json={"messages": [{"id": "wamid.audio"}]})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = WhatsAppClient(_cfg(), http=http)
    result = await client.send_audio(audio)
    await http.aclose()

    assert result["messages"][0]["id"] == "wamid.audio"
    assert calls == [
        ("POST", "/v21.0/123456/media"),
        ("POST", "/v21.0/123456/messages"),
    ]


@pytest.mark.asyncio
async def test_erro_da_api_vira_excecao():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = WhatsAppClient(_cfg(), http=http)
    with pytest.raises(WhatsAppError) as exc:
        await client.send_text("oi")
    await http.aclose()
    assert exc.value.status == 401


def test_parse_webhook_texto_e_audio():
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": "5584999999999",
                                    "id": "wamid.1",
                                    "timestamp": "1700000000",
                                    "type": "text",
                                    "text": {"body": "oi Zaia"},
                                },
                                {
                                    "from": "5584999999999",
                                    "id": "wamid.2",
                                    "timestamp": "1700000001",
                                    "type": "audio",
                                    "audio": {"id": "media-9", "mime_type": "audio/ogg"},
                                },
                            ]
                        }
                    }
                ]
            }
        ]
    }
    messages = parse_webhook(payload)
    assert [m.kind for m in messages] == ["text", "audio"]
    assert messages[0].text == "oi Zaia"
    assert messages[1].media_id == "media-9"
    assert messages[1].mime_type == "audio/ogg"


def test_config_redige_segredos_do_whatsapp():
    cfg = Config()
    cfg.whatsapp.access_token = "abc"
    cfg.whatsapp.verify_token = "xyz"
    public = cfg.public_dict()["whatsapp"]
    assert public["access_token"] == "***"
    assert public["verify_token"] == "***"
