import json
import os

NOTES_FILE = "./notes.json"

def load():
    """Load notes from the JSON file. Returns empty list if file does not exist."""
    if not os.path.exists(NOTES_FILE):
        return []
    with open(NOTES_FILE, 'r') as f:
        return json.load(f)

def save(notes):
    """Save the list of notes to the JSON file."""
    with open(NOTES_FILE, 'w') as f:
        json.dump(notes, f)

def add(text):
    """Add a new note with the given text. Returns the created note dict."""
    notes = load()
    # Determine next id: max existing id + 1, or 1 if no notes
    next_id = 1
    if notes:
        next_id = max(note['id'] for note in notes) + 1
    note = {'id': next_id, 'text': text}
    notes.append(note)
    save(notes)
    return note

def search(term):
    """Search for notes containing the term (case-insensitive). Returns list of matching note dicts."""
    notes = load()
    term_lower = term.lower()
    return [note for note in notes if term_lower in note['text'].lower()]

def delete(id):
    """Delete the note with the given id. Returns True if deleted, False if not found."""
    notes = load()
    for i, note in enumerate(notes):
        if note['id'] == id:
            del notes[i]
            save(notes)
            return True
    return False