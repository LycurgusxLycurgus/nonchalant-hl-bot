"""Hyperliquid exchange adapter wired to the official Python SDK."""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Any

from eth_account import Account
from eth_account.signers.local import LocalAccount
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info

from app.lib.logger import get_logger
from app.lib.metrics import METRICS


logger = get_logger(__name__)


@dataclass
class ExchangeCredentials:
    """Represents the decrypted agent credentials needed for signing."""

    address: str
    private_key: str
    account_address: str | None = None


class HyperliquidExchangeClient(AbstractAsyncContextManager["HyperliquidExchangeClient"]):
    """Thin async wrapper around the Hyperliquid Python SDK."""

    def __init__(self, credentials: ExchangeCredentials, *, base_url: str, skip_ws: bool = True) -> None:
        self._credentials = credentials
        self._base_url = base_url
        self._loop = asyncio.get_event_loop()

        self._info = Info(base_url, skip_ws)
        wallet: LocalAccount = Account.from_key(credentials.private_key)
        self._account_address = credentials.account_address or wallet.address
        self._exchange = Exchange(
            wallet,
            base_url=base_url,
            account_address=self._account_address,
        )

    async def __aenter__(self) -> "HyperliquidExchangeClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # pragma: no cover - trivial
        await self.close()

    async def set_isolated_leverage(self, market: str, leverage: int) -> dict[str, Any]:
        """Update isolated leverage for a perpetual market."""

        METRICS.increment("hl.requests.leverage_attempt")
        logger.info(
            "hl_set_leverage_attempt",
            extra={"market": market, "leverage": leverage, "base_url": self._base_url},
        )
        asset = self._resolve_asset_name(market)
        try:
            response = await self._run_in_executor(
                self._exchange.update_leverage,
                leverage,
                asset,
                False,
            )
        except Exception as exc:  # pragma: no cover - network failures
            METRICS.increment("hl.requests.leverage_error")
            logger.exception(
                "hl_set_leverage_error",
                extra={"market": market, "leverage": leverage, "base_url": self._base_url},
            )
            raise

        if isinstance(response, dict) and response.get("status") != "ok":
            METRICS.increment("hl.requests.leverage_error")
            logger.error(
                "hl_set_leverage_rejected",
                extra={"market": market, "leverage": leverage, "response": response},
            )
            raise HyperliquidAPIError("update_leverage", response)

        METRICS.increment("hl.requests.leverage_success")
        logger.info(
            "hl_set_leverage_success",
            extra={"market": market, "leverage": leverage, "response": response},
        )
        return response

    async def place_market_order(self, market: str, usd_notional: float) -> dict[str, Any]:
        """Submit a market order sized by USD notional."""

        is_buy = usd_notional >= 0
        absolute_notional = abs(Decimal(str(usd_notional)))
        size, mark_px, computed_notional = await self._calculate_size(market, absolute_notional)

        asset = self._resolve_asset_name(market)

        payload = {
            "market": market,
            "is_buy": is_buy,
            "size": float(size),
            "usd_notional": float(absolute_notional),
            "asset": asset,
            "mark_px": float(mark_px),
            "computed_usd": float(computed_notional),
        }

        METRICS.increment("hl.requests.market_order_attempt")
        logger.info(
            "hl_market_order_attempt",
            extra={**payload, "base_url": self._base_url},
        )
        try:
            response = await self._run_in_executor(
                self._exchange.market_open,
                asset,
                is_buy,
                float(size),
                None,
                0.01,
            )
        except Exception as exc:  # pragma: no cover - network failures
            METRICS.increment("hl.requests.market_order_error")
            logger.exception("hl_market_order_error", extra={**payload, "base_url": self._base_url})
            raise

        order_errors = self._extract_order_errors(response)
        if order_errors:
            METRICS.increment("hl.requests.market_order_error")
            logger.error(
                "hl_market_order_rejected",
                extra={**payload, "errors": order_errors, "response": response},
            )
            raise HyperliquidAPIError("market_open", {"errors": order_errors, "response": response})

        METRICS.increment("hl.requests.market_order_success")
        logger.info("hl_market_order_success", extra={**payload, "response": response})
        return response

    async def cancel_open_orders(self, market: str) -> dict[str, Any]:
        METRICS.increment("hl.requests.cancel_attempt")
        logger.info("hl_cancel_orders_attempt", extra={"market": market})
        asset = self._resolve_asset_name(market)

        try:
            open_orders = await self._run_in_executor(self._info.open_orders, self._account_address)
        except Exception:  # pragma: no cover - network failures
            METRICS.increment("hl.requests.cancel_error")
            logger.exception("hl_cancel_orders_error", extra={"market": market, "stage": "open_orders"})
            raise

        cancel_requests: list[dict[str, Any]] = []
        if isinstance(open_orders, list):
            for order in open_orders:
                if not isinstance(order, dict):
                    continue
                if order.get("coin") != asset:
                    continue
                oid = order.get("oid")
                if oid is None:
                    continue
                cancel_requests.append({"coin": asset, "oid": int(oid)})

        if not cancel_requests:
            logger.info("hl_cancel_orders_skip", extra={"market": market, "reason": "no_open_orders"})
            return {"status": "ok", "response": {"type": "no_orders"}}

        try:
            response = await self._run_in_executor(self._exchange.bulk_cancel, cancel_requests)
        except Exception:  # pragma: no cover - network failures
            METRICS.increment("hl.requests.cancel_error")
            logger.exception("hl_cancel_orders_error", extra={"market": market})
            raise

        if isinstance(response, dict) and response.get("status") != "ok":
            METRICS.increment("hl.requests.cancel_error")
            logger.error("hl_cancel_orders_rejected", extra={"market": market, "response": response})
            raise HyperliquidAPIError("cancel", response)

        METRICS.increment("hl.requests.cancel_success")
        logger.info("hl_cancel_orders_success", extra={"market": market, "response": response})
        return response

    async def close_position(self, market: str) -> dict[str, Any]:
        asset = self._resolve_asset_name(market)

        METRICS.increment("hl.requests.close_attempt")
        logger.info("hl_close_position_attempt", extra={"market": market, "asset": asset})
        try:
            response = await self._run_in_executor(self._exchange.market_close, asset)
        except Exception:  # pragma: no cover - network failures
            METRICS.increment("hl.requests.close_error")
            logger.exception("hl_close_position_error", extra={"market": market})
            raise

        order_errors = self._extract_order_errors(response)
        if order_errors:
            METRICS.increment("hl.requests.close_error")
            logger.error(
                "hl_close_position_rejected",
                extra={"market": market, "errors": order_errors, "response": response},
            )
            raise HyperliquidAPIError("market_close", {"errors": order_errors, "response": response})

        METRICS.increment("hl.requests.close_success")
        logger.info("hl_close_position_success", extra={"market": market, "response": response})
        return response

    async def usd_send(self, destination: str, amount: float) -> dict[str, Any]:
        METRICS.increment("hl.requests.usd_send_attempt")
        logger.info(
            "hl_usd_send_attempt",
            extra={"destination": destination, "amount": amount},
        )
        try:
            response = await self._run_in_executor(
                self._exchange.usd_transfer,
                float(amount),
                destination,
            )
        except Exception:  # pragma: no cover - network failures
            METRICS.increment("hl.requests.usd_send_error")
            logger.exception(
                "hl_usd_send_error",
                extra={"destination": destination, "amount": amount},
            )
            raise

        if isinstance(response, dict) and response.get("status") != "ok":
            METRICS.increment("hl.requests.usd_send_error")
            logger.error(
                "hl_usd_send_rejected",
                extra={"destination": destination, "amount": amount, "response": response},
            )
            raise HyperliquidAPIError("usd_transfer", response)

        METRICS.increment("hl.requests.usd_send_success")
        logger.info(
            "hl_usd_send_success",
            extra={"destination": destination, "amount": amount, "response": response},
        )
        return response

    async def spot_send(self, coin: str, destination: str, amount: float) -> dict[str, Any]:
        METRICS.increment("hl.requests.spot_send_attempt")
        logger.info(
            "hl_spot_send_attempt",
            extra={"coin": coin, "destination": destination, "amount": amount},
        )
        try:
            response = await self._run_in_executor(
                self._exchange.spot_transfer,
                float(amount),
                destination,
                coin,
            )
        except Exception:  # pragma: no cover - network failures
            METRICS.increment("hl.requests.spot_send_error")
            logger.exception(
                "hl_spot_send_error",
                extra={"coin": coin, "destination": destination, "amount": amount},
            )
            raise

        if isinstance(response, dict) and response.get("status") != "ok":
            METRICS.increment("hl.requests.spot_send_error")
            logger.error(
                "hl_spot_send_rejected",
                extra={"coin": coin, "destination": destination, "amount": amount, "response": response},
            )
            raise HyperliquidAPIError("spot_transfer", response)

        METRICS.increment("hl.requests.spot_send_success")
        logger.info(
            "hl_spot_send_success",
            extra={"coin": coin, "destination": destination, "amount": amount, "response": response},
        )
        return response

    async def close(self) -> None:
        if getattr(self._info, "ws_manager", None) is not None:
            self._info.disconnect_websocket()

    async def get_perp_position(self, market: str) -> dict[str, Decimal]:
        asset = self._resolve_asset_name(market)

        state = await self._run_in_executor(self._info.user_state, self._account_address)
        asset_positions = state.get("assetPositions", []) if isinstance(state, dict) else []

        mark_price = await self._get_mark_price(asset, default=Decimal("0"))

        for entry in asset_positions:
            position = entry.get("position") if isinstance(entry, dict) else None
            if not position or position.get("coin") != asset:
                continue

            size_str = position.get("szi")
            size = Decimal(str(size_str)) if size_str is not None else Decimal("0")
            if size == 0:
                break

            entry_px = position.get("entryPx")
            entry_price = Decimal(str(entry_px)) if entry_px is not None else Decimal("0")

            realized = position.get("realizedPnl")
            unrealized = position.get("unrealizedPnl")

            realized_pnl = Decimal(str(realized)) if realized is not None else Decimal("0")
            unrealized_pnl = Decimal(str(unrealized)) if unrealized is not None else Decimal("0")

            position_notional = abs(size) * mark_price

            return {
                "position_notional": position_notional,
                "entry_price": entry_price,
                "mark_price": mark_price,
                "realized_pnl": realized_pnl,
                "unrealized_pnl": unrealized_pnl,
            }

        return {
            "position_notional": Decimal("0"),
            "entry_price": Decimal("0"),
            "mark_price": mark_price,
            "realized_pnl": Decimal("0"),
            "unrealized_pnl": Decimal("0"),
        }

    async def _calculate_size(self, market: str, usd_notional: Decimal) -> Decimal:
        asset_name = self._resolve_asset_name(market)
        mark_px = await self._get_mark_price(asset_name)
        asset_id = self._info.name_to_asset(asset_name)
        decimals = self._info.asset_to_sz_decimals.get(asset_id, 8)
        quantizer = Decimal(1).scaleb(-decimals)

        min_notional = self._resolve_min_notional(asset_id)
        if usd_notional < min_notional:
            raise HyperliquidAPIError(
                "market_open",
                {
                    "errors": [
                        f"Order notional ${usd_notional} does not meet minimum ${min_notional}",
                    ]
                },
            )

        raw_size = usd_notional / mark_px
        size = raw_size.quantize(quantizer, rounding=ROUND_UP)
        if size <= 0:
            raise ValueError("Order size rounded to zero; increase usd_notional")

        computed_notional = size * mark_px
        return size, mark_px, computed_notional

    async def _run_in_executor(self, func, *args, **kwargs):
        return await self._loop.run_in_executor(None, lambda: func(*args, **kwargs))

    def _resolve_asset_name(self, market: str) -> str:
        if market.endswith("-PERP"):
            return market[:-5]
        if "-" in market:
            return market.split("-", 1)[0]
        return market

    @staticmethod
    def _extract_order_errors(response: Any) -> list[str]:
        if not isinstance(response, dict):
            return []

        status = response.get("status")
        if status != "ok":
            return [str(response)]

        payload = response.get("response")
        if not isinstance(payload, dict):
            return []

        data = payload.get("data")
        if not isinstance(data, dict):
            return []

        statuses = data.get("statuses")
        if not isinstance(statuses, list):
            return []

        errors: list[str] = []
        for item in statuses:
            if isinstance(item, dict):
                error = item.get("error")
                if error:
                    errors.append(str(error))
        return errors

    @staticmethod
    def _resolve_min_notional(asset_id: int) -> Decimal:
        # Conservative default matches public docs ($10)
        return Decimal("10")

    async def _get_mark_price(self, asset: str, *, default: Decimal | None = None) -> Decimal:
        all_mids = await self._run_in_executor(self._info.all_mids)
        value = all_mids.get(asset) if isinstance(all_mids, dict) else None
        if value is None:
            if default is not None:
                return default
            raise ValueError(f"Unable to fetch mark price for asset {asset}")
        return Decimal(str(value))


class HyperliquidAPIError(RuntimeError):
    """Represents a non-success response from the Hyperliquid API."""

    def __init__(self, action: str, response: dict[str, Any] | Any) -> None:
        self.action = action
        self.response = response
        message = f"Hyperliquid {action} failed: {response}"
        super().__init__(message)
