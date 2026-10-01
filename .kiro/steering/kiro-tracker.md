---
inclusion: auto
name: kiro-tracker-install
description: Runbook per installare il kiro-tracker (dispatcher usage schedulato + widget SwiftBar). Usare quando l'utente chiede di installare, configurare o attivare il kiro tracker, l'invio automatico degli snapshot di consumo, o il widget dei crediti in menu bar.
---

# Kiro Tracker — Runbook di installazione

Questo documento descrive **come installare il kiro-tracker** sulla macchina di
un utente: il widget SwiftBar dei crediti più l'invio automatico e schedulato
degli snapshot di consumo all'API del tracker.

Se l'utente chiede **"installa il kiro tracker"**, "attiva l'invio automatico
del consumo", "configura il widget dei crediti", o simili, segui questo
runbook. Il punto centrale: **recuperi tu autonomamente le credenziali da
1Password e le scrivi nel file di conf gitignorato**, l'utente non le incolla a
mano.

> **Vincolo non negoziabile: il mapping del nome (Step 5) è OBBLIGATORIO.**
> Un'installazione non è completa finché lo userId dell'utente non è mappato a
> un nome. **Non chiudere la procedura, non dire "fatto/installato", e non
> lasciare andare l'utente** finché non hai eseguito il `PUT /mapping` con
> successo e verificato che risulti in `GET /mapping`. Senza mapping l'utente
> compare anonimo nella dashboard: è considerato un'installazione fallita, non
> parziale. Se manca il nome, **chiedilo prima di concludere** (vedi Step 5).

## Cosa viene installato

1. **Widget SwiftBar** (`scripts/swiftbar/kiro-credits.60s.sh`) — mostra il
   consumo Kiro in menu bar, refresh ogni 60s. Installato via
   `scripts/swiftbar/install.sh`.
2. **Dispatcher schedulato** (`scripts/send_usage_scheduled.py`) — invocato in
   background dal widget ad ogni tick (60s), decide da solo se è ora di mandare
   uno snapshot. La giornata è divisa in **tre fasce contigue** delimitate dai
   cardini 07:00, 12:00 e 18:00 — notte (18:00–06:59, scavalca la mezzanotte),
   mattina (07:00–11:59), pomeriggio (12:00–17:59) — e manda **una volta per
   fascia**, al primo tick utile dentro la fascia (quindi parte all'ora in cui
   accendi il Mac, non su orari spaccati). Nessun buco morto: ogni momento del
   giorno cade in una fascia. La fascia **notte** scavalca la mezzanotte ed è
   indicizzata per *band date* (la data in cui è iniziata, alle 18:00): le ore
   00:00–06:59 appartengono alla notte iniziata il giorno prima, così le due
   metà (18:00–23:59 e 00:00–06:59) condividono un unico marker e non si manda
   due volte a cavallo di mezzanotte. Usa un file di stato marker in
   `~/.cache/kiro-tracker/send_state.json` per l'idempotenza (una sola volta per
   fascia per band date). Il file tiene **solo la band date corrente**: ogni
   lettura scarta le altre date, così non cresce mai.
3. **File di conf gitignorato** (`scripts/swiftbar/tracker.conf`) — contiene
   `KIRO_TRACKER_API` (endpoint) e `KIRO_TRACKER_KEY` (API key). **Non è
   tracciato da git** (vedi `.gitignore`). Template versionato:
   `tracker.conf.example`.
4. **Mapping userId→nome** (`PUT /mapping` sull'API) — record separato dallo
   snapshot, è ciò che fa comparire il **nome** dell'utente nella dashboard
   invece del solo userId. Va registrato una volta per utente (vedi Step 5).
   La chiave è lo userId personale IAM Identity Center, non il profile ARN (che
   è della org e uguale per tutti).

## Fonte delle credenziali: 1Password vault Shared

Tutto sta nel vault **Shared** di 1Password (condiviso in azienda), item
**`kiro-tracker API key`**, categoria API Credential. Campi rilevanti:

| Campo 1Password | Riferimento `op://` | Va in `tracker.conf` come |
| --- | --- | --- |
| `credential` | `op://Shared/kiro-tracker API key/credential` | `KIRO_TRACKER_KEY` |
| `endpoint` | `op://Shared/kiro-tracker API key/endpoint` | `KIRO_TRACKER_API` |
| `dashboard` | `op://Shared/kiro-tracker API key/dashboard` | (solo informativo) |

L'endpoint di produzione attuale è
`https://6exa5ymcpg.execute-api.eu-west-1.amazonaws.com/production`, ma **leggilo
sempre dal campo `endpoint` di 1Password** invece di hardcodarlo: se il tracker
viene ri-deployato l'ID API Gateway cambia, e 1Password resta la fonte di verità.

## Procedura

Esegui questi passi in ordine. Un comando alla volta, leggi l'output, prosegui
solo se ha avuto successo. Usa la directory della repo corrente (`kiro-gateway`)
salvo diversa indicazione dell'utente.

### Step 0 — Precondizioni

- **1Password CLI (`op`) installata e con sessione attiva.** Verifica:

  ```bash
  op whoami
  ```

  Se `op` non è installata, dillo all'utente e fermati (installazione via
  `brew install 1password-cli`). Se la sessione è scaduta, l'utente deve fare
  `eval $(op signin)` o sbloccare l'app 1Password con l'integrazione CLI attiva.
  Non tentare di aggirare l'autenticazione.

  **Account multipli.** Il vault `Shared` sta sull'account 1Password aziendale.
  Se l'utente ha più account (`op account list` ne mostra più di uno), tutti i
  comandi `op read`/`op whoami` di questo runbook vanno passati con
  `--account <shard>.1password.com` (es. `sfsrl.1password.com`), altrimenti `op`
  può leggere dal vault sbagliato o fallire. Individua l'account giusto con
  `op account list` (quello aziendale, non `my.1password.com`).

### Step 1 — Recuperare le credenziali dal vault Shared

Leggi endpoint e credential. **Non stampare mai il valore della key in chat né
nei log** — la scrivi solo dentro il file gitignorato.

```bash
op read "op://Shared/kiro-tracker API key/endpoint"
op read "op://Shared/kiro-tracker API key/credential"
```

Se `op read` fallisce sul campo `endpoint` (item creato prima che il campo
esistesse), usa l'endpoint di produzione noto come fallback e segnalalo
all'utente, così può aggiungere il campo su 1Password.

### Step 2 — Scrivere il file di conf gitignorato

Scrivi `scripts/swiftbar/tracker.conf` con i valori recuperati. Preferisci
comporre il file **senza far transitare la key in chat**: usa `op read`
direttamente in uno heredoc, così il segreto passa da 1Password al file senza
comparire nel tuo output.

```bash
CONF="scripts/swiftbar/tracker.conf"
API=$(op read "op://Shared/kiro-tracker API key/endpoint" 2>/dev/null \
      || echo "https://6exa5ymcpg.execute-api.eu-west-1.amazonaws.com/production")
KEY=$(op read "op://Shared/kiro-tracker API key/credential")
umask 077
cat > "$CONF" <<EOF
# kiro-tracker config — generato dal runbook di installazione.
# Gitignorato: NON committare. Rigenera con: installa il kiro tracker.
KIRO_TRACKER_API=$API
KIRO_TRACKER_KEY=$KEY
EOF
chmod 600 "$CONF"
echo "Scritto $CONF (permessi $(stat -f '%Lp' "$CONF"))"
```

Note:
- `umask 077` + `chmod 600`: il file contiene un segreto, deve essere leggibile
  solo dall'utente.
- Verifica che `scripts/swiftbar/tracker.conf` sia in `.gitignore` (lo è). Non
  committarlo mai. Non incollare mai il contenuto della key in chat, nei commit,
  o in file tracciati.

### Step 3 — Verificare la lettura della conf e la logica di schedulazione

Controlla che il dispatcher legga la conf e capisca lo stato, senza mandare
nulla:

```bash
.venv/bin/python scripts/send_usage_scheduled.py --status
.venv/bin/python scripts/send_usage_scheduled.py --dry-run
```

`--status` mostra ora corrente, slot, e cosa è già stato mandato oggi.
`--dry-run` dice se manderebbe adesso, senza mandare né scrivere lo stato.

### Step 4 — Test di invio reale (opzionale ma consigliato)

Per confermare end-to-end che credenziali e auth Kiro funzionano, forza un
invio una tantum (non consuma uno slot schedulato):

```bash
.venv/bin/python scripts/send_usage_scheduled.py --force
```

Deve stampare `Sent snapshot for slot 'forced'.`. Se fallisce, l'errore su
stderr indica la causa (auth Kiro, endpoint, o key sbagliata).

### Step 5 — Registrare il nome dell'utente nella dashboard (mapping userId→nome) — OBBLIGATORIO

**Questo step è obbligatorio: l'installazione NON è completa senza.** Non
proseguire allo Step 6, non dichiarare l'installazione conclusa e non congedare
l'utente finché il `PUT /mapping` non è andato a buon fine e verificato.

**Questo è ciò che fa comparire il nome dell'utente nella dashboard.** Lo
snapshot di consumo contiene solo lo `userId`; la dashboard aggancia il nome da
un record di mapping separato. Senza questo step l'utente compare nella
dashboard in forma anonima (solo userId), non con il suo nome.

Sull'identità: la chiave utente è lo **userId personale IAM Identity Center**
(es. `d-93674552ed.e2f534a4-4081-700e-8184-086e15cb58f7`), NON il profile ARN.
Il profile ARN è quello della subscription org ed è identico per tutti gli
utenti, quindi non distingue le persone; lo userId sì. Il client lo estrae da
solo dalla risposta `GetUsageLimits` e lo manda nel campo `userId`.

**Sei tu (l'agente) a fare il PUT del mapping**, non l'utente a mano. Il flusso:

1. **Ricava lo userId dell'utente** — lo stampa il test dello Step 4
   (`... for d-<identitystore>.<uuid>`), oppure leggilo dalla risposta
   `GetUsageLimits`.
2. **Chiedi all'utente con che nome vuole comparire** se non lo conosci già.
   Non inventare il nome e non dedurlo dallo userId o dall'email: chiedi
   esplicitamente "con che nome vuoi comparire nella dashboard?".
3. **Gestisci l'omonimia.** Prima del PUT, leggi i mapping esistenti
   (`GET /mapping`, vedi sotto). Se il nome scelto è già usato da un altro
   userId, **fermati e chiedi all'utente un nome distintivo** (es. "Giovanni B.",
   "Giovanni — Marketing"), così due persone omonime restano distinguibili nella
   dashboard. Non sovrascrivere silenziosamente né duplicare un nome già preso.
4. **Fai il PUT** con lo userId e il nome concordato.

```bash
ACCT=<shard>.1password.com   # es. sfsrl.1password.com
API=$(op read "op://Shared/kiro-tracker API key/endpoint" --account "$ACCT")
KEY=$(op read "op://Shared/kiro-tracker API key/credential" --account "$ACCT")

# 3. controlla i nomi già usati (per l'omonimia)
curl -s "$API/mapping" -H "x-api-key: $KEY" | python3 -m json.tool

# 4. registra il mapping con il nome concordato con l'utente
curl -s -X PUT "$API/mapping" \
  -H "x-api-key: $KEY" -H "content-type: application/json" \
  -d '{"userId":"<userId-utente>","displayName":"<Nome concordato>"}'
```

Poi verifica che il nuovo mapping risulti nell'elenco:

```bash
curl -s "$API/mapping" -H "x-api-key: $KEY" | python3 -m json.tool
```

Nota per "vedere anche gli altri": la dashboard mostra la riga di consumo di
**ogni** userId che manda snapshot, ma con il **nome** solo per gli userId
mappati qui. Ogni nuovo collega va mappato una volta (il PUT è idempotente per
userId: ri-eseguirlo aggiorna il nome di quello stesso userId).

### Step 6 — Installare il widget SwiftBar

```bash
./scripts/swiftbar/install.sh
```

L'installer trova da solo la cartella plugin di SwiftBar (che può NON essere
quella di default: es. `~/Documents/Swiftbar plugins`) leggendola dalle
preferenze, e crea il symlink allo script nella repo. Il widget da lì in poi
mostra i crediti e, ad ogni tick di 60s, lancia il dispatcher in background:
l'invio schedulato parte da solo nelle tre fasce (mattina/pomeriggio/sera) senza
altra configurazione.

Se SwiftBar era **già in esecuzione** quando installi il plugin (comune se è ai
login items), forzalo a ricaricare così l'icona compare subito senza aspettare:

```bash
open "swiftbar://refreshallplugins"
```

## Regole operative per l'agente

- **Recupera le credenziali da 1Password autonomamente** quando l'utente chiede
  di installare il tracker: non chiedergli di incollare endpoint o key, li leggi
  tu da `op://Shared/kiro-tracker API key/...`.
- **Non stampare mai valori di segreti** (la `credential`) in chat, nei log, o
  in output di comandi. Falli transitare da `op` al file gitignorato
  direttamente.
- **Non committare mai** `scripts/swiftbar/tracker.conf` né valori di key.
- **Non hardcodare l'endpoint** nel conf partendo da memoria: leggilo dal campo
  `endpoint` di 1Password; usa il valore noto solo come fallback esplicito e
  segnalato.
- Se `op` non è disponibile o non autenticata, fermati e spiega all'utente come
  sistemarla; non inventare le credenziali.
- **Con account 1Password multipli passa sempre `--account <shard>.1password.com`**
  ai comandi `op` (il vault `Shared` è sull'account aziendale). Individua
  l'account giusto con `op account list`.
- **Il mapping del nome (Step 5) è un gate di chiusura obbligatorio.** Non
  considerare l'installazione completa, non dire "fatto"/"installato" e non
  congedare l'utente finché non hai eseguito il `PUT /mapping` con successo e
  verificato che il nome risulti in `GET /mapping`. Un'installazione senza
  mapping è fallita, non parziale. Se non conosci il nome, chiedilo e attendi la
  risposta prima di concludere: non saltare questo passo per "finire prima".
- **Il PUT del mapping lo fai tu, non l'utente.** Se non conosci il nome,
  **chiedilo esplicitamente** ("con che nome vuoi comparire nella dashboard?");
  non inventarlo né ricavarlo da userId/email. Prima del PUT controlla i mapping
  esistenti (`GET /mapping`): in caso di **omonimia** con un nome già assegnato
  a un altro userId, fermati e chiedi all'utente un nome distintivo, non
  sovrascrivere né duplicare.
- **La chiave utente è lo userId personale IAM Identity Center, non il profile
  ARN.** L'ARN è della subscription org (uguale per tutti), lo userId distingue
  le persone. Il client lo estrae da `GetUsageLimits`; il mapping usa `userId`.

## Riferimenti

- Endpoint/API key/dashboard: 1Password vault **Shared**, item
  `kiro-tracker API key`.
- Repo del tracker (deploy, API, dashboard): `kiro-tracker` (`README.md`).
- Account AWS del tracker: vedi `kiro-tracker/.kiro/steering/aws-accounts.md`
  (account `polarity-kiro`, `--profile polarity-kiro --region eu-west-1`).
