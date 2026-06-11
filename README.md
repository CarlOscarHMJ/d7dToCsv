# d7dToCsv

Convert DEWESoft `.d7d` data files to CSV format, preserving the header
structure and data layout produced by DEWESoft's built-in export.

## Credits

The core file-reading capability comes from
[`python_dewesoft`](https://github.com/JanKalin/python_dewesoft) by
Jan Kalin and Uros Bohinc, which wraps Dewesoft's DWDataReaderLib DLL.

## Dependencies

A local copy of `python_dewesoft` is included under `dewesoft/` together
with the native libraries:

- `DWDataReaderLib64.so` / `DWDataReaderLib.so` for Linux
- `DWDataReaderLib64.dll` / `DWDataReaderLib.dll` for Windows

A virtual environment is set up with the required Python packages.

## Quick start

```bash
# Linux / macOS
.venv/bin/python3 d7dToCsv.py "input/*.d7d"
```

```powershell
# Windows (PowerShell)
.venv\Scripts\python3 d7dToCsv.py "input\*.d7d"
```

Run with no arguments for interactive mode.

If no output path is given, each CSV is written alongside its `.d7d` file
with the same base name.

## As a Python module

```python
from d7dToCsv import d7dToCsv

d7dToCsv("data/sensor_run_0001.d7d", "export/sensor_run_0001.csv")
```

## CSV format

The output CSV contains:

1. A metadata header block (file name, start time, channel count,
   sample rate, store type, pre/post time)
2. A column header row: `Time [s],Channel1 [unit],Channel2 [unit],...`
3. Data rows with relative time in seconds from the first sample

## Test files

Sample `.d7d` files and reference CSV exports are in
`../Data/Tunneldata/`.
