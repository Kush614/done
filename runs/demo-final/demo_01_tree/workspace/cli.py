#!/usr/bin/env python3
import argparse
import sys
import storage

def main():
    parser = argparse.ArgumentParser(description='A simple notes CLI.')
    subparsers = parser.add_subparsers(dest='command', required=True)

    # add command
    parser_add = subparsers.add_parser('add', help='Add a new note')
    parser_add.add_argument('text', help='The note text')

    # list command
    parser_list = subparsers.add_parser('list', help='List all notes')

    # search command
    parser_search = subparsers.add_parser('search', help='Search notes')
    parser_search.add_argument('term', help='The search term')

    # delete command
    parser_delete = subparsers.add_parser('delete', help='Delete a note by id')
    parser_delete.add_argument('id', type=int, help='The note id')

    args = parser.parse_args()

    if args.command == 'add':
        note = storage.add(args.text)
        print(note['id'])
    elif args.command == 'list':
        notes = storage.load()
        # Sort by id to be safe
        for note in sorted(notes, key=lambda n: n['id']):
            print(f"{note['id']}: {note['text']}")
    elif args.command == 'search':
        results = storage.search(args.term)
        for note in sorted(results, key=lambda n: n['id']):
            print(f"{note['id']}: {note['text']}")
    elif args.command == 'delete':
        if storage.delete(args.id):
            print(f"Deleted note {args.id}")
        else:
            print(f"Error: note with id {args.id} not found", file=sys.stderr)
            sys.exit(1)

if __name__ == '__main__':
    main()