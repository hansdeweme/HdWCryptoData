# Read multiple Python files and combine them into a single text file
with open("hdw_crypto_data.txt", "w", encoding="utf-8") as output_file:
    for filename in ["__init__.py",
                     "binance_vision_client.py",
                     "data_dumper.py",
                     "symbols.py",
                     "total_dataset_loader.py",
                     "total_dataset_builder.py",
                     "version.py",
                     "license",
                     "..\\binancedump.py",
                     "..\\ta_charts.py",
                     "..\\showcase_pyqt_app.py",
                     "..\\test_hdw_crypto_data.py",
                     "..\\stylesheet.py",
                     "..\\settings.json", 
                     ]:  # List your Python files here
        with open(filename, "r", encoding="utf-8") as input_file:
            content = input_file.read()
            output_file.write(f"--- Start of {filename} ---\n")
            output_file.write(content)
            output_file.write(f"\n--- End of {filename} ---\n\n")   
