# Plano de memória de longo prazo da Zaya

Este documento substitui o prompt `~/SecondBrain/Inbox/prompt-memoria-letta-like.md`
(130 linhas, Partes 1–3). Ele mantém as três partes e todas as restrições, corrige
quatro erros factuais e fecha quatro lacunas de arquitetura que fariam o plano
entregar uma busca melhor que nunca roda.

O check-in das 18h continua sendo o documento separado
`docs/memoria-checkin-18h.md`, que desenha a **etapa 5**. Este plano cobre as etapas
**0 a 4** e diz, em cada ponto onde os dois se tocam, qual dos dois manda.

Nada aqui foi implementado ainda. O objetivo é que a implementação comece pela etapa
0 e que cada etapa passe no `pytest` antes da seguinte.

---

## 1. Diagnóstico que motivou a reescrita

Tudo abaixo foi medido no banco real (`data/zaya.db`, 759 turnos, 369 turnos `user`,
17 conversas, `schema_version = 4`; `~/.local/share/zaya/zaya.db`, 658 turnos,
também v4) e no índice real (`~/.local/share/zaya/sb_index.db`, 13 notas). Como o
banco cresce a cada conversa, as contagens são de uma cópia tirada em 2026-11-11 —
o que importa são as **taxas**, não os números absolutos.

### 1.1 O gargalo não é a busca, é o portão

| Medida | Valor |
| --- | --- |
| turnos `user` analisados | 369 |
| turnos em que `needs_second_brain_context()` dispara | 43 (**12%**) |
| turnos em que o gate proposto (seção 6.5) dispara | 130 (**35%**) |
| turnos com hit no índice em que o top-1 é memória/perfil | 209 de 333 (**63%**) |
| desses 209, quantos o gate atual bloqueia | **186** |

Exemplos que o gate atual rejeita, com o índice encontrando a resposta:

| Pergunta | Gate atual | Top-1 do índice |
| --- | --- | --- |
| `como tá meu dedo?` | `False` | `Memoria/Memória 2026-11-10.md` |
| `quem é a Marina?` | `False` | `Inbox/Vagas - mensagens enviadas.md` |
| `o que eu decidi sobre o briefing?` | `False` | `Inbox/Vagas - mensagens enviadas.md` |
| `quais minhas metas?` | `False` | `memoria talita.md` |

O prompt original diz "injete antes do bloco do Second Brain" e não toca em
`needs_second_brain_context()` (`zaya/controller/intents.py:147`). Resultado: busca
melhor atrás do mesmo portão estreito. **A troca do gate é parte da Parte 2.**

Custo de rodar a busca em todos os turnos, para calibrar: ~0,4–0,6 ms/turno (~150–200
ms nos 369) e ~984 caracteres por turno quando há hit. O gargalo é token, não tempo de
CPU — daí o orçamento da seção 6.4.

### 1.2 O tokenizer recomendado não ajuda, e prefixo não é stemming

O prompt manda usar `porter unicode61`. Teste em FTS5 sintético, com cinco frases que
contêm os termos (`"prefiro respostas curtas"`, `"tenho preferencia por silencio"`,
`"decidi cortar cafe"`, `"a decisao foi dificil"`, `"trabalho com automacao"`):

| consulta | `porter unicode61` | `unicode61 remove_diacritics 2` | `unicode61` + prefixo | `trigram` |
| --- | --- | --- | --- | --- |
| `prefiro` | 1 | 1 | 1 | 1 |
| `preferencia` | 1 | 1 | 1 | 1 |
| `preferência` | 1 | 1 | 1 | **0** |
| `prefer` | 0 | 0 | **1** (`preferencia`) | 1 |
| `pref` | 0 | 0 | **2** (`prefiro`, `preferencia`) | 2 |
| `decidi` | 1 | 1 | 1 | 1 |
| `decisao` / `decisão` | 1 / 1 | 1 / 1 | 1 | 1 / **0** |
| `decid` | 0 | 0 | **1** (`decidi`, **não** `decisao`) | 1 |
| `trabalho` | 1 | 1 | 1 | 1 |
| `automacao` | 1 | 1 | 1 | 1 |

Três conclusões, todas medidas:

1. **`porter` e `unicode61` devolvem exatamente o mesmo.** `porter` é stemming de
   inglês e não reduz nada em português — não atrapalha, mas também não entrega o que
   o prompt espera dele. Não há motivo para escolhê-lo.
2. **`trigram` quebra com acento** (`preferência` e `decisão` → 0), porque não faz
   dobra de diacrítico. Fica fora.
3. **Prefixo não é stemming.** Ele resolve sufixo flexional que compartilha o radical
   (`decid` → `decidi`/`decidir`/`decidimos`, `trabalh` → `trabalho`/`trabalhei`), e
   **não** resolve par derivacional: `decid` **não** acha `decisao`, e
   `preferência`↔`prefiro` só se encontram truncando para `pref`, que também casaria
   `prefeito` e `preferido`.

Quem precisa de `preferência`↔`prefiro` de verdade precisa de stemmer de português
(não há no FTS5) ou de embeddings. O prefixo é uma melhora barata e mensurável, não a
solução — está na seção 6.2 com essa expectativa calibrada e na seção 9 como o motivo
concreto para os embeddings voltarem.

O que fica do prompt original: `unicode61 remove_diacritics 2`, que já é o padrão do
repo (`secondbrain/index.py:87` e `:117`), e a observação de que palavra acentuada
achada por palavra sem acento funciona (`cafe` ↔ `café`, `manha` ↔ `manhã`).

### 1.3 Python 3.14 não existe aqui

O `.venv` roda **3.12.13** e o FTS5 está compilado. Todo o ramo "se não tiver FTS5,
cai para `LIKE`" do prompt é caminho morto. Mantenha como guarda de uma linha
(`PRAGMA compile_options` no `__init__`, `_has_fts = False`, log uma vez), não como
decisão de projeto.

### 1.4 O diário de hoje se auto-referencia

| Nota | Tamanho |
| --- | --- |
| `Memoria/Memória 2026-11-08.md` | 3544 bytes |
| `Memoria/Memória 2026-11-09.md` | 287 bytes ("Dia sem mensagens registradas") |
| `Memoria/Memória 2026-11-10.md` | 2142 bytes |

A nota do dia 09, sem nenhuma conversa, lista em `## Registros do dia` a nota do dia
**anterior**. A causa está em `records`: o registro `kind='memory'`,
`title='Memória 2026-11-08'` foi criado em `2026-11-09T20:36:49` — o dia 08 foi
consolidado por catch-up no dia seguinte, então o registro caiu na janela do dia 09.
A nota termina descrevendo o próprio mecanismo que a gerou, não a Talita. E o único
conhecimento factual sempre presente no prompt são 451 caracteres limpos de
`preferences.md` (480 brutos, teto `PREFERENCES_MAX_CHARS = 1200`).
`memoria talita.md` (3193 bytes, 34 bullets, 8 seções) **não é lido por nenhum
código**: só está indexado.

---

## 2. O que muda em relação ao prompt original

| # | Prompt original | Este plano | Por quê |
| --- | --- | --- | --- |
| 1 | `fatos.md` "gerado pelo `MemoryStore` (write-through)" | `FactBook(store, brain, cfg)` em `zaya/memory/facts.py` | `MemoryStore` só fala SQLite; escrita de arquivo (atômica + backup) é do `SecondBrain` (`notes.py:143`) |
| 2 | "se ela editar à mão, o arquivo ganha" **e** "não apaga o que não existe no arquivo" | regra única: **sync só adiciona e atualiza** | as duas regras são opostas: um sync que aceita deleção é o que apaga |
| 3 | `porter unicode61` | `unicode61 remove_diacritics 2` + prefixo, com expectativa calibrada | medido na seção 1.2: `porter` não faz nada em português e prefixo não é stemming |
| 4 | Python 3.14 / fallback `LIKE` como caminho | 3.12.13, FTS5 garantido; `LIKE` só como guarda | medido na seção 1.3 |
| 5 | `_pendentes.md` | tabela `fact_candidates` | o check-in já eliminou o arquivo; duas filas para a mesma coisa |
| 6 | extração de fatos na consolidação (23h) | extração às 18h; consolidação só como fallback | a revisão das 18h acontece **antes** da consolidação do mesmo dia |
| 7 | nada sobre o gate | `wants_memory()` substitui `needs_second_brain_context()` | medição da seção 1.1 |
| 8 | `recall(k=5)` e `context_max_chars` | orçamento explícito por bloco | um hit já usa 480 dos 1200 caracteres |
| 9 | nada sobre canais | `proactive_callback` (TUI e Telegram) | `_proactive_loop` não roda na TUI nem no Telegram hoje |
| 10 | "recall semântico de verdade" | recall **léxico ampliado**; embeddings adiados | FTS5 não é semântico; prometer semântica agora é prometer o que não se entrega |

O item 10 é o único em que este plano **reduz** escopo de propósito. `sqlite-vec` +
`fastembed` ficam fora da v1: o `fastembed` baixa modelo de dezenas de MB, o que
colide com a própria restrição "não pode baixar modelo de 100MB sem a pessoa pedir".
A porta fica aberta (seção 9), com critério objetivo para voltar.

---

## 3. Restrições (mantidas do prompt original)

- **Zero daemon.** Nada de servidor para manter vivo.
- **Zero dependência obrigatória nova.** `pip install -e .` não muda.
- **Fonte de verdade = Second Brain.** Todo estado legível em Markdown dentro de
  `~/SecondBrain`. O SQLite é índice/cache, reconstruível.
- **Compatibilidade.** `schema_version` sobe para **6** com migração incremental;
  banco v4 ou v5 abre sem perda. `tests/test_memory.py` e
  `tests/test_memory_consolidation.py` continuam passando.

  O número é 6, não 5, por um motivo concreto: o working tree já usou **v5** para a
  coluna `channel` em `conversations` (`store.py:19`, migração não commitada). Os
  bancos em disco ainda dizem 4, mas o código já declara 5 — então `facts` entra em
  v6 e a migração de fatos roda tanto para quem está em 4 quanto para quem já passou
  pelo 5.
- **Sem chamada de LLM por turno.** Uma passada de extração por dia (seção 7).
- **Nada de `DELETE` como descarte.** Descartar é estado, não remoção.

---

## 4. Etapa 0 — schema v6, `facts`, `fact_candidates`, `FactBook`

Uma subida de versão, duas tabelas novas, um módulo novo. É a etapa que destrava
todo o resto.

### 4.1 `facts`

```sql
CREATE TABLE IF NOT EXISTS facts (
  id            INTEGER PRIMARY KEY,
  slot          TEXT NOT NULL,      -- 'preferencia' | 'pessoa' | 'projeto'
                                    -- | 'rotina' | 'decisao'
  key           TEXT NOT NULL,      -- rotulo curto e estavel: 'tom_de_voz'
  value         TEXT NOT NULL,
  confidence    REAL NOT NULL DEFAULT 1.0,
  source        TEXT NOT NULL,      -- 'review' | 'consolidacao' | 'tool' | 'manual'
  source_turn   INTEGER,            -- turno de origem (auditoria); NULL se manual
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  state         TEXT NOT NULL DEFAULT 'live',
                -- live | superseded | forgotten
  superseded_by INTEGER REFERENCES facts(id)
);
-- Um fato vivo por (slot, key). O predicado usa state, nao superseded_by:
-- um fato esquecido tambem precisa liberar a chave (ver 4.3).
CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_live
  ON facts(slot, key) WHERE state = 'live';
CREATE INDEX IF NOT EXISTS idx_facts_slot ON facts(slot, state);
```

`state` em vez de só `superseded_by` porque há **três** saídas e o prompt original só
previa duas: mudou (`superseded`, com ponteiro), foi esquecido (`forgotten`, sem
ponteiro), ou está vivo. Sem `state`, esquecer teria que virar um `DELETE` ou um
ponteiro falso (`superseded_by = id`), e a única coisa pior que perder histórico é
gravar lixo nele.

`superseded_by` continua existindo para a pergunta "por que você acha isso?" — é o
histórico de graça que o prompt original acertou.

### 4.2 `fact_candidates`

```sql
CREATE TABLE IF NOT EXISTS fact_candidates (
  id            INTEGER PRIMARY KEY,
  slot          TEXT NOT NULL,
  key           TEXT NOT NULL,
  value         TEXT NOT NULL,
  evidence_turn INTEGER,            -- turno de origem, para auditoria
  state         TEXT NOT NULL DEFAULT 'pending',
                -- pending | saved | discarded
  shown_at      TEXT,               -- ultima noite em que apareceu na lista
  created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_candidates_queue
  ON fact_candidates(state, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_candidates_discarded
  ON fact_candidates(slot, key) WHERE state = 'discarded';
```

O índice parcial **não** cumpre "descartado não volta": um índice único só compara
linhas que casam o predicado, e `pending` não casa `state = 'discarded'`. Quem
cumpre a promessa é o dedupe da varredura (seção 7.3), que consulta qualquer estado.
O índice serve para o descarte ser idempotente e para a consulta ser barata.

### 4.3 Supersessão e esquecimento

```python
def upsert_fact(slot, key, value, *, source, confidence=1.0, turn_id=None) -> int:
    # 1. procura live por (slot, key)
    # 2. se existe e o value e igual: atualiza updated_at/confidence, devolve o id
    # 3. se existe e difere: UPDATE state='superseded', superseded_by=<novo id>;
    #    INSERT do novo
    # 4. se nao existe: INSERT
```

Igualdade de `value` é comparação normalizada (espaços colapsados, `casefold`,
sem acento) — senão "prefere respostas curtas" e "Prefere respostas curtas"
gerariam uma linha nova por dia.

`forget_fact(slot, key)` faz `state='forgotten'`, `updated_at=now`. Não apaga. A
linha esquecida continua servindo de resposta a "eu já tive isso guardado?" — e é o
que torna o esquecimento reversível (seção 8):

```python
def restore_fact(slot, key) -> bool:
    # UPDATE facts SET state='live', superseded_by=NULL, updated_at=now
    # WHERE slot=? AND key=? AND state='forgotten'
```

`restore_fact` só funciona se a `(slot, key)` estiver livre. Se já existe um fato
vivo com a mesma chave (foi recriado depois do esquecimento), o `INDEX` único barra e
o método devolve `False` — restaurar por cima do novo seria perder o valor atual.

### 4.4 `FactBook`

`zaya/memory/facts.py`, novo. Não herda de `MemoryStore` e não é importado por ele.
Recebe o que precisa:

```python
class FactBook:
    def __init__(self, store: MemoryStore, brain: SecondBrain, cfg: MemoryConfig):
        ...
    # banco -> arquivo; PURA: sem relogio, mesma entrada => mesma string
    def render_markdown(self) -> str: ...
    def flush(self) -> bool:          # escreve so se o hash mudou; True se escreveu
    # arquivo -> banco
    def sync_from_markdown(self) -> dict[str, int]:   # {"added": n, "updated": n}
    # leitura para o prompt
    def live_block(self, max_chars: int) -> str: ...
```

`render_markdown()` **não** imprime timestamp. Isso não é detalhe de estilo: se ele
chamasse `datetime.now()`, duas chamadas seguidas devolveriam strings diferentes, o
hash nunca bateria e `flush()` escreveria a cada volta do `_memory_loop` — os testes 5
e 9 da seção 11 falhariam pelo motivo certo. O campo `updated:` do frontmatter é
carimbado por `flush()` no momento da escrita, não por `render_markdown()`.

`flush()` compara `sha256(render_markdown())` com `meta["facts:rendered_hash"]`.
Barato o bastante para rodar a cada volta do `_memory_loop` (60s), e resolve o
critério de aceite "fato inserido via tool aparece em `fatos.md` na hora": a tool
chama `flush()` no fim do handler.

**A escrita precisa passar `confirm=True`.** `SecondBrain.update_note`
(`notes.py:188`) chama `self._confirm(confirm)`, e `_confirm` levanta
`ConfirmationRequired` quando `cfg.secondbrain.require_confirmation` é verdadeiro
(`notes.py:125`) — e no config real ele é `True`. Sem `confirm=True`, o primeiro
`flush()` de um arquivo já existente estouraria, e o `_memory_loop` engoliria a
exceção num `logger.warning` para sempre. O `confirm=True` aqui não é atalho de
segurança: `update_note` já faz backup antes de escrever
(`backup_before_write = True`) e o `fatos.md` é um arquivo **gerado**, não uma nota
editada por ela. `create_note` não tem esse problema (`confirm_create = False`), mas
o caminho de criação continua sendo um só: `create_note` na primeira vez,
`update_note(confirm=True)` depois.

Efeito colateral aceito: `update_note` chama `index.index_note(...)`, então o
`fatos.md` **entra** no índice do Second Brain. É por isso que a seção 4.8 o filtra
no `auto_context` — sem o filtro, o mesmo conteúdo chegaria duas vezes.

### 4.5 O arquivo `Memoria/fatos.md`

Região gerenciada entre marcadores; tudo fora deles é da Talita e o `flush()` não
toca:

```markdown
---
title: Memória durável
updated: '2026-11-11T18:04:22-03:00'
source: zaya
---

# Memória durável

> Escrito pela Zaya. As seções abaixo são geradas; o que você escrever fora dos
> marcadores é preservado. Para esquecer um fato, use `/memoria esquecer <chave>`
> — apagar a linha aqui não apaga no banco.

<!-- zaya:facts:begin -->
## Preferências

- `tom_de_voz`: prefere respostas curtas pela manhã
- `tratamento`: usar "você", nunca "tu"

## Pessoas

- `marina`: dentista, consulta em janeiro

## Projetos

- `briefing`: meio-dia mostra só tarefas normais

## Rotina

- `treino`: às 6h nos dias de semana

## Decisões

- `cafe`: cortar depois das 15h
<!-- zaya:facts:end -->

## Notas minhas

(espaço livre — a Zaya não escreve aqui)
```

Regras de formato, todas testáveis:

- seção = `slot` em português, sempre na mesma ordem (`preferencia`, `pessoa`,
  `projeto`, `rotina`, `decisao`); slot vazio não imprime a seção;
- linha = `` - `<key>`: <value> `` com `value` em uma linha só (quebras viram
  espaço) e cortado em 200 caracteres;
- dentro de cada seção, ordem alfabética de `key` — determinística, para o hash não
  mudar sem mudança de conteúdo;
- o parser de `sync_from_markdown()` casa exatamente
  `` ^- `([a-z0-9_]{1,60})`: (.+)$ `` **dentro dos marcadores**; linha que não casa
  é ignorada em silêncio (permite comentário e linha em branco).

### 4.6 Sync: só adiciona e atualiza

| Situação | O que o sync faz |
| --- | --- |
| chave no arquivo, chave ausente no banco | INSERT com `source='manual'`, `confidence=1.0` |
| chave nos dois, valor diferente | UPDATE no vivo (com supersessão) |
| chave nos dois, valor igual | nada |
| chave no banco, ausente no arquivo | **nada** — mantém, e o próximo `flush()` reimprime a linha |

A última linha é a regra que resolve a contradição do prompt original. Apagar a
linha no Obsidian **não** esquece: o `flush()` seguinte a reescreve. Esquecer é ato
deliberado (`/memoria esquecer`, `memory_forget`, ou o check-in). Isso é uma escolha,
não um esquecimento do plano: um arquivo que se edita no celular, com sync
automático, não pode ter deleção implícita — bastaria apagar sem querer uma linha
num vault sincronizado para perder um fato sem revisão nenhuma.

**Quando o sync roda:** no boot (`_start_background_workers`) e, a cada volta do
`_memory_loop`, se o `mtime` do arquivo mudou — o mesmo padrão de cache que
`_preferences_block` usa (`zaya/controller/registration.py:13-22`). `render_markdown`
é chamado sempre antes, então o hash nunca é comparado com conteúdo velho.

### 4.7 Semente do que já existe

`/memoria semear` (comando novo) lê `~/SecondBrain/memoria talita.md` — 34 bullets em
8 seções — e `preferences.md`, e insere com `source='manual'`. Mapeamento:

| Seção de origem | `slot` |
| --- | --- |
| 1. Identificação e preferências | `preferencia` |
| 2. Mestrado e pesquisa | `projeto` |
| 3. Carreira e tecnologia | `projeto` |
| 6. Projetos | `projeto` |
| 7. Objetivos e metas atuais | `decisao` |
| 4, 5, 8 e `## Relações` | fora (doutorado futuro, política, meta-instruções) |

`key` = slug do começo do bullet (até 40 caracteres, sem acento, `_` no lugar de
espaço). É one-off: roda uma vez, imprime quantos entraram e quanto ficou de fora
por teto de caracteres. Não roda sozinho no boot — semear duas vezes é ruído.

### 4.8 Quando o `fatos.md` aparece no prompt

No **prefixo estático**, ao lado de `preferences.md`, via parâmetro novo em
`compose_system` (seção 6.3). `fatos.md` fica **fora** do `auto_context`: ele já vai
inteiro no prefixo, e indexá-lo faria a mesma informação chegar duas vezes no turno.
O filtro é uma linha em `turns.py`, comparando `hit.rel_path` com o caminho relativo
de `fatos.md` calculado uma vez no boot (o mesmo que o `FactBook` usa para escrever).

---

## 5. Etapa 1 — `MemoryStore` e migração v5→v6

`SCHEMA_VERSION = 6` (`zaya/memory/store.py:19`) e um `if version < 6:` em `_migrate`
(`:104`), no mesmo formato de `_rename_modes_v3` e `_rename_session_column_v4`.

Métodos novos, todos em `MemoryStore` (SQLite puro, sem arquivo):

```python
upsert_fact(...)            # 4.3
forget_fact(slot, key) -> bool
restore_fact(slot, key) -> bool        # 4.3: desfaz esquecimento
live_facts(slots=None) -> list[Row]
fact_by_key(slot, key) -> Row | None
queue_candidates(rows) -> int          # INSERT OR IGNORE por (slot,key,state)
pending_candidates(limit) -> list[Row] # ORDER BY created_at, id
mark_candidates_shown(ids, when)
set_candidate_state(candidate_id, state)
```

Migração: criar as tabelas, depois `set_meta("schema_version", "6")`. Nada de
`ALTER TABLE` em `turns` — a FTS da etapa 2 é tabela virtual nova, com `rebuild`.

**A guarda é `version < 6`, não `== 5`.** Quem nunca rodou o código desta árvore está
em 4 e precisa da migração de fatos **e** da de `channel`; quem já rodou está em 5 e
precisa só da de fatos. Um `if version == 5` deixaria o banco v4 sem `facts` — e o
erro só apareceria no primeiro `upsert_fact`, longe da causa.

Teste obrigatório com fixture de verdade (`tests/test_memory.py` já tem
`test_migrates_old_database_without_mode` como modelo): abrir um banco v4 sintético,
confirmar que `schema_version` virou 6, que `facts` está vazia e que os turnos antigos
continuam lá (contagem antes/depois). Um segundo caso parte de um banco **v5**
(só `channel`) e confirma que ele também chega em 6 com `facts` criada.

---

## 6. Etapa 2 — `turns_fts` e `recall()`

### 6.1 `turns_fts`

Tabela virtual **externa** (`content='turns'`), não contentless: economiza espaço e
mantém `turns` como a única cópia do texto.

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(
    text,
    content='turns',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS turns_ai AFTER INSERT ON turns BEGIN
  INSERT INTO turns_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TRIGGER IF NOT EXISTS turns_ad AFTER DELETE ON turns BEGIN
  INSERT INTO turns_fts(turns_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;

-- Nao existe UPDATE em turns hoje (verificado: nenhum DELETE nem UPDATE), mas o
-- trigger custa nada e evita indice silenciosamente velho se um dia existir.
CREATE TRIGGER IF NOT EXISTS turns_au AFTER UPDATE OF text ON turns BEGIN
  INSERT INTO turns_fts(turns_fts, rowid, text) VALUES ('delete', old.id, old.text);
  INSERT INTO turns_fts(rowid, text) VALUES (new.id, new.text);
END;
```

Na migração, `INSERT INTO turns_fts(turns_fts) VALUES('rebuild')` — 759 turnos,
instantâneo. Método `rebuild_turns_fts()` exposto para `/memoria reindexar`.

`rowid` da FTS é o `id` do turno, o que torna o join trivial e a exposição
desnecessária.

### 6.2 `recall()`

```python
def recall_turns(
    self,
    query: str,
    *,
    project_path: str,
    limit: int = 3,
    exclude_conversation: int | None = None,
    exclude_after_id: int | None = None,
) -> list[sqlite3.Row]:
```

SQL base (sem a cláusula de exclusão, que entra condicionalmente — ver abaixo):

```sql
SELECT t.id, t.role, t.text, t.created_at, bm25(turns_fts) AS score,
       snippet(turns_fts, 0, '', '', '…', 12) AS snip
FROM turns_fts
JOIN turns t ON t.id = turns_fts.rowid
JOIN conversations c ON c.id = t.conversation_id
WHERE turns_fts MATCH ?
  AND c.project_path = ?
  AND t.role IN ('user', 'assistant')
ORDER BY score ASC
LIMIT ?
```

**Os dois parâmetros de exclusão andam juntos.** Se só `exclude_after_id` for passado,
um `(? IS NULL OR NOT (c.id = ? AND t.id > ?))` com o primeiro `?` em `NULL` vira
verdadeiro, e a exclusão **silenciosamente não acontece** — a assinatura aceita a
chamada, o resultado vem completo e ninguém percebe. Por isso o método monta a
cláusula em Python e só a inclui quando `exclude_conversation is not None`, em vez de
confiar no truque de `NULL` no SQL:

```python
sql = base
if exclude_conversation is not None:
    sql += " AND NOT (c.id = ? AND t.id > ?)"
    params += [exclude_conversation, exclude_after_id or 0]
```

Com `exclude_after_id = None` e conversa informada, `0` exclui a conversa inteira —
que é o comportamento certo quando não há histórico a preservar.

Três filtros que o prompt original não tinha, e sem eles o recall vira ruído:

1. **`project_path`** — `turns` pende de `conversations`, e `turns_between` já filtra
   por diretório (`store.py:394`). Sem isso, memória de outro projeto vaza.
2. **`role IN ('user','assistant')`** — os turnos `context` ("Notas usadas: …",
   "Metas relacionadas: …") são metadados da própria Zaya. Indexá-los e devolvê-los
   no recall é o modelo citando a si mesmo.
3. **Exclusão da conversa atual recente** — o que está no histórico do turno não
   precisa voltar pelo recall; é o mesmo texto duas vezes no prompt.

Construção do `MATCH`: a mesma de `SecondBrainIndex._tokens`
(`secondbrain/index.py:274`), tokens `\w+` de 2+ caracteres, deduplicados, `OR`,
cada um como prefixo:

```python
match = " OR ".join(f'"{token}"*' for token in tokens)
```

Prefixos em **todos** os tokens é a escolha, mas com expectativa calibrada — não é
stemming (seção 1.2). O que ele resolve e o que não resolve:

| resolve | não resolve |
| --- | --- |
| `decid` → `decidi`, `decidir`, `decidimos` | `decid` → `decisão` (derivação) |
| `trabalh` → `trabalho`, `trabalhei` | `preferência` ↔ `prefiro` |
| `cafe` ↔ `café`, `manha` ↔ `manhã` (diacrítico) | `emprego` ↔ `vaga` (sinônimo) |

Ou seja: prefixo melhora recall de flexão e cobre acento, e é **barato** (uma
consulta indexada, sem dependência). O que ele não cobre fica registrado como
limitação, não como promessa.

- token de 2 caracteres não entra (já é a regra do `_tokens`), para `de`/`da` não
  virarem curinga;
- todo token de 3+ caracteres vira prefixo, sem exceção: a diferença de qualidade
  entre 3–4 e 5+ não justifica a complexidade. O que controla ruído é o orçamento da
  seção 6.4, não o corte do token.

**Risco declarado do prefixo:** token curto com radical comum (`pref` casando
`prefeito`, `preferido`, `preferência`) traz ruído. Por isso o teto de itens é 3 e o
de caracteres é 700 — ruído pode roubar vaga, não inflar o prompt. Se a medição em
conversa real mostrar que isso acontece, a correção é subir o mínimo para 4–5
caracteres, não desligar o prefixo.

`bm25` devolve valores negativos; menor é melhor, daí `ORDER BY score ASC`. Scores
de `turns_fts` e de `SecondBrainIndex` **não são comparáveis** — a fusão não
intercala por score, ela reserva espaço para cada fonte (seção 6.4).

### 6.3 O bloco no turno

Duas partes, em dois lugares diferentes, por um motivo de cache:

| Bloco | Onde | Teto | Por quê ali |
| --- | --- | --- | --- |
| fatos vivos | prefixo estático (`compose_system`) | `facts_max_chars = 1000` | mudam raramente; ficam no prefixo cacheado |
| recall do turno | mensagem do usuário, antes do bloco do Second Brain | `recall_max_chars = 700` | muda a cada turno; nunca no system |

`compose_system` ganha o parâmetro, inserido **antes** do bloco de preferências:

```python
def compose_system(
    persona, preferences="", *, facts="", concise=True, short_prompt=False,
    fast=False, now=None,
) -> SystemPrompt:
    ...
    static_parts = [persona_text, ENVIRONMENT_NOTE]
    if facts:
        static_parts.append(f"{FACTS_HEADER}\n{facts}")      # antes de preferences
    cleaned = clean_preferences(preferences, persona_text)
    if cleaned:
        static_parts.append(f"{PREFERENCES_HEADER}\n{cleaned}")
    ...
```

Ordem importa porque o prompt é lido de cima para baixo: memória durável primeiro
(quem ela é), ajuste de interação depois (como falar). Inverter faria a persona
"aprender" o estilo antes de saber o conteúdo.

`FACTS_HEADER` ("[Memória durável da Talita — fatos confirmados]") é explícito para
o modelo não tratar como pedido. `preferences.md` não sai: os dois convivem
(451 caracteres limpos hoje).

**Custo de cache:** toda escrita em `facts` invalida o prefixo estático. É aceitável
e mensurável — fatos mudam algumas vezes por semana, não por turno. E é por isso que
o `flush()`/`sync` de arquivo **não** entram no caminho do turno: só a etapa 5 e as
tools mexem em `facts`.

`facts` vazio (banco novo, antes da semente) não imprime o cabeçalho — os
`tests/test_token_budget.py:35-48` que garantem prefixo estável continuam valendo
porque o valor só muda quando o conteúdo muda.

### 6.4 Orçamento explícito

O prompt original pede `k=5` e "respeita `context_max_chars`" (1200), mas um único
hit usa até 480 (`context_chars`). Cinco trechos estouram sozinhos, e o corte cai no
fim — justamente a informação mais nova. Partição nova:

```python
class MemoryConfig(BaseModel):
    ...
    facts_enabled: bool = True
    facts_max_chars: int = 1000
    recall_enabled: bool = True
    recall_results: int = 3
    recall_item_chars: int = 280
    recall_max_chars: int = 700
    extract_enabled: bool = True
```

- `recall_results = 3` (não 5): com `recall_item_chars = 280`, 3 itens cabem em 840;
  o teto de 700 corta o terceiro pela metade em vez de cortar o bloco inteiro;
- o bloco do Second Brain mantém `context_max_chars = 1200` e
  `context_results = 2` como estão;
- pior caso do turno: 1000 (fatos, só quando há) + 700 (recall) + 1200 (Second
  Brain) ≈ 2900 caracteres ≈ 720 tokens. É o teto duro, e é menos do que o
  `max_history_chars = 24000` gasta por turno.

**Dedupe.** `recall` e Second Brain podem devolver o mesmo conteúdo: a nota
`Memoria/Memória 2026-11-10.md` **parafraseia** os turnos do dia 10. Regra:

- descarta `recall` cujo turno pertence a uma conversa já coberta por um hit do
  Second Brain (mesmo `section`/`title` do dia), e vice-versa — na prática, se o
  bloco do Second Brain já trouxe a nota do dia, o `recall` só contribui com turnos
  de **outros** dias;
- descarta hit do Second Brain cujo `rel_path` seja o `fatos.md` (4.8);
- dentro do `recall`, dedupe por `id` de turno (o `JOIN` pode repetir por causa do
  snippet).

Implementado em `zaya/controller/recall.py` (novo, ~80 linhas) como função pura
`compose_memory_block(facts, recall_hits, sb_hits, cfg) -> str` — pura para ser
testável sem banco.

### 6.5 O gate novo

`needs_second_brain_context()` (`zaya/controller/intents.py:147`) é substituído por
`wants_memory(text)`:

```python
_QUESTION = re.compile(
    r"\b(qual|quais|quem|onde|quando|como|quanto|quant[oa]s?|"
    r"o\s+que|que\s+que|por\s+que)\b", re.I,
)
_POSSESSIVE = re.compile(r"\b(meu|minha|meus|minhas|comigo|pra\s+mim|para\s+mim)\b", re.I)
_RECALL = re.compile(
    r"\b(lembra|lembrar|mem[óo]ria|falamos|conversamos|combinamos|"
    r"decidim\w*|decidi|anotamos|registramos)\b", re.I,
)
_ANAPHORA = re.compile(r"^\s*(e|mas|ent[ãa]o)\b", re.I)

def wants_memory(text: str) -> bool:
    if not text or not text.strip():
        return False
    if _RECALL.search(text):
        return True
    return bool(
        (_QUESTION.search(text) or _POSSESSIVE.search(text))
        and _content_tokens(text)
    )
```

`_content_tokens` = tokens de 2+ caracteres fora de uma lista pequena de
stopwords (`a, o, e, de, que, do, da, em, um, uma, é, tá, pra, pro, me, te, se, no,
na, com, mais, menos`). Existe para "o que é?" não disparar busca.

Medição nos 369 turnos reais: **130 (35%)** disparam, contra 43 (12%) hoje.
Acerta todos os casos bons testados — `como tá meu dedo?`, `quem é a Marina?`,
`o que eu decidi sobre o briefing?`, `quais minhas metas?`, `o que eu estudo?`,
`como funciona meu briefing de meio dia?` — e rejeita todo o small talk testado
(`bom dia`, `oi`, `beleza`, `obrigada`, `kkkk`, `vou ver`, `pode ser`, `entendi`,
`sim`, `não`, `que horas são`, `tudo bem?`, `e aí`, `falou`, `vlw`): 16 de 16
rejeitados, 12 de 12 aceitos.

**Caso anafórico, declarado como limitação tratada.** `e o corte no dedo?` continua
fora do gate (`False`) e é justo isso: sem contexto, "o corte no dedo" não é
consultável sozinho. Tratamento em `turns.py`, não no gate: se `_ANAPHORA.match(text)`
e o texto tem 6 tokens ou menos, a consulta do recall é
`f"{último turno user} {text}"`. O gate não muda; a consulta melhora. Se o turno
anterior não existir, segue sem recall.

`/contexto` (`commands.py:558`) continua desligando tudo: `auto_context = false`
desliga o bloco do Second Brain **e** o recall, mas **não** o `fatos.md` no prefixo
(é memoria durável, não contexto recuperado). Documentar isso no `/ajuda`.

---

## 7. Etapa 3 — extração de fatos (uma passada por dia)

### 7.1 Os dois caminhos, e a guarda que impede os dois

O prompt original põe a extração em `DailyMemory.run()` (23h). Isso não funciona com
o check-in das 18h: às 18h de hoje, a extração do dia de hoje ainda não aconteceu, e
a revisão revisaria sempre ontem. Ordem corrigida:

| Caminho | Quando | Destino |
| --- | --- | --- |
| **A (normal)** | janela das 18h, dentro de `_check_memory_review` | `facts` para o confirmado, `discarded` para o resto |
| **B (fallback)** | consolidação das 23h, só se o caminho A não rodou hoje | `fact_candidates` `pending`, revisados amanhã |

Guarda única, os dois caminhos: `claim_meta(f"memory:extract:{dia}", "1")`. Se já
existe, nenhum dos dois roda. Isso garante **uma** chamada de LLM por dia, qualquer
que seja o caminho — a mesma promessa do check-in, centralizada numa chave só.

Se `review_enabled = false` (check-in desligado), o caminho B grava direto em `facts`
com `source='consolidacao'` e `confidence=0.7`. É a única porta de entrada sem
revisão humana, e ela é explícita: quem desliga a revisão está escolhendo confiar na
extração automática. `fatos.md` marca esses com `(auto)` no fim da linha, para a
diferença ser visível no Obsidian.

### 7.2 Extração por LLM (caminho A e B)

Entrada: **só as frases candidatas** da heurística (`_CUES` da seção 7.3), nunca o
dia inteiro — 16000 caracteres de conversa para extrair 5 fatos é caro e piora a
precisão. Saída: JSON estrito.

```json
[{"slot": "preferencia", "key": "tom_de_voz",
  "value": "prefere respostas curtas pela manhã"}]
```

Regras do prompt:

- não invente; descarte o ambíguo;
- sem dados de saúde, finanças ou terceiros (o caso "ansiedade antes de
  apresentação" é exatamente o que não deve entrar sem confirmação);
- devolva **todos** os que passarem, sem cortar por quantidade — o teto de 5 é da
  mensagem, não da limpeza;
- `key`: slug `[a-z0-9_]{1,40}`, estável, no mesmo idioma das existentes;
- `value`: uma frase, até 200 caracteres.

Falha, timeout (`review_timeout_seconds = 30.0`) ou JSON inválido → segue só com a
heurística; a noite não quebra. Lista vazia é resultado legítimo ("nada aqui vale
guardar") e encerra a noite sem mensagem — diferente de falha, que ainda tem a
heurística para mostrar.

Sessão dedicada `_review_session_id`, criada como `_initiative_session_id`
(`initiative.py:304`), para não poluir a conversa principal nem o cache de prompt.

### 7.3 Heurística local (barata, roda a cada minuto)

```python
_CUES = (
    "lembra que", "sempre", "nunca", "prefiro", "não gosto", "nao gosto",
    "decidi", "decidimos", "meu aniversário", "meu aniversario", "me chamo",
    "meu nome é", "meu nome e", "trabalho com", "moro em", "to morando",
)
```

- frase com dica e entre 8 e 220 caracteres → candidata;
- `slot` por palavra-chave (`prefiro|gosto|detesto` → `preferencia`;
  `trabalho|moro|aniversário` → `pessoa`/`rotina`; `decidi|vamos` → `decisao`;
  nome próprio + relação → `pessoa`);
- `key` = slug estável de até 40 caracteres;
- **dedupe:** se já existe `(slot, key)` em `fact_candidates` em **qualquer** estado
  (`pending`, `saved`, `discarded`) ou fato **vivo** em `facts`, ignora. É esta
  consulta, e não o índice parcial, que sustenta "descartado não volta".

Fonte dos turnos: `store.turns_between(início do dia, agora, project_path)`
(`store.py:394`), com filtro `id > self._review_scanned_turn` em Python. A janela é o
dia, não o banco inteiro: turno de ontem não vira sugestão de hoje, e a fila de
pendentes já carrega o atrasado. Reabrir a Zaya revarre o dia e o dedupe segura a
repetição.

### 7.4 O que a consolidação NÃO faz

`DailyMemory.consolidate` continua escrevendo `Memoria/Memória <dia>.md` como hoje,
com uma mudança pequena e independente. A nota do dia 09 lista a nota do dia 08 em
`## Registros do dia` porque o **registro** `kind='memory', title='Memória 2026-11-08'`
foi criado em 2026-11-09T20:36:49 (catch-up rodando no dia seguinte) — então ele cai
na janela do dia 09. O filtro não é "do próprio dia anterior", é mais simples e mais
correto: descartar `kind='memory'` cujo `title` não seja `Memória <dia>`.

```python
# consolidation.py, depois de records_between
records = [
    row for row in records
    if not (
        str(row["kind"]) == "memory"
        and str(row["title"] or "") != f"Memória {day.isoformat()}"
    )
]
```

Isso mantém a autoconsolidação de um dia que teve conversa (a nota do próprio dia
aparece como registro) e remove o registro de nota alheia. Uma condição em Python,
sem tocar no SQL do `records_between` — que é usado em outro lugar e não deve mudar de
semântica.

O resto da consolidação fica igual. Ela **não** apaga o mapa da revisão nem os
candidatos — a revisão de amanhã ainda precisa deles.

---

## 8. Etapa 4 — ferramentas de memória

Três tools novas, registradas em `build_tools` (`zaya/agent/tools/__init__.py:17`):

| Tool | Confirma? | Efeito |
| --- | --- | --- |
| `memory_remember` | não (aditivo) | `upsert_fact(source='tool')` + `flush()` |
| `memory_forget` | `requires_approval`, mas ver abaixo | `forget_fact()` + `flush()` |
| `memory_list` | não (leitura) | `live_facts()` formatado por slot |

Parâmetros de `memory_remember`: `slot` (enum, obrigatório), `key` (string,
`pattern ^[a-z0-9_]{1,60}$`), `value` (string, 1–400 caracteres). Não aceita
`confidence` nem `source` — são do sistema, e uma tool que escreve o próprio score
de confiança é uma tool que mente.

**Injeção do store.** Hoje `AgentClient` cria `ToolContext(directory,
allow_outside_project)` em `runtime.py:71-73` e `build_tools` não recebe mais nada.
O caminho mínimo é um campo opcional no contexto:

```python
@dataclass
class ToolContext:
    directory: Path
    allow_outside_project: bool = True
    memory: MemoryStore | None = None    # None => tools de memoria indisponiveis
```

E `AgentClient.attach_memory(store)` chamado em `_build_app`
(`zaya/cli_runtime.py:35`), onde `store` e `client` já existem. As tools
**verificam** `ctx.memory is None` e devolvem `ToolResult.failure("memória
indisponível")` — nada de `assert`. Isso importa porque `zaya doctor`
(`cli_commands.py:90`) cria um `AgentClient` sem store e não pode quebrar.

A confirmação de `memory_forget` **não** usa `ui.confirm` de dentro da tool: a tool
roda no runtime, que já tem o fluxo de permissão (`runtime.py:714`,
`_ask_permission`). `memory_forget` declara `requires_approval=True` e herda esse
caminho — o mesmo de `edit` e `write`. Só que hoje ele não dispara (ver abaixo).

**Mas `requires_approval` sozinho não confirma.** A guarda é
`if tool.requires_approval and not self.cfg.agent.auto_approve`
(`zaya/agent/runtime.py:712`), e o `~/.config/zaya/config.toml` real tem
`auto_approve = true` (linha 27). Com isso, `memory_forget` esqueceria sem perguntar
nada — e o mesmo vale para `edit`, `write` e `bash`, que é uma decisão já tomada no
repo, não algo que este plano deva mudar.

A consequência para a memória é direta: **a segurança do esquecimento não pode vir
de um diálogo.** Ela vem de duas coisas:

1. `forget_fact` é **reversível** — `state='forgotten'` guarda o valor, e
   `/memoria lembrar <chave>` devolve o fato (mesmo `id`, `state='live'`). Nada é
   perdido no esquecimento, então um erro de um turno custa um comando, não um fato.
2. a tool exige o `key` **exato**; `memory_forget` não aceita `slot` sozinho, e não
   aceita padrão/glob. Esquecer "todas as preferências" não é expressável.

`/memoria esquecer <chave>` no chat segue a mesma regra, com uma diferença: o comando
é explícito da pessoa, então ele confirma na hora (`ui.confirm`) e mostra o valor que
vai sair. É a única porta com diálogo, justamente porque é a única em que a pessoa
está no comando. Se `auto_approve` for desligado depois, `requires_approval` passa a
valer e a tool ganha o diálogo de graça — mas o desenho não depende disso.

Slots em português no schema da tool (`preferencia`, `pessoa`, `projeto`, `rotina`,
`decisao`) e não em inglês: o modelo é instruído em português e a chave errada vira
duas linhas para o mesmo fato.

---

## 9. Fora de escopo (v1), com critério para voltar

| Fora | Por quê | Quando volta |
| --- | --- | --- |
| `sqlite-vec` + `fastembed` | baixa modelo; colide com "zero dependência obrigatória" | quando a taxa de miss medida em consultas reais passar de ~30% |
| recall **semântico** | FTS5 é léxico: `decid` não acha `decisão`, `preferência` não acha `prefiro` e `emprego` não acha `vaga` (medido na seção 1.2) | quando a taxa de miss em consultas reais passar de ~30%, medida por um log de `recall` sem hit; até lá o `fatos.md` cobre variação de vocabulário |
| `_pendentes.md` | substituído por `fact_candidates` | nunca |
| `porter` | não faz stemming de português (medido) | nunca |
| write-through no `MemoryStore` | store não escreve arquivo neste repo | nunca |
| ligar `_proactive_loop` na TUI | contexto de mensagem proativa é outro assunto (ver check-in, seção 14) | etapa própria |
| `confidence < 0.6` como filtro | sem escala calibrada, o número é decorativo | depois de ~30 revisões reais, para calibrar |
| `action: forget` automático | esquecer sem confirmar é irreversível | nunca sem revisão |

---

## 10. Pontos de edição

| Arquivo | Mudança |
| --- | --- |
| `zaya/memory/store.py` | `SCHEMA_VERSION = 6`; `_migrate_v6`; `facts`, `fact_candidates`, `turns_fts` + triggers; `upsert_fact`, `forget_fact`, `restore_fact`, `live_facts`, `fact_by_key`, `queue_candidates`, `pending_candidates`, `mark_candidates_shown`, `set_candidate_state`, `recall_turns`, `rebuild_turns_fts` |
| `zaya/memory/facts.py` | **novo**: `FactBook` (4.4) |
| `zaya/controller/recall.py` | **novo**: `compose_memory_block` (6.4) |
| `zaya/controller/intents.py` | `wants_memory()`; `needs_second_brain_context()` vira alias até a etapa 4 | 
| `zaya/controller/prompt.py` | `compose_system(..., facts="")`; `FACTS_HEADER`; corte por `facts_max_chars` |
| `zaya/controller/turns.py` | gate novo; recall do turno; dedupe; filtro do `fatos.md`; `attach` |
| `zaya/controller/memory.py` | `_memory_loop`: `sync`/`flush` do `FactBook` a cada volta; extração fallback em `_maybe_consolidate` |
| `zaya/memory/consolidation.py` | após `records_between`, filtrar `kind='memory'` de outro dia (7.4) |
| `zaya/agent/tools/base.py` | `ToolContext.memory: MemoryStore | None` |
| `zaya/agent/tools/memory.py` | **novo**: as três tools |
| `zaya/agent/tools/__init__.py` | registrar quando `ctx.memory` existir |
| `zaya/agent/runtime.py` | `attach_memory(store)` |
| `zaya/cli_runtime.py` | `client.attach_memory(store)` em `_build_app` |
| `zaya/controller/commands.py` | `/memoria` (lista), `/memoria esquecer <chave>`, `/memoria lembrar <chave>`, `/memoria semear`, `/memoria reindexar` |
| `zaya/config.py` | campos de 6.4 em `MemoryConfig`, `_ENV_MAP`, TOML padrão |
| `.env.example` | `ZAYA_MEMORY_FACTS_ENABLED`, `ZAYA_MEMORY_RECALL_ENABLED`, `ZAYA_MEMORY_RECALL_RESULTS`, `ZAYA_MEMORY_RECALL_MAX_CHARS` |
| `README.md` | 4 linhas em "Ajustando o ritmo dela" + 1 linha sobre `Memoria/fatos.md` |

`/memoria` já existe hoje (`commands.py:128`) e só consolida. Passa a ter
subcomando, mantendo `/consolidar` e `/memoria` sem argumento como estão — é a mesma
rota, com `argument` interpretado.

---

## 11. Testes

`tests/test_memory_facts.py` (novo):

1. **Migração →v6**: banco v4 (e v5) sintético abre, `schema_version` = 6, turnos
   preservados, `facts` vazia.
2. **Supersessão**: `upsert` do mesmo `(slot, key)` com valor novo → antiga
   `state='superseded'` + `superseded_by`; só uma `live`.
3. **Idempotência**: `upsert` com valor igual (inclusive variação de caixa/espaço)
   não cria linha nova e não muda o `id`.
4. **Esquecer libera a chave**: `forget_fact` → `state='forgotten'`; `upsert` do
   mesmo `key` depois funciona (é a razão do índice por `state`, não por
   `superseded_by`).
5. **`render_markdown` determinístico**: duas chamadas seguidas → string idêntica;
   ordem alfabética; seção vazia não imprime.
6. **Marcadores preservados**: `fatos.md` com texto fora dos marcadores → `flush()`
   não toca no texto; só a região interna muda.
7. **Sync aditivo (a regra de 4.6)**: linha apagada do arquivo **não** apaga do
   banco; `flush()` seguinte reescreve a linha. Este é o teste que impede a
   regressão da contradição do prompt original.
8. **Sync de criação**: linha nova no arquivo vira fato com `source='manual'`.
9. **Hash**: `flush()` duas vezes sem mudança escreve uma vez só (segunda devolve
   `False`).

`tests/test_memory_recall.py` (novo):

10. **FTS sincronizada**: `add_turn` → `recall_turns` acha; contagem de
    `turns_fts` = contagem de `turns`.
11. **Filtro de projeto**: turno de `ProjectA` não aparece no recall de `ProjectB`.
12. **Turno `context` fora**: `add_turn(..., "context", "Notas usadas: x")` não é
    retornado.
13. **Conversa atual excluída**: turno recente da própria conversa não volta.
14. **Prefixo é flexão, não stemming**: inserir "decidi cortar cafe"; buscar `decid`
    → 1 hit; buscar `decisão` → **0** hits (documenta a limitação da seção 1.2, em
    vez de prometer o que não acontece).
15. **Sem diacrítico**: `cafe` acha `café` e vice-versa.
16. **Rebuild**: apagar `turns_fts` por fora e chamar `rebuild_turns_fts()` → volta a
    bater com `turns`.

`tests/test_memory_prompt.py` (novo):

17. **Orçamento**: 3 hits de 280 + bloco de 700 → total ≤ `recall_max_chars`.
18. **Dedupe**: nota do dia no bloco do Second Brain + turno do mesmo dia no recall →
    só um aparece.
19. **`fatos.md` fora do `auto_context`**: hit com `rel_path == fatos.md` é filtrado.
20. **Prefixo estável**: `compose_system` com o mesmo `facts` em horas diferentes →
    `static` idêntico (extensão de `test_token_budget.py:35`).
21. **`facts` vazio**: nenhum cabeçalho, `static` igual ao de hoje.

`tests/test_intents.py` (extensão):

22. `wants_memory` dispara nos 12 casos bons e rejeita os 16 de small talk da
    seção 6.5, como tabela parametrizada.

`tests/test_memory_consolidation.py` (extensão):

23. Nota de um dia sem conversa **não** lista a nota de outro dia, mesmo quando o
    registro de memória caiu na janela do dia (catch-up).

`tests/test_memory_tools.py` (novo):

24. `ctx.memory is None` → `memory_remember` devolve falha, não levanta.
25. `memory_remember` grava, `memory_list` mostra, `flush()` reflete no arquivo.
26. `memory_forget` declara `requires_approval=True`, mas **esquece mesmo com
    `auto_approve=True`** (é o comportamento real do runtime); o teste garante que
    `forget_fact` deixa `state='forgotten'` e que `restore_fact` devolve `live`.
27. `restore_fact` com chave ocupada por fato vivo devolve `False` e não altera nada.

---

## 12. Critérios de aceite

- [ ] `pytest` verde, incluindo `test_memory.py`, `test_memory_consolidation.py`,
      `test_token_budget.py`.
- [ ] Banco v4 real (cópia) abre, migra para 6, contagem de turnos igual.
- [ ] `Memoria/fatos.md` é gerado a partir de `/memoria semear` com o conteúdo de
      `memoria talita.md`; texto fora dos marcadores sobrevive a dois `flush()`.
- [ ] Editar valor no arquivo e rodar `sync_from_markdown()` reflete no banco;
      **apagar** a linha não apaga.
- [ ] `memory_forget` → `state='forgotten'`; `/memoria lembrar <chave>` devolve o
      fato com o mesmo `id`.
- [ ] Atualizar fato via tool aparece no arquivo no mesmo turno.
- [ ] Num banco sintético de 2 meses atrás: `recall("decid")` acha um turno com
      "decidi", `cafe` acha "café", e `recall("vaga")` **não** acha "emprego"
      (limitação declarada na seção 1.2, testada em vez de mascarada).
- [ ] Turno de outro `project_path` não aparece no recall.
- [ ] `wants_memory` dispara em ≥ 30% dos 369 turnos reais e em 0 dos 16 casos de
      small talk.
- [ ] Bloco do turno ≤ 2900 caracteres no pior caso, medido por teste.
- [ ] Nenhuma dependência nova em `pyproject.toml`.
- [ ] `/contexto off` desliga recall e Second Brain, mantém `fatos.md`.
- [ ] Nenhum arquivo `_pendentes.md` é criado em nenhum caminho.

---

## 13. Ordem de execução

| Etapa | Entrega | Depende de | `pytest` entre |
| --- | --- | --- | --- |
| 0 | schema v6 + `facts`/`fact_candidates` + `FactBook` | — | sim |
| 1 | migração v4/v5→v6 testada + `/memoria semear` + `fatos.md` | 0 | sim |
| 2 | `turns_fts` + `recall_turns` | 0 | sim |
| 3 | `wants_memory` + bloco no turno + orçamento | 0, 1, 2 | sim |
| 4 | tools `memory_remember`/`forget`/`list` | 1 | sim |
| 5 | check-in das 18h (`docs/memoria-checkin-18h.md`) | 0, 1, 3 | sim |

A ordem muda em um ponto em relação ao prompt original: **o gate (etapa 3) vem antes
das tools (etapa 4)**. O gate é o que faz o trabalho aparecer; tools sem gate
funcionando ainda deixam a memória invisível na conversa. E as tools são a parte
mais fácil de adiar sem prejuízo — o check-in grava fatos sem elas.

A etapa 5 continua por último e continua sendo a mais fácil de remover:
`review_enabled = false` devolve o sistema ao estado da etapa 4.

---

## 14. Ajustes no documento do check-in (aplicados)

`docs/memoria-checkin-18h.md` foi escrito antes deste plano e continua válido. Três
pontos ficaram desalinhados com as decisões de 4.1 e 7.1 e foram corrigidos lá —
edições pontuais, não uma reescrita:

1. **`superseded_by` (seção 4.2 do check-in)** — o documento não afirmava o predicado
   errado, mas também não dizia qual é. Ficou explícito que o índice único de `facts`
   usa `WHERE state = 'live'`, **não** `WHERE superseded_by IS NULL`: `forget_fact`
   deixa `superseded_by` nulo e a chave precisa ser liberada para um novo `upsert`.
2. **Seção 3, linha das 23h** — "fatos confirmados viram `fatos.md`" está certo, mas
   faltava dizer que a extração das 23h é **fallback**: com a revisão ativa, a extração
   acontece às 18h e a consolidação não extrai nada (seção 7.1 daqui). A linha da
   tabela agora carrega essa condição e a chave da guarda (`memory:extract:<dia>`).
3. **Seção 5** — os campos `review_*` continuam como estão; foi acrescentado que se
   somam a eles os campos de 6.4 (`facts_enabled`, `recall_*`, `extract_enabled`),
   todos planos em `MemoryConfig`, pelo mesmo motivo: `_apply_env` (`config.py:654`)
   só escreve um nível.

Nada além disso. O fluxo, a gramática, o mapa em `meta`, o `count=False`, o
`speak=False` e o silêncio que não descarta continuam como estão documentados lá.

---

## 15. Notas de decisão

**Por que a extração faz uma passada e não duas.** Duas passadas (18h e 23h) dariam
fatos melhores no fim do dia — a conversa da noite entra. Mas custam duas chamadas de
LLM, e a segunda não tem revisão humana, o que reintroduz exatamente o problema que o
check-in existe para resolver. Uma passada, com fallback quando a primeira não
aconteceu, é a troca certa: o que acontece depois das 18h entra amanhã.

**Por que `state` e não só `superseded_by`.** O prompt original previu uma saída
(mudou) e a realidade tem duas (mudou, esqueceu). Enfiar "esqueceu" em `superseded_by`
com um ponteiro inventado falseia o histórico que a coluna existe para preservar.

**Por que o `fatos.md` usa marcadores.** A alternativa é reescrever o arquivo
inteiro a cada `flush()`, e aí qualquer anotação da Talita no mesmo arquivo some. Com
marcadores, o custo é um `re.search` e a recompensa é que o arquivo pode ser lido e
comentado no Obsidian sem que a Zaya apague nada. É o mesmo espírito de
`_preferences_block`: o arquivo é dela, a Zaya só mantém uma região.

**Por que `flush()` por hash e não por flag em memória.** Flag em memória não
sobrevive a reinício, e a primeira volta depois de reiniciar escreveria de novo sem
necessidade. Hash em `meta` é persistente, barato (uma linha) e faz o teste 9 ser
possível.

**Por que o gate novo reaproveita `_QUESTION` e `_POSSESSIVE`.** Porque o modo de
falha hoje é **silencioso**: a busca não roda e ninguém sabe. Um gate por pergunta
explícita é compreensível pela pessoa ("ele procurou porque eu perguntei"), enquanto
um classificador treinado seria opaco. 35% de disparo com 0% de falso positivo no
small talk é um bom ponto de operação; se a taxa subir demais, o orçamento de
caracteres é o que segura o custo, não o gate.

**Por que o recall não entra no prefixo estático.** Porque ele muda a cada turno e
invalidaria o cache do provedor em toda mensagem. O prefixo carrega o que é estável
por natureza (`persona`, `preferences.md`, `fatos.md`); o recall entra no fim, junto
do pedido, onde o cache não chega. É a mesma arquitetura que `prompt.py` já
documenta no topo do arquivo.

**Por que a etapa 2 não depende da etapa 1.** `turns_fts` é sobre `turns`, não sobre
`facts`. Dar recall antes de ter `fatos.md` já melhora a conversa, e separa os dois
riscos: um é tokenizer/migração, o outro é formato de arquivo/sync. Juntar as duas
numa etapa só faria um erro de FTS parecer um erro de sync.
