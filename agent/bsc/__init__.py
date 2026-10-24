"""KAGOU's BNB Chain realtime data layer: failover RPC pool, newHeads listener with backfill and reorg
handling, event store, monitors, and the hub that feeds the console's WebSocket. Read-only."""

from .service import ChainService

__all__ = ["ChainService"]
