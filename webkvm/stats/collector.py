"""Periodic polling of the BMC over IPMI. Keeps the latest state in memory and writes the history."""
import asyncio
import logging
import time

from ..bmc.ipmi import IpmiError

log = logging.getLogger('webkvm.collector')

POWER_SENSOR = 'Chassis Power'      # pseudo-sensor in the history: 1 on, 0 off


class Collector:
    def __init__(self, cfg, ipmi, store):
        self.cfg = cfg
        self.ipmi = ipmi
        self.store = store
        self.sensors = []
        self.chassis = {}
        self.info = {}
        self.up = False
        self.last_ok = None
        self.last_error = None
        self.scrape_duration = None
        self.sel_total = 0
        self.sel_new_total = 0          # new entries seen since the process started
        self._task = None
        self._wake = asyncio.Event()

    def start(self):
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def refresh_soon(self):
        """Force a new polling round (e.g. after a power command)."""
        self._wake.set()

    async def _loop(self):
        await self._load_info()
        await self._poll_sel()
        last_sel = time.monotonic()
        last_purge = 0
        while True:
            try:
                await self._poll()
                now = time.monotonic()
                if now - last_sel >= self.cfg.sel_interval:
                    await self._poll_sel()
                    last_sel = now
                if now - last_purge >= 3600:
                    cutoff = time.time() - self.cfg.retention_days * 86400
                    await asyncio.to_thread(self.store.purge, cutoff)
                    last_purge = now
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception('collector error')
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), self.cfg.ipmi_interval)
            except asyncio.TimeoutError:
                pass

    async def _load_info(self):
        try:
            mc = await self.ipmi.mc_info()
            fru = await self.ipmi.fru()
        except IpmiError as e:
            log.warning('BMC info not available: %s', e)
            return
        self.info = {
            'bmc_firmware': mc.get('Firmware Revision'),
            'ipmi_version': mc.get('IPMI Version'),
            'manufacturer': mc.get('Manufacturer Name'),
            'product': fru.get('Board Product') or mc.get('Product Name'),
            'board_serial': fru.get('Board Serial'),
            'board_mfg': fru.get('Board Mfg'),
            'product_name': fru.get('Product Name'),
            'product_serial': fru.get('Product Serial'),
        }

    async def _poll(self):
        t0 = time.monotonic()
        try:
            sensors = await self.ipmi.sensors()
            chassis = await self.ipmi.chassis()
        except IpmiError as e:
            if self.up or self.last_error != str(e):
                log.warning('IPMI polling failed: %s', e)
            self.up = False
            self.last_error = str(e)
            return
        self.scrape_duration = time.monotonic() - t0
        self.sensors = sensors
        self.chassis = chassis
        if not self.up:
            log.info('BMC reachable over IPMI (%d sensors)', len(sensors))
        self.up = True
        self.last_ok = time.time()
        self.last_error = None
        samples = [(s['name'], s['unit'], s['type'], s['value'])
                   for s in sensors if s['numeric']]
        if chassis.get('power_on') is not None:
            samples.append((POWER_SENSOR, '', 'chassis', 1.0 if chassis['power_on'] else 0.0))
        await asyncio.to_thread(self.store.add_samples, self.last_ok, samples)
        if not self.info:
            await self._load_info()

    async def _poll_sel(self):
        try:
            entries = await self.ipmi.sel()
        except IpmiError as e:
            log.info('SEL read failed: %s', e)
            return
        self.sel_total = len(entries)
        new = await asyncio.to_thread(self.store.add_sel, entries)
        if new and self.last_ok is not None:
            self.sel_new_total += len(new)
            for e in new[-5:]:
                log.info('SEL: %s %s %s %s', e['date'], e['time'], e['sensor'], e['event'])

    def snapshot(self):
        return {
            'up': self.up,
            'ts': self.last_ok,
            'last_error': self.last_error,
            'scrape_duration': self.scrape_duration,
            'power_on': self.chassis.get('power_on'),
            'chassis': self.chassis,
            'sel_count': self.sel_total,
            'sensors': self.sensors,
        }
