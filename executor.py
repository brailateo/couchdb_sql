"""
CouchDB executor for SQL-like commands.
Handles connection and operations against CouchDB.
"""

import os
import re
import json
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

import csv
from parser import (DeleteCommand, UpdateCommand, ExportCommand, SelectCommand, CountCommand,
                    InsertCommand, ImportCommand, CreateDatabaseCommand, ShowDatabasesCommand,
                    Condition, ConditionGroup)


class ExecutorError(Exception):
    pass


def _values_equal(actual, expected) -> bool:
    """Equality that keeps JSON booleans distinct from numbers.

    In Python, True == 1 and False == 0. CouchDB documents store them
    as different JSON types, so comparisons must not collapse them.
    """
    actual_bool = isinstance(actual, bool)
    expected_bool = isinstance(expected, bool)
    if actual_bool or expected_bool:
        return actual_bool and expected_bool and actual == expected
    return actual == expected


def _same_order_kind(actual, expected) -> bool:
    """False when a boolean is ordered against a non-boolean.

    True < 2 is True in Python because bool is a subclass of int.
    That would make `flag < 2` and `flag BETWEEN 0 AND 1` match JSON true.
    """
    actual_bool = isinstance(actual, bool)
    expected_bool = isinstance(expected, bool)
    if actual_bool or expected_bool:
        return actual_bool and expected_bool
    return True


class CouchDBExecutor:
    def __init__(self):
        load_dotenv()

        self.url = os.getenv('COUCHDB_URL', 'http://localhost:5984')
        self.user = os.getenv('COUCHDB_USER')
        self.password = os.getenv('COUCHDB_PASS')

        # Remove trailing slash from URL if present
        self.url = self.url.rstrip('/')

        self.auth = None
        if self.user and self.password:
            self.auth = HTTPBasicAuth(self.user, self.password)

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        """Make HTTP request to CouchDB."""
        url = f"{self.url}/{path}"
        kwargs['auth'] = self.auth
        kwargs.setdefault('headers', {})['Content-Type'] = 'application/json'

        response = requests.request(method, url, **kwargs)
        return response

    def test_connection(self) -> bool:
        """Test connection to CouchDB."""
        try:
            response = self._request('GET', '')
            return response.status_code == 200
        except requests.RequestException:
            return False

    def _get_field_value(self, doc: dict, field: str):
        """
        Get field value from document, supporting nested fields with dot notation.
        Example: 'address.city' gets doc['address']['city']
        """
        if '.' not in field:
            return doc.get(field)

        # Navigate nested structure
        parts = field.split('.')
        value = doc
        for part in parts:
            if isinstance(value, dict):
                value = value.get(part)
            else:
                return None
        return value

    def _evaluate_condition(self, doc: dict, condition: Condition) -> bool:
        """Evaluate a single condition against a document."""
        field = condition.field
        operator = condition.operator
        expected = condition.value

        # Get field value with nested field support
        actual = self._get_field_value(doc, field)

        # Handle IS NULL / IS NOT NULL
        if operator == 'IS NULL':
            return actual is None
        elif operator == 'IS NOT NULL':
            return actual is not None
        # Null and missing fields fail every comparison, including
        # `= null`. IS NULL / IS NOT NULL are handled above.
        elif actual is None:
            return False

        # Handle IN / NOT IN
        elif operator == 'IN':
            return any(_values_equal(actual, item) for item in expected)
        elif operator == 'NOT IN':
            return all(not _values_equal(actual, item) for item in expected)

        # Handle BETWEEN
        elif operator == 'BETWEEN':
            low, high = expected
            if not _same_order_kind(actual, low) or not _same_order_kind(actual, high):
                return False
            return low <= actual <= high

        # Handle comparison operators
        elif operator == '=':
            return _values_equal(actual, expected)
        elif operator == '!=':
            return not _values_equal(actual, expected)
        elif operator == '<':
            if not _same_order_kind(actual, expected):
                return False
            return actual < expected
        elif operator == '>':
            if not _same_order_kind(actual, expected):
                return False
            return actual > expected
        elif operator == '<=':
            if not _same_order_kind(actual, expected):
                return False
            return actual <= expected
        elif operator == '>=':
            if not _same_order_kind(actual, expected):
                return False
            return actual >= expected

        # Handle regex operators
        elif operator == '~':
            if actual is None or not isinstance(actual, str):
                return False
            try:
                return bool(re.search(expected, actual))
            except re.error:
                return False
        elif operator == '~*':
            if actual is None or not isinstance(actual, str):
                return False
            try:
                return bool(re.search(expected, actual, re.IGNORECASE))
            except re.error:
                return False

        return False

    def _evaluate_condition_group(self, doc: dict, group: ConditionGroup) -> bool:
        """
        Evaluate a condition group (possibly nested) against a document.
        Handles AND/OR logic with proper precedence from parser structure.
        """
        if not group.items:
            return True

        result = None

        for item, logical_op in group.items:
            # Item can be Condition or ConditionGroup
            if isinstance(item, ConditionGroup):
                match = self._evaluate_condition_group(doc, item)
            else:
                match = self._evaluate_condition(doc, item)

            if result is None:
                result = match
            elif logical_op == 'AND':
                result = result and match
            elif logical_op == 'OR':
                result = result or match

        return result if result is not None else False

    def _matches_conditions(self, doc: dict, conditions: ConditionGroup) -> bool:
        """
        Check if document matches conditions.
        conditions is a ConditionGroup (possibly nested).
        """
        return self._evaluate_condition_group(doc, conditions)

    def execute_delete(self, cmd: DeleteCommand) -> dict:
        """
        Execute DELETE command.
        Returns dict with stats about the operation.
        """
        database = cmd.database

        # Check if database exists
        db_response = self._request('GET', database)
        if db_response.status_code == 404:
            raise ExecutorError(f"Database '{database}' not found")
        elif db_response.status_code != 200:
            raise ExecutorError(f"Error accessing database: {db_response.text}")

        # Fetch all documents
        all_docs_response = self._request('GET', f'{database}/_all_docs', params={'include_docs': 'true'})
        if all_docs_response.status_code != 200:
            raise ExecutorError(f"Error fetching documents: {all_docs_response.text}")

        all_docs = all_docs_response.json()
        rows = all_docs.get('rows', [])

        # Filter documents matching conditions
        to_delete = []
        scanned = 0

        for row in rows:
            doc = row.get('doc', {})

            # Skip design documents
            if doc.get('_id', '').startswith('_design/'):
                continue

            scanned += 1

            if self._matches_conditions(doc, cmd.conditions):
                to_delete.append({
                    '_id': doc['_id'],
                    '_rev': doc['_rev'],
                    '_deleted': True
                })

        if not to_delete:
            return {
                'scanned': scanned,
                'matched': 0,
                'deleted': 0,
                'errors': 0
            }

        # Bulk delete
        bulk_response = self._request('POST', f'{database}/_bulk_docs', json={'docs': to_delete})
        if bulk_response.status_code not in (200, 201):
            raise ExecutorError(f"Bulk delete failed: {bulk_response.text}")

        results = bulk_response.json()
        deleted = sum(1 for r in results if r.get('ok'))
        errors = sum(1 for r in results if 'error' in r)

        return {
            'scanned': scanned,
            'matched': len(to_delete),
            'deleted': deleted,
            'errors': errors
        }

    def execute_update(self, cmd: UpdateCommand) -> dict:
        """
        Execute UPDATE command.
        Returns dict with stats about the operation.
        """
        database = cmd.database

        # Check if database exists
        db_response = self._request('GET', database)
        if db_response.status_code == 404:
            raise ExecutorError(f"Database '{database}' not found")
        elif db_response.status_code != 200:
            raise ExecutorError(f"Error accessing database: {db_response.text}")

        # Fetch all documents
        all_docs_response = self._request('GET', f'{database}/_all_docs', params={'include_docs': 'true'})
        if all_docs_response.status_code != 200:
            raise ExecutorError(f"Error fetching documents: {all_docs_response.text}")

        all_docs = all_docs_response.json()
        rows = all_docs.get('rows', [])

        # Filter documents matching conditions and apply updates
        to_update = []
        scanned = 0

        for row in rows:
            doc = row.get('doc', {})

            # Skip design documents
            if doc.get('_id', '').startswith('_design/'):
                continue

            scanned += 1

            if self._matches_conditions(doc, cmd.conditions):
                # Apply assignments to document
                updated_doc = doc.copy()
                for field, value in cmd.assignments.items():
                    updated_doc[field] = value
                to_update.append(updated_doc)

        if not to_update:
            return {
                'scanned': scanned,
                'matched': 0,
                'updated': 0,
                'errors': 0
            }

        # Bulk update
        bulk_response = self._request('POST', f'{database}/_bulk_docs', json={'docs': to_update})
        if bulk_response.status_code not in (200, 201):
            raise ExecutorError(f"Bulk update failed: {bulk_response.text}")

        results = bulk_response.json()
        updated = sum(1 for r in results if r.get('ok'))
        errors = sum(1 for r in results if 'error' in r)

        return {
            'scanned': scanned,
            'matched': len(to_update),
            'updated': updated,
            'errors': errors
        }

    def _analyze_fields(self, documents: list) -> dict:
        """
        Analyze all documents to extract field names and determine types.
        Returns dict of field_name -> sql_type.
        If mixed types found, defaults to TEXT.
        """
        field_types = {}  # field -> set of python types seen

        for doc in documents:
            for field, value in doc.items():
                if field not in field_types:
                    field_types[field] = set()

                if value is None:
                    # NULL doesn't determine type
                    pass
                elif isinstance(value, bool):
                    field_types[field].add('boolean')
                elif isinstance(value, int):
                    field_types[field].add('integer')
                elif isinstance(value, float):
                    field_types[field].add('real')
                elif isinstance(value, str):
                    field_types[field].add('text')
                elif isinstance(value, (dict, list)):
                    field_types[field].add('json')
                else:
                    field_types[field].add('text')

        # Determine final SQL type for each field
        sql_types = {}
        for field, types in field_types.items():
            if not types or 'text' in types:
                sql_types[field] = 'TEXT'
            elif types == {'integer'}:
                sql_types[field] = 'INTEGER'
            elif types == {'real'} or types == {'integer', 'real'}:
                sql_types[field] = 'REAL'
            elif types == {'boolean'}:
                sql_types[field] = 'BOOLEAN'
            elif 'json' in types:
                sql_types[field] = 'TEXT'  # Store JSON as text
            else:
                # Mixed types -> TEXT
                sql_types[field] = 'TEXT'

        return sql_types

    def _export_to_json(self, documents: list, filename: str) -> None:
        """Export documents as JSON array suitable for bulk insert."""
        # Remove _rev field as it's not needed for import
        export_docs = []
        for doc in documents:
            export_doc = {k: v for k, v in doc.items() if k != '_rev'}
            export_docs.append(export_doc)

        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(export_docs, f, indent=2, ensure_ascii=False)

    def _export_to_excel(self, documents: list, filename: str, field_types: dict) -> None:
        """Export documents to Excel format."""
        try:
            from openpyxl import Workbook
        except ImportError:
            raise ExecutorError("openpyxl is required for Excel export. Install with: pip install openpyxl")

        wb = Workbook()
        ws = wb.active

        # Get ordered list of fields (put _id first, skip _rev)
        fields = ['_id'] if '_id' in field_types else []
        fields += sorted([f for f in field_types.keys() if f not in ('_id', '_rev')])

        # Write header row
        for col, field in enumerate(fields, 1):
            ws.cell(row=1, column=col, value=field)

        # Write data rows
        for row_idx, doc in enumerate(documents, 2):
            for col_idx, field in enumerate(fields, 1):
                value = doc.get(field)
                # Convert complex types to JSON string for Excel
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False)
                ws.cell(row=row_idx, column=col_idx, value=value)

        wb.save(filename)

    def _escape_sql_value(self, value) -> str:
        """Escape value for SQL INSERT statement."""
        if value is None:
            return 'NULL'
        elif isinstance(value, bool):
            return 'TRUE' if value else 'FALSE'
        elif isinstance(value, (int, float)):
            return str(value)
        elif isinstance(value, str):
            # Escape single quotes by doubling them
            escaped = value.replace("'", "''")
            return f"'{escaped}'"
        elif isinstance(value, (dict, list)):
            # Convert to JSON string
            json_str = json.dumps(value, ensure_ascii=False).replace("'", "''")
            return f"'{json_str}'"
        else:
            escaped = str(value).replace("'", "''")
            return f"'{escaped}'"

    def _export_to_sql(self, documents: list, filename: str, field_types: dict, table_name: str) -> None:
        """Export documents as SQL CREATE TABLE and INSERT statements."""
        # Get ordered list of fields (put _id first, skip _rev)
        fields = ['_id'] if '_id' in field_types else []
        fields += sorted([f for f in field_types.keys() if f not in ('_id', '_rev')])

        with open(filename, 'w', encoding='utf-8') as f:
            # Write CREATE TABLE statement
            f.write(f"CREATE TABLE {table_name} (\n")
            field_defs = []
            for field in fields:
                sql_type = field_types.get(field, 'TEXT')
                if field == '_id':
                    field_defs.append(f"    {field} {sql_type} PRIMARY KEY")
                else:
                    field_defs.append(f"    {field} {sql_type}")
            f.write(",\n".join(field_defs))
            f.write("\n);\n\n")

            # Write INSERT statements
            for doc in documents:
                values = [self._escape_sql_value(doc.get(field)) for field in fields]
                fields_str = ", ".join(fields)
                values_str = ", ".join(values)
                f.write(f"INSERT INTO {table_name} ({fields_str}) VALUES ({values_str});\n")

    def execute_export(self, cmd: ExportCommand) -> dict:
        """
        Execute EXPORT command.
        Returns dict with stats about the operation.
        """
        database = cmd.database

        # Check if database exists
        db_response = self._request('GET', database)
        if db_response.status_code == 404:
            raise ExecutorError(f"Database '{database}' not found")
        elif db_response.status_code != 200:
            raise ExecutorError(f"Error accessing database: {db_response.text}")

        # Fetch all documents
        all_docs_response = self._request('GET', f'{database}/_all_docs', params={'include_docs': 'true'})
        if all_docs_response.status_code != 200:
            raise ExecutorError(f"Error fetching documents: {all_docs_response.text}")

        all_docs = all_docs_response.json()
        rows = all_docs.get('rows', [])

        # Filter documents matching conditions
        documents = []
        scanned = 0

        for row in rows:
            doc = row.get('doc', {})

            # Skip design documents
            if doc.get('_id', '').startswith('_design/'):
                continue

            scanned += 1

            # If no conditions, export all; otherwise filter
            if not cmd.conditions.items or self._matches_conditions(doc, cmd.conditions):
                documents.append(doc)

        if not documents:
            return {
                'scanned': scanned,
                'matched': 0,
                'exported': 0,
                'filename': cmd.filename,
                'format': cmd.format
            }

        # Analyze field types across all documents
        field_types = self._analyze_fields(documents)

        # Export based on format
        if cmd.format == 'json':
            self._export_to_json(documents, cmd.filename)
        elif cmd.format == 'xls':
            self._export_to_excel(documents, cmd.filename, field_types)
        elif cmd.format == 'sql':
            self._export_to_sql(documents, cmd.filename, field_types, database)

        return {
            'scanned': scanned,
            'matched': len(documents),
            'exported': len(documents),
            'filename': cmd.filename,
            'format': cmd.format,
            'fields': len(field_types)
        }

    def _format_cell_value(self, value) -> str:
        """Format a value for display in ASCII table."""
        if value is None:
            return 'NULL'
        elif isinstance(value, bool):
            return 'true' if value else 'false'
        elif isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        else:
            return str(value)

    def _build_ascii_table(self, fields: list, rows: list) -> str:
        """
        Build an ASCII table with borders.
        fields: list of column names
        rows: list of dicts (documents)
        Returns formatted table string.
        """
        if not fields or not rows:
            return ""

        # Calculate column widths (minimum width is the header length)
        col_widths = {field: len(field) for field in fields}

        # Check all rows to find max width for each column
        for row in rows:
            for field in fields:
                value = self._format_cell_value(row.get(field))
                col_widths[field] = max(col_widths[field], len(value))

        # Build the table
        lines = []

        # Top border
        border_parts = ['─' * (col_widths[f] + 2) for f in fields]
        top_border = '┌' + '┬'.join(border_parts) + '┐'
        mid_border = '├' + '┼'.join(border_parts) + '┤'
        bot_border = '└' + '┴'.join(border_parts) + '┘'

        lines.append(top_border)

        # Header row
        header_cells = [f" {field.ljust(col_widths[field])} " for field in fields]
        lines.append('│' + '│'.join(header_cells) + '│')

        # Separator after header
        lines.append(mid_border)

        # Data rows
        for row in rows:
            cells = []
            for field in fields:
                value = self._format_cell_value(row.get(field))
                # Right-align numbers, left-align everything else
                raw_value = row.get(field)
                if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
                    cells.append(f" {value.rjust(col_widths[field])} ")
                else:
                    cells.append(f" {value.ljust(col_widths[field])} ")
            lines.append('│' + '│'.join(cells) + '│')

        # Bottom border
        lines.append(bot_border)

        return '\n'.join(lines)

    def execute_select(self, cmd: SelectCommand) -> dict:
        """
        Execute SELECT command.
        Returns dict with results and table string for display.
        """
        database = cmd.database

        # Check if database exists
        db_response = self._request('GET', database)
        if db_response.status_code == 404:
            raise ExecutorError(f"Database '{database}' not found")
        elif db_response.status_code != 200:
            raise ExecutorError(f"Error accessing database: {db_response.text}")

        # Fetch all documents
        all_docs_response = self._request('GET', f'{database}/_all_docs', params={'include_docs': 'true'})
        if all_docs_response.status_code != 200:
            raise ExecutorError(f"Error fetching documents: {all_docs_response.text}")

        all_docs = all_docs_response.json()
        rows = all_docs.get('rows', [])

        # Filter documents matching conditions
        documents = []
        scanned = 0

        for row in rows:
            doc = row.get('doc', {})

            # Skip design documents
            if doc.get('_id', '').startswith('_design/'):
                continue

            scanned += 1

            # If no conditions, select all; otherwise filter
            if not cmd.conditions.items or self._matches_conditions(doc, cmd.conditions):
                documents.append(doc)

        # Sort if ORDER BY specified
        if cmd.order_by and documents:
            # Build a sort key function that handles multiple fields with directions
            def sort_key(doc):
                key_parts = []
                for field, direction in cmd.order_by:
                    value = doc.get(field)
                    # None values sort last for ASC, first for DESC
                    is_none = value is None
                    if direction == 'DESC':
                        # For DESC, we need to reverse the sort
                        # None should come last in DESC (so is_none=True sorts after)
                        if is_none:
                            key_parts.append((True, None))
                        elif isinstance(value, (int, float)):
                            key_parts.append((False, -value))
                        elif isinstance(value, str):
                            # Reverse string comparison by inverting characters
                            key_parts.append((False, tuple(-ord(c) for c in value)))
                        else:
                            key_parts.append((False, value))
                    else:
                        # ASC: None sorts last
                        key_parts.append((is_none, value))
                return tuple(key_parts)

            documents.sort(key=sort_key)

        # Apply OFFSET and LIMIT
        total_matched = len(documents)
        if cmd.offset is not None and cmd.offset > 0:
            documents = documents[cmd.offset:]
        if cmd.limit is not None and cmd.limit < len(documents):
            documents = documents[:cmd.limit]

        # Determine fields to display
        if cmd.fields == ['*']:
            # Get all unique fields from all documents, excluding _rev
            all_fields = set()
            for doc in documents:
                all_fields.update(doc.keys())
            all_fields.discard('_rev')
            # Put _id first, then sort the rest
            fields = ['_id'] if '_id' in all_fields else []
            all_fields.discard('_id')
            fields += sorted(all_fields)
        else:
            fields = cmd.fields

        # Build ASCII table
        table = self._build_ascii_table(fields, documents) if documents else ""

        # Determine if results were limited/offset
        has_limit = cmd.limit is not None
        has_offset = cmd.offset is not None and cmd.offset > 0
        is_paginated = has_limit or has_offset

        return {
            'scanned': scanned,
            'matched': total_matched,
            'returned': len(documents),
            'limited': is_paginated and len(documents) < total_matched,
            'offset': cmd.offset if has_offset else 0,
            'fields': fields,
            'rows': documents,
            'table': table
        }

    def execute_count(self, cmd: CountCommand) -> dict:
        """
        Execute COUNT command.
        Returns dict with count result.
        """
        database = cmd.database

        # Check if database exists
        db_response = self._request('GET', database)
        if db_response.status_code == 404:
            raise ExecutorError(f"Database '{database}' not found")
        elif db_response.status_code != 200:
            raise ExecutorError(f"Error accessing database: {db_response.text}")

        # Fetch all documents
        all_docs_response = self._request('GET', f'{database}/_all_docs', params={'include_docs': 'true'})
        if all_docs_response.status_code != 200:
            raise ExecutorError(f"Error fetching documents: {all_docs_response.text}")

        all_docs = all_docs_response.json()
        rows = all_docs.get('rows', [])

        # Filter documents matching conditions
        matched_docs = []
        scanned = 0

        for row in rows:
            doc = row.get('doc', {})

            # Skip design documents
            if doc.get('_id', '').startswith('_design/'):
                continue

            scanned += 1

            # If no conditions, count all; otherwise filter
            if not cmd.conditions.items or self._matches_conditions(doc, cmd.conditions):
                matched_docs.append(doc)

        # Calculate count
        if cmd.distinct_field is None:
            # COUNT(*) - count all matching documents
            count = len(matched_docs)
        else:
            # COUNT(DISTINCT field) - count unique values of the field
            distinct_values = set()
            for doc in matched_docs:
                value = self._get_field_value(doc, cmd.distinct_field)
                # bool is a subclass of int and would collapse with 1/0 in a set.
                if isinstance(value, bool):
                    distinct_values.add(('bool', value))
                    continue
                # Convert unhashable types to string for set membership
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, sort_keys=True)
                distinct_values.add(value)
            count = len(distinct_values)

        return {
            'scanned': scanned,
            'matched': len(matched_docs),
            'count': count,
            'distinct_field': cmd.distinct_field
        }

    def execute_insert(self, cmd: InsertCommand) -> dict:
        """
        Execute INSERT command.
        Returns dict with stats about the operation.
        """
        database = cmd.database

        # Check if database exists
        db_response = self._request('GET', database)
        if db_response.status_code == 404:
            raise ExecutorError(f"Database '{database}' not found")
        elif db_response.status_code != 200:
            raise ExecutorError(f"Error accessing database: {db_response.text}")

        # Prepare documents for bulk insert
        docs = []
        for row in cmd.rows:
            doc = dict(row)  # Copy the row dict
            docs.append(doc)

        # Bulk insert
        bulk_response = self._request('POST', f'{database}/_bulk_docs', json={'docs': docs})
        if bulk_response.status_code not in (200, 201):
            raise ExecutorError(f"Bulk insert failed: {bulk_response.text}")

        results = bulk_response.json()
        inserted = sum(1 for r in results if r.get('ok'))
        errors = sum(1 for r in results if 'error' in r)

        # Collect generated IDs for inserted documents
        inserted_ids = [r.get('id') for r in results if r.get('ok')]

        return {
            'total': len(docs),
            'inserted': inserted,
            'errors': errors,
            'ids': inserted_ids
        }

    def prepare_import(self, cmd: ImportCommand) -> dict:
        """
        Prepare import by parsing file and validating.
        Returns preview info and parsed documents for confirmation.
        """
        database = cmd.database

        # Check if database exists
        db_response = self._request('GET', database)
        if db_response.status_code == 404:
            raise ExecutorError(f"Database '{database}' not found")
        elif db_response.status_code != 200:
            raise ExecutorError(f"Error accessing database: {db_response.text}")

        # Check if file exists
        import os
        if not os.path.exists(cmd.filename):
            raise ExecutorError(f"File not found: {cmd.filename}")

        if cmd.format == 'csv':
            return self._prepare_csv_import(cmd.filename, database)
        elif cmd.format == 'json':
            return self._prepare_json_import(cmd.filename, database)
        else:
            raise ExecutorError(f"Unsupported import format: {cmd.format}")

    def _prepare_csv_import(self, filename: str, database: str) -> dict:
        """Parse CSV file and prepare for import."""
        documents = []
        numeric_fields = []  # Fields ending with _NUM
        final_field_names = {}  # Mapping from CSV header to final field name

        with open(filename, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)

            # Read header line
            try:
                headers = next(reader)
            except StopIteration:
                raise ExecutorError("CSV file is empty")

            if not headers:
                raise ExecutorError("CSV file has no columns")

            # Process headers - identify _NUM fields
            for header in headers:
                header = header.strip()
                if header.endswith('_NUM'):
                    numeric_fields.append(header)
                    final_field_names[header] = header[:-4]  # Strip _NUM
                else:
                    final_field_names[header] = header

            # Read and validate all data rows
            line_number = 1  # Header was line 1
            for row in reader:
                line_number += 1

                if not row or all(cell.strip() == '' for cell in row):
                    continue  # Skip empty rows

                if len(row) != len(headers):
                    raise ExecutorError(
                        f"Line {line_number}: Expected {len(headers)} columns but found {len(row)}\n"
                        f"Content: {','.join(row)}"
                    )

                doc = {}
                for i, header in enumerate(headers):
                    header = header.strip()
                    value = row[i].strip() if i < len(row) else ''
                    final_name = final_field_names[header]

                    if header in numeric_fields:
                        # Validate and convert numeric field
                        if value == '':
                            doc[final_name] = None
                        else:
                            try:
                                if '.' in value:
                                    doc[final_name] = float(value)
                                else:
                                    doc[final_name] = int(value)
                            except ValueError:
                                raise ExecutorError(
                                    f"Line {line_number}: Field '{header}' expects numeric value but got '{value}'\n"
                                    f"Content: {','.join(row)}"
                                )
                    else:
                        # String field
                        doc[final_name] = value if value else None

                documents.append(doc)

        if not documents:
            raise ExecutorError("CSV file has no data rows")

        return {
            'database': database,
            'preview': documents[0],
            'count': len(documents),
            'documents': documents,
            'needs_confirmation': True,
            'format': 'csv'
        }

    def _prepare_json_import(self, filename: str, database: str) -> dict:
        """Parse JSON file and prepare for import."""
        with open(filename, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError as e:
                raise ExecutorError(f"Invalid JSON file: {e}")

        if isinstance(data, list):
            documents = data
        elif isinstance(data, dict):
            # Single document
            documents = [data]
        else:
            raise ExecutorError("JSON file must contain an array of documents or a single document object")

        if not documents:
            raise ExecutorError("JSON file has no documents")

        # Validate all items are dicts
        for i, doc in enumerate(documents):
            if not isinstance(doc, dict):
                raise ExecutorError(f"Document at index {i} is not an object")

        return {
            'database': database,
            'preview': documents[0],
            'count': len(documents),
            'documents': documents,
            'needs_confirmation': True,
            'format': 'json'
        }

    def execute_import_confirmed(self, database: str, documents: list) -> dict:
        """Execute the confirmed import by bulk inserting documents."""
        if not documents:
            return {
                'total': 0,
                'inserted': 0,
                'errors': 0
            }

        # Remove _rev if present (from previous exports)
        docs = []
        for doc in documents:
            clean_doc = {k: v for k, v in doc.items() if k != '_rev'}
            docs.append(clean_doc)

        # Bulk insert
        bulk_response = self._request('POST', f'{database}/_bulk_docs', json={'docs': docs})
        if bulk_response.status_code not in (200, 201):
            raise ExecutorError(f"Bulk insert failed: {bulk_response.text}")

        results = bulk_response.json()
        inserted = sum(1 for r in results if r.get('ok'))
        errors = sum(1 for r in results if 'error' in r)

        return {
            'total': len(docs),
            'inserted': inserted,
            'errors': errors
        }

    def execute_create_database(self, cmd: CreateDatabaseCommand) -> dict:
        """Create a new database."""
        database = cmd.database

        # Check if database already exists
        check_response = self._request('GET', database)
        if check_response.status_code == 200:
            raise ExecutorError(f"Database '{database}' already exists")

        # Create database
        create_response = self._request('PUT', database)
        if create_response.status_code not in (200, 201):
            raise ExecutorError(f"Failed to create database: {create_response.text}")

        return {
            'database': database,
            'created': True
        }

    def execute_show_databases(self, cmd: ShowDatabasesCommand) -> dict:
        """List all databases."""
        response = self._request('GET', '_all_dbs')
        if response.status_code != 200:
            raise ExecutorError(f"Failed to list databases: {response.text}")

        databases = response.json()
        # Filter out system databases (starting with _)
        user_databases = [db for db in databases if not db.startswith('_')]

        return {
            'databases': user_databases,
            'total': len(user_databases)
        }

    def execute(self, cmd) -> dict:
        """Execute a parsed command."""
        if isinstance(cmd, DeleteCommand):
            return self.execute_delete(cmd)

        if isinstance(cmd, UpdateCommand):
            return self.execute_update(cmd)

        if isinstance(cmd, ExportCommand):
            return self.execute_export(cmd)

        if isinstance(cmd, SelectCommand):
            return self.execute_select(cmd)

        if isinstance(cmd, CountCommand):
            return self.execute_count(cmd)

        if isinstance(cmd, InsertCommand):
            return self.execute_insert(cmd)

        if isinstance(cmd, CreateDatabaseCommand):
            return self.execute_create_database(cmd)

        if isinstance(cmd, ShowDatabasesCommand):
            return self.execute_show_databases(cmd)

        raise ExecutorError(f"Unknown command type: {type(cmd)}")
