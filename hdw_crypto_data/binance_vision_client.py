# binance_vision_client.py
# Part of the HdW_crypto_data package
# Purpose: provide safe access to Binance Vision API
#
from __future__ import annotations
import hashlib
from pathlib import Path
from urllib.parse import urlparse
import certifi
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BINANCE_VISION_HOST = "data.binance.vision"


class BinanceChecksumNotFoundError(RuntimeError):
    """Raised when Binance Vision has no checksum file for a downloaded archive."""


class BinanceChecksumMismatchError(RuntimeError):
    """Raised when a downloaded archive does not match Binance's checksum."""


def create_binance_session() -> requests.Session:
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        status=4,
        backoff_factor=0.75,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )

    session = requests.Session()
    session.headers["User-Agent"] = "hdw-crypto-data/0.1"
    session.mount("https://data.binance.vision/", HTTPAdapter(max_retries=retry),)
    return session


def validate_binance_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("Binance Vision downloads must use HTTPS")
    if parsed.hostname != BINANCE_VISION_HOST:
        raise ValueError(f"Unexpected download host: {parsed.hostname}")

def download_file(
    session: requests.Session,
    url: str,
    destination: Path,
) -> None:
    validate_binance_url(url)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        with session.get(url, stream=True, timeout=(10, 120), verify=certifi.where(), ) as response:
            response.raise_for_status()
            with temporary.open("wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        output.write(chunk)
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _parse_checksum(checksum_text: str) -> str:
    parts = checksum_text.strip().split()
    if not parts:
        raise ValueError("Checksum file is empty")

    checksum = parts[0].strip().lower()
    if len(checksum) != 64 or any(char not in "0123456789abcdef" for char in checksum):
        raise ValueError(f"Unexpected checksum format: {checksum!r}")
    return checksum


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file_checksum(
    session: requests.Session,
    archive_url: str,
    archive_path: Path,
) -> None:
    """Verify a downloaded Binance Vision archive against archive.zip.CHECKSUM."""
    checksum_url = f"{archive_url}.CHECKSUM"
    validate_binance_url(checksum_url)

    try:
        with session.get(checksum_url, timeout=(10, 30), verify=certifi.where()) as response:
            response.raise_for_status()
            expected_checksum = _parse_checksum(response.text)
    except requests.HTTPError as ex:
        if ex.response is not None and ex.response.status_code == 404:
            raise BinanceChecksumNotFoundError(f"Checksum not found: {checksum_url}") from ex
        raise

    actual_checksum = _sha256_file(archive_path)
    if actual_checksum != expected_checksum:
        raise BinanceChecksumMismatchError(
            f"Checksum mismatch for {archive_path.name}: expected {expected_checksum}, got {actual_checksum}"
        )
