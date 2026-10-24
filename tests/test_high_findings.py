"""Regression tests for the three high-severity audit findings.

The fake HTTP layer implements only the CouchDB routes this client calls:
GET /{db}, GET /{db}/_all_docs?include_docs=true, and POST /{db}/_bulk_docs.
"""

import copy
import json

import pytest

from executor import CouchDBExecutor
from parser import Condition, ParseError, parse


SHOP = [
    {"_id": "1", "_rev": "1-a", "status": "active", "n": 1, "flag": True,
     "name": "apple", "address": {"city": "Paris"}},
    {"_id": "2", "_rev": "1-b", "status": "inactive", "n": 2, "flag": False,
     "name": "app", "address": {"city": "Lyon"}},
    {"_id": "3", "_rev": "1-c", "status": None, "n": 1, "flag": 1,
     "name": "banana", "address": {"city": "Paris"}},
    {"_id": "4", "_rev": "1-d", "n": 0, "flag": 0, "name": "", "address": "nope"},
    {"_id": "5", "_rev": "1-e", "status": "active", "n": "10", "name": "apricot"},
    {"_id": "_design/idx", "_rev": "1-f", "language": "javascript"},
    {"_id": "6", "_rev": "1-g", "status": "pending", "name": "apricot2",
     "message": "error OR level = 1"},
]


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

    def json(self):
        if isinstance(self._payload, str):
            raise ValueError("not json")
        return self._payload


class FakeCouch:
    def __init__(self, databases):
        self.databases = databases

    def request(self, method, path, **kwargs):
        path = path.split("?", 1)[0].lstrip("/")
        if method == "GET" and path in self.databases:
            return FakeResponse(200, {"db_name": path})
        if method == "GET" and path.endswith("/_all_docs"):
            db_name = path.split("/", 1)[0]
            if db_name not in self.databases:
                return FakeResponse(404, {"error": "not_found"})
            rows = [{"id": doc["_id"], "doc": doc} for doc in self.databases[db_name]]
            return FakeResponse(200, {"total_rows": len(rows), "rows": rows})
        if method == "POST" and path.endswith("/_bulk_docs"):
            db_name = path.split("/", 1)[0]
            results = []
            for doc in kwargs["json"]["docs"]:
                if doc.get("_deleted"):
                    self.databases[db_name] = [
                        existing for existing in self.databases[db_name]
                        if existing["_id"] != doc["_id"]
                    ]
                    results.append({"ok": True, "id": doc["_id"]})
                    continue
                replaced = False
                updated = []
                for existing in self.databases[db_name]:
                    if existing["_id"] == doc.get("_id"):
                        updated.append(doc)
                        replaced = True
                    else:
                        updated.append(existing)
                if not replaced:
                    updated.append(doc)
                self.databases[db_name] = updated
                results.append({"ok": True, "id": doc.get("_id")})
            return FakeResponse(201, results)
        return FakeResponse(404, {"error": "not_found", "path": path})


def make_executor(docs=None):
    databases = {"shop": copy.deepcopy(SHOP if docs is None else docs)}
    executor = CouchDBExecutor()
    executor._request = FakeCouch(databases).request
    return executor, databases


def matched_ids(executor, sql):
    result = executor.execute(parse(sql))
    return [row["_id"] for row in result["rows"]]


def lone_condition(sql):
    command = parse(sql)
    assert len(command.conditions.items) == 1
    condition = command.conditions.items[0][0]
    assert isinstance(condition, Condition)
    return condition


class TestQuotedSyntax:
    def test_or_inside_string_is_one_predicate(self):
        condition = lone_condition(
            "DELETE FROM shop WHERE message != 'error OR level = 1';"
        )
        assert condition.field == "message"
        assert condition.operator == "!="
        assert condition.value == "error OR level = 1"

    def test_and_or_between_and_parentheses_inside_strings(self):
        assert lone_condition(
            "SELECT * FROM t WHERE name = 'Tom AND Jerry';"
        ).value == "Tom AND Jerry"
        assert lone_condition(
            "SELECT * FROM t WHERE name = 'Tom OR Jerry';"
        ).value == "Tom OR Jerry"

        between = lone_condition(
            "SELECT * FROM t WHERE name BETWEEN 'A AND B' AND 'Z';"
        )
        assert between.operator == "BETWEEN"
        assert between.value == ("A AND B", "Z")

        command = parse("SELECT * FROM t WHERE name = 'foo)' AND x = 1;")
        assert command.conditions.items[0][0].value == "foo)"
        assert command.conditions.items[1][0].field == "x"
        assert command.conditions.items[1][1] == "AND"

    def test_escaped_quote_does_not_end_the_string(self):
        condition = lone_condition(
            "SELECT * FROM products WHERE name = 'Mc''Donald OR x = 1';"
        )
        assert condition.value == "Mc'Donald OR x = 1"

    def test_update_where_inside_set_string(self):
        command = parse(
            "UPDATE products SET note = 'see WHERE clause' WHERE _id = '1';"
        )
        assert command.assignments == {"note": "see WHERE clause"}
        assert lone_condition(
            "UPDATE products SET note = 'see WHERE clause' WHERE _id = '1';"
        ).value == "1"

        escaped = parse(
            "UPDATE t SET note = 'it''s WHERE' WHERE _id = '1';"
        )
        assert escaped.assignments == {"note": "it's WHERE"}

    def test_unclosed_quote_is_rejected(self):
        with pytest.raises(ParseError, match="Unclosed quote"):
            parse("SELECT * FROM t WHERE name = 'Tom AND Jerry;")
        with pytest.raises(ParseError, match="Unclosed quote"):
            parse("UPDATE t SET note = 'hello WHERE _id = 1;")
        with pytest.raises(ParseError, match="Unclosed quote"):
            parse("DELETE FROM t WHERE name = 'abc;")

    def test_in_list_parentheses_and_delete_guard_still_work(self):
        condition = lone_condition(
            "SELECT * FROM t WHERE status IN ('a OR b', 'c');"
        )
        assert condition.operator == "IN"
        assert condition.value == ["a OR b", "c"]

        with pytest.raises(ParseError, match="WHERE"):
            parse("DELETE FROM shop;")
        with pytest.raises(ParseError, match="WHERE"):
            parse("UPDATE shop SET flag = false;")

        semicolon = lone_condition("SELECT * FROM t WHERE name = 'a;b';")
        assert semicolon.value == "a;b"


class TestNullAndBooleanComparisons:
    def test_not_equal_and_not_in_skip_null_and_missing(self):
        executor, _db = make_executor()
        # inactive and pending only. Explicit null (3) and missing (4) stay.
        assert matched_ids(
            executor, "SELECT _id FROM shop WHERE status != 'active';"
        ) == ["2", "6"]
        assert matched_ids(
            executor, "SELECT _id FROM shop WHERE status NOT IN ('active');"
        ) == ["2", "6"]

    def test_equals_null_does_not_match_null_or_missing(self):
        executor, _db = make_executor()
        assert matched_ids(
            executor, "SELECT _id FROM shop WHERE status = null;"
        ) == []
        # IS NULL remains the way to match null and missing fields.
        assert matched_ids(
            executor, "SELECT _id FROM shop WHERE status IS NULL;"
        ) == ["3", "4"]
        assert matched_ids(
            executor, "SELECT _id FROM shop WHERE status IS NOT NULL;"
        ) == ["1", "2", "5", "6"]

    def test_delete_quoted_or_keeps_exact_match_and_nulls(self):
        executor, databases = make_executor()
        result = executor.execute(parse(
            "DELETE FROM shop WHERE message != 'error OR level = 1';"
        ))
        remaining = [doc["_id"] for doc in databases["shop"]]
        assert result["matched"] == 0
        assert result["deleted"] == 0
        assert remaining == ["1", "2", "3", "4", "5", "_design/idx", "6"]

        docs = copy.deepcopy(SHOP)
        docs.append({"_id": "7", "_rev": "1-h", "status": "active", "message": "hello"})
        executor, databases = make_executor(docs)
        result = executor.execute(parse(
            "DELETE FROM shop WHERE message != 'error OR level = 1';"
        ))
        remaining = [doc["_id"] for doc in databases["shop"]]
        assert result["deleted"] == 1
        assert "7" not in remaining
        assert "6" in remaining
        assert "1" in remaining
        assert "_design/idx" in remaining

    def test_boolean_is_not_a_number(self):
        executor, _db = make_executor()
        assert matched_ids(executor, "SELECT _id FROM shop WHERE flag = 0;") == ["4"]
        assert matched_ids(executor, "SELECT _id FROM shop WHERE flag = 1;") == ["3"]
        assert matched_ids(executor, "SELECT _id FROM shop WHERE flag = false;") == ["2"]
        assert matched_ids(executor, "SELECT _id FROM shop WHERE flag = true;") == ["1"]
        assert matched_ids(
            executor, "SELECT _id FROM shop WHERE flag IN (0, 1);"
        ) == ["3", "4"]
        assert matched_ids(
            executor, "SELECT _id FROM shop WHERE flag IN (true, false);"
        ) == ["1", "2"]
        assert matched_ids(
            executor, "SELECT _id FROM shop WHERE flag BETWEEN 0 AND 1;"
        ) == ["3", "4"]
        assert matched_ids(
            executor, "SELECT _id FROM shop WHERE flag < 2;"
        ) == ["3", "4"]
        assert matched_ids(
            executor, "SELECT _id FROM shop WHERE flag != 0;"
        ) == ["1", "2", "3"]

    def test_flag_equals_zero_deletes_only_the_integer(self):
        executor, databases = make_executor()
        result = executor.execute(parse("DELETE FROM shop WHERE flag = 0;"))
        flags = {doc["_id"]: doc.get("flag") for doc in databases["shop"]}
        assert result["deleted"] == 1
        assert "4" not in flags
        assert flags["2"] is False
        assert flags["1"] is True
        assert flags["3"] == 1

    def test_count_distinct_keeps_bool_separate_from_int(self):
        executor, _db = make_executor()
        result = executor.execute(parse("SELECT COUNT(DISTINCT flag) FROM shop;"))
        # true, false, 1, 0, and missing. Design doc is not scanned.
        assert result["count"] == 5
        assert result["scanned"] == 6

    def test_queries_that_already_worked(self):
        executor, databases = make_executor()
        assert matched_ids(
            executor, "SELECT _id FROM shop WHERE status = 'active';"
        ) == ["1", "5"]
        assert matched_ids(
            executor, "SELECT _id FROM shop WHERE address.city = 'Paris';"
        ) == ["1", "3"]
        assert matched_ids(
            executor,
            "SELECT _id FROM shop WHERE (status = 'active' OR status = 'pending') AND n = 1;",
        ) == ["1"]
        assert matched_ids(
            executor, "SELECT _id FROM shop WHERE n = 1;"
        ) == ["1", "3"]
        count = executor.execute(parse("SELECT COUNT(*) FROM shop;"))
        assert count["count"] == 6

        updated = executor.execute(parse(
            "UPDATE shop SET note = 'see WHERE clause' WHERE _id = '1';"
        ))
        assert updated["updated"] == 1
        doc1 = next(doc for doc in databases["shop"] if doc["_id"] == "1")
        assert doc1["note"] == "see WHERE clause"
        assert doc1["status"] == "active"
        assert all(doc.get("note") is None for doc in databases["shop"] if doc["_id"] != "1")

    def test_mixed_types_still_raise(self):
        executor, _db = make_executor()
        with pytest.raises(TypeError):
            executor.execute(parse("SELECT _id FROM shop WHERE n > '5';"))
