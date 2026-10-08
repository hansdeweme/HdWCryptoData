# Copyright (c) 2024-2026 Hans De Weme
# Licensed under the MIT License (https://opensource.org/licenses/MIT).
# Public exports for an importable CryptoData package.
# ``hdw_crypto_data`` package directory.

from .binance_rest_client import BinanceRestClient
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
)
from .total_dataset_builder import (
    MakeTotalError,
    MakeTotalResult,
    TotalDatasetBuilder,
)
from .total_dataset_loader import TotalDatasetLoader
from .version import VERSION as __version__



__all__ = [
    "__version__",
    "BinanceRestClient",
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
]
