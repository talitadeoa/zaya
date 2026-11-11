# ZAYA

**A agente pessoal que mora no seu Mac.**

Zaya não é um chat com microfone. É uma agente com identidade, memória e
iniciativa: ela **ouve**, **lembra** do que importa, **age** no seu computador e
**fala** de volta — no seu tom, no seu ritmo. Por baixo, um **agente próprio**
(loop de ferramentas + API do modelo) dá mãos a ela; a Zaya é a pessoa.

```text
Você:  "Zaya, o que eu tenho hoje?"
Zaya:  lê a Agenda, cruza com o Second Brain e responde — falando.

Você:  "revise a arquitetura do projeto de automação"
Zaya:  consulta o contexto do projeto, usa as ferramentas para ler e editar
       arquivos e volta com o resultado, o diff e a explicação.
```

## O que faz dela uma agente

| | |
|---|---|
| 👂 **Ouve** | STT local (`faster-whisper`), pt-BR, sem mandar áudio para fora. |
| 🧠 **Lembra** | Second Brain em Markdown + memória de conversas em SQLite. |
| 🛠️ **Age** | Edita arquivos, roda comandos, navega, mexe na Agenda, no Spotify. |
| 💡 **Tem iniciativa** | Lembretes, check-ins, foco do ciclo e rituais de lua. |
| 🎭 **Tem personalidade** | Quatro modos, preferências aprendidas e tom próprio. |
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

**Requisitos:** macOS, [uv](https://docs.astral.sh/uv/), uma chave de API de
modelo (DeepSeek, OpenAI, Anthropic ou um servidor local compatível com OpenAI),
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
zaya doctor          # ambiente, provedor do agente, voz e Second Brain
zaya                 # abre a interface
```

Segredos ficam em `~/.config/zaya/.env` ou no ambiente — nunca no TOML
(veja `.env.example`).

## O cérebro: agente próprio

O agente fala direto com a API do modelo e executa as ferramentas localmente —
não há servidor intermediário. O config declara provedor e modelo:

```toml
[agent]
provider = "deepseek"        # deepseek | openai | openrouter | ollama | anthropic | custom
model = "deepseek-flash"
```

A chave fica no ambiente ou em `~/.config/zaya/.env`:

```bash
ZAYA_AGENT_API_KEY=...       # vale para qualquer provedor
# ou as chaves nativas: DEEPSEEK_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, ...
```

Exemplos:

```toml
[agent]
provider = "anthropic"
model = "claude-sonnet-4-5"

[agent]
provider = "ollama"          # servidor local, sem chave
model = "llama3.2"
```

Troque em tempo real com `/modelo provider/model`; `zaya doctor --verbose`
consulta a lista de modelos do provedor. Para um endpoint incompatível com os
presets, use `provider = "custom"` e informe `base_url`.

As sessões ficam em `~/.local/share/zaya/sessions/` e são retomadas entre
execuções. O agente tem ferramentas de leitura (`read`, `list`, `glob`, `grep`),
escrita (`write`, `edit`) e shell (`bash`); as que alteram algo pedem
confirmação, a menos que `agent.auto_approve=true`.

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
- **Markdown:** as respostas são renderizadas com títulos, ênfase e blocos de
  código destacados. Desligue com `ZAYA_UI_MARKDOWN=false` ou
  `interface.markdown_render=false` para ver o texto cru.

### Execução única

```bash
zaya ask "revise o README e sugira melhorias"
zaya ask --no-voice "responda apenas: ok"
```

Reaproveita a conversa do projeto — ótimo para scripts e smoke tests.

### Comandos

| Comando | Função |
|---|---|
| `/ajuda` · `/status` · `/tokens` | comandos · provedor, sessão, modelo, memória · consumo por turno |
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

Quatro modos mudam a diretriz que ela envia ao motor:

- **pesquisadora** — pesquisa, mestrado, leitura, escrita e método.
- **trabalhadora** — emprego, currículo, vagas e entrevistas.
- **investidora** — bolsa, orçamento e investimentos.
- **assistente** — agenda, rotinas, prioridades e conversa informal.

`/modo <nome>` troca na hora, **Tab/F3** abrem o seletor de modo e o modo fica
salvo entre sessões. O padrão é **assistente** (`[interface] default_mode`).

As **preferências** (`~/SecondBrain/preferences.md`) ficam no prefixo estável do
prompt — lidas uma vez e reaproveitadas pelo cache: nome, idioma, formalidade,
o que evitar. Ela aprende com `/preferencia ...`, "anote como preferência: ..."
ou "lembre que eu prefiro ...".

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

Por padrão o Telegram só lê arquivos: como o serviço roda sem terminal
interativo, `write`/`edit`/`bash` são negados. Para a Zaya editar e executar
pelo chat, ligue `auto_approve` (exige `ZAYA_TELEGRAM_ALLOWED_CHAT_ID` fixado,
para o bot não dar acesso ao primeiro chat que falar com ele):

```bash
# ~/.config/zaya/.env
ZAYA_TELEGRAM_AUTO_APPROVE=true
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

- **08:00** — bom dia com as tarefas do dia, sem incluir as rotinas.
- **12:00** — o que foi concluído, o que falta e como está a rotina matinal.
- **12:00 (seg, qua, sex)** — resumo da carteira (`/carteira`) com a cotação
  de cada ativo, melhor e pior variação do dia. Também é determinístico, sem
  gastar token.

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

# Resumo da carteira de ativos (0 = segunda, 2 = quarta, 4 = sexta).
market_enabled = true
market_days = [0, 2, 4]
market_at = "12:00"
market_until = "18:00"    # depois disso o resumo da bolsa não sai
```

A rotina matinal é encontrada pelo nome (padrão: qualquer rotina que contenha
"matinal"), usando as subtarefas geradas pela Agenda no dia. Se as notas da
rotina ainda não existirem, ela usa o cadastro de rotinas como fallback.

### Rotina semanal de vagas no Telegram

Mesmo desenho do briefing: determinístico, sem gastar token. Toda segunda às
09:00 ela busca as fontes abertas de vaga, aplica os filtros do playbook,
deduplica contra o `state.json`, pontua e manda as novas no Telegram.

```bash
zaya career status          # o que já está registrado
zaya career buscar          # busca agora, sem enviar (--json para processar)
zaya career enviar          # busca e manda no Telegram
```

O método (fontes, filtros, rubrica de score, pipeline do CV) está em
`~/SecondBrain/cv/playbook-vagas.md`; o estado fica em `data/career/state.json`.
Vaga sem link não entra.

```toml
[career]
enabled = true
days = [0]                # 0=seg, 1=ter, 2=qua, 3=qui, 4=sex
at = "09:00"
until = "18:00"           # depois disso não envia mais
max_items = 8             # vagas por mensagem; o resto fica no estado
```

Semana sem vaga nova também gera mensagem. A entrega é marcada por vaga
(`entrega: "entregue"`), então se o Telegram falhar a próxima rodada reenvia em
vez de perder. O slot do dia fica reservado no SQLite e TUI, terminal e serviço
do Telegram não duplicam a rodada.

## Privacidade e segurança

- **Áudio nunca sai da máquina**: o STT é local. Só o texto da resposta vai para
  o TTS `edge` — use `say` para ficar 100% offline.
- **Chaves só em `.env`/ambiente**; o config é redigido ao ser exibido.
- Nada da transcrição é executado direto: tudo passa pelo agente e pelas
  permissões das ferramentas.
- Escrita e shell pedem confirmação; `agent.auto_approve` é opt-in e vem
  desligado.
- `agent.allow_outside_project=false` restringe as ferramentas ao diretório do
  projeto; por padrão ele fica livre para a Zaya alcançar o Second Brain.

## Velocidade e consumo de tokens

O prompt é montado em duas camadas:

1. **prefixo estável** — persona, preferências e diretrizes fixas. Fica no
   começo e é reutilizado pelo cache do provedor (nos provedores compatíveis
   com OpenAI o cache é automático; a Anthropic recebe `cache_control`).
2. **bloco dinâmico** — data/hora, pedido curto e modo rápido, sempre no fim.

O contexto do Second Brain **não** entra em todo turno: a busca só acontece
quando a mensagem tem pista de retomada ("lembra", "aquele projeto", "o que
decidimos"). Quando acontece, vêm no máximo 2–3 trechos curtos, com teto total,
e o contexto usado fica só naquele turno — no histórico fica apenas a mensagem
original. Metas seguem a mesma regra. O histórico é comprimido num resumo
quando passa de `max_history_messages` mensagens **ou** de
`max_history_chars` caracteres, e o catálogo de ferramentas é filtrado por
sessão (leitura sempre; escrita/shell entram quando o pedido pede ação e ficam
disponíveis até trocar de sessão/modo — assim o prefixo não muda a cada turno).
Resultados e argumentos grandes de ferramenta (um `read` de arquivo enorme ou
um `write` com muito conteúdo, por exemplo) são vistos inteiros no turno atual,
mas ficam limitados a `tool_result_chars` no histórico.

O resumo do histórico tem dois modos:

- `history_summary = "local"` — extrativo e determinístico: nunca falha, não
  gasta chamada extra, mas pode perder nuance de decisões antigas.
- `history_summary = "llm"` — pede um resumo semântico ao modelo (preservando
  decisões, nomes, prazos e pendências), com fallback automático para o local
  se a chamada falhar ou demorar mais de 20 s. O custo dessa chamada entra no
  `/tokens` do turno.

```toml
[agent]
fast_model = "deepseek-chat"      # pedidos curtos vão para o modelo pequeno
tool_filter = true                # só as ferramentas que o turno precisa
cache_prompt = true               # cacheia persona + preferências
max_history_messages = 20         # depois disso, resumo do histórico
max_history_chars = 24000         # teto de tamanho do histórico
summary_chars = 1200
usage = true                      # mede tokens e mostra no /status
tool_result_chars = 4000          # resultado grande não fica no histórico
history_summary = "local"         # local (extrativo) | llm (semântico)
history_summary_model = ""        # vazio = modelo principal

[secondbrain]
context_results = 2               # 2–3 trechos bastam
context_chars = 480               # por trecho
context_max_chars = 1200          # teto do bloco inteiro
```

`/status` mostra a economia configurada e, quando o provedor informa usage,
os tokens do último turno e o acumulado da sessão, incluindo quantos vieram
de cache. `/tokens` lista os últimos 10 turnos e o total da sessão. Se o
endpoint recusar `stream_options`, a Zaya repete a chamada sem medição em vez
de falhar. Em endpoints que não reportam usage, o `/status` avisa que não há
medição.

Com tudo desligado, a Zaya volta ao comportamento antigo: contexto sempre que
a palavra-chave aparece, histórico inteiro e catálogo completo de ferramentas.

```toml
[interface]
concise = true              # respostas diretas, menos exploração
selection_mode = true       # seleção de texto ligada: arraste destaca e copia
markdown_render = true      # respostas com títulos, ênfase e código destacados

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
  agent/                 # loop, providers (OpenAI/Anthropic), ferramentas
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

Cobrem config, intenções, Second Brain, memória, provedores, ferramentas e
eventos do agente — sem microfone, download de modelo ou rede.

## Se algo não funcionar

- `zaya doctor` mostra o que falta.
- **“chave de API ausente”** → defina `ZAYA_AGENT_API_KEY` (ou a chave nativa do
  provedor) em `~/.config/zaya/.env`.
- **“modelo não listado”** → confira `agent.model` contra `zaya doctor --verbose`
  e reinicie com `/reiniciar`.
- **Microfone** → permissão de Microfone para o Terminal em Ajustes do Sistema.
- **Voz robótica** → `/voz say`; natural → `/voz edge`.

## Roadmap

- [x] Fase 1: diagnóstico + API real validada
- [x] Fase 2: voz (push-to-talk, STT, agente, resposta)
- [x] Fase 3: Second Brain (FTS5, busca, criação, contexto)
- [x] Fase 4: contexto automático + registro de decisões
- [x] Fase 5: TTS com interrupção, `/interromper`, contexto persistente
- [ ] Conversa contínua com wake word
- [ ] Steering nativo (mensagem no meio de um turno)
- [ ] Busca semântica local (embeddings)
