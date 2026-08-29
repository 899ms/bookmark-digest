import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import scheduler_runner as sr


class SchedulerRunnerTests(unittest.TestCase):
    def run_main(self, argv, *, collect_payload, consumer_stdout, consumer_stderr="", consumer_code=0, extra_run_json=None):
        calls = []

        def fake_run_json(cmd):
            calls.append(list(cmd))
            if len(calls) == 1:
                return collect_payload
            if extra_run_json:
                return extra_run_json(cmd, calls)
            return {"status": "processed"}

        def fake_subprocess_run(cmd, **kwargs):
            return SimpleNamespace(returncode=consumer_code, stdout=consumer_stdout, stderr=consumer_stderr)

        with tempfile.TemporaryDirectory() as td, \
             patch.object(sr, "STATE_DIR", Path(td)), \
             patch.object(sr, "run_json", side_effect=fake_run_json), \
             patch.object(sr.subprocess, "run", side_effect=fake_subprocess_run), \
             patch("sys.argv", argv):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = sr.main()
            leftovers = list(Path(td).glob("inbox-*.json"))
        return code, buf.getvalue(), calls, leftovers

    def test_rejects_receipt_not_in_current_inbox_before_commit(self):
        payload = {"items": [{"id": "123", "url": "https://x.com/a/status/123"}], "inbox_empty": False}
        manifest = json.dumps({"receipts": [{
            "receipt_file": "/tmp/r.json",
            "source_url": "https://x.com/b/status/999",
            "consumer": "research",
        }]})
        code, output, calls, leftovers = self.run_main(
            ["scheduler_runner.py", "--consumer-command", "fake-consumer"],
            collect_payload=payload,
            consumer_stdout=manifest,
        )
        self.assertEqual(code, 2)
        self.assertIn("receipt_not_in_current_inbox", output)
        self.assertEqual(len(calls), 1)
        self.assertEqual(leftovers, [])

    def test_auto_unbookmark_is_scoped_to_current_committed_ids(self):
        payload = {"items": [
            {"id": "123", "url": "https://x.com/a/status/123"},
            {"id": "456", "url": "https://x.com/b/status/456"},
        ], "inbox_empty": False}
        manifest = json.dumps({"receipts": [{
            "receipt_file": "/tmp/r123.json",
            "source_url": "https://x.com/a/status/123",
            "consumer": "research",
        }]})

        def extra(cmd, calls):
            if "commit" in cmd:
                return {"candidate_id": "sig_123", "status": "processed"}
            if "unbookmark-processed" in cmd:
                return {"requested": 1, "removed": 1, "removed_ids": ["123"], "verified": True}
            raise AssertionError(cmd)

        code, output, calls, leftovers = self.run_main(
            ["scheduler_runner.py", "--consumer-command", "fake-consumer", "--auto-unbookmark"],
            collect_payload=payload,
            consumer_stdout=manifest,
            extra_run_json=extra,
        )
        self.assertEqual(code, 0)
        mutation = next(cmd for cmd in calls if "unbookmark-processed" in cmd)
        self.assertIn("--tweet-id", mutation)
        self.assertIn("123", mutation)
        self.assertNotIn("456", mutation)
        self.assertEqual(leftovers, [])
        self.assertIn('"committed": 1', output)

    def test_consumer_stderr_is_not_persisted_to_runner_output(self):
        payload = {"items": [{"id": "123", "url": "https://x.com/a/status/123"}], "inbox_empty": False}
        secret = "SECRET_BOOKMARK_TEXT_TOKEN"
        code, output, calls, leftovers = self.run_main(
            ["scheduler_runner.py", "--consumer-command", "fake-consumer"],
            collect_payload=payload,
            consumer_stdout="",
            consumer_stderr=secret,
            consumer_code=9,
        )
        self.assertEqual(code, 2)
        self.assertNotIn(secret, output)
        self.assertIn("consumer_exit", output)
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
