"""scripts/extract_trace.py: turning a log copied from Render into the verifier's trace file."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import extract_trace  # noqa: E402


def line(ts, **extra):
    record = {"ts": ts, "service": "verifier", "run_id": "r1", "request_id": "q1", "step": 0, "direction": "in",
              "endpoint_or_tool": "POST /verify", "status": 200, "latency_ms": 1200, "attempt": 1, "tokens": 0}
    record.update(extra)
    return json.dumps(record)


class ExtractTraceTests(unittest.TestCase):
    def test_keeps_trace_lines_and_drops_render_noise(self):
        log = "\n".join([
            "==> Your service is live 🎉",
            line("2026-11-02T10:00:02.000Z"),
            "127.0.0.1 - - [02/Nov/2026] \"GET /healthz HTTP/1.1\" 200 -",
            "",
            line("2026-11-02T10:00:01.000Z", direction="out", endpoint_or_tool="model POST api.example.com/v1"),
        ])
        lines, skipped = extract_trace.trace_lines(log)
        self.assertEqual(skipped, 2)
        self.assertEqual([json.loads(x)["ts"] for x in lines], ["2026-11-02T10:00:01.000Z", "2026-11-02T10:00:02.000Z"],
                         "written in time order")

    def test_a_prefix_or_suffix_around_the_json_is_ignored(self):
        lines, _ = extract_trace.trace_lines("Nov 2 10:00:03 AM  " + line("2026-11-02T10:00:03.000Z") + "   [render]")
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["status"], 200)

    def test_exact_duplicates_from_overlapping_copies_are_dropped_but_similar_lines_are_not(self):
        a = line("2026-11-02T10:00:01.000Z")
        b = line("2026-11-02T10:00:01.000Z", attempt=2)
        lines, _ = extract_trace.trace_lines("\n".join([a, a, b]))
        self.assertEqual(len(lines), 2)

    def test_json_that_is_not_a_trace_line_is_not_kept(self):
        lines, skipped = extract_trace.trace_lines('{"hello": "world"}\n{"ts": "x", "service": "verifier"}\n[1, 2]')
        self.assertEqual((lines, skipped), ([], 3))

    def test_a_line_is_not_changed_inside(self):
        original = line("2026-11-02T10:00:01.000Z", detail="replayed: stored answer, no new work")
        lines, _ = extract_trace.trace_lines(original)
        self.assertEqual(json.loads(lines[0]), json.loads(original))

    def test_the_command_writes_the_file_and_says_what_it_did(self):
        with tempfile.TemporaryDirectory() as folder:
            source, out = Path(folder, "render.txt"), Path(folder, "sub", "verifier.jsonl")
            source.write_text("noise\n" + line("2026-11-02T10:00:01.000Z") + "\n", encoding="utf-8")
            self.assertEqual(extract_trace.main([str(source), "--out", str(out)]), 0)
            self.assertEqual(len(out.read_text(encoding="utf-8").splitlines()), 1)

    def test_a_log_without_trace_lines_is_reported_not_written(self):
        with tempfile.TemporaryDirectory() as folder:
            source, out = Path(folder, "render.txt"), Path(folder, "verifier.jsonl")
            source.write_text("==> Deploying...\n", encoding="utf-8")
            self.assertEqual(extract_trace.main([str(source), "--out", str(out)]), 1)
            self.assertFalse(out.exists())
            self.assertEqual(extract_trace.main([str(Path(folder, "missing.txt")), "--out", str(out)]), 2)


if __name__ == "__main__":
    unittest.main()
