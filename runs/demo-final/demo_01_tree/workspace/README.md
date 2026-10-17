# Notes CLI

A simple command-line interface for managing notes, stored in a JSON file.

## Installation

No dependencies required. Requires Python 3.8+.

## Usage

The CLI provides four commands:

### Add a note
```bash
notes add <text>
```
Example:
```bash
notes add "Buy groceries"
```
Output: the ID of the created note (e.g., `1`)

### List all notes
```bash
notes list
```
Example:
```bash
notes list
```
Output:
```
1: Buy groceries
```

### Search notes
```bash
notes search <term>
```
Example (case-insensitive):
```bash
notes search groceries
```
Output:
```
1: Buy groceries
```

### Delete a note
```bash
notes delete <id>
```
Example:
```bash
notes delete 1
```
Output: `Note 1 deleted.`
If the note does not exist, an error message is printed and the program exits with a non-zero status.

## Storage Format

Notes are stored in `./notes.json` as a JSON array of objects. Each object has:
- `id`: an integer (starting at 1, incrementing, not reused after deletion)
- `text`: a string containing the note's content

Example `notes.json`:
```json
[
  {"id": 1, "text": "Buy groceries"},
  {"id": 2, "text": "Read a book"}
]
```

## Running Tests

To run the test suite:
```bash
python -m unittest tests/test_notes.py
```