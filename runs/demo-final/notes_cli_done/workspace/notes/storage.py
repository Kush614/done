"""Persistence layer for notes stored in a JSON file."""

import json
import os


def default_path():
    """Return the default location of the notes JSON file."""
    return os.environ.get("NOTES_FILE", os.path.join(os.getcwd(), "notes.json"))


def load(path):
    """Load notes from the JSON file, returning a list of dicts.

    Returns an empty list if the file does not exist.
    """
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        return []
    return data


def save(path, notes):
    """Write the list of notes to the JSON file."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(notes, fh, indent=2)
        fh.write("\n")


def next_id(notes):
    """Return the next available id (max existing id + 1, or 1 if empty)."""
    if not notes:
        return 1
    return max(n["id"] for n in notes) + 1