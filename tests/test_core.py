from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
from core import list_thread_summaries, normalize_thread_id, read_thread, search_thread_summaries  # noqa: E402

THREAD = "019eaadc-d12d-7f33-84e8-cd56bba38544"


def write_rollout(home: Path, text: str = "Please fix the parser") -> Path:
    folder = home / "sessions" / "2026" / "09" / "29"
    folder.mkdir(parents=True)
    path = folder / f"rollout-2026-09-29T20-00-00-{THREAD}.jsonl"
    lines = [
        {"timestamp": "2026-09-29T20:00:00Z", "type": "session_meta", "payload": {"id": THREAD, "session_id": THREAD, "timestamp": "2026-09-29T20:00:00Z", "cwd": r"C:\\work\\project", "source": "cli", "cli_version": "0.test"}},
        {"timestamp": "2026-09-29T20:00:01Z", "type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}},
        {"timestamp": "2026-09-29T20:00:01Z", "type": "event_msg", "payload": {"type": "user_message", "message": text}},
        {"timestamp": "2026-09-29T20:00:02Z", "type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "I changed parser.py."}]}},
        {"timestamp": "2026-09-29T20:00:03Z", "type": "response_item", "payload": {"type": "reasoning", "summary": [{"type": "summary_text", "text": "hidden"}]}},
        {"timestamp": "2026-09-29T20:00:04Z", "type": "response_item", "payload": {"type": "function_call", "name": "shell", "arguments": "{\"cmd\":\"pytest\"}"}},
    ]
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    return path


class PluginTests(unittest.TestCase):
    def test_core(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            write_rollout(home, "Investigate deeplink regression")
            self.assertEqual(normalize_thread_id(f"codex://threads/{THREAD}"), THREAD)
            result = read_thread(f"codex://threads/{THREAD}", home=home)
            self.assertEqual([x["role"] for x in result["transcript"]], ["user", "assistant"])
            self.assertNotIn("hidden", json.dumps(result))
            self.assertEqual(len(list_thread_summaries(home=home)), 1)
            self.assertEqual(search_thread_summaries("regression", home=home)[0]["thread_id"], THREAD)

    def test_stdio_legacy_and_modern(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            write_rollout(home)
            env = dict(os.environ, CODEX_HOME=str(home), PYTHONUTF8="1")
            messages = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
                {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "read_thread", "arguments": {"thread": THREAD}}},
                {"jsonrpc": "2.0", "id": "d", "method": "server/discover", "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}}},
                {"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}}},
            ]
            proc = subprocess.run(
                [sys.executable, str(ROOT / "server" / "server.py")],
                input="".join(json.dumps(m) + "\n" for m in messages),
                text=True,
                capture_output=True,
                env=env,
                check=True,
            )
            replies = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
            self.assertEqual(len(replies), 5)
            self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-11-25")
            self.assertEqual(len(replies[1]["result"]["tools"]), 4)
            self.assertFalse(replies[2]["result"]["isError"])
            self.assertEqual(replies[3]["result"]["supportedVersions"], ["2026-07-28"])
            self.assertEqual(replies[4]["result"]["resultType"], "complete")


if __name__ == "__main__":
    unittest.main()
