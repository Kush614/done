import unittest
import tempfile
import os
import storage
import cli

class TestNotes(unittest.TestCase):
    def setUp(self):
        # Create a temporary file
        self.temp_fd, self.temp_path = tempfile.mkstemp()
        # Patch the storage.NOTES_FILE to point to our temporary file
        self.original_notes_file = storage.NOTES_FILE
        storage.NOTES_FILE = self.temp_path
        # Ensure the file exists (storage.load will create if not exist, but we want clean state)
        with open(self.temp_path, 'w') as f:
            f.write('[]')

    def tearDown(self):
        # Restore original NOTES_FILE
        storage.NOTES_FILE = self.original_notes_file
        # Close and remove the temporary file
        os.close(self.temp_fd)
        os.unlink(self.temp_path)

    def test_add_creates_note_with_incrementing_id_and_persists(self):
        # Add first note
        note1 = storage.add('First note')
        self.assertEqual(note1['id'], 1)
        self.assertEqual(note1['text'], 'First note')
        # Add second note
        note2 = storage.add('Second note')
        self.assertEqual(note2['id'], 2)
        self.assertEqual(note2['text'], 'Second note')
        # Reload and check persistence
        notes = storage.load()
        self.assertEqual(len(notes), 2)
        self.assertEqual(notes[0]['id'], 1)
        self.assertEqual(notes[0]['text'], 'First note')
        self.assertEqual(notes[1]['id'], 2)
        self.assertEqual(notes[1]['text'], 'Second note')

    def test_list_returns_all_notes_sorted_by_id(self):
        # Start with empty list
        storage.save([])
        # Add three notes out of order of text but ids will be sequential
        storage.add('Third note')
        storage.add('First note')
        storage.add('Second note')
        notes = storage.load()  # This returns list sorted by id as per storage.add
        self.assertEqual(len(notes), 3)
        self.assertEqual(notes[0]['id'], 1)
        self.assertEqual(notes[0]['text'], 'Third note')
        self.assertEqual(notes[1]['id'], 2)
        self.assertEqual(notes[1]['text'], 'First note')
        self.assertEqual(notes[2]['id'], 3)
        self.assertEqual(notes[2]['text'], 'Second note')
        # The list is already sorted by id because we always append with increasing id.

    def test_search_is_case_insensitive_and_returns_matching_notes(self):
        storage.save([])
        storage.add('Hello World')
        storage.add('another test')
        storage.add('Goodbye')
        # Search for 'hello' (case-insensitive)
        results = storage.search('hello')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'], 1)
        self.assertEqual(results[0]['text'], 'Hello World')
        # Search for 'TEST'
        results = storage.search('TEST')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'], 2)
        self.assertEqual(results[0]['text'], 'another test')
        # Search for multiple matches
        results = storage.search('o')
        self.assertEqual(len(results), 2)  # 'Hello World' and 'Goodbye'
        # Search for non-existent term
        results = storage.search('xyz')
        self.assertEqual(len(results), 0)

    def test_delete_removes_note_and_returns_true_and_false_for_missing_id(self):
        storage.save([])
        # Add two notes
        note1 = storage.add('Note 1')
        note2 = storage.add('Note 2')
        self.assertEqual(note1['id'], 1)
        self.assertEqual(note2['id'], 2)
        # Delete existing note
        result = storage.delete(1)
        self.assertTrue(result)
        notes = storage.load()
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]['id'], 2)
        # Delete the same note again (should return False)
        result = storage.delete(1)
        self.assertFalse(result)
        # Delete the other note
        result = storage.delete(2)
        self.assertTrue(result)
        notes = storage.load()
        self.assertEqual(len(notes), 0)
        # Delete from empty list
        result = storage.delete(1)
        self.assertFalse(result)

if __name__ == '__main__':
    unittest.main()