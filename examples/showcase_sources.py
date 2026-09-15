"""Application-owned market data contract and optional source adapters."""
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
import pandas as pd


SOURCE_PACKAGES = ('hdw_stock_data', 'hdw_crypto_data')


def source_availability():
    """Check discoverability without importing optional acquisition pipelines.

    A package available from a source checkout also counts as present.
    Runtime dependency/provider errors are still reported by the worker.
    """
    return tuple(find_spec(package) is not None for package in SOURCE_PACKAGES)


@dataclass(frozen=True)
class MarketDataset:
    dataframe: pd.DataFrame
    symbol: str
    interval: str
    provider: str
    timezone: str
    actual_start: pd.Timestamp
    actual_end: pd.Timestamp
    warnings: tuple[str, ...] = ()

    def __post_init__(self):
        # A frozen dataclass does not freeze its DataFrame. Own a defensive copy.
        df = self.dataframe.copy(deep=True)
        required = ('open', 'high', 'low', 'close', 'volume')
        if df.empty or not set(required).issubset(df.columns):
            raise ValueError('A nonempty OHLCV DataFrame is required.')
        if not isinstance(df.index, pd.DatetimeIndex) or df.index.tz is None:
            raise ValueError('A timezone-aware DatetimeIndex is required.')
        if df.index.hasnans or not df.index.is_unique or not df.index.is_monotonic_increasing:
            raise ValueError('Timestamps must be valid, unique, and sorted.')
        if not df.columns.is_unique:
            raise ValueError('Columns must be unique.')
        for column in required:
            if not pd.api.types.is_numeric_dtype(df[column]):
                raise ValueError(f'{column} must be numeric.')
        if str(df.index.tz) != self.timezone or self.actual_start != df.index.min() or self.actual_end != df.index.max():
            raise ValueError('Dataset metadata must match its DataFrame.')
        if 'number_of_trades' not in df:
            df['number_of_trades'] = pd.Series(pd.NA, index=df.index, dtype='Float64')
        elif not pd.api.types.is_numeric_dtype(df.number_of_trades):
            df['number_of_trades'] = pd.to_numeric(df.number_of_trades, errors='raise').astype('Float64')
        object.__setattr__(self, 'dataframe', df)
        object.__setattr__(self, 'warnings', tuple(self.warnings))

    @classmethod
    def from_dataframe(cls, dataframe, *, symbol, interval='1h', provider='Existing', warnings=()):
        if not isinstance(dataframe.index, pd.DatetimeIndex):
            raise ValueError('A timezone-aware DatetimeIndex is required.')
        return cls(dataframe, symbol, interval, provider, str(dataframe.index.tz),
                   dataframe.index.min(), dataframe.index.max(), tuple(warnings))


@dataclass(frozen=True)
class StockRequest:
    symbol: str
    interval: str = '1h'
    days: int = 730
    timezone: str = 'UTC'


@dataclass(frozen=True)
class CryptoRequest:
    symbol: str
    settings: dict
    interval: str = '1h'
    timezone: str = 'UTC'
    download_archives: bool = True
    force_merge: bool = False


class StockSourceAdapter:
    def __init__(self, loader=None):
        self.loader = loader

    def load(self, request: StockRequest) -> MarketDataset:
        if self.loader is None:
            from hdw_stock_data import YahooStockLoader
            self.loader = YahooStockLoader()
        result = self.loader.load(request.symbol, interval=request.interval,
                                  days=request.days, preferred_tz=request.timezone)
        d = result.descriptor
        return MarketDataset(result.dataframe, d.symbol, d.interval, 'Yahoo',
                             d.timezone, d.actual_start, d.actual_end, result.warnings)


class CryptoSourceAdapter:
    def load(self, request: CryptoRequest) -> MarketDataset:
        # Optional dependency: importing the showcase does not import crypto code.
        from hdw_crypto_data.binance_vision_dumper import BinanceVisionDumper
        from hdw_crypto_data.total_dataset_builder import TotalDatasetBuilder
        from hdw_crypto_data.total_dataset_loader import TotalDatasetLoader
        from hdw_crypto_data.symbols import normalize_symbol
        if request.interval != '1h':
            raise ValueError('The crypto showcase pipeline supports 1h only.')
        quote = request.settings.get('quote_currency', 'USDT').upper()
        symbol = normalize_symbol(request.symbol.strip().upper().removesuffix(quote))
        settings = dict(request.settings)
        settings['preferred_time_zone'] = request.timezone
        settings.setdefault('spot', str(Path(__file__).resolve().parent / 'spot'))
        settings['full_spot'] = str(Path(settings['spot']).resolve())
        settings['quote_currency'] = quote
        if request.download_archives:
            dumper = BinanceVisionDumper(
                path_dir_where_to_dump=str(Path(settings['spot']).resolve()),
                asset_class='spot', data_type='klines', data_frequency=request.interval)
            dumper.dump_data(tickers=[f'{symbol}{quote}'], date_start=None, date_end=None,
                            is_to_update_existing=True, tickers_to_exclude=['UST'])
            dumper.delete_outdated_daily_results()
        result = TotalDatasetBuilder(symbol, settings, force_merge=request.force_merge).build()
        df = TotalDatasetLoader(symbol, settings).load_total_dataframe(
            file_path=result.filepath, mode='ta', preferred_tz=request.timezone)
        return MarketDataset.from_dataframe(df, symbol=f'{symbol}{quote}',
                                            interval=request.interval, provider='Binance',
                                            warnings=getattr(result, 'warnings', ()))


def load_csv(path, *, symbol, interval='1h', timezone='UTC'):
    """Import canonical CSV; timestamps must contain explicit UTC offsets."""
    df = pd.read_csv(path)
    if 'dt' not in df:
        raise ValueError('CSV must contain a dt column with timezone-aware timestamps.')
    timestamps = [pd.Timestamp(value) for value in df.pop('dt')]
    if any(pd.isna(t) or t.tzinfo is None for t in timestamps):
        raise ValueError('CSV timestamps must include a timezone offset.')
    df.index = pd.to_datetime(timestamps, utc=True).tz_convert(timezone)
    df.index.name = 'dt'
    return MarketDataset.from_dataframe(df, symbol=symbol, interval=interval, provider='CSV')


class AcquisitionController:
    def load(self, request):
        if isinstance(request, StockRequest):
            return StockSourceAdapter().load(request)
        if isinstance(request, CryptoRequest):
            return CryptoSourceAdapter().load(request)
        raise TypeError('Unknown acquisition request.')
