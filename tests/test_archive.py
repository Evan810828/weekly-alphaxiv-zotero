import copy
import datetime as dt
import unittest
from unittest.mock import patch
import archive as a

MONDAY = dt.date(2026, 11, 2)
DEST = "TEST2345"
PAPER = {"itemType": "preprint", "title": "Synthetic example", "url": "https://arxiv.org/abs/2501.01234", "DOI": "10.48550/arXiv.2501.01234"}


class FakeAlpha:
    def __init__(self, papers=()):
        self.folders = [{"name": "Oct-Week4-2026", "folder_id": "previous", "parent_id": None},
                        {"name": "Star", "folder_id": "previous-star", "parent_id": "previous", "paper_count": len(papers), "papers": list(papers)}]
        self.created = []

    def library(self):
        return copy.deepcopy(self.folders)

    def create(self, name, parent):
        self.created.append((name, parent))
        self.folders.append({"name": name, "folder_id": str(len(self.folders)), "parent_id": parent, "paper_count": 0, "papers": []})


class FakeZotero:
    collection = DEST

    def __init__(self, items=()):
        self.records = list(items)
        self.created = 0

    def items(self):
        return copy.deepcopy(self.records)

    def create(self, paper):
        self.created += 1
        saved = {"key": "ITEM2345", "version": 1, "data": {**paper, "collections": [DEST]}}
        self.records.append(saved)
        return saved

    def file_existing(self, item):
        for record in self.records:
            if record["key"] == item["key"]:
                collections = record["data"]["collections"]
                if DEST in collections:
                    return "already_present"
                collections.append(DEST)
                return "membership_added"


class ArchiveTests(unittest.TestCase):
    def test_week_names_and_boundaries(self):
        for day, expected in [("2026-10-26", "Oct-Week4-2026"), ("2026-11-02", "Nov-Week1-2026"),
                              ("2026-03-30", "Mar-Week5-2026"), ("2027-01-04", "Jan-Week1-2027")]:
            self.assertEqual(a.folder_name(dt.date.fromisoformat(day)), expected)
        self.assertEqual(a.folder_name(dt.date(2027, 1, 4) - dt.timedelta(days=7)), "Dec-Week4-2026")

    def test_non_monday_rejected(self):
        with self.assertRaises(a.Gate):
            a.folder_name(dt.date(2026, 11, 3))

    def test_timezone_spans_dst(self):
        for value in ("2026-03-09T09:00:00", "2026-11-02T09:00:00"):
            local = dt.datetime.fromisoformat(value).replace(tzinfo=a.TZ)
            self.assertEqual(a.monday(local.date()), local.date())
        self.assertNotEqual(dt.datetime(2026, 3, 9, 9, tzinfo=a.TZ).utcoffset(), dt.datetime(2026, 11, 2, 9, tzinfo=a.TZ).utcoffset())

    def test_identifier_normalization(self):
        self.assertEqual(a.arxiv_id("https://arxiv.org/pdf/2501.01234v3"), "2501.01234")
        self.assertEqual(a.arxiv_id("arXiv:hep-th/9901001v2"), "hep-th/9901001")
        self.assertEqual(a.doi("https://doi.org/10.1000/ABC"), "10.1000/abc")

    def test_match_versioned_arxiv(self):
        item = {"data": {"url": "https://arxiv.org/pdf/2501.01234v2"}}
        self.assertIs(a.match_item(PAPER, [item]), item)

    def test_ambiguous_identifier_blocks(self):
        with self.assertRaisesRegex(a.Gate, "multiple_identifier_matches"):
            a.match_item(PAPER, [{"data": PAPER}, {"data": PAPER}])

    def test_missing_identifier_title_candidate_blocks(self):
        with self.assertRaisesRegex(a.Gate, "identifier_review"):
            a.match_item(PAPER, [{"data": {"title": "SYNTHETIC EXAMPLE!"}}])

    def test_duplicate_folders_block(self):
        with self.assertRaisesRegex(a.Gate, "duplicate_folder"):
            a.find_folder([{"name": "Star", "parent_id": "x"}] * 2, "Star", "x")

    def test_missing_star(self):
        self.assertIsNone(a.previous_papers([], MONDAY))

    def test_truncated_source_blocks(self):
        alpha = FakeAlpha([{"universal_paper_id": "2501.01234"}])
        alpha.folders[1]["paper_count"] = 2
        with self.assertRaisesRegex(a.Gate, "incomplete"):
            a.previous_papers(alpha.library(), MONDAY)

    def test_empty_source_still_prepares_folders(self):
        alpha, z = FakeAlpha(), FakeZotero()
        result = a.run(alpha, lambda: z, MONDAY, True)
        self.assertEqual(result["status"], "previous_star_empty")
        self.assertEqual(len(alpha.created), 2)

    def test_repeated_run_is_idempotent(self):
        alpha, z = FakeAlpha([{}]), FakeZotero()
        first = a.run(alpha, lambda: z, MONDAY, True, lambda _: PAPER)
        second = a.run(alpha, lambda: z, MONDAY, True, lambda _: PAPER)
        self.assertEqual(first["counts"]["imported"], 1)
        self.assertEqual(second["counts"]["already_present"], 1)
        self.assertEqual(z.created, 1)
        self.assertEqual(len(alpha.created), 2)
        self.assertEqual(len(alpha.folders[1]["papers"]), 1)

    def test_membership_preserves_other_collections(self):
        alpha = FakeAlpha([{}])
        z = FakeZotero([{"key": "ITEM2345", "data": {**PAPER, "collections": ["OTHER234"]}}])
        result = a.run(alpha, lambda: z, MONDAY, True, lambda _: PAPER)
        self.assertEqual(result["counts"]["membership_added"], 1)
        self.assertEqual(z.records[0]["data"]["collections"], ["OTHER234", DEST])
        self.assertEqual(z.created, 0)

    def test_dry_run_has_no_writes(self):
        alpha, z = FakeAlpha([{}]), FakeZotero()
        result = a.run(alpha, lambda: z, MONDAY, False, lambda _: PAPER)
        self.assertEqual(result["counts"]["would_import"], 1)
        self.assertEqual(alpha.created, [])
        self.assertEqual(z.created, 0)

    def test_failure_does_not_publish_metadata(self):
        import json
        alpha, z = FakeAlpha([{}]), FakeZotero([{"data": {"title": PAPER["title"]}}])
        result = a.run(alpha, lambda: z, MONDAY, False, lambda _: PAPER)
        self.assertEqual(result["counts"]["failed"], 1)
        self.assertNotIn(PAPER["title"], json.dumps(result))
        self.assertNotIn("2501.01234", json.dumps(result))

    def test_conflict_reloads_and_preserves_new_memberships(self):
        z = object.__new__(a.Zotero)
        z.collection, z.prefix = DEST, "/users/example"
        snapshots = [{"key": "ITEM2345", "version": 1, "data": {"collections": ["OLD"]}},
                     {"key": "ITEM2345", "version": 2, "data": {"collections": ["OLD", "NEW"]}},
                     {"key": "ITEM2345", "version": 3, "data": {"collections": ["OLD", "NEW", DEST]}}]
        with patch.object(z, "get", side_effect=snapshots), patch.object(z, "api", side_effect=[a.HTTPFailure("zotero", 412), None]) as api:
            self.assertEqual(z.file_existing({"key": "ITEM2345"}), "membership_added")
            self.assertEqual(set(api.call_args.args[2]["collections"]), {"OLD", "NEW", DEST})

    def test_tool_errors_are_redacted(self):
        with self.assertRaisesRegex(a.Gate, "^alphaxiv_tool_failed$"):
            a.unpack_tool({"isError": True, "content": [{"type": "text", "text": "private details"}]})


if __name__ == "__main__":
    unittest.main()
