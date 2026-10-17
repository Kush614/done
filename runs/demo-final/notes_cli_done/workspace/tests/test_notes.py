"""Tests for the notes CLI."""

import json
import os

import pytest

from notes import storage
from notes.cli import add_note, delete_note, list_notes, search_notes


@pytest.fixture
def notes_file(tmp_path):
    return str(tmp_path / "notes.json")


def test_add_and_list(notes_file):
    add_note("buy milk", notes_file)
    add_note("call bob", notes_file)
    notes = storage.load(notes_file)
    assert len(notes) == 2
    assert notes[0]["text"] == "buy milk"
    assert notes[1]["text"] == "call bob"
    assert notes[0]["id"] == 1
    assert notes[1]["id"] == 2


def test_search(notes_file):
    add_note("buy milk", notes_file)
    add_note("call bob", notes_file)
    matches = search_notes("milk", notes_file)
    assert len(matches) == 1
    assert matches[0]["text"] == "buy milk"


def test_delete(notes_file):
    add_note("buy milk", notes_file)
    add_note("call bob", notes_file)
    assert delete_note(1, notes_file) is True
    notes = storage.load(notes_file)
    assert len(notes) == 1
    assert notes[0]["text"] == "call bob"


def test_delete_nonexistent(notes_file):
    add_note("buy milk", notes_file)
    assert delete_note(99999, notes_file) is False
    notes = storage.load(notes_file)
    assert len(notes) == 1


def test_persistence_format(notes_file):
    add_note("hello", notes_file)
    with open(notes_file, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    assert isinstance(data, list)
    assert all("id" in n and "text" in n for n in data)
    assert data[0]["text"] == "hello"


def test_load_missing_file(tmp_path):
    missing = str(tmp_path / "does_not_exist.json")
    assert storage.load(missing) == []