# CouchDB SQL

A SQL-like command-line interface for CouchDB operations.

## Overview

CouchDB SQL provides a familiar SQL-like syntax for performing operations on CouchDB databases. Instead of writing complex HTTP requests or using the Fauxton UI, you can use simple commands like `SELECT`, `INSERT`, `UPDATE`, and `DELETE`.

## Features

- **SQL-like syntax** - Familiar commands for developers coming from SQL databases
- **Interactive REPL** - Command-line interface with history and auto-completion
- **Rich querying** - WHERE clauses with comparisons, regex, IN, BETWEEN, IS NULL
- **Nested field support** - Access nested documents with dot notation (`address.city`)
- **Data import/export** - Support for CSV, JSON, Excel, and SQL formats
- **Bulk operations** - Efficient batch inserts, updates, and deletes
- **Safe by default** - DELETE and UPDATE require WHERE clause

## Installation

### Prerequisites

- Python 3.7+
- CouchDB server (local or remote)

### Setup

```bash
# Clone the repository
git clone https://github.com/brailateo/couchdb_sql.git
cd couchdb_sql

# Install dependencies
pip install -r requirements.txt

# Configure CouchDB connection
cp .env.example .env
# Edit .env with your credentials
```

### Configuration

Edit `.env` file with your CouchDB connection details:

```
COUCHDB_URL=http://localhost:5984
COUCHDB_USER=admin
COUCHDB_PASS=password
```

## Usage

Start the interactive shell:

```bash
python main.py
```

You'll see the prompt:

```
CouchDB SQL Interface
Type SQL-like commands or 'help' for assistance.
Type 'quit' or 'exit' to leave.

couchdb>
```

### Multi-line Input

Commands can span multiple lines, like in PostgreSQL's `psql`. The command is executed when you end a line with `;`:

```
couchdb> SELECT *
      -> FROM products
      -> WHERE price > 100
      -> ORDER BY name;
```

The prompt changes to `->` to indicate continuation mode.

## Commands

### SELECT

Query documents and display results in a formatted ASCII table.

```sql
SELECT <fields> FROM <database> [WHERE <conditions>] [ORDER BY <field> [ASC|DESC], ...] [LIMIT <n>] [OFFSET <n>]
```

**Examples:**

```sql
-- Select all documents
SELECT * FROM products

-- Select specific fields
SELECT name, price, stock FROM products

-- Filter with WHERE
SELECT * FROM users WHERE active = true

-- Multiple conditions
SELECT * FROM orders WHERE status = 'pending' AND total > 100

-- Regex matching (case-sensitive)
SELECT * FROM products WHERE name ~ '^Model'

-- Regex matching (case-insensitive)
SELECT * FROM users WHERE email ~* 'gmail'

-- IN operator
SELECT * FROM products WHERE category IN ('electronics', 'computers')

-- BETWEEN operator
SELECT * FROM products WHERE price BETWEEN 10 AND 100

-- NULL checks
SELECT * FROM users WHERE deleted_at IS NULL

-- Nested fields
SELECT * FROM orders WHERE shipping.address.city = 'Paris'

-- Sorting
SELECT * FROM products ORDER BY price DESC, name ASC

-- Pagination
SELECT * FROM products ORDER BY created_at DESC LIMIT 10 OFFSET 20

-- Complex conditions with parentheses
SELECT * FROM items WHERE (type = 'A' OR type = 'B') AND active = true
```

**Sample output:**

```
┌──────────┬─────────────┬───────┬──────┐
│ _id      │ name        │ price │ stock│
├──────────┼─────────────┼───────┼──────┤
│ prod-001 │ Laptop      │   999 │   15 │
│ prod-002 │ Mouse       │    29 │  142 │
│ prod-003 │ Keyboard    │    79 │   38 │
└──────────┴─────────────┴───────┴──────┘

3 row(s) selected
```

### SELECT COUNT

Count documents or distinct values.

```sql
SELECT COUNT(*) FROM <database> [WHERE <conditions>]
SELECT COUNT(DISTINCT <field>) FROM <database> [WHERE <conditions>]
```

**Examples:**

```sql
-- Count all documents
SELECT COUNT(*) FROM users

-- Count with condition
SELECT COUNT(*) FROM orders WHERE status = 'completed'

-- Count distinct values
SELECT COUNT(DISTINCT category) FROM products

-- Count distinct with condition
SELECT COUNT(DISTINCT country) FROM customers WHERE active = true
```

**Sample output:**

```
COUNT(*): 1542
```

### INSERT

Insert one or more documents.

```sql
INSERT INTO <database> (field1, field2, ...) VALUES (val1, val2), (val3, val4), ...
```

**Examples:**

```sql
-- Insert single document
INSERT INTO products (name, price, stock) VALUES ('Widget', 9.99, 100)

-- Insert multiple documents
INSERT INTO users (name, email, role) VALUES ('Alice', 'alice@example.com', 'admin'), ('Bob', 'bob@example.com', 'user')

-- With explicit _id
INSERT INTO products (_id, name, price) VALUES ('prod-001', 'Laptop', 999)

-- String with quotes (use '' to escape single quotes)
INSERT INTO messages (text) VALUES ('It''s a "great" day')
```

**Sample output:**

```
Inserted: 2 document(s)
IDs: 6f8a9b2c3d4e5f, 7a8b9c0d1e2f3g
```

### UPDATE

Update documents matching conditions.

```sql
UPDATE <database> SET field1 = value1, field2 = value2 WHERE <conditions>
```

**Examples:**

```sql
-- Update single field
UPDATE products SET price = 19.99 WHERE _id = 'prod-001'

-- Update multiple fields
UPDATE products SET price = 29.99, on_sale = true WHERE category = 'clearance'

-- Update with complex condition
UPDATE users SET verified = true WHERE email ~* 'company.com' AND created_at < '2024-01-01'
```

**Sample output:**

```
Scanned: 1542 documents
Matched: 23 documents
Updated: 23 documents
```

### DELETE

Delete documents matching conditions.

```sql
DELETE FROM <database> WHERE <conditions>
```

**Examples:**

```sql
-- Delete by ID
DELETE FROM products WHERE _id = 'prod-001'

-- Delete with condition
DELETE FROM logs WHERE level = 'debug' AND timestamp < 1704067200

-- Delete with IN operator
DELETE FROM sessions WHERE status IN ('expired', 'revoked')
```

**Sample output:**

```
Scanned: 10000 documents
Matched: 156 documents
Deleted: 156 documents
```

### IMPORT

Import documents from CSV or JSON files.

```sql
IMPORT <filename> INTO <database>
```

**Supported formats:**
- `.csv` - CSV with header row
- `.json` - JSON array of documents

**CSV numeric convention:** Fields ending with `_NUM` are converted to numbers (suffix stripped).

**Examples:**

```sql
-- Import CSV
IMPORT products.csv INTO products

-- Import JSON
IMPORT backup.json INTO users

-- Quoted filename with spaces
IMPORT 'my data.csv' INTO products
```

**CSV file example (`products.csv`):**

```csv
name,price_NUM,stock_NUM,category
Widget,9.99,100,electronics
Gadget,19.99,50,electronics
```

Results in documents:

```json
{"name": "Widget", "price": 9.99, "stock": 100, "category": "electronics"}
```

**Sample interaction:**

```
couchdb> IMPORT products.csv INTO products

File: products.csv (CSV)
Target database: products

Sample document (first row):
{
  "name": "Widget",
  "price": 9.99,
  "stock": 100,
  "category": "electronics"
}

3 document(s) will be imported.

Proceed with import? (yes/no): yes

Imported: 3 document(s)
```

### EXPORT

Export documents to various file formats.

```sql
EXPORT <database> TO <filename> [WHERE <conditions>]
```

**Supported formats:**
- `.xlsx` / `.xls` - Excel spreadsheet
- `.json` - JSON array
- `.sql` - SQL CREATE TABLE + INSERT statements

**Examples:**

```sql
-- Export all to Excel
EXPORT products TO products.xlsx

-- Export filtered to JSON
EXPORT orders TO pending_orders.json WHERE status = 'pending'

-- Export to SQL
EXPORT users TO users_backup.sql WHERE active = true
```

**Sample output:**

```
Scanned: 1542 documents
Matched: 1542 documents
Exported: 1542 documents
File: products.xlsx (XLS)
Fields: 8
```

### CREATE

Create a new database.

```sql
CREATE <database>
```

**Example:**

```sql
CREATE products
```

**Sample output:**

```
Database 'products' created successfully.
```

### SHOW DATABASES

List all user databases.

```sql
SHOW DATABASES
```

**Sample output:**

```
Databases:
  inventar
  orders
  products
  users

4 database(s)
```

## WHERE Clause Reference

### Comparison Operators

| Operator | Description | Example |
|----------|-------------|---------|
| `=` | Equal to | `status = 'active'` |
| `!=` | Not equal to | `type != 'deleted'` |
| `<` | Less than | `price < 100` |
| `>` | Greater than | `stock > 0` |
| `<=` | Less than or equal | `age <= 65` |
| `>=` | Greater than or equal | `rating >= 4.5` |
| `~` | Regex match (case-sensitive) | `name ~ '^Model'` |
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

### String Quoting

Strings use single quotes. Escape single quotes by doubling them:

```sql
WHERE name = 'McDonald''s'
WHERE message = 'It''s a "great" day'
```

## Safety Features

- **DELETE/UPDATE require WHERE** - Commands without WHERE clause are blocked
- **Design documents protected** - `_design/*` documents are automatically skipped
- **Import confirmation** - IMPORT shows preview and asks for confirmation
- **Connection test** - Warning displayed if CouchDB is unreachable at startup

## Dependencies

- `requests` - HTTP client for CouchDB API
- `python-dotenv` - Environment variable management
- `openpyxl` - Excel file support (optional, for EXPORT to .xlsx)

## License

MIT License
