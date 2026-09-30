from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

_UUID_RE = re.compile(r"(?i)([0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})")
_DEEPLINK_RE = re.compile(r"(?i)^codex://threads/([^/?#]+)")
_VALID_SUFFIXES = (".jsonl", ".jsonl.zst")


class ThreadReaderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ThreadSummary:
    thread_id: str
    path: str
    archived: bool
    created_at: str | None
    updated_at: str | None
    cwd: str | None
    source: str | None
    model_provider: str | None
    cli_version: str | None
    preview: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TranscriptItem:
    timestamp: str | None
    role: str
    text: str
    kind: str = "message"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser().resolve() if configured else (Path.home() / ".codex").resolve()


def normalize_thread_id(value: str) -> str:
    if not isinstance(value, str):
        raise ThreadReaderError("thread must be a string")
    raw = value.strip()
    match = _DEEPLINK_RE.match(raw)
    if match:
        raw = match.group(1)
    match = _UUID_RE.search(raw)
    if not match:
        raise ThreadReaderError("Expected codex://threads/<uuid> or a bare Codex thread UUID.")
    return match.group(1).lower()


def _is_rollout_path(path: Path) -> bool:
    name = path.name.lower()
    return name.startswith("rollout-") and name.endswith(_VALID_SUFFIXES)


def _filename_thread_id(path: Path) -> str | None:
    matches = _UUID_RE.findall(path.name)
    return matches[0].lower() if matches else None


def zstd_backend() -> str | None:
    try:
        from compression import zstd as _  # type: ignore[attr-defined]
        return "python-stdlib-compression.zstd"
    except (ImportError, ModuleNotFoundError):
        pass
    try:
        import zstandard as _  # type: ignore[import-not-found]
        return "python-zstandard"
    except ImportError:
        pass
    if shutil.which("zstd"):
        return "zstd-cli"
    return None


@contextmanager
def _open_zstd_text(path: Path):
    try:
        from compression import zstd  # type: ignore[attr-defined]
        with zstd.open(path, "rt", encoding="utf-8", errors="replace") as handle:
            yield handle
        return
    except (ImportError, ModuleNotFoundError):
        pass

    try:
        import zstandard as zstd  # type: ignore[import-not-found]
    except ImportError:
        zstd = None
    if zstd is not None:
        raw = path.open("rb")
        reader = zstd.ZstdDecompressor().stream_reader(raw)
        text = io.TextIOWrapper(reader, encoding="utf-8", errors="replace")
        try:
            yield text
        finally:
            text.close()
        return

    exe = shutil.which("zstd")
    if exe:
        proc = subprocess.Popen(
            [exe, "-q", "-d", "-c", "--", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert proc.stdout is not None
        try:
            yield proc.stdout
        finally:
            proc.stdout.close()
            stderr = proc.stderr.read() if proc.stderr is not None else ""
            code = proc.wait()
            if proc.stderr is not None:
                proc.stderr.close()
            if code != 0:
                raise ThreadReaderError(f"zstd failed to decode {path.name}: {stderr.strip() or f'exit {code}'}")
        return

    raise ThreadReaderError(
        f"{path.name} is Zstandard-compressed, but no decoder is available. "
        "Use Python 3.14+ with compression.zstd, install the Python package 'zstandard', "
        "or install the 'zstd' command-line utility."
    )


@contextmanager
def _open_text(path: Path):
    if path.name.lower().endswith(".jsonl.zst"):
        with _open_zstd_text(path) as handle:
            yield handle
    else:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            yield handle


def _iter_json(path: Path) -> Iterator[dict[str, Any]]:
    try:
        with _open_text(path) as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except OSError as exc:
        raise ThreadReaderError(f"Could not read rollout file {path}: {exc}") from exc


def _session_meta_from_obj(obj: dict[str, Any]) -> dict[str, Any] | None:
    if obj.get("type") != "session_meta":
        return None
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        return None
    meta = payload.get("meta")
    return meta if isinstance(meta, dict) else payload


def read_session_meta(path: Path) -> dict[str, Any]:
    for obj in _iter_json(path):
        meta = _session_meta_from_obj(obj)
        if meta is not None:
            return meta
        if obj.get("type") in {"response_item", "event_msg"}:
            break
    return {}


def _iter_rollout_paths(home: Path, include_archived: bool = True) -> Iterator[tuple[Path, bool]]:
    roots = [(home / "sessions", False)]
    if include_archived:
        roots.append((home / "archived_sessions", True))
    for root, archived in roots:
        if not root.exists():
            continue
        for path in root.rglob("rollout-*"):
            if path.is_file() and _is_rollout_path(path):
                if path.name.lower().endswith(".jsonl.zst"):
                    plain = path.with_name(path.name[:-4])
                    if plain.exists():
                        continue
                yield path, archived


def resolve_thread_path(thread: str, *, home: Path | None = None, include_archived: bool = True) -> tuple[Path, bool]:
    thread_id = normalize_thread_id(thread)
    home = (home or codex_home()).resolve()
    filename_matches: list[tuple[Path, bool]] = []
    all_paths: list[tuple[Path, bool]] = []
    for path, archived in _iter_rollout_paths(home, include_archived=include_archived):
        all_paths.append((path, archived))
        if _filename_thread_id(path) == thread_id:
            filename_matches.append((path, archived))

    def newest(candidates: list[tuple[Path, bool]]) -> tuple[Path, bool]:
        return max(candidates, key=lambda pair: pair[0].stat().st_mtime_ns)

    if filename_matches:
        verified: list[tuple[Path, bool]] = []
        for pair in filename_matches:
            meta = read_session_meta(pair[0])
            meta_id = str(meta.get("id") or meta.get("session_id") or "").lower()
            if not meta_id or meta_id == thread_id:
                verified.append(pair)
        if verified:
            return newest(verified)

    metadata_matches: list[tuple[Path, bool]] = []
    for pair in all_paths:
        meta = read_session_meta(pair[0])
        meta_id = str(meta.get("id") or meta.get("session_id") or "").lower()
        if meta_id == thread_id:
            metadata_matches.append(pair)
    if metadata_matches:
        return newest(metadata_matches)

    raise ThreadReaderError(
        f"Thread {thread_id} was not found under {home / 'sessions'}"
        + (f" or {home / 'archived_sessions'}" if include_archived else "")
        + "."
    )


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
            continue
        if not isinstance(item, dict):
            continue
        for key in ("text", "input_text", "output_text"):
            value = item.get(key)
            if isinstance(value, str) and value:
                parts.append(value)
                break
    return "\n".join(parts).strip()


def _response_message(obj: dict[str, Any]) -> TranscriptItem | None:
    if obj.get("type") != "response_item":
        return None
    payload = obj.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "message":
        return None
    role = str(payload.get("role") or "unknown")
    if role not in {"user", "assistant", "developer", "system"}:
        return None
    text = _content_text(payload.get("content"))
    if not text:
        return None
    return TranscriptItem(timestamp=obj.get("timestamp"), role=role, text=text)


def _event_message(obj: dict[str, Any]) -> TranscriptItem | None:
    if obj.get("type") != "event_msg":
        return None
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        return None
    event_type = payload.get("type")
    if event_type == "user_message":
        text, role = payload.get("message"), "user"
    elif event_type == "agent_message":
        text, role = payload.get("message"), "assistant"
    else:
        return None
    if not isinstance(text, str) or not text.strip():
        return None
    return TranscriptItem(timestamp=obj.get("timestamp"), role=role, text=text.strip())


def _tool_item(obj: dict[str, Any], max_item_chars: int) -> TranscriptItem | None:
    if obj.get("type") != "response_item":
        return None
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        return None
    kind = str(payload.get("type") or "")
    if kind not in {
        "function_call", "function_call_output", "custom_tool_call", "custom_tool_call_output",
        "web_search_call", "computer_call", "computer_call_output", "local_shell_call", "local_shell_call_output",
    }:
        return None
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if len(rendered) > max_item_chars:
        rendered = rendered[:max_item_chars] + f"… [truncated {len(rendered) - max_item_chars} chars]"
    return TranscriptItem(timestamp=obj.get("timestamp"), role="tool", text=rendered, kind=kind)


def extract_transcript(path: Path, *, include_tools: bool = False, include_instructions: bool = False, max_item_chars: int = 12_000) -> list[TranscriptItem]:
    items: list[TranscriptItem] = []
    seen_same_timestamp: set[tuple[str | None, str, str]] = set()
    for obj in _iter_json(path):
        item = _response_message(obj) or _event_message(obj)
        if item is not None:
            if item.role in {"developer", "system"} and not include_instructions:
                continue
            key = (item.timestamp, item.role, item.text)
            if key not in seen_same_timestamp:
                seen_same_timestamp.add(key)
                if len(item.text) > max_item_chars:
                    item = TranscriptItem(item.timestamp, item.role, item.text[:max_item_chars] + f"… [truncated {len(item.text) - max_item_chars} chars]", item.kind)
                items.append(item)
            continue
        if include_tools:
            tool = _tool_item(obj, max_item_chars=max_item_chars)
            if tool is not None:
                items.append(tool)
    return items


def _format_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _first_user_preview(path: Path, limit: int = 500) -> str | None:
    for obj in _iter_json(path):
        item = _response_message(obj) or _event_message(obj)
        if item is not None and item.role == "user":
            return item.text.replace("\n", " ")[:limit]
    return None


def _summary_for(path: Path, archived: bool) -> ThreadSummary:
    meta = read_session_meta(path)
    thread_id = str(meta.get("id") or meta.get("session_id") or _filename_thread_id(path) or "")
    source = meta.get("source")
    if isinstance(source, dict):
        source = json.dumps(source, ensure_ascii=False, sort_keys=True)
    return ThreadSummary(
        thread_id=thread_id,
        path=str(path),
        archived=archived,
        created_at=str(meta.get("timestamp")) if meta.get("timestamp") is not None else None,
        updated_at=_format_mtime(path),
        cwd=str(meta.get("cwd")) if meta.get("cwd") is not None else None,
        source=str(source) if source is not None else None,
        model_provider=str(meta.get("model_provider")) if meta.get("model_provider") is not None else None,
        cli_version=str(meta.get("cli_version")) if meta.get("cli_version") is not None else None,
        preview=_first_user_preview(path),
    )


def list_thread_summaries(*, home: Path | None = None, include_archived: bool = True, limit: int = 30) -> list[ThreadSummary]:
    home = (home or codex_home()).resolve()
    by_thread: dict[str, tuple[Path, bool]] = {}
    for path, archived in _iter_rollout_paths(home, include_archived=include_archived):
        thread_id = _filename_thread_id(path)
        if not thread_id:
            meta = read_session_meta(path)
            thread_id = str(meta.get("id") or meta.get("session_id") or "").lower() or None
        if not thread_id:
            continue
        current = by_thread.get(thread_id)
        if current is None or path.stat().st_mtime_ns > current[0].stat().st_mtime_ns:
            by_thread[thread_id] = (path, archived)
    selected = sorted(by_thread.values(), key=lambda p: p[0].stat().st_mtime_ns, reverse=True)
    bounded = max(1, min(int(limit), 200))
    return [_summary_for(path, archived) for path, archived in selected[:bounded]]


def search_thread_summaries(query: str, *, home: Path | None = None, include_archived: bool = True, limit: int = 20) -> list[dict[str, Any]]:
    if not isinstance(query, str):
        raise ThreadReaderError("query must be a string")
    needle = query.casefold().strip()
    if not needle:
        raise ThreadReaderError("search query must not be empty")
    home = (home or codex_home()).resolve()
    results: list[dict[str, Any]] = []
    for summary in list_thread_summaries(home=home, include_archived=include_archived, limit=200):
        path = Path(summary.path)
        match_text = None
        match_role = None
        for item in extract_transcript(path, max_item_chars=20_000):
            pos = item.text.casefold().find(needle)
            if pos >= 0:
                left = max(0, pos - 220)
                right = min(len(item.text), pos + len(query) + 420)
                match_text = item.text[left:right].replace("\n", " ")
                match_role = item.role
                break
        if match_text is not None:
            record = summary.to_dict()
            record["match_role"] = match_role
            record["match"] = match_text
            results.append(record)
            if len(results) >= max(1, min(int(limit), 100)):
                break
    return results


def read_thread(thread: str, *, home: Path | None = None, include_archived: bool = True, include_tools: bool = False, include_instructions: bool = False, max_chars: int = 120_000, end_item: int | None = None, max_item_chars: int = 12_000) -> dict[str, Any]:
    home = (home or codex_home()).resolve()
    path, archived = resolve_thread_path(thread, home=home, include_archived=include_archived)
    summary = _summary_for(path, archived)
    transcript = extract_transcript(path, include_tools=include_tools, include_instructions=include_instructions, max_item_chars=max_item_chars)

    budget = max(1_000, min(int(max_chars), 500_000))
    end = len(transcript) if end_item is None else max(0, min(int(end_item), len(transcript)))
    selected_rev: list[tuple[int, TranscriptItem]] = []
    used = 0
    idx = end - 1
    while idx >= 0:
        item = transcript[idx]
        cost = len(item.text)
        if selected_rev and used + cost > budget:
            break
        if not selected_rev and cost > budget:
            tail = item.text[-budget:]
            clipped = f"[leading {cost - budget} chars omitted]…" + tail
            selected_rev.append((idx, TranscriptItem(item.timestamp, item.role, clipped, item.kind)))
            used = budget
            idx -= 1
            break
        selected_rev.append((idx, item))
        used += cost
        idx -= 1
        if used >= budget:
            break

    selected = list(reversed(selected_rev))
    start = selected[0][0] if selected else end
    return {
        "thread": summary.to_dict(),
        "transcript": [item.to_dict() for _, item in selected],
        "truncated": start > 0,
        "returned_chars": used,
        "total_items": len(transcript),
        "start_item": start,
        "end_item": end,
        "returned_items": len(selected),
        "next_older_end_item": start if start > 0 else None,
        "notes": [
            "Read-only: no Codex history or SQLite state was modified.",
            "Hidden reasoning records are intentionally excluded.",
            "The newest part of the requested range is returned first when the character budget truncates history.",
            "Use next_older_end_item as end_item to page backward through older context.",
        ],
    }


def status(*, home: Path | None = None) -> dict[str, Any]:
    home = (home or codex_home()).resolve()
    sessions = home / "sessions"
    archived = home / "archived_sessions"
    counts = {"sessions": 0, "archived_sessions": 0, "compressed": 0}
    for path, is_archived in _iter_rollout_paths(home, include_archived=True):
        counts["archived_sessions" if is_archived else "sessions"] += 1
        if path.name.lower().endswith(".zst"):
            counts["compressed"] += 1
    return {
        "codex_home": str(home),
        "codex_home_exists": home.exists(),
        "sessions_path": str(sessions),
        "sessions_exists": sessions.exists(),
        "archived_sessions_path": str(archived),
        "archived_sessions_exists": archived.exists(),
        "rollout_counts": counts,
        "python": sys.version.split()[0],
        "zstd_backend": zstd_backend(),
        "read_only": True,
    }
