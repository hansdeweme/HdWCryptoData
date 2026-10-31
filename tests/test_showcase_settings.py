"""Settings discovery must survive copying the showcase out of examples/."""
import json
from pathlib import Path
import pytest
from examples.showcase_sources import load_showcase_settings


def write(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values), encoding='utf-8')


def test_standalone_reads_adjacent_settings_not_parent_examples(tmp_path):
    root = tmp_path/'HdWCryptoData'
    settings = root/'settings.json'
    write(settings, {'spot': str(tmp_path/'work'), 'full_spot': str(tmp_path/'work'/'spot')})
    write(tmp_path/'examples'/'settings.json', {'spot': 'wrong'})
    result = load_showcase_settings(root/'showcase_pyqt_app.py')
    assert result['spot'] == str(tmp_path/'work')
    assert result['full_spot'] == str(tmp_path/'work'/'spot')
    assert result['_settings_path'] == str(settings)


def test_examples_layout_preserves_project_relative_paths(tmp_path):
    write(tmp_path/'examples'/'settings.json', {'spot': '.', 'full_spot': './spot', 'crypto_icons': 'examples/icons'})
    result = load_showcase_settings(tmp_path/'examples'/'showcase_pyqt_app.py')
    assert result['spot'] == str(tmp_path)
    assert result['full_spot'] == str(tmp_path/'spot')
    assert result['crypto_icons'] == str(tmp_path/'examples'/'icons')


def test_packaged_entry_point_finds_canonical_examples_settings(tmp_path):
    write(tmp_path/'examples'/'settings.json', {'spot': str(tmp_path/'work')})
    result = load_showcase_settings(tmp_path/'hdw_crypto_data'/'showcase_pyqt_app.py')
    assert result['spot'] == str(tmp_path/'work')


def test_absent_settings_defaults_to_entry_project_not_its_parent(tmp_path):
    root = tmp_path/'standalone'
    result = load_showcase_settings(root/'showcase_pyqt_app.py')
    assert result['spot'] == str(root)
    assert result['_settings_path'] is None


def test_invalid_settings_fail_instead_of_silently_using_wrong_directory(tmp_path):
    write(tmp_path/'settings.json', ['not', 'an', 'object'])
    with pytest.raises(ValueError, match='JSON object'):
        load_showcase_settings(tmp_path/'showcase_pyqt_app.py')
