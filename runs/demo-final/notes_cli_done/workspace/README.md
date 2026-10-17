# notes

A simple command-line note-taking tool that stores notes in a JSON file.

## Installation

No installation required. Run the tool directly with Python:

```sh
python3 -m notes <command>
```

## Storage

Notes are stored in a JSON file. By default the file is `notes.json` in the
current directory. You can override the location with the `NOTES_FILE`
environment variable:

```sh
NOTES_FILE=/path/to/notes.json python3 -m notes add "my note"
```

Each note is stored as an object with an `id` and a `text` field, inside a
JSON array.

## Commands

### add

Add a new note.

```sh
python3 -m notes add "buy milk"
```

### list

List all notes, one per line, in the format `<id> <text>`.

```sh
python3 -m notes list
```

### search

Search notes for a term and print matching notes.

```sh
python3 -m notes search milk
```

### delete

Delete a note by its id. Exits with a non-zero status if the id does not
exist.

```sh
python3 -m notes delete 1
```

## Running the tests

```sh
python3 -m pytest
```