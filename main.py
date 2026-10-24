#!/usr/bin/env python3
"""
CouchDB SQL - A SQL-like interface for CouchDB operations.
"""

import cmd
import sys
import json

from parser import parse, ParseError, ImportCommand
from executor import CouchDBExecutor, ExecutorError


class CouchDBSQL(cmd.Cmd):
    intro = """CouchDB SQL Interface
Type SQL-like commands or 'help' for assistance.
Type 'quit' or 'exit' to leave.

Supported commands:
  SELECT <fields> FROM <db> [WHERE <cond>] [ORDER BY <f> [DESC], ...] [LIMIT <n>] [OFFSET <n>]
  SELECT COUNT(*) FROM <db> [WHERE <cond>]
  SELECT COUNT(DISTINCT <field>) FROM <db> [WHERE <cond>]
  INSERT INTO <db> (field1, field2) VALUES ('val1', 123), ('val2', 456)
  IMPORT <filename> INTO <db>
  DELETE FROM <database> WHERE <conditions>
  UPDATE <database> SET <field>=<value>,... WHERE <conditions>
  EXPORT <database> TO <filename> [WHERE <conditions>]
  CREATE <database>
  SHOW DATABASES

WHERE features: IN, BETWEEN, IS NULL, IS NOT NULL, parentheses, nested fields (a.b.c)

Examples:
  SELECT * FROM products WHERE status IN ('active','pending') AND price BETWEEN 10 AND 100
  SELECT COUNT(*) FROM users WHERE active=true
  INSERT INTO products (name, price, active) VALUES ('Widget', 9.99, true)
  IMPORT data.csv INTO products
  SELECT * FROM users WHERE (role='admin' OR role='mod') AND active=true
  DELETE FROM logs WHERE created_at<1704067200 AND level IN ('debug','trace')


Commands can span multiple lines. End with ; to execute.
"""
    prompt = 'couchdb> '
    continuation_prompt = '      -> '

    def __init__(self):
        super().__init__()
        self.executor = CouchDBExecutor()
        self.command_buffer = []  # Buffer for multi-line commands

        # Test connection on startup
        if not self.executor.test_connection():
            print(f"Warning: {self.executor.connection_message}")
            print()

    def precmd(self, line: str) -> str:
        """Handle multi-line input. Accumulate lines until we see a semicolon."""
        line = line.rstrip()

        # Check for built-in commands that should execute immediately
        line_lower = line.lower().strip()
        if line_lower in ('quit', 'exit', 'eof', '') or line_lower.startswith('help'):
            # If we have buffered content, clear it first
            if self.command_buffer:
                self.command_buffer = []
                self.prompt = 'couchdb> '
            return line

        # Check if line ends with semicolon (command complete)
        if line.rstrip().endswith(';'):
            # Add this line to buffer and join all lines
            self.command_buffer.append(line)
            full_command = ' '.join(self.command_buffer)
            self.command_buffer = []
            self.prompt = 'couchdb> '
            return full_command
        else:
            # Accumulate in buffer, continue reading
            self.command_buffer.append(line)
            self.prompt = self.continuation_prompt
            return ''  # Return empty to skip processing

    def emptyline(self):
        """Handle empty line - do nothing, but don't repeat last command."""
        pass

    def default(self, line: str):
        """Handle SQL-like commands."""
        line = line.strip()

        if not line:
            return

        try:
            cmd_obj = parse(line)
            if cmd_obj is None:
                return

            # Handle IMPORT command specially (requires confirmation)
            if isinstance(cmd_obj, ImportCommand):
                self._handle_import(cmd_obj)
                return

            result = self.executor.execute(cmd_obj)

            # Display results based on command type
            if 'created' in result:
                # CREATE command
                print(f"Database '{result['database']}' created successfully.")
            elif 'databases' in result:
                # SHOW DATABASES command
                if result['databases']:
                    print("Databases:")
                    for db in result['databases']:
                        print(f"  {db}")
                    print(f"\n{result['total']} database(s)")
                else:
                    print("No databases found.")
            elif 'inserted' in result:
                # INSERT command - show insert stats
                print(f"Inserted: {result['inserted']} document(s)")
                if result.get('errors', 0) > 0:
                    print(f"Errors: {result['errors']}")
                    self._print_error_details(result)
                if result.get('ids') and len(result['ids']) <= 10:
                    print(f"IDs: {', '.join(result['ids'])}")
                elif result.get('ids'):
                    print(f"IDs: {', '.join(result['ids'][:10])}... (+{len(result['ids']) - 10} more)")
            elif 'count' in result:
                # COUNT command - show count result
                if result.get('distinct_field'):
                    print(f"COUNT(DISTINCT {result['distinct_field']}): {result['count']}")
                else:
                    print(f"COUNT(*): {result['count']}")
            elif 'table' in result:
                # SELECT command - show ASCII table
                if result['table']:
                    print(result['table'])
                if result.get('limited'):
                    offset_info = f", offset {result['offset']}" if result.get('offset', 0) > 0 else ""
                    print(f"\n{result['returned']} row(s) returned (from {result['matched']} matched{offset_info})")
                else:
                    print(f"\n{result['matched']} row(s) selected")
            else:
                # Other commands - show stats
                print(f"Scanned: {result['scanned']} documents")
                print(f"Matched: {result['matched']} documents")
                if 'deleted' in result:
                    print(f"Deleted: {result['deleted']} documents")
                if 'updated' in result:
                    print(f"Updated: {result['updated']} documents")
                if 'exported' in result:
                    print(f"Exported: {result['exported']} documents")
                    print(f"File: {result['filename']} ({result['format'].upper()})")
                    print(f"Fields: {result['fields']}")
                if result.get('errors', 0) > 0:
                    print(f"Errors: {result['errors']}")
                    self._print_error_details(result)

            for warning in self.executor.type_warnings:
                print(f"Warning: {warning}")

        except ParseError as e:
            print(f"Parse error: {e}")
        except ExecutorError as e:
            print(f"Execution error: {e}")
        except Exception as e:
            print(f"Error: {e}")

    def _handle_import(self, cmd_obj: ImportCommand):
        """Handle IMPORT command with confirmation."""
        try:
            # Prepare import (parse and validate file)
            result = self.executor.prepare_import(cmd_obj)

            # Show preview
            print(f"\nFile: {cmd_obj.filename} ({result['format'].upper()})")
            print(f"Target database: {result['database']}")
            print(f"\nSample document (first row):")
            print(json.dumps(result['preview'], indent=2, ensure_ascii=False))
            print(f"\n{result['count']} document(s) will be imported.")

            # Ask for confirmation
            while True:
                try:
                    response = input("\nProceed with import? (yes/no): ").strip().lower()
                except EOFError:
                    print("\nImport cancelled.")
                    return

                if response in ('yes', 'y'):
                    break
                elif response in ('no', 'n'):
                    print("Import cancelled.")
                    return
                else:
                    print("Please enter 'yes' or 'no'.")

            # Execute import
            import_result = self.executor.execute_import_confirmed(
                result['database'],
                result['documents']
            )

            print(f"\nImported: {import_result['inserted']} document(s)")
            if import_result.get('errors', 0) > 0:
                print(f"Errors: {import_result['errors']}")
                self._print_error_details(import_result)

        except ExecutorError as e:
            print(f"Import error: {e}")
        except Exception as e:
            print(f"Error: {e}")

    def _print_error_details(self, result):
        for detail in result.get('error_details') or []:
            print(f"  {detail.get('id')}: {detail.get('error')}: {detail.get('reason')}")

    def do_quit(self, arg):
        """Exit the program."""
        print("Goodbye!")
        return True

    def do_exit(self, arg):
        """Exit the program."""
        return self.do_quit(arg)

    def do_EOF(self, arg):
        """Handle Ctrl+D."""
        print()
        return self.do_quit(arg)

    def do_help(self, arg):
        """Show help information."""
        if arg:
            super().do_help(arg)
        else:
            print("""
CouchDB SQL Help
================

Commands:
  SELECT <fields> FROM <db> [WHERE <cond>] [ORDER BY <f> [DESC], ...] [LIMIT <n>] [OFFSET <n>]
    Query and display documents in an ASCII table.
    Use * for all fields, or list specific fields separated by commas.
    ORDER BY supports multiple fields with ASC (default) or DESC direction.
    LIMIT restricts rows returned, OFFSET skips rows (for pagination).

  SELECT COUNT(*) FROM <db> [WHERE <cond>]
    Count all documents matching conditions.

  SELECT COUNT(DISTINCT <field>) FROM <db> [WHERE <cond>]
    Count distinct values of a field in matching documents.

  INSERT INTO <db> (field1, field2, ...) VALUES (val1, val2), (val3, val4), ...
    Insert one or more documents into a database.
    - Field names in parentheses define the document structure
    - Each VALUES tuple creates one document
    - Strings use single quotes with '' to escape: 'Mc''Donald'

  DELETE FROM <database> WHERE <conditions>
    Delete documents matching conditions from a database.

  UPDATE <database> SET <field>=<value>,... WHERE <conditions>
    Update fields in documents matching conditions.

  EXPORT <database> TO <filename> [WHERE <conditions>]
    Export documents to a file. Supported formats:
    - .xls/.xlsx  Excel spreadsheet (requires openpyxl)
    - .json       JSON array (suitable for bulk import)
    - .sql        SQL CREATE TABLE + INSERT statements

  IMPORT <filename> INTO <database>
    Import documents from a file. Supported formats:
    - .csv        CSV with header row (first line = field names)
    - .json       JSON array of documents
    Field names ending with _NUM are converted to numbers (suffix stripped).
    Shows preview and asks for confirmation before importing.

  CREATE <database>
    Create a new database.

  SHOW DATABASES
    List all user databases (excludes system databases starting with _).

Comparison operators:
  field=value           Equal to
  field!=value          Not equal to
  field<value           Less than
  field>value           Greater than
  field<=value          Less than or equal
  field>=value          Greater than or equal
  field~'pattern'       Regex match (case-sensitive)
  field~*'pattern'      Regex match (case-insensitive)

Advanced operators:
  field IN ('a','b')        Value is in list
  field NOT IN ('a','b')    Value is not in list
  field BETWEEN 10 AND 100  Value is in range (inclusive)
  field IS NULL             Value is null/missing
  field IS NOT NULL         Value exists and is not null

Logical operators:
  AND                   Both conditions must match
  OR                    Either condition must match
  (...)                 Parentheses for grouping

Nested fields:
  address.city          Access nested document fields
  user.profile.name     Multiple levels supported

Examples:
  SELECT * FROM products WHERE status IN ('active','pending')
  SELECT * FROM orders WHERE price BETWEEN 100 AND 500
  SELECT * FROM users WHERE deleted_at IS NULL
  SELECT * FROM items WHERE (type='A' OR type='B') AND active=true
  SELECT * FROM customers WHERE address.country='France'
  SELECT COUNT(*) FROM users WHERE active=true
  SELECT COUNT(DISTINCT category) FROM products WHERE price>100
  INSERT INTO products (name, price) VALUES ('Widget', 9.99), ('Gadget', 19.99)
  IMPORT products.csv INTO products
  DELETE FROM logs WHERE level IN ('debug','trace') AND age>30
  EXPORT orders TO orders.json WHERE status!='cancelled'

Other commands:
  help              Show this help
  quit / exit       Exit the program

Multi-line input:
  Commands can span multiple lines. End with ; to execute.
  The prompt changes to '      -> ' while entering a multi-line command.
""")


def main():
    try:
        CouchDBSQL().cmdloop()
    except KeyboardInterrupt:
        print("\nInterrupted. Goodbye!")
        sys.exit(0)


if __name__ == '__main__':
    main()
