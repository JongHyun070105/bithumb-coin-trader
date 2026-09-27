"""Metadata parser and isolation contract for accompanying external context text documents.

Enforces:
- Separation of letter text from historical 2018-2021 quantitative execution data
- Non-contemporaneous attribution (document reflects post-hoc self-description)
- Prohibition against using prose claims as quantitative ground truth or trade labels
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from bithumb_coin_trader.research_infra.external_expert import sha256_file


@dataclass(frozen=True)
class ExternalContextDocument:
    document_name: str
    sha256: str
    size_bytes: int
    character_count: int
    encoding: str
    temporal_relationship: str  # NON_CONTEMPORANEOUS_POST_HOC
    permissible_role: str  # QUALITATIVE_SELF_DESCRIBED_CONTEXT_ONLY
    prohibited_role: str  # NO_QUANTITATIVE_GROUND_TRUTH_NO_TRADE_LABELING
    topics_covered: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_name": self.document_name,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "character_count": self.character_count,
            "encoding": self.encoding,
            "temporal_relationship": self.temporal_relationship,
            "permissible_role": self.permissible_role,
            "prohibited_role": self.prohibited_role,
            "topics_covered": list(self.topics_covered),
            "governance_rule": (
                "Text letter was released alongside public dataset but discusses current market outlook "
                "(e.g. BTC 85k expectations) rather than contemporaneous 2018-2021 execution decisions. "
                "It must not be used to retroactively label or validate trades."
            ),
        }


def parse_context_document(file_path: Path) -> ExternalContextDocument:
    if not file_path.is_file():
        raise FileNotFoundError(f"Context document not found: {file_path}")

    sha = sha256_file(file_path)
    size = file_path.stat().st_size
    text = file_path.read_text(encoding="utf-8", errors="replace")

    topics = [
        "self_described_valuation_philosophy",
        "crypto_market_outlook_btc_eth_sol",
        "nasdaq_kospi_derivative_experience",
        "candle_volume_trend_heuristics",
        "capital_preservation_principles",
    ]

    return ExternalContextDocument(
        document_name=file_path.name,
        sha256=sha,
        size_bytes=size,
        character_count=len(text),
        encoding="utf-8",
        temporal_relationship="NON_CONTEMPORANEOUS_POST_HOC",
        permissible_role="QUALITATIVE_SELF_DESCRIBED_CONTEXT_ONLY",
        prohibited_role="NO_QUANTITATIVE_GROUND_TRUTH_NO_TRADE_LABELING",
        topics_covered=tuple(topics),
    )
