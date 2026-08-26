"""Bithumb Real-Time Market Microstructure Collector.

Connects to Bithumb's official WebSocket v1 API to ingest:
1. Orderbook stream (L2 Orderbook with depth 5~30)
2. Trade stream (Tick-level aggressive buys/sells with exact timestamps)
3. Ticker stream (Real-time mid-price, volume, and 24h stats)

Saves data in lossless, partitioned, append-only JSONL files for Strategy V9 research.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys
from typing import Any, Sequence
import uuid

try:
    import websockets
except ImportError:
    websockets = None  # Handled gracefully if websockets package needs install

ROOT = Path(__file__).resolve().parents[2]
MICROSTRUCTURE_DATA_DIR = ROOT / "data" / "microstructure"

BITHUMB_WS_URL = "wss://ws-api.bithumb.com/websocket/v1"
BITHUMB_PUB_WS_URL = "wss://pubwss.bithumb.com/pub/ws"

logger = logging.getLogger("bithumb_coin_trader.microstructure_collector")


class BithumbMicrostructureCollector:
    """Lossless Append-Only WebSocket Data Collector for Bithumb KRW Markets."""

    def __init__(
        self,
        target_markets: Sequence[str],
        base_dir: Path | None = None,
        *,
        ws_url: str = BITHUMB_WS_URL,
    ) -> None:
        self.target_markets = list(target_markets)
        self.base_dir = base_dir or MICROSTRUCTURE_DATA_DIR
        self.ws_url = ws_url
        self.is_running = False
        self._ensure_storage_dirs()

    def _ensure_storage_dirs(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        (self.base_dir / "orderbook").mkdir(parents=True, exist_ok=True)
        (self.base_dir / "trade").mkdir(parents=True, exist_ok=True)
        (self.base_dir / "ticker").mkdir(parents=True, exist_ok=True)

    def _get_partition_path(self, stream_type: str, timestamp: datetime) -> Path:
        dt_str = timestamp.strftime("%Y-%m-%d")
        hour_str = timestamp.strftime("%H")
        dir_path = self.base_dir / stream_type / dt_str
        dir_path.mkdir(parents=True, exist_ok=True)
        return dir_path / f"{stream_type}_{dt_str}_{hour_str}.jsonl"

    def _append_record(self, stream_type: str, record: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc)
        record["ingested_at"] = now.isoformat()
        file_path = self._get_partition_path(stream_type, now)
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with file_path.open("a", encoding="utf-8") as f:
            f.write(line)

    def build_subscription_payload(self) -> list[dict[str, Any]]:
        ticket_id = f"bithumb_v9_{uuid.uuid4().hex[:8]}"
        return [
            {"ticket": ticket_id},
            {"type": "orderbook", "codes": self.target_markets},
            {"type": "trade", "codes": self.target_markets},
            {"type": "ticker", "codes": self.target_markets},
            {"format": "DEFAULT"},
        ]

    async def run_collector(self, max_duration_seconds: float | None = None) -> None:
        if websockets is None:
            raise RuntimeError("The 'websockets' library is required. Install via pip.")

        self.is_running = True
        start_time = asyncio.get_event_loop().time()
        payload = json.dumps(self.build_subscription_payload())

        logger.info(f"Starting Bithumb WebSocket Collector for {len(self.target_markets)} markets...")

        while self.is_running:
            try:
                async with websockets.connect(self.ws_url, ping_interval=20, ping_timeout=10) as ws:
                    logger.info(f"Connected to {self.ws_url}. Sending subscriptions...")
                    await ws.send(payload)

                    while self.is_running:
                        if max_duration_seconds:
                            elapsed = asyncio.get_event_loop().time() - start_time
                            if elapsed >= max_duration_seconds:
                                logger.info(f"Reached max duration {max_duration_seconds}s. Stopping.")
                                self.is_running = False
                                break

                        msg = await ws.recv()
                        if isinstance(msg, bytes):
                            msg_str = msg.decode("utf-8")
                        else:
                            msg_str = str(msg)

                        data = json.loads(msg_str)
                        msg_type = data.get("type", "").lower()

                        if msg_type in ("orderbook", "trade", "ticker"):
                            self._append_record(msg_type, data)

            except Exception as e:
                logger.warning(f"WebSocket error: {e}. Reconnecting in 3 seconds...")
                await asyncio.sleep(3.0)

        logger.info("Collector stopped.")


def run_standalone_collector(markets: Sequence[str], duration: float | None = None) -> None:
    collector = BithumbMicrostructureCollector(markets)
    asyncio.run(collector.run_collector(max_duration_seconds=duration))
