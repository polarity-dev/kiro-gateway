---
name: enable-claude-code
description: Rendere una repo Kiro compatibile con Claude Code. Usare SEMPRE quando l'utente chiede di far funzionare, allineare, adattare, configurare, portare, o convertire una repo per Claude Code, dual-mode, o entrambi gli IDE. Migra steering a skill, crea symlink e CLAUDE.md.
---

# Enable Claude Code in a Kiro repo

Trasforma una repo che oggi usa solo `.kiro/steering/` in una repo che gira nativamente sia in **Kiro** che in **Claude Code**, con **una sola copia fisica** dei contenuti. Nessuna duplicazione, nessun formato proprietario.

Il porting segue quattro assi:

1. **Steering triggerati → skill.** I file in `.kiro/steering/` con `inclusion: manual` o `inclusion: fileMatch` diventano `.kiro/skills/<name>/SKILL.md`. Gli always-on restano dove sono.
2. **Symlink, non copie.** `.claude/skills` → `.kiro/skills`. `.mcp.json` → `.kiro/settings/mcp.json`. Una sola sorgente di verità, entrambi gli IDE la leggono.
3. **`CLAUDE.md` come entrypoint** che importa gli steering rimasti via `@` (sintassi supportata da Claude Code). Zero duplicazione di contenuto.
4. **Ref cleanup.** La sintassi `#nome-skill` (mai stata valida in Claude Code, oggi non più valida neanche in Kiro per skill non-steering) viene sostituita con `` `nome-skill` `` puro.

Non è un'operazione che l'agente deve fare da solo in silenzio. È guidata: a ogni step mostri all'utente cosa stai per toccare, aspetti il "ok".

---

## Come si invoca

L'utente entra in una qualsiasi repo Kiro e dice qualcosa come:

- *"Mi rendi questa repo compatibile con Claude Code?"*
- *"Convertila in formato duale Kiro / Claude Code"*
- *"Porta gli steering triggerati a skill"*

La skill si auto-attiva dalla `description`. Se non lo fa, l'utente può digitare `/enable-claude-code` (Claude Code) o selezionarla dal picker `/` (Kiro).

---

## Il runbook

Lavora in ordine. Ogni step ha un check di sanità prima di scrivere.

### Step 0 — Preflight

Verifica in ordine. Se qualcosa non torna, dillo e fermati:

- **Git repo pulito.** `git status --porcelain` deve tornare vuoto. Se ci sono modifiche uncommitted, chiedi all'utente di committare o stashare prima. Non partiamo su lavoro sporco.
- **Directory `.kiro/steering/` esiste.** Altrimenti non c'è nulla da migrare: probabilmente la repo non è una Kiro repo. Fermati.
- **Non è già dual-mode.** Se esistono già tutti e tre: `.claude/skills` (symlink), `CLAUDE.md`, `.kiro/skills/` non vuota, la migrazione è già stata fatta. Chiedi all'utente cosa vuole: rieseguire per aggiungere nuovi steering emersi nel frattempo, o uscire.

### Step 1 — Classificare gli steering

Leggi ogni file `.kiro/steering/*.md` e guardane il frontmatter. Kiro supporta tre valori di `inclusion`:

| inclusion | Cosa fa Kiro | Cosa facciamo noi |
|---|---|---|
| `always` (o assente) | Contesto sempre attivo, in ogni chat | **Resta steering.** Verrà importato da `CLAUDE.md` via `@`. |
| `fileMatch` (con `fileMatchPattern`) | Attivato quando si apre un file che matcha | **Migra a skill.** La skill si auto-triggera dalla `description`. Se il fileMatchPattern è forte (es. `**/*.tf`), menzionalo nella description ("Usare quando si lavora su file Terraform"). |
| `manual` | Solo quando invocata con `#nome` | **Migra a skill.** È il caso più frequente. |

Se il file non ha frontmatter `inclusion`, guarda il contenuto: se descrive un flusso operativo triggerato (es. "quando l'utente chiede X, fai Y"), è una skill mascherata; se è un contesto stabile (glossario, ruolo dell'utente, principi generali), è steering.

**Presenta la classificazione all'utente prima di procedere.** Formato:

```
Steering trovati (N file):

  Restano come steering (always-on):
    - personal-settings.md       ← ruolo utente
    - knowledge-manager.md       ← contesto sempre attivo

  Diventano skill:
    - transcribe-audio.md         → .kiro/skills/transcribe-audio/
    - process-pdf.md              → .kiro/skills/process-pdf/
    - git-sync.md                 → .kiro/skills/git-sync/
    ...

  Ambigui — decidi tu:
    - foo.md                      ← non ha inclusion, contenuto poco chiaro
```

Sugli ambigui, mostra all'utente le prime 20 righe del file e chiedi "steering o skill?". Non decidere per lui.

### Step 2 — Migrazione

Per ogni file classificato come **skill**:

```bash
mkdir -p .kiro/skills/<name>
git mv .kiro/steering/<name>.md .kiro/skills/<name>/SKILL.md
```

`git mv` preserva la storia. Non usare `cp` + `rm`, ti perdi la history del file.

Poi **normalizza il frontmatter** del `SKILL.md`. Kiro e Claude Code accettano entrambi solo due campi standard:

```yaml
---
name: <slug-kebab-case>       # stesso della directory
description: <riga singola, forte, con trigger keywords>
---
```

**Rimuovi** i campi Kiro-only: `inclusion`, `fileMatchPattern`, e qualunque altro custom. Il trigger vive nella `description`.

**La description è la cosa più importante.** Deve fare due lavori:

1. Dire cosa fa la skill in una riga (per il picker).
2. Contenere i trigger keywords che faranno auto-attivare la skill quando l'utente ne parla senza nominarla.

Esempi di descrizioni ben fatte (dalla repo marketing-and-sales):

> ✅ *"Trascrivere file audio (chiamate, meeting, note vocali) in testo con Amazon Transcribe. Usare quando l'utente carica un audio in raw-data/ o chiede la trascrizione di una call."*

> ✅ *"Generare preventivi e offerte commerciali Polarity per qualsiasi tipo di progetto. Usare SEMPRE quando l'utente menziona preventivo, offerta commerciale, proposta commerciale o contratto cliente."*

Cattivi esempi:

> ❌ *"Skill per audio."* — troppo vago, non si auto-triggera mai.
> ❌ *"Uses AWS Transcribe API to convert audio."* — descrive l'implementazione, non i trigger.

Se una skill migrata ha una description debole o mancante, riscrivila. Estrai i trigger dal corpo del file (frasi tipo "quando l'utente chiede...", "usa questo quando..."). Se non trovi trigger nel corpo, chiedi all'utente.

### Step 3 — Symlink infrastruttura

```bash
# Skill: entrambi gli IDE leggono la stessa dir fisica
mkdir -p .claude
ln -sfn ../.kiro/skills .claude/skills

# MCP: Claude Code cerca .mcp.json in root, Kiro cerca .kiro/settings/mcp.json.
# Un solo file, due punti di accesso.
[ -f .kiro/settings/mcp.json ] && ln -sfn .kiro/settings/mcp.json .mcp.json
```

**Verifica che i symlink si risolvano** (`ls -la .claude/skills` deve mostrare la freccia). Su alcuni filesystem (network shares, exFAT) i symlink non funzionano: in quel caso avvisa l'utente e proponi copie hard con un git hook di sync (fuori scope di questa skill, ma segnalalo).

### Step 4 — `CLAUDE.md` entrypoint

Crea `CLAUDE.md` in root usando il template in `<skill-dir>/templates/CLAUDE.md.template`. Sostituisci:

- `{{PROJECT_NAME}}` → il nome della repo (leggi dal `name` in `package.json`, `pyproject.toml`, o usa il basename della directory).
- `{{STEERING_IMPORTS}}` → una riga `@.kiro/steering/<file>.md` per ogni steering rimasto **always-on** dopo lo Step 1. Esempio:

```markdown
@.kiro/steering/personal-settings.md

@.kiro/steering/knowledge-manager.md
```

Se non esiste già un `CLAUDE.md`, scrivi il template così com'è. Se esiste, **non sovrascriverlo**: mostra il diff all'utente e chiedi come procedere (merge manuale, o ok a sostituire).

### Step 5 — `.claude/settings.local.json.example`

Copia `<skill-dir>/templates/settings.local.json.example.template` in `.claude/settings.local.json.example`.

Se in `.kiro/settings/mcp.json` (o nel suo `.example`) trovi campi `autoApprove` degli MCP server, **aggiungili** all'`allow` del template come `mcp__<server>__<tool>`. Esempio: `autoApprove: ["read_documentation"]` sul server `aws-docs` diventa `"mcp__aws-docs__read_documentation"` nell'allow.

Questo file **è committato** (è un template). Il vero `.claude/settings.local.json` è per-utente e gitignored.

### Step 6 — `.gitignore`

Aggiungi (se mancano) queste due righe:

```
.mcp.json
.claude/settings.local.json
```

`.mcp.json` è un symlink a `.kiro/settings/mcp.json`, che a sua volta contiene già `AWS_PROFILE` e altri valori per-utente — deve restare gitignored anche indirettamente.

### Step 7 — Ref cleanup

Nella repo Kiro classica, per invocare manualmente uno steering `inclusion: manual` si scrive `#nome-file` nella chat. Dopo la migrazione:

- In **Kiro**, `#nome-skill` non funziona più (le skill non sono steering).
- In **Claude Code**, `#` non è mai stato un trigger valido.

Vanno rimossi dai contenuti. Cerca in tutta la repo (esclusa `.git/`):

```bash
grep -rn --include="*.md" --include="*.py" --include="*.sh" \
     -E '#(transcribe-audio|process-pdf|<altre-skill-migrate>)' .
```

Costruisci il pattern dai nomi delle skill che hai appena migrato. Sostituisci ogni occorrenza con `` `nome-skill` `` (backtick puri). Non toccare heading `#` markdown.

Cerca anche path stale al vecchio steering:

```bash
grep -rn "\.kiro/steering/<name-migrato>\.md" .
```

E riscrivili a `.kiro/skills/<name>/SKILL.md`.

**Mostra ogni file toccato all'utente** prima di committare — a volte un `#foo` è genuino (un heading markdown, un id CSS, un commento Python) e non va sostituito.

### Step 8 — README

Se la repo ha un `README.md`, aggiornalo:

- Menziona esplicitamente **Claude Code** accanto a Kiro nelle sezioni "Quick start" e "Requirements".
- Aggiorna la struttura directory: `.kiro/skills/` esiste ora, `.claude/skills` è il symlink.
- Se c'era una sintassi tipo `/nome-skill` per invocare manualmente, sostituiscila con qualcosa di neutro: *"To force a skill: Kiro `/`, Claude Code `/nome-skill`"*.

Non riscrivere sezioni tecniche del README non pertinenti. Tocca solo quello che riflette la migrazione.

### Step 9 — Commit split suggerito

**Non auto-committare.** Mostra all'utente la seguente struttura di 3 commit (che è quella usata su polarity-marketing-and-sales, che funziona bene per la review):

1. **`feat: compatibilità Claude Code + Kiro senza duplicazione`**
   - Include: `git mv` degli steering a skill, symlink, `CLAUDE.md`, `.claude/settings.local.json.example`, `.gitignore`.
2. **`ref: rimossi #skill refs e path skill obsoleti`**
   - Include: le sostituzioni fatte allo Step 7.
3. **`doc: allineare README alla nuova struttura skill`**
   - Include: aggiornamenti allo Step 8.

Se la repo ha convenzioni di commit diverse, adattati. L'importante è tenere separati **migrazione** e **cleanup ref** — il primo è pesante da rivedere, il secondo è cambio-string massivo e va guardato con occhi diversi.

---

## Validazione finale

Prima di dire "fatto", verifica:

- [ ] `ls -la .claude/skills` → symlink che si risolve a `../.kiro/skills`
- [ ] `ls -la .mcp.json` (se applicabile) → symlink a `.kiro/settings/mcp.json`
- [ ] `.kiro/skills/*/SKILL.md` → ogni frontmatter ha solo `name` + `description`
- [ ] `grep -rn '#<skill-migrata>' .` → nessun hit (heading markdown esclusi)
- [ ] `grep -rn '\.kiro/steering/<skill-migrata>\.md' .` → nessun hit
- [ ] `CLAUDE.md` importa esattamente gli steering rimasti in `.kiro/steering/` after la migrazione
- [ ] `git status` → mostra i file toccati, nulla di sospetto

Poi suggerisci all'utente di fare un test rapido: aprire la repo in Claude Code (`claude` da terminale) e verificare che `/model` funzioni, che le skill migrate compaiano nel picker skill (se ce n'è uno), e che una skill scelta a caso si triggeri.

---

## Non-negoziabili

- **Mai perdere history.** `git mv`, non `mv`. Un `cp + rm` fa apparire la skill come un file nuovo e perdi mesi di storia.
- **Mai auto-committare.** L'utente vuole vedere il diff. Aiutalo a spezzarlo in 3 commit tematici.
- **Mai sovrascrivere `CLAUDE.md` esistente senza chiedere.** Se c'è, quasi sicuramente contiene contesto che l'utente ha scritto a mano.
- **Description forti.** Se una skill migrata resta con una description generica, non si triggererà mai da sola in Claude Code, e l'utente penserà che il porting sia rotto. Riscrivi le description deboli anche se non era strettamente richiesto.
- **Nessuna migrazione "always" a forza.** Se un file steering ha `inclusion: always` (esplicito o implicito), resta steering. Serve a Kiro per riempirsi il context all'avvio; convertirlo a skill lo renderebbe on-demand, cambiando la semantica.

---

## Riferimenti

Il template completo del pattern è visibile su `polarity-marketing-and-sales`, commit range `fecaf55..eaca5ec`:

- `fecaf55` — migrazione principale (21 steering → 21 skill + infrastruttura)
- `dd3c276` — allineamento README/CLAUDE.md/knowledge manager
- `03bf4c5` — rimozione `#skill` refs e path obsoleti
- `eaca5ec` — cleanup residuo (docstring script, README fix)

Se serve un esempio concreto di frontmatter, symlink, o `CLAUDE.md` scritto bene, quella repo è la fonte.
