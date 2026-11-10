# ZAYA

**A agente pessoal que mora no seu Mac.**

Zaya não é um chat com microfone. É uma agente com identidade, memória e
iniciativa: ela **ouve**, **lembra** do que importa, **age** no seu computador e
**fala** de volta — no seu tom, no seu ritmo. Por baixo, o **OpenCode** é o motor
que dá mãos a ela; a Zaya é a pessoa.

```text
Você:  "Zaya, o que eu tenho hoje?"
Zaya:  lê a Agenda, cruza com o Second Brain e responde — falando.

Você:  "revise a arquitetura do projeto de automação"
Zaya:  consulta o contexto do projeto, envia a tarefa ao OpenCode
       e volta com o resultado, o diff e a explicação.
```

## O que faz dela uma agente

| | |
|---|---|
| 👂 **Ouve** | STT local (`faster-whisper`), pt-BR, sem mandar áudio para fora. |
| 🧠 **Lembra** | Second Brain em Markdown + memória de conversas em SQLite. |
| 🛠️ **Age** | Edita arquivos, roda comandos, navega, mexe na Agenda, no Spotify. |
| 💡 **Tem iniciativa** | Lembretes, check-ins, foco do ciclo e rituais de lua. |
| 🎭 **Tem personalidade** | Cinco modos, preferências aprendidas e tom próprio. |
| 🗣️ **Fala** | TTS natural (`edge`) com fallback offline (`say`). |

## Um dia com a Zaya

```text
"bom dia"                         → saudação na hora, sem chamar o modelo
"me lembre de beber água em 30min" → lembrete local, dispara quando der a hora
"o que tem na Agenda hoje?"        → tarefas do dia, sem duplicar
"anote: deploy precisa de backup"  → nota no Second Brain, com link e índice
"continua aquele projeto de ontem" → retoma a sessão e o contexto
"pausa a música"                   → Spotify
"sair"                             → tchau de verdade
23:00                              → consolida o dia numa nota de memória
```

## Começando

**Requisitos:** macOS, [uv](https://docs.astral.sh/uv/), OpenCode 1.18+,
`ffmpeg` e PortAudio (`brew install portaudio`). Python fica fixo em 3.12
(`faster-whisper` ainda não tem wheels para 3.14).

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"

# comando `zaya` no PATH (opcional)
uv tool install --python 3.12 --editable .
```

Primeiro contato:

```bash
zaya config --init   # cria ~/.config/zaya/config.toml
zaya doctor          # ambiente, servidor, voz e Second Brain
zaya                 # abre a interface
```

Segredos ficam em `~/.config/zaya/.env` ou no ambiente — nunca no TOML
(veja `.env.example`).

## O cérebro: modelo e OpenCode

O modelo é declarado no config da Zaya, no formato `provider/model`:

```toml
[opencode]
model = "deepseek/deepseek-flash"
```

O OpenCode resolve o provider. Qualquer modelo conectado funciona — DeepSeek,
OpenCode, OpenAI, Anthropic, local. Troque em tempo real com
`/modelo provider/model`; `zaya doctor --verbose` lista os disponíveis.

> **Modelos `*-free` têm cota.** Se aparecer `Free usage limit reached`,
> conecte um provider (ex.: DeepSeek) e aponte o `model` para ele.

A Zaya mantém o servidor OpenCode vivo entre execuções, numa porta estável por
projeto, e guarda a credencial local em arquivo `0600` — abrir a Zaya de novo é
quase instantâneo.

## Conversando

```bash
zaya            # interface padrão (TUI)
zaya -old       # terminal completo
zaya --watch    # reinicia sozinha quando o código muda
```

- **Voz contínua (padrão):** fale sem apertar Enter; a fala termina após ~5 s de
  silêncio. **M** pausa/retoma, **Esc** interrompe, **T** volta ao texto.
- **Texto:** digite e dê Enter. Enter vazio (ou `/fala`) volta para a voz.
- **Sem confirmação de transcrição:** o texto reconhecido vai direto para ela.
- Diga **"parar"**, **"modo texto"** ou **"sair"** — ou use os atalhos.
- **M** também desliga o microfone sozinho após 30 s de silêncio
  (`ZAYA_AUDIO_AUTO_MUTE_SECONDS`; `0` desativa).

### Execução única

```bash
zaya ask "revise o README e sugira melhorias"
zaya ask --no-voice "responda apenas: ok"
```

Reaproveita a conversa do projeto — ótimo para scripts e smoke tests.

### Comandos

| Comando | Função |
|---|---|
| `/ajuda` · `/status` | comandos disponíveis · servidor, sessão, modelo, memória |
| `/buscar <termo>` · `/nota <título>` | Second Brain |
| `/indexar` · `/novo` · `/consolidar [dia]` | reindexa · nova conversa · memória do dia |
| `/modelo <provider/model>` · `/modo [nome]` | troca modelo · troca persona |
| `/bolsa <ticker>` · `/carteira` | cotação · sua carteira |
| `/metas` · `/painel` · `/foco` | foco do ciclo e metas de longo prazo |
| `/tarefas [sync\|fazer\|concluir\|limpar]` | tarefas de longo prazo |
| `/voz on\|off\|edge\|say\|none` · `/continuo on\|off` | voz e escuta |
| `/fala` · `/texto` · `/interromper` | modo de entrada e controle |
| `/preferencia <texto>` · `/preferencias` | aprende · mostra preferências |
| `/confirmar on\|off` · `/autoaprovar on\|off` | confirmação de notas · ferramentas |
| `/contexto on\|off` · `/historico` · `/sessao` | contexto, histórico e sessão |
| `/reiniciar` · `/sair` | reinicia com código novo · encerra |

## Personalidade

Cinco modos mudam a diretriz que ela envia ao motor:

- **academica** — pesquisa, mestrado, leitura, escrita e método.
- **carreira** — emprego, currículo, vagas e entrevistas.
- **assistente** — agenda, rotinas, compromissos e prioridades.
- **conversa** — informal, acolhedora, íntima.
- **financas** — bolsa, orçamento e investimentos.

`/modo <nome>` troca na hora, **Tab/F3** percorre os modos e o modo fica salvo
entre sessões. O padrão é **conversa** (`[interface] default_mode`).

As **preferências** (`~/SecondBrain/preferences.md`) são lidas em todo turno:
nome, idioma, formalidade, o que evitar. Ela aprende com `/preferencia ...`,
"anote como preferência: ..." ou "lembre que eu prefiro ...".

## Memória

**Second Brain** é uma pasta de Markdown sua (padrão `~/SecondBrain`), indexada
com SQLite FTS5 — busca offline e incremental. Links `[[Nome da Nota]]`, tags no
frontmatter ou `#tag`.

- Criar nota é **aditivo** e não pede confirmação por padrão.
- Editar/excluir **pedem confirmação**, geram backup em `.zaya/backups` e vão
  para `.zaya/trash` — nunca para o lixo do sistema.
- Ela **nunca escreve fora da raiz** configurada.
- `reindex_on_start = false`: o índice só é refeito no primeiro uso ou em
  `/indexar`.

A memória operacional (conversas, turnos, registros) fica em SQLite, em
`~/.local/share/zaya/`.

### Memória diária (consolidação)

No fim do dia, a Zaya transforma os turnos em uma nota no Second Brain —
`Memoria/Memória AAAA-MM-DD.md`, já indexada e recuperável nos dias seguintes.

- Roda às **23:00** (enquanto ela está aberta) e, se você fechar depois disso,
  consolida o que faltou ao sair.
- Ao abrir no dia seguinte, **recupera o dia anterior** se ele não foi
  consolidado.
- Funciona na TUI, no terminal e no serviço do Telegram (worker próprio,
  independente do modo proativo).
- O resumo é feito pelo modelo; se o provedor falhar, cai num resumo local
  determinístico (nunca perde o dia).
- Manual: `/consolidar`, `/consolidar ontem` ou `/consolidar 2026-11-09`;
  `/consolidar forcar` refaz a nota com o que houver.

```toml
[memory]
enabled = true
at = "23:00"          # horário local
on_exit = true
use_llm = true        # resumo pelo modelo (com fallback local)
folder = "Memoria"
```

A nota separa **Síntese**, **Decisões**, **Conversas** e **Registros** (notas e
coisas criadas no dia).

## Integrações

**Telegram** — sem número dedicado:

```bash
zaya telegram updates        # descobre o chat_id
zaya telegram me
zaya telegram serve          # responde texto e áudio
```

**WhatsApp Cloud API** — envio e webhook:

```bash
zaya whatsapp send "bom dia"
zaya whatsapp audio voz.mp3
zaya whatsapp serve
```

**Spotify** — `zaya spotify status|toggle|next|volume 40`; por voz também.

**Navegador (Playwright)** — perfil persistente com seus logins:

```bash
uv pip install --python .venv/bin/python -e ".[browser]"
.venv/bin/playwright install chromium
zaya browser login https://www.upwork.com
zaya browser text https://exemplo.com
zaya browser screenshot https://exemplo.com captura.png
```

**Agenda e tarefas** — lê a página *"tarefas pra zaya"*, sincroniza o status e
acompanha o dia. `/tarefas sync` quando quiser; para checagem automática,
`ZAYA_AGENDA_TASKS_POLL_SECONDS=30`.

**Metas, foco e lua** — `/metas`, `/foco definir semanal <texto>`; nas fases
principais da lua ela propõe o ritual do ciclo.

## Iniciativa

Enquanto está aberta, a Zaya pode:

- saudar você ao iniciar (respeitando horário de silêncio);
- avisar lembretes que você pediu;
- fazer um check-in curto após um tempo sem interação.

```text
"me lembre de beber água em 30 minutos"
"me avise de ligar daqui a 2 horas"

/lembrar pagar o boleto hoje às 18h
/lembretes
/cancelar 3
/proativo on|off
```

Os lembretes ficam no SQLite local; se ela estiver fechada na hora, você recebe
na próxima abertura. Ajustes em `[proactive]` (intervalo, limite diário,
`quiet_hours`).

### Briefing diário no Telegram

Automação determinística, sem gastar token: lê a Agenda e envia duas mensagens
por dia para o seu chat do Telegram.

- **08:00** — bom dia com as tarefas do dia e os hábitos da rotina matinal
  (marcando o que já foi feito), perguntando se você cumpriu a rotina.
- **12:00** — o que foi concluído, o que falta e como está a rotina matinal.

Funciona na TUI, no terminal e no serviço do Telegram; roda com qualquer um
deles aberto. O slot do dia fica marcado no SQLite, então TUI e serviço abertos
ao mesmo tempo não duplicam a mensagem. Se a Agenda falhar, tenta de novo depois.

```toml
[briefing]
enabled = true
morning_at = "08:00"
noon_at = "12:00"
noon_until = "18:00"      # depois disso o resumo do meio-dia não sai
morning_routine = "matinal"  # trecho do nome da rotina na Agenda
max_tasks = 15
```

A rotina matinal é encontrada pelo nome (padrão: qualquer rotina que contenha
"matinal"), usando as subtarefas geradas pela Agenda no dia. Se as notas da
rotina ainda não existirem, ela usa o cadastro de rotinas como fallback.

## Privacidade e segurança

- **Áudio nunca sai da máquina**: o STT é local. Só o texto da resposta vai para
  o TTS `edge` — use `say` para ficar 100% offline.
- **Chaves só em `.env`/ambiente**; o config é redigido ao ser exibido.
- O servidor OpenCode escuta em `127.0.0.1`, com senha local e permissões
  restritas.
- Nada da transcrição é executado direto: tudo passa pelo agente e pelas
  permissões do OpenCode.
- `auto_approve` é opt-in e vem desligado.

## Velocidade

```toml
[interface]
concise = true              # respostas diretas, menos exploração

[secondbrain]
confirm_create = false
reindex_on_start = false    # use /indexar quando quiser

[stt]
model = "base"              # mais rápido que "small"
```

Saudações, hora e data respondem sem LLM. O streaming mostra o texto assim que
chega. Para voz falada mais rápida e offline: `/voz say`.

## Como ela é feita

```text
zaya/
  audio/recorder.py      # microfone + VAD
  stt/faster_whisper.py  # transcrição local
  tts/                   # edge-tts + say, limpeza de Markdown
  opencode/client.py     # sessões, prompts, SSE, permissões
  secondbrain/           # índice FTS5 + notas com backup
  memory/store.py        # conversas e turnos (SQLite)
  controller/            # intenções, personas, iniciativa
  interface/             # TUI e terminal Rich
  cli.py                 # zaya, doctor, config, index, search, note
```

O comando continua se chamando `zaya` por enquanto (pacote, pastas `~/.config/zaya`
e variáveis `ZAYA_*`); o **nome dela** é Zaya.

## Testes

```bash
.venv/bin/python -m pytest
```

Cobrem config, intenções, Second Brain, memória, eventos/payloads do OpenCode —
sem microfone, download de modelo ou rede.

## Se algo não funcionar

- `zaya doctor` mostra o que falta.
- **“Free usage limit reached”** → modelo `*-free` sem cota; conecte um provider
  e ajuste `model` (ex.: `deepseek/deepseek-flash`). Reinicie com `/reiniciar`.
- **Microfone** → permissão de Microfone para o Terminal em Ajustes do Sistema.
- **Voz robótica** → `/voz say`; natural → `/voz edge`.
- **Servidor** → se houver um OpenCode de outra pasta na mesma porta, ajuste
  `opencode.base_url` ou encerre o antigo.

## Roadmap

- [x] Fase 1: diagnóstico + API real validada
- [x] Fase 2: voz (push-to-talk, STT, OpenCode, resposta)
- [x] Fase 3: Second Brain (FTS5, busca, criação, contexto)
- [x] Fase 4: contexto automático + registro de decisões
- [x] Fase 5: TTS com interrupção, `/interromper`, contexto persistente
- [ ] Conversa contínua com wake word
- [ ] Steering nativo (`delivery=steer`)
- [ ] Busca semântica local (embeddings)
