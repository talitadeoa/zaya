# ZORA

Assistente pessoal de voz para macOS, integrada ao **OpenCode** (como motor de
programação) e a um **Second Brain em Markdown**. A Zora é uma camada própria de
interface, voz, memória e orquestração — ela **não** reimplementa um agente de
programação.

```
Você: (Enter) "Zora, revise a arquitetura do projeto de automação."
Zora: consulta o Second Brain, envia a tarefa ao OpenCode e responde falando.
```

## Por que Python 3.12?

O `faster-whisper` depende de `ctranslate2` e `onnxruntime`, que **não têm wheels
para Python 3.14 nem para macOS Intel recente**. Este projeto fixa Python 3.12
(`>=3.12,<3.13`), gerenciado pelo `uv`, sem alterar o Python do sistema.

## Requisitos

- macOS (testado em 14.8.4 Intel x86_64)
- [uv](https://docs.astral.sh/uv/)
- OpenCode >= 1.18 (`opencode --version`)
- `ffmpeg`, `say`/`afplay` (nativos do macOS)
- PortAudio para microfone: `brew install portaudio`
- Um provider configurado no OpenCode (ex.: DeepSeek via `opencode auth login`)

## Instalação

```bash
cd /Users/talita/zora
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"

# disponibiliza o comando `zora` no PATH (opcional)
uv tool install --python 3.12 --editable .
```

Crie/revise a configuração:

```bash
zora config --init      # cria ~/.config/zora/config.toml
zora config             # mostra caminhos e configuração efetiva
zora doctor             # diagnostica ambiente, servidor, voz e Second Brain
```

Segredos **nunca** vão no TOML. Use `~/.config/zora/.env` ou variáveis de
ambiente (veja `.env.example`).

## Modelo (DeepSeek)

O modelo é resolvido pelo OpenCode, no formato `provider/model`:

```toml
[opencode]
model = "deepseek/deepseek-flash"
```

Troque em tempo de execução com `/modelo deepseek/deepseek-v4-pro`. A arquitetura
não depende do DeepSeek: qualquer provider/model conectado ao OpenCode funciona.

## Uso

```bash
# dentro do diretório do projeto
zora
```

- **Modo contínuo (padrão):** fale sem apertar Enter. A Zora ouve, executa,
  responde e volta a ouvir. A fala termina após **5s de silêncio**.
- **Legendas ao vivo:** a transcrição parcial aparece enquanto você fala
  (a cada ~1,5s); no fim, a Zora usa a transcrição completa.
- **M** pausa/retoma o microfone (útil quando você não vai falar).
- **Esc** interrompe a fala/execução na hora e volta a ouvir.
- **T** volta ao modo texto (ou diga **"parar"**, **"para"**, **"modo texto"**).
- Diga **"sair"** para encerrar.
- A transcrição é enviada **direto**, sem pedido de confirmação
  (`stt.confirm_transcript = false`).
- **Enter** envia o texto digitado; com o campo vazio, grava uma instrução por voz (push-to-talk).
- Digitar texto envia a instrução diretamente.
- **Ctrl+C** interrompe a execução atual ou pausa a escuta contínua.

### Execução única (sem interface)

```bash
zora ask "revise o README e sugira melhorias"
zora ask --no-voice "responda apenas: ok"
```

`zora ask` reaproveita a mesma conversa/sessão do projeto e é útil para scripts e
smoke tests.

### Comandos

| Comando | Função |
|---|---|
| `/ajuda` | lista comandos |
| `/status` | servidor, sessão, modelo, memória e voz |
| `/buscar <termo>` | pesquisa no Second Brain |
| `/nota <título>` | lê uma nota |
| `/indexar` | reindexa o Second Brain (forçado) |
| `/novo` | nova conversa + nova sessão OpenCode |
| `/modelo <provider/model>` | troca o modelo |
| `/modo [nome]` | lista ou troca o modo ativo |
| `/voz on\|off\|edge\|say\|none` | controla a voz |
| `/continuo on\|off` | escuta contínua sem Enter |
| `/fala` | volta para a escuta contínua |
| `/texto` | volta para o modo texto (digitar) |
| `/preferencia <texto>` | aprende uma preferência (taste) |
| `/preferencias` | mostra as preferências registradas |
| `/confirmar on\|off` | pede (ou não) confirmação ao criar nota |
| `/autoaprovar on\|off` | auto-aprova ferramentas do OpenCode (perigoso) |
| `/interromper` | aborta a execução atual |
| `/sessao` | mostra o ID da sessão |
| `/contexto on\|off` | liga/desliga contexto automático do Second Brain |
| `/historico` | últimos turnos da conversa |
| `/sair` | encerra |

### Modos

A Zora tem quatro modos, que mudam a diretriz enviada ao OpenCode:

- **academica** — pesquisa, mestrado, leitura, escrita e metodologia.
- **carreira** — emprego, currículo, vagas e entrevistas.
- **assistente** — agenda, rotinas, compromissos e prioridades.
- **conversa** — conversa informal, acolhedora e íntima.

O modo ativo é persistido entre sessões e pode ser trocado com `/modo <nome>`.
O padrão é definido em `[interface] default_mode`.

### Voz

- **STT:** `faster-whisper` local, pt-BR, CPU `int8` (configurável: `base`,
  `small`, `medium`...). O primeiro uso baixa o modelo do Hugging Face.
- **TTS:** `edge` (online, voz `pt-BR-FranciscaNeural`) por padrão, com fallback
  automático para `say` (offline, voz `Luciana`) se a rede falhar. Para uso 100%
  offline: `/voz say`.
- **Modo contínuo (padrão):** o microfone fica aberto e ouve sem limite de
  tempo. A fala termina após **5s de silêncio** (`audio.silence_ms`) ou quando
  você aperta **Enter** para iniciar/encerrar manualmente. Diga **"parar"** ou
  **"modo texto"** para sair; **"sair"** encerra. Desligue com `/continuo off`.
- **Transcrição parcial ao vivo:** enquanto você fala, trechos parciais são
  exibidos a cada `stt.partial_interval_seconds` (1,5s), usando uma janela de
  `stt.partial_window_seconds` (10s). Ao final, a transcrição completa substitui
  as parciais e é enviada ao agente.
- Uma fala não tem limite de duração por padrão (`audio.max_seconds = 0`).
- **Sem confirmação de transcrição:** o texto reconhecido vai direto para a Zora
  (`stt.confirm_transcript = false`). Se ouvir errado, diga "parar" e corrija.
- Áudios de entrada **nunca** saem da máquina; o STT é local.

### Iniciativa e lembretes

A Zora pode tomar iniciativa **enquanto está aberta**:

- saúda você ao iniciar (respeitando horário de silêncio);
- avisa lembretes que você criou;
- após um tempo sem interação, faz um check-in curto (limite diário e
  intervalo mínimo).

```text
"me lembre de beber água em 30 minutos"
"lembre-me de estudar amanhã às 9h"
"me avise de ligar daqui a 2 horas"

/lembrar pagar o boleto hoje às 18h
/lembretes
/cancelar 3
/proativo on|off
```

Configuração em `[proactive]`:

```toml
[proactive]
enabled = true
startup_greeting = true
idle_checkin = true
idle_minutes = 45
min_interval_minutes = 60
daily_max = 4
quiet_hours = "23:00-08:00"
check_interval_seconds = 30
reminders = true
```

Os lembretes ficam no SQLite local e disparam quando a Zora está rodando; se ela
estiver fechada na hora, você os recebe na próxima abertura.

### Preferências (taste)

`~/SecondBrain/preferences.md` é lido em **todo turno** e injetado no prompt
(nome, idioma, tom, formalidade etc.). Ele evolui com o tempo:

```bash
# no terminal da Zora
/preferencia prefiro respostas ainda mais curtas
/preferencias          # mostra o que está registrado

# ou por linguagem natural
"anote como preferência: evite jargões"
"lembre que eu prefiro café sem açúcar"
```

O arquivo é aditivo (deduplicado) e ganha backup antes de cada alteração.

### Second Brain

Configure o caminho em `secondbrain.path` (padrão `~/SecondBrain`). A base de
exemplo já vem criada. O índice fica fora da base (`~/.local/share/zora/sb_index.db`).

- Busca textual com **SQLite FTS5** (offline, incremental).
- Links internos no padrão `[[Nome da Nota]]`; tags via frontmatter YAML ou `#tag`.
- **Criar nota não pede confirmação por padrão** (`confirm_create = false`) e é
  aditivo. **Editar/excluir continuam pedindo** (`require_confirmation = true`),
  geram backup em `.zora/backups` e exclusões vão para `.zora/trash`.
- A Zora nunca escreve fora da raiz configurada (proteção contra path traversal).

Exemplos:

```
"registre a decisão: vamos usar OpenCode como motor de execução"
"anote que o deploy precisa de backup antes de migrar"
"Zora, continue aquele projeto de automação que discutimos antes"
```

### WhatsApp

Integração com o WhatsApp Cloud API (Meta) para enviar texto e áudio:

```bash
zora whatsapp send "bom dia"
zora whatsapp audio ~/caminho/voz.mp3
zora whatsapp serve      # webhook para receber mensagens
```

Configure `ZORA_WHATSAPP_*` no `.env` (veja `.env.example`). O `serve` precisa de
um túnel HTTPS (ngrok/cloudflared) para a Meta alcançar o webhook. Para mensagem
de voz nativa, converta o áudio para OGG/Opus; MP3 funciona como áudio comum.

### Telegram

Alternativa mais simples ao WhatsApp (sem número dedicado):

1. No Telegram, fale com **@BotFather**, envie `/newbot` e copie o **token**.
2. Coloque `ZORA_TELEGRAM_BOT_TOKEN` e `ZORA_TELEGRAM_ENABLED=true` no `.env`.
3. Fale com o seu bot uma vez e rode `zora telegram updates` para descobrir o
   `chat_id`; defina em `ZORA_TELEGRAM_ALLOWED_CHAT_ID` se quiser fixar.
4. Teste com:

```bash
zora telegram me
zora telegram send "bom dia"
zora telegram serve
```

`zora telegram serve` fica ouvindo texto e áudio e executa cada mensagem na
Zora (OpenCode + Second Brain), respondendo no próprio Telegram quando termina.
O `poll` usa long polling (não precisa de túnel HTTPS). Áudio funciona via
`zora telegram audio arquivo.mp3`; para respostas também como mensagem de voz
(OGG/Opus, requer `ffmpeg`), defina `ZORA_TELEGRAM_VOICE_REPLIES=true`.

Para a Zora **editar arquivos** pelo Telegram, as ferramentas do OpenCode
precisam de aprovação. Como o serviço não tem terminal, habilite
`ZORA_OPENCODE_AUTO_APPROVE=true` (perigoso; use só com o bot restrito ao seu
`chat_id`). Sem isso, ações que exigem permissão serão recusadas.

### Spotify

Controle o Spotify no macOS (app instalado):

```bash
zora spotify status
zora spotify toggle
zora spotify next
zora spotify volume 40
```

Por voz: "pausa a música", "próxima música", "aumenta o volume". Se não estiver
instalado: `brew install --cask spotify`.

## Arquitetura

```
zora/
  config.py              # TOML + .env + defaults, paths
  audio/recorder.py      # microfone push-to-talk + VAD por RMS
  stt/faster_whisper.py  # transcrição local pt-BR
  tts/                   # edge-tts + say, limpeza de Markdown, fallback
  opencode/client.py     # HTTP real: sessions, prompts, SSE, permissions
  opencode/events.py     # parser SSE e parts
  secondbrain/index.py   # FTS5 incremental
  secondbrain/notes.py   # criar/ler/editar com confirmação + backup
  memory/store.py        # SQLite: conversas, turnos, registros
  controller/            # intents + orquestrador
  interface/terminal.py  # UI Rich
  cli.py                 # `zora`, doctor, config, index, search, note
tests/
```

O servidor OpenCode é gerenciado por projeto: a Zora escolhe uma porta estável
derivada do diretório e reaproveita a sessão anterior (`resume_session`). Para
usar um servidor já existente, defina `opencode.base_url`.

## Testes

```bash
.venv/bin/python -m pytest
```

Os testes cobrem configuração, intenções, índice/busca/notas do Second Brain,
memória, parsing de eventos e payloads do OpenCode — sem exigir microfone,
modelo baixado ou rede.

## Velocidade

Ajustes que reduzem latência (sem trocar de modelo):

```toml
[interface]
concise = true              # pede respostas diretas, menos exploração

[secondbrain]
confirm_create = false      # registrar ideia não pergunta
reindex_on_start = false    # não varre a base a cada início (use /indexar)

[stt]
model = "base"              # mais rápido que "small" (menos preciso)
```

- O streaming retorna assim que o texto do assistente chega, sem espera extra.
- O índice só é reconstruído no primeiro uso ou quando você roda `/indexar`.
- Para respostas faladas mais rápidas e offline, use `/voz say`.
- `opencode.auto_approve = false` por padrão. Ativar isso remove as perguntas de
  ferramentas do OpenCode, mas **auto-aprova comandos/edições** — use com cautela.

## Segurança e privacidade

- API keys/segredos apenas em `.env`/ambiente; o config é redigido ao exibir.
- STT local; o TTS `edge` envia apenas o texto da resposta (use `say` para offline).
- O servidor escuta em `127.0.0.1`; opcionalmente protegido por
  `OPENCODE_SERVER_PASSWORD` (HTTP Basic).
- Nenhum comando é executado diretamente da transcrição: tudo passa pelo agente e
  pelas permissões do OpenCode.
- Criação de nota é aditiva e não pede confirmação por padrão; edição/exclusão
  continuam exigindo confirmação e geram backup.
- `opencode.auto_approve` é opt-in e vem desligado: por padrão as permissões de
  ferramentas do OpenCode são respeitadas.

## Roadmap incremental

- [x] Fase 1: diagnóstico + arquitetura + API real validada
- [x] Fase 2: MVP de voz (push-to-talk, STT, OpenCode, resposta)
- [x] Fase 3: Second Brain (índice FTS5, busca, criação, links, contexto)
- [x] Fase 4: contexto automático + registro de decisões
- [x] Fase 5 (parcial): TTS com interrupção, `/interromper`, contexto persistente
- [ ] Conversa contínua / wake word
- [ ] Steering nativo (`/api/session/{id}/prompt` com `delivery=steer`)
- [ ] Busca semântica opcional (embeddings locais)

## Troubleshooting

- `zora doctor` mostra o que está faltando.
- **Microfone:** conceda permissão de Microfone ao Terminal em Ajustes do Sistema.
- **faster-whisper:** confirme que está rodando no `.venv` Python 3.12.
- **Servidor:** se houver um OpenCode em outra pasta usando a mesma porta, ajuste
  `opencode.base_url` ou encerre o servidor antigo.
- **Voz robótica/offline:** `/voz say`; **voz natural:** `/voz edge`.
