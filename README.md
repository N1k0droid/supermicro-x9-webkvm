# supermicro-x9-webkvm

**English** · [Italiano](README.it.md)

A small Docker container that gives old **Supermicro X9** boards (ATEN BMC firmware) a modern
remote console and monitoring: **KVM in the browser without Java**, BMC sensor statistics,
power control, Prometheus metrics and a Zabbix template.

Tested on an **X9SRL-F with BMC firmware 3.19**. Other X9 boards with the same ATEN firmware
family should work too.

## Why

On X9 boards the BMC remote console ("iKVM") only exists as a **Java applet / JNLP file**.
Modern browsers no longer run Java, recent Java versions refuse the old applet and its
certificates, and keeping an old Java around just to see the BIOS is painful and unsafe.

This container speaks the BMC's KVM protocol directly (re-implemented in Python) and shows the
console in any browser through noVNC. Since it already talks to the BMC, it also collects the
IPMI sensors (temperatures, fans, voltages, event log) and exposes them to a dashboard,
Prometheus and Zabbix.

## Features

- **KVM console in the browser** (noVNC), no Java, no JNLP files: BIOS, POST, boot loaders,
  OS installers, emergency consoles.
- **Native VNC port** (optional, password protected) for TigerVNC and other viewers.
- **IPMI statistics** in pure Python: live dashboard with BMC thresholds, history charts
  (SQLite), event log (SEL), board and firmware info.
- **Power control**: power on, ACPI soft shutdown, reset, power cycle, forced off.
- **Prometheus** `/metrics` and a **Zabbix 7.4 / 8 template**.
- **English / Italian** web interface (follows the browser, switchable with the EN/IT button).

```
 browser ──HTTP/WS──► webkvm :8080 ──┬─ BMC web login (:80) → KVM token
 TigerVNC ──VNC────► webkvm :5900    ├─ ATEN KVM (:5900) → shared framebuffer
 Prometheus/Zabbix ─► /metrics, /api ├─ IPMI (UDP 623) → SQLite history
                                     └─ power commands over IPMI
```

### Limits (of the BMC, not of webkvm)

- The BMC captures at most **1280x1024**. BIOS, POST and boot screens are well below that;
  a desktop at a higher resolution is not fully visible. For a 1080p desktop use a remote
  desktop tool inside the OS (RDP, VNC server, ...).
- The BMC only sees the **onboard VGA**. If the server boots on an add-in graphics card, set
  the BIOS option *VGA Priority / Primary Display* to **Onboard**.
- Absolute mouse on a single screen; no virtual media.

## Quick start (Docker)

You need a host with Docker and Docker Compose (a Linux server, a NAS, a mini PC...) that can
reach the BMC on **TCP 80**, **TCP 5900** and **UDP 623**.

**1. Get the files**

```bash
git clone https://github.com/N1k0droid/supermicro-x9-webkvm.git
cd supermicro-x9-webkvm
```

(or download the ZIP from GitHub and unpack it)

**2. Build the image**

```bash
docker build -t supermicro-x9-webkvm .
```

**3. Create the gateway password hash and the metrics token** (see
[Passwords, token and .env](#passwords-token-and-env))

```bash
docker run --rm -it supermicro-x9-webkvm python -m webkvm hash-password
docker run --rm supermicro-x9-webkvm python -m webkvm gen-token
```

**4. Create `.env`** from the example and fill it in

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

**5. Start it**

```bash
docker compose up -d
docker compose logs -f
```

**6. Open `http://<host>:8080`**, sign in with `WEBKVM_USER` and your password, and press
**Open KVM console**.

**7. If something does not work**, run the diagnostics (see [Troubleshooting](#troubleshooting)):

```bash
docker compose exec webkvm python -m webkvm check
```

## Passwords, token and .env

All settings live in a `.env` file next to `docker-compose.yml`. Start from
[.env.example](.env.example), which documents every variable. The file contains secrets:
keep it private (`chmod 600 .env`) and never commit it.

| Variable | What it is | How to get it |
|---|---|---|
| `BMC_HOST` | IP or name of the BMC | your network |
| `BMC_USER`, `BMC_PASSWORD` | a BMC account with administrator rights | the BMC web interface |
| `WEBKVM_USER` | user name for the webkvm login page | your choice (default `admin`) |
| `WEBKVM_PASSWORD_HASH` | hash of the webkvm login password | `python -m webkvm hash-password` |
| `WEBKVM_METRICS_TOKEN` | token for Prometheus / Zabbix | `python -m webkvm gen-token` |
| `VNC_PASSWORD` | password for native VNC viewers, **max 8 characters** | your choice; empty = native VNC off |
| `WEBKVM_HTTP_PORT` | dashboard port on the host | default `8080` |

**Gateway password hash.** The webkvm login password is never stored in clear: you store its
scrypt hash. The command asks for the password twice and prints a line like
`scrypt$16384$8$1$...`:

```bash
docker run --rm -it supermicro-x9-webkvm python -m webkvm hash-password
```

Put it in `.env` **between single quotes**, because it contains `$` characters that Docker
Compose would otherwise try to expand:

```
WEBKVM_PASSWORD_HASH='scrypt$16384$8$1$AbC...$XyZ...'
```

**Metrics token.** A random string that Prometheus and Zabbix send as
`Authorization: Bearer <token>`. It only gives read access to `/metrics` and
`/api/v1/sensors`, never to the console or power commands.

```bash
docker run --rm supermicro-x9-webkvm python -m webkvm gen-token
```

Without Docker, the same commands work with Python 3.10+ after
`pip install -r requirements.txt`: `python -m webkvm hash-password`, `python -m webkvm gen-token`.

**On Windows** there is a helper that asks everything (passwords hidden) and writes `.env`
for you; it needs a Python virtual environment with the requirements installed:

```powershell
py -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File .\dev\make-env.ps1 -Out .\.env
```

## Platforms

### Linux with Docker Compose

Follow the quick start. To update: `git pull`, then `docker compose up -d --build`.

If the BMC sits on a management VLAN that only the host can reach, enable
`network_mode: host` in `docker-compose.yml` (the `ports:` lines are then ignored, and the
container uses ports 8080 and 5900 of the host directly).

### Synology (Container Manager)

1. Copy the repository files into a shared folder on the NAS, e.g.
   `/volume1/docker/supermicro-x9-webkvm` (make sure hidden files such as `.dockerignore`
   are copied too).
2. Create `.env` in the same folder, next to `docker-compose.yml` (see
   [Passwords, token and .env](#passwords-token-and-env); on Windows `dev\make-env.ps1` can
   write it directly to the NAS share). Without `.env` the project fails with
   "Failed to load .env".
3. Container Manager → **Project → Create** → choose the folder → use the existing
   `docker-compose.yml` → build. The first build takes a few minutes.
4. If port 8080 is already used on the NAS, set `WEBKVM_HTTP_PORT` in `.env` (e.g. `8188`).

**Updating**: copy the new files over the old ones (keep `.env`). Container Manager tends to
reuse the old image: if **Action → Build** is not enough, delete the project and create it
again from the same folder (the history is kept unless you also delete the volumes). The
running version is shown at `http://<nas>:<port>/healthz`.

### Windows (development)

`dev\run-local.ps1` starts the gateway on `127.0.0.1:8080` without Docker, for testing
against a BMC from a Windows PC. `dev\export.ps1` copies only the publishable files into
`dist\` (no secrets).

## KVM console

- In the browser: page `/kvm`, with Ctrl+Alt+Del, useful key combinations (F2/Del for the
  BIOS, F11/F12 for the boot menu, Ctrl+Alt+Fn), fit-to-window and full screen.
- With a native viewer: `vncviewer <host>:5900`, password `VNC_PASSWORD` (only the first 8
  characters count, a VNC protocol limit).
- Several viewers can be connected at once: they see the same screen and share the input.
- The BMC session opens with the first viewer and closes `KVM_IDLE_TIMEOUT` seconds after the
  last one. Every connection signs in to the BMC again, so token expiry is never a problem.
- **Keyboard**: noVNC and TigerVNC send the physical key (QEMU Extended Key Event), so any
  keyboard layout works as long as the server's OS uses the same layout as your keyboard.
- The status bar shows the real state of the BMC session: resolution, "no video signal"
  while the server reboots, or "BMC unreachable" if the session drops. **Reconnect** also
  asks the BMC for a fresh full frame.

## Statistics and API

- Dashboard: sensors grouped by type and coloured with the BMC thresholds, history chart
  (1 h / 6 h / 24 h / 7 d with average and min/max band), SEL event log, board info.
- API (with the login cookie, or with `Authorization: Bearer $WEBKVM_METRICS_TOKEN` for
  `/api/v1/sensors` and `/metrics`):
  - `GET /api/v1/sensors`: current state (sensors with thresholds, chassis, SEL count);
  - `GET /api/v1/history?sensor=CPU%20Temp&range=86400`: history reduced to about 300 points;
  - `GET /api/v1/sel`, `GET /api/v1/info`;
  - `GET /healthz`: liveness and version (no login).

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

Metrics: `bmc_up`, `bmc_sensor_value{sensor,unit,type}`, `bmc_sensor_status`, `bmc_sensor_ok`,
`bmc_sensor_threshold{sensor,kind}`, `bmc_chassis_power_on`, `bmc_chassis_intrusion`,
`bmc_chassis_fault{fault}`, `bmc_sel_entries`, `bmc_info`, `webkvm_kvm_connected`,
`webkvm_kvm_viewers`.

### Zabbix 7.4 / 8

Tested on Zabbix 7.4 and 8 with a real X9SRL-F.

1. Import [zabbix/template_webkvm_supermicro.yaml](zabbix/template_webkvm_supermicro.yaml)
   (*Data collection → Templates → Import*).
2. Create a host (no interface needed) and link the template
   **Supermicro BMC by webkvm HTTP**.
3. Set the host macros:
   - `{$WEBKVM.URL}`, e.g. `http://192.168.1.10:8080`, without a trailing slash;
   - `{$WEBKVM.TOKEN}`, type *Secret text*, the value of `WEBKVM_METRICS_TOKEN`.
4. To skip the first wait: *Execute now* on the item `webkvm: sensor readings (JSON)` and on
   the discovery rule "Sensor discovery".

The Zabbix server (or proxy) must reach the container on its HTTP port.

- One HTTP agent item reads `/api/v1/sensors`; everything else is dependent items, so Zabbix
  does not need to speak IPMI with the BMC.
- Low-level discovery creates a value item and a status item for every numeric sensor.
  Triggers use the BMC thresholds (warning / critical) plus an extra temperature threshold
  `{$WEBKVM.TEMP.WARN}`, which can be overridden per sensor:
  `{$WEBKVM.TEMP.WARN:"CPU Temp"}`.
- Other triggers: BMC unreachable, no data from webkvm, server powered off, new SEL entries,
  chassis intrusion, chassis faults.

Zabbix also ships an official **Supermicro Aten by SNMP** template that polls the BMC over
SNMP (SNMP must be enabled on the BMC). The two are independent; this one adds the SEL, the
power state and the native BMC thresholds without configuring SNMP.

## Troubleshooting

Diagnostics test each piece against the BMC and never print passwords, tokens, cookies or
screen images, so the output can be shared:

```bash
docker compose exec webkvm python -m webkvm check            # everything
docker compose exec webkvm python -m webkvm check ipmi       # statistics only
docker compose exec webkvm python -m webkvm check kvm        # web login + KVM console only
docker compose exec webkvm python -m webkvm check kvm-watch 60   # + watch screen updates for 60 s
```

- **The project does not start, "Failed to load .env"**: `.env` is missing or not next to
  `docker-compose.yml`.
- **KVM: "connection refused" on port 5900**: the BMC's KVM service sometimes stops after many
  sessions in a short time. Reset the BMC (web interface → *Maintenance → Unit Reset*); the
  server keeps running.
- **"No video signal"**: the server is rebooting, the screen is off, or it uses an add-in
  graphics card (see [Limits](#limits-of-the-bmc-not-of-webkvm)).
- **The page looks old after an update**: the image was not rebuilt (see Synology above);
  `/healthz` shows the running version.
- **IPMI errors**: check that *IPMI over LAN* is enabled on the BMC and that the user has
  administrator rights.

## Security

- The X9 web interface and KVM port are not encrypted: BMC credentials travel in clear on the
  management LAN, exactly as with the original Java client. Keep the BMC on a trusted network.
- The gateway has its own login (scrypt hash, rate limit on failed attempts), `HttpOnly`
  `SameSite=Strict` cookies, CSRF tokens for commands and an Origin check on the WebSocket.
- To reach it from outside your LAN, put it behind an HTTPS reverse proxy (or a VPN) and set
  `WEBKVM_TRUSTED_PROXY=true`.
- Power commands, logins and console sessions are written to the `webkvm.audit` log.
- Passwords never appear in the logs.

## Development

```bash
pip install -r requirements.txt               # aiohttp, pyghmi
python -m unittest discover -s tests          # unit and end-to-end tests (simulated BMC)
BMC_HOST=... BMC_PASSWORD=... WEBKVM_PASSWORD=... DATA_DIR=./data NOVNC_DIR=./novnc python -m webkvm
```

noVNC (its `core/` and `vendor/` folders) must be in `NOVNC_DIR` or in
`webkvm/web/static/novnc` for the KVM page; the Docker image downloads it at build time.
The protocol is documented in [docs/PROTOCOL.md](docs/PROTOCOL.md).

```
webkvm/        Python package (python -m webkvm)
  kvm/         ATEN/Hermon protocol, framebuffer, RFB server, session, VNC auth
  bmc/         BMC web login (KVM token), IPMI (pyghmi or ipmitool)
  stats/       IPMI collector, SQLite history, Prometheus format
  web/         aiohttp app, login, API, WebSocket bridge; static/ = web UI (EN/IT)
zabbix/        Zabbix 7.4 / 8 template
tests/         unit and end-to-end tests
docs/          protocol reference
dev/           Windows helper scripts
```

## Credits and licences

- This project: [MIT licence](LICENSE).
- [noVNC](https://github.com/novnc/noVNC) (MPL-2.0), downloaded at build time, not included
  in this repository.
- [pyghmi](https://opendev.org/x/pyghmi) (Apache-2.0) and
  [aiohttp](https://github.com/aio-libs/aiohttp) (Apache-2.0), installed from PyPI.

Not affiliated with Supermicro or ATEN. Use the power controls at your own risk.
