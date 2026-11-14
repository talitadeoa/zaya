# Zaya

**Uma agente que mora no seu Mac e presta atenção.**

Você fala. Ela ouve, pensa, mexe no computador se precisar e responde com voz.
Não é um chat com microfone acoplado: é uma agente com nome, memória e
iniciativa, que roda inteira na sua máquina e vai acumulando contexto sobre a
sua vida — os projetos, a pesquisa, a agenda, as ideias soltas.

```text
Você   zaya, o que eu tenho hoje?
Zaya   abre a Agenda, cruza com o que você anotou ontem
       e responde falando, sem te fazer repetir nada.

Você   revisa a arquitetura daquele projeto de automação?
Zaya   encontra o repositório, lê os arquivos, propõe a mudança,
       mostra o diff e explica o que fez.
```

Ela não finge que sabe. Quando não sabe, procura. Quando vai mexer em algo,
pergunta. Quando você some por um tempo, ela continua ali — lembrando do que
você pediu, cuidando do dia, esperando a próxima conversa.

---

## O que faz dela uma agente

A diferença entre um assistente e uma agente é o que acontece entre uma pergunta
e a resposta.

Uma agente **ouve** o que você disse, **entende** o que precisa ser feito,
**escolhe** as ferramentas certas, **executa**, **confere** o resultado e só
então **fala**. Tudo isso em loop, sozinha, com você no comando.

A Zaya faz exatamente esse caminho:

- **Ouve localmente.** O áudio nunca sai da máquina. O `faster-whisper`
  transcreve em pt-BR, em CPU, com VAD para não cortar sua fala no meio.
- **Pensa com o modelo que você escolher.** DeepSeek, OpenAI, Anthropic,
  OpenRouter, Ollama local ou qualquer endpoint compatível. A chave é sua, a
  conversa é sua.
- **Age de verdade.** Lê e edita arquivos, roda comandos, pesquisa no Second
  Brain, controla o Spotify, abre o navegador com seus logins, conversa com a
  Agenda.
- **Lembra.** Conversas, decisões, notas, preferências e o resumo de cada dia
  ficam guardados em SQLite e Markdown, prontos para serem recuperados quando
  fizer sentido.
- **Tem iniciativa.** Ela não espera você lembrar: dá bom dia, avisa do
  lembrete, fecha o dia, manda as vagas da semana.

E, principalmente: ela **fala de volta**. Com voz natural, no seu ritmo, e com
personalidade suficiente para não soar como um formulário.

---

## Um dia com a Zaya

Nada disso é roteiro de demonstração. É o uso normal.

```text
08:00   bom dia, Zaya
        → ela responde na hora, sem gastar token, e já sabe que horas são

08:02   me lembra de beber água em 30 minutos
        → lembrete salvo no SQLite; dispara quando der a hora, mesmo em voz

09:15   o que tem na Agenda hoje?
        → lê as tarefas do dia, sem duplicar, e comenta o que é prioridade

11:40   anota: o deploy precisa de backup antes da migração
        → nota criada no Second Brain, com índice e link

14:00   continua aquele projeto de ontem
        → ela retoma a sessão, o contexto e o fio da conversa

18:30   pausa a música
        → Spotify obedece

23:00   (você nem pediu)
        → ela consolida o dia numa nota de memória, já indexada

23:05   sair
        → tchau de verdade: consolida o que faltou e encerra
```

---

## Começando

Você precisa de macOS, [uv](https://docs.astral.sh/uv/), `ffmpeg`, PortAudio e
uma chave de API de algum provedor de modelo. O Python fica travado em 3.12
porque o `faster-whisper` ainda não tem wheels para versões mais novas.

```bash
brew install ffmpeg portaudio

git clone https://github.com/talitadeoa/zaya.git && cd zaya
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"

# opcional: deixar o comando `zaya` disponível no PATH
uv tool install --python 3.12 --editable .
```

Primeiro contato:

```bash
zaya config --init   # cria ~/.config/zaya/config.toml
zaya doctor          # confere ambiente, provedor, voz e Second Brain
zaya                 # abre a interface
```

Segredos vão em `~/.config/zaya/.env` ou no ambiente — nunca no TOML. O arquivo
`.env.example` tem todas as variáveis, comentadas uma a uma.

O cérebro dela é escolhido no `config.toml`:

```toml
[agent]
provider = "deepseek"        # deepseek | openai | openrouter | ollama | anthropic | custom
model = "deepseek-chat"
```

E a chave, no `.env`:

```bash
ZAYA_AGENT_API_KEY=...       # vale para qualquer provedor
# ou as chaves nativas: DEEPSEEK_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY
```

Se um provedor não estiver nos presets, use `provider = "custom"` e informe o
`base_url`. Para trocar sem editar nada, use `/modelo`: sem argumento, lista os
modelos de todos os provedores que têm chave; com o número (`/modelo 3`) ou o
slug (`/modelo openai/gpt-4o`) troca na hora. No app, `F6` abre o mesmo seletor.
A escolha fica salva em `config.toml` (com uma cópia `.bak`) e vale no próximo
turno e na próxima abertura — a não ser que `ZAYA_AGENT_PROVIDER` ou
`ZAYA_AGENT_MODEL` estejam definidos no `.env`, que têm prioridade.

Se preferir abrir com um duplo clique, o `Abrir Zaya.command` já aponta para a
configuração local em `~/zaya`.

---

## Como conversar com ela

A interface padrão é uma TUI feita em Textual: bonita, rápida, com painéis,
histórico, markdown renderizado e seleção de texto.

```bash
zaya            # TUI (padrão)
zaya -old       # terminal clássico, para quem gosta
zaya --watch    # reinicia sozinha quando o código muda
```

**Voz contínua** vem ligada: fale sem apertar nada. A fala termina sozinha
depois de uma pausa de silêncio. `M` pausa ou retoma o microfone, `Esc`
interrompe a resposta, `T` volta ao texto.

**Texto** é só digitar e dar Enter. Enter vazio volta para a voz. E não existe
aquela etapa chata de confirmar transcrição: o que ela entendeu vai direto para
o agente.

Atalhos que valem a pena memorizar:

| Tecla | O que faz |
|---|---|
| `F1` | paleta de comandos |
| `F2` | sessões anteriores |
| `Tab` / `F3` | trocar de modo |
| `F4` | alternar seleção de texto |
| `F5` | recolher os painéis |
| `Ctrl+C` | copiar a seleção |
| `Ctrl+L` | limpar a tela |

Para scripts ou testes rápidos existe a execução única, que reaproveita a
conversa do projeto:

```bash
zaya ask "revise o README e sugira melhorias"
zaya ask --no-voice "responda apenas: ok"
```

---

## Quem ela é quando muda de modo

A Zaya não tem uma personalidade só — tem quatro, e você troca na hora com
`/modo <nome>` ou `Tab`.

- **pesquisadora** — mestrado, leitura, fichamento, escrita, metodologia.
  Rigor conceitual, referências confiáveis, zero citação inventada.
- **trabalhadora** — emprego, currículo, vagas, entrevistas, negociação.
  Prática, direta, com próximos passos e prazos.
- **investidora** — bolsa, indicadores, orçamento, risco. Explica o raciocínio
  e nunca trata análise como recomendação definitiva.
- **assistente** — agenda, rotinas, prioridades e conversa informal. É o modo
  padrão, e o mais próximo de uma amiga que entende de logística.

Cada modo injeta uma diretriz no prompt, sem mudar a infraestrutura. O modo fica
salvo entre sessões.

Além dos modos, ela **aprende preferências**: seu nome, o idioma, o nível de
formalidade, o que evitar. Você ensina com `/preferencia ...`, ou
simplesmente dizendo *"lembre que eu prefiro respostas curtas"*. Isso vai para
`~/SecondBrain/preferences.md` e entra no prefixo estável do prompt — lido uma
vez, reaproveitado pelo cache.

---

## A memória que te poupa de repetir tudo

O coração da Zaya é o **Second Brain**: uma pasta de Markdown que é sua, no
formato que você quiser, indexada localmente com SQLite FTS5.

```markdown
---
tags: [mestrado, campo]
---

# Diário de campo — aldeia

Discussão sobre parentesco com [[Nota sobre Lévi-Strauss]].
#antropologia
```

- Busca **offline e incremental** — nada de mandar suas notas para a nuvem.
- Links `[[Nome da Nota]]`, tags no frontmatter e `#tag` no corpo.
- Criar nota é **aditivo** e não pede confirmação.
- Editar ou excluir **pede confirmação**, gera backup em `.zaya/backups` e move
  para `.zaya/trash` — nunca para o lixo do sistema.
- Ela **nunca escreve fora da raiz** configurada.

E o contexto não entra em todo turno, o que seria caro e barulhento. A busca só
acontece quando a sua mensagem tem pista de retomada — "lembra", "aquele
projeto", "o que decidimos". Quando acontece, vêm dois ou três trechos curtos,
com teto de tamanho, só naquele turno.

**No fim do dia**, ela transforma os turnos em uma nota de memória:
`Memoria/Memória AAAA-MM-DD.md`, com Síntese, Decisões, Conversas e Registros.
Roda às 23:00, consolida o que faltou ao sair, e recupera o dia anterior se
você abrir no dia seguinte sem ter fechado. Se o modelo falhar, cai num resumo
local determinístico: o dia nunca se perde.

---

## O que ela faz sozinha

Enquanto está aberta, a Zaya não fica só esperando. Ela:

- **dá bom dia** ao iniciar, respeitando horário de silêncio;
- **avisa lembretes** que você pediu, no horário certo;
- **faz um check-in curto** depois de um tempo sem conversa (com limite diário,
  para não virar notificação chata);
- **propõe o ritual do ciclo** nas fases principais da lua, cruzando com as
  manifestações que você anotou na Agenda.

E há automações determinísticas, que **não gastam token**:

**Briefing diário no Telegram.** Às 08:00, bom dia com as tarefas do dia, sem
incluir as rotinas. Ao meio-dia, o que foi concluído, o que falta e como está a
rotina matinal. Segunda, quarta e sexta, o resumo da carteira com a cotação de
cada ativo.

**Rotina semanal de vagas.** Toda segunda às 09:00, ela busca as fontes abertas,
aplica os filtros do seu playbook, deduplica, pontua e manda as novas no
Telegram. Vaga sem link não entra. Semana sem vaga nova também gera mensagem,
porque silêncio também é informação.

```bash
zaya career status     # o que já está registrado
zaya career buscar     # busca agora, sem enviar
zaya career enviar     # busca e manda no Telegram
```

O método inteiro — fontes, filtros, rubrica de score, pipeline do CV — vive em
`~/SecondBrain/cv/playbook-vagas.md`. A Zaya lê esse arquivo antes de opinar.

### Ajustando o ritmo dela

Quase tudo tem um botão. O que costuma valer a pena:

```toml
[proactive]
idle_minutes = 45        # tempo sem conversa antes do check-in
daily_max = 4            # quantas vezes por dia, no máximo
quiet_hours = "23:00-08:00"

[memory]
at = "23:00"             # hora da consolidação diária
use_llm = true           # resumo pelo modelo, com fallback local

[briefing]
morning_at = "08:00"
noon_at = "12:00"
market_days = [0, 2, 4]  # carteira: segunda, quarta, sexta

[career]
days = [0]               # vagas: segunda
at = "09:00"

[stt]
model = "base"           # "small" é mais preciso, mas lento em CPU
```

Cada variável de ambiente correspondente está em `.env.example`, com o mesmo
nome em `ZAYA_*`.

---

## Onde ela aparece

**No terminal**, como TUI ou terminal clássico. É a casa dela.

**No Telegram**, sem precisar de número dedicado:

```bash
zaya telegram updates   # descobre o chat_id
zaya telegram me
zaya telegram serve     # escuta texto e áudio, responde no chat
```

Por padrão, pelo Telegram ela só lê arquivos: como o serviço roda sem terminal
interativo, `write`, `edit` e `bash` são negados. Para liberar edição e execução
pelo chat, ligue o auto-approve **com o chat_id fixado**, para o bot não dar
acesso ao primeiro que falar com ele.

**No WhatsApp**, via Cloud API da Meta:

```bash
zaya whatsapp send "bom dia"
zaya whatsapp audio voz.mp3
zaya whatsapp serve
```

**No Spotify** — `zaya spotify status|toggle|next|volume 40`, ou por voz.

**No navegador**, com Playwright e perfil persistente (seus logins ficam
salvos e reutilizáveis):

```bash
uv pip install --python .venv/bin/python -e ".[browser]"
.venv/bin/playwright install chromium
zaya browser login https://exemplo.com
zaya browser text https://exemplo.com
zaya browser screenshot https://exemplo.com captura.png
```

---

## Confiança, não fé

Uma agente que mexe no seu computador precisa de limites explícitos. Os da Zaya
são estes:

- **Áudio nunca sai da máquina.** O STT é local. Só o texto da resposta vai para
  o TTS `edge` — use `/voz say` para ficar 100% offline.
- **Chaves só em `.env` ou no ambiente.** O config aparece redigido quando
  exibido.
- **Nada da transcrição é executado direto.** Tudo passa pelo agente e pelas
  permissões das ferramentas.
- **Escrita e shell pedem confirmação.** `agent.auto_approve` é opt-in e vem
  desligado.
- **`agent.allow_outside_project=false`** prende as ferramentas ao diretório do
  projeto. Por padrão fica livre, para ela alcançar o Second Brain.

---

## Por baixo do capô

Sem servidor intermediário: o agente fala direto com a API do modelo e executa
as ferramentas localmente.

```text
zaya/
  audio/recorder.py      # microfone + VAD
  stt/faster_whisper.py  # transcrição local
  tts/                   # edge-tts + say, limpeza de Markdown
  agent/                 # loop, providers, ferramentas
  secondbrain/           # índice FTS5 + notas com backup
  memory/store.py        # conversas e turnos (SQLite)
  controller/            # intenções, personas, iniciativa
  interface/             # TUI Textual e terminal Rich
  cli.py                 # zaya, doctor, config, index, search, note
```

As ferramentas do agente são sete: leitura (`read`, `list`, `glob`, `grep`),
escrita (`write`, `edit`) e shell (`bash`). As que alteram algo pedem
confirmação, a menos que você tenha ligado o auto-approve.

**O prompt é montado em duas camadas**, para gastar menos e responder mais
rápido:

1. **prefixo estável** — persona, preferências e diretrizes fixas. Fica no
   começo e é reutilizado pelo cache do provedor.
2. **bloco dinâmico** — data, hora, pedido curto e modo rápido. Vai no fim.

O histórico é comprimido num resumo quando passa do limite. O catálogo de
ferramentas é fixo: todos os modos recebem o mesmo conjunto (`read`, `write`,
`edit`, `list`, `glob`, `grep`, `bash`) em todos os turnos. Não há filtro por
modo nem por tipo de pedido — quem quiser desligar ferramentas usa
`agent.tools = false`. Resultados grandes de ferramenta são vistos inteiros no
turno atual, mas limitados no histórico.

Tudo isso é ajustável em `[agent]` e `[secondbrain]`. `/status` mostra a economia
configurada e, quando o provedor informa usage, os tokens do último turno e o
acumulado da sessão — incluindo quantos vieram de cache. `/tokens` lista os
últimos turnos.

---

## Comandos, para quando você quiser ir direto

| Comando | Função |
|---|---|
| `/ajuda` · `/status` · `/tokens` | comandos · provedor, sessão, modelo · consumo |
| `/buscar <termo>` · `/nota <título>` | Second Brain |
| `/indexar` · `/novo` (`/nova`) · `/consolidar [dia]` | reindexa · nova conversa · memória do dia |
| `/modelo [número\|slug]` · `/modo [nome]` | lista/troca o modelo · troca persona (F6 no app) |
| `/bolsa <ticker>` · `/carteira` | cotação · sua carteira |
| `/metas` · `/painel` · `/foco` | foco do ciclo e metas de longo prazo |
| `/tarefas [sync\|fazer\|concluir\|limpar]` | tarefas de longo prazo |
| `/voz on\|off\|edge\|say\|none` · `/continuo on\|off` | voz e escuta |
| `/fala` · `/texto` · `/interromper` | modo de entrada e controle |
| `/preferencia <texto>` · `/preferencias` | aprende · mostra preferências |
| `/confirmar on\|off` · `/autoaprovar on\|off` | confirmação de notas · ferramentas |
| `/contexto on\|off` · `/historico` · `/sessao` | contexto, histórico e sessão |
| `/reiniciar` · `/sair` | reinicia com código novo · encerra |

A lista completa aparece com `/ajuda` dentro do app.

---

## Se algo não funcionar

- `zaya doctor` mostra o que falta. `--verbose` lista os modelos do provedor.
- **"chave de API ausente"** → defina `ZAYA_AGENT_API_KEY` (ou a chave nativa do
  provedor) em `~/.config/zaya/.env`.
- **"modelo não listado"** → confira `agent.model` contra o `doctor --verbose` e
  reinicie com `/reiniciar`.
- **Microfone mudo** → dê permissão de Microfone ao Terminal em Ajustes do
  Sistema.
- **Voz robótica** → `/voz say` para offline, `/voz edge` para natural.

Testes, sem microfone, download de modelo ou rede:

```bash
.venv/bin/python -m pytest
```

---

## O que vem depois

Já funcionam: voz contínua, agente com ferramentas, Second Brain, contexto
automático, TTS com interrupção, TUI nova, briefing e rotina de vagas.

Na fila: conversa contínua com wake word, steering nativo (mandar uma mensagem
no meio de um turno) e busca semântica local com embeddings.

---

*O comando continua se chamando `zaya` — pacote, pastas e variáveis `ZAYA_*`. O
nome dela é Zaya. A diferença entre os dois é a mesma entre uma pasta e alguém
que mora nela.*
