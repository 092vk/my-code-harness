#!/usr/bin/env python3
"""Tests for the comment-preserving JSONC editor."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "lib"))

from opencode_config import (  # noqa: E402
    Document,
    JsoncError,
    build_servers,
    remove_servers,
    upsert_servers,
)


class JsoncEditorTests(unittest.TestCase):
    def test_merge_preserves_comments_and_unrelated_keys(self):
        source = """{
  "$schema": "https://opencode.ai/config.json",
  // keep me
  "model": "x/y",
  "mcp": {
    "servers": {
      "other": { "type": "local", "command": ["foo"] }
    }
  }
}
"""
        result, report = upsert_servers(source, build_servers("cbm"))
        self.assertIn("// keep me", result)
        self.assertIn('"other"', result)
        self.assertEqual(report["added"], ["codebase-memory"])
        parsed = Document(result).value
        self.assertEqual(parsed["model"], "x/y")
        self.assertIn("codebase-memory", parsed["mcp"]["servers"])

    def test_merge_is_idempotent(self):
        result, _ = upsert_servers("{}", build_servers("cbm"))
        result2, report = upsert_servers(result, build_servers("cbm"))
        self.assertEqual(result, result2)
        self.assertEqual(report["unchanged"], ["codebase-memory"])

    def test_merge_into_empty_mcp_object(self):
        source = '{\n  "mcp": {}\n}\n'
        result, _ = upsert_servers(source, build_servers("codegraph"))
        parsed = Document(result).value
        self.assertIn("codegraph", parsed["mcp"]["servers"])
        self.assertIn('  "mcp": {', result)

    def test_conflict_requires_force(self):
        source = """{
  "mcp": {
    "servers": {
      "codebase-memory": { "type": "local", "command": ["custom"] }
    }
  }
}
"""
        with self.assertRaises(JsoncError):
            upsert_servers(source, build_servers("cbm"))
        result, report = upsert_servers(source, build_servers("cbm"), force=True)
        self.assertEqual(report["updated"], ["codebase-memory"])
        parsed = Document(result).value
        self.assertEqual(
            parsed["mcp"]["servers"]["codebase-memory"]["command"],
            ["npx", "-y", "codebase-memory-mcp"],
        )

    def test_remove_middle_first_and_last(self):
        source = """{
  "mcp": {
    "servers": {
      "a": { "x": 1 },
      "b": { "x": 2 },
      "c": { "x": 3 }
    }
  }
}
"""
        result, removed = remove_servers(source, ["b"])
        self.assertEqual(removed, ["b"])
        self.assertEqual(list(Document(result).value["mcp"]["servers"]), ["a", "c"])

        result, removed = remove_servers(result, ["a"])
        self.assertEqual(removed, ["a"])
        self.assertEqual(list(Document(result).value["mcp"]["servers"]), ["c"])

        result, removed = remove_servers(result, ["c"])
        self.assertEqual(removed, ["c"])
        self.assertEqual(Document(result).value["mcp"]["servers"], {})

    def test_both_backends(self):
        result, report = upsert_servers("{}", build_servers("both"))
        self.assertEqual(sorted(report["added"]), ["codebase-memory", "codegraph"])
        parsed = Document(result).value
        self.assertIn("codebase-memory", parsed["mcp"]["servers"])
        self.assertIn("codegraph", parsed["mcp"]["servers"])

    def test_trailing_comma_jsonc(self):
        source = """{
  "mcp": {
    "servers": {
      "other": { "type": "local" },
    },
  },
}
"""
        result, _ = upsert_servers(source, build_servers("cbm"))
        parsed = Document(result).value
        self.assertIn("codebase-memory", parsed["mcp"]["servers"])

    def test_invalid_shape_is_rejected(self):
        with self.assertRaises(JsoncError):
            upsert_servers('{ "mcp": [] }', build_servers("cbm"))

    def test_v1_shape_is_rejected_with_actionable_error(self):
        source = """{
  "mcp": {
    "codebase-memory": { "type": "local", "command": ["cbm"] }
  }
}
"""
        with self.assertRaises(JsoncError) as context:
            upsert_servers(source, build_servers("cbm"))
        self.assertIn("V1", str(context.exception))


if __name__ == "__main__":
    unittest.main()
