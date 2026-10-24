# CouchDB SQL

A SQL-like command-line interface for CouchDB operations.

**Version:** 0.2.0 (`__version__` in `main.py`)
**Repository:** https://github.com/brailateo/couchdb_sql

## Overview

This tool provides a familiar SQL-like syntax for performing operations on CouchDB databases. Instead of writing complex HTTP requests or using the Fauxton UI, you can use simple commands like `SELECT`, `INSERT`, `UPDATE`, and `DELETE`.

## Setup

```bash
# Clone repository
git clone https://github.com/brailateo/couchdb_sql.git
cd couchdb_sql

# Install dependencies
pip install -r requirements.txt

# Configure CouchDB connection
cp .env.example .env
# Edit .env with your credentials
```

### Environment Variables (.env)

```
COUCHDB_URL=http://localhost:5984
COUCHDB_USER=admin
COUCHDB_PASS=password
```

## Usage

```bash
python main.py
```

### Multi-line Input

Commands can span multiple lines (like PostgreSQL's `psql`). End with `;` to execute:

```
couchdb> SELECT *
      -> FROM products
      -> WHERE price > 100
      -> ORDER BY name;
```

The prompt changes to `->` while in continuation mode.

## Commands

### SELECT

Query and display documents in a formatted ASCII table.

```sql
SELECT <fields> FROM <database> [WHERE <conditions>] [ORDER BY <field> [ASC|DESC], ...] [LIMIT <n>] [OFFSET <n>]
```

**Features:**
- Use `*` to select all fields, or list specific fields separated by commas
- Optional WHERE clause for filtering (supports regex with `~` and `~*`)
- Optional ORDER BY for sorting results:
  - Multiple fields: `ORDER BY category, price DESC`
  - Direction per field: `ASC` (default) or `DESC`
- Optional LIMIT to restrict number of rows returned
- Optional OFFSET to skip rows (for pagination)
- Results displayed in a bordered ASCII table with:
  - Auto-calculated column widths
  - Right-aligned numbers, left-aligned text
  - Unicode box-drawing characters for borders

**Examples:**
```sql
SELECT * FROM inventar;
SELECT * FROM inventar LIMIT 10;
SELECT * FROM inventar ORDER BY pret DESC LIMIT 10 OFFSET 20;
SELECT _id, tip, nume FROM inventar ORDER BY tip, stoc DESC;
SELECT nume, pret FROM products WHERE pret > 100 ORDER BY pret DESC, nume;
SELECT * FROM products WHERE nume ~ '[Ll]aptop';
SELECT * FROM users WHERE email ~* 'gmail';
```

**Sample output:**
```
┌──────────┬─────┬────────────┬──────┐
│ _id      │ tip │ nume       │ stoc │
├──────────┼─────┼────────────┼──────┤
│ doc001   │ MF  │ Laptop     │   15 │
│ doc002   │ MF  │ Monitor    │    8 │
│ doc003   │ MF  │ Tastatura  │   42 │
└──────────┴─────┴────────────┴──────┘

3 row(s) selected
```

### SELECT COUNT

Count documents or distinct field values.

```sql
SELECT COUNT(*) FROM <database> [WHERE <conditions>]
SELECT COUNT(DISTINCT <field>) FROM <database> [WHERE <conditions>]
```

**Features:**
- `COUNT(*)` counts all documents matching the WHERE conditions
- `COUNT(DISTINCT field)` counts unique values of the specified field
- Supports nested fields with dot notation (e.g., `COUNT(DISTINCT address.city)`)

**Examples:**
```sql
SELECT COUNT(*) FROM users;
SELECT COUNT(*) FROM users WHERE active = true;
SELECT COUNT(DISTINCT category) FROM products;
SELECT COUNT(DISTINCT address.country) FROM customers WHERE status = 'active';
```

**Sample output:**
```
COUNT(*): 42
```
or
```
COUNT(DISTINCT category): 7
```

### INSERT

Insert one or more documents into a database.

```sql
INSERT INTO <database> (field1, field2, ...) VALUES (val1, val2), (val3, val4), ...
```

**Features:**
- Insert multiple documents in a single command
- Field names define the document structure
- Each VALUES tuple creates one document
- Auto-generated `_id` if not specified
- Supports all value types: strings, numbers, booleans, null

**Examples:**
```sql
-- Insert single document
INSERT INTO products (name, price, active) VALUES ('Widget', 9.99, true);

-- Insert multiple documents
INSERT INTO users (name, email, age) VALUES ('Alice', 'alice@example.com', 30), ('Bob', 'bob@example.com', 25);

-- With escaped quotes
INSERT INTO messages (text) VALUES ('It''s a "great" day');

-- With explicit _id
INSERT INTO products (_id, name, price) VALUES ('prod-001', 'Laptop', 999.99);
```

**Sample output:**
```
Inserted: 2 document(s)
IDs: 6f8a9b2c3d4e5f, 7a8b9c0d1e2f3g
```

### UPDATE

Update fields in documents matching conditions.

```sql
UPDATE <database> SET <field>=<value>, ... WHERE <conditions>
```

**Examples:**
```sql
UPDATE inventar SET tip = 'vax', stoc = 3.14 WHERE tip = 'mf' AND stoc = 0;
UPDATE users SET active = true WHERE verified = true AND active = false;
UPDATE products SET discount = 0.15, on_sale = true WHERE category = 'electronics';
```

**Sample output:**
```
Scanned: 1542 documents
Matched: 23 documents
Updated: 23 documents
```

### DELETE

Delete documents matching conditions from a database.

```sql
DELETE FROM <database> WHERE <conditions>
```

**Examples:**
```sql
DELETE FROM inventar WHERE tip = 'MF' AND stoc = 0;
DELETE FROM products WHERE price = 0 OR discontinued = true;
DELETE FROM logs WHERE timestamp < 1704067200;
```

**Sample output:**
```
Scanned: 10000 documents
Matched: 156 documents
Deleted: 156 documents
```

### IMPORT

Import documents from a file into a database.

```sql
IMPORT <filename> INTO <database>
```

**Supported formats:**
- `.csv` - CSV file with header row
- `.json` - JSON array of documents

**CSV Import Features:**
- First line must contain field names (headers)
- Field names ending with `_NUM` are converted to numbers (suffix stripped from final field name)
- Empty values become `null`
- Shows preview of first document before importing
- Validates all rows before importing
- Asks for confirmation before proceeding

**Examples:**
```sql
IMPORT products.csv INTO products;
IMPORT backup.json INTO inventar;
IMPORT 'file with spaces.csv' INTO products;
```

**CSV Example with _NUM convention:**

If your CSV file `products.csv` contains:
```csv
name,price_NUM,stock_NUM,active
Widget,9.99,100,true
Gadget,19.99,50,false
```

The imported documents will be:
```json
{
  "name": "Widget",
  "price": 9.99,
  "stock": 100,
  "active": "true"
}
```

Note: `price_NUM` and `stock_NUM` become `price` and `stock` as numbers.

**Sample interaction:**
```
couchdb> IMPORT products.csv INTO products;

File: products.csv (CSV)
Target database: products

Sample document (first row):
{
  "name": "Widget",
  "price": 9.99,
  "stock": 100
}

2930 document(s) will be imported.

Proceed with import? (yes/no): yes

Imported: 2930 document(s)
```

**Error handling:**
If a `_NUM` field contains a non-numeric value, the import stops with an error:
```
Import error: Line 145: Field 'price_NUM' expects numeric value but got 'N/A'
Content: Widget,N/A,100,true
```

### EXPORT

Export documents to various file formats.

```sql
EXPORT <database> TO <filename> [WHERE <conditions>]
```

**Supported formats:**
- `.xls` / `.xlsx` - Excel spreadsheet (requires `openpyxl`)
- `.json` - JSON array suitable for bulk import
- `.sql` - SQL CREATE TABLE + INSERT statements

**Examples:**
```sql
EXPORT inventar TO inventar.xlsx;
EXPORT inventar TO backup.json WHERE tip = 'MF';
EXPORT products TO products.sql WHERE active = true;
```

**SQL Export Details:**
- Scans all matching documents to determine field names and types
- If a field has mixed types (e.g., integer and string), defaults to TEXT
- Generates CREATE TABLE with appropriate column types (INTEGER, REAL, BOOLEAN, TEXT)
- Table name is the database name
- _id field becomes PRIMARY KEY
- _rev field is excluded from export

### CREATE

Create a new database.

```sql
CREATE <database>
```

**Example:**
```sql
CREATE products;
```

**Sample output:**
```
Database 'products' created successfully.
```

### SHOW DATABASES

List all user databases.

```sql
SHOW DATABASES;
```

**Sample output:**
```
Databases:
  inventar
  products
  users

3 database(s)
```

Note: System databases (starting with `_`) are excluded from the list.

## Syntax Reference

### Conditions (WHERE clause)

| Operator | Description | Example |
|----------|-------------|---------|
| `=` | Equal to | `status = 'active'` |
| `!=` | Not equal to | `type != 'deleted'` |
| `<` | Less than | `price < 100` |
| `>` | Greater than | `stock > 0` |
| `<=` | Less than or equal | `age <= 65` |
| `>=` | Greater than or equal | `rating >= 4.5` |
| `~` | Regex match (case-sensitive) | `name ~ '^John'` |
| `~*` | Regex match (case-insensitive) | `email ~* 'gmail'` |

### Advanced Operators

| Operator | Description | Example |
|----------|-------------|---------|
| `IN` | Value in list | `status IN ('active', 'pending')` |
| `NOT IN` | Value not in list | `type NOT IN ('deleted', 'archived')` |
| `BETWEEN` | Value in range (inclusive) | `price BETWEEN 10 AND 100` |
| `IS NULL` | Value is null/missing | `deleted_at IS NULL` |
| `IS NOT NULL` | Value exists | `email IS NOT NULL` |

### Logical Operators

| Operator | Description |
|----------|-------------|
| `AND` | Both conditions must match |
| `OR` | Either condition must match |
| `(...)` | Parentheses for grouping |

### Nested Field Access

Access fields in nested documents using dot notation:

```sql
-- Single level nesting
WHERE address.city = 'Paris'

-- Multiple levels
WHERE user.profile.settings.theme = 'dark'

-- Combined with other operators
WHERE metadata.tags IN ('featured', 'promoted') AND metadata.score > 80
```

### Parentheses for Complex Logic

Group conditions with parentheses to control evaluation order:

```sql
-- Without parentheses: AND has higher precedence
WHERE a = 1 OR b = 2 AND c = 3    -- Evaluates as: a=1 OR (b=2 AND c=3)

-- With parentheses: explicit grouping
WHERE (a = 1 OR b = 2) AND c = 3  -- Evaluates as: (a=1 OR b=2) AND c=3

-- Complex example
WHERE (status = 'active' OR status = 'pending') AND (price < 100 OR featured = true)
```

### Value Types

| Type | Example |
|------|---------|
| String | `'text'` (preferred) or `"text"` |
| Integer | `123` |
| Float | `123.45` |
| Boolean | `true` / `false` |
| Null | `null` |

### String Quoting (PostgreSQL-style)

Strings should be enclosed in single quotes. To include special characters:

| Character | How to escape | Example |
|-----------|---------------|---------|
| Single quote `'` | Double it `''` | `'Mc''Donald'` → `Mc'Donald` |
| Double quote `"` | Use as-is | `'He said "hello"'` → `He said "hello"` |

**Examples:**
```sql
-- Double quotes inside single quotes (no escaping needed)
WHERE message = 'this is an "EXAMPLE" of quoted string'

-- Single quote inside string (double it)
WHERE name = 'come to Mc''Donald and eat'

-- Combined
WHERE note = 'It''s a "great" day'
```

## Architecture

```
couchdb_sql/
├── .env                 # CouchDB connection config (not in git)
├── .env.example         # Template for .env
├── .gitignore           # Git ignore rules
├── requirements.txt     # Python dependencies
├── README.md            # User documentation
├── CLAUDE.md            # Developer documentation
├── main.py              # REPL entry point using cmd module
├── parser.py            # SQL-like command parser
└── executor.py          # CouchDB HTTP operations
```

### Components

- **parser.py**: Tokenizes and parses SQL-like commands into command objects (SelectCommand, CountCommand, InsertCommand, DeleteCommand, UpdateCommand, ExportCommand, ImportCommand, CreateDatabaseCommand, ShowDatabasesCommand)
- **executor.py**: Executes commands against CouchDB using the REST API with bulk operations; handles file import/export and ASCII table formatting
- **main.py**: Interactive REPL shell using Python's `cmd` module with multi-line input support

## Safety Features

- DELETE and UPDATE without WHERE clause are blocked
- Design documents (`_design/*`) are automatically skipped
- IMPORT shows preview and asks for confirmation before inserting
- Connection test on startup with warning if CouchDB is unreachable

---

## Future Tasks

### DROP Database

Database deletion.

```sql
DROP <database>
```

### SHOW Commands (Extended)

Additional database introspection.

```sql
SHOW FIELDS FROM <database>
SHOW INDEXES FROM <database>
```

### Transaction/Dry-run Mode

Preview changes before executing.

```sql
SET DRY_RUN = true
DELETE FROM inventar WHERE stoc = 0
-- Shows what would be deleted without actually deleting
SET DRY_RUN = false
```

### Batch Mode

Execute commands from a file.

```bash
python main.py --file commands.sql
python main.py -e "DELETE FROM test WHERE x = 1"
```

### Configuration Improvements

- Multiple connection profiles in `.env`
- Command to switch databases/connections at runtime
- SSL/TLS certificate configuration

### Output Formatting

- JSON output mode for SELECT results
- Verbose mode showing HTTP requests
- Quiet mode for scripting
