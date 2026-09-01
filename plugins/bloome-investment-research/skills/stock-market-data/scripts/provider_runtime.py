"""Explicit market-data provider routing.

Only providers named in ``provider_config.json`` may be used. A one-item chain
never falls back. A multi-item chain falls back in the listed order and records
every attempt. This module intentionally contains the only yfinance import used
by the skill's scripts.
"""

from __future__ import annotations

import importlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


CONFIG_ENV = "STOCK_DATA_PROVIDER_CONFIG"
DEFAULT_CONFIG = Path(__file__).with_name("provider_config.json")


class ProviderError(RuntimeError):
    """Base provider routing error."""


class ProviderUnavailable(ProviderError):
    """Provider could not serve the request and an explicit fallback may run."""


class ProviderConfigurationError(ProviderError):
    """Provider policy is missing or invalid."""


@dataclass(frozen=True)
class RoutedResult:
    value: Any
    provider: str
    attempts: tuple[dict[str, str], ...]
    fallback_used: bool


def load_provider_config(path: str | Path | None = None) -> dict:
    config_path = Path(path or os.environ.get(CONFIG_ENV) or DEFAULT_CONFIG)
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderConfigurationError(f"Cannot load provider config {config_path}: {exc}") from exc
    if config.get("fallback_policy") != "explicit_only":
        raise ProviderConfigurationError("fallback_policy must be explicit_only")
    if not isinstance(config.get("datasets"), dict):
        raise ProviderConfigurationError("provider config requires a datasets object")
    modes = config.get("dataset_modes")
    if not isinstance(modes, dict):
        raise ProviderConfigurationError("provider config requires a dataset_modes object")
    missing_modes = set(config["datasets"]) - set(modes)
    if missing_modes:
        raise ProviderConfigurationError(f"Missing provider mode for: {sorted(missing_modes)}")
    invalid_modes = {name: mode for name, mode in modes.items() if mode not in {"fallback", "union"}}
    if invalid_modes:
        raise ProviderConfigurationError(f"Invalid provider modes: {invalid_modes}")
    market_chains = config.get("dataset_market_chains", {})
    if not isinstance(market_chains, dict):
        raise ProviderConfigurationError("dataset_market_chains must be an object")
    for dataset, markets in market_chains.items():
        if dataset not in config["datasets"] or not isinstance(markets, dict):
            raise ProviderConfigurationError(f"Invalid market-chain dataset: {dataset}")
        for market, chain in markets.items():
            if (
                not isinstance(market, str)
                or not isinstance(chain, list)
                or not chain
                or not all(isinstance(provider, str) and provider for provider in chain)
                or len(chain) != len(set(chain))
            ):
                raise ProviderConfigurationError(
                    f"Invalid provider chain for dataset={dataset}, market={market}"
                )
    return config


def configured_chain(
    dataset: str,
    *,
    market: str | None = None,
    config: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    policy = dict(config or load_provider_config())
    market_chains = policy.get("dataset_market_chains", {}).get(dataset, {})
    chain = market_chains.get(market) if market else None
    if chain is None:
        chain = policy.get("datasets", {}).get(dataset)
    if not isinstance(chain, list) or not chain or not all(isinstance(v, str) and v for v in chain):
        raise ProviderConfigurationError(f"No explicit provider chain configured for dataset '{dataset}'")
    if len(chain) != len(set(chain)):
        raise ProviderConfigurationError(f"Duplicate provider in chain for dataset '{dataset}'")
    return tuple(chain)


def provider_mode(dataset: str, *, config: Mapping[str, Any] | None = None) -> str:
    policy = dict(config or load_provider_config())
    mode = policy.get("dataset_modes", {}).get(dataset)
    if mode not in {"fallback", "union"}:
        raise ProviderConfigurationError(f"No valid provider mode configured for dataset '{dataset}'")
    return mode


def provider_market_metadata(
    provider: str,
    dataset: str,
    market: str,
    *,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return declared provider metadata for a specific dataset and market."""
    policy = dict(config or load_provider_config())
    metadata = (
        policy.get("provider_market_metadata", {})
        .get(provider, {})
        .get(dataset, {})
        .get(market, {})
    )
    if not isinstance(metadata, dict):
        raise ProviderConfigurationError(
            f"Invalid provider metadata for provider={provider}, dataset={dataset}, market={market}"
        )
    return dict(metadata)


def route(
    dataset: str,
    operation: Callable[[str], Any],
    *,
    market: str | None = None,
    config: Mapping[str, Any] | None = None,
) -> RoutedResult:
    """Run an operation against exactly the configured provider chain."""
    if provider_mode(dataset, config=config) != "fallback":
        raise ProviderConfigurationError(
            f"route() only supports fallback datasets; '{dataset}' is configured as union"
        )
    chain = configured_chain(dataset, market=market, config=config)
    attempts: list[dict[str, str]] = []
    for index, provider in enumerate(chain):
        try:
            value = operation(provider)
            if value is None:
                raise ProviderUnavailable("provider returned no data")
            attempts.append({"provider": provider, "status": "success"})
            return RoutedResult(value, provider, tuple(attempts), index > 0)
        except ProviderUnavailable as exc:
            attempts.append({"provider": provider, "status": "unavailable", "reason": str(exc)})
    detail = "; ".join(f"{a['provider']}: {a.get('reason', a['status'])}" for a in attempts)
    raise ProviderUnavailable(f"All explicitly configured providers failed for {dataset}: {detail}")


def require_provider_module(dataset: str, provider: str):
    """Return a provider SDK only when it is explicitly allowed for a dataset."""
    if provider not in configured_chain(dataset):
        raise ProviderConfigurationError(
            f"Provider '{provider}' is not configured for dataset '{dataset}'"
        )
    module_names = {"yfinance": "yfinance"}
    module_name = module_names.get(provider)
    if not module_name:
        raise ProviderConfigurationError(f"No SDK module registered for provider '{provider}'")
    return importlib.import_module(module_name)


def yfinance_ticker(dataset: str, symbol: str):
    """Compatibility adapter for scripts not yet expressed as provider operations."""
    yf = require_provider_module(dataset, "yfinance")
    return yf.Ticker(symbol)


def yfinance_download(dataset: str, *args, **kwargs):
    yf = require_provider_module(dataset, "yfinance")
    return yf.download(*args, **kwargs)
