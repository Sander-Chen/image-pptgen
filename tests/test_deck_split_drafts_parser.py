from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TOOLKIT_SRC = ROOT / "packages" / "pptgen_toolkit" / "src"
if str(TOOLKIT_SRC) not in sys.path:
    sys.path.insert(0, str(TOOLKIT_SRC))

import db as dbmod
from backend.services import codex_exec, deck_split_drafts, public_image_surface
from backend.services.codex_executable import CodexExecutableUnavailable
from pptgen_toolkit.client import PptgenClient
from splitter import split_by_markdown


SOURCE = (
    "# Source title\n\n"
    "## 第一章\n\n"
    "第一章保留普通词语 Alpha，并保留编号 42。\n\n"
    "## 第二章\n\n"
    "第二章保留普通词语 Beta，并保留编号 7。\n"
)
LONG_SOURCE = (
    "# Source title\n\n"
    "## 第一章\n\n"
    + ("第一章忠实内容，" * 40)
    + "\n\n## 第二章\n\n"
    + ("第二章忠实内容，" * 40)
    + "最终编号 7。\n"
)


def test_truncated_faithful_source_reconstructs_complete_pages() -> None:
    truncated = LONG_SOURCE[: LONG_SOURCE.index("最终编号") + 3]

    slides = deck_split_drafts._parse_generated_slides(
        truncated,
        source_content=LONG_SOURCE,
        allow_source_reconstruction=True,
    )

    expected = [
        {**slide, "split_mode": "llm_auto"}
        for slide in split_by_markdown(LONG_SOURCE) or []
    ]
    assert slides == expected


def test_source_reconstruction_rejects_rewritten_output() -> None:
    with pytest.raises(json.JSONDecodeError):
        deck_split_drafts._parse_generated_slides(
            "# Rewritten\n\n## 第一章\n\nParaphrased content.",
            source_content=SOURCE,
            allow_source_reconstruction=True,
        )


def test_fenced_faithful_markdown_uses_existing_heading_splitter() -> None:
    slides = deck_split_drafts._parse_generated_slides(f"```markdown\n{SOURCE}\n```")

    assert [slide["title"] for slide in slides] == ["第一章", "第二章"]
    assert [slide["split_mode"] for slide in slides] == ["llm_auto", "llm_auto"]


def test_compact_boundary_plan_reconstructs_source_bodies() -> None:
    raw = (
        "```json\n"
        "[{\"title\":\"第一页\",\"section_ids\":[1]},"
        "{\"title\":\"第二页\",\"section_ids\":[2]}]\n"
        "```"
    )

    slides = deck_split_drafts._parse_generated_slides(raw, source_content=SOURCE)

    assert [slide["title"] for slide in slides] == ["第一页", "第二页"]
    assert slides[0]["content"].endswith("编号 42。")
    assert slides[1]["content"].endswith("编号 7。")


def test_compact_contract_is_codex_only() -> None:
    prompt = "canonical prompt"

    codex_prompt = deck_split_drafts._prompt_for_split(
        {"api_type": "codex_exec"}, prompt, SOURCE
    )
    assert "section_ids" in codex_prompt
    assert '"id": 1, "title": "第一章"' in codex_prompt
    assert deck_split_drafts._prompt_for_split(
        {"api_type": "gemini"}, prompt, SOURCE
    ) == prompt


def test_compact_boundary_plan_rejects_gaps_reordering_and_duplicates() -> None:
    for section_ids in ([2, 1], [1, 1], [2], [1, 3]):
        raw = json.dumps([{"title": "无效", "section_ids": section_ids}])
        parsed = deck_split_drafts._parse_generated_slides(
            raw,
            source_content=SOURCE,
        )
        assert parsed == [{"title": "无效", "section_ids": section_ids}]


def test_revision_accepts_compact_boundary_plan(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_run(_config, prompt, *, stage_id):
        captured["prompt"] = prompt
        assert stage_id == "deck-split-revision"
        return (
            "```json\n"
            "[{\"title\":\"第二页\",\"section_ids\":[1,2]}]\n"
            "```"
        )

    monkeypatch.setattr(deck_split_drafts, "_run_split", fake_run)
    slides = deck_split_drafts.generate_split_revision(
        SOURCE,
        [{"title": "第一章", "content": "旧内容"}],
        "只调整分页",
        {"api_type": "codex_exec", "model": "gpt-5.6-luna", "thinking": "low"},
        content_mode="faithful",
    )

    assert "section_ids" in captured["prompt"]
    assert slides[0]["title"] == "第二页"
    assert slides[0]["content"].endswith("编号 7。")


def test_generate_llm_split_reconstructs_codex_source_prefix(monkeypatch) -> None:
    truncated = LONG_SOURCE[: LONG_SOURCE.index("最终编号") + 3]
    monkeypatch.setattr(
        deck_split_drafts,
        "_run_split",
        lambda *_args, **_kwargs: truncated,
    )

    slides = deck_split_drafts.generate_llm_split(
        LONG_SOURCE,
        {"api_type": "codex_exec", "model": "gpt-5.6-luna", "thinking": "low"},
        "prompt",
    )

    assert [slide["title"] for slide in slides] == ["第一章", "第二章"]
    assert slides[1]["content"].endswith("编号 7。")


def test_chinese_history_manifest_uses_all_server_derived_units() -> None:
    source = (ROOT / "eval-materials" / "chinese-history.md").read_text()

    prompt = deck_split_drafts._prompt_for_split(
        {"api_type": "codex_exec"},
        "canonical prompt",
        source,
    )

    sections = split_by_markdown(source) or []
    assert len(sections) == 7
    assert '"id": 1' in prompt
    assert '"id": 7' in prompt
    assert "section_ids" in prompt


def _long_plain_text_source() -> str:
    paragraphs = []
    for index in range(1, 181):
        paragraphs.append(
            f"第{index}段保留原始事实 Alpha-{index}，包含编号 {1000 + index}。"
            "这是一段没有 Markdown 标题的长文本，所有词语、数字和标点都必须按原顺序保留。"
        )
    return "\n\n".join(paragraphs)


def _public_luna_config() -> dict[str, str]:
    return {
        "api_type": "codex_exec",
        "content_mode": "faithful",
        "profile_name": "AutoSplit · GPT-5.6 Luna",
        "model": "gpt-5.6-luna",
    }


def test_public_plain_text_prompt_requests_boundaries_without_body_authorship() -> None:
    source = _long_plain_text_source()
    assert len(source.encode("utf-8")) > 23_000

    prompt = deck_split_drafts._prompt_for_split(
        _public_luna_config(),
        "legacy prompt that asks for rewritten Markdown",
        source,
    )
    units = deck_split_drafts._ordered_source_units(source)

    assert len(units) > 4
    assert "legacy prompt that asks for rewritten Markdown" not in prompt
    assert "section_ids" in prompt
    assert "never copy them into the response" in prompt
    assert json.dumps(units[0]["content"], ensure_ascii=False) in prompt


def test_public_plain_text_boundary_plan_reconstructs_exact_source_tokens() -> None:
    source = _long_plain_text_source()
    units = deck_split_drafts._ordered_source_units(source)
    midpoint = len(units) // 2
    raw = json.dumps(
        [
            {"title": "上半部分", "section_ids": list(range(1, midpoint + 1))},
            {
                "title": "下半部分",
                "section_ids": list(range(midpoint + 1, len(units) + 1)),
            },
        ],
        ensure_ascii=False,
    )

    slides = deck_split_drafts._parse_generated_slides(raw, source_content=source)

    assert [slide["title"] for slide in slides] == ["上半部分", "下半部分"]
    assert public_image_surface._public_split_body_tokens(source) == (
        public_image_surface._public_split_body_tokens(
            "\n".join(slide["content"] for slide in slides)
        )
    )


@pytest.mark.parametrize(
    "plan",
    [
        [{"title": "缺失", "section_ids": [1]}],
        [{"title": "重复", "section_ids": [1, 1]}],
        [{"title": "乱序", "section_ids": [2, 1]}],
        [{"title": "越界", "section_ids": [1, 999]}],
        [{"title": "模型正文", "section_ids": [1], "content": "不得接受"}],
    ],
)
def test_public_plain_text_boundary_plan_fails_closed_for_invalid_ids_and_body(plan) -> None:
    source = _long_plain_text_source()
    parsed = deck_split_drafts._parse_generated_slides(
        json.dumps(plan, ensure_ascii=False),
        source_content=source,
    )

    assert parsed == plan


def test_public_plain_text_near_complete_markdown_remains_rejected_by_parity() -> None:
    source = _long_plain_text_source()
    omitted = source.rsplit("\n\n", 1)[0]
    first_half, second_half = omitted.split("\n\n", 1)
    model_authored = (
        f"# 演示文稿\n\n## 第一页\n\n{first_half}\n\n"
        f"## 第二页\n\n{second_half}"
    )

    slides = deck_split_drafts._parse_generated_slides(
        model_authored,
        source_content=source,
    )

    with pytest.raises(deck_split_drafts.SplitDraftError, match="source parity mismatch"):
        public_image_surface._public_split_source_parity(source, slides)


def _codex_result_for_raw_jsonl(tmp_path: Path, raw: str, *, exit_code: int = 1):
    tmp_path.mkdir(parents=True, exist_ok=True)
    raw_path = tmp_path / "codex.raw.jsonl"
    raw_path.write_text(raw, encoding="utf-8")
    return SimpleNamespace(
        exit_code=exit_code,
        timed_out=False,
        raw_jsonl_path=raw_path,
    )


def _transport_terminal() -> str:
    events = [
        {"type": "thread.started", "thread_id": "thread-test"},
        {"type": "turn.started"},
        {
            "type": "error",
            "message": "Reconnecting... 2/5 (stream disconnected before completion: No route to host (os error 65))",
        },
        {
            "type": "item.completed",
            "item": {
                "id": "item_0",
                "type": "error",
                "message": "Falling back from WebSockets to HTTPS transport. stream disconnected before completion: No route to host (os error 65)",
            },
        },
        {
            "type": "turn.failed",
            "error": {
                "message": "stream disconnected before completion: error sending request for url (https://chatgpt.com/backend-api/codex/responses)"
            },
        },
    ]
    return "".join(json.dumps(event) + "\n" for event in events)


def test_transport_classifier_accepts_only_bounded_raw_terminal(tmp_path: Path) -> None:
    result = _codex_result_for_raw_jsonl(tmp_path, _transport_terminal())

    assert deck_split_drafts.is_transport_only_codex_failure(result) is True


def test_transport_classifier_rejects_missing_terminal_malformed_and_oversized_streams(
    tmp_path: Path,
) -> None:
    no_terminal = _transport_terminal().replace('"type": "turn.failed"', '"type": "turn.started"')
    malformed = _codex_result_for_raw_jsonl(tmp_path / "malformed", '{"type": "turn.failed"\n')
    assert deck_split_drafts.is_transport_only_codex_failure(
        _codex_result_for_raw_jsonl(tmp_path, no_terminal)
    ) is False
    assert deck_split_drafts.is_transport_only_codex_failure(malformed) is False

    oversized_dir = tmp_path / "oversized"
    oversized_dir.mkdir()
    oversized = _codex_result_for_raw_jsonl(
        oversized_dir,
        json.dumps(
            {
                "type": "turn.failed",
                "error": {"message": "stream disconnected " + ("x" * 70000)},
            }
        ),
    )
    assert deck_split_drafts.is_transport_only_codex_failure(oversized) is False


def test_transport_classifier_rejects_similar_non_transport_provider_failure(
    tmp_path: Path,
) -> None:
    raw = _transport_terminal().replace(
        "stream disconnected before completion: error sending request for url",
        "model rejected request after stream disconnected before completion: error sending request for url",
    )

    assert deck_split_drafts.is_transport_only_codex_failure(
        _codex_result_for_raw_jsonl(tmp_path, raw)
    ) is False

    unproven_transport = _transport_terminal().replace(
        "No route to host", "connection reset by peer"
    )
    assert deck_split_drafts.is_transport_only_codex_failure(
        _codex_result_for_raw_jsonl(tmp_path / "unproven", unproven_transport)
    ) is False


def test_codex_split_keeps_public_error_message_but_marks_transport_internal(
    tmp_path: Path, monkeypatch
) -> None:
    result = _codex_result_for_raw_jsonl(tmp_path, _transport_terminal())
    result.final_text = ""

    async def fake_runner(**_kwargs):
        return result

    monkeypatch.setattr(deck_split_drafts, "run_codex_exec_json", fake_runner)
    with pytest.raises(deck_split_drafts.SplitExecutionFailure) as caught:
        deck_split_drafts._run_codex_split(
            {"model": "gpt-5.6-luna", "thinking": "low"},
            "prompt",
            stage_id="deck-split",
        )

    assert caught.value.code == "provider_rejected"
    assert caught.value.transport_only is True
    assert caught.value.message == "Local Codex Exec rejected the Auto Split request"


def test_public_codex_split_forwards_only_the_approved_session_isolation(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_runner(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(exit_code=0, timed_out=False, final_text="split result")

    monkeypatch.setattr(deck_split_drafts, "run_codex_exec_json", fake_runner)

    result = deck_split_drafts._run_codex_split(
        {
            "profile_name": "AutoSplit · GPT-5.6 Luna",
            "model": "gpt-5.6-luna",
            "thinking": "low",
        },
        "prompt",
        stage_id="deck-split",
    )

    assert result == "split result"
    assert captured["extra_config"] == [
        "features.apps=false",
        "features.plugins=false",
        "apps._default.enabled=false",
    ]
    assert captured["timeout_seconds"] == 840
    assert captured["admission_timeout_seconds"] == 30
    assert captured["timeout_seconds"] < PptgenClient("http://127.0.0.1:3130").long_timeout
    assert (
        captured["timeout_seconds"] + codex_exec._PROCESS_TREE_TERMINATION_GRACE_SECONDS
        < PptgenClient("http://127.0.0.1:3130").long_timeout
    )


def test_non_public_codex_split_does_not_inherit_public_session_isolation(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_runner(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(exit_code=0, timed_out=False, final_text="split result")

    monkeypatch.setattr(deck_split_drafts, "run_codex_exec_json", fake_runner)

    assert deck_split_drafts._run_codex_split(
        {
            "profile_name": "Custom AutoSplit",
            "model": "gpt-5.6-terra",
            "thinking": "low",
        },
        "prompt",
        stage_id="generic-auto-split",
    ) == "split result"
    assert "extra_config" not in captured
    assert "timeout_seconds" not in captured
    assert "admission_timeout_seconds" not in captured


def test_gate_capacity_timeout_is_a_non_retryable_resource_failure() -> None:
    failure = deck_split_drafts._failure_for_exception(
        deck_split_drafts.CodexGateCapacityTimeout("capacity exhausted")
    )

    assert failure.code == "resource_unavailable"
    assert failure.transport_only is False
    assert "retry" in failure.message.lower()


def test_executable_identity_failure_is_typed_and_not_transport_retryable() -> None:
    failure = deck_split_drafts._failure_for_exception(
        CodexExecutableUnavailable(
            "windows_cache_candidate_missing",
            path=Path(r"C:\Users\agent\AppData\Local\OpenAI\Codex\bin"),
        )
    )

    assert failure.code == "executable_identity_unavailable"
    assert failure.transport_only is False
    assert "C:\\Users" not in failure.message
    assert "hash" not in failure.message.lower()


def test_resource_unavailable_schema_upgrade_preserves_existing_drafts(
    tmp_path: Path,
) -> None:
    connection = sqlite3.connect(tmp_path / "legacy-drafts.db")
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE decks (id INTEGER PRIMARY KEY)")
    connection.execute("CREATE TABLE model_profiles (id INTEGER PRIMARY KEY)")
    connection.execute(
        """CREATE TABLE deck_split_drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
            status TEXT DEFAULT 'pending',
            mode TEXT NOT NULL,
            model TEXT,
            model_profile_id INTEGER REFERENCES model_profiles(id),
            thinking_effort TEXT CHECK (thinking_effort IN ('low', 'medium', 'high')),
            content_mode TEXT CHECK (content_mode IN ('faithful', 'editorial')),
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_error_code TEXT CHECK (
                last_error_code IN ('configuration', 'timeout', 'provider_rejected', 'parse', 'integrity')
            ),
            slides_json TEXT NOT NULL,
            error_message TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            confirmed_at TEXT
        )"""
    )
    connection.execute("INSERT INTO decks (id) VALUES (7)")
    connection.execute("INSERT INTO model_profiles (id) VALUES (3)")
    connection.execute(
        """INSERT INTO deck_split_drafts
           (deck_id, status, mode, model, model_profile_id, thinking_effort,
            content_mode, attempt_count, last_error_code, slides_json, error_message)
           VALUES (7, 'failed', 'llm_auto', 'gpt-5.6-luna', 3, 'low', 'faithful',
                   1, 'timeout', '[]', 'previous timeout')"""
    )

    dbmod._upgrade_deck_split_draft_error_codes(connection)
    dbmod._upgrade_deck_split_draft_error_codes(connection)
    connection.execute(
        "UPDATE deck_split_drafts SET last_error_code = 'resource_unavailable' WHERE id = 1"
    )
    connection.execute(
        "UPDATE deck_split_drafts SET last_error_code = 'executable_identity_unavailable' WHERE id = 1"
    )
    preserved = connection.execute("SELECT * FROM deck_split_drafts WHERE id = 1").fetchone()
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "UPDATE deck_split_drafts SET last_error_code = 'unknown_error' WHERE id = 1"
        )
    connection.close()

    assert dict(preserved) == {
        "id": 1,
        "deck_id": 7,
        "status": "failed",
        "mode": "llm_auto",
        "model": "gpt-5.6-luna",
        "model_profile_id": 3,
        "thinking_effort": "low",
        "content_mode": "faithful",
        "attempt_count": 1,
        "last_error_code": "executable_identity_unavailable",
        "slides_json": "[]",
        "error_message": "previous timeout",
        "created_at": preserved["created_at"],
        "confirmed_at": None,
    }


def test_split_error_code_schema_upgrade_rolls_back_on_rename_failure(
    tmp_path: Path,
) -> None:
    connection = sqlite3.connect(tmp_path / "legacy-drafts-rollback.db")
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE decks (id INTEGER PRIMARY KEY)")
    connection.execute("CREATE TABLE model_profiles (id INTEGER PRIMARY KEY)")
    connection.execute(
        """CREATE TABLE deck_split_drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
            status TEXT DEFAULT 'pending', mode TEXT NOT NULL, model TEXT,
            model_profile_id INTEGER REFERENCES model_profiles(id),
            thinking_effort TEXT, content_mode TEXT, attempt_count INTEGER NOT NULL DEFAULT 0,
            last_error_code TEXT CHECK (last_error_code IN ('configuration', 'timeout', 'provider_rejected', 'parse', 'integrity')),
            slides_json TEXT NOT NULL, error_message TEXT, created_at TEXT DEFAULT (datetime('now')), confirmed_at TEXT
        )"""
    )
    connection.execute("INSERT INTO decks (id) VALUES (7)")
    connection.execute(
        "INSERT INTO deck_split_drafts (deck_id, mode, slides_json) VALUES (7, 'llm_auto', '[]')"
    )
    # A stale migration table makes the rename fail. The SAVEPOINT must leave
    # both the original schema and its row untouched.
    connection.execute(
        "CREATE TABLE deck_split_drafts_r34_error_codes_legacy (id INTEGER PRIMARY KEY)"
    )

    with pytest.raises(sqlite3.OperationalError):
        dbmod._upgrade_deck_split_draft_error_codes(connection)

    table_sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'deck_split_drafts'"
    ).fetchone()[0]
    row = connection.execute("SELECT deck_id, mode, slides_json FROM deck_split_drafts").fetchone()
    assert "executable_identity_unavailable" not in table_sql
    assert tuple(row) == (7, "llm_auto", "[]")
    assert connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='deck_split_drafts_r34_error_codes_legacy'"
    ).fetchone()
    connection.close()


def test_target_page_count_partitions_ordered_units_by_content_weight() -> None:
    source = (
        "# Source\n\n"
        "## A\n\nshort A\n\n"
        "## B\n\nlong B one two three four five\n\n"
        "## C\n\nmedium C one two\n\n"
        "## D\n\nshort D"
    )

    slides = deck_split_drafts._target_page_count_slides(source, 2)

    assert len(slides) == 2
    assert [slide["title"] for slide in slides] == ["A / B", "C / D"]
    assert "short A" in slides[0]["content"]
    assert "long B one two three four five" in slides[0]["content"]
    assert "medium C one two" in slides[1]["content"]
    assert "short D" in slides[1]["content"]
    assert [slide["split_mode"] for slide in slides] == ["llm_auto", "llm_auto"]


def test_target_page_count_uses_source_content_length_for_boundaries() -> None:
    source = (
        "## A\n\n"
        + ("A" * 20)
        + "\n\n## B\n\n"
        + ("B" * 20)
        + "\n\n## C\n\n"
        + ("C" * 100)
    )

    slides = deck_split_drafts._target_page_count_slides(source, 2)

    assert [slide["title"] for slide in slides] == ["A / B", "C"]


def test_target_page_count_rejects_unreachable_count_without_partial_pages() -> None:
    with pytest.raises(deck_split_drafts.TargetPageCountUnavailable):
        deck_split_drafts._target_page_count_slides(SOURCE, 3)

    with pytest.raises(deck_split_drafts.TargetPageCountUnavailable):
        deck_split_drafts._target_page_count_slides(SOURCE, 0)

    with pytest.raises(deck_split_drafts.TargetPageCountUnavailable):
        deck_split_drafts._target_page_count_slides(
            "## 第一章\n\n有内容\n\n## 第二章\n\n",
            2,
        )


THREEBODY_FIRST_10000_PATH = ROOT / "threebody-first-10000.txt"
THREEBODY_FIRST_10000_SHA256 = (
    "a34ec2532ce6a7335819054f535dfd2183e88e71c0e2510c8cf9630e15da2d1f"
)
PLAIN_TEXT_EXPANDABLE_SOURCE = "\n\n".join(
    (
        f"第{index}段保留原始事实 Alpha-{index}，包含编号 {1000 + index}。"
        "这是一段没有 Markdown 标题的普通文本。"
    )
    for index in range(1, 7)
)
PLAIN_TEXT_INSUFFICIENT_SOURCE = "唯一连续事实Alpha编号42没有空白也没有句读"
STRUCTURED_MARKDOWN_WITH_SAFE_SPANS = (
    "## 第一章\n\n"
    "第一章句子一。第一章句子二。第一章句子三。第一章句子四。第一章句子五。\n\n"
    "## 第二章\n\n"
    "第二章句子一。第二章句子二。第二章句子三。第二章句子四。第二章句子五。"
)


def _assert_source_token_parity(source: str, slides: list[dict]) -> None:
    assert public_image_surface._public_split_body_tokens(source) == (
        public_image_surface._public_split_body_tokens(
            "\n".join(slide["content"] for slide in slides)
        )
    )


def _threebody_first_10000_source() -> str:
    if not THREEBODY_FIRST_10000_PATH.is_file():
        pytest.skip(
            "untracked acceptance material threebody-first-10000.txt is absent"
        )
    source = THREEBODY_FIRST_10000_PATH.read_text(encoding="utf-8")
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    assert len(source) == 10_000
    assert digest == THREEBODY_FIRST_10000_SHA256
    return source


def test_plain_text_target_page_count_expands_committed_fixture() -> None:
    units = deck_split_drafts._ordered_source_units(
        PLAIN_TEXT_EXPANDABLE_SOURCE, prefer_explicit_h1=True
    )
    assert 0 < len(units) < 5

    slides = deck_split_drafts._target_page_count_slides(
        PLAIN_TEXT_EXPANDABLE_SOURCE, 5, prefer_explicit_h1=True
    )

    assert len(slides) == 5
    assert all(slide["content"].strip() for slide in slides)
    assert [slide["split_mode"] for slide in slides] == ["llm_auto"] * 5
    _assert_source_token_parity(PLAIN_TEXT_EXPANDABLE_SOURCE, slides)


def test_plain_text_target_page_count_expands_threebody_exact_file_to_15() -> None:
    source = _threebody_first_10000_source()
    units = deck_split_drafts._ordered_source_units(
        source, prefer_explicit_h1=True
    )
    assert len(units) == 8

    slides = deck_split_drafts._target_page_count_slides(
        source, 15, prefer_explicit_h1=True
    )

    assert len(slides) == 15
    assert all(slide["content"].strip() for slide in slides)
    assert [slide["split_mode"] for slide in slides] == ["llm_auto"] * 15
    _assert_source_token_parity(source, slides)


def test_plain_text_target_page_count_is_unavailable_without_enough_safe_spans() -> None:
    units = deck_split_drafts._ordered_source_units(
        PLAIN_TEXT_INSUFFICIENT_SOURCE, prefer_explicit_h1=True
    )
    assert len(units) == 1

    with pytest.raises(deck_split_drafts.TargetPageCountUnavailable) as caught:
        deck_split_drafts._target_page_count_slides(
            PLAIN_TEXT_INSUFFICIENT_SOURCE, 3, prefer_explicit_h1=True
        )

    assert caught.value.code == "target_page_count_unavailable"
    assert caught.value.status_code == 422


def test_structured_markdown_target_page_count_does_not_expand_internal_spans() -> None:
    units = deck_split_drafts._ordered_source_units(
        STRUCTURED_MARKDOWN_WITH_SAFE_SPANS, prefer_explicit_h1=True
    )
    assert len(units) == 2

    equal = deck_split_drafts._target_page_count_slides(
        STRUCTURED_MARKDOWN_WITH_SAFE_SPANS, 2, prefer_explicit_h1=True
    )
    assert [slide["title"] for slide in equal] == ["第一章", "第二章"]
    _assert_source_token_parity(STRUCTURED_MARKDOWN_WITH_SAFE_SPANS, equal)

    with pytest.raises(deck_split_drafts.TargetPageCountUnavailable) as caught:
        deck_split_drafts._target_page_count_slides(
            STRUCTURED_MARKDOWN_WITH_SAFE_SPANS, 6, prefer_explicit_h1=True
        )

    assert caught.value.code == "target_page_count_unavailable"
    assert caught.value.status_code == 422


EXPLICIT_H1_THIRTEEN_PAGES = (
    ROOT / "tests" / "fixtures" / "explicit-h1-thirteen-pages.md"
).read_text(encoding="utf-8")
EXPLICIT_H1_TITLES = [f"第{index}页 显式一级标题" for index in range(1, 14)]
NESTED_H3_UNIT_TITLES = [
    "第1页 显式一级标题",
    "第2页嵌套二级",
    "第3页嵌套二级",
    "第4页嵌套二级",
]


def _prompt_source_units(prompt: str) -> list[dict]:
    start = prompt.index("```json\n") + len("```json\n")
    end = prompt.index("\n```", start)
    parsed = json.loads(prompt[start:end])
    assert isinstance(parsed, list)
    return parsed


def test_explicit_h1_helper_keeps_thirteen_fence_safe_pages() -> None:
    from splitter import split_by_explicit_h1

    sections = split_by_explicit_h1(EXPLICIT_H1_THIRTEEN_PAGES)

    assert sections is not None
    assert [section["title"] for section in sections] == EXPLICIT_H1_TITLES
    assert [section["split_mode"] for section in sections] == ["h1"] * 13
    for index, section in enumerate(sections, start=1):
        assert f"ALPHA-{index}" in section["content"]
        assert f"编号 {100 + index}。" in section["content"]
    assert "## 第1页嵌套二级" in sections[0]["content"]
    assert "### 嵌套三级甲" in sections[0]["content"]
    assert "## 第4页嵌套二级" in sections[3]["content"]
    assert "### 嵌套三级丁" in sections[3]["content"]
    assert "# 伪一级标题 围栏内" in sections[4]["content"]
    assert "# 伪一级标题 注释内" in sections[4]["content"]
    assert "伪一级标题 围栏内" not in [section["title"] for section in sections]
    assert "伪一级标题 注释内" not in [section["title"] for section in sections]


def test_explicit_h1_helper_falls_back_for_zero_or_one_h1() -> None:
    from splitter import split_by_explicit_h1

    assert split_by_explicit_h1("## 第一章\n甲\n## 第二章\n乙") is None
    assert split_by_explicit_h1("# 唯一一级\n## 第一章\n甲\n## 第二章\n乙") is None
    assert split_by_explicit_h1(
        "```markdown\n# 伪一级标题 围栏内\n```\n<!--\n# 伪一级标题 注释内\n-->\n正文"
    ) is None


def test_split_by_markdown_still_chooses_four_nested_h3_units_on_thirteen_h1_source() -> None:
    sections = split_by_markdown(EXPLICIT_H1_THIRTEEN_PAGES)

    assert sections is not None
    assert [section["title"] for section in sections] == NESTED_H3_UNIT_TITLES
    assert [section["split_mode"] for section in sections] == ["h3"] * 4


def test_public_faithful_prompt_exposes_thirteen_explicit_h1_units() -> None:
    prompt = deck_split_drafts._prompt_for_split(
        _public_luna_config(),
        "canonical prompt",
        EXPLICIT_H1_THIRTEEN_PAGES,
    )
    units = deck_split_drafts._ordered_source_units(
        EXPLICIT_H1_THIRTEEN_PAGES, prefer_explicit_h1=True
    )
    manifest = _prompt_source_units(prompt)

    assert [unit["title"] for unit in units] == EXPLICIT_H1_TITLES
    assert [item["title"] for item in manifest] == EXPLICIT_H1_TITLES
    assert [item["id"] for item in manifest] == list(range(1, 14))
    assert [item["title"] for item in manifest] != NESTED_H3_UNIT_TITLES
    for index, unit in enumerate(units, start=1):
        assert f"ALPHA-{index}" in unit["content"]
        assert f"编号 {100 + index}。" in unit["content"]
    assert "## 第2页嵌套二级" in units[1]["content"]
    assert "### 嵌套三级乙" in units[1]["content"]
    assert "# 伪一级标题 围栏内" in units[4]["content"]
    assert "# 伪一级标题 注释内" in units[4]["content"]


def test_target_page_count_thirteen_uses_explicit_h1_units_without_model() -> None:
    slides = deck_split_drafts._target_page_count_slides(
        EXPLICIT_H1_THIRTEEN_PAGES, 13, prefer_explicit_h1=True
    )

    assert [slide["title"] for slide in slides] == EXPLICIT_H1_TITLES
    assert [slide["split_mode"] for slide in slides] == ["llm_auto"] * 13
    for index, slide in enumerate(slides, start=1):
        assert f"ALPHA-{index}" in slide["content"]
        assert f"编号 {100 + index}。" in slide["content"]
    assert "## 第3页嵌套二级" in slides[2]["content"]
    assert "### 嵌套三级丙" in slides[2]["content"]
    assert "# 伪一级标题 围栏内" in slides[4]["content"]
    assert "# 伪一级标题 注释内" in slides[4]["content"]


def test_target_page_count_fourteen_is_unavailable_for_thirteen_h1_units() -> None:
    with pytest.raises(deck_split_drafts.TargetPageCountUnavailable) as caught:
        deck_split_drafts._target_page_count_slides(
            EXPLICIT_H1_THIRTEEN_PAGES, 14, prefer_explicit_h1=True
        )

    assert caught.value.code == "target_page_count_unavailable"
    assert caught.value.status_code == 422


def _non_public_codex_config() -> dict[str, str]:
    return {
        "api_type": "codex_exec",
        "content_mode": "faithful",
        "profile_name": "Custom AutoSplit",
        "model": "gpt-5.6-luna",
    }


def test_non_public_faithful_prompt_keeps_four_h3_units_on_thirteen_h1_source() -> None:
    prompt = deck_split_drafts._prompt_for_split(
        _non_public_codex_config(),
        "canonical prompt",
        EXPLICIT_H1_THIRTEEN_PAGES,
    )
    units = deck_split_drafts._ordered_source_units(EXPLICIT_H1_THIRTEEN_PAGES)
    manifest = _prompt_source_units(prompt)

    assert [item["title"] for item in manifest] == NESTED_H3_UNIT_TITLES
    assert [item["id"] for item in manifest] == [1, 2, 3, 4]
    assert [unit["title"] for unit in units] == NESTED_H3_UNIT_TITLES
    assert [item["title"] for item in manifest] != EXPLICIT_H1_TITLES


def test_non_public_faithful_boundary_plan_uses_generic_h3_units() -> None:
    raw = json.dumps(
        [
            {"title": "页一", "section_ids": [1]},
            {"title": "页二", "section_ids": [2]},
            {"title": "页三", "section_ids": [3]},
            {"title": "页四", "section_ids": [4]},
        ],
        ensure_ascii=False,
    )

    slides = deck_split_drafts._parse_generated_slides(
        raw,
        source_content=EXPLICIT_H1_THIRTEEN_PAGES,
    )

    assert [slide["title"] for slide in slides] == ["页一", "页二", "页三", "页四"]
    assert "### 嵌套三级甲" in slides[0]["content"]
    assert "ALPHA-1" in slides[0]["content"]
    assert slides[1]["content"].startswith("第2页二级事实 BETA-2。")
    assert "### 嵌套三级乙" in slides[1]["content"]
    assert "ALPHA-4" in slides[3]["content"]
    assert all("section_ids" not in slide for slide in slides)


def test_public_faithful_boundary_plan_reconstructs_thirteen_h1_units() -> None:
    raw = json.dumps(
        [
            {"title": title, "section_ids": [index]}
            for index, title in enumerate(EXPLICIT_H1_TITLES, start=1)
        ],
        ensure_ascii=False,
    )

    slides = deck_split_drafts._parse_generated_slides(
        raw,
        source_content=EXPLICIT_H1_THIRTEEN_PAGES,
        prefer_explicit_h1=True,
    )

    assert [slide["title"] for slide in slides] == EXPLICIT_H1_TITLES
    for index, slide in enumerate(slides, start=1):
        assert f"ALPHA-{index}" in slide["content"]
        assert f"编号 {100 + index}。" in slide["content"]
    assert "## 第1页嵌套二级" in slides[0]["content"]
    assert "### 嵌套三级甲" in slides[0]["content"]
    assert "# 伪一级标题 围栏内" in slides[4]["content"]
