/* UI translations (English / Italian).
 *
 * Usage: elements with data-i18n="key" get their text replaced, data-i18n-title and
 * data-i18n-aria the matching attribute. Scripts call I18N.t('key', {var: value}).
 * Language: saved choice, otherwise the browser language (it* -> Italian, else English).
 * The button with id "lang" shows the active language; a click switches and reloads. */
(function () {
  'use strict';

  const STRINGS = {
    en: {
      'login.title': 'webkvm - sign in',
      'login.subtitle': 'KVM console and BMC statistics',
      'login.user': 'User',
      'login.password': 'Password',
      'login.submit': 'Sign in',
      'login.err.blocked': 'Too many failed attempts: try again in a few minutes.',
      'login.err.bad': 'Wrong user or password.',

      'top.power_pill': 'Power',
      'top.open_kvm': 'Open KVM console',
      'top.power_menu': 'Power ▾',
      'top.logout': 'Log out',
      'power.on': 'Power on',
      'power.soft': 'ACPI shutdown (soft)',
      'power.reset': 'Reset',
      'power.cycle': 'Power cycle',
      'power.off': 'Power off (forced)',
      'power.confirm.on': 'Power on the server?',
      'power.confirm.on.text': 'Sends the power-on command to the BMC.',
      'power.confirm.soft': 'ACPI shutdown?',
      'power.confirm.soft.text': 'Asks the operating system to shut down cleanly (like a short press of the power button).',
      'power.confirm.reset': 'Reset the server?',
      'power.confirm.reset.text': 'Immediate cold restart: unsaved data is lost.',
      'power.confirm.cycle': 'Power cycle?',
      'power.confirm.cycle.text': 'Turns the server off and on again right away: unsaved data is lost.',
      'power.confirm.off': 'Forced power off?',
      'power.confirm.off.text': 'Cuts power immediately, like holding the power button: unsaved data is lost.',
      'power.sent': 'Command sent: {r}',
      'power.failed': 'Command failed: {e}',
      'dlg.cancel': 'Cancel',
      'dlg.confirm': 'Confirm',

      'sec.system': 'System',
      'sec.sensors': 'Sensors',
      'sec.history': 'History',
      'sec.sel': 'Event log (SEL)',
      'sensors.waiting': 'Waiting for the first IPMI reading…',
      'chart.sensor': 'Sensor',
      'range.7d': '7 d',
      'sel.date': 'Date', 'sel.time': 'Time', 'sel.sensor': 'Sensor', 'sel.event': 'Event', 'sel.state': 'State',
      'sel.loading': 'Loading…',
      'sel.count': '{n} entries in the BMC',
      'sel.empty': 'No events recorded.',
      'sel.search': 'Search sensor, event, date…',
      'sel.all_states': 'All states',
      'sel.asserted': 'Asserted',
      'sel.deasserted': 'Deasserted',
      'sel.shown': '{n} of {m} shown',
      'sel.no_match': 'No event matches the filters.',
      'sel.clear': 'Clear filters',
      'sel.all_sensors': 'All sensors',

      'state.gateway_down': 'gateway unreachable',
      'state.bmc_up': 'BMC reachable',
      'state.bmc_down': 'BMC unreachable',
      'state.power_unknown': 'Power ?',
      'state.on': 'On',
      'state.off': 'Off',
      'state.last_read': 'Last reading: {t}',
      'state.error': ' · error: {e}',
      'ago.never': 'never',
      'ago.s': '{n} s ago',
      'ago.min': '{n} min ago',
      'kvm.idle': 'idle', 'kvm.connecting': 'connecting…', 'kvm.connected': 'connected', 'kvm.retrying': 'reconnecting…',
      'fact.board': 'Board',
      'fact.board_serial': 'Board serial',
      'fact.bmc_fw': 'BMC firmware',
      'fact.restore': 'Power restore policy',
      'fact.last_power': 'Last power event',
      'fact.intrusion': 'Chassis intrusion',
      'fact.intrusion.yes': 'DETECTED',
      'fact.faults': 'Chassis faults',
      'fact.faults.none': 'none',
      'fact.kvm': 'KVM console',
      'fact.viewers': '{n} viewer(s)',
      'fact.vnc': 'Native VNC',
      'fact.vnc.port': 'port {p}',
      'fact.vnc.off': 'disabled',
      'common.no': 'no',
      'type.temperature': 'Temperatures', 'type.fan': 'Fans', 'type.voltage': 'Voltages', 'type.power': 'Power',
      'type.current': 'Current', 'type.discrete': 'State', 'type.other': 'Other', 'type.chassis': 'Chassis',
      'sensor.absent': 'absent',
      'thr.warn_below': 'warn < {v}',
      'thr.crit_below': 'crit < {v}',
      'thr.warn': 'warn {v}',
      'thr.crit': 'crit {v}',
      'thr.crit_range': 'crit {a} – {b}',
      'chart.power': 'Power (1 = on)',
      'chart.empty': 'No data in the selected period.',
      'chart.aria': 'sensor history chart',
      'chart.last': 'Last: {v} · {t}',
      'chart.point': '{t} · avg {v}',
      'chart.minmax': ' (min {a}, max {b})',
      'error': 'Error: {e}',

      'kvmp.title': 'webkvm · console',
      'kvmp.connecting': 'Connecting…',
      'kvmp.cad_title': 'Send Ctrl+Alt+Del',
      'kvmp.cad': 'Ctrl+Alt+Del',
      'kvmp.keys': 'Keys ▾',
      'kvmp.key.f2': 'F2 (BIOS setup)',
      'kvmp.key.del': 'Del (BIOS setup)',
      'kvmp.key.f11': 'F11 (boot menu)',
      'kvmp.key.f12': 'F12 (network boot)',
      'kvmp.key.win': 'Windows key',
      'kvmp.key.prtsc': 'Print Screen',
      'kvmp.scale_title': 'Fit to window or 1:1',
      'kvmp.fit': 'Fit',
      'kvmp.full': 'Full screen',
      'kvmp.reconnect': 'Reconnect',
      'kvmp.retry': 'Retry',
      'kvmp.ov.connecting': 'Connecting to the console…',
      'kvmp.ov.connecting.text': 'Signing in to the BMC and waiting for the first frame (a few seconds).',
      'kvmp.ov.unavailable': 'Console unavailable',
      'kvmp.ov.refused': 'The BMC refused the connection.',
      'kvmp.ov.lost': 'Connection lost',
      'kvmp.ov.retry_in': 'Retrying in {n} s…',
      'kvmp.ov.dead': 'The console does not respond. Check the dashboard for the BMC state.',
      'kvmp.st.connected': 'Connected',
      'kvmp.st.connected_res': 'Connected · {w}×{h}',
      'kvmp.st.no_signal': 'Connected · no video signal (rebooting or screen off)',
      'kvmp.st.bmc_down': 'BMC unreachable, retrying…',
      'kvmp.st.bmc_connecting': 'Connecting to the BMC…',
      'kvmp.st.frozen': 'Frozen image: the session to the BMC dropped',
      'kvmp.st.error': 'Error',
      'kvmp.st.disconnected': 'Disconnected',
    },
    it: {
      'login.title': 'webkvm - accesso',
      'login.subtitle': 'Console KVM e statistiche del BMC',
      'login.user': 'Utente',
      'login.password': 'Password',
      'login.submit': 'Accedi',
      'login.err.blocked': 'Troppi tentativi falliti: riprova tra qualche minuto.',
      'login.err.bad': 'Utente o password errati.',

      'top.power_pill': 'Alimentazione',
      'top.open_kvm': 'Apri console KVM',
      'top.power_menu': 'Alimentazione ▾',
      'top.logout': 'Esci',
      'power.on': 'Accendi',
      'power.soft': 'Spegnimento ACPI (soft)',
      'power.reset': 'Reset',
      'power.cycle': 'Power cycle',
      'power.off': 'Spegni (forzato)',
      'power.confirm.on': 'Accendere il server?',
      'power.confirm.on.text': 'Invia il comando di accensione al BMC.',
      'power.confirm.soft': 'Spegnimento ACPI?',
      'power.confirm.soft.text': 'Chiede al sistema operativo di spegnersi in modo ordinato (come una breve pressione del tasto).',
      'power.confirm.reset': 'Reset del server?',
      'power.confirm.reset.text': 'Riavvio immediato a freddo: i dati non salvati vanno persi.',
      'power.confirm.cycle': 'Power cycle?',
      'power.confirm.cycle.text': 'Spegne e riaccende subito il server: i dati non salvati vanno persi.',
      'power.confirm.off': 'Spegnimento forzato?',
      'power.confirm.off.text': 'Toglie l\'alimentazione subito, come tenere premuto il tasto: i dati non salvati vanno persi.',
      'power.sent': 'Comando inviato: {r}',
      'power.failed': 'Comando fallito: {e}',
      'dlg.cancel': 'Annulla',
      'dlg.confirm': 'Conferma',

      'sec.system': 'Sistema',
      'sec.sensors': 'Sensori',
      'sec.history': 'Storico',
      'sec.sel': 'Registro eventi (SEL)',
      'sensors.waiting': 'In attesa della prima lettura IPMI…',
      'chart.sensor': 'Sensore',
      'range.7d': '7 g',
      'sel.date': 'Data', 'sel.time': 'Ora', 'sel.sensor': 'Sensore', 'sel.event': 'Evento', 'sel.state': 'Stato',
      'sel.loading': 'Caricamento…',
      'sel.count': '{n} voci nel BMC',
      'sel.empty': 'Nessun evento registrato.',
      'sel.search': 'Cerca sensore, evento, data…',
      'sel.all_states': 'Tutti gli stati',
      'sel.asserted': 'Attivato (Asserted)',
      'sel.deasserted': 'Rientrato (Deasserted)',
      'sel.shown': '{n} di {m} mostrati',
      'sel.no_match': 'Nessun evento corrisponde ai filtri.',
      'sel.clear': 'Azzera filtri',
      'sel.all_sensors': 'Tutti i sensori',

      'state.gateway_down': 'gateway non raggiungibile',
      'state.bmc_up': 'BMC raggiungibile',
      'state.bmc_down': 'BMC non raggiungibile',
      'state.power_unknown': 'Alimentazione ?',
      'state.on': 'Acceso',
      'state.off': 'Spento',
      'state.last_read': 'Ultima lettura: {t}',
      'state.error': ' · errore: {e}',
      'ago.never': 'mai',
      'ago.s': '{n} s fa',
      'ago.min': '{n} min fa',
      'kvm.idle': 'inattiva', 'kvm.connecting': 'connessione…', 'kvm.connected': 'collegata', 'kvm.retrying': 'riconnessione…',
      'fact.board': 'Scheda',
      'fact.board_serial': 'Seriale scheda',
      'fact.bmc_fw': 'Firmware BMC',
      'fact.restore': 'Politica al ritorno corrente',
      'fact.last_power': 'Ultimo evento alimentazione',
      'fact.intrusion': 'Intrusione chassis',
      'fact.intrusion.yes': 'RILEVATA',
      'fact.faults': 'Guasti chassis',
      'fact.faults.none': 'nessuno',
      'fact.kvm': 'Console KVM',
      'fact.viewers': '{n} viewer',
      'fact.vnc': 'VNC nativo',
      'fact.vnc.port': 'porta {p}',
      'fact.vnc.off': 'disattivato',
      'common.no': 'no',
      'type.temperature': 'Temperature', 'type.fan': 'Ventole', 'type.voltage': 'Tensioni', 'type.power': 'Potenza',
      'type.current': 'Corrente', 'type.discrete': 'Stato', 'type.other': 'Altro', 'type.chassis': 'Chassis',
      'sensor.absent': 'assente',
      'thr.warn_below': 'avviso < {v}',
      'thr.crit_below': 'crit < {v}',
      'thr.warn': 'avviso {v}',
      'thr.crit': 'crit {v}',
      'thr.crit_range': 'crit {a} – {b}',
      'chart.power': 'Alimentazione (1 = acceso)',
      'chart.empty': 'Nessun dato nel periodo selezionato.',
      'chart.aria': 'grafico storico del sensore',
      'chart.last': 'Ultimo: {v} · {t}',
      'chart.point': '{t} · media {v}',
      'chart.minmax': ' (min {a}, max {b})',
      'error': 'Errore: {e}',

      'kvmp.title': 'webkvm · console',
      'kvmp.connecting': 'Connessione…',
      'kvmp.cad_title': 'Invia Ctrl+Alt+Canc',
      'kvmp.cad': 'Ctrl+Alt+Canc',
      'kvmp.keys': 'Tasti ▾',
      'kvmp.key.f2': 'F2 (setup BIOS)',
      'kvmp.key.del': 'Canc (setup BIOS)',
      'kvmp.key.f11': 'F11 (menu di avvio)',
      'kvmp.key.f12': 'F12 (avvio da rete)',
      'kvmp.key.win': 'Tasto Windows',
      'kvmp.key.prtsc': 'Stamp',
      'kvmp.scale_title': 'Adatta alla finestra o 1:1',
      'kvmp.fit': 'Adatta',
      'kvmp.full': 'Schermo intero',
      'kvmp.reconnect': 'Riconnetti',
      'kvmp.retry': 'Riprova',
      'kvmp.ov.connecting': 'Connessione alla console…',
      'kvmp.ov.connecting.text': 'Login al BMC e attesa del primo frame (pochi secondi).',
      'kvmp.ov.unavailable': 'Console non disponibile',
      'kvmp.ov.refused': 'Il BMC ha rifiutato la connessione.',
      'kvmp.ov.lost': 'Connessione persa',
      'kvmp.ov.retry_in': 'Nuovo tentativo tra {n} s…',
      'kvmp.ov.dead': 'La console non risponde. Controlla la dashboard per lo stato del BMC.',
      'kvmp.st.connected': 'Collegato',
      'kvmp.st.connected_res': 'Collegato · {w}×{h}',
      'kvmp.st.no_signal': 'Collegato · nessun segnale video (riavvio o schermo spento)',
      'kvmp.st.bmc_down': 'BMC non raggiungibile, riprovo…',
      'kvmp.st.bmc_connecting': 'Connessione al BMC…',
      'kvmp.st.frozen': 'Immagine ferma: la sessione verso il BMC è caduta',
      'kvmp.st.error': 'Errore',
      'kvmp.st.disconnected': 'Disconnesso',
    },
  };

  const KEY = 'webkvm.lang';

  function detect() {
    try {
      const saved = localStorage.getItem(KEY);
      if (saved && STRINGS[saved]) return saved;
    } catch (e) { /* storage unavailable: fall back to the browser language */ }
    return (navigator.language || 'en').toLowerCase().startsWith('it') ? 'it' : 'en';
  }

  const lang = detect();
  const locale = lang === 'it' ? 'it-IT' : 'en-GB';

  function t(key, vars) {
    let s = STRINGS[lang][key];
    if (s === undefined) s = STRINGS.en[key];
    if (s === undefined) return key;
    if (vars) s = s.replace(/\{(\w+)\}/g, (m, k) => (vars[k] !== undefined ? vars[k] : m));
    return s;
  }

  function apply(root) {
    const r = root || document;
    r.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
    r.querySelectorAll('[data-i18n-title]').forEach(el => { el.title = t(el.dataset.i18nTitle); });
    r.querySelectorAll('[data-i18n-aria]').forEach(el => { el.setAttribute('aria-label', t(el.dataset.i18nAria)); });
    r.querySelectorAll('[data-i18n-placeholder]').forEach(el => { el.placeholder = t(el.dataset.i18nPlaceholder); });
    document.documentElement.lang = lang;
  }

  function setLang(next) {
    try { localStorage.setItem(KEY, next); } catch (e) { /* not persisted: applies to this page only */ }
    location.reload();
  }

  window.I18N = { t, lang, locale, apply, setLang };

  document.addEventListener('DOMContentLoaded', () => {
    apply();
    const btn = document.getElementById('lang');
    if (btn) {
      // shows the ACTIVE language; the tooltip says what a click does
      btn.textContent = lang.toUpperCase();
      btn.title = lang === 'it' ? 'Passa all\'inglese' : 'Switch to Italian';
      btn.setAttribute('aria-label', btn.title);
      btn.addEventListener('click', () => setLang(lang === 'it' ? 'en' : 'it'));
    }
  });
})();
