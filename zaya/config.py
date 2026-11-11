"""Configuracao da Zaya.

Precedencia (maior vence):
  1. variaveis de ambiente ZAYA_*
  2. arquivo TOML (`~/.config/zaya/config.toml` ou ZAYA_CONFIG)
  3. defaults deste modulo

Segredos nunca sao gravados no config: use `~/.config/zaya/.env` ou variaveis
de ambiente.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

APP_NAME = "zaya"


class AgentConfig(BaseModel):
    """Motor proprio da Zaya: provedor, modelo e ferramentas."""

    model_config = ConfigDict(extra="ignore")

    # deepseek | openai | openrouter | ollama | anthropic | custom
    provider: str = "deepseek"
    model: str = "deepseek-flash"
    # Vazio => usa o endpoint padrao do provider.
    base_url: str = ""
    # Segredo: prefira ZAYA_AGENT_API_KEY no ambiente/.env.
    api_key: str = ""
    # Diretorio em que o agente trabalha (vazio = diretorio atual).
    directory: str = ""
    max_tokens: int = 4096
    temperature: float | None = 0.3
    max_steps: int = 32
    resume_session: bool = True
    request_timeout: float = 900.0
    # Sem nenhum evento nesse tempo (e ainda em execucao) => considera travado.
    stall_timeout: float = 90.0
    auto_approve: bool = False
    # Ferramentas de leitura e escrita no computador.
    tools: bool = True
    allow_outside_project: bool = True
    # Modelo pequeno para pedidos curtos (vazio = sempre o modelo principal).
    fast_model: str = ""
    # Envia so as ferramentas que o turno pode precisar.
    tool_filter: bool = True
    # Marca o prefixo estavel do prompt para cache (Anthropic).
    cache_prompt: bool = True
    # Depois desse numero de mensagens, o historico antigo vira um resumo.
    max_history_messages: int = 20
    # Teto de caracteres do historico que vai ao modelo (0 = sem teto).
    max_history_chars: int = 24000
    summary_chars: int = 1200
    # Medir tokens por turno (usa stream_options em APIs compativeis).
    usage: bool = True
    # Teto de caracteres de um resultado de ferramenta guardado no historico.
    # 0 = sem teto. O modelo ve o resultado inteiro no turno atual.
    tool_result_chars: int = 4000
    # Como resumir o historico antigo: local (extrativo) ou llm (semantico).
    # O modo llm cai automaticamente no local se a chamada falhar.
    history_summary: str = "local"
    # Modelo do resumo (vazio = modelo principal).
    history_summary_model: str = ""

    def model_slug(self) -> str:
        return f"{self.provider}/{self.model}"


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
    # 2 a 3 trechos curtos bastam: mais que isso infla o prompt sem ganho.
    context_results: int = 2
    # Limite de caracteres por nota injetada no contexto.
    context_chars: int = 480
    # Teto total do bloco de contexto recuperado.
    context_max_chars: int = 1200
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
        default_factory=lambda: [".obsidian", ".git", ".trash", ".zaya", "node_modules"]
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
    # Painéis de metas e lua começam recolhidos (F5 continua ocultando).
    dashboard_collapsed: bool = True
    # Interface: terminal (padrão) ou textual.
    ui: str = "textual"
    # Comeca no modo selecao: arrastar no chat copia, sem desligar os botoes.
    selection_mode: bool = False
    # Modo rápido: sem busca automática e respostas mais curtas.
    fast: bool = False
    # Saudações/small talk respondem na hora, sem chamar o modelo.
    fast_smalltalk: bool = True
    # Eco em chinês (caracteres + pinyin) para cada mensagem.
    chinese_echo: bool = False
    # Modo inicial: pesquisadora | trabalhadora | investidora | assistente.
    default_mode: str = "assistente"


class MemoryConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Consolida os turnos do dia numa nota de memoria no Second Brain.
    enabled: bool = True
    # Horario local (HH:MM) em que o dia e consolidado enquanto a Zaya roda.
    at: str = "23:00"
    # Ao encerrar depois desse horario, consolida o que ainda estiver pendente.
    on_exit: bool = True
    # Resumo pelo modelo; se falhar, cai no resumo local deterministico.
    use_llm: bool = True
    folder: str = "Memoria"
    max_turns: int = 120
    max_chars: int = 16000
    llm_timeout_seconds: float = 90.0


class BriefingConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Bom dia com tarefas/habitos e resumo do meio-dia, sem usar o modelo.
    enabled: bool = True
    morning_at: str = "08:00"
    noon_at: str = "12:00"
    # Depois desse horario o resumo do meio-dia nao e mais enviado.
    noon_until: str = "18:00"
    # Trecho do nome da rotina da Agenda (sem acento, case-insensitive).
    morning_routine: str = "matinal"
    max_tasks: int = 15
    check_interval_seconds: float = 30.0
    retry_minutes: float = 10.0
    # Resumo da carteira (watchlist) nos dias escolhidos, sem usar o modelo.
    market_enabled: bool = True
    # Dias da semana com 0 = segunda (seg, qua, sex por padrao).
    market_days: list[int] = Field(default_factory=lambda: [0, 2, 4])
    market_at: str = "12:00"
    # Depois desse horario o resumo da carteira nao e mais enviado.
    market_until: str = "18:00"


class CareerConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Rotina semanal de vagas: busca nas fontes abertas, filtra, pontua e
    # entrega no Telegram. O metodo vive em ~/SecondBrain/cv/playbook-vagas.md.
    enabled: bool = True
    # Dias da semana com 0 = segunda (segunda por padrao).
    days: list[int] = Field(default_factory=lambda: [0])
    at: str = "09:00"
    # Depois desse horario a rotina nao e mais enviada.
    until: str = "18:00"
    check_interval_seconds: float = 30.0
    retry_minutes: float = 10.0
    # Vagas por mensagem; o resto fica registrado no estado.
    max_items: int = 8
    # Vazio => <data_dir>/career/state.json
    state_file: str = ""


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
    path: str = "~/.config/zaya/focus.json"
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
    # .env do projeto Agenda (Supabase). Nada e copiado para o repo da Zaya.
    env_file: str = "~/agenda-git/agenda/.env"
    poll_minutes: float = 30.0
    max_items: int = 5
    # Avisar proativamente sobre tarefas pendentes do dia.
    notify_pending: bool = False
    # Checagem automatica da pagina "tarefas pra zaya" (0 = desligada).
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
    # O Telegram não tem terminal interativo para aprovar ferramentas. Com
    # isto ligado, write/edit/bash são aprovados automaticamente (exige
    # allowed_chat_id fixado). Desligado, essas ferramentas são negadas.
    auto_approve: bool = False


class Config(BaseModel):
    model_config = ConfigDict(extra="ignore")

    agent: AgentConfig = Field(default_factory=AgentConfig)
    stt: STTConfig = Field(default_factory=STTConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    secondbrain: SecondBrainConfig = Field(default_factory=SecondBrainConfig)
    interface: InterfaceConfig = Field(default_factory=InterfaceConfig)
    proactive: ProactiveConfig = Field(default_factory=ProactiveConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    briefing: BriefingConfig = Field(default_factory=BriefingConfig)
    career: CareerConfig = Field(default_factory=CareerConfig)
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
        env = os.environ.get("ZAYA_CONFIG")
        if env:
            return Path(env).expanduser()
        return Path.home() / ".config" / APP_NAME / "config.toml"

    @staticmethod
    def default_dotenv_path() -> Path:
        env = os.environ.get("ZAYA_DOTENV")
        if env:
            return Path(env).expanduser()
        return Path.home() / ".config" / APP_NAME / ".env"

    @staticmethod
    def default_data_dir() -> Path:
        env = os.environ.get("ZAYA_DATA_DIR")
        if env:
            return Path(env).expanduser()
        return Path.home() / ".local" / "share" / APP_NAME

    def resolved_data_dir(self) -> Path:
        base = Path(self.data_dir).expanduser() if self.data_dir else self.default_data_dir()
        return base.resolve()

    def db_path(self) -> Path:
        return self.resolved_data_dir() / "zaya.db"

    def index_path(self) -> Path:
        if self.secondbrain.index_db:
            return Path(self.secondbrain.index_db).expanduser().resolve()
        return self.resolved_data_dir() / "sb_index.db"

    def log_dir(self) -> Path:
        return self.resolved_data_dir() / "logs"

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
        if self.agent.directory:
            return Path(self.agent.directory).expanduser().resolve()
        return Path.cwd().resolve()

    def ensure_dirs(self) -> None:
        for p in (self.resolved_data_dir(), self.log_dir()):
            p.mkdir(parents=True, exist_ok=True)

    def public_dict(self) -> dict[str, Any]:
        data = self.model_dump()
        if data.get("agent", {}).get("api_key"):
            data["agent"]["api_key"] = "***"
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
        real_env = env is None
        env = dict(os.environ if env is None else env)
        _load_dotenv(cls.default_dotenv_path(), env)
        if real_env:
            # Deixa o que veio do .env visivel para subprocessos (ferramenta
            # de shell, CLIs filhas) sem sobrescrever o ambiente existente.
            _export_env(env)

        path = Path(config_path).expanduser() if config_path else cls.default_config_path()
        raw: dict[str, Any] = {}
        if path.is_file():
            with path.open("rb") as fh:
                raw = tomllib.load(fh)

        _migrate_legacy_config(raw)
        _apply_env(raw, env)

        cfg = cls.model_validate(raw)
        cfg.config_file = str(path)
        cfg.ensure_dirs()
        return cfg


def _export_env(env: dict[str, str]) -> None:
    """Espelha o .env no ambiente do processo, sem sobrescrever o que ja existe."""
    for key, value in env.items():
        os.environ.setdefault(key, value)


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
    "ZAYA_AGENDA_ENABLED": ("agenda", "enabled"),
    "ZAYA_AGENDA_ENV_FILE": ("agenda", "env_file"),
    "ZAYA_AGENDA_MAX_ITEMS": ("agenda", "max_items"),
    "ZAYA_AGENDA_NOTIFY_PENDING": ("agenda", "notify_pending"),
    "ZAYA_AGENDA_POLL_MINUTES": ("agenda", "poll_minutes"),
    "ZAYA_AGENDA_TASKS_POLL_SECONDS": ("agenda", "tasks_poll_seconds"),
    "ZAYA_AUDIO_AUTO_MUTE_SECONDS": ("audio", "auto_mute_seconds"),
    "ZAYA_AUDIO_CONTINUOUS": ("audio", "continuous"),
    "ZAYA_AUDIO_INPUT_DEVICE": ("audio", "input_device"),
    "ZAYA_AUDIO_MAX_SECONDS": ("audio", "max_seconds"),
    "ZAYA_AUDIO_SILENCE_MS": ("audio", "silence_ms"),
    "ZAYA_BROWSER_ENABLED": ("browser", "enabled"),
    "ZAYA_BROWSER_HEADLESS": ("browser", "headless"),
    "ZAYA_BROWSER_PROFILE_DIR": ("browser", "profile_dir"),
    "ZAYA_BROWSER_TIMEOUT_MS": ("browser", "timeout_ms"),
    "ZAYA_FOCUS_ENABLED": ("focus", "enabled"),
    "ZAYA_FOCUS_PATH": ("focus", "path"),
    "ZAYA_FOCUS_PROMPT_ON_MOON": ("focus", "prompt_on_moon"),
    "ZAYA_CHINESE": ("interface", "chinese_echo"),
    "ZAYA_FAST": ("interface", "fast"),
    "ZAYA_FAST_SMALLTALK": ("interface", "fast_smalltalk"),
    "ZAYA_UI_DASHBOARD_COLLAPSED": ("interface", "dashboard_collapsed"),
    "ZAYA_UI_SHOW_DIFFS": ("interface", "show_diffs"),
    "ZAYA_UI_SHOW_METAS_ON_START": ("interface", "show_metas_on_start"),
    "ZAYA_UI_SHOW_REASONING": ("interface", "show_reasoning"),
    "ZAYA_UI_SELECTION_MODE": ("interface", "selection_mode"),
    "ZAYA_UI": ("interface", "ui"),
    "ZAYA_AGENT_ALLOW_OUTSIDE_PROJECT": ("agent", "allow_outside_project"),
    "ZAYA_AGENT_API_KEY": ("agent", "api_key"),
    "ZAYA_AGENT_AUTO_APPROVE": ("agent", "auto_approve"),
    "ZAYA_AGENT_BASE_URL": ("agent", "base_url"),
    "ZAYA_AGENT_CACHE_PROMPT": ("agent", "cache_prompt"),
    "ZAYA_AGENT_DIRECTORY": ("agent", "directory"),
    "ZAYA_AGENT_FAST_MODEL": ("agent", "fast_model"),
    "ZAYA_AGENT_HISTORY_SUMMARY": ("agent", "history_summary"),
    "ZAYA_AGENT_HISTORY_SUMMARY_MODEL": ("agent", "history_summary_model"),
    "ZAYA_AGENT_MAX_HISTORY_CHARS": ("agent", "max_history_chars"),
    "ZAYA_AGENT_MAX_HISTORY_MESSAGES": ("agent", "max_history_messages"),
    "ZAYA_AGENT_MAX_STEPS": ("agent", "max_steps"),
    "ZAYA_AGENT_MAX_TOKENS": ("agent", "max_tokens"),
    "ZAYA_AGENT_MODEL": ("agent", "model"),
    "ZAYA_AGENT_PROVIDER": ("agent", "provider"),
    "ZAYA_AGENT_REQUEST_TIMEOUT": ("agent", "request_timeout"),
    "ZAYA_AGENT_RESUME_SESSION": ("agent", "resume_session"),
    "ZAYA_AGENT_STALL_TIMEOUT": ("agent", "stall_timeout"),
    "ZAYA_AGENT_SUMMARY_CHARS": ("agent", "summary_chars"),
    "ZAYA_AGENT_TEMPERATURE": ("agent", "temperature"),
    "ZAYA_AGENT_TOOL_FILTER": ("agent", "tool_filter"),
    "ZAYA_AGENT_TOOLS": ("agent", "tools"),
    "ZAYA_AGENT_TOOL_RESULT_CHARS": ("agent", "tool_result_chars"),
    "ZAYA_AGENT_USAGE": ("agent", "usage"),
    "ZAYA_MEMORY_AT": ("memory", "at"),
    "ZAYA_MEMORY_ENABLED": ("memory", "enabled"),
    "ZAYA_MEMORY_FOLDER": ("memory", "folder"),
    "ZAYA_MEMORY_ON_EXIT": ("memory", "on_exit"),
    "ZAYA_MEMORY_USE_LLM": ("memory", "use_llm"),
    "ZAYA_BRIEFING_ENABLED": ("briefing", "enabled"),
    "ZAYA_BRIEFING_MORNING_AT": ("briefing", "morning_at"),
    "ZAYA_BRIEFING_NOON_AT": ("briefing", "noon_at"),
    "ZAYA_BRIEFING_NOON_UNTIL": ("briefing", "noon_until"),
    "ZAYA_BRIEFING_MORNING_ROUTINE": ("briefing", "morning_routine"),
    "ZAYA_BRIEFING_MAX_TASKS": ("briefing", "max_tasks"),
    "ZAYA_BRIEFING_MARKET_ENABLED": ("briefing", "market_enabled"),
    "ZAYA_BRIEFING_MARKET_AT": ("briefing", "market_at"),
    "ZAYA_BRIEFING_MARKET_UNTIL": ("briefing", "market_until"),
    "ZAYA_CAREER_ENABLED": ("career", "enabled"),
    "ZAYA_CAREER_AT": ("career", "at"),
    "ZAYA_CAREER_UNTIL": ("career", "until"),
    "ZAYA_CAREER_MAX_ITEMS": ("career", "max_items"),
    "ZAYA_CAREER_STATE_FILE": ("career", "state_file"),
    "ZAYA_PROACTIVE_CHECK_INTERVAL_SECONDS": ("proactive", "check_interval_seconds"),
    "ZAYA_PROACTIVE_DAILY_MAX": ("proactive", "daily_max"),
    "ZAYA_PROACTIVE_ENABLED": ("proactive", "enabled"),
    "ZAYA_PROACTIVE_GREETING_VIA_LLM": ("proactive", "greeting_via_llm"),
    "ZAYA_PROACTIVE_IDLE_CHECKIN": ("proactive", "idle_checkin"),
    "ZAYA_PROACTIVE_IDLE_MINUTES": ("proactive", "idle_minutes"),
    "ZAYA_PROACTIVE_MIN_INTERVAL_MINUTES": ("proactive", "min_interval_minutes"),
    "ZAYA_PROACTIVE_QUIET_HOURS": ("proactive", "quiet_hours"),
    "ZAYA_PROACTIVE_REMINDERS": ("proactive", "reminders"),
    "ZAYA_PROACTIVE_STARTUP_GREETING": ("proactive", "startup_greeting"),
    "ZAYA_SB_AUTO_CONTEXT": ("secondbrain", "auto_context"),
    "ZAYA_SB_CONFIRM_CREATE": ("secondbrain", "confirm_create"),
    "ZAYA_SB_CONTEXT_CHARS": ("secondbrain", "context_chars"),
    "ZAYA_SB_CONTEXT_MAX_CHARS": ("secondbrain", "context_max_chars"),
    "ZAYA_SB_CONTEXT_RESULTS": ("secondbrain", "context_results"),
    "ZAYA_SECOND_BRAIN_INDEX": ("secondbrain", "index_db"),
    "ZAYA_SECOND_BRAIN_PATH": ("secondbrain", "path"),
    "ZAYA_SB_PREFERENCES_FILE": ("secondbrain", "preferences_file"),
    "ZAYA_SB_REINDEX_ON_START": ("secondbrain", "reindex_on_start"),
    "ZAYA_SB_REQUIRE_CONFIRMATION": ("secondbrain", "require_confirmation"),
    "ZAYA_STT_CONFIRM_TRANSCRIPT": ("stt", "confirm_transcript"),
    "ZAYA_STT_LANGUAGE": ("stt", "language"),
    "ZAYA_STT_MODEL": ("stt", "model"),
    "ZAYA_STT_PARTIAL_INTERVAL_SECONDS": ("stt", "partial_interval_seconds"),
    "ZAYA_STT_PARTIAL_TRANSCRIPTION": ("stt", "partial_transcription"),
    "ZAYA_STT_PARTIAL_WINDOW_SECONDS": ("stt", "partial_window_seconds"),
    "ZAYA_TELEGRAM_ALLOWED_CHAT_ID": ("telegram", "allowed_chat_id"),
    "ZAYA_TELEGRAM_API_BASE_URL": ("telegram", "api_base_url"),
    "ZAYA_TELEGRAM_AUTO_APPROVE": ("telegram", "auto_approve"),
    "ZAYA_TELEGRAM_BOT_TOKEN": ("telegram", "bot_token"),
    "ZAYA_TELEGRAM_ENABLED": ("telegram", "enabled"),
    "ZAYA_TELEGRAM_POLL_TIMEOUT": ("telegram", "poll_timeout"),
    "ZAYA_TELEGRAM_VOICE_REPLIES": ("telegram", "voice_replies"),
    "ZAYA_TTS_VOICE": ("tts", "edge_voice"),
    "ZAYA_TTS_ENABLED": ("tts", "enabled"),
    "ZAYA_TTS_PROVIDER": ("tts", "provider"),
    "ZAYA_TTS_SENTENCE_PAUSE_MS": ("tts", "sentence_pause_ms"),
    "ZAYA_WHATSAPP_ACCESS_TOKEN": ("whatsapp", "access_token"),
    "ZAYA_WHATSAPP_API_VERSION": ("whatsapp", "api_version"),
    "ZAYA_WHATSAPP_ENABLED": ("whatsapp", "enabled"),
    "ZAYA_WHATSAPP_PHONE_NUMBER_ID": ("whatsapp", "phone_number_id"),
    "ZAYA_WHATSAPP_RECIPIENT": ("whatsapp", "recipient"),
    "ZAYA_WHATSAPP_VERIFY_TOKEN": ("whatsapp", "verify_token"),
    "ZAYA_WHATSAPP_WEBHOOK_HOST": ("whatsapp", "webhook_host"),
    "ZAYA_WHATSAPP_WEBHOOK_PORT": ("whatsapp", "webhook_port"),
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


# Variaveis do formato antigo (OpenCode). Continuam aceitas enquanto a
# configuracao nao migra para ZAYA_AGENT_*.
_LEGACY_ENV_MAP: dict[str, str] = {
    "ZAYA_OPENCODE_AUTO_APPROVE": "auto_approve",
    "ZAYA_OPENCODE_DIRECTORY": "directory",
    "ZAYA_OPENCODE_MODEL": "model",
}

# Modelos do catalogo antigo que nao existem na API direta da DeepSeek.
_PROVIDER_API_KEYS: dict[str, str] = {
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
}

_LEGACY_MODEL_ALIASES: dict[str, tuple[str, str]] = {
    "deepseek/deepseek-flash": ("deepseek", "deepseek-flash"),
    "deepseek/deepseek-v4.1-flash": ("deepseek", "deepseek-flash"),
}


def _migrate_legacy_config(raw: dict[str, Any]) -> None:
    """Move ``[opencode]`` para ``[agent]`` quando a config ainda e antiga."""
    legacy = raw.pop("opencode", None)
    if not isinstance(legacy, dict):
        return
    agent = raw.setdefault("agent", {})
    if not isinstance(agent, dict):
        return
    for field in (
        "auto_approve",
        "directory",
        "request_timeout",
        "resume_session",
        "stall_timeout",
        "temperature",
        "max_tokens",
    ):
        if field in legacy and field not in agent:
            agent[field] = legacy[field]
    slug = str(legacy.get("model") or "").strip()
    if slug and "model" not in agent:
        provider, _, model = slug.partition("/")
        if provider and model:
            alias = _LEGACY_MODEL_ALIASES.get(slug)
            if alias:
                provider, model = alias
            agent.setdefault("provider", provider)
            agent["model"] = model
        else:
            agent["model"] = slug


def _apply_env(raw: dict[str, Any], env: dict[str, str]) -> None:
    for key, (section, field) in _ENV_MAP.items():
        value = env.get(key)
        if value is None or value == "":
            continue
        if _field_is_bool(section, field):
            raw.setdefault(section, {})[field] = _parse_bool(value)
        else:
            raw.setdefault(section, {})[field] = value

    agent = raw.setdefault("agent", {})
    if isinstance(agent, dict):
        for key, field in _LEGACY_ENV_MAP.items():
            value = env.get(key)
            if value and field not in agent:
                if field == "auto_approve":
                    agent[field] = _parse_bool(value)
                elif field == "model" and "/" in value:
                    provider, _, model = value.partition("/")
                    alias = _LEGACY_MODEL_ALIASES.get(value)
                    if alias:
                        provider, model = alias
                    agent.setdefault("provider", provider)
                    agent["model"] = model
                else:
                    agent[field] = value

    # A chave lida do .env precisa chegar ao agente: `resolve_api_key` consulta
    # o ambiente do processo, mas o .env e carregado numa copia local.
    if isinstance(agent, dict) and not str(agent.get("api_key") or "").strip():
        provider = str(agent.get("provider") or "deepseek").strip().lower()
        provider_key = _PROVIDER_API_KEYS.get(provider, "")
        for key in (
            "ZAYA_AGENT_API_KEY",
            provider_key,
            "DEEPSEEK_API_KEY",
            "OPENAI_API_KEY",
            "OPENROUTER_API_KEY",
            "ANTHROPIC_API_KEY",
        ):
            if key and env.get(key):
                agent["api_key"] = env[key]
                break

    if env.get("ZAYA_DATA_DIR"):
        raw["data_dir"] = env["ZAYA_DATA_DIR"]


_DEFAULT_CONFIG_TEMPLATE = """\
# Configuracao da Zaya. Segredos NAO devem ficar aqui: use ~/.config/zaya/.env

[agent]
# deepseek | openai | openrouter | ollama | anthropic | custom
provider = "deepseek"
model = "deepseek-flash"
# Vazio = endpoint padrao do provider.
base_url = ""
# Diretorio de trabalho (vazio = diretorio atual).
directory = ""
max_tokens = 4096
temperature = 0.3
max_steps = 32
resume_session = true
request_timeout = 900.0
# Auto-aprovar ferramentas (perigoso; padrao false).
auto_approve = false
tools = true
allow_outside_project = true
# Modelo pequeno para pedidos curtos (vazio = sempre o principal).
fast_model = ""
# So envia ao modelo as ferramentas que o turno pode precisar.
tool_filter = true
# Cacheia o prefixo estavel do prompt (persona + preferencias).
cache_prompt = true
# Depois disso o historico antigo vira um resumo compacto.
max_history_messages = 20
max_history_chars = 24000
summary_chars = 1200
# Mede tokens por turno (e mostra no /status).
usage = true
# Resultado de ferramenta no historico (o turno atual ve tudo; 0 = sem teto).
tool_result_chars = 4000
# Resumo do historico: local (extrativo) | llm (semantico, com fallback local).
history_summary = "local"
# Modelo do resumo (vazio = modelo principal).
history_summary_model = ""
# A chave da API NAO fica aqui: use ZAYA_AGENT_API_KEY em ~/.config/zaya/.env

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
context_results = 2            # 2-3 trechos curtos bastam
context_chars = 480            # por trecho
context_max_chars = 1200       # teto do bloco inteiro
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
dashboard_collapsed = true   # metas e lua começam recolhidos
ui = "textual"             # textual (padrão) | terminal
selection_mode = false     # true = abre no modo seleção (arrasta copia; botões ativos)
fast = false               # modo rápido: sem busca automática, respostas curtas
fast_smalltalk = true      # saudações respondem na hora, sem chamar o modelo
chinese_echo = false       # mostra chinês + pinyin de cada mensagem
default_mode = "assistente"     # pesquisadora | trabalhadora | investidora | assistente

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
path = "~/.config/zaya/focus.json"
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
auto_approve = false      # true = aprova write/edit/bash no chat autorizado
# bot_token vai apenas no .env/ambiente.

[memory]
# Consolida o dia em uma nota de memoria no Second Brain.
enabled = true
at = "23:00"
on_exit = true
use_llm = true
folder = "Memoria"

[briefing]
# Bom dia e resumo do meio-dia no Telegram, direto da Agenda (sem LLM).
enabled = true
morning_at = "08:00"
noon_at = "12:00"
noon_until = "18:00"
morning_routine = "matinal"
max_tasks = 15

[career]
# Rotina semanal de vagas: busca nas fontes abertas, filtra, pontua e
# entrega no Telegram. O metodo esta em ~/SecondBrain/cv/playbook-vagas.md.
enabled = true
days = [0]                  # 0=seg, 1=ter, 2=qua, 3=qui, 4=sex
at = "09:00"
until = "18:00"             # depois disso nao envia mais (recupera na proxima)
max_items = 8               # vagas por mensagem; o resto fica no estado

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
