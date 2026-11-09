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


def manual(_):
    raise a.Gate("source_needs_manual_metadata")


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

    def test_wrong_alphaxiv_account_blocks(self):
        with self.assertRaisesRegex(a.Gate, "account_fingerprint_mismatch"):
            a.verify_account([{"folder_id": "wrong"}], "expected")
        a.verify_account([{"folder_id": "expected"}], "expected")

    def test_single_title_match_in_destination_is_present(self):
        present = {"key": "ITEM0001", "data": {"title": "SYNTHETIC EXAMPLE!", "collections": [DEST]}}
        self.assertIs(a.match_item(PAPER, [present], DEST), present)
        for apply in (False, True):
            alpha, z = FakeAlpha([{}]), FakeZotero([copy.deepcopy(present)])
            result = a.run(alpha, lambda: z, MONDAY, apply, lambda _: PAPER)
            self.assertEqual(result["counts"]["already_present"], 1, apply)
            self.assertEqual(result["counts"]["failed"], 0, apply)
            self.assertEqual(z.created, 0)

    def test_title_match_outside_destination_still_blocks(self):
        elsewhere = {"key": "ITEM0001", "data": {"title": PAPER["title"], "collections": ["OTHER234"]}}
        with self.assertRaisesRegex(a.Gate, "identifier_review"):
            a.match_item(PAPER, [elsewhere], DEST)
        alpha, z = FakeAlpha([{}]), FakeZotero([elsewhere])
        result = a.run(alpha, lambda: z, MONDAY, True, lambda _: PAPER)
        self.assertEqual(result["counts"]["failed"], 1)
        self.assertEqual(result["errors"][0]["code"], "title_candidate_needs_identifier_review")
        self.assertEqual(z.created, 0)

    def test_two_title_matches_in_destination_block(self):
        present = {"data": {"title": PAPER["title"], "collections": [DEST]}}
        with self.assertRaisesRegex(a.Gate, "identifier_review"):
            a.match_item(PAPER, [present, copy.deepcopy(present)], DEST)

    def test_match_reference_uses_doi_and_title_only(self):
        self.assertEqual(a.match_reference({"type": "external", "universal_paper_id": "not-a-doi", "title": " Some  title "}),
                         {"title": "Some title"})
        self.assertEqual(a.match_reference({"universal_paper_id": "https://doi.org/10.1000/ABC", "title": "X"}),
                         {"title": "X", "DOI": "10.1000/abc"})
        self.assertEqual(a.match_reference({"doi": "10.1000/xyz"}), {"title": "", "DOI": "10.1000/xyz"})

    def test_arxiv_sources_do_not_use_the_fallback(self):
        alpha, z = FakeAlpha([{"type": "public", "universal_paper_id": "2501.01234"}]), FakeZotero()
        result = a.run(alpha, lambda: z, MONDAY, True, lambda _: PAPER)
        self.assertEqual(result["counts"]["imported"], 1)

    def test_other_metadata_failures_still_fail(self):
        def broken(_):
            raise a.Gate("arxiv_metadata_incomplete")
        result = a.run(FakeAlpha([{"title": PAPER["title"]}]), lambda: FakeZotero([{"key": "K", "data": {"title": PAPER["title"], "collections": [DEST]}}]), MONDAY, True, broken)
        self.assertEqual(result["errors"][0]["code"], "arxiv_metadata_incomplete")

    def test_doi_source_in_destination_is_present(self):
        source = {"type": "external", "universal_paper_id": "10.1000/abc", "title": "Unrelated title"}
        present = {"key": "ITEM0001", "data": {"title": "Different wording", "DOI": "https://doi.org/10.1000/ABC", "collections": [DEST]}}
        for apply in (False, True):
            alpha, z = FakeAlpha([source]), FakeZotero([copy.deepcopy(present)])
            result = a.run(alpha, lambda: z, MONDAY, apply, manual)
            self.assertEqual(result["counts"]["already_present"], 1, apply)
            self.assertEqual(result["counts"]["failed"], 0, apply)
            self.assertEqual(z.created, 0)

    def test_doi_source_elsewhere_is_filed_not_created(self):
        source = {"type": "external", "universal_paper_id": "10.1000/abc"}
        z = FakeZotero([{"key": "ITEM0001", "data": {"DOI": "10.1000/abc", "collections": ["OTHER234"]}}])
        result = a.run(FakeAlpha([source]), lambda: z, MONDAY, True, manual)
        self.assertEqual(result["counts"]["membership_added"], 1)
        self.assertEqual(z.records[0]["data"]["collections"], ["OTHER234", DEST])
        self.assertEqual(z.created, 0)

    def test_non_arxiv_title_in_destination_is_present(self):
        source = {"type": "public", "universal_paper_id": "2611.some-slug", "title": "Sparks of  Something: LLMs & weights"}
        present = {"key": "ITEM0001", "data": {"title": "Sparks of Something: LLMs & Weights", "collections": [DEST]}}
        for apply in (False, True):
            alpha, z = FakeAlpha([source]), FakeZotero([copy.deepcopy(present)])
            result = a.run(alpha, lambda: z, MONDAY, apply, manual)
            self.assertEqual(result["counts"]["already_present"], 1, apply)
            self.assertEqual(result["counts"]["failed"], 0, apply)
            self.assertEqual(z.created, 0)

    def test_non_arxiv_title_elsewhere_needs_review(self):
        source = {"type": "public", "universal_paper_id": "2611.some-slug", "title": "Sparks of Something"}
        z = FakeZotero([{"key": "ITEM0001", "data": {"title": "Sparks of Something", "collections": ["OTHER234"]}}])
        result = a.run(FakeAlpha([source]), lambda: z, MONDAY, True, manual)
        self.assertEqual(result["errors"][0]["code"], "title_candidate_needs_identifier_review")
        self.assertEqual(z.records[0]["data"]["collections"], ["OTHER234"])

    def test_unmatched_source_without_metadata_still_needs_manual_handling(self):
        for source in ({"type": "external", "universal_paper_id": "10.1000/abc", "title": "Brand new"},
                       {"type": "public", "universal_paper_id": "2611.some-slug", "title": "Brand new"},
                       {"type": "private", "universal_paper_id": "x"}):
            for apply in (False, True):
                z = FakeZotero([{"key": "K", "data": {"title": "Other", "collections": [DEST]}}])
                result = a.run(FakeAlpha([source]), lambda: z, MONDAY, apply, manual)
                self.assertEqual(result["counts"]["failed"], 1, (source, apply))
                self.assertEqual(result["errors"][0]["code"], "source_needs_manual_metadata")
                self.assertEqual(z.created, 0)

    def test_alphaxiv_identifier_extraction(self):
        self.assertEqual(a.alphaxiv_id("https://www.alphaxiv.org/abs/2611.weight-space-programming"), "2611.weight-space-programming")
        self.assertEqual(a.alphaxiv_id("alphaXiv: 2611.Weight-Space-Programming"), "2611.weight-space-programming")
        self.assertIsNone(a.alphaxiv_id("https://www.alphaxiv.org/abs/2509.21057"))
        self.assertEqual(a.arxiv_id("https://www.alphaxiv.org/abs/2509.21057"), "2509.21057")
        self.assertIsNone(a.alphaxiv_id("2611.weight-space-programming"))
        self.assertEqual(a.item_ids({"data": {"url": "https://www.alphaxiv.org/abs/2611.slug-x"}}), {"alphaxiv:2611.slug-x"})

    def test_metadata_dispatch(self):
        with patch.object(a, "arxiv_metadata", return_value="arxiv") as arx, patch.object(a, "alphaxiv_metadata", return_value="alphaxiv") as alx:
            self.assertEqual(a.metadata({"type": "public", "universal_paper_id": "2501.01234"}), "arxiv")
            self.assertEqual(a.metadata({"type": "public", "universal_paper_id": "2611.Weight-Space"}), "alphaxiv")
            self.assertEqual(alx.call_args.args[0], "2611.weight-space")
            for source in ({"type": "private", "universal_paper_id": "2501.01234"}, {"type": "public", "universal_paper_id": "weird"}, {}):
                with self.assertRaisesRegex(a.Gate, "source_needs_manual_metadata"):
                    a.metadata(source)
            self.assertEqual(arx.call_count, 1)

    def test_iso_date(self):
        self.assertEqual(a.iso_date("Mon Nov 02 2026 00:00:00 GMT+0000 (Coordinated Universal Time)"), "2026-11-02")
        self.assertEqual(a.iso_date("2026-11-02T00:00:00Z"), "2026-11-02")
        self.assertEqual(a.iso_date("garbage", "2026-11-01"), "2026-11-01")
        self.assertEqual(a.iso_date(None), "")

    PAGE = ('<html><head><meta name="author" content="Meta Only &amp; Co"/>'
            '<script type="application/ld+json">{"@context":"https://schema.org","@type":"ScholarlyArticle",'
            '"headline":"Sparks of  Something: LLMs &amp; Weights","author":[{"@type":"Person","name":"Ada Lovelace"},{"@type":"Person","name":"Plato"}],'
            '"datePublished":"Mon Nov 02 2026 00:00:00 GMT+0000 (Coordinated Universal Time)",'
            '"citation":{"@type":"CreativeWork","identifier":"2611.some-slug"}}</script></head></html>')
    SOURCE = {"type": "public", "universal_paper_id": "2611.some-slug", "title": "Sparks of Something: LLMs &amp; weights", "publication_date": "2026-11-01"}

    def test_alphaxiv_metadata_from_page(self):
        with patch.object(a, "request", return_value=(self.PAGE.encode(), {})) as req:
            paper = a.alphaxiv_metadata("2611.some-slug", self.SOURCE)
        self.assertEqual(req.call_args.args[:2], ("alphaxiv_page", "https://www.alphaxiv.org/abs/2611.some-slug"))
        self.assertEqual(paper["title"], "Sparks of Something: LLMs &amp; Weights")
        self.assertEqual(paper["creators"], [{"creatorType": "author", "firstName": "Ada", "lastName": "Lovelace"}, {"creatorType": "author", "name": "Plato"}])
        self.assertEqual(paper["date"], "2026-11-02")
        self.assertEqual(paper["url"], "https://www.alphaxiv.org/abs/2611.some-slug")
        self.assertEqual(a.item_ids(paper), {"alphaxiv:2611.some-slug"})
        self.assertNotIn("DOI", paper)

    def test_alphaxiv_metadata_guards(self):
        with patch.object(a, "request", return_value=(self.PAGE.encode(), {})):
            with self.assertRaisesRegex(a.Gate, "alphaxiv_metadata_identity_failed"):
                a.alphaxiv_metadata("2611.other-slug", {**self.SOURCE, "universal_paper_id": "2611.other-slug"})
            with self.assertRaisesRegex(a.Gate, "alphaxiv_metadata_identity_failed"):
                a.alphaxiv_metadata("2611.some-slug", {**self.SOURCE, "title": "A different paper"})
            self.assertEqual(a.alphaxiv_metadata("2611.some-slug", {**self.SOURCE, "title": ""})["date"], "2026-11-02")
        no_authors = self.PAGE.replace('"author":[{"@type":"Person","name":"Ada Lovelace"},{"@type":"Person","name":"Plato"}],', "")
        with patch.object(a, "request", return_value=(no_authors.encode(), {})):
            self.assertEqual(a.alphaxiv_metadata("2611.some-slug", self.SOURCE)["creators"], [{"creatorType": "author", "firstName": "Meta Only &", "lastName": "Co"}])
        bare = no_authors.replace('<meta name="author" content="Meta Only &amp; Co"/>', "")
        with patch.object(a, "request", return_value=(bare.encode(), {})):
            with self.assertRaisesRegex(a.Gate, "alphaxiv_metadata_incomplete"):
                a.alphaxiv_metadata("2611.some-slug", self.SOURCE)
        with patch.object(a, "request", return_value=(b"<html>no structured data</html>", {})):
            with self.assertRaisesRegex(a.Gate, "alphaxiv_metadata_missing"):
                a.alphaxiv_metadata("2611.some-slug", self.SOURCE)
        by_url = (self.PAGE.replace('"citation":{"@type":"CreativeWork","identifier":"2611.some-slug"}', '"url":"https://www.alphaxiv.org/abs/2611.some-slug"')
                  .replace('"author":[{"@type":"Person","name":"Ada Lovelace"},{"@type":"Person","name":"Plato"}]', '"author":{"@type":"Person","name":"Solo Author"}')
                  .replace('<script type="application/ld+json">', '<script data-x="1" type="application/ld+json" nonce="n">'))
        with patch.object(a, "request", return_value=(by_url.encode(), {})):
            paper = a.alphaxiv_metadata("2611.some-slug", self.SOURCE)
            self.assertEqual(paper["creators"], [{"creatorType": "author", "firstName": "Solo", "lastName": "Author"}])
        with patch.object(a, "request", side_effect=a.HTTPFailure("alphaxiv_page", 404)):
            with self.assertRaisesRegex(a.Gate, "alphaxiv_page_http_404"):
                a.alphaxiv_metadata("2611.some-slug", self.SOURCE)

    def test_alphaxiv_native_paper_imports_once(self):
        with patch.object(a, "request", return_value=(self.PAGE.encode(), {})), patch.object(a.time, "sleep"):
            alpha, z = FakeAlpha([self.SOURCE]), FakeZotero()
            first = a.run(alpha, lambda: z, MONDAY, True, a.metadata)
            second = a.run(alpha, lambda: z, MONDAY, True, a.metadata)
        self.assertEqual((first["counts"]["imported"], first["counts"]["failed"]), (1, 0))
        self.assertEqual((second["counts"]["already_present"], second["counts"]["failed"]), (1, 0))
        self.assertEqual(z.created, 1)
        self.assertEqual(z.records[0]["data"]["extra"], "alphaXiv: 2611.some-slug")

    def test_arxiv_fetch_failure_fails_only_that_paper(self):
        sources = [{"type": "public", "universal_paper_id": "2501.01234"}, {"type": "public", "universal_paper_id": "2611.some-slug", "title": self.SOURCE["title"]}]
        def fake_request(service, url, *args, **kwargs):
            if service == "arxiv":
                raise a.HTTPFailure("arxiv", 503)
            return self.PAGE.encode(), {}
        z = FakeZotero()
        with patch.object(a, "request", side_effect=fake_request), patch.object(a.time, "sleep") as sleep:
            result = a.run(FakeAlpha(sources), lambda: z, MONDAY, True, a.metadata)
        self.assertEqual(result["errors"], [{"paper_number": 1, "code": "arxiv_http_503"}])
        self.assertEqual(result["counts"]["imported"], 1)
        self.assertEqual(sleep.call_count, 2)

    def test_unexpected_paper_error_does_not_abort_run(self):
        calls = []
        def flaky(source):
            calls.append(source)
            if source.get("boom"):
                raise KeyError("private detail")
            return PAPER
        alpha, z = FakeAlpha([{"boom": True}, {}]), FakeZotero()
        with patch.object(a.time, "sleep"):
            result = a.run(alpha, lambda: z, MONDAY, True, flaky)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["counts"]["failed"], 1)
        self.assertEqual(result["counts"]["imported"], 1)
        self.assertEqual(result["errors"], [{"paper_number": 1, "code": "unexpected_paper_failure"}])
        self.assertNotIn("private detail", str(result))

    def test_truncated_source_reports_created_folders(self):
        alpha = FakeAlpha([{}])
        alpha.folders[1]["papers_truncated"] = True
        result = a.run(alpha, FakeZotero, MONDAY, True)
        self.assertEqual(result["counts"]["folders_created"], 2)
        self.assertEqual(result["error"], "source_folder_incomplete")


if __name__ == "__main__":
    unittest.main()
