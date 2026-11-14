# Check-in de memória das 18h: a Zaya pergunta o que guardar

Este documento desenha a **etapa 5** do plano de memória de longo prazo. As etapas
0 a 4 (schema, `fatos.md`, recall por FTS5, ferramentas de memória, bloco no turno)
vêm antes; sem elas o check-in não tem onde gravar nem o que propor.

A ideia é curta: uma vez por dia, no fim da tarde, a Zaya mostra uma lista curta de
coisas que aprendeu sobre a Talita e pergunta o que deve virar memória durável e o
que deve ser esquecido. Ela responde com uma frase de dez caracteres. Nada de
formulário, nada de tela nova.

---

## 1. Problema

O plano Letta-like propõe extração de fatos pela consolidação das 23h. Isso resolve
o registro, mas não a **curadoria**:

- um fato errado extraído pelo modelo fica valendo para sempre;
- um fato sensível ("ansiedade antes de apresentação") entra na memória sem
  ninguém ter escolhido guardá-lo;
- a pessoa nunca vê o que a Zaya acha que sabe sobre ela, então não corrige.

O check-in das 18h existe para dar **uma tela de revisão por dia, sem tela**. É o
único ponto do sistema em que a memória durável é confirmada por quem é o sujeito
dela.

---

## 2. Estado atual (verificado no código)

| Fato | Onde |
| --- | --- |
| Interface padrão é a TUI | `InterfaceConfig.ui = "textual"` (`zaya/config.py:165`) |
| Caminho Textual chama `prepare()`, **não** `start()` | `zaya/cli_runtime.py:73-77` |
| `prepare()` só liga os workers de fundo | `zaya/controller/orchestrator.py:328` |
| `_proactive_loop` **não** nasce na TUI: só em `start()` e em `/proativo` | `zaya/controller/orchestrator.py:382`, `zaya/controller/initiative.py:86` (loop) e `:73` (`/proativo`) |
| Workers de fundo (memória, briefing, vagas) | `zaya/controller/orchestrator.py:207` |
| Envio proativo central | `zaya/controller/initiative.py:242` (`_say_proactive`) |
| Loop de memória: 60s, checa `_busy`, roda em todo mundo | `zaya/controller/memory.py:66` |
| Briefing **não** serve: sai cedo sem Telegram | `zaya/controller/briefing.py:24` |
| Rotina de vagas também exige Telegram | `zaya/controller/career.py:37` |
| Despacho de texto | `zaya/controller/turns.py:91` (`_dispatch`), `:66` (`_dispatch_silent`) |
| `parse_register` engole respostas curtas | `zaya/controller/intents.py:91` |
| Reserva atômica de "já enviei hoje" | `zaya/memory/store.py:510` (`claim_meta`) |
| Consolidação diária | `zaya/memory/consolidation.py:205` (`consolidate`) |

Três conclusões que mudam o desenho.

**O check-in não pode morar no `_proactive_loop`.** A premissa registrada antes
("o loop proativo roda na TUI e no Telegram") está errada. `_proactive_loop` só é
criado em dois lugares: `Orchestrator.start()`
(`zaya/controller/orchestrator.py:382`) e `_toggle_proactive`
(`zaya/controller/initiative.py:73`, usado por `/proativo`). E `start()` é chamado
**só** pelo caminho de terminal (`zaya/cli_runtime.py:58`). O caminho Textual — o
padrão (`ui = "textual"`) — chama `prepare()` (`cli_runtime.py:77`), que apenas roda
`_start_background_workers()` (`orchestrator.py:332`, chamado por `prepare()` em `:328`). Na TUI, portanto, o loop
proativo não começa sozinho: só se alguém digitar `/proativo` duas vezes (a primeira
desliga, a segunda religa e cria a task). Na prática, lembretes, agenda e foco estão
desligados ali.

Isso não é um detalhe: pendurar o check-in no `_proactive_loop` significaria
pendurar no único lugar que não roda na interface padrão. O análogo correto é o
**worker de memória** (`zaya/controller/memory.py:55-82`), que nasce em
`_start_background_workers()` e por isso existe em todo mundo — TUI, terminal e
Telegram.

**Efeito colateral registrado, não corrigido aqui.** Se o `_proactive_loop` nunca
sobe na TUI, também nunca sobem lembretes nem `_check_focus` ali — quem usa a TUI
como interface principal não recebe lembrete nenhum. Consertar é uma linha em
`prepare()` (`asyncio.create_task(self._proactive_loop(greet=False))`), mas liga
também o idle check-in de 45 minutos e muda o comportamento de quem já usa a TUI.
Fica **fora de escopo** (seção 14); o check-in não depende disso porque mora no
worker de memória.

**Mensagem proativa hoje é invisível nas duas pontas.** `zaya/cli_runtime.py:76` sobe
a TUI com `TerminalUI(quiet=True)` e `zaya/cli_integrations.py:117` sobe o serviço do
Telegram com o mesmo `quiet=True`; `TerminalUI._print`
(`zaya/interface/terminal.py:136`) retorna cedo quando `quiet`, e `_say_proactive`
chama `self.ui.proactive(text)`, que passa por `_print`. Na TUI hoje isso não aparece
como sintoma porque o loop proativo também não roda ali; no Telegram porque o serviço
também não liga o loop. Mas o check-in sobe por um worker que existe nas duas
interfaces e depende de ser **lido**, então isso é pré-requisito (seção 4.1).

---

## 3. Fluxo

| Hora | O que acontece | Quem faz |
| --- | --- | --- |
| durante o dia | turnos gravados normalmente | `MemoryStore.add_turn` |
| a cada 60s | varredura local de dicas nos turnos novos (`lembra que`, `sempre`, `prefiro`, `decidi`, `meu aniversário`, nomes próprios) | `_memory_loop` → `_collect_fact_candidates` |
| 18:00, primeira volta na janela | **uma** chamada de LLM (timeout 30s) para limpar os candidatos; guardada por `meta`, nunca repete no dia | `_check_memory_review` |
| 18:00–21:00 | mensagem com até 5 itens numerados, `count=False`, `speak=False` | `_check_memory_review` → `_say_proactive` |
| resposta | gramática curta aplicada, sem abrir turno no modelo | `_dispatch` / `_dispatch_silent` |
| resposta ausente | nada muda; os itens continuam pendentes | — |
| 23:00 | consolidação de sempre + fatos confirmados viram `fatos.md`. A extração aqui é **fallback**: só roda se a passada das 18h não aconteceu no dia (guarda `memory:extract:<dia>`) | `DailyMemory.consolidate` + `FactBook.render_markdown` |

A varredura e a chamada de LLM são separadas de propósito: a varredura é local e
barata, então pode rodar a cada minuto; a chamada de LLM é caro, então roda uma vez
por dia, e só se houver dica nova. Dia sem conversa = dia sem chamada.

As duas coisas acontecem dentro de `_memory_loop` (`zaya/controller/memory.py:66`),
que já acorda a cada 60s, já respeita `_busy` / `_recording` / `_listening` e já
existe na TUI, no terminal e no Telegram. Nenhum worker novo, nenhum laço novo.

### Por que 18h e não 20h

`quiet_hours = "23:00-08:00"` e `noon_until = "18:00"` no briefing deixam a janela
das 18h livre de outras mensagens automáticas. Às 20h a consolidação já está perto,
e um "guardar isso?" às 22h50 vira ruído com a nota do dia saindo.

### Por que não no `_proactive_loop`

Já explicado na seção 2, mas vale repetir porque é o erro mais fácil de cometer:
`_proactive_loop` nasce em `Orchestrator.start()`
(`zaya/controller/orchestrator.py:382`), e o caminho Textual — a interface padrão —
nunca chama `start()`, só `prepare()` (`zaya/cli_runtime.py:77`). O que existe na
TUI são os workers de `_start_background_workers()`: memória, briefing e vagas.
Briefing e vagas saem cedo sem Telegram (`zaya/controller/briefing.py:24`,
`zaya/controller/career.py:37`). Sobra a memória.

---

## 4. O que precisa existir antes

### 4.1 Visibilidade proativa (TUI e Telegram)

`TerminalUI` ganha um callback opcional, no mesmo padrão de `permission_callback`
(`zaya/interface/terminal.py:65`):

```python
# TUI/Telegram instalam aqui o destino das mensagens proativas. Sem isso,
# quiet=True descarta tudo e o usuario nunca ve lembrete nem check-in.
self.proactive_callback: Callable[[str], None] | None = None
```

E `proactive()` passa a chamar o callback quando existir, antes de qualquer
checagem de `quiet`. `ZayaTextualApp.on_mount` (`zaya/interface/textual_app.py:232`)
instala:

```python
if ui is not None and hasattr(ui, "proactive_callback"):
    ui.proactive_callback = self._write_proactive
```

com `_write_proactive` montando um card igual ao `_write_zaya`
(`textual_app.py:1058`), com rótulo próprio (`✦ Zaya · iniciativa`).

Sem `call_from_thread`: quem chama `ui.proactive` é o `_memory_loop`, um task
`asyncio` no **mesmo** event loop da TUI — diferente de `permission_callback`, que é
invocado de uma thread por `asyncio.to_thread`. Usar `call_from_thread` aqui
funcionaria por acidente no Textual 8, mas é a API errada para o caso e falha se o
callback algum dia vier de dentro do próprio loop.

Pré-requisito do check-in, porque mensagem de revisão que não aparece é pior que
nenhuma. São três edições pequenas e independentes: `TerminalUI`, TUI e serviço do
Telegram.

### 4.1.1 O mesmo problema no Telegram

No serviço do Telegram (`zaya/cli_integrations.py:117`) a UI também é
`TerminalUI(quiet=True)`, então `ui.proactive(...)` é descartado do mesmo jeito. Hoje
isso não dói porque `_proactive_loop` **também** não sobe ali — o serviço chama só
`start_briefing()` e `start_career()`, e o briefing envia por
`self.telegram.send_message` direto (`zaya/controller/briefing.py:87`, `:126`).

O check-in muda isso: ele sobe no `_memory_loop`, que **existe** no Telegram. Sem um
destino, a pergunta das 18h seria silenciosamente engolida justamente no canal em que
ela é mais útil (a pessoa lê no celular).

A solução é a mesma, um callback a mais no serviço:

```python
# zaya/telegram/service.py, em run(), antes do loop de updates
async def _send_proactive(text: str) -> None:
    await self.client.send_message(text)

self.ui.proactive_callback = lambda text: asyncio.create_task(_send_proactive(text))
```

`proactive()` é síncrono e `send_message` é assíncrono, daí o `create_task`. O
callback roda no mesmo loop do serviço — nada de thread. E como é uma task solta, o
helper precisa do `try/except` + `logger.warning` interno, senão uma falha de rede
vira exceção perdida (`zaya/controller/briefing.py:86-91` é o padrão do repo).

Com isso, o check-in sai no canal certo uma vez só: a chave `claim_meta` é a mesma
para TUI e Telegram, então quem chegar primeiro manda. Se a pessoa estiver com os
dois abertos, a mensagem aparece em um lugar só — o que é o comportamento desejado
para uma pergunta que precisa de uma resposta.

### 4.2 Tabela de candidatos (entra na migração v6 da etapa 0)

A etapa 0 sobe `SCHEMA_VERSION` para **6** e cria `facts`. O número é 6 porque **v5
já foi usado** pela coluna `channel` em `conversations` (`store.py:19` na árvore de
trabalho; os bancos em disco ainda dizem 4). Os candidatos entram na **mesma**
migração de fatos — uma subida de versão, duas tabelas:

```sql
CREATE TABLE IF NOT EXISTS fact_candidates (
  id            INTEGER PRIMARY KEY,
  slot          TEXT NOT NULL,      -- 'preferencia' | 'pessoa' | 'projeto' | 'rotina' | 'decisao'
  key           TEXT NOT NULL,      -- rotulo curto e estavel
  value         TEXT NOT NULL,
  evidence_turn INTEGER,            -- turno de origem, para auditoria
  state         TEXT NOT NULL DEFAULT 'pending',
                -- pending | saved | discarded
  shown_at      TEXT,               -- ultima noite em que apareceu na lista
  created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_candidates_queue ON fact_candidates(state, created_at);
-- Impede dois descartes da mesma (slot, key) e serve de indice de consulta.
CREATE UNIQUE INDEX IF NOT EXISTS idx_candidates_discarded
  ON fact_candidates(slot, key) WHERE state = 'discarded';
```

O índice parcial **não** impede que a varredura insira um `pending` novo para um
`(slot, key)` que já foi descartado: um índice único só compara linhas que casam o
predicado, e `pending` não casa `state = 'discarded'`. Quem garante a promessa
"descartado não volta" é a consulta da seção 10, que olha `fact_candidates` e `facts`
antes do `INSERT`. O índice existe para que o descarte seja idempotente (descartar
duas vezes não duplica linha) e para a consulta ser barata.

Por que tabela e não JSON em `meta`: a ordem "mais antigos primeiro" e o teto de 5
saem de graça de `ORDER BY created_at, id LIMIT 5`; e um candidato pendente
atravessa dias sem precisar de arquivo. O `_pendentes.md` do plano original deixa de
existir — era um arquivo morto que ninguém lia.

O `meta` guarda só o mapa da noite corrente:

```
memory:review:2026-11-11 -> {"shown": {"1": 12, "2": 19}, "at": "18:00"}
memory:review:llm:2026-11-11 -> [{"slot": "preferencia", "key": "...", "value": "..."}]
```

A segunda chave guarda a limpeza da LLM do dia, para o retry de envio não repetir a
chamada (seção 8). Ela é descartável: se sumir, o próximo ciclo refaz a passada.

O mapa é o que permite `salva 1 e 3` significar exatamente os candidatos 12 e 19, e
não "os três primeiros da fila de agora" (que pode ter mudado se a pessoa respondeu
no dia seguinte). O mapa **não** é apagado nem marcado depois de uma resposta: quem
impede a mensagem de reaparecer no mesmo dia é o `claim_meta` (a chave do dia já
existe), e quem permite responder na manhã seguinte é o mesmo mapa continuar lá.

---

## 5. Configuração

Campos **planos** dentro de `MemoryConfig` (`zaya/config.py:180`), seguindo o
precedente de `market_enabled` / `market_at` / `market_days` em `BriefingConfig`.
Nada de `[memory.review]` aninhado: `_apply_env` (`zaya/config.py:654`) só sabe
escrever um nível (`raw.setdefault(section, {})[field]`), e um submodelo exigiria
tratar caminho com ponto só para ganhar organização visual.

```python
class MemoryConfig(BaseModel):
    ...
    # Check-in das 18h: mostra o que a Zaya aprendeu e pergunta o que guardar.
    review_enabled: bool = True
    review_at: str = "18:00"
    # Depois disso nao pergunta mais; o que ficou pendente espera o dia seguinte.
    review_until: str = "21:00"
    # Itens por mensagem. O excedente fica na fila para as proximas noites.
    review_max_items: int = 5
    # Uma passada de LLM por dia para limpar os candidatos (fallback: heuristica).
    review_use_llm: bool = True
    review_timeout_seconds: float = 30.0
```

No TOML padrão (`_DEFAULT_CONFIG_TEMPLATE`, `zaya/config.py:841`):

```toml
[memory]
enabled = true
at = "23:00"
on_exit = true
use_llm = true
folder = "Memoria"
review_enabled = true        # check-in das 18h: o que guardar, o que esquecer
review_at = "18:00"
review_until = "21:00"
review_max_items = 5
review_use_llm = true
```

`review_timeout_seconds` fica fora do template de propósito: é ajuste de máquina, não
de comportamento, e quem precisa mexe no TOML na mão. Os outros cinco são os que
mudam a rotina e por isso aparecem também como env.

E em `_ENV_MAP`:

```
ZAYA_MEMORY_REVIEW_ENABLED     -> ("memory", "review_enabled")
ZAYA_MEMORY_REVIEW_AT          -> ("memory", "review_at")
ZAYA_MEMORY_REVIEW_UNTIL       -> ("memory", "review_until")
ZAYA_MEMORY_REVIEW_MAX_ITEMS   -> ("memory", "review_max_items")
ZAYA_MEMORY_REVIEW_USE_LLM     -> ("memory", "review_use_llm")
```

`review_enabled=false` desliga só o check-in; a extração da consolidação continua.
Sem revisão humana, os fatos entram com `source='consolidacao'` e `confidence` mais
baixo — é a distinção que mantém a v1 honesta sobre o que foi confirmado. A marcação
por origem é da etapa 0 (coluna `source` em `facts`), não desta etapa.

Estes cinco campos não são os únicos: somam-se a eles, na mesma `MemoryConfig` e pelo
mesmo motivo (tudo plano, porque `_apply_env` só escreve um nível), os campos de recall
e extração da seção 6.4 do plano — `facts_enabled`, `recall_enabled`,
`recall_results`, `recall_max_chars`, `extract_enabled`. Manter a mesma forma evita
que uma etapa precise mexer no parser da outra.

---

## 6. A mensagem

```
Revisão do dia — guardo ou descarto?

1. Preferência: respostas curtas pela manhã
2. Projeto: migrar as notas do Second Brain para uma pasta só
3. Decisão: cortar café depois das 15h
4. Pessoa: Marina é a dentista, consulta em janeiro
5. Rotina: treino às 6h nos dias de semana

Responda "salva 1 e 3", "descarta 2" ou "depois" para deixar comigo.
```

Regras de composição:

- no máximo `review_max_items` linhas, contadas em uma passada (`len()`, não
  estimativa de tokens);
- linha = `{n}. {slot em português}: {value}` — o `slot` é o que dá contexto para
  decidir, então ele aparece sempre, na mesma forma;
- `value` cortado em 90 caracteres, sem quebra de linha;
- a linha de instrução muda se não couber todo mundo: `"e mais 3 ficam para amanhã."`;
- sem emoji, sem negrito, sem tabela. É para ser lido de relance e respondido com
  o polegar.

Envio:

```python
await self._say_proactive(text, count=False, speak=False)
```

`count=False` é obrigatório: o check-in não consome a cota de 4 mensagens
proativas/dia nem o intervalo de 60 minutos. Ele é **pergunta de manutenção**, não
iniciativa espontânea. Se consumisse, um dia com agenda + lembrete + check-in
gastaria 3 das 4 cotas antes das 19h.

`speak=False` é novo em `_say_proactive`. A mensagem tem lista e números; o TTS leria
"um ponto dois pontos" e ficaria insuportável. A confirmação da resposta (curta)
continua sendo falada.

Assinatura final:

```python
async def _say_proactive(self, text: str, count: bool = True, speak: bool = True) -> None:
```

---

## 7. Gramática da resposta

A resposta é interpretada por `ReviewMixin._handle_memory_review_reply(text, speak)`,
chamado no topo de `_dispatch` (`speak=True`) e de `_dispatch_silent`
(`speak=False`), **antes** de `_quick_reply` e **antes** de `parse_register`. Retorna
`str | None`: `None` = não era resposta de revisão, siga o fluxo normal.

Precedência, nesta ordem:

1. resolve o mapa da revisão: o de **hoje** se existir; senão o de **ontem**. Não
   existe mapa, ou existe com `shown` vazio? Devolve `None` na hora. Isso é o que
   garante que `salva 1 e 3` fora de contexto continue caindo em `parse_register` como
   hoje.
2. o texto começa com `salva|guarda|descarta` **e** o resto é número/artigo/conjunção
   ou `tudo`? Aplica.
3. o texto é uma das frases de "deixa para depois" (`depois`, `deixa pra depois`,
   `deixa para depois`, `agora não`, `hoje não`)? Não mexe em nada.
4. o texto é só uma lista de números (`1 e 3`, `2, 4`) **e** o mapa aberto é o de
   **hoje**? Salva.
5. qualquer outro caso → `None`.

O mapa vale até o **fim do dia seguinte** ao envio (23:59), sem pausa no meio. Quem
responde às 22h do mesmo dia e quem responde às 9h do dia seguinte entram na mesma
regra. Depois disso o mapa continua no `meta` para auditoria, mas não intercepta mais
nada.

Se o mapa de ontem ainda está aberto e o check-in de hoje também sair, o de **hoje**
vence: a numeração mostrada mais recentemente é a única que a pessoa tem na frente.
O de ontem é abandonado silenciosamente — os candidatos dele continuam `pending` e
reaparecem na próxima lista (é o mesmo comportamento do silêncio, seção 8).

Regra de resolução do mapa, usada tanto aqui quanto na aplicação: **o mapa de hoje
manda sozinho sempre que existir**. Só se não houver mapa de hoje é que o de ontem é
considerado. Sem esse detalhe, responder ontem e hoje mandaria no mapa de ontem, que a
pessoa não tem mais na frente.

**Não existe estado "fechado" no mapa.** Responder não fecha, não apaga e não marca
nada: o mapa serve só para traduzir número → candidato. Reenviar já é impossível pelo
`claim_meta`; e "não perguntar de novo hoje" é consequência disso, não de um campo
extra. Se o mapa fosse fechado após a primeira resposta, quem respondesse `salva 1` e
depois `descarta 2` perderia a segunda — que é exatamente o uso normal de uma lista.

| Entrada | Efeito |
| --- | --- |
| `salva 1`, `salva 1 e 3`, `salva 1, 3`, `guarda a 1`, `salva os 2` | salva os itens citados |
| `salva tudo` | salva todos os itens mostrados |
| `descarta 2`, `descarta 1 e 2` | marca como descartado |
| `descarta tudo` | descarta todos os mostrados |
| `1 e 3` (só números, no dia do envio) | **salva** os citados |
| `depois`, `deixa pra depois`, `deixa para depois`, `agora não`, `hoje não` | não mexe em nada; a revisão do dia fica como está |
| qualquer outra coisa | `None`; a revisão continua aberta e o texto segue o fluxo normal |

Duas restrições de propósito, porque a janela do mapa dura ~30h:

- **`nao`/`não` puro não entra.** É resposta comum demais para outras coisas ("não",
  respondendo a uma pergunta do modelo). Só as frases de adiamento explícitas casam.
- **A lista nua de números só vale no dia do envio.** No dia seguinte o mapa ainda
  funciona para `salva 1 e 3` (que tem verbo), mas `1 e 3` sozinho é ambíguo demais
  para uma manhã em que a pessoa não tem a lista na frente. Isso não quebra a manhã
  seguinte: a numeração continua acessível por verbo.

`depois` é intencionalmente indistinguível de não responder. Registrar "a pessoa
pediu para deixar" exigiria estado novo e não muda nada observável: os pendentes
voltam amanhã nos dois casos, e a mensagem de hoje não reaparece porque a chave do
dia está reservada (`claim_meta`). Menos estado, mesmo comportamento.

Decisões embutidas:

**Lista nua de números salva (no dia do envio).** Quem digita "1 e 3" com a lista na
frente quer guardar. Descartar é a ação destrutiva, então exige verbo explícito. A
assimetria é intencional; a restrição ao dia do envio está acima.

**O verbo precisa vir antes.** Só casamos quando o texto **começa** com
`salva|guarda|descarta`. Isso evita capturar "não esquece de descartar o lixo amanhã"
como descarte da lista, e deixa as frases de adiamento num passo separado (senão
`deixa pra depois` viraria ambiguidade entre "adiar" e "guardar").

**O resto do texto só pode ser número, artigo, conjunção ou `tudo`.** Implementação
segura: depois do verbo, `re.fullmatch(r"(?:(?:a|o|os|as)\s+)?\d+(?:\s*(?:,|e)\s*\d+)*|tudo", resto, re.I)`.
Se sobrar qualquer outra palavra, devolve `None`.

**Sem revisão aberta, o comportamento é o de hoje.** `salva 1 e 3` fora de uma
revisão pendente continua criando uma nota `"1 e 3"` via `parse_register`. Isso é
feio, mas é o comportamento atual e mudar isso está fora do escopo.

### As três armadilhas (verificadas rodando o código)

```
parse_register("salva 1 e 3")  -> RegisterIntent(content="1 e 3", ...)
parse_register("guarda a 1")   -> RegisterIntent(content="a 1", ...)
parse_register("salva tudo")   -> RegisterIntent(content="tudo", ...)
parse_register("descarta 2")   -> None
```

1. **`parse_register` captura "salva" e "guarda".** Se a interceptação vier depois
   dele, "salva 1 e 3" vira nota no Second Brain com o título `1 e 3`. Por isso a
   checagem de revisão é a **primeira** coisa dos dois despachos.
2. **`descarta` não é capturado por ninguém.** `parse_register`, `parse_preference`,
   `parse_reminder` e `fast_reply` devolvem `None`. Sem interceptação, "descarta 2"
   cai no modelo, que responde algo genérico e o item continua pendente.
3. **`fast_reply` e `parse_preference` são seguros** para todas as respostas curtas
   testadas (`sim`, `não`, `1 e 3`, `salva`, `ok`, `pode`, `depois`, `descarta` →
   `None`). Ou seja, a interceptação pode ficar antes deles sem quebrar small talk.

Ordem final em `_dispatch` (`zaya/controller/turns.py:91`):

```python
async def _dispatch(self, text: str) -> str:
    review = await self._handle_memory_review_reply(text, speak=True)
    if review is not None:
        return review
    quick = self._quick_reply(text)
    ...
```

E o mesmo em `_dispatch_silent` (`zaya/controller/turns.py:66`), que é o caminho do
Telegram (`answer_once(..., speak=False)` → `_dispatch_silent`) e da voz
(`zaya/controller/voice.py:118`).

---

## 8. Silêncio não descarta nada

Se a pessoa não responder, `_check_memory_review` **não** toca em nada:

- os candidatos continuam `state='pending'`;
- o `meta` da noite fica com `shown`, mas sem resultado;
- a fila de amanhã é a mesma, com os mais antigos primeiro.

Motivo: revisão sem resposta é o caso **normal**. A pessoa estava ocupada. Punir o
silêncio descartando fatos transformaria o check-in num prazo, e um prazo invisível
é pior que nenhum.

Consequência assumida: a fila pode crescer. Com teto de 5 por noite e candidatos
raros (a heurística é conservadora), o caso real é fila de 0 a 3. Não há expiração
automática na v1 — ver seção 14.

### Janela e "já enviei hoje"

```python
cfg = self.cfg.memory
if not cfg.enabled or not cfg.review_enabled:
    return
if not (cfg.review_at <= now.strftime("%H:%M") < cfg.review_until):
    return
if in_quiet_hours(now, self.cfg.proactive.quiet_hours):
    return  # janela configurada dentro das horas de silencio nao fala
if not self.store.pending_candidates(limit=1):
    return  # nada a perguntar: nao queima a chave do dia
key = f"memory:review:{now.date().isoformat()}"
if not self.store.claim_meta(key, "sending"):
    return
```

`quiet_hours` entra porque o `_proactive_loop` já respeita
(`zaya/controller/initiative.py:100`) e o check-in não pode ser a única rotina a falar
às 23h se alguém mover `review_at`. Com o padrão (`18:00`–`21:00` contra
`23:00`–`08:00`) a checagem nunca dispara; ela existe para o caso de configuração.
A função é a mesma (`zaya/controller/proactive.py:218`).

`claim_meta` (`zaya/memory/store.py:510`) é o mesmo padrão do briefing: reserva
atômica, então TUI e serviço do Telegram abertos ao mesmo tempo não mandam duas
mensagens. Falha ao compor → `delete_meta(key)` e nova tentativa no próximo ciclo
de 60s, exatamente como em `zaya/controller/briefing.py:63-91`.

A passada de LLM **não** pode rodar a cada tentativa: um erro de envio às 18h viraria
dezenas de chamadas até as 21h. Por isso o resultado da limpeza é gravado em
`meta["memory:review:llm:<dia>"]` antes do envio, e o retry relê esse JSON. Uma
chamada por dia, como prometido na seção 3.

Se depois da limpeza não sobrou nenhum item (a LLM descartou todos), a noite termina
sem mensagem e a chave do dia é devolvida com `delete_meta(key)` — não há pergunta a
fazer, e o próximo dia começa limpo. Nesse caso o mapa da revisão não chega a existir,
o que é coerente: sem lista mostrada, não há número para responder.

Depois do envio, `set_meta(key, json.dumps({...}))` com o mapa `n → candidate_id` e
`at`. O `claim_meta` inicial já reservou a chave; o `set_meta` sobrescreve o valor
provisório `"sending"`. Na mesma transação, cada candidato mostrado recebe
`shown_at = hoje` — é o que permite distinguir "já foi mostrado e ignorado" de "nunca
apareceu" numa auditoria futura, sem coluna nova.

---

## 9. Aplicação

`ReviewMixin._apply_memory_review(actions)`:

1. lê o `meta` da noite pela mesma regra da seção 7 (hoje se existir, senão ontem);
2. resolve cada número para `candidate_id` pelo mapa; número fora da lista é
   ignorado e a resposta final diz quantos foram aplicados;
3. `saved` → `FactBook.upsert(...)` com `source='review'`, `confidence=1.0`, e
   `fact_candidates.state='saved'`;
4. `discarded` → `fact_candidates.state='discarded'` (nunca `DELETE`);
5. `FactBook.render_markdown()` não roda aqui: o `fatos.md` é reescrito pela rotina
   já existente, para não escrever arquivo no meio de um turno;
6. resposta curta: `"Guardei 2 e esqueci 1."` ou `"Nada mudou; sigo com tudo
   pendente."`

A aplicação é incremental: `salva 1` agora e `descarta 2` daqui a dez minutos funcionam
as duas, cada uma sobre o mesmo mapa. Só o que foi citado muda de estado.

O índice único parcial `idx_candidates_discarded` **não** faz o trabalho sozinho (ver
4.2): quem impede a repetição é o dedupe da varredura (seção 10), que consulta
`fact_candidates` e `facts` antes de inserir. O índice só garante que descartar duas
vezes não duplique linha.

Sobre `superseded_by` em `facts`: quando a mesma `(slot, key)` é salva com valor
novo, o registro antigo ganha `superseded_by` e a linha viva é a nova. O
`fatos.md` mostra só as vivas. Detalhe da etapa 0, citado aqui só para deixar claro
que "guardar de novo" não duplica.

O índice único que garante um fato vivo por `(slot, key)` usa `WHERE state = 'live'`,
**não** `WHERE superseded_by IS NULL`. O motivo é `forget_fact`: esquecer deixa
`superseded_by` nulo (não houve substituto), e com o predicado antigo a chave
continuaria ocupada — o próximo `upsert_fact` da mesma chave falharia no índice. Com
`state`, esquecer libera a chave e `restore_fact` só funciona enquanto ela estiver
livre (etapa 0, seção 4.1 e 4.3 do plano).

### A resposta também vira turno

Ponto fácil de esquecer: `answer_once` grava o turno de **usuário**
(`orchestrator.py:313`) **antes** de chamar `_dispatch`, então a resposta da revisão
já entra no histórico como fala da Talita. Se `_handle_memory_review_reply` devolver
a confirmação sem gravá-la, o histórico fica com uma pergunta da Talita sem resposta
— e a TUI, que desenha o histórico do `store` (`textual_app.py:398`), mostra isso
como buraco.

Por isso a aplicação termina no mesmo caminho de `_emit_quick_reply`
(`turns.py:123`):

```python
# dentro de _handle_memory_review_reply, depois de aplicar
self.store.add_turn(self.conversation_id, "assistant", confirmation)
if speak:
    self.ui.print_markdown(confirmation)
    await self._speak(confirmation)
return confirmation
```

`speak` é `True` em `_dispatch` e `False` em `_dispatch_silent`, igual ao resto das
respostas locais. A confirmação é curta ("Guardei 2 e esqueci 1."), então falar é
adequado — diferente da lista numerada.

---

## 10. Coleta de candidatos

`_collect_fact_candidates()` roda a cada volta do `_memory_loop`, mas
com guarda de custo: só processa turnos com `id > self._review_scanned_turn`, então
cada turno é olhado **uma vez**. Fonte dos turnos: `store.turns_between(início do dia,
agora)` (`zaya/memory/store.py:359`), a mesma consulta da consolidação. O filtro por
`id` é feito em Python sobre o resultado — não precisa de método novo no store, e a
janela do dia já limita o volume.

Por que a janela é o dia e não o banco inteiro: o banco real tem 745 turnos, quase
todos irrelevantes. Turno de ontem não vira sugestão de hoje; a fila de pendentes já
carrega o que ficou de dias anteriores. Reabrir a Zaya revarre o dia corrente, e aí o
dedupe por `(slot, key)` (abaixo) é o que impede candidato repetido.

Custo por volta: uma consulta indexada sobre os turnos do dia (dezenas, não centenas)
e a heurística em Python sobre os que passaram do cursor. É a mesma ordem de grandeza
da consulta que `_maybe_consolidate` já faz a cada minuto.

Heurística (local, sem LLM), sobre texto `user` do dia:

```python
_CUES = (
    "lembra que", "sempre", "nunca", "prefiro", "não gosto", "nao gosto",
    "decidi", "decidimos", "meu aniversário", "meu aniversario", "me chamo",
    "meu nome é", "meu nome e", "trabalho com", "moro em", "to morando",
)
```

- frase que contém uma dica e tem entre 8 e 220 caracteres → candidata;
- `slot` inferido por palavra-chave (`prefiro|gosto|detesto` → `preferencia`;
  `trabalho|moro|aniversário` → `pessoa`/`rotina`; `decidi|vamos` → `decisao`;
  nome próprio + relação → `pessoa`);
- `key` = slug estável de até 40 caracteres (primeiros radicais da frase,
  normalizados sem acento);
- dedupe: se já existe linha em `fact_candidates` com o mesmo `(slot, key)` — em
  qualquer estado: `pending`, `saved` ou `discarded` — a dica é ignorada. Se existe
  fato **vivo** em `facts` com o mesmo `(slot, key)`, também. É esta consulta, e não o
  índice parcial, que sustenta "descartado não volta".

A passada de LLM (se `review_use_llm`) recebe **só** as frases candidatas, não o dia
inteiro, e devolve JSON:

```json
[{"slot": "preferencia", "key": "tom_de_voz", "value": "prefere respostas curtas pela manhã"}]
```

Instruções do prompt: não invente, descarte o que for ambíguo, não inclua dados de
saúde, finanças ou terceiros — devolva **todos** os que passarem, sem cortar por
quantidade; o teto de 5 é da mensagem (seção 6), não da limpeza. Falha, timeout ou
JSON inválido → segue só com a heurística; o check-in sai igual, com texto mais cru.
Lista vazia é resultado legítimo ("nada aqui vale guardar") e a noite termina sem
mensagem — diferente de falha, que ainda tem a heurística para mostrar.

Sessão dedicada `_review_session_id`, criada como `_initiative_session_id`
(`zaya/controller/initiative.py:304`), para não poluir a conversa principal nem o
cache de prompt.

**O que acontece com o que a LLM cortou.** Ela filtra justamente os falsos positivos
da heurística ("sempre chego atrasado" não é fato sobre a pessoa). Se esses ficassem
`pending`, voltariam à passada de LLM todo dia, gastariam a única chamada da noite e,
num dia de falha, apareceriam na lista como sugestão ruim. Por isso o corte também é
`state='discarded'` — a mesma tabela, o mesmo efeito prático ("não volta"), sem coluna
nova. A diferença entre corte da máquina e corte da pessoa não precisa ser auditada
na v1; se precisar depois, vira uma coluna `reason`.

Isso fecha a fila: ao fim da passada, todo candidato está `pending` (esperando a vez,
acima do teto, ou aguardando resposta) ou já saiu por `saved`/`discarded`. A noite
seguinte reaproveita só os `pending`, com `shown_at` marcando quem já apareceu.

---

## 11. Pontos de edição

| Arquivo | Mudança |
| --- | --- |
| `zaya/memory/store.py` | `SCHEMA_VERSION = 6`; `fact_candidates` na migração v6; `queue_candidates`, `pending_candidates`, `mark_candidates_shown`, `set_candidate_state` |
| `zaya/memory/facts.py` | (etapa 0) `FactBook`; aqui `upsert(...)` já aceita `source`/`confidence` |
| `zaya/controller/review.py` | **novo**: `ReviewMixin` com `_check_memory_review`, `_collect_fact_candidates`, `_handle_memory_review_reply`, `_apply_memory_review`, `_compose_review_message` |
| `zaya/controller/orchestrator.py` | herdar `ReviewMixin`; inicializar `_review_scanned_turn = 0`, `_review_session_id = None` |
| `zaya/controller/contract.py` | campos novos no contrato abstrato, junto de `_memory_task` |
| `zaya/controller/memory.py` | `_memory_loop` chama `_collect_fact_candidates()` a cada volta e `_check_memory_review(now)` **antes** de `_maybe_consolidate` |
| `zaya/controller/initiative.py` | `_say_proactive(text, count=True, speak=True)` |
| `zaya/controller/turns.py` | interceptação no topo de `_dispatch` e `_dispatch_silent` |
| `zaya/interface/terminal.py` | `proactive_callback` + chamada em `proactive()` |
| `zaya/interface/textual_app.py` | instala `proactive_callback`; `_write_proactive` |
| `zaya/telegram/service.py` | instala `proactive_callback` que envia por `send_message` |
| `zaya/config.py` | campos planos em `MemoryConfig`, `_ENV_MAP`, TOML padrão |
| `.env.example` | as cinco variáveis `ZAYA_MEMORY_REVIEW_*` comentadas, junto do bloco `ZAYA_MEMORY_*` |
| `README.md` | 4 linhas em "Ajustando o ritmo dela" |

O ponto de entrada no `_memory_loop` fica assim (o `try` já existe em
`zaya/controller/memory.py:77`):

```python
while True:
    await asyncio.sleep(60.0)
    if not self.cfg.memory.enabled:
        continue
    if self._busy or self._recording or self._listening:
        continue
    try:
        await self._collect_fact_candidates()
        await self._check_memory_review(datetime.now().astimezone())
        await self._maybe_consolidate(datetime.now().astimezone())
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        self.logger.warning("falha na memória diária: %s", exc)
```

O check-in vem **antes** de `_maybe_consolidate`: às 23h, a consolidação e a
revisão disputariam o mesmo dia. A consolidação **não** apaga o mapa da revisão —
quem responde na manhã seguinte ainda precisa dele (seção 7). O mapa expira sozinho
pela regra da janela, não por efeito colateral de outra rotina.

O check-in **não** depende de `idle_checkin` nem de `_can_idle_checkin`. Ele não é
iniciativa espontânea; é rotina de manutenção com horário próprio.

---

## 12. Testes

`tests/test_memory_review.py` (novo):

1. **Janela**: `review_at <= hh:mm < review_until` envia; fora da janela não envia.
2. **Uma vez por dia**: duas chamadas seguidas em `_check_memory_review` → um único
   `ui.proactive` (contando via `FakeUI.__getattr__`, padrão de
   `tests/test_orchestrator.py:30`).
3. **Não gasta cota**: depois do envio, `_proactive_count` continua `0`.
4. **Teto e ordem**: 8 pendentes → mensagem com 5, os 5 mais antigos; os outros 3
   aparecem na noite seguinte.
5. **`salva 1 e 3`**: cria exatamente 2 fatos; `brain.create_note` **não** é chamado
   (prova de que a interceptação venceu `parse_register`); a confirmação é gravada
   como turno `assistant`.
6. **`guarda a 1`**: idem, com artigo.
7. **`descarta 2`**: `fact_candidates` daquele item fica `discarded`; nenhum fato
   criado; reabrir a Zaya no mesmo dia (a varredura roda de novo sobre o mesmo turno)
   não cria candidato novo para a mesma dica — é o teste que prova que o dedupe da
   seção 10, e não o índice parcial, segura a repetição.
8. **`1 e 3` nu, no dia do envio**: salva (assimetria documentada); na manhã
   seguinte o mesmo `1 e 3` nu **não** intercepta e cai no fluxo normal.
9. **Silêncio**: segunda chamada no dia seguinte mantém os mesmos pendentes, com
   numeração nova e os mesmos `candidate_id`.
10. **Fallback do LLM**: `client.prompt_sync` levanta exceção → mensagem sai com os
    candidatos crus, sem erro na UI.
11. **Regressão**: sem revisão aberta, `_dispatch("salva 1 e 3")` volta a criar nota
    `1 e 3` como hoje.
12. **Mapa aberto e não vencido**: responder na manhã seguinte ao envio aplica a
    revisão, mesmo depois de `_maybe_consolidate` ter rodado; dois dias depois,
    `salva 1 e 3` volta a cair em `parse_register`.
13. **Migração**: banco v4 com turnos e lembretes abre, vira 6, `fact_candidates`
    existe vazia, contagem de turnos intacta (mesmo padrão de
    `tests/test_memory.py`). Um segundo caso parte de v5 (só `channel`) e chega em 6.
14. **`depois` não reenvia**: responder `depois` não apaga nada, não muda estado e o
    ciclo seguinte de `_check_memory_review` no mesmo dia não manda nada (chave do dia
    já reservada). Os pendentes continuam pendentes.
15. **Respostas incrementais**: `salva 1` e depois `descarta 2` no mesmo dia aplicam
    as duas, cada uma no mapa corrente.
16. **Visibilidade**: com `proactive_callback` instalado, `TerminalUI(quiet=True)`
    entrega o texto ao callback e não imprime no console; sem callback, `quiet=True`
    descarta (comportamento de hoje preservado).
17. **Uma chamada de LLM por dia**: envio falha na primeira tentativa, o retry no ciclo
    seguinte compõe de novo sem chamar `client.prompt_sync` outra vez.
18. **Horas de silêncio**: `review_at = "23:30"` com `quiet_hours = "23:00-08:00"` não
    envia e não queima a chave do dia.

`tests/test_config.py`: defaults `review_enabled is True`, `review_at == "18:00"`,
`review_max_items == 5`, e env `ZAYA_MEMORY_REVIEW_ENABLED=false` desliga.

---

## 13. Critérios de aceite

- [ ] Às 18h, com a TUI aberta, a mensagem aparece **no chat** e não consome
      `daily_max`.
- [ ] `salva 1 e 3` aplica exatamente os itens 1 e 3 e **não** cria nota no Second
      Brain.
- [ ] `descarta 2` persiste o descarte e o item não volta em nenhuma noite seguinte.
- [ ] Não responder não altera nenhum estado; os itens voltam amanhã.
- [ ] Responder às 20h30 funciona; responder na manhã seguinte também, e a
      consolidação das 23h **não** apaga o mapa antes disso.
- [ ] Depois da janela (até o fim do dia seguinte), o mapa não intercepta mais nada
      e `salva 1 e 3` volta a cair em `parse_register`.
- [ ] `pytest` verde, incluindo `test_memory`, `test_memory_consolidation`,
      `test_proactive`, `test_orchestrator`, `test_turns_events`.
- [ ] `ruff check zaya tests` sem avisos novos.
- [ ] `git diff --stat` sem arquivos fora dos listados na seção 11.

---

## 14. Fora de escopo (não faça)

- **Ligar o `_proactive_loop` na TUI.** É uma linha em `prepare()`
  (`orchestrator.py:332`), e consertaria lembretes, agenda e foco na interface
  padrão. Mas liga também o idle check-in de 45 minutos, que hoje não existe ali, e
  muda o comportamento de quem já usa a TUI. Merece diff e teste próprios —
  registrado aqui para não parecer esquecimento. O check-in das 18h **não depende**
  disso.
- **Expiração automática de candidato pendente.** "Depois de 3 noites, esquece" é
  uma decisão de produto, não técnica, e a fila real é curta. Se virar problema, o
  ajuste é uma linha em `pending_candidates` mais um campo de config.
- **Embeddings para decidir se dois candidatos são o mesmo fato.** O dedupe por
  `(slot, key)` é grosseiro de propósito; `sqlite-vec` continua fora até haver
  evidência de que o caso real precisa.
- **Tela/diff de revisão no Second Brain.** O check-in é no chat; quem quiser
  conferir abre `fatos.md`.
- **Reagendar a revisão pela conversa** ("me pergunta às 20h"). É config.
- **Revisão no WhatsApp** (`enabled = false` no repo). O caminho é o mesmo
  `_dispatch_silent`, então quando o canal ligar, funciona; testar agora é custo sem
  retorno.
- **Consolidar os 5 itens em menos perguntas** ("posso guardar tudo?"). Uma pergunta
  por noite é o limite do aceitável.

---

## 15. Posição na sequência

| Etapa | Entrega | Depende de |
| --- | --- | --- |
| 0 | schema v6: `facts` + `fact_candidates`, `FactBook` | — |
| 1 | `fatos.md` + sync + migração v4/v5→v6 testada | 0 |
| 2 | `turns_fts` + `recall()` + fallback `LIKE` | 0 |
| 3 | tools `memory_remember` / `memory_forget` / `memory_list` | 0, 1 |
| 4 | bloco de fatos no turno + extração na consolidação | 0, 1 |
| **5** | **check-in das 18h** | **0, 1, 4** |

A etapa 5 é a última de propósito. Sem a etapa 1 não há `fatos.md` para receber o que
foi confirmado; sem a etapa 4 não há extração, e o check-in mostraria só frases cruas
da heurística. Ela também é a mais fácil de remover: `review_enabled = false` devolve
o sistema exatamente ao estado da etapa 4.

---

## Notas de decisão

**Por que `count=False` e não uma fila separada.** A cota de 4 existe para limitar
interrupção espontânea. O check-in é solicitado pela própria pessoa toda noite, no
mesmo horário — não é interrupção, é rotina. Consumir cota faria a Zaya ficar quieta
à tarde justamente porque perguntou o que devia guardar. `count=False` é a distinção
certa e já existe no código (`_fire_due_reminders`, `_say_proactive(..., count=False)`).

**Por que o descarte é estado e não `DELETE`.** Sem isso, a varredura de amanhã
reencontra a mesma dica no mesmo turno antigo e propõe o mesmo fato de novo. O
usuário descartaria duas vezes, três vezes, e concluiria que o botão não funciona.
A promessa é cumprida pelo dedupe da varredura (seção 10), que precisa olhar
`fact_candidates` em **qualquer** estado — `discarded` inclusive. O índice parcial
sozinho não impede um `pending` novo para o mesmo `(slot, key)`.

**Por que a interceptação vem antes de `fast_reply`.** Poderia vir depois — todas as
respostas testadas devolvem `None` em `fast_reply`. Mas vir antes elimina a
possibilidade de um small talk futuro capturar "depois" ou "nada" e roubar a
resposta da revisão. A ordem não custa nada e elimina uma classe de regressão.

**Sobre `speak=False`.** `_say_proactive` fala tudo que mostra. Para uma lista de
cinco itens com números, isso é hostil. A alternativa seria uma mensagem sem lista
("aprendi três coisas hoje, quer revisar?") seguida de outra mensagem — dois turnos
proativos para a mesma pergunta. Desligar a fala nessa mensagem é o menor diff.

**A mensagem pode não sair.** Se a pessoa abrir a Zaya às 21h05, o check-in do dia
não acontece e os pendentes esperam. Isso é intencional: chegar às 21h05 com "o que
eu guardo de hoje?" compete com a consolidação das 23h e com o fim do dia. A janela
é o contrato.
