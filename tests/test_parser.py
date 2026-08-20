from __future__ import annotations

import gzip
import unittest

from tenable_reports.infrastructure.tenable_vm.parser import ChunkParseError, parse_chunk_response


class ChunkParserTests(unittest.TestCase):
    def test_parses_json_array(self) -> None:
        records = parse_chunk_response(b'[{"id":"a"},{"id":"b"}]')
        self.assertEqual([record["id"] for record in records], ["a", "b"])

    def test_parses_json_lines_and_gzip(self) -> None:
        payload = gzip.compress(b'{"id":"a"}\n{"id":"b"}\n')
        records = parse_chunk_response(payload)
        self.assertEqual(len(records), 2)

    def test_invalid_line_is_not_silently_discarded(self) -> None:
        with self.assertRaises(ChunkParseError):
            parse_chunk_response(b'{"id":"a"}\nnot-json\n')

    def test_empty_chunk_is_valid(self) -> None:
        self.assertEqual(parse_chunk_response(b""), [])


if __name__ == "__main__":
    unittest.main()
