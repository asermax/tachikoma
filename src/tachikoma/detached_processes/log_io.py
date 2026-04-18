"""Log file reading helpers for detached process output.

Provides efficient tail-based and windowed reading of process log files
without loading the entire file into memory.
"""

from pathlib import Path


def read_tail(path: Path, n: int = 100) -> list[str]:
    """Read the last n lines from a file using reverse-seek.

    Walks backward in fixed-size chunks, splitting on newlines,
    until n lines are collected or the file start is reached.
    Returns lines in oldest-first order.

    Raises FileNotFoundError if the file does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(f"Log file not found: {path}")

    file_size = path.stat().st_size
    if file_size == 0:
        return []

    chunk_size = 8192
    lines: list[str] = []
    position = file_size
    remaining = b""

    while position > 0 and len(lines) < n:
        read_size = min(chunk_size, position)
        position -= read_size

        with open(path, "rb") as f:
            f.seek(position)
            chunk = f.read(read_size)

        data = chunk + remaining
        parts = data.split(b"\n")

        if position > 0:
            remaining = parts[0]
            line_bytes = parts[1:]
        else:
            line_bytes = parts

        for line in reversed(line_bytes):
            if len(lines) >= n:
                break
            decoded = line.decode("utf-8", errors="replace")
            if decoded:
                lines.append(decoded)

    lines.reverse()
    return lines


def read_window(path: Path, line_offset: int, line_count: int) -> list[str]:
    """Read a specific window of lines from a file.

    Sequentially reads lines from the start to satisfy the offset/count
    window. Suitable for paging through log output.

    Raises FileNotFoundError if the file does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(f"Log file not found: {path}")

    result: list[str] = []
    current_line = 0

    with open(path, errors="replace") as f:
        for raw_line in f:
            if current_line >= line_offset + line_count:
                break

            if current_line >= line_offset:
                stripped = raw_line.rstrip("\n")
                if stripped:
                    result.append(stripped)

            current_line += 1

    return result
