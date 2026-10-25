"""Background duties that used to live in the Telegram bot process, now run by the server.

The chain monitor's pass and the finance employee's basic treasury alerts only ever needed the ledger: every alert
is written to the store as an "alert" event, which the console shows. The bot's part was posting them to a group;
KAGOU has no Telegram, so that part is gone and nothing here sends a message anywhere.

Scheduled reports, digests and the console outbox were Telegram posts only and are not carried over: the console
builds the same reports on demand (/api/report/weekly, /api/report/today).
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

from agent.skills.finance_ops.alerts import check_transfers
from agent.skills.finance_ops.insights import TelegramSettings

log = logging.getLogger("kagou.background")


class BackgroundDuties:
    def __init__(self, desk, source=None, treasury=(), thresholds=None, every_s: float = 300) -> None:
        self.desk = desk
        self.source = source  # Etherscan-style transfer source for the basic treasury alerts; None turns them off
        self.treasury = tuple(treasury)
        self.thresholds = dict(thresholds or {})
        self.every_s = every_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self, now: datetime | None = None) -> list[str]:
        """One pass. Returns the text of every new alert (each is logged to the store exactly once)."""
        now = now or datetime.now(timezone.utc)
        desk = self.desk
        if desk.on("chain_monitor") and desk.monitor.ready:
            return [a["text"] for a in desk.monitor.run_once(now)]
        store = desk.finance.store
        if not (self.source and self.treasury and TelegramSettings.load(store).treasury_alerts):
            return []
        # Without a chain monitor on the team, the finance employee keeps its basic treasury alerts.
        since = now - timedelta(hours=1)
        transfers = [t for addr in self.treasury for t in self.source.transfers(addr, since)]
        new = []
        for alert in check_transfers(transfers, self.treasury, list(desk.finance.policy.whitelist), self.thresholds):
            key = "alert:" + alert.key
            if store.get_setting(key) is None:  # each alert once, even across restarts
                store.set_setting(key, "1")
                store.log_event("alert", alert.text, "monitor", {"key": alert.key})
                new.append(alert.text)
        return new

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="kagou-background", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:  # noqa: BLE001 - one failed pass must not end the loop
                log.exception("background pass failed")
            self._stop.wait(self.every_s)
