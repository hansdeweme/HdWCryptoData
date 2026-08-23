# smoke_test.py
# Copyright (c) 2025, 2026 Hans De Weme
# Licensed under the MIT License (https://opensource.org/licenses/MIT).
# part of the HdW_crypto_data Project
import json
import os
from pathlib import Path
# Import the package modules
from hdw_crypto_data.binance_vision_dumper import BinanceVisionDumper
from hdw_crypto_data.total_dataset_builder import TotalDatasetBuilder
from hdw_crypto_data.total_dataset_loader  import TotalDatasetLoader
from ta_charts                             import TACharts

dump = False
asset = "BONK"
df = None

if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()

    settings_path = "settings.json" if os.path.isfile("settings.json") else "..\\settings.json"
    with open(settings_path, "r") as f:
        settings = json.load(f)
    quote = settings.get("quote_currency", "USDT")
    market = f"{asset.upper()}{quote}"
    
    if dump:
        spot_dir = str(Path(settings.get("spot", "..")).resolve())
        dumper = BinanceVisionDumper(
            path_dir_where_to_dump=spot_dir,
            asset_class="spot",
            data_type="klines",
            data_frequency="1h",
        )
        dumper.dump_data(
            tickers=[market],
            date_start=None,
            date_end=None,
            is_to_update_existing=True,
            tickers_to_exclude=["UST"],
        )
        dumper.delete_outdated_daily_results()
    else:
        # Synchronous execution: no app.exec() needed
        builder = TotalDatasetBuilder(asset, settings_path, force_merge=False)
        result = builder.build()
        loader = TotalDatasetLoader(asset, settings_path)
        df = loader.load_total_dataframe(file_path=result.filepath, mode="ta", preferred_tz="CET")
        print(df.info())
        print(df.head())
        print(df.tail())
    if df is None or df.empty:
        print(f"[Warning] No data, {asset.upper()} DataFrame missing or empty.")
    else:
        info = TACharts(asset, df)   
        
