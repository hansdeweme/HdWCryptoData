"""Equivalent Windows device paths must not trigger false containment failures."""
import os
from pathlib import Path
from unittest.mock import patch
import pytest
from hdw_crypto_data.archive_manager import _safe_path, _canonical_windows_path


@pytest.mark.skipif(os.name != 'nt', reason='Windows device-path spelling')
def test_sidecar_resolve_retaining_device_prefix(tmp_path):
    root = tmp_path.resolve()
    sidecar = root/'archive_index.sqlite-wal'
    real_resolve = Path.resolve
    def resolve(path, *args, **kwargs):
        if path == sidecar:
            return Path('\\\\?\\'+str(sidecar))
        return real_resolve(path, *args, **kwargs)
    with patch.object(Path, 'resolve', resolve):
        assert _safe_path(root, sidecar, must_exist=False) == sidecar
    assert _canonical_windows_path(Path('\\\\?\\UNC\\server\\share\\file')) == Path('\\\\server\\share\\file')


def test_prefix_normalization_does_not_allow_escape_or_traversal(tmp_path):
    with pytest.raises(ValueError):
        _safe_path(tmp_path, tmp_path/'..'/'outside', must_exist=False)
    with pytest.raises(ValueError):
        _safe_path(tmp_path, tmp_path.parent/'outside', must_exist=False)
