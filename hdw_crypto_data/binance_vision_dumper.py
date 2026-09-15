# binance_vision_dumper.py
# Copyright (c) 2025, 2026 Hans De Weme
# Licensed under the MIT License (https://opensource.org/licenses/MIT).
# part of the HdW_crypto_data Project
# Purpose: download historical kline data from Binance Vision with multiprocessing
# Part of the Code is based on Binance_Historical_Data by Stas Prokopiev (github: tas-prokopiev)
#  
import os
import shutil
import zipfile
import datetime
from pathlib                import Path
from dataclasses            import dataclass
from enum                   import Enum
from collections            import defaultdict
from concurrent.futures     import ThreadPoolExecutor
from dateutil.relativedelta import relativedelta
from tqdm.auto              import tqdm
import requests
# local imports
try:
    from .symbols import normalize_symbol
    from .binance_vision_client import (
        BinanceChecksumMismatchError,
        BinanceChecksumNotFoundError,
        create_binance_session,
        download_file,
        verify_file_checksum,
    )
except ImportError:
    from symbols import normalize_symbol
    from binance_vision_client import (
        BinanceChecksumMismatchError,
        BinanceChecksumNotFoundError,
        create_binance_session,
        download_file,
        verify_file_checksum,
    )
try:
    from mpire import WorkerPool
    HAS_MPIRE = True
except ImportError:
    HAS_MPIRE = False

BASE_URL = "https://data.binance.vision/data"

class ArchiveDownloadStatus(Enum):
    ALREADY_PRESENT = "already_present"
    DOWNLOADED = "downloaded"
    NOT_FOUND = "not_found"
    NETWORK_FAILURE = "network_failure"
    HTTP_FAILURE = "http_failure"
    RATE_LIMITED = "rate_limited"
    INVALID_ARCHIVE = "invalid_archive"
    EXTRACTION_FAILURE = "extraction_failure"
    CHECKSUM_NOT_FOUND = "checksum_not_found"
    CHECKSUM_MISMATCH = "checksum_mismatch"

@dataclass(frozen=True)
class ArchiveDownloadResult:
    date: datetime.date
    status: ArchiveDownloadStatus
    error: str | None = None


class BinanceVisionDumpError(RuntimeError):
    """Raised when Binance Vision archive dumping has operational failures."""

    def __init__(self, failures: list[ArchiveDownloadResult]) -> None:
        self.failures = failures
        summary = self._summarize_failures(failures)
        super().__init__(f"Binance Vision dump failed for {len(failures)} file(s): {summary}")

    @staticmethod
    def _summarize_failures(failures: list[ArchiveDownloadResult]) -> str:
        counts = defaultdict(int)
        for failure in failures:
            counts[failure.status.value] += 1
        return ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))


def _download_and_extract_task(
    base_url: str,
    path_dir_where_to_dump: str,
    asset_class: str,
    data_type: str,
    data_frequency: str,
    ticker: str,
    date_obj: datetime.date,
    timeperiod_per_file: str,
) -> ArchiveDownloadResult:
    """Download a single zip archive from Binance Vision, extract CSV, and remove zip."""
    if timeperiod_per_file == "monthly":
        str_date = date_obj.strftime("%Y-%m")
    else:
        str_date = date_obj.strftime("%Y-%m-%d")

    file_base = f"{ticker}-{data_frequency}-{str_date}"
    file_zip = f"{file_base}.zip"
    file_csv = f"{file_base}.csv"

    # Avoid duplicate 'spot/spot' if path_dir_where_to_dump already ends with asset_class
    if os.path.basename(path_dir_where_to_dump).lower() == asset_class.lower():
        folder_suffix = os.path.join(timeperiod_per_file, data_type, ticker, data_frequency)
    else:
        folder_suffix = os.path.join(asset_class, timeperiod_per_file, data_type, ticker, data_frequency)

    save_dir = os.path.join(path_dir_where_to_dump, folder_suffix)
    local_zip_path = Path(save_dir, file_zip)
    local_csv_path = Path(save_dir, file_csv)

    if os.path.exists(local_csv_path):
        return ArchiveDownloadResult(date_obj, ArchiveDownloadStatus.ALREADY_PRESENT)

    url_suffix = f"{asset_class}/{timeperiod_per_file}/{data_type}/{ticker}/{data_frequency}/{file_zip}"
    remote_url = f"{base_url}/{url_suffix}"

    session = create_binance_session()
    try:
        try:
            download_file(session, remote_url, local_zip_path)
            verify_file_checksum(session, remote_url, local_zip_path)
        except requests.HTTPError as ex:
            status_code = ex.response.status_code if ex.response is not None else None
            if status_code == 404:
                return ArchiveDownloadResult(date_obj, ArchiveDownloadStatus.NOT_FOUND)
            if status_code == 429:
                return ArchiveDownloadResult(date_obj, ArchiveDownloadStatus.RATE_LIMITED, str(ex))
            return ArchiveDownloadResult(date_obj, ArchiveDownloadStatus.HTTP_FAILURE, str(ex))
        except BinanceChecksumNotFoundError as ex:
            return ArchiveDownloadResult(date_obj, ArchiveDownloadStatus.CHECKSUM_NOT_FOUND, str(ex))
        except BinanceChecksumMismatchError as ex:
            return ArchiveDownloadResult(date_obj, ArchiveDownloadStatus.CHECKSUM_MISMATCH, str(ex))
        except requests.RequestException as ex:
            return ArchiveDownloadResult(date_obj, ArchiveDownloadStatus.NETWORK_FAILURE, str(ex))
        except Exception as ex:
            return ArchiveDownloadResult(date_obj, ArchiveDownloadStatus.NETWORK_FAILURE, str(ex))

        # Extract CSV from ZIP
        try:
            with zipfile.ZipFile(local_zip_path, "r") as zip_ref:
                members = zip_ref.namelist()
                if file_csv not in members:
                    return ArchiveDownloadResult(
                        date_obj,
                        ArchiveDownloadStatus.INVALID_ARCHIVE,
                        f"Expected {file_csv!r}; archive contains {members!r}",
                    )

                member = zip_ref.getinfo(file_csv)
                destination = Path(save_dir, file_csv)
                temporary = destination.with_suffix(destination.suffix + ".part")
                try:
                    with zip_ref.open(member) as source, temporary.open("wb") as target:
                        shutil.copyfileobj(source, target)
                    temporary.replace(destination)
                finally:
                    temporary.unlink(missing_ok=True)
        except zipfile.BadZipFile as ex:
            return ArchiveDownloadResult(date_obj, ArchiveDownloadStatus.INVALID_ARCHIVE, str(ex))
        except Exception as ex:
            return ArchiveDownloadResult(date_obj, ArchiveDownloadStatus.EXTRACTION_FAILURE, str(ex))
    finally:
        local_zip_path.unlink(missing_ok=True)
    return ArchiveDownloadResult(date_obj, ArchiveDownloadStatus.DOWNLOADED)

class BinanceVisionDumper:
    """Download and verify historical market-data archives from Binance Vision."""
    def __init__(
        self,
        path_dir_where_to_dump: str,
        asset_class: str = "spot",
        data_type: str = "klines",
        data_frequency: str = "1h",
    ) -> None:
        self.path_dir_where_to_dump = os.path.abspath(path_dir_where_to_dump)
        self._asset_class = asset_class
        self._data_type = data_type
        self._data_frequency = data_frequency
        self._base_url = BASE_URL
        self.dict_new_archives_saved_by_ticker = defaultdict(dict)
        self.download_failures = []

    def dump_data(
        self,
        tickers: list[str] | str | None = None,
        date_start: datetime.date | None = None,
        date_end: datetime.date | None = None,
        is_to_update_existing: bool = True,
        int_max_tickers_to_get: int | None = None,
        tickers_to_exclude: list[str] | None = None,
    ) -> None:
        """Main method to dump new or update existing historical klines data."""
        self.dict_new_archives_saved_by_ticker.clear()
        self.download_failures = []
        if isinstance(tickers, str):
            list_trading_pairs = [normalize_symbol(tickers)]
        elif isinstance(tickers, (list, tuple)):
            list_trading_pairs = [normalize_symbol(ticker) for ticker in tickers]
        else:
            list_trading_pairs = []
        if tickers_to_exclude:
            excluded_tickers = {normalize_symbol(ticker) for ticker in tickers_to_exclude}
            list_trading_pairs = [t for t in list_trading_pairs if t not in excluded_tickers]
        if int_max_tickers_to_get:
            list_trading_pairs = list_trading_pairs[:int_max_tickers_to_get]

        print(f"[Info] Download data for {len(list_trading_pairs)} ticker(s): {list_trading_pairs}")
        print(f"[Info] ---> Target directory: {self.path_dir_where_to_dump}")
        print(f"[Info] ---> Data Frequency: {self._data_frequency}")

        # Date bounds
        if date_start is None or date_start < datetime.date(2017, 1, 1):
            date_start = datetime.date(2017, 1, 1)
        yesterday = datetime.datetime.utcnow().date() - relativedelta(days=1)
        if date_end is None or date_end > yesterday:
            date_end = yesterday
        print(f"[Info] ---> Start Date: {date_start.strftime("%Y%m%d")}")
        print(f"[Info] ---> End Date: {date_end.strftime("%Y%m%d")}")
        if date_start > date_end:
            print("[Info] ---> No dates to download in the requested range")
            return
        date_end_first_day_of_month = datetime.date(year=date_end.year, month=date_end.month, day=1)
        for ticker in tqdm(list_trading_pairs, leave=True, desc="Tickers"):
            # 1) Download monthly data (past months)
            if date_end_first_day_of_month - relativedelta(days=1) >= date_start:
                self._download_data_for_1_ticker(
                    ticker=ticker,
                    date_start=date_start,
                    date_end=(date_end_first_day_of_month - relativedelta(days=1)),
                    timeperiod_per_file="monthly",
                    is_to_update_existing=is_to_update_existing,
                )
            # 2) Download daily data (current month up to yesterday)
            if date_end >= date_end_first_day_of_month:
                self._download_data_for_1_ticker(
                    ticker=ticker,
                    date_start=max(date_start, date_end_first_day_of_month),
                    date_end=date_end,
                    timeperiod_per_file="daily",
                    is_to_update_existing=is_to_update_existing,
                )
        self._print_dump_statistics()
        if self.download_failures:
            raise BinanceVisionDumpError(self.download_failures)

    def _download_data_for_1_ticker(
        self,
        ticker: str,
        date_start: datetime.date,
        date_end: datetime.date,
        timeperiod_per_file: str = "monthly",
        is_to_update_existing: bool = False,
    ) -> None:
        list_dates = self._create_list_dates_for_timeperiod(
            date_start=date_start,
            date_end=date_end,
            timeperiod_per_file=timeperiod_per_file,
        )
        dir_where_to_save = self.get_local_dir_to_data(ticker, timeperiod_per_file)
        os.makedirs(dir_where_to_save, exist_ok=True)
        if is_to_update_existing:
            list_dates_with_data = self.get_all_dates_with_data_for_ticker(ticker, timeperiod_per_file)
            if list_dates_with_data:
                first_saved_date = min(list_dates_with_data)
                list_dates_cleared = [
                    d for d in list_dates
                    if d >= first_saved_date and d not in list_dates_with_data
                ]
            else:
                list_dates_cleared = list_dates
        else:
            list_dates_cleared = list_dates
        if not list_dates_cleared:
            return
        list_args = [
            (
                self._base_url,
                self.path_dir_where_to_dump,
                self._asset_class,
                self._data_type,
                self._data_frequency,
                ticker,
                date_obj,
                timeperiod_per_file,
            )
            for date_obj in list_dates_cleared
        ]
        processes = min(len(list_args), 10)
        list_results = None
        if HAS_MPIRE:
            try:
                with WorkerPool(n_jobs=processes) as pool:
                    list_results = list(tqdm(
                        pool.imap_unordered(_download_and_extract_task, list_args,),
                        leave=False,
                        total=len(list_args),
                        desc=f"{ticker} {timeperiod_per_file} files",
                        unit="files",
                    ))
            except Exception as ex:
                print(f"[Warning] mpire unavailable for this run; falling back to threads: {ex}")

        if list_results is None:
            with ThreadPoolExecutor(max_workers=processes) as pool:
                list_results = list(tqdm(
                    pool.map(lambda args: _download_and_extract_task(*args), list_args),
                    leave=False,
                    total=len(list_args),
                    desc=f"{ticker} {timeperiod_per_file} files",
                    unit="files",
                ))
        downloaded_count = sum(1 for result in list_results if result.status == ArchiveDownloadStatus.DOWNLOADED)
        already_present_count = sum(1 for result in list_results if result.status == ArchiveDownloadStatus.ALREADY_PRESENT)
        not_found_count = sum(1 for result in list_results if result.status == ArchiveDownloadStatus.NOT_FOUND)
        failure_results = [
            result for result in list_results
            if result.status in {
                ArchiveDownloadStatus.NETWORK_FAILURE,
                ArchiveDownloadStatus.HTTP_FAILURE,
                ArchiveDownloadStatus.RATE_LIMITED,
                ArchiveDownloadStatus.INVALID_ARCHIVE,
                ArchiveDownloadStatus.EXTRACTION_FAILURE,
                ArchiveDownloadStatus.CHECKSUM_NOT_FOUND,
                ArchiveDownloadStatus.CHECKSUM_MISMATCH,
            }
        ]
        self.download_failures.extend(failure_results)

        print(
            f"[Info] ---> {ticker} {timeperiod_per_file}: "
            f"{downloaded_count} downloaded, "
            f"{already_present_count} already present, "
            f"{not_found_count} not found, "
            f"{len(failure_results)} failed"
        )
        for failure in failure_results[:5]:
            print(f"[Warning] ---> {ticker} {timeperiod_per_file} {failure.date}: {failure.status.value}: {failure.error}")
        if len(failure_results) > 5:
            print(f"[Warning] ---> {ticker} {timeperiod_per_file}: {len(failure_results) - 5} additional failure(s) omitted")

        self.dict_new_archives_saved_by_ticker[ticker][timeperiod_per_file] = downloaded_count

    def get_local_dir_to_data(self, ticker: str, timeperiod_per_file: str) -> str:
        """Path to directory where ticker data is saved."""
        base = self.path_dir_where_to_dump
        if os.path.basename(base).lower() == self._asset_class.lower():
            return os.path.join(base, timeperiod_per_file, self._data_type, ticker, self._data_frequency)
        return os.path.join(base, self._asset_class, timeperiod_per_file, self._data_type, ticker, self._data_frequency)

    def create_filename(
        self,
        ticker: str,
        date_obj: datetime.date,
        timeperiod_per_file: str = "monthly",
        extension: str = "csv",
    ) -> str:
        """Create file name matching the Binance convention."""
        if timeperiod_per_file == "monthly":
            str_date = date_obj.strftime("%Y-%m")
        else:
            str_date = date_obj.strftime("%Y-%m-%d")
        return f"{ticker}-{self._data_frequency}-{str_date}.{extension}"

    def get_all_dates_with_data_for_ticker(self, ticker: str, timeperiod_per_file: str = "monthly",) -> list[datetime.date]:
        """Get list with all dates for which there is saved data on disk."""
        date_start = datetime.date(year=2017, month=1, day=1)
        date_end = datetime.datetime.utcnow().date()
        list_dates = self._create_list_dates_for_timeperiod(
            date_start=date_start,
            date_end=date_end,
            timeperiod_per_file=timeperiod_per_file,
        )
        str_dir_where_to_save = self.get_local_dir_to_data(ticker, timeperiod_per_file)
        list_dates_with_data = []
        if not os.path.exists(str_dir_where_to_save):
            return []
        for date_obj in list_dates:
            file_name = self.create_filename(
                ticker,
                date_obj,
                timeperiod_per_file=timeperiod_per_file,
                extension="csv",
            )
            path_where_to_save = os.path.join(str_dir_where_to_save, file_name)
            if os.path.exists(path_where_to_save):
                list_dates_with_data.append(date_obj)
        return list_dates_with_data

    def get_all_tickers_with_data(self, timeperiod_per_file: str = "daily") -> list[str]:
        """Get all tickers for which data was dumped in the target folder."""
        base = self.path_dir_where_to_dump
        if os.path.basename(base).lower() == self._asset_class.lower():
            folder_path = os.path.join(base, timeperiod_per_file, self._data_type)
        else:
            folder_path = os.path.join(base, self._asset_class, timeperiod_per_file, self._data_type)
        if not os.path.exists(folder_path):
            return []
        return [
            d for d in os.listdir(folder_path)
            if os.path.isdir(os.path.join(folder_path, d))
        ]

    def delete_outdated_daily_results(self) -> None:
        """Delete daily CSV files for which full month monthly data is already present."""
        print(f"[Info] ----> Delete old daily data for which there is monthly data")
        deleted_count = 0
        tickers = self.get_all_tickers_with_data(timeperiod_per_file="daily")
        for ticker in tickers:
            list_saved_months_dates = self.get_all_dates_with_data_for_ticker(ticker, timeperiod_per_file="monthly")
            list_saved_days_dates = self.get_all_dates_with_data_for_ticker(ticker, timeperiod_per_file="daily")
            str_folder = self.get_local_dir_to_data(ticker, timeperiod_per_file="daily")
            for date_saved_day in list_saved_days_dates:
                date_saved_day_month = date_saved_day.replace(day=1)
                if date_saved_day_month not in list_saved_months_dates:
                    continue
                str_filename = self.create_filename(ticker, date_saved_day, timeperiod_per_file="daily", extension="csv",)
                file_path = os.path.join(str_folder, str_filename)
                try:
                    os.remove(file_path)
                    deleted_count += 1
                except Exception as ex:
                    print(f"[Warning] Unable to delete file {file_path}: {ex}")
        print(f"[Info] ---> Done. Outdated daily files deleted ({deleted_count} total)")

    def _print_dump_statistics(self) -> None:
        """Print dump summary."""
        for ticker, stats in self.dict_new_archives_saved_by_ticker.items():
            print(f"[Info] ---> For {ticker}: {stats.get("monthly", 0)} new month(s), {stats.get("daily", 0)} new day(s) saved")

    @staticmethod
    def _create_list_dates_for_timeperiod(
        date_start: datetime.date,
        date_end: datetime.date | None = None,
        timeperiod_per_file: str = "monthly",
    ) -> list[datetime.date]:
        """Create list of dates with asked frequency for [date_start, date_end]."""
        list_dates = []
        if date_end is None:
            date_end = datetime.datetime.utcnow().date()
        date_to_use = date_start
        while date_to_use <= date_end:
            list_dates.append(date_to_use)
            if timeperiod_per_file == "monthly":
                date_to_use = date_to_use + relativedelta(months=1)
            else:
                date_to_use = date_to_use + relativedelta(days=1)
        return list_dates
