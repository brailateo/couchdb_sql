"""
SQL-like command parser for CouchDB operations.
Supports:
  SELECT <fields> FROM <database> [WHERE <conditions>] [ORDER BY <fields>] [LIMIT <n>] [OFFSET <n>]
  SELECT COUNT(*) FROM <database> [WHERE <conditions>]
  SELECT COUNT(DISTINCT <field>) FROM <database> [WHERE <conditions>]
  INSERT INTO <database> (field1, field2) VALUES ('val1', 123), ('val2', 456)
  IMPORT <filename> INTO <database>
  DELETE FROM <database> WHERE <conditions>
  UPDATE <database> SET <assignments> WHERE <conditions>
  EXPORT <database> TO <filename> [WHERE <conditions>]
  CREATE <database>
  SHOW DATABASES

WHERE clause features:
  - Comparison: =, !=, <, >, <=, >=
  - Regex: ~ (case-sensitive), ~* (case-insensitive)
  - IN operator: field IN ('a', 'b', 'c')
  - BETWEEN operator: field BETWEEN 10 AND 100
  - NULL checks: field IS NULL, field IS NOT NULL
  - Logical: AND, OR with parentheses for grouping
  - Nested fields: address.city, user.profile.name
"""

import re
from dataclasses import dataclass
from typing import Any, Union, List


@dataclass
class Condition:
    """A single condition like field='value' or field IS NULL."""
    field: str
    operator: str  # =, !=, <, >, <=, >=, ~, ~*, IN, BETWEEN, IS NULL, IS NOT NULL
    value: Any  # Single value, list for IN, tuple (low, high) for BETWEEN, None for IS NULL/IS NOT NULL


@dataclass
class ConditionGroup:
    """A group of conditions combined with AND/OR, possibly nested."""
    items: list  # List of (Condition|ConditionGroup, logical_op) tuples
    # logical_op is None for first item, 'AND' or 'OR' for subsequent


@dataclass
class DeleteCommand:
    database: str
    conditions: ConditionGroup


@dataclass
class UpdateCommand:
    database: str
    assignments: dict
    conditions: ConditionGroup


@dataclass
class ExportCommand:
    database: str
    filename: str
    format: str
    conditions: ConditionGroup  # Can be empty ConditionGroup


@dataclass
class SelectCommand:
    fields: list
    database: str
    conditions: ConditionGroup  # Can be empty ConditionGroup
    order_by: list
    limit: int
    offset: int


@dataclass
class CountCommand:
    database: str
    conditions: ConditionGroup  # Can be empty ConditionGroup
    distinct_field: str  # None for COUNT *, field name for COUNT DISTINCT <field>


@dataclass
class InsertCommand:
    database: str
    fields: list  # List of field names
    rows: list    # List of dicts, each dict is a row with field->value mapping


@dataclass
class ImportCommand:
    filename: str
    database: str
    format: str   # 'csv' or 'json'


@dataclass
class CreateDatabaseCommand:
    database: str


@dataclass
class ShowDatabasesCommand:
    pass


class ParseError(Exception):
    pass


def tokenize_value(value_str: str) -> Any:
    """
    Convert string value to appropriate Python type.
    Supports PostgreSQL-style escaping: '' becomes ' inside single-quoted strings.
    Double quotes inside single-quoted strings are allowed as-is.
    """
    value_str = value_str.strip()

    # Single-quoted string (PostgreSQL style: '' escapes to ')
    if value_str.startswith("'") and value_str.endswith("'") and len(value_str) >= 2:
        inner = value_str[1:-1]
        # Replace escaped single quotes ('') with single quote (')
        return inner.replace("''", "'")

    # Double-quoted string (kept for backward compatibility)
    if value_str.startswith('"') and value_str.endswith('"') and len(value_str) >= 2:
        return value_str[1:-1]

    # Boolean
    if value_str.lower() == 'true':
        return True
    if value_str.lower() == 'false':
        return False

    # Null
    if value_str.lower() == 'null':
        return None

    # Number
    try:
        if '.' in value_str:
            return float(value_str)
        return int(value_str)
    except ValueError:
        pass

    # Unquoted string (treat as string)
    return value_str


def parse_in_values(values_str: str) -> list:
    """
    Parse the values list for IN operator: ('a', 'b', 123).
    Supports PostgreSQL-style escaping: '' inside single quotes.
    """
    values_str = values_str.strip()
    if not values_str.startswith('(') or not values_str.endswith(')'):
        raise ParseError(f"IN values must be enclosed in parentheses: {values_str}")

    inner = values_str[1:-1].strip()
    if not inner:
        raise ParseError("IN operator requires at least one value")

    # Parse comma-separated values, respecting quotes and '' escapes
    values = []
    current = ""
    in_quotes = False
    quote_char = None
    i = 0

    while i < len(inner):
        char = inner[i]

        if char in ('"', "'") and not in_quotes:
            # Starting a quoted string
            in_quotes = True
            quote_char = char
            current += char
            i += 1
        elif char == quote_char and in_quotes:
            # Check for escaped quote ('')
            if char == "'" and i + 1 < len(inner) and inner[i + 1] == "'":
                # Escaped single quote - include both
                current += "''"
                i += 2
            else:
                # End of quoted string
                in_quotes = False
                quote_char = None
                current += char
                i += 1
        elif char == ',' and not in_quotes:
            if current.strip():
                values.append(tokenize_value(current.strip()))
            current = ""
            i += 1
        else:
            current += char
            i += 1

    if current.strip():
        values.append(tokenize_value(current.strip()))

    return values


def parse_single_condition(condition_str: str) -> Condition:
    """Parse a single condition (no AND/OR/parentheses)."""
    condition_str = condition_str.strip()

    # Check for IS NULL / IS NOT NULL
    is_null_match = re.match(r'^([\w.\-]+)\s+IS\s+NULL\s*$', condition_str, re.IGNORECASE)
    if is_null_match:
        return Condition(field=is_null_match.group(1), operator='IS NULL', value=None)

    is_not_null_match = re.match(r'^([\w.\-]+)\s+IS\s+NOT\s+NULL\s*$', condition_str, re.IGNORECASE)
    if is_not_null_match:
        return Condition(field=is_not_null_match.group(1), operator='IS NOT NULL', value=None)

    # Check for BETWEEN. The bounds' AND must be outside quotes so a
    # bound can itself contain the word AND.
    between_head = re.match(r'^([\w.\-]+)\s+BETWEEN\s+', condition_str, re.IGNORECASE)
    if between_head:
        rest = condition_str[between_head.end():]
        and_at = _find_keyword_outside_quotes(rest, 'AND')
        if and_at < 0:
            raise ParseError(f"Invalid condition: {condition_str}")
        low_text = rest[:and_at].strip()
        high_text = rest[and_at + len('AND'):].strip()
        if not low_text or not high_text:
            raise ParseError(f"Invalid condition: {condition_str}")
        low = tokenize_value(low_text)
        high = tokenize_value(high_text)
        return Condition(field=between_head.group(1), operator='BETWEEN', value=(low, high))

    # Check for IN
    in_match = re.match(r'^([\w.\-]+)\s+IN\s*(\(.+\))\s*$', condition_str, re.IGNORECASE)
    if in_match:
        field = in_match.group(1)
        values = parse_in_values(in_match.group(2))
        return Condition(field=field, operator='IN', value=values)

    # Check for NOT IN
    not_in_match = re.match(r'^([\w.\-]+)\s+NOT\s+IN\s*(\(.+\))\s*$', condition_str, re.IGNORECASE)
    if not_in_match:
        field = not_in_match.group(1)
        values = parse_in_values(not_in_match.group(2))
        return Condition(field=field, operator='NOT IN', value=values)

    # Standard comparison operators (including regex)
    # Field names can include dots for nested access
    pattern = r'^\s*([\w.\-]+)\s*(~\*|~|!=|<=|>=|=|<|>)\s*(.+?)\s*$'
    match = re.match(pattern, condition_str)

    if not match:
        raise ParseError(f"Invalid condition: {condition_str}")

    field = match.group(1)
    operator = match.group(2)
    value = tokenize_value(match.group(3))

    return Condition(field=field, operator=operator, value=value)


def _skip_quoted_string(text: str, index: int):
    """If text[index] opens a quote, return the index just after the closer.

    Single quotes use PostgreSQL '' escapes. Returns None when text[index]
    is not a quote. Raises ParseError when the closer is missing.
    """
    if index >= len(text) or text[index] not in ("'", '"'):
        return None

    quote = text[index]
    i = index + 1
    length = len(text)
    while i < length:
        if text[i] == quote:
            if quote == "'" and i + 1 < length and text[i + 1] == "'":
                i += 2
                continue
            return i + 1
        i += 1
    raise ParseError("Unclosed quote")


def _find_keyword_outside_quotes(text: str, keyword: str):
    """Return the index of keyword outside quotes, or -1.

    The keyword must be a whole word (not part of an identifier).
    """
    kw = keyword.upper()
    klen = len(keyword)
    i = 0
    length = len(text)
    while i < length:
        closed = _skip_quoted_string(text, i)
        if closed is not None:
            i = closed
            continue
        if text[i:i + klen].upper() == kw:
            before_ok = i == 0 or text[i - 1].isspace()
            after = i + klen
            after_ok = after == length or not (text[after].isalnum() or text[after] == '_')
            if before_ok and after_ok:
                return i
        i += 1
    return -1


class WhereParser:
    """Recursive descent parser for WHERE clauses with parentheses support."""

    def __init__(self, where_str: str):
        self.original = where_str
        self.pos = 0
        self.length = len(where_str)

    def parse(self) -> ConditionGroup:
        """Parse the entire WHERE clause."""
        if not self.original.strip():
            return ConditionGroup(items=[])

        result = self.parse_expression()
        self.skip_whitespace()

        if self.pos < self.length:
            raise ParseError(f"Unexpected characters at position {self.pos}: {self.original[self.pos:]}")

        return result

    def skip_whitespace(self):
        """Skip whitespace characters."""
        while self.pos < self.length and self.original[self.pos].isspace():
            self.pos += 1

    def peek(self, length=1) -> str:
        """Look ahead without consuming."""
        return self.original[self.pos:self.pos + length]

    def match_keyword(self, keyword: str) -> bool:
        """Check if the next token matches a keyword (case-insensitive)."""
        self.skip_whitespace()
        if self.pos + len(keyword) > self.length:
            return False

        potential = self.original[self.pos:self.pos + len(keyword)]
        if potential.upper() != keyword.upper():
            return False

        # Make sure it's a complete word (followed by space, paren, or end)
        next_pos = self.pos + len(keyword)
        if next_pos < self.length:
            next_char = self.original[next_pos]
            if next_char.isalnum() or next_char == '_':
                return False

        return True

    def consume_keyword(self, keyword: str):
        """Consume a keyword."""
        self.skip_whitespace()
        self.pos += len(keyword)

    def parse_expression(self) -> ConditionGroup:
        """Parse an expression (handles OR at lowest precedence)."""
        return self.parse_or_expression()

    def parse_or_expression(self) -> ConditionGroup:
        """Parse OR expressions."""
        left = self.parse_and_expression()
        items = [(left, None)]

        while self.match_keyword('OR'):
            self.consume_keyword('OR')
            right = self.parse_and_expression()
            items.append((right, 'OR'))

        if len(items) == 1 and isinstance(items[0][0], ConditionGroup):
            return items[0][0]

        return ConditionGroup(items=items)

    def parse_and_expression(self) -> ConditionGroup:
        """Parse AND expressions."""
        left = self.parse_primary()
        items = [(left, None)]

        while self.match_keyword('AND'):
            self.consume_keyword('AND')
            right = self.parse_primary()
            items.append((right, 'AND'))

        if len(items) == 1:
            if isinstance(items[0][0], ConditionGroup):
                return items[0][0]
            # Single condition - wrap in group
            return ConditionGroup(items=items)

        return ConditionGroup(items=items)

    def parse_primary(self) -> Union[Condition, ConditionGroup]:
        """Parse a primary expression (condition or parenthesized group)."""
        self.skip_whitespace()

        if self.pos >= self.length:
            raise ParseError("Unexpected end of WHERE clause")

        # Check for opening parenthesis
        if self.peek() == '(':
            self.pos += 1  # consume '('
            expr = self.parse_expression()
            self.skip_whitespace()
            if self.pos >= self.length or self.peek() != ')':
                raise ParseError("Missing closing parenthesis")
            self.pos += 1  # consume ')'
            return expr

        # Parse a single condition
        return self.parse_condition()

    def parse_condition(self) -> Condition:
        """Parse a single condition."""
        self.skip_whitespace()
        start_pos = self.pos

        # Find the end of this condition (before AND, OR, or closing paren).
        # Quoted text is opaque: AND, OR, WHERE, and parentheses inside
        # strings are literal, and a missing closer is an error.
        depth = 0
        end_pos = self.pos
        in_between = False  # Track if we've seen BETWEEN but not its AND yet

        while end_pos < self.length:
            closed = _skip_quoted_string(self.original, end_pos)
            if closed is not None:
                end_pos = closed
                continue

            char = self.original[end_pos]

            if char == '(':
                depth += 1
                end_pos += 1
            elif char == ')':
                if depth == 0:
                    break
                depth -= 1
                end_pos += 1
            elif depth == 0:
                remaining = self.original[end_pos:]

                # Check for BETWEEN keyword - the next AND will be part of it
                if re.match(r'^\s+BETWEEN\s+', remaining, re.IGNORECASE):
                    in_between = True
                    end_pos += 1
                    continue

                # Check for AND keyword
                if re.match(r'^\s+AND\s+', remaining, re.IGNORECASE):
                    if in_between:
                        # This AND is part of BETWEEN...AND, not a logical operator
                        in_between = False
                        end_pos += 1
                        continue
                    break

                # Check for OR keyword
                if re.match(r'^\s+OR\s+', remaining, re.IGNORECASE):
                    break

                end_pos += 1
            else:
                end_pos += 1

        condition_str = self.original[start_pos:end_pos].strip()
        if not condition_str:
            raise ParseError(f"Empty condition at position {start_pos}")

        self.pos = end_pos
        return parse_single_condition(condition_str)


def parse_where_clause(where_str: str) -> ConditionGroup:
    """Parse WHERE clause with full support for parentheses and all operators."""
    parser = WhereParser(where_str)
    return parser.parse()


def parse_delete(sql: str) -> DeleteCommand:
    """Parse DELETE command."""
    pattern = r'^\s*DELETE\s+FROM\s+([\w$()+-]+)(?:\s+WHERE\s+(.+))?\s*$'
    match = re.match(pattern, sql.rstrip(';'), re.IGNORECASE)

    if not match:
        raise ParseError("Invalid DELETE syntax. Expected: DELETE FROM <database> WHERE <conditions>")

    database = match.group(1)
    where_clause = match.group(2) or ""

    conditions = parse_where_clause(where_clause)

    if not conditions.items:
        raise ParseError("DELETE without WHERE clause is not allowed (safety measure)")

    return DeleteCommand(database=database, conditions=conditions)


def parse_set_clause(set_str: str) -> dict:
    """
    Parse SET clause like: field1='value1',field2=123.
    Supports PostgreSQL-style escaping: '' inside single quotes.
    """
    if not set_str.strip():
        return {}

    assignments = {}
    # Parse manually to handle '' escapes properly
    i = 0
    while i < len(set_str):
        # Skip whitespace
        while i < len(set_str) and set_str[i].isspace():
            i += 1
        if i >= len(set_str):
            break

        # Parse field name
        field_start = i
        while i < len(set_str) and (set_str[i].isalnum() or set_str[i] in '_.-'):
            i += 1
        field = set_str[field_start:i].strip()

        if not field:
            raise ParseError(f"Invalid SET clause: expected field name at position {i}")

        # Skip whitespace and '='
        while i < len(set_str) and set_str[i].isspace():
            i += 1
        if i >= len(set_str) or set_str[i] != '=':
            raise ParseError(f"Invalid SET clause: expected '=' after field '{field}'")
        i += 1  # skip '='
        while i < len(set_str) and set_str[i].isspace():
            i += 1

        # Parse value
        if i >= len(set_str):
            raise ParseError(f"Invalid SET clause: expected value for field '{field}'")

        value_start = i
        if set_str[i] == "'":
            # Single-quoted string with '' escape support
            i += 1  # skip opening quote
            while i < len(set_str):
                if set_str[i] == "'":
                    if i + 1 < len(set_str) and set_str[i + 1] == "'":
                        i += 2  # skip escaped quote
                    else:
                        i += 1  # skip closing quote
                        break
                else:
                    i += 1
            value = set_str[value_start:i]
        elif set_str[i] == '"':
            # Double-quoted string
            i += 1
            while i < len(set_str) and set_str[i] != '"':
                i += 1
            i += 1  # skip closing quote
            value = set_str[value_start:i]
        else:
            # Unquoted value - read until comma or end
            while i < len(set_str) and set_str[i] != ',':
                i += 1
            value = set_str[value_start:i].strip()

        assignments[field] = tokenize_value(value.strip())

        # Skip whitespace and comma
        while i < len(set_str) and set_str[i].isspace():
            i += 1
        if i < len(set_str) and set_str[i] == ',':
            i += 1

    if not assignments:
        raise ParseError(f"Invalid SET clause: {set_str}")

    return assignments


def parse_update(sql: str) -> UpdateCommand:
    """Parse UPDATE command."""
    sql = sql.rstrip(';').strip()

    head = re.match(r'^\s*UPDATE\s+([\w$()+-]+)\s+SET\s+', sql, re.IGNORECASE)
    if not head:
        raise ParseError("Invalid UPDATE syntax. Expected: UPDATE <database> SET <field>=<value>,... WHERE <conditions>")

    database = head.group(1)
    rest = sql[head.end():]
    # Split on WHERE outside quotes so a string value can contain the word.
    where_at = _find_keyword_outside_quotes(rest, 'WHERE')
    if where_at < 0:
        raise ParseError("Invalid UPDATE syntax. Expected: UPDATE <database> SET <field>=<value>,... WHERE <conditions>")

    set_clause = rest[:where_at].strip()
    where_clause = rest[where_at + len('WHERE'):].strip()

    assignments = parse_set_clause(set_clause)
    conditions = parse_where_clause(where_clause)

    if not assignments:
        raise ParseError("UPDATE requires at least one field assignment in SET clause")

    if not conditions.items:
        raise ParseError("UPDATE without WHERE clause is not allowed (safety measure)")

    return UpdateCommand(database=database, assignments=assignments, conditions=conditions)


def parse_export(sql: str) -> ExportCommand:
    """Parse EXPORT command."""
    sql = sql.rstrip(';').strip()

    pattern = r'^\s*EXPORT\s+([\w$()+-]+)\s+TO\s+([^\s]+)(?:\s+WHERE\s+(.+))?\s*$'
    match = re.match(pattern, sql, re.IGNORECASE)

    if not match:
        raise ParseError("Invalid EXPORT syntax. Expected: EXPORT <database> TO <filename> [WHERE <conditions>]")

    database = match.group(1)
    filename = match.group(2).strip('"\'')
    where_clause = match.group(3) or ""

    lower_filename = filename.lower()
    if lower_filename.endswith('.xls') or lower_filename.endswith('.xlsx'):
        export_format = 'xls'
    elif lower_filename.endswith('.json'):
        export_format = 'json'
    elif lower_filename.endswith('.sql'):
        export_format = 'sql'
    else:
        raise ParseError("Unsupported export format. Use .xls, .xlsx, .json, or .sql extension")

    conditions = parse_where_clause(where_clause)

    return ExportCommand(database=database, filename=filename, format=export_format, conditions=conditions)


def parse_order_by(order_by_str: str) -> list:
    """Parse ORDER BY clause with multiple fields and ASC/DESC."""
    if not order_by_str.strip():
        return []

    result = []
    parts = order_by_str.split(',')

    for part in parts:
        part = part.strip()
        if not part:
            continue

        tokens = part.split()
        if len(tokens) == 1:
            result.append((tokens[0], 'ASC'))
        elif len(tokens) == 2:
            field = tokens[0]
            direction = tokens[1].upper()
            if direction not in ('ASC', 'DESC'):
                raise ParseError(f"Invalid ORDER BY direction: {tokens[1]}. Use ASC or DESC.")
            result.append((field, direction))
        else:
            raise ParseError(f"Invalid ORDER BY clause: {part}")

    return result


def parse_select(sql: str) -> Union[SelectCommand, CountCommand]:
    """Parse SELECT command, including SELECT COUNT(*) and SELECT COUNT(DISTINCT field)."""
    sql = sql.rstrip(';').strip()

    # Check for SELECT COUNT(DISTINCT field) FROM ...
    count_distinct_pattern = r'^\s*SELECT\s+COUNT\s*\(\s*DISTINCT\s+([\w.\-]+)\s*\)\s+FROM\s+([\w$()+-]+)(?:\s+WHERE\s+(.+))?\s*$'
    match = re.match(count_distinct_pattern, sql, re.IGNORECASE)
    if match:
        distinct_field = match.group(1)
        database = match.group(2)
        where_clause = match.group(3) or ""
        conditions = parse_where_clause(where_clause)
        return CountCommand(database=database, conditions=conditions, distinct_field=distinct_field)

    # Check for SELECT COUNT(*) FROM ...
    count_all_pattern = r'^\s*SELECT\s+COUNT\s*\(\s*\*\s*\)\s+FROM\s+([\w$()+-]+)(?:\s+WHERE\s+(.+))?\s*$'
    match = re.match(count_all_pattern, sql, re.IGNORECASE)
    if match:
        database = match.group(1)
        where_clause = match.group(2) or ""
        conditions = parse_where_clause(where_clause)
        return CountCommand(database=database, conditions=conditions, distinct_field=None)

    # SELECT COUNT that did not match the forms above (for example COUNT(*) ... LIMIT)
    # must not fall through to a normal SELECT of a column named COUNT(*).
    if re.match(r'^\s*SELECT\s+COUNT\b', sql, re.IGNORECASE):
        raise ParseError(
            "Invalid SELECT COUNT syntax. Expected: "
            "SELECT COUNT(*) FROM <database> [WHERE <conditions>] or "
            "SELECT COUNT(DISTINCT <field>) FROM <database> [WHERE <conditions>]. "
            "ORDER BY, LIMIT, and OFFSET are not supported on COUNT."
        )

    # Standard SELECT
    pattern = r'^\s*SELECT\s+(.+?)\s+FROM\s+([\w$()+-]+)(?:\s+WHERE\s+(.+?))?(?:\s+ORDER\s+BY\s+(.+?))?(?:\s+LIMIT\s+(\d+))?(?:\s+OFFSET\s+(\d+))?\s*$'
    match = re.match(pattern, sql, re.IGNORECASE)

    if not match:
        raise ParseError("Invalid SELECT syntax. Expected: SELECT <fields> FROM <database> [WHERE <conditions>] [ORDER BY <field> [ASC|DESC], ...] [LIMIT <n>] [OFFSET <n>]")

    fields_str = match.group(1).strip()
    database = match.group(2)
    where_clause = match.group(3) or ""
    order_by_str = match.group(4) or ""
    limit_str = match.group(5)
    offset_str = match.group(6)

    if fields_str == '*':
        fields = ['*']
    else:
        fields = [f.strip() for f in fields_str.split(',')]
        if not all(fields):
            raise ParseError("Invalid field list in SELECT")

    conditions = parse_where_clause(where_clause)
    order_by = parse_order_by(order_by_str) if order_by_str else []
    limit = int(limit_str) if limit_str else None
    offset = int(offset_str) if offset_str else None

    return SelectCommand(fields=fields, database=database, conditions=conditions, order_by=order_by, limit=limit, offset=offset)


def parse_count(sql: str) -> CountCommand:
    """
    Parse COUNT command.
    Formats:
      COUNT * FROM <database> [WHERE <conditions>]
      COUNT DISTINCT <field> FROM <database> [WHERE <conditions>]
    """
    sql = sql.rstrip(';').strip()

    # Try COUNT DISTINCT first
    distinct_pattern = r'^\s*COUNT\s+DISTINCT\s+([\w.\-]+)\s+FROM\s+([\w$()+-]+)(?:\s+WHERE\s+(.+))?\s*$'
    match = re.match(distinct_pattern, sql, re.IGNORECASE)

    if match:
        distinct_field = match.group(1)
        database = match.group(2)
        where_clause = match.group(3) or ""
        conditions = parse_where_clause(where_clause)
        return CountCommand(database=database, conditions=conditions, distinct_field=distinct_field)

    # Try COUNT *
    count_all_pattern = r'^\s*COUNT\s+\*\s+FROM\s+([\w$()+-]+)(?:\s+WHERE\s+(.+))?\s*$'
    match = re.match(count_all_pattern, sql, re.IGNORECASE)

    if match:
        database = match.group(1)
        where_clause = match.group(2) or ""
        conditions = parse_where_clause(where_clause)
        return CountCommand(database=database, conditions=conditions, distinct_field=None)

    raise ParseError("Invalid COUNT syntax. Expected: COUNT * FROM <database> [WHERE ...] or COUNT DISTINCT <field> FROM <database> [WHERE ...]")


def parse_values_list(values_str: str) -> list:
    """
    Parse a single VALUES tuple like ('value1', 123, true).
    Returns list of values.
    Supports PostgreSQL-style '' escaping.
    """
    values_str = values_str.strip()
    if not values_str.startswith('(') or not values_str.endswith(')'):
        raise ParseError(f"VALUES tuple must be enclosed in parentheses: {values_str}")

    inner = values_str[1:-1].strip()
    if not inner:
        return []

    # Parse comma-separated values, respecting quotes and '' escapes
    values = []
    current = ""
    in_quotes = False
    quote_char = None
    i = 0

    while i < len(inner):
        char = inner[i]

        if char in ('"', "'") and not in_quotes:
            in_quotes = True
            quote_char = char
            current += char
            i += 1
        elif char == quote_char and in_quotes:
            if char == "'" and i + 1 < len(inner) and inner[i + 1] == "'":
                current += "''"
                i += 2
            else:
                in_quotes = False
                quote_char = None
                current += char
                i += 1
        elif char == ',' and not in_quotes:
            values.append(tokenize_value(current.strip()))
            current = ""
            i += 1
        else:
            current += char
            i += 1

    if current.strip():
        values.append(tokenize_value(current.strip()))

    return values


def parse_insert(sql: str) -> InsertCommand:
    """
    Parse INSERT command.
    Format: INSERT INTO <database> (field1, field2) VALUES ('val1', 123), ('val2', 456)
    """
    sql = sql.rstrip(';').strip()

    # Match: INSERT INTO <database> (fields) VALUES ...
    pattern = r'^\s*INSERT\s+INTO\s+([\w$()+-]+)\s*\(([^)]+)\)\s*VALUES\s*(.+)\s*$'
    match = re.match(pattern, sql, re.IGNORECASE)

    if not match:
        raise ParseError("Invalid INSERT syntax. Expected: INSERT INTO <database> (field1, field2, ...) VALUES ('val1', val2), ...")

    database = match.group(1)
    fields_str = match.group(2)
    values_part = match.group(3)

    # Parse field names
    fields = [f.strip() for f in fields_str.split(',')]
    if not all(fields):
        raise ParseError("Invalid field list in INSERT")

    # Parse multiple value tuples: (v1, v2), (v3, v4), ...
    # Need to handle nested parentheses and quoted strings
    rows = []
    i = 0
    while i < len(values_part):
        # Skip whitespace and commas between tuples
        while i < len(values_part) and values_part[i] in ' \t\n,':
            i += 1

        if i >= len(values_part):
            break

        if values_part[i] != '(':
            raise ParseError(f"Expected '(' at position {i} in VALUES clause")

        # Find matching closing parenthesis
        depth = 0
        start = i
        in_quotes = False
        quote_char = None

        while i < len(values_part):
            char = values_part[i]

            if char in ('"', "'") and not in_quotes:
                in_quotes = True
                quote_char = char
            elif char == quote_char and in_quotes:
                if char == "'" and i + 1 < len(values_part) and values_part[i + 1] == "'":
                    i += 1  # skip escaped quote
                else:
                    in_quotes = False
                    quote_char = None
            elif not in_quotes:
                if char == '(':
                    depth += 1
                elif char == ')':
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
            i += 1

        tuple_str = values_part[start:i].strip()
        values = parse_values_list(tuple_str)

        if len(values) != len(fields):
            raise ParseError(f"VALUES tuple has {len(values)} values but {len(fields)} fields specified")

        # Create row dict
        row = dict(zip(fields, values))
        rows.append(row)

    if not rows:
        raise ParseError("INSERT requires at least one VALUES tuple")

    return InsertCommand(database=database, fields=fields, rows=rows)


def parse_import(sql: str) -> ImportCommand:
    """
    Parse IMPORT command.
    Format: IMPORT <filename> INTO <database>
    Filename can be quoted if it contains spaces.
    """
    sql = sql.rstrip(';').strip()

    # Try quoted filename first
    pattern_quoted = r'^\s*IMPORT\s+["\']([^"\']+)["\']\s+INTO\s+([\w$()+-]+)\s*$'
    match = re.match(pattern_quoted, sql, re.IGNORECASE)

    if not match:
        # Try unquoted filename
        pattern = r'^\s*IMPORT\s+([^\s]+)\s+INTO\s+([\w$()+-]+)\s*$'
        match = re.match(pattern, sql, re.IGNORECASE)

    if not match:
        raise ParseError("Invalid IMPORT syntax. Expected: IMPORT <filename> INTO <database>")

    filename = match.group(1)
    database = match.group(2)

    # Determine format from extension
    lower_filename = filename.lower()
    if lower_filename.endswith('.csv'):
        import_format = 'csv'
    elif lower_filename.endswith('.json'):
        import_format = 'json'
    else:
        raise ParseError("Unsupported import format. Use .csv or .json extension")

    return ImportCommand(filename=filename, database=database, format=import_format)


def parse_create(sql: str) -> CreateDatabaseCommand:
    """
    Parse CREATE command.
    Format: CREATE <database>
    """
    sql = sql.rstrip(';').strip()

    pattern = r'^\s*CREATE\s+([\w$()+-]+)\s*$'
    match = re.match(pattern, sql, re.IGNORECASE)

    if not match:
        raise ParseError("Invalid CREATE syntax. Expected: CREATE <database>")

    database = match.group(1)
    return CreateDatabaseCommand(database=database)


def parse_show(sql: str) -> ShowDatabasesCommand:
    """
    Parse SHOW command.
    Format: SHOW DATABASES
    """
    sql = sql.rstrip(';').strip()

    pattern = r'^\s*SHOW\s+DATABASES\s*$'
    match = re.match(pattern, sql, re.IGNORECASE)

    if not match:
        raise ParseError("Invalid SHOW syntax. Expected: SHOW DATABASES")

    return ShowDatabasesCommand()


def parse(sql: str):
    """Parse SQL-like command and return appropriate command object."""
    sql = sql.strip()

    if not sql:
        return None

    upper_sql = sql.upper()

    if upper_sql.startswith('DELETE'):
        return parse_delete(sql)

    if upper_sql.startswith('UPDATE'):
        return parse_update(sql)

    if upper_sql.startswith('EXPORT'):
        return parse_export(sql)

    if upper_sql.startswith('SELECT'):
        return parse_select(sql)

    if upper_sql.startswith('INSERT'):
        return parse_insert(sql)

    if upper_sql.startswith('IMPORT'):
        return parse_import(sql)

    if upper_sql.startswith('CREATE'):
        return parse_create(sql)

    if upper_sql.startswith('SHOW'):
        return parse_show(sql)

    raise ParseError("Unknown command. Supported: SELECT, INSERT, DELETE, UPDATE, EXPORT, IMPORT, CREATE, SHOW")
