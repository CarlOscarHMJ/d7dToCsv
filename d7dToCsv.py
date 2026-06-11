"""
d7dToCsv  Convert DEWESoft .d7d files to CSV format.

Usage:
    python d7dToCsv.py                         interactive mode
    python d7dToCsv.py input.d7d [output_directory]
    python d7dToCsv.py "Tunneldata/*.d7d" out/

    Or as a module:
    from d7dToCsv import d7dToCsv
    d7dToCsv("input.d7d", "output.csv")
"""

import glob
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dewesoft import DWDataReader

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MICROSECONDS_PER_MILLISECOND = 1000
VALUE_FORMAT = ".8g"
RELATIVE_TIME_FORMAT = ".6g"
DATE_FORMAT = "%d.%m.%Y %H:%M:%S.%f"
MICROSECOND_TRUNCATE_LENGTH = -3
STORE_TYPE_FAST_ON_TRIGGER = "schnell bei Trigger"
STORE_TYPE_ALWAYS_FAST = "immer schnell"

HEADER_TITLE = "Data info"
COLUMN_TIME = "Time [s]"
NEWLINE = os.linesep

PROGRESS_BAR_WIDTH = 30


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def d7dToCsv(d7dPath, csvPath=None):
    """Convert a DEWESoft .d7d file to CSV.

    d7dToCsv(d7dPath) writes a CSV alongside the .d7d file.

    d7dToCsv(d7dPath, csvPath) writes the CSV to csvPath.

    Returns the path to the created CSV file.
    """

    dll = DWDataReader.open_dll()
    try:
        return _convertOneFile(d7dPath, csvPath, dll)
    finally:
        DWDataReader.close_dll(dll)


# ---------------------------------------------------------------------------
# Internal conversion
# ---------------------------------------------------------------------------


def _convertOneFile(d7dPath, csvPath, dll):
    """Convert a single .d7d file using an already-open DLL handle."""

    outputPath = csvPath if csvPath else os.path.splitext(d7dPath)[0] + ".csv"

    fileInfo = DWDataReader.read_dws(d7dPath, dll=dll)
    data = DWDataReader.read_dws(d7dPath, fields=[], dll=dll)

    headerLines = _buildHeaderLines(d7dPath, fileInfo)
    columnHeader = _buildColumnHeader(fileInfo["channels"])
    relativeTime = _computeRelativeTime(data)
    dataCols = data.columns.tolist()

    _writeCsv(
        outputPath,
        headerLines + [columnHeader],
        relativeTime,
        data.values,
        dataCols,
    )

    return outputPath


# ---------------------------------------------------------------------------
# Header helpers
# ---------------------------------------------------------------------------


def _buildHeaderLines(d7dPath, fileInfo):
    """Return the metadata header block as a list of strings."""

    return [
        HEADER_TITLE,
        f"File name: {d7dPath}",
        f"Start time: {_formatStartTime(fileInfo['start_store_time'])}",
        f"Number of channels: {fileInfo['number_of_channels']}",
        f"Sample rate: {int(fileInfo['sample_rate'])}",
        f"Store type: {_getStoreType(fileInfo['events'])}",
        "Pre time: 0",
        f"Post time: {int(fileInfo['duration'] * MICROSECONDS_PER_MILLISECOND)}",
        "",
    ]


def _buildColumnHeader(channels):
    """Return the column header row as a comma-separated string."""

    def formatChannel(ch):
        name = ch[1]
        unit = ch[3] if ch[3] else "-"
        return f"{name} [{unit}]"

    channelHeaders = [formatChannel(ch) for ch in channels]
    return ",".join([COLUMN_TIME] + channelHeaders)


def _formatStartTime(startTime):
    """Format a datetime as 'DD.MM.YYYY HH:MM:SS.mmm'."""

    return startTime.strftime(DATE_FORMAT)[:MICROSECOND_TRUNCATE_LENGTH]


# ---------------------------------------------------------------------------
# Store type helper
# ---------------------------------------------------------------------------


def _getStoreType(events):
    """Determine store type string from file events."""

    hasTrigger = any(
        "trig" in str(row["text"]).lower() for _, row in events.iterrows()
    )
    return STORE_TYPE_FAST_ON_TRIGGER if hasTrigger else STORE_TYPE_ALWAYS_FAST


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def _computeRelativeTime(data):
    """Return relative time in seconds from the DataFrame's DatetimeIndex."""

    return (data.index - data.index[0]).total_seconds().values


def _formatValue(val):
    """Format a numeric value for CSV output, matching Dewesoft's format."""

    if val is None or (isinstance(val, float) and np.isnan(val)):
        return ""
    if isinstance(val, float):
        return f"{val:{VALUE_FORMAT}}"
    return str(val)


# ---------------------------------------------------------------------------
# CSV writer
# ---------------------------------------------------------------------------


def _writeCsv(outputPath, headerLines, relativeTime, values, dataCols):
    """Write header lines and data rows to a CSV file."""

    with open(outputPath, "w", newline="") as f:
        f.write(NEWLINE.join(headerLines) + NEWLINE)
        _writeDataRows(f, relativeTime, values, dataCols)


def _writeDataRows(fileHandle, relativeTime, values, dataCols):
    """Write data rows using np.savetxt for C-level speed."""

    combined = np.column_stack([relativeTime, values])
    formats = [f"%{RELATIVE_TIME_FORMAT}"] + [f"%{VALUE_FORMAT}"] * len(dataCols)
    np.savetxt(fileHandle, combined, fmt=formats, delimiter=",", newline=NEWLINE)


# ---------------------------------------------------------------------------
# Progress display
# ---------------------------------------------------------------------------


def _formatProgress(current, total, elapsed):
    """Format a single-line progress bar with percentage and ETA."""

    fraction = current / total
    filled = int(PROGRESS_BAR_WIDTH * fraction)
    bar = "[" + "=" * filled + ">" * (1 if filled < PROGRESS_BAR_WIDTH else 0)
    bar += " " * (PROGRESS_BAR_WIDTH - filled - 1) + "]"

    pct = int(fraction * 100)
    eta = (elapsed / current) * (total - current) if current else 0

    return f"\r  {bar} {current}/{total} ({pct}%)  ETA: {_formatDuration(eta)}  "


def _formatDuration(seconds):
    """Format a duration in seconds to a human-readable string."""

    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m {seconds % 60:.0f}s"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}h {m}m"


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------


def _runBatchMode(d7dPaths, outDir):
    """Convert a list of .d7d files with progress display."""

    if outDir:
        os.makedirs(outDir, exist_ok=True)

    nFiles = len(d7dPaths)
    print(f"Found {nFiles} file(s). Converting...\n")

    dll = DWDataReader.open_dll()
    try:
        startTime = time.time()
        for i, d7dPath in enumerate(d7dPaths, 1):
            baseName = os.path.splitext(os.path.basename(d7dPath))[0] + ".csv"
            csvPath = os.path.join(outDir, baseName) if outDir else None
            _convertOneFile(d7dPath, csvPath, dll)

            elapsed = time.time() - startTime
            print(_formatProgress(i, nFiles, elapsed), end="")
            sys.stdout.flush()
    finally:
        DWDataReader.close_dll(dll)

    totalTime = time.time() - startTime
    print(f"\n\nDone. {nFiles} file(s) converted in {_formatDuration(totalTime)}.")


# ---------------------------------------------------------------------------
# Interactive mode
# ---------------------------------------------------------------------------


def _runInteractiveMode():
    """Prompt the user for input pattern and output directory, then convert."""

    print("=" * 50)
    print("  d7d2csv  -  DEWESoft .d7d to CSV converter")
    print("=" * 50)
    print()

    pattern = input(
        "Input file(s): "
    ).strip()
    if not pattern:
        print("No input specified. Exiting.")
        sys.exit(0)

    d7dPaths = sorted(glob.glob(pattern))
    if not d7dPaths:
        print(f"No files match: {pattern}")
        sys.exit(1)

    print(f"\nFound {len(d7dPaths)} .d7d file(s):")
    for path in d7dPaths:
        print(f"  {path}")

    print()
    print("Output folder:")
    print("  [1] Same as input file(s)")
    print("  [2] Custom folder")

    choice = input("Choice (1/2): ").strip()
    if choice == "2":
        outDir = input("Enter output folder: ").strip()
        if not outDir:
            print("No output folder given. Exiting.")
            sys.exit(0)
    else:
        outDir = None

    print()
    confirm = input(f"Start conversion? [Y/n] ").strip().lower()
    if confirm and confirm != "y":
        print("Cancelled.")
        sys.exit(0)

    print()
    _runBatchMode(d7dPaths, outDir)

    try:
        input("\nPress Enter to exit.")
    except (EOFError, OSError):
        pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    if len(sys.argv) < 2:
        _runInteractiveMode()
        return

    d7dPaths = sorted(glob.glob(sys.argv[1]))
    if not d7dPaths:
        print(f"Error: no files match: {sys.argv[1]}")
        sys.exit(1)

    outDir = sys.argv[2] if len(sys.argv) > 2 else None
    _runBatchMode(d7dPaths, outDir)


if __name__ == "__main__":
    main()
