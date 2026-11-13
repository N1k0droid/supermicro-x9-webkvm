# supermicro-x9-webkvm

[English](README.md) · **Italiano**

Un piccolo container Docker che dà alle vecchie schede **Supermicro X9** (BMC con firmware
ATEN) una console remota e un monitoraggio moderni: **KVM nel browser senza Java**,
statistiche dei sensori del BMC, controllo dell'alimentazione, metriche Prometheus e un
template Zabbix.

Provato su una **X9SRL-F con firmware BMC 3.19**. Dovrebbe funzionare anche su altre schede X9
con la stessa famiglia di firmware ATEN.

## Perché

Sulle schede X9 la console remota del BMC ("iKVM") esiste solo come **applet Java / file
JNLP**. I browser moderni non eseguono più Java, le versioni recenti di Java rifiutano la
vecchia applet e i suoi certificati, e tenere un Java vecchio solo per vedere il BIOS è
scomodo e poco sicuro.

Questo container parla direttamente il protocollo KVM del BMC (reimplementato in Python) e
mostra la console in qualsiasi browser tramite noVNC. Visto che dialoga già con il BMC, raccoglie
anche i sensori IPMI (temperature, ventole, tensioni, registro eventi) e li espone a una
dashboard, a Prometheus e a Zabbix.

## Funzioni

- **Console KVM nel browser** (noVNC), senza Java e senza file JNLP: BIOS, POST, boot loader,
  installer, console di emergenza.
- **Porta VNC nativa** (opzionale, protetta da password) per TigerVNC e altri viewer.
- **Statistiche IPMI** in Python puro: dashboard live con le soglie del BMC, grafici dello
  storico (SQLite), registro eventi (SEL), informazioni su scheda e firmware.
- **Controllo dell'alimentazione**: accensione, spegnimento ACPI soft, reset, power cycle,
  spegnimento forzato.
- **Prometheus** `/metrics` e un **template Zabbix 7.4 / 8**.
- Interfaccia web in **italiano e inglese** (segue la lingua del browser, si cambia con il
  pulsante EN/IT).

```
 browser ──HTTP/WS──► webkvm :8080 ──┬─ login web del BMC (:80) → token KVM
 TigerVNC ──VNC────► webkvm :5900    ├─ KVM ATEN (:5900) → framebuffer condiviso
 Prometheus/Zabbix ─► /metrics, /api ├─ IPMI (UDP 623) → storico SQLite
                                     └─ comandi di alimentazione via IPMI
```

### Limiti (del BMC, non di webkvm)

- Il BMC cattura al massimo **1280x1024**. BIOS, POST e schermate di avvio sono molto sotto
  questo limite; un desktop a risoluzione più alta non si vede per intero. Per un desktop a
  1080p usa un accesso remoto dal sistema operativo (RDP, server VNC, ...).
- Il BMC vede solo la **VGA integrata**. Se il server si avvia su una scheda video aggiuntiva,
  imposta nel BIOS *VGA Priority / Primary Display* su **Onboard**.
- Mouse assoluto su un solo schermo; niente virtual media.

## Avvio rapido (Docker)

Serve un host con Docker e Docker Compose (un server Linux, un NAS, un mini PC...) che
raggiunga il BMC su **TCP 80**, **TCP 5900** e **UDP 623**.

**1. Scarica i file**

```bash
git clone https://github.com/N1k0droid/supermicro-x9-webkvm.git
cd supermicro-x9-webkvm
```

(oppure scarica lo ZIP da GitHub e scompattalo)

**2. Costruisci l'immagine**

```bash
docker build -t supermicro-x9-webkvm .
```

**3. Crea l'hash della password del gateway e il token per le metriche** (vedi
[Password, token e .env](#password-token-e-env))

```bash
docker run --rm -it supermicro-x9-webkvm python -m webkvm hash-password
docker run --rm supermicro-x9-webkvm python -m webkvm gen-token
```

**4. Crea il file `.env`** partendo dall'esempio e compilalo

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

**5. Avvialo**

```bash
docker compose up -d
docker compose logs -f
```

**6. Apri `http://<host>:8080`**, entra con `WEBKVM_USER` e la tua password e premi
**Apri console KVM**.

**7. Se qualcosa non va**, lancia la diagnostica (vedi
[Risoluzione dei problemi](#risoluzione-dei-problemi)):

```bash
docker compose exec webkvm python -m webkvm check
```

## Password, token e .env

Tutte le impostazioni stanno in un file `.env` accanto a `docker-compose.yml`. Parti da
[.env.example](.env.example), che descrive ogni variabile (i commenti sono in inglese). Il file
contiene segreti: tienilo privato (`chmod 600 .env`) e non pubblicarlo mai.

| Variabile | Cos'è | Come ottenerla |
|---|---|---|
| `BMC_HOST` | IP o nome del BMC | la tua rete |
| `BMC_USER`, `BMC_PASSWORD` | un account del BMC con diritti di amministratore | interfaccia web del BMC |
| `WEBKVM_USER` | utente per la pagina di login di webkvm | a scelta (default `admin`) |
| `WEBKVM_PASSWORD_HASH` | hash della password di login di webkvm | `python -m webkvm hash-password` |
| `WEBKVM_METRICS_TOKEN` | token per Prometheus / Zabbix | `python -m webkvm gen-token` |
| `VNC_PASSWORD` | password per i viewer VNC nativi, **max 8 caratteri** | a scelta; vuota = VNC nativo spento |
| `WEBKVM_HTTP_PORT` | porta della dashboard sull'host | default `8080` |

**Hash della password del gateway.** La password di login di webkvm non viene mai salvata in
chiaro: si salva il suo hash scrypt. Il comando chiede la password due volte e stampa una riga
come `scrypt$16384$8$1$...`:

```bash
docker run --rm -it supermicro-x9-webkvm python -m webkvm hash-password
```

Mettila nel `.env` **tra apici singoli**, perché contiene caratteri `$` che Docker Compose
altrimenti proverebbe a interpretare:

```
WEBKVM_PASSWORD_HASH='scrypt$16384$8$1$AbC...$XyZ...'
```

**Token per le metriche.** Una stringa casuale che Prometheus e Zabbix inviano come
`Authorization: Bearer <token>`. Dà accesso in sola lettura a `/metrics` e `/api/v1/sensors`,
mai alla console o ai comandi di alimentazione.

```bash
docker run --rm supermicro-x9-webkvm python -m webkvm gen-token
```

Senza Docker gli stessi comandi funzionano con Python 3.10+ dopo
`pip install -r requirements.txt`: `python -m webkvm hash-password`, `python -m webkvm gen-token`.

**Su Windows** c'è uno script che chiede tutto (password nascoste) e scrive il `.env` al posto
tuo; serve un ambiente virtuale Python con i requisiti installati:

```powershell
py -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File .\dev\make-env.ps1 -Out .\.env
```

## Piattaforme

### Linux con Docker Compose

Segui l'avvio rapido. Per aggiornare: `git pull`, poi `docker compose up -d --build`.

Se il BMC è su una VLAN di management raggiungibile solo dall'host, attiva
`network_mode: host` in `docker-compose.yml` (le righe `ports:` vengono allora ignorate e il
container usa direttamente le porte 8080 e 5900 dell'host).

### Synology (Container Manager)

1. Copia i file del repository in una cartella condivisa del NAS, es.
   `/volume1/docker/supermicro-x9-webkvm` (controlla che vengano copiati anche i file nascosti
   come `.dockerignore`).
2. Crea il `.env` nella stessa cartella, accanto a `docker-compose.yml` (vedi
   [Password, token e .env](#password-token-e-env); su Windows `dev\make-env.ps1` può
   scriverlo direttamente nella condivisione del NAS). Senza `.env` il progetto fallisce con
   "Failed to load .env".
3. Container Manager → **Progetto → Crea** → scegli la cartella → usa il
   `docker-compose.yml` esistente → compila. La prima build richiede qualche minuto.
4. Se sul NAS la porta 8080 è già occupata, imposta `WEBKVM_HTTP_PORT` nel `.env`
   (es. `8188`).

**Aggiornare**: copia i file nuovi sopra i vecchi (lascia il `.env`). Container Manager tende a
riusare l'immagine vecchia: se **Azione → Compila** non basta, elimina il progetto e ricrealo
dalla stessa cartella (lo storico resta, se non scegli di eliminare anche i volumi). La
versione in esecuzione si legge in `http://<nas>:<porta>/healthz`.

### Windows (sviluppo)

`dev\run-local.ps1` avvia il gateway su `127.0.0.1:8080` senza Docker, per provarlo contro un
BMC da un PC Windows. `dev\export.ps1` copia in `dist\` solo i file pubblicabili (senza
segreti).

## Console KVM

- Nel browser: pagina `/kvm`, con Ctrl+Alt+Canc, combinazioni utili (F2/Canc per il BIOS,
  F11/F12 per il menu di avvio, Ctrl+Alt+Fn), adatta alla finestra e schermo intero.
- Con un viewer nativo: `vncviewer <host>:5900`, password `VNC_PASSWORD` (contano solo i primi
  8 caratteri, limite del protocollo VNC).
- Più viewer possono essere collegati insieme: vedono lo stesso schermo e condividono l'input.
- La sessione sul BMC si apre al primo viewer e si chiude `KVM_IDLE_TIMEOUT` secondi dopo
  l'ultimo. Ogni connessione rifà il login sul BMC, quindi la scadenza del token non è mai un
  problema.
- **Tastiera**: noVNC e TigerVNC mandano il tasto fisico (QEMU Extended Key Event), quindi
  funziona qualsiasi layout, purché il sistema del server usi lo stesso layout della tua
  tastiera.
- La barra di stato mostra lo stato reale della sessione verso il BMC: risoluzione, "nessun
  segnale video" mentre il server si riavvia, oppure "BMC non raggiungibile" se la sessione
  cade. **Riconnetti** chiede anche al BMC un frame completo nuovo.

## Statistiche e API

- Dashboard: sensori raggruppati per tipo e colorati secondo le soglie del BMC, grafico dello
  storico (1 h / 6 h / 24 h / 7 g con media e banda min/max), registro eventi SEL, info sulla
  scheda.
- API (con il cookie di login, oppure con `Authorization: Bearer $WEBKVM_METRICS_TOKEN` per
  `/api/v1/sensors` e `/metrics`):
  - `GET /api/v1/sensors`: stato attuale (sensori con soglie, chassis, numero di voci SEL);
  - `GET /api/v1/history?sensor=CPU%20Temp&range=86400`: storico ridotto a circa 300 punti;
  - `GET /api/v1/sel`, `GET /api/v1/info`;
  - `GET /healthz`: stato e versione (senza login).

### Prometheus

```yaml
scrape_configs:
  - job_name: webkvm
    scrape_interval: 30s
    authorization:
      credentials: <WEBKVM_METRICS_TOKEN>
    static_configs:
      - targets: ['host:8080']
```

Metriche: `bmc_up`, `bmc_sensor_value{sensor,unit,type}`, `bmc_sensor_status`, `bmc_sensor_ok`,
`bmc_sensor_threshold{sensor,kind}`, `bmc_chassis_power_on`, `bmc_chassis_intrusion`,
`bmc_chassis_fault{fault}`, `bmc_sel_entries`, `bmc_info`, `webkvm_kvm_connected`,
`webkvm_kvm_viewers`.

### Zabbix 7.4 / 8

Provato su Zabbix 7.4 e 8 con una X9SRL-F reale. I nomi di item e trigger del template sono in
inglese.

1. Importa [zabbix/template_webkvm_supermicro.yaml](zabbix/template_webkvm_supermicro.yaml)
   (*Data collection → Templates → Import*).
2. Crea un host (non serve nessuna interfaccia) e assegnagli il template
   **Supermicro BMC by webkvm HTTP**.
3. Imposta le macro dell'host:
   - `{$WEBKVM.URL}`, es. `http://192.168.1.10:8080`, senza barra finale;
   - `{$WEBKVM.TOKEN}`, di tipo *Secret text*, con il valore di `WEBKVM_METRICS_TOKEN`.
4. Per non aspettare il primo giro: *Execute now* sull'item `webkvm: sensor readings (JSON)` e
   sulla regola di discovery "Sensor discovery".

Il server (o il proxy) Zabbix deve raggiungere il container sulla sua porta HTTP.

- Un solo item HTTP agent legge `/api/v1/sensors`; tutto il resto sono item dipendenti, quindi
  Zabbix non deve parlare IPMI con il BMC.
- La low-level discovery crea un item per il valore e uno per lo stato di ogni sensore
  numerico. I trigger usano le soglie del BMC (avviso / critico) più una soglia propria per le
  temperature, `{$WEBKVM.TEMP.WARN}`, ridefinibile per sensore:
  `{$WEBKVM.TEMP.WARN:"CPU Temp"}`.
- Altri trigger: BMC non raggiungibile, nessun dato da webkvm, server spento, nuove voci SEL,
  intrusione chassis, guasti chassis.

Zabbix include anche un template ufficiale **Supermicro Aten by SNMP**, che interroga il BMC via
SNMP (va abilitato sul BMC). I due sono indipendenti; questo aggiunge SEL, stato
dell'alimentazione e soglie native del BMC senza configurare SNMP.

## Risoluzione dei problemi

La diagnostica prova ogni pezzo contro il BMC e non stampa mai password, token, cookie o
immagini dello schermo, quindi l'output si può condividere:

```bash
docker compose exec webkvm python -m webkvm check            # tutto
docker compose exec webkvm python -m webkvm check ipmi       # solo statistiche
docker compose exec webkvm python -m webkvm check kvm        # solo login web + console KVM
docker compose exec webkvm python -m webkvm check kvm-watch 60   # + osserva gli aggiornamenti per 60 s
```

- **Il progetto non parte, "Failed to load .env"**: il `.env` manca o non è accanto a
  `docker-compose.yml`.
- **KVM: "connection refused" sulla porta 5900**: il servizio KVM del BMC a volte si blocca dopo
  molte sessioni in poco tempo. Riavvia il BMC (interfaccia web → *Maintenance → Unit Reset*);
  il server resta acceso.
- **"Nessun segnale video"**: il server si sta riavviando, lo schermo è spento, oppure usa una
  scheda video aggiuntiva (vedi [Limiti](#limiti-del-bmc-non-di-webkvm)).
- **Dopo un aggiornamento la pagina sembra vecchia**: l'immagine non è stata ricostruita (vedi
  Synology sopra); `/healthz` mostra la versione in esecuzione.
- **Errori IPMI**: controlla che sul BMC sia attivo *IPMI over LAN* e che l'utente abbia diritti
  di amministratore.

## Sicurezza

- L'interfaccia web e la porta KVM degli X9 non sono cifrate: le credenziali del BMC viaggiano in
  chiaro sulla LAN di management, esattamente come con il client Java originale. Tieni il BMC su
  una rete fidata.
- Il gateway ha un proprio login (hash scrypt, limite ai tentativi falliti), cookie `HttpOnly`
  `SameSite=Strict`, token CSRF per i comandi e controllo dell'Origin sul WebSocket.
- Per raggiungerlo da fuori casa mettilo dietro un reverse proxy HTTPS (o una VPN) e imposta
  `WEBKVM_TRUSTED_PROXY=true`.
- Comandi di alimentazione, login e sessioni della console finiscono nel log `webkvm.audit`.
- Le password non compaiono mai nei log.

## Sviluppo

```bash
pip install -r requirements.txt               # aiohttp, pyghmi
python -m unittest discover -s tests          # test unitari ed end-to-end (BMC simulato)
BMC_HOST=... BMC_PASSWORD=... WEBKVM_PASSWORD=... DATA_DIR=./data NOVNC_DIR=./novnc python -m webkvm
```

Per la pagina KVM serve noVNC (le sue cartelle `core/` e `vendor/`) in `NOVNC_DIR` o in
`webkvm/web/static/novnc`; l'immagine Docker lo scarica durante la build. Il protocollo è
descritto (in inglese) in [docs/PROTOCOL.md](docs/PROTOCOL.md).

## Crediti e licenze

- Questo progetto: [licenza MIT](LICENSE).
- [noVNC](https://github.com/novnc/noVNC) (MPL-2.0), scaricato durante la build, non incluso in
  questo repository.
- [pyghmi](https://opendev.org/x/pyghmi) (Apache-2.0) e
  [aiohttp](https://github.com/aio-libs/aiohttp) (Apache-2.0), installati da PyPI.

Progetto non affiliato con Supermicro o ATEN. Usa i comandi di alimentazione a tuo rischio.
