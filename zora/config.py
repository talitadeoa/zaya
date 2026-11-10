"""Configuracao da Zaia.

Precedencia (maior vence):
  1. variaveis de ambiente ZORA_*
  2. arquivo TOML (`~/.config/zora/config.toml` ou ZORA_CONFIG)
  3. defaults deste modulo

Segredos nunca sao gravados no config: use `~/.config/zora/.env` ou variaveis
de ambiente.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

APP_NAME = "zora"


class OpenCodeConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Vazio => a Zaia escolhe uma porta estavel por diretorio de projeto.
    base_url: str = ""
    directory: str = ""
    model: str = "deepseek/deepseek-flash"
    agent: str = "build"
    auto_start: bool = True
    managed: bool = True
    server_password: str = ""
    resume_session: bool = True
    request_timeout: float = 900.0
    # Sem nenhum evento nesse tempo (e ainda em execucao) => considera travado.
    stall_timeout: float = 90.0
    startup_timeout: float = 30.0
    auto_approve: bool = False


class STTConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str = "small"
    language: str = "pt"
    device: str = "cpu"
    compute_type: str = "int8"
    beam_size: int = 1
    vad_filter: bool = True
    # Se false, a transcricao e enviada direto, sem pedir correcao.
    confirm_transcript: bool = False
    # Legendas parciais enquanto a pessoa fala (transcricao final continua completa).
    partial_transcription: bool = True
    partial_interval_seconds: float = 1.5
    partial_window_seconds: float = 10.0


class TTSConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    provider: str = "edge"  # edge | say | none
    edge_voice: str = "pt-BR-FranciscaNeural"
    say_voice: str = "Luciana"
    rate: str = "+0%"
    max_chars: int = 1200
    # Pausa extra entre frases (ms). 0 = sem pausa adicional.
    sentence_pause_ms: int = 120


class AudioConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sample_rate: int = 16000
    channels: int = 1
    # Silencio necessario para encerrar uma fala (5s = pausa confortavel).
    silence_ms: int = 5000
    min_speech_ms: int = 250
    preroll_ms: int = 300
    # 0 = sem limite de duracao para uma fala.
    max_seconds: float = 0.0
    speech_threshold_db: float = -42.0
    input_device: str = ""
    # Escuta continua: microfone aberto, fala -> executa -> responde -> volta a ouvir.
    continuous: bool = True
    # Depois desse tempo sem fala, desliga o microfone (0 = nunca).
    auto_mute_seconds: float = 30.0


class SecondBrainConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    path: str = "~/SecondBrain"
    index_db: str = ""
    auto_context: bool = True
    context_results: int = 3
    # Limite de caracteres por nota injetada no contexto.
    context_chars: int = 600
    # Criar nota e aditivo: por padrao nao pergunta (mais rapido).
    confirm_create: bool = False
    # Editar/excluir sao destrutivos: por padrao continuam exigindo confirmacao.
    require_confirmation: bool = True
    # Reindexar a cada inicio pode custar caro em bases grandes.
    reindex_on_start: bool = False
    backup_before_write: bool = True
    default_folder: str = "Inbox"
    # Vazio => <secondbrain.path>/preferences.md
    preferences_file: str = ""
    exclude: list[str] = Field(
        default_factory=lambda: [".obsidian", ".git", ".trash", ".zora", "node_modules"]
    )


class InterfaceConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    show_banner: bool = True
    speak_tool_activity: bool = False
    steering: bool = True
    # Pede respostas diretas ao agente, reduzindo exploracao desnecessaria.
    concise: bool = True
    # Mostrar raciocinio e diffs das ferramentas no terminal.
    show_reasoning: bool = True
    show_diffs: bool = True
    # Mostrar o painel de metas ao iniciar.
    show_metas_on_start: bool = True
    # Interface: terminal (padrão) ou textual.
    ui: str = "textual"
    # Modo rápido: sem busca automática e respostas mais curtas.
    fast: bool = False
    # Saudações/small talk respondem na hora, sem chamar o OpenCode.
    fast_smalltalk: bool = True
    # Eco em chinês (caracteres + pinyin) para cada mensagem.
    chinese_echo: bool = False
    # Modo inicial: academica | carreira | assistente | conversa | financas.
    default_mode: str = "conversa"


class MemoryConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Consolida os turnos do dia numa nota de memoria no Second Brain.
    enabled: bool = True
    # Horario local (HH:MM) em que o dia e consolidado enquanto a Zaia roda.
    at: str = "23:00"
    # Ao encerrar depois desse horario, consolida o que ainda estiver pendente.
    on_exit: bool = True
    # Resumo pelo modelo; se falhar, cai no resumo local deterministico.
    use_llm: bool = True
    folder: str = "Memoria"
    max_turns: int = 120
    max_chars: int = 16000
    llm_timeout_seconds: float = 90.0


class ProactiveConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    startup_greeting: bool = True
    # Saudacao inicial instantanea (sem LLM). Ligue para personalizar via modelo.
    greeting_via_llm: bool = False
    idle_checkin: bool = True
    idle_minutes: float = 45.0
    min_interval_minutes: float = 60.0
    daily_max: int = 4
    quiet_hours: str = "23:00-08:00"
    check_interval_seconds: float = 30.0
    reminders: bool = True


class WhatsAppConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    phone_number_id: str = ""
    # Segredos: apenas via ambiente/.env, nunca no TOML.
    access_token: str = ""
    verify_token: str = ""
    recipient: str = ""
    api_version: str = "v21.0"
    graph_base_url: str = "https://graph.facebook.com"
    # Porta local do webhook (recebe mensagens da Meta).
    webhook_host: str = "127.0.0.1"
    webhook_port: int = 8765


class FocusConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    path: str = "~/.config/zora/focus.json"
    # Perguntar sobre o foco nas fases principais da lua.
    prompt_on_moon: bool = True


class BrowserConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    # Perfil persistente: guarda cookies/logins entre execucoes.
    profile_dir: str = ""
    headless: bool = True
    timeout_ms: int = 30000


class AgendaConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    # .env do projeto Agenda (Supabase). Nada e copiado para o repo da Zaia.
    env_file: str = "~/agenda-git/agenda/.env"
    poll_minutes: float = 30.0
    max_items: int = 5
    # Avisar proativamente sobre tarefas pendentes do dia.
    notify_pending: bool = False
    # Checagem automatica da pagina "tarefas pra zora" (0 = desligada).
    tasks_poll_seconds: float = 0.0


class TelegramConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    # Segredo: apenas via ambiente/.env.
    bot_token: str = ""
    # Chat autorizado (você). Vazio => o primeiro chat que falar com o bot.
    allowed_chat_id: str = ""
    api_base_url: str = "https://api.telegram.org"
    poll_timeout: int = 25
    # Responder também com áudio (gera mp3 local e envia como áudio).
    voice_replies: bool = False


class Config(BaseModel):
    model_config = ConfigDict(extra="ignore")

    opencode: OpenCodeConfig = Field(default_factory=OpenCodeConfig)
    stt: STTConfig = Field(default_factory=STTConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    secondbrain: SecondBrainConfig = Field(default_factory=SecondBrainConfig)
    interface: InterfaceConfig = Field(default_factory=InterfaceConfig)
    proactive: ProactiveConfig = Field(default_factory=ProactiveConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    whatsapp: WhatsAppConfig = Field(default_factory=WhatsAppConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    agenda: AgendaConfig = Field(default_factory=AgendaConfig)
    focus: FocusConfig = Field(default_factory=FocusConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    data_dir: str = ""

    # Caminho de onde o config foi carregado (informativo).
    config_file: str = ""

    # ------------------------------------------------------------------ paths
    @staticmethod
    def default_config_path() -> Path:
        env = os.environ.get("ZORA_CONFIG")
        if env:
            return Path(env).expanduser()
        return Path.home() / ".config" / APP_NAME / "config.toml"

    @staticmethod
    def default_dotenv_path() -> Path:
        env = os.environ.get("ZORA_DOTENV")
        if env:
            return Path(env).expanduser()
        return Path.home() / ".config" / APP_NAME / ".env"

    @staticmethod
    def default_data_dir() -> Path:
        env = os.environ.get("ZORA_DATA_DIR")
        if env:
            return Path(env).expanduser()
        return Path.home() / ".local" / "share" / APP_NAME

    def resolved_data_dir(self) -> Path:
        base = Path(self.data_dir).expanduser() if self.data_dir else self.default_data_dir()
        return base.resolve()

    def db_path(self) -> Path:
        return self.resolved_data_dir() / "zora.db"

    def index_path(self) -> Path:
        if self.secondbrain.index_db:
            return Path(self.secondbrain.index_db).expanduser().resolve()
        return self.resolved_data_dir() / "sb_index.db"

    def log_dir(self) -> Path:
        return self.resolved_data_dir() / "logs"

    def servers_dir(self) -> Path:
        return self.resolved_data_dir() / "servers"

    def browser_profile_dir(self) -> Path:
        if self.browser.profile_dir:
            return Path(self.browser.profile_dir).expanduser().resolve()
        return self.resolved_data_dir() / "browser"

    def second_brain_path(self) -> Path:
        return Path(self.secondbrain.path).expanduser().resolve()

    def preferences_path(self) -> Path:
        if self.secondbrain.preferences_file:
            return Path(self.secondbrain.preferences_file).expanduser().resolve()
        return self.second_brain_path() / "preferences.md"

    def project_directory(self) -> Path:
        if self.opencode.directory:
            return Path(self.opencode.directory).expanduser().resolve()
        return Path.cwd().resolve()

    def ensure_dirs(self) -> None:
        for p in (self.resolved_data_dir(), self.log_dir(), self.servers_dir()):
            p.mkdir(parents=True, exist_ok=True)

    def public_dict(self) -> dict[str, Any]:
        data = self.model_dump()
        if data.get("opencode", {}).get("server_password"):
            data["opencode"]["server_password"] = "***"
        for field in ("access_token", "verify_token"):
            if data.get("whatsapp", {}).get(field):
                data["whatsapp"][field] = "***"
        if data.get("telegram", {}).get("bot_token"):
            data["telegram"]["bot_token"] = "***"
        return data

    # ------------------------------------------------------------------ load
    @classmethod
    def load(
        cls, config_path: str | Path | None = None, env: dict[str, str] | None = None
    ) -> Config:
        env = dict(os.environ if env is None else env)
        _load_dotenv(cls.default_dotenv_path(), env)

        path = Path(config_path).expanduser() if config_path else cls.default_config_path()
        raw: dict[str, Any] = {}
        if path.is_file():
            with path.open("rb") as fh:
                raw = tomllib.load(fh)

        _apply_env(raw, env)

        cfg = cls.model_validate(raw)
        cfg.config_file = str(path)
        cfg.ensure_dirs()
        return cfg


def _load_dotenv(path: Path, env: dict[str, str]) -> None:
    """Carrega KEY=VALUE simples sem sobrescrever o ambiente existente."""
    if not path.is_file():
        return
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in env:
                env[key] = value
    except OSError:
        return


_ENV_MAP: dict[str, tuple[str, str]] = {
    "ZORA_AGENDA_ENABLED": ("agenda", "enabled"),
    "ZORA_AGENDA_ENV_FILE": ("agenda", "env_file"),
    "ZORA_AGENDA_MAX_ITEMS": ("agenda", "max_items"),
    "ZORA_AGENDA_NOTIFY_PENDING": ("agenda", "notify_pending"),
    "ZORA_AGENDA_POLL_MINUTES": ("agenda", "poll_minutes"),
    "ZORA_AGENDA_TASKS_POLL_SECONDS": ("agenda", "tasks_poll_seconds"),
    "ZORA_AUDIO_AUTO_MUTE_SECONDS": ("audio", "auto_mute_seconds"),
    "ZORA_AUDIO_CONTINUOUS": ("audio", "continuous"),
    "ZORA_AUDIO_INPUT_DEVICE": ("audio", "input_device"),
    "ZORA_AUDIO_MAX_SECONDS": ("audio", "max_seconds"),
    "ZORA_AUDIO_SILENCE_MS": ("audio", "silence_ms"),
    "ZORA_BROWSER_ENABLED": ("browser", "enabled"),
    "ZORA_BROWSER_HEADLESS": ("browser", "headless"),
    "ZORA_BROWSER_PROFILE_DIR": ("browser", "profile_dir"),
    "ZORA_BROWSER_TIMEOUT_MS": ("browser", "timeout_ms"),
    "ZORA_FOCUS_ENABLED": ("focus", "enabled"),
    "ZORA_FOCUS_PATH": ("focus", "path"),
    "ZORA_FOCUS_PROMPT_ON_MOON": ("focus", "prompt_on_moon"),
    "ZORA_CHINESE": ("interface", "chinese_echo"),
    "ZORA_FAST": ("interface", "fast"),
    "ZORA_FAST_SMALLTALK": ("interface", "fast_smalltalk"),
    "ZORA_UI_SHOW_DIFFS": ("interface", "show_diffs"),
    "ZORA_UI_SHOW_METAS_ON_START": ("interface", "show_metas_on_start"),
    "ZORA_UI_SHOW_REASONING": ("interface", "show_reasoning"),
    "ZORA_UI": ("interface", "ui"),
    "ZORA_OPENCODE_AGENT": ("opencode", "agent"),
    "ZORA_OPENCODE_AUTO_APPROVE": ("opencode", "auto_approve"),
    "ZORA_OPENCODE_AUTO_START": ("opencode", "auto_start"),
    "ZORA_OPENCODE_BASE_URL": ("opencode", "base_url"),
    "ZORA_OPENCODE_DIRECTORY": ("opencode", "directory"),
    "ZORA_OPENCODE_MODEL": ("opencode", "model"),
    "ZORA_OPENCODE_PASSWORD": ("opencode", "server_password"),
    "ZORA_MEMORY_AT": ("memory", "at"),
    "ZORA_MEMORY_ENABLED": ("memory", "enabled"),
    "ZORA_MEMORY_FOLDER": ("memory", "folder"),
    "ZORA_MEMORY_ON_EXIT": ("memory", "on_exit"),
    "ZORA_MEMORY_USE_LLM": ("memory", "use_llm"),
    "ZORA_PROACTIVE_CHECK_INTERVAL_SECONDS": ("proactive", "check_interval_seconds"),
    "ZORA_PROACTIVE_DAILY_MAX": ("proactive", "daily_max"),
    "ZORA_PROACTIVE_ENABLED": ("proactive", "enabled"),
    "ZORA_PROACTIVE_GREETING_VIA_LLM": ("proactive", "greeting_via_llm"),
    "ZORA_PROACTIVE_IDLE_CHECKIN": ("proactive", "idle_checkin"),
    "ZORA_PROACTIVE_IDLE_MINUTES": ("proactive", "idle_minutes"),
    "ZORA_PROACTIVE_MIN_INTERVAL_MINUTES": ("proactive", "min_interval_minutes"),
    "ZORA_PROACTIVE_QUIET_HOURS": ("proactive", "quiet_hours"),
    "ZORA_PROACTIVE_REMINDERS": ("proactive", "reminders"),
    "ZORA_PROACTIVE_STARTUP_GREETING": ("proactive", "startup_greeting"),
    "ZORA_SB_AUTO_CONTEXT": ("secondbrain", "auto_context"),
    "ZORA_SB_CONFIRM_CREATE": ("secondbrain", "confirm_create"),
    "ZORA_SB_CONTEXT_CHARS": ("secondbrain", "context_chars"),
    "ZORA_SB_CONTEXT_RESULTS": ("secondbrain", "context_results"),
    "ZORA_SECOND_BRAIN_INDEX": ("secondbrain", "index_db"),
    "ZORA_SECOND_BRAIN_PATH": ("secondbrain", "path"),
    "ZORA_SB_PREFERENCES_FILE": ("secondbrain", "preferences_file"),
    "ZORA_SB_REINDEX_ON_START": ("secondbrain", "reindex_on_start"),
    "ZORA_SB_REQUIRE_CONFIRMATION": ("secondbrain", "require_confirmation"),
    "ZORA_STT_CONFIRM_TRANSCRIPT": ("stt", "confirm_transcript"),
    "ZORA_STT_LANGUAGE": ("stt", "language"),
    "ZORA_STT_MODEL": ("stt", "model"),
    "ZORA_STT_PARTIAL_INTERVAL_SECONDS": ("stt", "partial_interval_seconds"),
    "ZORA_STT_PARTIAL_TRANSCRIPTION": ("stt", "partial_transcription"),
    "ZORA_STT_PARTIAL_WINDOW_SECONDS": ("stt", "partial_window_seconds"),
    "ZORA_TELEGRAM_ALLOWED_CHAT_ID": ("telegram", "allowed_chat_id"),
    "ZORA_TELEGRAM_API_BASE_URL": ("telegram", "api_base_url"),
    "ZORA_TELEGRAM_BOT_TOKEN": ("telegram", "bot_token"),
    "ZORA_TELEGRAM_ENABLED": ("telegram", "enabled"),
    "ZORA_TELEGRAM_POLL_TIMEOUT": ("telegram", "poll_timeout"),
    "ZORA_TELEGRAM_VOICE_REPLIES": ("telegram", "voice_replies"),
    "ZORA_TTS_VOICE": ("tts", "edge_voice"),
    "ZORA_TTS_ENABLED": ("tts", "enabled"),
    "ZORA_TTS_PROVIDER": ("tts", "provider"),
    "ZORA_TTS_SENTENCE_PAUSE_MS": ("tts", "sentence_pause_ms"),
    "ZORA_WHATSAPP_ACCESS_TOKEN": ("whatsapp", "access_token"),
    "ZORA_WHATSAPP_API_VERSION": ("whatsapp", "api_version"),
    "ZORA_WHATSAPP_ENABLED": ("whatsapp", "enabled"),
    "ZORA_WHATSAPP_PHONE_NUMBER_ID": ("whatsapp", "phone_number_id"),
    "ZORA_WHATSAPP_RECIPIENT": ("whatsapp", "recipient"),
    "ZORA_WHATSAPP_VERIFY_TOKEN": ("whatsapp", "verify_token"),
    "ZORA_WHATSAPP_WEBHOOK_HOST": ("whatsapp", "webhook_host"),
    "ZORA_WHATSAPP_WEBHOOK_PORT": ("whatsapp", "webhook_port"),
}


def _field_is_bool(section: str, field: str) -> bool:
    """True se o campo do modelo e booleano (parse dedicado de env)."""
    section_field = Config.model_fields.get(section)
    if section_field is None:
        return False
    field_info = getattr(section_field.annotation, "model_fields", {}).get(field)
    return field_info is not None and field_info.annotation is bool


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on", "y"}


def _apply_env(raw: dict[str, Any], env: dict[str, str]) -> None:
    for key, (section, field) in _ENV_MAP.items():
        value = env.get(key)
        if value is None or value == "":
            continue
        if _field_is_bool(section, field):
            raw.setdefault(section, {})[field] = _parse_bool(value)
        else:
            raw.setdefault(section, {})[field] = value

    if env.get("ZORA_DATA_DIR"):
        raw["data_dir"] = env["ZORA_DATA_DIR"]


_DEFAULT_CONFIG_TEMPLATE = """\
# Configuracao da Zaia. Segredos NAO devem ficar aqui: use ~/.config/zora/.env

[opencode]
# Vazio = a Zaia gerencia um servidor por diretorio de projeto.
base_url = ""
directory = ""
model = "deepseek/deepseek-flash"
agent = "build"
auto_start = true
managed = true
resume_session = true
request_timeout = 900.0
# Auto-aprovar ferramentas do OpenCode (perigoso; padrao false)
auto_approve = false

[stt]
model = "small"
language = "pt"
device = "cpu"
compute_type = "int8"
beam_size = 1
vad_filter = true
confirm_transcript = false      # envia a transcricao direto, sem pedir correcao
partial_transcription = true    # legendas parciais enquanto fala
partial_interval_seconds = 1.5
partial_window_seconds = 10.0

[tts]
enabled = true
provider = "edge"          # edge | say | none
edge_voice = "pt-BR-FranciscaNeural"
say_voice = "Luciana"
rate = "+0%"
max_chars = 1200
sentence_pause_ms = 120      # pausa extra entre frases (0 = nenhuma)

[audio]
sample_rate = 16000
channels = 1
silence_ms = 5000               # pausa de 5s encerra a fala
min_speech_ms = 250
preroll_ms = 300
max_seconds = 0.0               # 0 = sem limite para uma fala
speech_threshold_db = -42.0
input_device = ""
continuous = true               # microfone aberto; fala -> executa -> volta a ouvir
auto_mute_seconds = 30          # silencio que desliga o microfone (0 = nunca)

[secondbrain]
path = "~/SecondBrain"
context_results = 3
context_chars = 600
index_db = ""
auto_context = true
confirm_create = false          # criar nota nao pergunta (aditivo)
require_confirmation = true     # editar/excluir ainda pergunta
reindex_on_start = false        # ligue para reindexar a cada inicio
backup_before_write = true
default_folder = "Inbox"
preferences_file = ""          # vazio => <secondbrain.path>/preferences.md

[interface]
show_banner = true
speak_tool_activity = false
steering = true
concise = true
show_reasoning = true
show_diffs = true
show_metas_on_start = true
ui = "textual"             # textual (padrão) | terminal
fast = false               # modo rápido: sem busca automática, respostas curtas
fast_smalltalk = true      # saudações respondem na hora, sem chamar o modelo
chinese_echo = false       # mostra chinês + pinyin de cada mensagem
default_mode = "conversa"       # academica | carreira | assistente | conversa | financas

[whatsapp]
enabled = false
phone_number_id = ""
recipient = ""
api_version = "v21.0"
webhook_host = "127.0.0.1"
webhook_port = 8765
# access_token e verify_token vão apenas no .env/ambiente.

[browser]
enabled = true
profile_dir = ""          # vazio => <data_dir>/browser
headless = true
timeout_ms = 30000

[focus]
enabled = true
path = "~/.config/zora/focus.json"
prompt_on_moon = true

[agenda]
enabled = true
env_file = "~/agenda-git/agenda/.env"
poll_minutes = 30
max_items = 5
notify_pending = false
tasks_poll_seconds = 0      # 0 = nao checa sozinho

[telegram]
enabled = false
allowed_chat_id = ""
api_base_url = "https://api.telegram.org"
poll_timeout = 25
voice_replies = false
# bot_token vai apenas no .env/ambiente.

[memory]
# Consolida o dia em uma nota de memoria no Second Brain.
enabled = true
at = "23:00"
on_exit = true
use_llm = true
folder = "Memoria"

[proactive]
enabled = true
startup_greeting = true
greeting_via_llm = false   # true = saudação personalizada pelo modelo (mais lenta)
idle_checkin = true
idle_minutes = 45.0
min_interval_minutes = 60.0
daily_max = 4
quiet_hours = "23:00-08:00"
check_interval_seconds = 30.0
reminders = true
"""


def write_default_config(path: Path | None = None, force: bool = False) -> Path:
    target = path or Config.default_config_path()
    target = Path(target).expanduser()
    if target.exists() and not force:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")
    return target
