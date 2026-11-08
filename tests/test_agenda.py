from __future__ import annotations

from zora.agenda.client import AgendaClient, _load_env_file
from zora.config import AgendaConfig


def test_env_file_e_cliente_habilitado(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "VITE_SUPABASE_URL=https://exemplo.supabase.co\n"
        "SUPABASE_SERVICE_ROLE_KEY=chave\n"
        "AGENDA_USER_ID=user-1\n",
        encoding="utf-8",
    )
    config = AgendaConfig(env_file=str(env_file))
    client = AgendaClient(config)
    assert client.enabled is True
    assert _load_env_file(env_file)["AGENDA_USER_ID"] == "user-1"


def test_cliente_desabilitado_sem_env(tmp_path):
    config = AgendaConfig(env_file=str(tmp_path / "nao-existe.env"))
    client = AgendaClient(config)
    assert client.enabled is False


def test_agenda_desabilitada_por_config(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "VITE_SUPABASE_URL=https://exemplo.supabase.co\n"
        "SUPABASE_SERVICE_ROLE_KEY=chave\n"
        "AGENDA_USER_ID=user-1\n",
        encoding="utf-8",
    )
    config = AgendaConfig(env_file=str(env_file), enabled=False)
    assert AgendaClient(config).enabled is False
