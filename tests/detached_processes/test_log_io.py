"""Tests for log file reading helpers."""

from pathlib import Path

import pytest

from tachikoma.detached_processes.log_io import read_tail, read_window


def _write_lines(path: Path, count: int) -> list[str]:
    """Write N numbered lines to a file and return them."""
    lines = [f"line {i}" for i in range(count)]
    path.write_text("\n".join(lines) + "\n")
    return lines


def test_read_tail_returns_last_n(tmp_path):
    path = tmp_path / "test.log"
    lines = _write_lines(path, 100)

    result = read_tail(path, 10)
    assert len(result) == 10
    assert result == lines[-10:]


def test_read_tail_file_smaller_than_n(tmp_path):
    path = tmp_path / "small.log"
    lines = _write_lines(path, 5)

    result = read_tail(path, 100)
    assert result == lines


def test_read_tail_default_100(tmp_path):
    path = tmp_path / "default.log"
    _write_lines(path, 200)

    result = read_tail(path)
    assert len(result) == 100


def test_read_tail_empty_file(tmp_path):
    path = tmp_path / "empty.log"
    path.write_text("")

    result = read_tail(path)
    assert result == []


def test_read_tail_missing_file(tmp_path):
    path = tmp_path / "missing.log"

    with pytest.raises(FileNotFoundError):
        read_tail(path)


def test_read_window_correct_slice(tmp_path):
    path = tmp_path / "window.log"
    lines = _write_lines(path, 50)

    result = read_window(path, 10, 5)
    assert result == lines[10:15]


def test_read_window_from_start(tmp_path):
    path = tmp_path / "window_start.log"
    lines = _write_lines(path, 20)

    result = read_window(path, 0, 5)
    assert result == lines[:5]


def test_read_window_beyond_end(tmp_path):
    path = tmp_path / "window_beyond.log"
    lines = _write_lines(path, 10)

    result = read_window(path, 8, 10)
    assert result == lines[8:]


def test_read_window_missing_file(tmp_path):
    path = tmp_path / "missing.log"

    with pytest.raises(FileNotFoundError):
        read_window(path, 0, 10)
