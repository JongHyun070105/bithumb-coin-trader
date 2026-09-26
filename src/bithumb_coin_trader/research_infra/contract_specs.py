"""Historical BitMEX contract specifications, notional calculators, and semantics audit.

Ground truth specifications sourced from historical BitMEX API documentation and public instrument metadata (2018-2021).
Covers:
- Inverse contracts: XBTUSD, dated futures (XBTM18, XBTU18, XBTZ18, XBTH19, XBTM19, XBTU19, XBTH20, XBTU21)
- Quanto contracts: ETHUSD, XRPUSD (settled in Bitcoin with fixed satoshi/pt multipliers)
- Linear satoshi altcoin futures: ADAM18, ADAM19, TRXU18, TRXU19, TRXZ18, TRXH19, XRPU18, XRPZ18, XRPH19, XRPH21, EOSM18, EOSU18, EOSZ18, EOSH21, BCHM18, BCHZ18, BCHM19, LTCU18, LTCH19
- Linear USDT contracts: LINKUSDT, DOTUSDT, DOGEUSDT, ADAUSDT, BNBUSDT, YFIUSDTZ20
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class HistoricalContractSpec:
    symbol: str
    family: str  # INVERSE_PERP, INVERSE_FUTURE, QUANTO_PERP, LINEAR_SATOSHI_FUTURE, LINEAR_USDT_PERP, UNVERIFIED
    underlying: str
    quote_currency: str
    settl_currency: str
    multiplier: Decimal  # Multiplier per contract
    multiplier_unit: str
    is_inverse: bool
    is_quanto: bool
    funding_applicable: bool
    maker_fee_rate: Decimal  # e.g. -0.00025 for -0.025% rebate
    taker_fee_rate: Decimal  # e.g. 0.00075 for 0.075% fee
    source_provenance: str

    def calculate_notional(
        self,
        quantity: Decimal,
        price: Decimal,
        btc_usd_price: Decimal | None = None,
    ) -> dict[str, Decimal]:
        """Calculate exact contract volume, USD notional, and BTC notional without conflation."""
        contracts = abs(quantity)
        if contracts == 0 or price <= 0:
            return {
                "contracts": Decimal(0),
                "usd_notional": Decimal(0),
                "btc_notional": Decimal(0),
                "native_notional": Decimal(0),
            }

        if self.is_inverse:
            # 1 contract = $1 USD.
            # USD notional = contracts. BTC notional = contracts / price.
            usd_notional = contracts
            btc_notional = contracts / price
            native_notional = usd_notional
        elif self.is_quanto:
            if self.symbol == "ETHUSD":
                # 1 contract = 0.000001 BTC per USD point (100 satoshi / pt)
                # USD notional = contracts * price
                usd_notional = contracts * price
                btc_notional = usd_notional * self.multiplier
                native_notional = usd_notional
            elif self.symbol == "XRPUSD":
                # 1 contract = 0.0002 BTC (20,000 satoshi) * price (in USD)
                # In BitMEX, foreignnotional was contracts * price * multiplier in USD terms
                usd_notional = contracts * price
                btc_notional = contracts * self.multiplier  # 0.0002 BTC per contract
                native_notional = usd_notional
            else:
                usd_notional = contracts * price
                btc_notional = (usd_notional / btc_usd_price) if btc_usd_price else Decimal(0)
                native_notional = usd_notional
        elif self.family == "LINEAR_SATOSHI_FUTURE":
            # 1 contract = 1 token (e.g. 1 ADA, 1 TRX). Price is in XBT (satoshi/coin).
            # Native notional = contracts (tokens). BTC notional = contracts * price.
            native_notional = contracts
            btc_notional = contracts * price
            usd_notional = (btc_notional * btc_usd_price) if btc_usd_price else Decimal(0)
        elif self.family == "LINEAR_USDT_PERP":
            # 1 contract = 1 USDT of underlying. Price in USDT.
            usd_notional = contracts * price
            native_notional = usd_notional
            btc_notional = (usd_notional / btc_usd_price) if btc_usd_price else Decimal(0)
        else:
            # UNVERIFIED
            native_notional = contracts
            usd_notional = Decimal(0)
            btc_notional = Decimal(0)

        return {
            "contracts": contracts,
            "usd_notional": round(usd_notional, 2),
            "btc_notional": round(btc_notional, 8),
            "native_notional": round(native_notional, 4),
        }

    def calculate_cycle_pnl_btc(
        self,
        direction: str,
        matched_qty: Decimal,
        entry_vwap: Decimal,
        exit_vwap: Decimal,
    ) -> Decimal:
        """Calculate gross realized PnL in Bitcoin (BTC) under historical contract semantics."""
        if matched_qty <= 0 or entry_vwap <= 0 or exit_vwap <= 0:
            return Decimal(0)

        if self.is_inverse:
            # PnL in BTC = contracts * (1/entry - 1/exit) for Long
            if direction == "LONG":
                return matched_qty * (Decimal(1) / entry_vwap - Decimal(1) / exit_vwap)
            else:
                return matched_qty * (Decimal(1) / exit_vwap - Decimal(1) / entry_vwap)
        elif self.is_quanto:
            if self.symbol == "ETHUSD":
                # Multiplier = 0.000001 BTC / point
                diff = (exit_vwap - entry_vwap) if direction == "LONG" else (entry_vwap - exit_vwap)
                return matched_qty * diff * self.multiplier
            elif self.symbol == "XRPUSD":
                # Multiplier = 0.0002 BTC / point
                diff = (exit_vwap - entry_vwap) if direction == "LONG" else (entry_vwap - exit_vwap)
                return matched_qty * diff * self.multiplier
            else:
                # Fallback quanto
                diff = (exit_vwap - entry_vwap) if direction == "LONG" else (entry_vwap - exit_vwap)
                return matched_qty * diff * self.multiplier
        elif self.family == "LINEAR_SATOSHI_FUTURE":
            # Price is already in BTC per token
            diff = (exit_vwap - entry_vwap) if direction == "LONG" else (entry_vwap - exit_vwap)
            return matched_qty * diff
        else:
            # Linear quote approximation
            diff = (exit_vwap - entry_vwap) if direction == "LONG" else (entry_vwap - exit_vwap)
            return matched_qty * diff


# Authoritative Contract Specification Registry
CONTRACT_SPECS: dict[str, HistoricalContractSpec] = {
    "XBTUSD": HistoricalContractSpec(
        symbol="XBTUSD",
        family="INVERSE_PERP",
        underlying="BTC",
        quote_currency="USD",
        settl_currency="XBt",
        multiplier=Decimal(1),
        multiplier_unit="USD",
        is_inverse=True,
        is_quanto=False,
        funding_applicable=True,
        maker_fee_rate=Decimal("-0.00025"),
        taker_fee_rate=Decimal("0.00075"),
        source_provenance="BitMEX Public Specification API /instrument (XBTUSD)",
    ),
    "ETHUSD": HistoricalContractSpec(
        symbol="ETHUSD",
        family="QUANTO_PERP",
        underlying="ETH",
        quote_currency="USD",
        settl_currency="XBt",
        multiplier=Decimal("0.000001"),  # 100 satoshi per USD point
        multiplier_unit="XBt_per_USD",
        is_inverse=False,
        is_quanto=True,
        funding_applicable=True,
        maker_fee_rate=Decimal("-0.00025"),
        taker_fee_rate=Decimal("0.00075"),
        source_provenance="BitMEX Public Specification API /instrument (ETHUSD Quanto Perpetual)",
    ),
    "XRPUSD": HistoricalContractSpec(
        symbol="XRPUSD",
        family="QUANTO_PERP",
        underlying="XRP",
        quote_currency="USD",
        settl_currency="XBt",
        multiplier=Decimal("0.0002"),  # 20,000 satoshi per USD point
        multiplier_unit="XBt_per_USD",
        is_inverse=False,
        is_quanto=True,
        funding_applicable=True,
        maker_fee_rate=Decimal("-0.00025"),
        taker_fee_rate=Decimal("0.00075"),
        source_provenance="BitMEX Public Specification API /instrument (XRPUSD Quanto Perpetual)",
    ),
}

# Add all dated XBT inverse futures
for symbol in ["XBTM18", "XBTU18", "XBTZ18", "XBTH19", "XBTM19", "XBTU19", "XBTH20", "XBTU21"]:
    CONTRACT_SPECS[symbol] = HistoricalContractSpec(
        symbol=symbol,
        family="INVERSE_FUTURE",
        underlying="BTC",
        quote_currency="USD",
        settl_currency="XBt",
        multiplier=Decimal(1),
        multiplier_unit="USD",
        is_inverse=True,
        is_quanto=False,
        funding_applicable=False,
        maker_fee_rate=Decimal("-0.00025"),
        taker_fee_rate=Decimal("0.00075"),
        source_provenance=f"BitMEX Historical Inverse Dated Future ({symbol})",
    )

# Add linear satoshi altcoin futures
SATOSHI_FUTURES = [
    "ADAM18", "ADAM19", "ADAU18", "ADAZ18",
    "TRXU18", "TRXU19", "TRXZ18", "TRXH19",
    "XRPM18", "XRPU18", "XRPZ18", "XRPH19", "XRPM19", "XRPH21",
    "EOSM18", "EOSU18", "EOSZ18", "EOSH21",
    "BCHM18", "BCHU18", "BCHZ18", "BCHM19",
    "LTCU18", "LTCH19",
]
for symbol in SATOSHI_FUTURES:
    CONTRACT_SPECS[symbol] = HistoricalContractSpec(
        symbol=symbol,
        family="LINEAR_SATOSHI_FUTURE",
        underlying=symbol[:3],
        quote_currency="XBT",
        settl_currency="XBt",
        multiplier=Decimal(1),
        multiplier_unit="Token",
        is_inverse=False,
        is_quanto=False,
        funding_applicable=False,
        maker_fee_rate=Decimal("-0.00025"),
        taker_fee_rate=Decimal("0.00075"),
        source_provenance=f"BitMEX Historical Altcoin Linear Satoshi Future ({symbol})",
    )

# Add linear USDT perpetuals
USDT_PERPS = ["LINKUSDT", "DOTUSDT", "DOGEUSDT", "ADAUSDT", "BNBUSDT", "YFIUSDTZ20"]
for symbol in USDT_PERPS:
    CONTRACT_SPECS[symbol] = HistoricalContractSpec(
        symbol=symbol,
        family="LINEAR_USDT_PERP",
        underlying=symbol.replace("USDT", "").replace("Z20", ""),
        quote_currency="USDT",
        settl_currency="XBt",
        multiplier=Decimal(1),
        multiplier_unit="Token",
        is_inverse=False,
        is_quanto=False,
        funding_applicable=True,
        maker_fee_rate=Decimal("-0.00025"),
        taker_fee_rate=Decimal("0.00075"),
        source_provenance=f"BitMEX Historical Linear USDT Perpetual ({symbol})",
    )


def get_contract_spec(symbol: str) -> HistoricalContractSpec:
    """Retrieve historical contract spec or return UNVERIFIED spec."""
    if symbol in CONTRACT_SPECS:
        return CONTRACT_SPECS[symbol]

    # Check prefix matching
    if symbol.startswith("XBT") or symbol.startswith("BTC"):
        return HistoricalContractSpec(
            symbol=symbol,
            family="INVERSE_FUTURE",
            underlying="BTC",
            quote_currency="USD",
            settl_currency="XBt",
            multiplier=Decimal(1),
            multiplier_unit="USD",
            is_inverse=True,
            is_quanto=False,
            funding_applicable=False,
            maker_fee_rate=Decimal("-0.00025"),
            taker_fee_rate=Decimal("0.00075"),
            source_provenance="Inferred Inverse Bitcoin Derivative",
        )

    return HistoricalContractSpec(
        symbol=symbol,
        family="UNVERIFIED",
        underlying="UNKNOWN",
        quote_currency="USD",
        settl_currency="XBt",
        multiplier=Decimal(1),
        multiplier_unit="UNKNOWN",
        is_inverse=False,
        is_quanto=False,
        funding_applicable=False,
        maker_fee_rate=Decimal("-0.00025"),
        taker_fee_rate=Decimal("0.00075"),
        source_provenance="UNVERIFIED_CONTRACT_SEMANTICS",
    )
