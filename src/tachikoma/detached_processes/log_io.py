"""Log file reading helpers for detached process output."""

from pathlib import Path


def read_tail(path: Path, n: int = 100) -> list[str]:
    """Read the last n lines from a file using reverse-seek.

    Walks backward in fixed-size chunks, splitting on newlines,
    until n lines are collected or the file start is reached.
    Returns lines in oldest-first order.

    Raises FileNotFoundError if the file does not exist.
    """
    chunk_size = 8192
    lines: list[str] = []
    remaining = b""
    first_iteration = True

    with open(path, "rb") as f:
        f.seek(0, 2)
        position = f.tell()

        if position == 0:
            return []

        while position > 0 and len(lines) < n:
            read_size = min(chunk_size, position)
            position -= read_size

            f.seek(position)
            chunk = f.read(read_size)

            data = chunk + remaining
            parts = data.split(b"\n")

            if position > 0:
                remaining = parts[0]
                line_bytes = parts[1:]
            else:
                line_bytes = parts

            # Drop the trailing-newline artifact on the first iteration only —
            # genuine blank lines elsewhere in the file should be preserved.
            if first_iteration and line_bytes and line_bytes[-1] == b"":
                line_bytes = line_bytes[:-1]
            first_iteration = False

            for line in reversed(line_bytes):
                if len(lines) >= n:
                    break
                lines.append(line.decode("utf-8", errors="replace"))

    lines.reverse()
    return lines


def read_window(path: Path, line_offset: int, line_count: int) -> list[str]:
    """Read a specific window of lines from a file.

    Raises FileNotFoundError if the file does not exist.
    """
    result: list[str] = []
    current_line = 0

    with open(path, errors="replace") as f:
        for raw_line in f:
            if current_line >= line_offset + line_count:
                break

            if current_line >= line_offset:
                result.append(raw_line.rstrip("\n"))

            current_line += 1

    return result
