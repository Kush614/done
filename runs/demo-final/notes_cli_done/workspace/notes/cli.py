"""Command-line interface for the notes tool."""

import argparse
import sys

from notes import storage


def add_note(text, path):
    notes = storage.load(path)
    note = {"id": storage.next_id(notes), "text": text}
    notes.append(note)
    storage.save(path, notes)
    return note


def list_notes(path):
    notes = storage.load(path)
    for note in notes:
        print(f"{note['id']} {note['text']}")
    return notes


def search_notes(term, path):
    notes = storage.load(path)
    matches = [n for n in notes if term in n["text"]]
    for note in matches:
        print(f"{note['id']} {note['text']}")
    return matches


def delete_note(note_id, path):
    notes = storage.load(path)
    remaining = [n for n in notes if n["id"] != note_id]
    if len(remaining) == len(notes):
        print(f"Error: no note with id {note_id}", file=sys.stderr)
        return False
    storage.save(path, remaining)
    return True


def build_parser():
    parser = argparse.ArgumentParser(
        prog="notes", description="A simple command-line note-taking tool."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Add a new note")
    add_parser.add_argument("text", help="The note text")

    subparsers.add_parser("list", help="List all notes")

    search_parser = subparsers.add_parser("search", help="Search notes")
    search_parser.add_argument("term", help="Term to search for")

    delete_parser = subparsers.add_parser("delete", help="Delete a note by id")
    delete_parser.add_argument("id", type=int, help="The note id to delete")

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    path = storage.default_path()

    if args.command == "add":
        add_note(args.text, path)
    elif args.command == "list":
        list_notes(path)
    elif args.command == "search":
        search_notes(args.term, path)
    elif args.command == "delete":
        if not delete_note(args.id, path):
            return 1

    return 0