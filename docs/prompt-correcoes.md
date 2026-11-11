# Tarefa: corrigir lembretes atrasados, escopo do "sempre permitir" e truncamento de saída do bash

Você vai implementar três correções pontuais no projeto Zaya (Python 3.12, Textual TUI,
assistente pessoal local). São independentes entre si, mas devem ser entregues juntas.

## Contexto obrigatório

Leia antes de editar:

- `README.md` — comportamento esperado do agente e economia de token.
- `zaya/agent/runtime.py` — loop do agente, permissões, execução de ferramentas.
- `zaya/agent/session.py` — `AgentSession` e `SessionStore`.
- `zaya/agent/tools/shell.py` — ferramenta bash.
- `zaya/controller/initiative.py` — loop proativo e lembretes.
- `zaya/controller/voice.py` — loop contínuo de voz (também dispara lembretes).
- `zaya/memory/store.py` — SQLite (schema v4), tabela `reminders`.
- `zaya/config.py` — `ProactiveConfig` e `AgentConfig`.
- `tests/test_memory.py`, `tests/test_proactive.py`, `tests/test_turns_events.py` — padrão de teste.

## Regras de escopo (importantes)

1. **Não faça commit nem push.** Deixe o diff para revisão.
2. O working tree já tem alterações não commitadas em `README.md`, `config.py`,
   `modes.py`, `textual_app.py`, `textual_render.py`, `tests/` e `uv.lock`.
   **Não reverta, não reformate e não toque** nesses arquivos além do estritamente
   necessário para a Tarefa 1.2.
3. Comentários e mensagens de UI em português, seguindo o estilo do repo
   (comentários sem acento: "sessao", "execucao").
4. Não introduza dependência nova. Não altere o schema do SQLite nesta tarefa.
5. Rode `uv run pytest` ao final de cada tarefa, não só no fim.

---

## Tarefa 1 — Lembretes perdidos não devem tocar fora de hora

### Problema

`zaya/controller/initiative.py:222` (`_fire_due_reminders`) chama
`self.store.due_reminders(now.isoformat())`, que retorna **tudo** que venceu
(`zaya/memory/store.py:426`), e dispara cada item individualmente com
`_say_proactive`. Se a Zaya fica fechada das 8h às 23h, o lembrete das 8h toca
às 23h com a mesma urgência de um lembrete de agora — e se houver cinco
vencidos, são cinco falas seguidas.

O mesmo método é chamado por dois caminhos, que precisam ter comportamento
idêntico: `_proactive_loop` (`initiative.py:104`) e o loop de voz
(`voice.py:58-61`).

### Comportamento esperado

1. Um lembrete vencido há menos de `grace` horas dispara normalmente,
   como hoje (texto: `"Lembrete: {texto}"`, `count=False`).
2. Um lembrete vencido há `grace` horas ou mais é considerado **perdido**.
3. Lembretes perdidos **não** tocam individualmente. No lugar, sai **uma única**
   mensagem agregada, também com `count=False`:
   - 1 perdido: `"Enquanto eu estava fora, venceu este lembrete: {texto} ({quando})."`
   - N perdidos: `"Enquanto eu estava fora, venceram {N} lembretes: {t1} ({q1}), {t2} ({q2})…"`
   - Limite de 5 itens na lista; o resto vira `"e mais {k}"`.
   - `{quando}` formatado como `%d/%m às %H:%M`.
4. Em ambos os casos o lembrete recebe `mark_reminder_spoken`, para não repetir.
5. A mensagem agregada sai **depois** dos lembretes recentes, para que o que é
   urgente apareça primeiro.

### Onde mexer

- `zaya/config.py`, em `ProactiveConfig` (linha ~222): adicionar
  `reminder_grace_hours: float = 3.0` com comentário explicando que é o limite
  entre "atrasado" e "perdido".
- `zaya/memory/store.py`: adicionar método `due_reminders_split(now_iso, cutoff_iso)`
  que devolve `(recentes, perdidos)` em uma única query, mantendo `due_reminders`
  intacto (é usado em `voice.py:58` como sinal de "tem algo devido").
  Respeite o padrão de lock: `with self._lock:`.
- `zaya/controller/initiative.py`: reescrever o corpo de `_fire_due_reminders`
  usando o novo método. Não duplique a lógica em `voice.py` — ele já delega.

### Testes

Em `tests/test_memory.py` (perto de `test_reminders`, linha ~151):

- lembrete vencido há 10 min cai em "recentes";
- lembrete vencido há 5h cai em "perdidos";
- ambos os grupos vêm ordenados por `due_at`;
- `due_reminders` continua com o comportamento antigo (não quebrar `voice.py`).

Em `tests/test_proactive.py`: teste do texto agregado — 1 item, N itens, e o
caso de corte em 5 com "e mais k".

### Critério de aceite

Com `reminder_grace_hours = 3.0`, criar dois lembretes vencidos (um de 10 min,
um de 5h) e chamar `_fire_due_reminders`: deve sair uma fala de lembrete normal
e **uma** fala agregada, nessa ordem, e os dois ficam `spoken = 1`.

---

## Tarefa 2 — `always` deve valer só para a sessão atual

### Problema

`zaya/agent/runtime.py:77` declara `self._always_approved: set[str] = set()`,
global e em memória. Em `runtime.py:707-713`, responder `always` a um
`PermissionAsked` adiciona o nome da ferramenta a esse set **para sempre**
(na prática, até o processo reiniciar) e **para todos os projetos**.

Consequência: aprovar `bash` uma vez no projeto A libera shell silenciosamente
no projeto B, sem nenhuma confirmação. A intenção de `always` é "confio nisto
nesta tarefa", não "confio em shell para sempre".

### Comportamento esperado

1. A aprovação `always` passa a valer apenas para a `AgentSession` corrente.
2. Trocar de conversa/modo (`_switch_conversation` em `controller/orchestrator.py`,
   que já chama `_clear_stale_session`) faz o escopo voltar a zero.
3. `auto_approve = true` continua funcionando como hoje, sem passar por esse set.
4. Nada disso é persistido em disco: reiniciar a Zaya limpa as aprovações.
   (É intencional — não adicione campo em `to_dict`/`from_dict`.)

### Onde mexer

- `zaya/agent/session.py`: em `AgentSession`, adicionar
  `always_approved: set[str] = field(default_factory=set, compare=False, repr=False)`.
  Manter fora de `to_dict()`/`from_dict()`.
- `zaya/agent/runtime.py`: remover `self._always_approved` (linha 77) e trocar o
  uso em `runtime.py:707-713` por `session.always_approved`. O `_session()` já
  devolve a `AgentSession` no escopo correto.
- Confira que `reply_permission` (`runtime.py:~770`) e o callback do Telegram
  (`telegram/service.py:28`, que devolve `"always"`) continuam funcionando —
  eles só respondem à permissão; quem guarda o set é o runtime.

### Testes

Em `tests/test_turns_events.py` (perto de `test_auto_approve_nao_pergunta`, linha ~149)
ou `tests/test_agent.py`:

- responder `always` a `bash` e, na **mesma** sessão, o segundo `bash` não emite
  `PermissionAsked`;
- em **outra** sessão do mesmo runtime, `bash` volta a emitir `PermissionAsked`;
- `auto_approve = True` não emite `PermissionAsked` em nenhuma sessão.

### Critério de aceite

Aprovar `bash` com `always` na conversa A e mandar um `bash` na conversa B:
a B precisa pedir confirmação.

---

## Tarefa 3 — Truncar a saída do bash mantendo o fim, não o começo

### Problema

`zaya/agent/tools/shell.py:39-40`:

```python
if len(output) > OUTPUT_LIMIT:
    output = output[:OUTPUT_LIMIT] + f"\n\n[saida truncada em {OUTPUT_LIMIT} caracteres]"
```

Mantém os **primeiros** 20.000 caracteres. Para saída de comando, o que importa
está no fim: o erro do pytest, o final do traceback, o resumo do build, o
`FAILED`/`ERROR`. Hoje a Zaya recebe o cabeçalho e perde a conclusão.

### Comportamento esperado

1. Manter os **últimos** `OUTPUT_LIMIT` caracteres.
2. Prefixar um marcador explícito, para o modelo saber que houve corte:
   `[inicio da saida omitido — mostrando os ultimos {OUTPUT_LIMIT} caracteres]`
3. Não mudar `OUTPUT_LIMIT` (20000) nem o formato do resto da função.
4. O caso `returncode != 0` continua igual: `ToolResult.failure` com `[exit N]`.

### Testes

Criar `tests/test_shell_tool.py` (se não existir teste de ferramenta de shell;
verifique antes em `tests/test_agent.py`):

- comando que gera saída curta: sai intacto, sem marcador;
- comando que gera saída longa (ex.: `python -c "print('a'*30000 + 'FIM')"`):
  o resultado contém `FIM`, **não** contém o começo, e contém o marcador;
- comando que falha com muito stderr: o final do erro aparece no resultado.

---

## Verificação final

1. `uv run pytest` — tudo verde, incluindo os testes antigos de
   `test_reminders`, `test_permissions`, `test_turns_events` e `test_telegram`.
2. `uv run ruff check zaya tests` — sem novos avisos.
3. Revisar o diff: `git diff --stat` não pode listar arquivos fora de
   `zaya/agent/`, `zaya/controller/initiative.py`, `zaya/config.py`,
   `zaya/memory/store.py` e `tests/`.
4. Entregar um resumo curto: o que mudou, os testes novos, e **o que ficou de
   fora** e por quê.

---

## Fora de escopo (não faça)

- Migração de schema / coluna `missed_policy` por lembrete. Uma política global
  em `ProactiveConfig` resolve o caso real com diff menor; por-lembrete só faz
  sentido quando existir UI para escolher.
- Fila de mensagens na TUI (`textual_app.py:584`), que ainda responde
  "aguarde o turno atual…".
- Reconciliação de turno interrompido em `SessionStore.load`.
- Ferramentas `task`/`todowrite`/`webfetch` citadas em `TOOL_LABELS`
  (`controller/turns.py:62-63`) mas inexistentes em `build_tools`.
- Qualquer coisa em `zaya/browser/`, `zaya/telegram/` fora do citado na Tarefa 2.

---

## Notas de decisão

Duas observações sobre escolhas feitas neste prompt.

**Tirei a coluna `missed_policy` do banco.** No relatório anterior eu sugeri coluna
por lembrete, copiando o `missedPolicy` do `nightly-labs/openbot`. Pensando no caso
real da Zaya, uma coluna sem interface para escolher não serve para nada — ninguém
vai editar SQLite à mão. Uma política global em `ProactiveConfig` resolve o problema
com diff bem menor e zero risco de migração. Deixei registrado no "fora de escopo"
para não parecer esquecimento.

**A Tarefa 2 é a mais delicada**, não a mais simples. `always_approved` é um `set`
compartilhado e o Telegram depende dele devolver `"always"`. Mudei o desenho de
"dict no runtime" para "campo na `AgentSession`" porque `_session()` já resolve o
escopo certo — e por isso o critério de aceite é o teste entre duas conversas, não
só a mudança de linha.
