"""Headless showcase wiring and synthetic acquisition, without live GUI/network."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

pytest.importorskip('PyQt6')
from PyQt6.QtWidgets import QApplication
from examples.showcase_sources import CryptoRequest, CryptoSourceAdapter, MarketDataset, ArchiveProvisionOutcome
from examples.showcase_pyqt_app import MiniDumperApp, PipelineWorker
from hdw_crypto_data import CoinbaseArchiveProvisioner, TotalDatasetBuilder
from test_coinbase_archives import SyntheticClient


@pytest.fixture(scope='module')
def application():
    return QApplication.instance() or QApplication([])


def test_showcase_provider_interval_and_separate_actions(application, tmp_path):
    with patch('examples.showcase_pyqt_app.source_availability', return_value=(True, True)):
        window = MiniDumperApp()
    window.show()
    application.processEvents()
    window.source_combo.setCurrentIndex(2)
    assert window.source_combo.currentText() == 'Coinbase crypto'
    assert window.selected_symbols() == ['FARTCOIN-USD']
    assert window.normalize_asset('HNT-USD') == 'HNT-USD'
    assert window.provision_button.isVisible() and not window.archive_check.isVisible()
    assert window.days_spin.value() == 7
    window.interval_combo.setCurrentText('15m')
    window.datastore_input.setText(str(tmp_path))
    captured = []
    with patch.object(PipelineWorker, 'start', lambda worker: captured.extend(worker.requests)):
        window.start_processing()
    assert captured[0].provider == 'coinbase' and captured[0].operation == 'total'
    assert captured[0].interval == '15m' and captured[0].settings['datastore_root'] == str(tmp_path)
    window.batch_finished()
    captured.clear()
    with patch.object(PipelineWorker, 'start', lambda worker: captured.extend(worker.requests)):
        window.start_provisioning()
    assert captured[0].operation == 'provision'
    window.batch_finished()
    frame = pd.DataFrame({'open':[2.], 'high':[3.], 'low':[1.], 'close':[2.], 'volume':[10.]},
        index=pd.date_range('2026-10-01', periods=1, tz='UTC'))
    dataset = MarketDataset.from_dataframe(frame, symbol='HNT-USD', interval='15m', provider='Coinbase', warnings=('11 missing buckets',))
    window.add_dataset(dataset)
    assert 'HNT-USD' in window.chart_asset_combo.currentText() and 'Coinbase' in window.chart_asset_combo.currentText()
    assert 'Trade counts: unavailable' in window.metadata_label.text()
    assert '11 missing buckets' in window.log_console.toPlainText()
    window.source_combo.setCurrentIndex(1)
    assert window.normalize_asset('BONKUSDT') == 'BONK'
    assert window.interval_combo.currentText() == '1h' and not window.interval_combo.isEnabled()
    assert window.archive_check.isVisible() and not window.provision_button.isVisible()
    window.source_combo.setCurrentIndex(0)
    assert window.days_spin.value() == 730
    window.close()


@pytest.mark.parametrize('root_kind', ['datastore', 'provider', 'spot', 'binance_spot'])
def test_showcase_adapter_builds_loads_and_does_not_provision_implicitly(tmp_path, root_kind):
    start = pd.Timestamp('2026-10-01T00:00:00Z')
    end = start+pd.Timedelta(days=1)
    cutoff = end+pd.Timedelta(hours=2)
    CoinbaseArchiveProvisioner(tmp_path, client=SyntheticClient()).provision('HNT-USD', interval='15m',
        start=start, end=end, as_of=cutoff)
    original = TotalDatasetBuilder
    def construct(*args, **kwargs):
        return original(*args, **kwargs, recent_source=SyntheticClient())
    selected_root = {'datastore': tmp_path, 'provider': tmp_path/'coinbase', 'spot': tmp_path/'coinbase'/'spot',
                     'binance_spot': tmp_path/'spot'}[root_kind]
    request = CryptoRequest('HNT-USD', {'datastore_root': str(selected_root)}, interval='15m',
        provider='coinbase', as_of=cutoff.isoformat())
    with patch('hdw_crypto_data.TotalDatasetBuilder', side_effect=construct), patch('hdw_crypto_data.CoinbaseArchiveProvisioner') as provisioner:
        dataset = CryptoSourceAdapter().load(request)
    provisioner.assert_not_called()
    assert dataset.provider == 'Coinbase' and dataset.symbol == 'HNT-USD'
    assert len(dataset.dataframe) == 104 and str(dataset.dataframe.index.tz) == 'UTC'
    assert len(list((tmp_path/'coinbase'/'totals').glob('*.csv'))) == 1
    assert not list(tmp_path.glob('coinbase/coinbase'))


def test_worker_provision_result_is_not_emitted_as_chart_data(application):
    request = CryptoRequest('HNT-USD', {}, provider='coinbase', operation='provision')
    class Controller:
        def load(self, request):
            return ArchiveProvisionOutcome(24, 'COMPLETE; 0 missing')
    worker = PipelineWorker([request], controller=Controller())
    charts, outcomes = [], []
    worker.dataset_ready.connect(charts.append)
    worker.asset_finished.connect(lambda *args: outcomes.append(args))
    worker.run()
    assert charts == [] and outcomes[0][1:3] == (True, 24)


def test_binance_showcase_adapter_keeps_old_pipeline(tmp_path):
    request = CryptoRequest('BONK', {'spot': str(tmp_path), 'full_spot': str(tmp_path/'spot')}, download_archives=False)
    frame = pd.DataFrame({'open':[2.], 'high':[3.], 'low':[1.], 'close':[2.], 'volume':[10.]},
        index=pd.date_range('2026-10-01', periods=1, tz='UTC'))
    with patch('hdw_crypto_data.total_dataset_builder.TotalDatasetBuilder') as build, patch('hdw_crypto_data.total_dataset_loader.TotalDatasetLoader') as load:
        build.return_value.build.return_value.filepath = 'existing.csv'
        build.return_value.build.return_value.warnings = ()
        load.return_value.load_total_dataframe.return_value = frame
        dataset = CryptoSourceAdapter().load(request)
    assert dataset.provider == 'Binance' and dataset.symbol == 'BONKUSDT'
    assert build.call_args.args[0] == 'BONK'
