import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bookmark_digest as bd


class BookmarkDigestTests(unittest.TestCase):
    def payload(self):
        result = {
            "__typename": "Tweet",
            "rest_id": "123",
            "legacy": {
                "full_text": "useful workflow",
                "favorite_count": 4,
                "retweet_count": 2,
                "reply_count": 1,
            },
            "core": {"user_results": {"result": {"legacy": {"screen_name": "alice"}}}},
            "quoted_status_result": {
                "result": {"rest_id": "999", "legacy": {"full_text": "quoted but not bookmarked"}}
            },
        }
        return {
            "data": {
                "bookmark_timeline_v2": {
                    "timeline": {
                        "instructions": [
                            {"type": "TimelineAddEntries", "entries": [{"content": {"entryType": "TimelineTimelineItem", "itemContent": {"tweet_results": {"result": result}}}}]}
                        ]
                    }
                }
            }
        }

    def write_receipt(self, root: Path, *, source_url="https://x.com/alice/status/123", consumer="research", receipt_id="r-1", status="accepted"):
        path = root / "receipt.json"
        path.write_text(json.dumps({
            "schema_version": bd.RECEIPT_SCHEMA,
            "status": status,
            "source_url": source_url,
            "consumer": consumer,
            "receipt_id": receipt_id,
        }), encoding="utf-8")
        return path

    def test_parse_ignores_quoted_status_and_exposes_candidate_id(self):
        rows = bd.parse_bookmark_payload(self.payload())
        self.assertEqual([r["id"] for r in rows], ["123"])
        self.assertEqual(rows[0]["url"], "https://x.com/alice/status/123")
        self.assertEqual(rows[0]["candidate_id"], bd.candidate_id(rows[0]["url"]))
        self.assertNotIn("quoted but not bookmarked", rows[0]["text"])

    def test_parser_fails_closed_on_unknown_schema(self):
        with self.assertRaises(bd.SourceHealthError):
            bd.parse_bookmark_payload({"data": {}})
        with self.assertRaises(bd.SourceHealthError):
            bd.parse_bookmark_payload({"errors": [{"message": "not authorized"}], "data": {"bookmark_timeline_v2": {"timeline": {"instructions": []}}}})
        with self.assertRaises(bd.SourceHealthError):
            bd.parse_bookmark_payload({"data": {"bookmark_timeline_v2": {"timeline": {"instructions": [{"entries": [{"content": {"newItemContent": {}}}]}]}}}})
        with self.assertRaises(bd.SourceHealthError):
            bd.parse_bookmark_payload({"data": {"bookmark_timeline_v2": {"timeline": {"instructions": [{"type": "FutureInstruction", "entries": []}]}}}})
        missing_instruction_type = self.payload()
        del missing_instruction_type["data"]["bookmark_timeline_v2"]["timeline"]["instructions"][0]["type"]
        with self.assertRaises(bd.SourceHealthError):
            bd.parse_bookmark_payload(missing_instruction_type)
        missing_typename = self.payload()
        del missing_typename["data"]["bookmark_timeline_v2"]["timeline"]["instructions"][0]["entries"][0]["content"]["itemContent"]["tweet_results"]["result"]["__typename"]
        with self.assertRaises(bd.SourceHealthError):
            bd.parse_bookmark_payload(missing_typename)
        unknown_cursor_entry = self.payload()
        content = unknown_cursor_entry["data"]["bookmark_timeline_v2"]["timeline"]["instructions"][0]["entries"][0]["content"]
        content["entryType"] = "FutureCursor"
        content["cursorType"] = "Top"
        with self.assertRaises(bd.SourceHealthError):
            bd.parse_bookmark_payload(unknown_cursor_entry)
        tweet_with_cursor = self.payload()
        tweet_with_cursor["data"]["bookmark_timeline_v2"]["timeline"]["instructions"][0]["entries"][0]["content"]["cursorType"] = "Top"
        with self.assertRaises(bd.SourceHealthError):
            bd.parse_bookmark_payload(tweet_with_cursor)
        future = self.payload()
        future["data"]["bookmark_timeline_v2"]["timeline"]["instructions"][0]["entries"][0]["content"]["itemContent"]["tweet_results"]["result"]["__typename"] = "FutureTweetShape"
        with self.assertRaises(bd.SourceHealthError):
            bd.parse_bookmark_payload(future)

    def test_parser_accepts_real_empty_timeline_shape(self):
        self.assertEqual(
            bd.parse_bookmark_payload({"data": {"bookmark_timeline_v2": {"timeline": {"instructions": []}}}}),
            [],
        )

    def test_candidate_id_is_stable_across_author_aliases(self):
        self.assertEqual(
            bd.candidate_id("https://x.com/alice/status/123"),
            bd.candidate_id("https://x.com/i/web/status/123"),
        )

    def test_candidate_id_rejects_non_x_and_suffix_collision(self):
        for url in (
            "https://evil.example/status/123",
            "https://x.com/alice/status/123suffix",
            "https://x.com/alice/status/123?ref=x",
            "https://user:pass@x.com/alice/status/123",
            "https://x.com/alice/status/١٢٣",
        ):
            with self.assertRaises(ValueError, msg=url):
                bd.candidate_id(url)

    def test_commit_requires_valid_matching_receipt_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state = root / "state.json"
            receipt = self.write_receipt(root)
            cid = bd.mark_processed(state, "https://x.com/alice/status/123", "research", receipt)
            saved = json.loads(state.read_text())
            self.assertEqual(saved["processed"][cid]["status"], "processed")
            self.assertEqual(saved["version"], bd.STATE_VERSION)
            self.assertEqual(saved["processed"][cid]["receipt_id"], "r-1")
            self.assertTrue(saved["processed"][cid]["receipt_sha256"])
            stored = state.parent / saved["processed"][cid]["receipt_file"]
            self.assertTrue(stored.is_file())
            self.assertEqual(stored.stat().st_mode & 0o777, 0o600)
            gate_key = bd.gate_key_path(state)
            self.assertTrue(gate_key.is_file())
            self.assertEqual(gate_key.stat().st_mode & 0o777, 0o600)
            self.assertTrue(saved["processed"][cid]["gate_mac"])
            self.assertEqual(bd.processed_state_tweet_ids(state), ["123"])
            self.assertEqual(
                bd.processed_tweet_ids([{"id": "123", "url": "https://x.com/i/web/status/123"}], state),
                ["123"],
            )

    def test_commit_rejects_blank_consumer_or_mismatched_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            receipt = self.write_receipt(root)
            with self.assertRaises(bd.ReceiptError):
                bd.mark_processed(root / "state.json", "https://x.com/alice/status/123", "", receipt)
            bad = self.write_receipt(root, source_url="https://x.com/alice/status/456")
            with self.assertRaises(bd.ReceiptError):
                bd.mark_processed(root / "state.json", "https://x.com/alice/status/123", "research", bad)

    def test_processed_gate_rejects_legacy_or_incomplete_state(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            cid = bd.candidate_id("https://x.com/alice/status/123")
            state.write_text(json.dumps({"version": bd.STATE_VERSION, "processed": {cid: {"status": "processed", "tweet_id": "123", "source_url": "https://x.com/alice/status/123", "consumer": "", "receipt_id": "fabricated", "receipt_sha256": "fabricated", "receipt_file": f"receipts/{cid}.json", "gate_mac": "fabricated"}}}), encoding="utf-8")
            self.assertEqual(bd.processed_tweet_ids([{"id": "123"}], state), [])
            state.write_text(json.dumps({"version": "2.1", "processed": {cid: {"status": "processed", "tweet_id": "123"}}}), encoding="utf-8")
            self.assertEqual(bd.processed_tweet_ids([{"id": "123"}], state), [])

    def test_synchronized_state_and_receipt_forgery_without_gate_key_is_inert(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state = root / "state.json"
            cid = bd.candidate_id("https://x.com/alice/status/123")
            receipts = root / "receipts"
            receipts.mkdir()
            stored = receipts / f"{cid}.json"
            stored.write_text(json.dumps({"schema_version": bd.RECEIPT_SCHEMA, "status": "accepted", "source_url": "https://x.com/alice/status/123", "consumer": "research", "receipt_id": "handmade"}), encoding="utf-8")
            proof = bd.validate_receipt(stored, "https://x.com/alice/status/123", "research")
            entry = {"status": "processed", "tweet_id": "123", "source_url": "https://x.com/alice/status/123", "consumer": "research", "receipt_id": "handmade", "receipt_sha256": proof["sha256"], "receipt_file": f"receipts/{cid}.json", "gate_mac": "handmade"}
            state.write_text(json.dumps({"version": bd.STATE_VERSION, "processed": {cid: entry}}), encoding="utf-8")
            self.assertEqual(bd.processed_tweet_ids([{"id": "123"}], state), [])

    def test_mark_removed_only_after_valid_processed_gate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state = root / "state.json"
            receipt = self.write_receipt(root)
            cid = bd.mark_processed(state, "https://x.com/alice/status/123", "research", receipt)
            self.assertEqual(bd.processed_state_tweet_ids(state), ["123"])
            self.assertEqual(bd.mark_removed(state, ["123"]), ["123"])
            saved = json.loads(state.read_text())
            self.assertEqual(saved["processed"][cid]["status"], "removed")
            self.assertTrue(saved["processed"][cid]["removed_at"])
            self.assertEqual(bd.processed_state_tweet_ids(state), [])
            self.assertEqual(bd.mark_removed(state, ["123"]), [])

    def test_processed_gate_revalidates_stored_receipt_hash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state = root / "state.json"
            receipt = self.write_receipt(root)
            cid = bd.mark_processed(state, "https://x.com/alice/status/123", "research", receipt)
            saved = json.loads(state.read_text())
            stored = root / saved["processed"][cid]["receipt_file"]
            stored.write_text(json.dumps({"schema_version": bd.RECEIPT_SCHEMA, "status": "accepted", "source_url": "https://x.com/alice/status/123", "consumer": "research", "receipt_id": "tampered"}), encoding="utf-8")
            self.assertEqual(bd.processed_tweet_ids([{"id": "123"}], state), [])

    def test_dom_selector_uses_top_level_time_anchor_not_any_quoted_link(self):
        visible = bd.visible_top_level_ids_expression()
        click = bd.click_top_level_bookmark_expression("123")
        self.assertIn("querySelectorAll('time')", visible)
        self.assertIn("closest('article", visible)
        self.assertIn("===a", visible)
        self.assertIn("hs.length!==1", visible)
        self.assertIn("querySelectorAll('time')", click)
        self.assertIn("===x", click)
        self.assertIn("===a", click)
        self.assertIn("hs.length!==1", click)
        self.assertIn("bs.length!==1", click)
        detail = bd.detail_bookmark_state_expression("123")
        self.assertIn("pageId", detail)
        self.assertIn("closest('article", detail)
        self.assertIn("hs.length!==1", detail)
        self.assertIn("ambiguous", detail)
        self.assertIn("arts.length!==1", detail)
        self.assertIn("getBoundingClientRect", detail)
        self.assertIn("r.top<innerHeight", detail)
        direct_click = bd.click_detail_unbookmark_expression("123")
        self.assertIn("page!==", direct_click)
        self.assertIn("arts.length!==1", direct_click)
        self.assertIn("rem.length!==1", direct_click)
        self.assertIn("add.length!==0", direct_click)
        self.assertIn("getBoundingClientRect", direct_click)
        self.assertIn("r.top<innerHeight", direct_click)

    def test_unbookmark_empty_is_inert(self):
        self.assertEqual(
            bd.unbookmark("http://127.0.0.1:1", []),
            {"requested": 0, "removed": 0, "removed_ids": [], "failed": [], "verified": True},
        )

    def test_collect_cli_never_reports_empty_when_processed_item_still_exists(self):
        collection = {"health": "ok", "items": [{"id": "123", "url": "https://x.com/alice/status/123"}], "collected_count": 1, "graphql_pages": 1, "complete": True, "truncated": False}
        with patch.object(bd, "collect", return_value=collection), patch.object(bd, "processed_tweet_ids", return_value=["123"]):
            payload = bd.collect_cli_payload("http://example", Path("state.json"), 20)
        self.assertFalse(payload["inbox_empty"])
        self.assertEqual(payload["processed_pending_removal"], ["123"])

    def test_fresh_detail_readback_prevents_false_success(self):
        result = bd.verify_removal_result(["123"], ["123"], set(), {"123": False})
        self.assertFalse(result["verified"])
        self.assertEqual(result["removed"], 0)
        self.assertIn("123", result["failed"])

    def test_fresh_detail_readback_allows_verified_success(self):
        result = bd.verify_removal_result(["123"], ["123"], set(), {"123": True})
        self.assertTrue(result["verified"])
        self.assertEqual(result["removed_ids"], ["123"])

    def test_main_dry_run_uses_receipt_gated_state_without_collection(self):
        with patch("sys.argv", ["bookmark_digest.py", "unbookmark-processed", "--dry-run"]), \
             patch.object(bd, "resolve_cdp", return_value="http://example"), \
             patch.object(bd, "processed_state_tweet_ids", return_value=["123"]), \
             patch.object(bd, "collect") as collection, \
             patch.object(bd, "unbookmark_direct") as mutation:
            self.assertEqual(bd.main(), 0)
            collection.assert_not_called()
            mutation.assert_not_called()

    def test_state_only_gate_rejects_tampered_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state = root / "state.json"
            receipt = self.write_receipt(root)
            cid = bd.mark_processed(state, "https://x.com/alice/status/123", "research", receipt)
            saved = json.loads(state.read_text())
            stored = root / saved["processed"][cid]["receipt_file"]
            stored.write_text(json.dumps({"schema_version": bd.RECEIPT_SCHEMA, "status": "accepted", "source_url": "https://x.com/alice/status/123", "consumer": "research", "receipt_id": "tampered"}), encoding="utf-8")
            self.assertEqual(bd.processed_state_tweet_ids(state), [])

    def test_main_marks_only_verified_removed_ids(self):
        with patch("sys.argv", ["bookmark_digest.py", "unbookmark-processed"]), \
             patch.object(bd, "resolve_cdp", return_value="http://example"), \
             patch.object(bd, "processed_state_tweet_ids", return_value=["123", "456"]), \
             patch.object(bd, "unbookmark_direct", return_value={"requested": 2, "removed": 1, "removed_ids": ["123"], "failed": ["456"], "verified": False}), \
             patch.object(bd, "mark_removed", return_value=["123"]) as mark:
            self.assertEqual(bd.main(), 3)
            mark.assert_called_once_with(Path.home() / ".local/state/bookmark-digest/state.json", ["123"])

    def test_main_normalizes_unexpected_errors(self):
        with patch("sys.argv", ["bookmark_digest.py", "collect"]), \
             patch.object(bd, "resolve_cdp", return_value="http://example"), \
             patch.object(bd, "collect", side_effect=KeyError("shape")):
            self.assertEqual(bd.main(), 2)


if __name__ == "__main__":
    unittest.main()
