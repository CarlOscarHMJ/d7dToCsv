"""
d7dToCsv  Convert DEWESoft .d7d files to CSV format.

Usage:
    python d7dToCsv.py                         interactive mode (asks for input only)
    python d7dToCsv.py input.d7d               converts to CSV alongside input
    python d7dToCsv.py input.d7d out/          writes CSV to out/
    python d7dToCsv.py "data/*.d7d" out/       batch with glob pattern
    python d7dToCsv.py "data/*.d7d" --workers 1   serial mode

    Or as a module:
    from d7dToCsv import d7dToCsv
    d7dToCsv("input.d7d", "output.csv")
"""

import argparse
import atexit
import glob
import multiprocessing
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

    # ETA assumes uniform conversion speed across all remaining files
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
# Parallel worker state and helpers
# ---------------------------------------------------------------------------

_workerDll = None
_progressState = None
_workerIndex = None


def _initWorker(progressState, nextIdCounter):
    """Initialise a worker process: open DLL, claim worker ID, register cleanup."""

    global _workerDll, _progressState, _workerIndex

    _progressState = progressState
    with nextIdCounter.get_lock():
        _workerIndex = nextIdCounter.value
        nextIdCounter.value += 1

    _workerDll = DWDataReader.open_dll()
    atexit.register(_cleanupWorker)


def _cleanupWorker():
    """Close the worker's DLL handle on process exit."""

    global _workerDll
    if _workerDll is not None:
        DWDataReader.close_dll(_workerDll)
        _workerDll = None


def _processChunk(chunk):
    """Process a chunk of .d7d files sequentially in one worker process.

    Returns (successPaths, errors) where errors is a list of (path, msg) tuples.
    """

    global _workerIndex, _progressState, _workerDll

    workerId = _workerIndex
    nChunk = len(chunk)

    firstFile = chunk[0][0] if chunk else ""
    _progressState[workerId] = (0, nChunk, os.path.basename(firstFile))

    successPaths = []
    errors = []

    for i, (d7dPath, csvPath) in enumerate(chunk):
        try:
            result = _convertOneFile(d7dPath, csvPath, _workerDll)
            successPaths.append(result)
        except Exception as e:
            errors.append((d7dPath, str(e)))

        nextFile = chunk[i + 1][0] if i + 1 < nChunk else ""
        _progressState[workerId] = (i + 1, nChunk, os.path.basename(nextFile))

    return successPaths, errors


def _chunkTasks(tasks, nChunks):
    """Distribute tasks evenly across nChunks using round-robin."""

    if nChunks >= len(tasks):
        return [[task] for task in tasks]

    chunks = [[] for _ in range(nChunks)]
    for i, task in enumerate(tasks):
        chunks[i % nChunks].append(task)
    return chunks


def _renderWorkerDisplay(progressState, nWorkers, nFiles, startTime):
    """Build multi-line progress display showing per-worker and overall bars.

    Returns (displayString, totalCompleted).
    """

    lines = []
    totalCompleted = 0

    for w in range(nWorkers):
        state = progressState.get(w)
        if state is None:
            continue
        completed, total, currentFile = state
        totalCompleted += completed

        fraction = completed / total if total else 0
        filled = int(PROGRESS_BAR_WIDTH * fraction)
        bar = "[" + "=" * filled + ">" * (1 if filled < PROGRESS_BAR_WIDTH else 0)
        bar += " " * (PROGRESS_BAR_WIDTH - filled - 1) + "]"
        pct = int(fraction * 100)

        fileDisplay = currentFile if currentFile else "done"
        lines.append(
            f"  W{w + 1:2d} {bar} {completed:3d}/{total:3d} ({pct:3d}%)  {fileDisplay}"
        )

    elapsed = time.time() - startTime
    fraction = totalCompleted / nFiles if nFiles else 0
    filled = int(PROGRESS_BAR_WIDTH * fraction)
    bar = "[" + "=" * filled + ">" * (1 if filled < PROGRESS_BAR_WIDTH else 0)
    bar += " " * (PROGRESS_BAR_WIDTH - filled - 1) + "]"
    pct = int(fraction * 100)
    eta = (elapsed / totalCompleted) * (nFiles - totalCompleted) if totalCompleted else 0

    lines.append(
        f"  Tot  {bar} {totalCompleted}/{nFiles} ({pct}%)  ETA: {_formatDuration(eta)}"
    )

    return "\n".join(lines), totalCompleted


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------


def _runBatchMode(d7dPaths, outDir, workers=None):
    """Convert a list of .d7d files, in parallel by default.

    workers: Number of parallel workers. None = auto (N-1 cores), 1 = serial.
    """

    if outDir:
        os.makedirs(outDir, exist_ok=True)

    nFiles = len(d7dPaths)

    tasks = []
    for d7dPath in d7dPaths:
        baseName = os.path.splitext(os.path.basename(d7dPath))[0] + ".csv"
        csvPath = os.path.join(outDir, baseName) if outDir else None
        tasks.append((d7dPath, csvPath))

    if workers is None:
        cpuCount = os.cpu_count() or 1
        workers = max(1, cpuCount - 1)

    print(f"Found {nFiles} file(s). Converting with {workers} worker(s)...\n")

    if workers == 1:
        _runSerial(tasks)
    else:
        _runParallel(tasks, workers)


def _runSerial(tasks):
    """Convert files one at a time (single process, single DLL handle)."""

    nFiles = len(tasks)
    errors = []
    dll = DWDataReader.open_dll()
    try:
        startTime = time.time()
        for i, (d7dPath, csvPath) in enumerate(tasks, 1):
            try:
                _convertOneFile(d7dPath, csvPath, dll)
            except Exception as e:
                errors.append((d7dPath, str(e)))
            elapsed = time.time() - startTime
            print(_formatProgress(i, nFiles, elapsed), end="")
            sys.stdout.flush()
    finally:
        DWDataReader.close_dll(dll)

    totalTime = time.time() - startTime
    print()
    if errors:
        print(f"\n{len(errors)} file(s) failed:")
        for path, err in errors:
            print(f"  {path}: {err}")
        print()

    successCount = nFiles - len(errors)
    print(f"\nDone. {successCount}/{nFiles} file(s) converted in {_formatDuration(totalTime)}.")


def _runParallel(tasks, workers):
    """Convert files in parallel using a process pool.

    Each worker process opens its own DLL handle once and reuses it
    for its entire chunk.  Per-worker progress bars are displayed
    alongside an overall progress bar with ETA.  Errors are collected
    per-file rather than crashing the batch.
    """

    nFiles = len(tasks)
    actualWorkers = min(workers, nFiles)
    chunks = _chunkTasks(tasks, actualWorkers)

    manager = multiprocessing.Manager()
    progressState = manager.dict()
    nextIdCounter = multiprocessing.Value("i", 0)

    for w in range(actualWorkers):
        progressState[w] = None

    startTime = time.time()

    with multiprocessing.Pool(
        actualWorkers,
        initializer=_initWorker,
        initargs=(progressState, nextIdCounter),
    ) as pool:
        asyncResults = [
            pool.apply_async(_processChunk, (chunk,)) for chunk in chunks
        ]

        prevLineCount = 0
        while not all(r.ready() for r in asyncResults):
            displayStr, _ = _renderWorkerDisplay(
                progressState, actualWorkers, nFiles, startTime
            )
            if prevLineCount:
                sys.stdout.write(f"\033[{prevLineCount}A")
            sys.stdout.write("\033[J")
            sys.stdout.write(displayStr + "\n")
            sys.stdout.flush()
            prevLineCount = displayStr.count("\n") + 1
            time.sleep(0.3)

        # Final render
        displayStr, completed = _renderWorkerDisplay(
            progressState, actualWorkers, nFiles, startTime
        )
        if prevLineCount:
            sys.stdout.write(f"\033[{prevLineCount}A")
        sys.stdout.write("\033[J")
        sys.stdout.write(displayStr + "\n")
        sys.stdout.flush()

        allErrors = []
        for r in asyncResults:
            _, errors = r.get()
            allErrors.extend(errors)

    totalTime = time.time() - startTime
    print()
    if allErrors:
        print(f"{len(allErrors)} file(s) failed:")
        for path, err in allErrors:
            print(f"  {path}: {err}")
        print()

    successCount = nFiles - len(allErrors)
    print(
        f"Done. {successCount}/{nFiles} file(s) converted"
        f" in {_formatDuration(totalTime)} using {actualWorkers} workers."
    )


# ---------------------------------------------------------------------------
# Interactive mode
# ---------------------------------------------------------------------------


def _runInteractiveMode():
    """Prompt the user for an input pattern, then convert using defaults."""

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

    allPaths = sorted(glob.glob(pattern))
    d7dPaths = [p for p in allPaths if p.lower().endswith(".d7d")]
    skipped = len(allPaths) - len(d7dPaths)
    if skipped:
        print(f"\nSkipped {skipped} non-.d7d file(s).")
    if not d7dPaths:
        print(f"No .d7d files match: {pattern}")
        sys.exit(1)

    print(f"\nFound {len(d7dPaths)} .d7d file(s):")
    for path in d7dPaths:
        print(f"  {path}")

    print()
    _runBatchMode(d7dPaths, None, workers=None)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Convert DEWESoft .d7d files to CSV format."
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="Input file or glob pattern (e.g. 'data/*.d7d')",
    )
    parser.add_argument(
        "output",
        nargs="?",
        default=None,
        help="Output directory for CSV files",
    )
    parser.add_argument(
        "-w",
        "--workers",
        type=int,
        default=None,
        help="Number of parallel workers (default: N-1 cores, 1 = serial)",
    )

    args = parser.parse_args()

    if not args.input:
        _runInteractiveMode()
        return

    allPaths = sorted(glob.glob(args.input))
    d7dPaths = [p for p in allPaths if p.lower().endswith(".d7d")]
    skipped = len(allPaths) - len(d7dPaths)
    if skipped:
        print(f"Skipped {skipped} non-.d7d file(s).")
    if not d7dPaths:
        print(f"Error: no .d7d files match: {args.input}")
        sys.exit(1)

    _runBatchMode(d7dPaths, args.output, workers=args.workers)


if __name__ == "__main__":
    main()
