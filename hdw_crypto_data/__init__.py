# Copyright (c) 2024-2026 Hans De Weme
# Licensed under the MIT License (https://opensource.org/licenses/MIT).
# Public exports for an importable CryptoData package.
# ``hdw_crypto_data`` package directory.

from .binance_rest_client import BinanceRestClient
from .binance_kline_client import (
    BinanceKlineClient,
    BinanceKlineError,
    BinanceKlineResponseError,
    BinanceRateLimitError,
)
from .binance_vision_dumper import (
    ArchiveDownloadResult,
    ArchiveDownloadStatus,
    BinanceVisionDumpError,
    BinanceVisionDumper,
)
from .symbols import (
    SYMBOL_PATTERN,
    normalize_market_symbol,
    normalize_symbol,
    MarketIdentity,
    resolve_market,
)
from .total_dataset_builder import (
    MakeTotalError,
    MakeTotalResult,
    TotalDatasetBuilder,
)
from .total_dataset_loader import TotalDatasetLoader
from .coinbase_client import CoinbaseClient, CoinbaseError, CoinbaseResponseError
from .coinbase_archive_provisioner import CoinbaseArchiveProvisioner, inspect_partition
from .version import VERSION as __version__



__all__ = [
    "CoinbaseClient",
    "CoinbaseError",
    "CoinbaseResponseError",
    "CoinbaseArchiveProvisioner",
    "inspect_partition",
    "__version__",
    "BinanceRestClient",
    "BinanceKlineClient",
    "BinanceKlineError",
    "BinanceKlineResponseError",
    "BinanceRateLimitError",
    "BinanceVisionDumper",
    "BinanceVisionDumpError",
    "ArchiveDownloadResult",
    "ArchiveDownloadStatus",
    "MakeTotalError",
    "MakeTotalResult",
    "TotalDatasetBuilder",
    "TotalDatasetLoader",
    "SYMBOL_PATTERN",
    "normalize_market_symbol",
    "normalize_symbol",
    "MarketIdentity",
    "resolve_market",
]
