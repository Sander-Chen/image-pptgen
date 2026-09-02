from __future__ import annotations

import asyncio
import errno
import hashlib
import importlib
import inspect
import json
import os
import struct
from types import SimpleNamespace
import zlib
from pathlib import Path

import pytest

import config
import db as dbmod
from backend.services import codex_audit


RAW_THREAD = "NIMG_RAW_THREAD_SENTINEL"
RAW_CALL = "NIMG_RAW_CALL_SENTINEL"
RAW_ITEM = "NIMG_RAW_ITEM_SENTINEL"
RAW_PROMPT = "NIMG_RAW_PROMPT_SENTINEL"
RAW_ACCOUNT = "NIMG_RAW_ACCOUNT_SENTINEL"
RAW_ERROR_PATH = "/private/NIMG_RAW_ERROR_PATH_SENTINEL"
CALL_ARGUMENTS = json.dumps(
    {"prompt": RAW_PROMPT, "account": RAW_ACCOUNT}, separators=(",", ":")
)
CALL_ARGUMENTS_SHA256 = hashlib.sha256(CALL_ARGUMENTS.encode("utf-8")).hexdigest()
REVISED_PROMPT = f"Revised slide image prompt: {RAW_PROMPT}"
REVISED_PROMPT_SHA256 = hashlib.sha256(REVISED_PROMPT.encode("utf-8")).hexdigest()
LUNA_EVENT_INPUT_SOURCE = "image_generation_end.revised_prompt"
SOL_FUNCTION_CALL_INPUT_SOURCE = "function_call.arguments"
SOL_DIRECTOR_COMMAND = [
    "codex",
    "exec",
    "--sandbox",
    "read-only",
    "--model",
    "gpt-5.6-sol",
    "-c",
    'model_reasoning_effort="low"',
]


@pytest.fixture(autouse=True)
def _isolated_artifacts_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Keep every Native private-path assertion beneath its configured root."""
    monkeypatch.setattr(config, "ARTIFACTS_DIR", str(tmp_path / "artifacts"))


@pytest.fixture(autouse=True)
def _isolated_native_prompt_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Native launcher Prompt resolution must never read the root runtime DB."""
    monkeypatch.setattr(dbmod, "DB_PATH", tmp_path / "native-prompts.sqlite")
    dbmod.init_db()


def _native_module():
    try:
        module = importlib.import_module("backend.services.codex_native_image")
    except ModuleNotFoundError:
        module = None
    assert module is not None, "Native image evidence adapter is missing"
    return module


def _native_api():
    collect = getattr(_native_module(), "collect_native_image_evidence", None)
    assert callable(collect), "Native image canonical evidence collector is missing"
    return collect


def test_pre_image_handoff_records_terra_identity_without_provider_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _native_module()
    monkeypatch.setenv("IMAGE_PPTGEN_E2E_STOP_BEFORE_IMAGE_PROVIDER", "1")

    async def forbidden_provider(**_kwargs):
        raise AssertionError("pre-image handoff must not invoke Codex provider")

    monkeypatch.setattr(module, "run_codex_exec_json", forbidden_provider)
    output_path = tmp_path / "artifacts" / "runs" / "run-11" / "slides" / "slide-3" / "business.png"
    with pytest.raises(RuntimeError, match="image_pptgen_e2e_pre_image_provider_handoff"):
        module.generate_codex_native_image(
            {"api_type": "codex_native_image", "model": "gpt-5.6-terra", "thinking": "low"},
            "terra pre-image business prompt",
            str(output_path),
            run_id=11,
            run_slide_id=3,
            stage_id="native-stage",
            output_dir=tmp_path / "unused",
            timeout_seconds=5,
        )

    private_dir = module.native_runner_artifact_dir(
        artifacts_root=config.ARTIFACTS_DIR,
        run_id=11,
        run_slide_id=3,
        stage_id="native-stage",
        attempt=1,
    )
    handoff = json.loads((private_dir / "pre-image-provider-handoff.json").read_text())
    assert handoff["status"] == "ready"
    assert handoff["model"] == "gpt-5.6-terra"
    assert handoff["reasoning_effort"] == "low"
    assert handoff["provider_invoked"] is False
    assert len(handoff["business_prompt_sha256"]) == 64
    assert len(handoff["transport_prompt_sha256"]) == 64


def test_native_success_failure_and_director_consume_only_stream_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production Native callers must not reopen stdout after the semantic pass."""
    import pipeline

    module = _native_module()
    raw_path = tmp_path / "forbidden.raw.jsonl"
    raw_path.write_text('{"not":"a downstream input"}\n', encoding="utf-8")
    summary = SimpleNamespace(thread_id=RAW_THREAD, thread_ids=(RAW_THREAD,), final_capture=None)
    result = SimpleNamespace(
        stream_summary=summary,
        raw_jsonl_path=raw_path,
        command=list(SOL_DIRECTOR_COMMAND),
        invocation_id=71,
        attempt=1,
        exit_code=0,
        timed_out=False,
        final_text="director final",
        final_response_path=tmp_path / "final_response.txt",
        stage_id="native-stage",
        role="image_designer",
        cwd=tmp_path,
        prompt_path=tmp_path / "prompt.md",
        observed_jsonl_path=tmp_path / "forbidden.observed.jsonl",
        stderr_path=tmp_path / "stderr.txt",
        command_path=tmp_path / "command.json",
        started_at="2026-07-29T00:00:00Z",
        ended_at="2026-07-29T00:00:01Z",
        elapsed_ms=1,
        prompt_sha256="a" * 64,
        peak_rss_kb=1,
    )
    native_success: list[dict] = []
    native_failure: list[dict] = []
    director_calls: list[dict] = []

    def forbidden_jsonl(*_args, **_kwargs):
        raise AssertionError("Native caller reopened raw JSONL")

    def forbidden_read_text(self, *args, **kwargs):
        raise AssertionError(f"downstream Path.read_text is forbidden: {self}")

    def fake_cli_identity(*_args, **_kwargs):
        return "/private/codex", "test", "a" * 64

    async def fake_run_codex_exec_json(**kwargs):
        result.prompt_sha256 = hashlib.sha256(str(kwargs["prompt"]).encode("utf-8")).hexdigest()
        return result

    async def fake_supervised_child(*, invoke, **_kwargs):
        observed = await invoke(SimpleNamespace(attempt=1))
        return SimpleNamespace(
            result=observed,
            attempt_count=1,
            complete_projection=lambda: True,
            fail_unrecoverable=lambda _reason: None,
        )

    monkeypatch.setattr(module, "_jsonl_records", forbidden_jsonl)
    monkeypatch.setattr(Path, "read_text", forbidden_read_text)
    monkeypatch.setattr(module, "_native_cli_identity", fake_cli_identity)
    monkeypatch.setattr(module, "run_codex_exec_json", fake_run_codex_exec_json)
    monkeypatch.setattr(module, "run_supervised_codex_child", fake_supervised_child)
    monkeypatch.setattr(
        module,
        "collect_native_image_evidence",
        lambda **kwargs: native_success.append(kwargs) or {"public_projection": {"ok": True}},
    )

    output_path = tmp_path / "artifacts" / "runs" / "run-11" / "slides" / "slide-3" / "business.png"
    generated = module.generate_codex_native_image(
        {"api_type": "codex_native_image", "model": "gpt-5.6-luna", "thinking": "low"},
        "native semantic-summary prompt",
        str(output_path),
        run_id=11,
        run_slide_id=3,
        stage_id="native-stage",
        output_dir=tmp_path / "unused",
        timeout_seconds=5,
    )
    assert generated["native_public"] == {"ok": True}
    assert native_success[0]["stdout_events"] == [{"type": "thread.started", "thread_id": RAW_THREAD}]

    monkeypatch.setattr(
        module,
        "collect_common_codex_conversation_audit",
        lambda **kwargs: native_failure.append(kwargs) or {},
    )
    module._native_failure_evidence(
        result=result,
        agent_config={"model": "gpt-5.6-luna", "thinking": "low"},
        private_dir=tmp_path / "private",
        timeout=True,
    )
    assert native_failure[0]["stdout_events"] == [{"type": "thread.started", "thread_id": RAW_THREAD}]

    monkeypatch.setattr(pipeline, "ARTIFACTS_DIR", str(tmp_path / "director-artifacts"))
    monkeypatch.setattr(pipeline, "run_codex_exec_json", fake_run_codex_exec_json)
    monkeypatch.setattr(pipeline, "run_supervised_codex_child", fake_supervised_child)
    monkeypatch.setattr(pipeline, "_record_codex_result", lambda *_args, **_kwargs: 99)
    monkeypatch.setattr(pipeline, "_write_codex_request_evidence", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "_native_cli_identity", fake_cli_identity)
    monkeypatch.setattr(
        module,
        "collect_native_image_evidence",
        lambda **kwargs: director_calls.append(kwargs) or {"public_projection": {}},
    )

    text, attempts = pipeline.run_native_image_three_zero_director(
        {"api_type": "codex_exec", "model": "gpt-5.6-sol", "thinking": "low"},
        "director semantic-summary prompt",
        run_id=11,
        run_slide_id=3,
        stage_id="director-stage",
        timeout_seconds=5,
    )
    assert text == "director final"
    assert attempts == [{"attempt": 1, "status": "result_received"}]
    assert director_calls[0]["stdout_events"] == [{"type": "thread.started", "thread_id": RAW_THREAD}]


def test_capture_backed_result_satisfies_recovery_and_director_at_its_consumption_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipeline
    from backend.services import codex_child_recovery
    from backend.services.codex_jsonl_stream import FinalTextCapture

    module = _native_module()
    final_text = "captured director final"
    spool_path = tmp_path / "captured-final.txt"
    spool_path.write_text(final_text, encoding="utf-8")
    capture = FinalTextCapture(
        inline_text=None,
        spool_path=spool_path,
        text_length=len(final_text),
        text_sha256=hashlib.sha256(final_text.encode("utf-8")).hexdigest(),
    )
    result = SimpleNamespace(
        stream_summary=SimpleNamespace(thread_id=RAW_THREAD, thread_ids=(RAW_THREAD,), final_capture=capture),
        command=list(SOL_DIRECTOR_COMMAND),
        invocation_id=71,
        attempt=1,
        exit_code=0,
        timed_out=False,
        final_text="",
        final_response_path=tmp_path / "final_response.txt",
        stage_id="director-stage",
        role="image_designer",
        cwd=tmp_path,
        prompt_path=tmp_path / "prompt.md",
        raw_jsonl_path=tmp_path / "raw.jsonl",
        observed_jsonl_path=tmp_path / "observed.jsonl",
        stderr_path=tmp_path / "stderr.txt",
        command_path=tmp_path / "command.json",
        started_at="2026-07-29T00:00:00Z",
        ended_at="2026-07-29T00:00:01Z",
        elapsed_ms=1,
        prompt_sha256="a" * 64,
        peak_rss_kb=1,
    )
    assert codex_child_recovery._has_acceptable_result(result, require_final_text=True)

    async def fake_run_codex_exec_json(**_kwargs):
        return result

    async def fake_supervised_child(*, invoke, **_kwargs):
        observed = await invoke(SimpleNamespace(attempt=1))
        return SimpleNamespace(
            result=observed,
            attempt_count=1,
            complete_projection=lambda: True,
            fail_unrecoverable=lambda _reason: None,
        )

    monkeypatch.setattr(pipeline, "ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setattr(pipeline, "run_codex_exec_json", fake_run_codex_exec_json)
    monkeypatch.setattr(pipeline, "run_supervised_codex_child", fake_supervised_child)
    monkeypatch.setattr(pipeline, "_record_codex_result", lambda *_args, **_kwargs: 99)
    monkeypatch.setattr(pipeline, "_write_codex_request_evidence", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "_native_cli_identity", lambda *_args, **_kwargs: ("/private/codex", "test", "a" * 64))
    monkeypatch.setattr(module, "collect_native_image_evidence", lambda **_kwargs: {"public_projection": {}})

    received, attempts = pipeline.run_native_image_three_zero_director(
        {"api_type": "codex_exec", "model": "gpt-5.6-sol", "thinking": "low"},
        "capture-backed director prompt",
        run_id=11,
        run_slide_id=3,
        stage_id="director-stage",
        timeout_seconds=5,
    )
    assert received == final_text
    assert attempts == [{"attempt": 1, "status": "result_received"}]
    assert result.final_text == "", "core result must remain bounded rather than materializing the capture"


def test_capture_backed_result_is_accepted_by_the_actual_supervised_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The durable supervisor accepts the capture contract before any Director seam."""
    from backend.services.codex_child_recovery import run_supervised_codex_child
    from backend.services.codex_jsonl_stream import FinalTextCapture

    monkeypatch.setattr(dbmod, "DB_PATH", tmp_path / "capture-child.sqlite")
    dbmod.init_db()
    artifact_dir = tmp_path / "capture-child"
    artifact_dir.mkdir()
    final_text = "capture-backed supervised child final"
    spool_path = artifact_dir / "captured-final.txt"
    spool_path.write_text(final_text, encoding="utf-8")
    final_response_path = artifact_dir / "final_response.txt"
    final_response_path.write_text(final_text, encoding="utf-8")
    event = {"type": "item.completed", "item": {"type": "agent_message", "text": final_text}}
    raw_path = artifact_dir / "codex.raw.jsonl"
    raw_path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    observed_path = artifact_dir / "codex.observed.jsonl"
    observed_path.write_text(
        json.dumps({"observed_at": "2026-07-29T00:00:00Z", "event": event}) + "\n",
        encoding="utf-8",
    )
    capture = FinalTextCapture(
        inline_text=None,
        spool_path=spool_path,
        text_length=len(final_text),
        text_sha256=hashlib.sha256(final_text.encode("utf-8")).hexdigest(),
    )
    result = SimpleNamespace(
        stream_summary=SimpleNamespace(final_capture=capture),
        stage_id="capture-child-stage",
        role="image_designer",
        command=["codex"],
        cwd=artifact_dir,
        prompt_path=artifact_dir / "prompt.md",
        raw_jsonl_path=raw_path,
        observed_jsonl_path=observed_path,
        stderr_path=artifact_dir / "stderr.txt",
        command_path=artifact_dir / "command.json",
        final_response_path=final_response_path,
        transcript_path=artifact_dir / "transcript.json",
        started_at="2026-07-29T00:00:00Z",
        ended_at="2026-07-29T00:00:01Z",
        elapsed_ms=1,
        exit_code=0,
        prompt_sha256="a" * 64,
        final_text="",
        peak_rss_kb=1,
    )
    calls: list[int] = []

    async def invoke(context):
        calls.append(context.attempt)
        return result

    execution = asyncio.run(
        run_supervised_codex_child(
            run_id=None,
            run_slide_id=None,
            stage_id="capture-child-stage",
            role="image_designer",
            idempotency_key="capture-backed-supervisor",
            invoke=invoke,
            max_recoveries=0,
            require_final_text=True,
        )
    )
    assert calls == [1]
    assert execution.result is result
    assert execution.reused_result is False
    assert result.final_text == ""
    assert execution.complete_projection() is True


def test_native_direct_adapter_exposes_the_existing_image_generator_seam():
    """Freeze the adapter call shape before Direct dispatch is wired.

    ``run_image_route`` must keep owning Direct orchestration.  Its one provider
    seam receives this adapter instead of growing a parallel Native pipeline.
    """

    adapter = getattr(_native_module(), "generate_codex_native_image", None)
    assert callable(adapter), "Native Direct image adapter is missing"

    parameters = inspect.signature(adapter).parameters
    assert list(parameters)[:3] == ["agent_config", "prompt", "output_path"]
    for name in (
        "run_id",
        "run_slide_id",
        "stage_id",
        "output_dir",
        "timeout_seconds",
        "reference_image_paths",
        "metadata",
    ):
        assert name in parameters
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY


def test_native_renderer_requires_attached_conversation_image_context_without_mutating_business_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The shared renderer passes one visible one-shot contract on Codex stdin."""

    module = _native_module()
    business_prompt = "NIMG one-shot business slide prompt: preserve this exactly."
    active_prompt = dbmod.get_active_prompt("image_generator")
    assert active_prompt is not None
    captured: dict[str, object] = {}

    class StopAfterTransportCapture(Exception):
        pass

    async def fake_run_codex_exec_json(**kwargs):
        captured.update(kwargs)
        raise StopAfterTransportCapture()

    async def fake_supervised_child(*, invoke, **_kwargs):
        return await invoke(SimpleNamespace(attempt=1))

    monkeypatch.setattr(module, "run_codex_exec_json", fake_run_codex_exec_json)
    monkeypatch.setattr(module, "run_supervised_codex_child", fake_supervised_child)

    with pytest.raises(RuntimeError, match="native_image_generation_failed"):
        module.generate_codex_native_image(
            {"api_type": "codex_native_image", "model": "gpt-5.6-luna", "thinking": "low"},
            business_prompt,
            str(_business_output_path(tmp_path / "artifacts")),
            run_id=11,
            run_slide_id=3,
            stage_id="native-image-stage",
            output_dir=tmp_path / "unused-output",
            timeout_seconds=45,
            reference_image_paths=[tmp_path / "reference.png"],
            metadata={"strategy": "image_direct"},
        )

    assert captured["prompt"] == (
        "# Active system-managed image_generator Prompt\n"
        f"{active_prompt['content']}\n\n"
        "Native one-shot execution contract:\n"
        "- Make exactly one imagegen call for this page.\n"
        "- Do not regenerate, retry, or make any additional imagegen calls.\n"
        "- The approved reference images are already attached to this Codex conversation through --image; use "
        "num_last_images_to_include with the smallest count that includes every attached conversation image in the one "
        "imagegen call.\n"
        "- Do not use referenced_image_paths to reopen any already attached reference images from the local filesystem.\n"
        "- Stop after that one imagegen call.\n\n"
        "# Original business slide prompt (verbatim)\n"
        f"{business_prompt}"
    )
    assert str(captured["prompt"]).count(business_prompt) == 1
    assert captured["sandbox"] == "read-only"
    assert captured["ephemeral"] is False
    assert captured["model"] == "gpt-5.6-luna"
    assert captured["reasoning_effort"] == "low"
    assert captured["image_paths"] == [tmp_path / "reference.png"]


def test_native_renderer_keeps_the_existing_one_shot_contract_when_no_reference_is_attached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    module = _native_module()
    business_prompt = "NIMG no-reference business slide prompt: preserve this exactly."
    active_prompt = dbmod.get_active_prompt("image_generator")
    assert active_prompt is not None
    captured: dict[str, object] = {}

    class StopAfterTransportCapture(Exception):
        pass

    async def fake_run_codex_exec_json(**kwargs):
        captured.update(kwargs)
        raise StopAfterTransportCapture()

    async def fake_supervised_child(*, invoke, **_kwargs):
        return await invoke(SimpleNamespace(attempt=1))

    monkeypatch.setattr(module, "run_codex_exec_json", fake_run_codex_exec_json)
    monkeypatch.setattr(module, "run_supervised_codex_child", fake_supervised_child)

    with pytest.raises(RuntimeError, match="native_image_generation_failed"):
        module.generate_codex_native_image(
            {"api_type": "codex_native_image", "model": "gpt-5.6-luna", "thinking": "low"},
            business_prompt,
            str(_business_output_path(tmp_path / "artifacts")),
            run_id=11,
            run_slide_id=3,
            stage_id="native-image-stage",
            output_dir=tmp_path / "unused-output",
            timeout_seconds=45,
            reference_image_paths=[],
            metadata={"strategy": "image_direct"},
        )

    assert captured["prompt"] == (
        "# Active system-managed image_generator Prompt\n"
        f"{active_prompt['content']}\n\n"
        "Native one-shot execution contract:\n"
        "- Make exactly one imagegen call for this page.\n"
        "- Do not regenerate, retry, or make any additional imagegen calls.\n"
        "- Stop after that one imagegen call.\n\n"
        "# Original business slide prompt (verbatim)\n"
        f"{business_prompt}"
    )
    assert str(captured["prompt"]).count(business_prompt) == 1
    assert "num_last_images_to_include" not in str(captured["prompt"])
    assert "referenced_image_paths" not in str(captured["prompt"])
    assert captured["image_paths"] == []


def _install_successful_native_launcher_capture(module, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    captured: list[dict[str, object]] = []

    async def fake_run_codex_exec_json(**kwargs):
        captured.append(kwargs)
        transport = str(kwargs["prompt"])
        return SimpleNamespace(
            command=["codex"],
            invocation_id=700 + len(captured),
            exit_code=0,
            timed_out=False,
            prompt_sha256=hashlib.sha256(transport.encode("utf-8")).hexdigest(),
            stream_summary=SimpleNamespace(thread_id=RAW_THREAD, thread_ids=(RAW_THREAD,), final_capture=None),
        )

    async def fake_supervised_child(*, invoke, **_kwargs):
        result = await invoke(SimpleNamespace(attempt=1))
        return SimpleNamespace(
            result=result,
            attempt_count=1,
            complete_projection=lambda: True,
            fail_unrecoverable=lambda _reason: None,
        )

    monkeypatch.setattr(module, "run_codex_exec_json", fake_run_codex_exec_json)
    monkeypatch.setattr(module, "run_supervised_codex_child", fake_supervised_child)
    monkeypatch.setattr(module, "_native_cli_identity", lambda *_args, **_kwargs: ("/private/codex", "test", "a" * 64))
    monkeypatch.setattr(module, "collect_native_image_evidence", lambda **_kwargs: {"public_projection": {"ok": True}})
    return captured


def _run_successful_native_launcher(module, tmp_path: Path, business_prompt: str) -> dict:
    return module.generate_codex_native_image(
        {"api_type": "codex_native_image", "model": "gpt-5.6-luna", "thinking": "low"},
        business_prompt,
        str(_business_output_path(tmp_path / "artifacts" / "business.png")),
        run_id=11,
        run_slide_id=3,
        stage_id="native-image-stage",
        output_dir=tmp_path / "unused-output",
        timeout_seconds=45,
        reference_image_paths=[],
        metadata={"strategy": "image_direct"},
    )


def test_native_launcher_selects_changed_active_prompt_and_returns_safe_rendered_lineage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _native_module()
    captured = _install_successful_native_launcher_capture(module, monkeypatch)
    active_prompt = dbmod.get_active_prompt("image_generator")
    assert active_prompt is not None
    prompt_id = int(active_prompt["id"])
    first_prompt = "NIMG active Prompt body first version: secret system instruction."
    second_prompt = "NIMG active Prompt body second version: changed system instruction."
    business_prompt = "NIMG business prompt must remain verbatim exactly once."

    assert dbmod.update_prompt(prompt_id, content=first_prompt)
    first = _run_successful_native_launcher(module, tmp_path, business_prompt)
    assert dbmod.update_prompt(prompt_id, content=second_prompt)
    second = _run_successful_native_launcher(module, tmp_path, business_prompt)

    assert len(captured) == 2
    first_lineage = first["native_prompt_lineage"]
    second_lineage = second["native_prompt_lineage"]
    for lineage, content, call in zip((first_lineage, second_lineage), (first_prompt, second_prompt), captured):
        transport = str(call["prompt"])
        assert lineage == {
            "role": "image_generator",
            "prompt_id": prompt_id,
            "prompt_content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "rendered_prompt_sha256": hashlib.sha256(transport.encode("utf-8")).hexdigest(),
        }
        assert transport.count(content) == 1
        assert transport.count(business_prompt) == 1
        assert "Native one-shot execution contract:" in transport
        assert "Make exactly one imagegen call" in transport
        assert call["model"] == "gpt-5.6-luna"
        assert call["reasoning_effort"] == "low"
        assert call["image_paths"] == []
    assert first_lineage["prompt_content_sha256"] != second_lineage["prompt_content_sha256"]
    assert first_lineage["rendered_prompt_sha256"] != second_lineage["rendered_prompt_sha256"]
    assert first_prompt not in json.dumps(first, ensure_ascii=False)
    assert second_prompt not in json.dumps(second, ensure_ascii=False)


def test_native_launcher_rejects_valid_but_mismatched_invocation_prompt_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _native_module()
    business_output = _business_output_path(tmp_path / "artifacts")
    business_output.parent.mkdir(parents=True)
    business_output.write_text("stale business image", encoding="utf-8")
    wrong_prompt_sha256 = "0" * 64
    success_evidence: list[dict[str, object]] = []
    failure_evidence: list[object] = []
    successful_projections: list[bool] = []

    async def fake_run_codex_exec_json(**kwargs):
        transport_sha256 = hashlib.sha256(str(kwargs["prompt"]).encode("utf-8")).hexdigest()
        assert transport_sha256 != wrong_prompt_sha256
        return SimpleNamespace(
            command=["codex"],
            invocation_id=701,
            exit_code=0,
            timed_out=False,
            prompt_sha256=wrong_prompt_sha256,
            stream_summary=SimpleNamespace(thread_id=RAW_THREAD, thread_ids=(RAW_THREAD,), final_capture=None),
        )

    async def fake_supervised_child(*, invoke, **_kwargs):
        result = await invoke(SimpleNamespace(attempt=1))
        return SimpleNamespace(
            result=result,
            attempt_count=1,
            complete_projection=lambda: successful_projections.append(True) or True,
            fail_unrecoverable=lambda _reason: None,
        )

    monkeypatch.setattr(module, "run_codex_exec_json", fake_run_codex_exec_json)
    monkeypatch.setattr(module, "run_supervised_codex_child", fake_supervised_child)
    monkeypatch.setattr(module, "_native_cli_identity", lambda *_args, **_kwargs: ("/private/codex", "test", "a" * 64))
    monkeypatch.setattr(
        module,
        "collect_native_image_evidence",
        lambda **kwargs: success_evidence.append(kwargs) or {"public_projection": {"unexpected": True}},
    )
    monkeypatch.setattr(module, "_native_failure_evidence", lambda **kwargs: failure_evidence.append(kwargs["result"]))

    with pytest.raises(RuntimeError, match="native_image_generation_failed") as error:
        module.generate_codex_native_image(
            {"api_type": "codex_native_image", "model": "gpt-5.6-luna", "thinking": "low"},
            "hash mismatch business prompt",
            str(business_output),
            run_id=11,
            run_slide_id=3,
            stage_id="native-image-stage",
            output_dir=tmp_path / "unused-output",
            timeout_seconds=45,
            reference_image_paths=[],
            metadata={"strategy": "image_direct"},
        )

    assert str(error.value.__cause__) == "native_image_prompt_lineage_failed"
    assert successful_projections == []
    assert success_evidence == []
    assert len(failure_evidence) == 1
    assert not business_output.exists()


def test_native_launcher_fails_closed_before_spawn_when_active_image_generator_prompt_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _native_module()
    active_prompt = dbmod.get_active_prompt("image_generator")
    assert active_prompt is not None
    assert dbmod.delete_prompt(int(active_prompt["id"]))
    spawned = False

    async def forbidden_spawn(**_kwargs):
        nonlocal spawned
        spawned = True
        raise AssertionError("Native child spawn must not be reached")

    monkeypatch.setattr(module, "run_codex_exec_json", forbidden_spawn)
    with pytest.raises(ValueError, match="active image_generator Prompt"):
        _run_successful_native_launcher(module, tmp_path, "missing active Prompt business input")
    assert spawned is False


def test_native_launcher_fails_closed_before_spawn_when_active_prompt_role_is_wrong(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _native_module()
    spawned = False

    async def forbidden_spawn(**_kwargs):
        nonlocal spawned
        spawned = True
        raise AssertionError("Native child spawn must not be reached")

    monkeypatch.setattr(dbmod, "get_active_prompt", lambda _role: {"id": 30, "agent_type": "image_designer", "content": "wrong role"})
    monkeypatch.setattr(module, "run_codex_exec_json", forbidden_spawn)
    with pytest.raises(ValueError, match="active image_generator Prompt"):
        _run_successful_native_launcher(module, tmp_path, "wrong role Prompt business input")
    assert spawned is False


def test_native_cli_identity_probes_the_same_resolved_hashed_binary_without_a_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    module = _native_module()
    binary = tmp_path / "codex-fixture"
    binary.write_bytes(b"NIMG-055A-R2 exact binary fixture")
    observed: list[tuple[list[str], dict]] = []

    def fake_run(command: list[str], **kwargs):
        observed.append((command, kwargs))
        return type("Completed", (), {"returncode": 0, "stdout": "codex-cli fixture-0.145.0\n", "stderr": ""})()

    monkeypatch.setattr(module.shutil, "which", lambda _name: str(binary))
    monkeypatch.setattr(module, "subprocess", type("Subprocess", (), {"run": staticmethod(fake_run)}), raising=False)

    cli_binary, cli_version, binary_sha256 = module._native_cli_identity(
        ["codex", "exec", "--json"], {"cli_version": "configured-but-untrusted"}
    )

    assert cli_binary == str(binary.resolve())
    assert binary_sha256 == hashlib.sha256(binary.read_bytes()).hexdigest()
    assert cli_version == "codex-cli fixture-0.145.0"
    assert observed == [
        (
            [str(binary.resolve()), "--version"],
            {"capture_output": True, "check": False, "shell": False, "text": True, "timeout": 5},
        )
    ]


def test_native_cli_identity_fails_closed_when_same_binary_identity_probe_cannot_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    module = _native_module()
    binary = tmp_path / "codex-fixture"
    binary.write_bytes(b"NIMG-055A-R2 failed identity fixture")

    def fail_run(_command: list[str], **_kwargs):
        raise OSError("identity probe unavailable")

    monkeypatch.setattr(module.shutil, "which", lambda _name: str(binary))
    monkeypatch.setattr(module, "subprocess", type("Subprocess", (), {"run": staticmethod(fail_run)}), raising=False)

    _cli_binary, cli_version, binary_sha256 = module._native_cli_identity(
        ["codex", "exec", "--json"], {"cli_version": "configured-but-untrusted"}
    )

    assert binary_sha256 == hashlib.sha256(binary.read_bytes()).hexdigest()
    assert cli_version == "unknown"


def _png(width: int, height: int) -> bytes:
    """Create a deterministic RGB PNG without adding a test-time Pillow dependency."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    scanline = b"\x00" + b"\x12\x34\x56" * width
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(scanline * height)) + chunk(b"IEND", b"")


def _persistent_child(
    tmp_path: Path,
    *,
    include_imagegen_function_call: bool = False,
    function_call_ids: list[str] | None = None,
    function_call_arguments: str | None = CALL_ARGUMENTS,
    completed_end_count: int = 1,
    image_end_call_id: str | None = None,
    include_end_call_id: bool = True,
    include_revised_prompt: bool = True,
    include_saved_path: bool = True,
    valid_png: bool = True,
    width: int = 1672,
    height: int = 941,
) -> dict:
    """Create the canonical saved-session shape, not a synthetic stdout item shape.

    The verified Luna sessions use a completed ``image_generation_end`` event
    without an ``imagegen`` function call. The verified Sol sessions additionally
    persist exactly one matching ``response_item.payload`` function call. In both
    forms, only the exact completed end event selects the opaque saved path.
    """

    if include_imagegen_function_call and function_call_ids is None:
        function_call_ids = [RAW_CALL]
    function_call_ids = function_call_ids or []
    codex_home = tmp_path / "codex-home"
    sessions = codex_home / "sessions" / "2026" / "07" / "24"
    sessions.mkdir(parents=True)
    session = sessions / f"rollout-2026-07-24T00-00-00-{RAW_THREAD}.jsonl"
    saved_path = codex_home / "generated_images" / "opaque-tool-storage" / "native-output.png"
    newest_unrelated_path = saved_path.with_name("newer-unrelated-output.png")
    saved_path.parent.mkdir(parents=True)
    saved_path.write_bytes(_png(width, height) if valid_png else b"not-a-png")
    newest_unrelated_path.write_bytes(_png(99, 77))

    records: list[dict] = [
        {
            "timestamp": "2026-07-24T00:00:00.000Z",
            "type": "session_meta",
            "payload": {"id": RAW_THREAD},
        }
    ]
    if include_imagegen_function_call:
        for call_id in function_call_ids:
            function_call_payload = {
                "type": "function_call",
                "id": RAW_ITEM,
                "name": "imagegen",
                "call_id": call_id,
            }
            if function_call_arguments is not None:
                function_call_payload["arguments"] = function_call_arguments
            records.append(
                {
                    "timestamp": "2026-07-24T00:00:01.000Z",
                    "type": "response_item",
                    "payload": function_call_payload,
                }
            )
    for end_index in range(completed_end_count):
        image_end_payload = {
            "type": "image_generation_end",
            "status": "completed",
        }
        if include_end_call_id:
            image_end_payload["call_id"] = image_end_call_id if image_end_call_id is not None else RAW_CALL
        if include_revised_prompt:
            image_end_payload["revised_prompt"] = REVISED_PROMPT
        if include_saved_path:
            image_end_payload["saved_path"] = str(saved_path)
        records.append(
            {
                "timestamp": f"2026-07-24T00:00:0{2 + end_index}.000Z",
                "type": "event_msg",
                "payload": image_end_payload,
            }
        )
    session.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")

    # A newer unrelated canonical-shaped session must never influence exact
    # stdout-thread binding or satisfy a missing call/end record.
    (sessions / "rollout-2026-07-24T00-00-03-unrelated.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2026-07-24T00:00:03.000Z",
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "imagegen",
                    "call_id": "wrong-call",
                    "arguments": "{}",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "codex_home": codex_home,
        "session": session,
        "saved_path": saved_path,
        "newest_unrelated_path": newest_unrelated_path,
        "stdout_events": [{"type": "thread.started", "thread_id": RAW_THREAD}],
    }


def _private_dir(tmp_path: Path) -> Path:
    return (
        tmp_path
        / "artifacts"
        / ".codex-private"
        / "native-image"
        / "run-11"
        / "slide-3"
        / "stage-image-generator"
        / "attempt-2"
    )


def _business_output_path(artifacts_root: Path) -> Path:
    """Return the caller-owned business artifact path, never private evidence."""

    return artifacts_root / "runs" / "run-11" / "slides" / "slide-3" / "business.png"


def _collect(child: dict, private_dir: Path, **overrides):
    arguments = {
        "stdout_events": child["stdout_events"],
        "codex_home": child["codex_home"],
        "private_dir": private_dir,
        "business_output_path": _business_output_path(private_dir.parents[5]),
        "requested_model": "gpt-5.6-luna",
        "requested_reasoning_effort": "low",
        "actual_model": "gpt-5.6-luna",
        "actual_reasoning_effort": "low",
        "cli_binary": "/opt/codex",
        "cli_version": "codex-cli test-version",
        "binary_sha256": "binary-sha",
        "attempt": 2,
        "terminal_state": "result_received",
        "retry": True,
        "error": None,
        "timeout": False,
        "skip": False,
        "fallback_used": False,
    }
    arguments.update(overrides)
    return _native_api()(**arguments)


def _thread_scoped_child(tmp_path: Path, *, create_output: bool = True, valid_png: bool = True) -> dict:
    """Create the post-R49 CLI shape without any legacy image end event."""
    module = _native_module()
    child = _persistent_child(tmp_path, completed_end_count=0)
    baseline = module._capture_thread_output_baseline(codex_home=child["codex_home"], thread_id=RAW_THREAD)
    thread_directory = child["codex_home"] / "generated_images" / RAW_THREAD
    if create_output:
        thread_directory.mkdir(parents=True)
        (thread_directory / "tool-output.png").write_bytes(_png(1672, 941) if valid_png else b"not-a-png")
    child.update(
        {
            "thread_output_baseline": baseline,
            "thread_directory": thread_directory,
            "stdout_events": [
                {"type": "thread.started", "thread_id": RAW_THREAD},
                {"type": "turn.completed"},
            ],
        }
    )
    return child


def _collect_thread_scoped(child: dict, private_dir: Path, **overrides):
    arguments = {
        "thread_output_baseline": child["thread_output_baseline"],
        "execution_exit_code": 0,
        "execution_timed_out": False,
    }
    arguments.update(overrides)
    return _collect(child, private_dir, **arguments)


def _common_audit_arguments(child: dict, private_dir: Path) -> dict:
    return {
        "stdout_events": child["stdout_events"],
        "codex_home": child["codex_home"],
        "private_dir": private_dir,
        "requested_model": "gpt-5.6-luna",
        "requested_reasoning_effort": "low",
        "actual_model": "gpt-5.6-luna",
        "actual_reasoning_effort": "low",
        "cli_binary": "/opt/codex",
        "cli_version": "codex-cli test-version",
        "binary_sha256": "binary-sha",
        "attempt": 2,
        "terminal_state": "result_received",
        "retry": True,
        "error": None,
        "timeout": False,
        "skip": False,
        "fallback_used": False,
    }


@pytest.mark.parametrize(
    "session_meta",
    [None, {"id": "different-thread"}],
    ids=["missing_session_meta", "wrong_session_meta"],
)
def test_canonical_session_rejects_exact_filename_without_matching_session_meta(
    tmp_path: Path, session_meta: dict | None
):
    module = _native_module()
    child = _persistent_child(tmp_path)
    records = []
    if session_meta is not None:
        records.append({"type": "session_meta", "payload": session_meta})
    records.append({"type": "event_msg", "payload": {"unrelated": True}})
    child["session"].write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="exact stdout thread id"):
        module.find_canonical_session(codex_home=child["codex_home"], thread_id=RAW_THREAD)


def test_common_native_audit_emits_a_complete_uuid7_bound_canonical_manifest(tmp_path: Path):
    """The successful Native manifest must name the exact streamed identity, not a scan result."""
    module = _native_module()
    uuid7_thread = "019fab6b-11e6-78f2-8bdf-334d357e070f"
    codex_home = tmp_path / "codex-home"
    source = (
        codex_home
        / "sessions"
        / "2026"
        / "07"
        / "29"
        / f"rollout-2026-07-29T01-09-08-{uuid7_thread}.jsonl"
    )
    source.parent.mkdir(parents=True)
    source.write_text(
        json.dumps({"type": "session_meta", "payload": {"id": uuid7_thread}}) + "\n",
        encoding="utf-8",
    )
    private_dir = (
        tmp_path
        / "artifacts"
        / ".codex-private"
        / "native-image"
        / "run-11"
        / "slide-3"
        / "uuid7-manifest"
        / "attempt-2"
    )
    arguments = _common_audit_arguments(
        {"codex_home": codex_home, "stdout_events": [{"type": "thread.started", "thread_id": uuid7_thread}]},
        private_dir,
    )

    evidence = module.collect_common_codex_conversation_audit(**arguments)

    canonical = evidence["canonical_session"]
    assert evidence["audit_complete"] is True
    assert canonical["thread_id"] == uuid7_thread
    assert canonical["source_path"] == str(source)
    assert canonical["archive_path"]


def test_native_runner_private_dir_requires_exact_configured_run_slide_stage_attempt_shape(tmp_path: Path):
    module = _native_module()
    artifacts_root = tmp_path / "artifacts"
    private_dir = _private_dir(tmp_path)
    validate = getattr(module, "_native_private_root_from_dir")

    assert validate(private_dir=private_dir, artifacts_root=artifacts_root) == (
        artifacts_root / ".codex-private" / "native-image" / "run-11"
    )
    assert module.native_runner_artifact_dir(
        artifacts_root=artifacts_root,
        run_id=11,
        run_slide_id=3,
        stage_id="stage-image-generator",
        attempt=2,
    ) == private_dir

    invalid_dirs = (
        artifacts_root / ".codex-private" / "native-image" / "run-11" / "foo",
        artifacts_root / ".codex-private" / "native-image" / "run-11" / "slide-3" / "stage-image-generator",
        private_dir / "extra",
        artifacts_root / ".codex-private" / "native-image" / "run-11" / "slide-0" / "stage-image-generator" / "attempt-2",
        Path(str(artifacts_root / ".codex-private" / "native-image" / "run-11" / "slide-3" / "stage-image-generator" / ".." / "stage-image-generator" / "attempt-2")),
        tmp_path / "foreign" / ".codex-private" / "native-image" / "run-11" / "slide-3" / "stage-image-generator" / "attempt-2",
    )
    for invalid_dir in invalid_dirs:
        with pytest.raises(module.NativePrivatePathError):
            validate(private_dir=invalid_dir, artifacts_root=artifacts_root)

    with pytest.raises(module.CommonNativeAuditError) as rejected:
        module.collect_common_codex_conversation_audit(
            **_common_audit_arguments(_persistent_child(tmp_path), invalid_dirs[0]),
            finalize=False,
        )
    assert rejected.value.evidence["failure_code"] == "private_evidence_path_invalid"


def test_common_audit_re_finalizer_failure_is_visible_and_invoked_once(tmp_path: Path):
    module = _native_module()
    child = _persistent_child(tmp_path)
    calls: list[tuple[int, dict]] = []

    def explode(invocation_id: int, metadata: dict):
        calls.append((invocation_id, metadata))
        raise RuntimeError("re-finalization exploded")

    with pytest.raises(RuntimeError, match="re-finalization exploded"):
        module.collect_common_codex_conversation_audit(
            **_common_audit_arguments(child, _private_dir(tmp_path)),
            invocation_id=501,
            refinalize=explode,
        )

    assert [invocation_id for invocation_id, _metadata in calls] == [501]
    assert calls[0][1]["canonical_session"]["archive_path"]


def test_re_finalize_native_invocation_reports_a_missing_invocation_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    module = _native_module()
    monkeypatch.setattr(dbmod, "DB_PATH", tmp_path / "missing-invocation.db")
    dbmod.init_db()

    with pytest.raises(ValueError, match="cannot re-finalize missing Native Codex invocation 777"):
        module.re_finalize_native_invocation(777, {"terminal_state": "failed"})


def test_explicit_native_discriminator_avoids_legacy_shape_collisions_and_keeps_event_ids_scoped(tmp_path: Path):
    module = _native_module()
    legacy_shape = {
        "thread_id": "legacy-thread",
        "canonical_session": {"archive_path": "/legacy/session.jsonl"},
        "business_image": {"path": "/legacy/business.png"},
        "normalization": {"algorithm": "legacy"},
        "public_projection": {"legacy": True},
    }
    nested_legacy = {"legacy": [legacy_shape]}
    assert not codex_audit.contains_native_private_evidence(legacy_shape)
    assert not codex_audit.contains_nested_native_private_evidence(nested_legacy)
    assert codex_audit.project_native_public_value(nested_legacy) == nested_legacy

    legacy_public = codex_audit._public_codex_invocation(
        {"metadata": legacy_shape, "events": [{"item_id": "legacy-public-item", "item_type": "message"}]}
    )
    assert legacy_public["events"][0]["item_id"] == "legacy-public-item"
    assert "native_image" not in legacy_public

    child = _persistent_child(tmp_path)
    common = module.collect_common_codex_conversation_audit(
        **_common_audit_arguments(child, _private_dir(tmp_path)),
        finalize=False,
    )
    assert common[codex_audit.NATIVE_PRIVATE_EVIDENCE_KEY] == codex_audit.NATIVE_PRIVATE_EVIDENCE_VALUE
    assert codex_audit.contains_native_private_evidence(common)
    assert codex_audit.contains_nested_native_private_evidence({"native": [common]})
    native_public = codex_audit._public_codex_invocation(
        {"metadata": common, "events": [{"item_id": RAW_ITEM, "item_type": "function_call"}]}
    )
    assert "item_id" not in native_public["events"][0]
    assert native_public["native_image"]["requested_model"] == "gpt-5.6-luna"

    with pytest.raises(module.CommonNativeAuditError) as failed:
        module.collect_common_codex_conversation_audit(
            **{
                **_common_audit_arguments(child, _private_dir(tmp_path)),
                "stdout_events": [
                    {"type": "thread.started", "thread_id": RAW_THREAD},
                    {"type": "thread.started", "thread_id": "other-thread"},
                ],
                "finalize": False,
            }
        )
    pre_manifest_failure = failed.value.evidence
    assert codex_audit.contains_native_private_evidence(pre_manifest_failure)
    assert "native_private_manifest" not in pre_manifest_failure


def _assert_image_record(record: dict, *, path: Path, width: int, height: int, payload: bytes) -> None:
    assert record["path"] == str(path)
    assert record["png_valid"] is True
    assert record["width"] == width
    assert record["height"] == height
    assert record["bytes"] == len(payload)
    assert record["sha256"] == hashlib.sha256(payload).hexdigest()


def _assert_safe_projection(
    projection: dict,
    *,
    business_sha256: str | None = None,
    private_values: tuple[str, ...] = (),
    expected_model: str = "gpt-5.6-luna",
) -> None:
    serialized = json.dumps(projection, ensure_ascii=False)
    for forbidden in (
        RAW_THREAD,
        RAW_CALL,
        RAW_ITEM,
        RAW_PROMPT,
        RAW_ACCOUNT,
        RAW_ERROR_PATH,
        *private_values,
    ):
        assert forbidden not in serialized
    assert projection["terminal_state"] in {"result_received", "normalization_failed", "failed"}
    assert projection["attempt"] == 2
    assert projection["requested_model"] == expected_model
    assert projection["actual_model"] == expected_model
    if business_sha256 is not None:
        assert projection["business_image"]["sha256"] == business_sha256
        assert projection["business_image"]["png_valid"] is True
        assert "path" not in projection["business_image"]


def test_native_image_uses_canonical_payload_and_saved_path_with_complete_private_audit(tmp_path: Path):
    """Luna's event-only form binds its literal revised prompt and saved path."""

    child = _persistent_child(tmp_path)
    private_dir = _private_dir(tmp_path)
    default_png = child["saved_path"].read_bytes()
    original_session = child["session"].read_bytes()

    evidence = _collect(child, private_dir)

    assert codex_audit.contains_native_private_evidence(evidence)
    assert evidence["thread_id"] == RAW_THREAD
    assert evidence["imagegen_call_id"] == RAW_CALL
    assert evidence["imagegen_input"] == {
        "source": LUNA_EVENT_INPUT_SOURCE,
        "sha256": REVISED_PROMPT_SHA256,
        "revised_prompt_sha256": REVISED_PROMPT_SHA256,
    }
    assert evidence["imagegen_call_arguments_sha256"] == "not_applicable"
    assert evidence["requested_model"] == evidence["actual_model"] == "gpt-5.6-luna"
    assert evidence["requested_reasoning_effort"] == evidence["actual_reasoning_effort"] == "low"
    assert evidence["cli_version"] == "codex-cli test-version"
    assert evidence["cli_binary"] == "/opt/codex"
    assert evidence["binary_sha256"] == "binary-sha"
    assert evidence["attempt"] == 2
    assert evidence["terminal_state"] == "result_received"
    assert evidence["retry"] is True
    assert evidence["error"] is None
    assert evidence["timeout"] is False
    assert evidence["skip"] is False
    assert evidence["fallback_used"] is False

    session = evidence["canonical_session"]
    assert session["source_path"] == str(child["session"])
    assert Path(session["archive_path"]).is_relative_to(private_dir)
    assert session["bytes"] == len(original_session)
    assert session["sha256"] == hashlib.sha256(original_session).hexdigest()
    assert Path(session["archive_path"]).read_bytes() == original_session
    assert child["session"].read_bytes() == original_session

    _assert_image_record(evidence["default_image"], path=child["saved_path"], width=1672, height=941, payload=default_png)
    assert evidence["default_image"]["path"] != str(child["newest_unrelated_path"])
    original_path = Path(evidence["original_image"]["path"])
    _assert_image_record(evidence["original_image"], path=original_path, width=1672, height=941, payload=default_png)
    business_path = Path(evidence["business_image"]["path"])
    _assert_image_record(evidence["business_image"], path=business_path, width=1672, height=941, payload=default_png)
    assert original_path != child["saved_path"]
    assert original_path.is_relative_to(private_dir)
    assert original_path.read_bytes() == default_png
    assert business_path == _business_output_path(tmp_path / "artifacts")
    assert business_path.is_relative_to(tmp_path / "artifacts")
    assert not business_path.is_relative_to(tmp_path / "artifacts" / ".codex-private")
    assert business_path.read_bytes() == default_png
    assert abs(1672 / 941 - 16 / 9) <= 0.02
    assert evidence["normalization"]["normalized"] is False
    assert evidence["normalization"]["operation"] == "byte_copy"
    assert evidence["normalization"]["parent_sha256"] == evidence["normalization"]["child_sha256"]
    assert "image_output_protocol" not in evidence["public_projection"]
    _assert_safe_projection(
        evidence["public_projection"],
        business_sha256=evidence["business_image"]["sha256"],
        private_values=(
            str(child["codex_home"]),
            str(child["session"]),
            str(child["saved_path"]),
            str(original_path),
            str(business_path),
        ),
    )


def test_native_image_sol_function_call_keeps_literal_arguments_binding_and_end_path(tmp_path: Path):
    """Sol retains its explicit call binding without making it a Luna prerequisite."""

    child = _persistent_child(tmp_path, include_imagegen_function_call=True)
    default_png = child["saved_path"].read_bytes()

    evidence = _collect(
        child,
        _private_dir(tmp_path),
        requested_model="gpt-5.6-sol",
        actual_model="gpt-5.6-sol",
    )

    assert evidence["imagegen_call_id"] == RAW_CALL
    assert evidence["imagegen_call_arguments_sha256"] == CALL_ARGUMENTS_SHA256
    assert evidence["imagegen_input"] == {
        "source": SOL_FUNCTION_CALL_INPUT_SOURCE,
        "sha256": CALL_ARGUMENTS_SHA256,
        "revised_prompt_sha256": REVISED_PROMPT_SHA256,
    }
    _assert_image_record(evidence["default_image"], path=child["saved_path"], width=1672, height=941, payload=default_png)
    assert evidence["default_image"]["path"] != str(child["newest_unrelated_path"])
    _assert_safe_projection(
        evidence["public_projection"],
        business_sha256=evidence["business_image"]["sha256"],
        private_values=(
            str(child["codex_home"]),
            str(child["session"]),
            str(child["saved_path"]),
            str(child["newest_unrelated_path"]),
        ),
        expected_model="gpt-5.6-sol",
    )


def test_native_image_binds_one_new_thread_scoped_cli_png_without_fabricating_legacy_provenance(tmp_path: Path):
    module = _native_module()
    child = _thread_scoped_child(tmp_path)
    copied_assistant_output = tmp_path / "project-copy.png"
    copied_assistant_output.write_bytes((child["thread_directory"] / "tool-output.png").read_bytes())

    evidence = _collect_thread_scoped(child, _private_dir(tmp_path))

    assert evidence["image_output_protocol"] == module.THREAD_SCOPED_GENERATED_IMAGE_PROTOCOL
    assert evidence["imagegen_call_id"] == "not_applicable"
    assert evidence["imagegen_call_arguments_sha256"] == "not_applicable"
    assert evidence["imagegen_input"] == "not_applicable"
    provenance = evidence["thread_scoped_generated_image"]
    assert provenance["thread_id"] == RAW_THREAD
    assert provenance["source_path"] == str(child["thread_directory"] / "tool-output.png")
    assert provenance["source_path"] != str(copied_assistant_output)
    assert provenance["image"]["png_valid"] is True
    assert evidence["default_image"]["sha256"] == provenance["image"]["sha256"]
    assert evidence["public_projection"]["image_output_protocol"] == module.THREAD_SCOPED_GENERATED_IMAGE_PROTOCOL
    public_serialized = json.dumps(evidence["public_projection"], ensure_ascii=False)
    assert RAW_THREAD not in public_serialized
    assert str(child["thread_directory"]) not in public_serialized


def test_native_launcher_captures_the_thread_baseline_before_accepting_cli_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    module = _native_module()
    child = _persistent_child(tmp_path, completed_end_count=0)
    monkeypatch.setenv("CODEX_HOME", str(child["codex_home"]))

    async def fake_run_codex_exec_json(**kwargs):
        hook = kwargs.get("thread_started_hook")
        assert callable(hook)
        hook(RAW_THREAD)
        thread_directory = child["codex_home"] / "generated_images" / RAW_THREAD
        thread_directory.mkdir(parents=True)
        (thread_directory / "tool-output.png").write_bytes(_png(1672, 941))
        transport = str(kwargs["prompt"])
        return SimpleNamespace(
            command=["codex"],
            invocation_id=None,
            exit_code=0,
            timed_out=False,
            prompt_sha256=hashlib.sha256(transport.encode("utf-8")).hexdigest(),
            stream_summary=SimpleNamespace(
                thread_id=RAW_THREAD,
                thread_ids=(RAW_THREAD,),
                event_projections=(
                    {"event_type": "thread.started", "thread_id": RAW_THREAD},
                    {"event_type": "turn.completed"},
                ),
                omitted_event_projection_count=0,
                final_capture=None,
            ),
        )

    async def fake_supervised_child(*, invoke, **_kwargs):
        result = await invoke(SimpleNamespace(attempt=1))
        return SimpleNamespace(
            result=result,
            attempt_count=1,
            complete_projection=lambda: True,
            fail_unrecoverable=lambda _reason: None,
        )

    monkeypatch.setattr(module, "run_codex_exec_json", fake_run_codex_exec_json)
    monkeypatch.setattr(module, "run_supervised_codex_child", fake_supervised_child)
    monkeypatch.setattr(module, "_native_cli_identity", lambda *_args, **_kwargs: ("/private/codex", "test", "a" * 64))
    business_output = _business_output_path(tmp_path / "artifacts")
    generated = module.generate_codex_native_image(
        {"api_type": "codex_native_image", "model": "gpt-5.6-luna", "thinking": "low"},
        "thread-scoped Native launch prompt",
        str(business_output),
        run_id=11,
        run_slide_id=3,
        stage_id="native-image-stage",
        output_dir=tmp_path / "unused-output",
        timeout_seconds=45,
    )

    assert Path(generated["response"]["image_path"]).read_bytes() == _png(1672, 941)
    assert generated["native_public"]["image_output_protocol"] == module.THREAD_SCOPED_GENERATED_IMAGE_PROTOCOL


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("absent", "exactly one new file"),
        ("multiple", "exactly one new file"),
        ("invalid", "valid PNG"),
        ("preexisting", "baseline"),
        ("no_turn_complete", "turn.completed"),
        ("error_event", "error-free"),
        ("nonzero", "successful non-timeout"),
    ],
)
def test_native_image_thread_scoped_protocol_fails_closed_for_untrusted_or_ambiguous_output(
    tmp_path: Path, mutation: str, expected: str
):
    module = _native_module()
    child = _thread_scoped_child(tmp_path)
    overrides: dict[str, object] = {}
    output = child["thread_directory"] / "tool-output.png"
    if mutation == "absent":
        output.unlink()
    elif mutation == "multiple":
        (child["thread_directory"] / "second-output.png").write_bytes(_png(10, 10))
    elif mutation == "invalid":
        output.write_bytes(b"not-a-png")
    elif mutation == "preexisting":
        preexisting = _persistent_child(tmp_path / "preexisting", completed_end_count=0)
        preexisting_dir = preexisting["codex_home"] / "generated_images" / RAW_THREAD
        preexisting_dir.mkdir(parents=True)
        (preexisting_dir / "old.png").write_bytes(_png(10, 10))
        baseline = module._capture_thread_output_baseline(
            codex_home=preexisting["codex_home"], thread_id=RAW_THREAD
        )
        preexisting["thread_output_baseline"] = baseline
        preexisting["thread_directory"] = preexisting_dir
        preexisting["stdout_events"] = [
            {"type": "thread.started", "thread_id": RAW_THREAD},
            {"type": "turn.completed"},
        ]
        child = preexisting
    elif mutation == "no_turn_complete":
        child["stdout_events"] = [{"type": "thread.started", "thread_id": RAW_THREAD}]
    elif mutation == "error_event":
        child["stdout_events"].append({"type": "error"})
    elif mutation == "nonzero":
        overrides["execution_exit_code"] = 1

    with pytest.raises(ValueError, match=expected):
        _collect_thread_scoped(child, _private_dir(tmp_path), **overrides)


def test_native_image_thread_scoped_protocol_rejects_a_link_or_hardlink_candidate(tmp_path: Path):
    child = _thread_scoped_child(tmp_path)
    output = child["thread_directory"] / "tool-output.png"
    output.unlink()
    target = tmp_path / "outside.png"
    target.write_bytes(_png(10, 10))
    output.symlink_to(target)

    with pytest.raises(ValueError, match="link or reparse"):
        _collect_thread_scoped(child, _private_dir(tmp_path))

    output.unlink()
    hardlink_target = tmp_path / "hardlink-outside.png"
    hardlink_target.write_bytes(_png(10, 10))
    os.link(hardlink_target, output)
    with pytest.raises(ValueError, match="hard-linked"):
        _collect_thread_scoped(child, _private_dir(tmp_path))


def test_native_image_thread_scoped_protocol_rejects_a_different_stdout_thread(tmp_path: Path):
    module = _native_module()
    child = _thread_scoped_child(tmp_path)
    with pytest.raises(ValueError, match="does not match"):
        module._thread_scoped_image_details(
            baseline=child["thread_output_baseline"],
            stdout_events=[
                {"type": "thread.started", "thread_id": "different-thread"},
                {"type": "turn.completed"},
            ],
            execution_exit_code=0,
            execution_timed_out=False,
        )


@pytest.mark.parametrize(
    (
        "include_imagegen_function_call",
        "function_call_ids",
        "function_call_arguments",
        "completed_end_count",
        "image_end_call_id",
        "include_end_call_id",
        "include_revised_prompt",
        "include_saved_path",
        "valid_png",
        "failure",
    ),
    [
        (False, None, CALL_ARGUMENTS, 0, None, True, True, True, True, "exactly one completed"),
        (False, None, CALL_ARGUMENTS, 2, None, True, True, True, True, "exactly one completed"),
        (False, None, CALL_ARGUMENTS, 1, None, False, True, True, True, "call_id"),
        (False, None, CALL_ARGUMENTS, 1, None, True, False, True, True, "revised_prompt"),
        (False, None, CALL_ARGUMENTS, 1, None, True, True, False, True, "saved_path"),
        (True, [RAW_CALL, "second-call"], CALL_ARGUMENTS, 1, None, True, True, True, True, "exactly one imagegen"),
        (True, [RAW_CALL], None, 1, None, True, True, True, True, "arguments"),
        (True, [RAW_CALL], CALL_ARGUMENTS, 1, "wrong-call", True, True, True, True, "call"),
        (False, None, CALL_ARGUMENTS, 1, None, True, True, True, False, "PNG"),
    ],
    ids=[
        "zero_completed_end",
        "multiple_completed_ends",
        "missing_end_call_id",
        "missing_revised_prompt",
        "missing_saved_path",
        "multiple_sol_calls",
        "missing_sol_arguments",
        "mismatched_sol_call_id",
        "invalid_event_selected_png",
    ],
)
def test_native_image_rejects_missing_ambiguous_or_mismatched_canonical_binding(
    tmp_path: Path,
    include_imagegen_function_call: bool,
    function_call_ids: list[str] | None,
    function_call_arguments: str | None,
    completed_end_count: int,
    image_end_call_id: str | None,
    include_end_call_id: bool,
    include_revised_prompt: bool,
    include_saved_path: bool,
    valid_png: bool,
    failure: str,
):
    child = _persistent_child(
        tmp_path,
        include_imagegen_function_call=include_imagegen_function_call,
        function_call_ids=function_call_ids,
        function_call_arguments=function_call_arguments,
        completed_end_count=completed_end_count,
        image_end_call_id=image_end_call_id,
        include_end_call_id=include_end_call_id,
        include_revised_prompt=include_revised_prompt,
        include_saved_path=include_saved_path,
        valid_png=valid_png,
    )

    with pytest.raises(ValueError, match=failure):
        _collect(
            child,
            _private_dir(tmp_path),
            requested_model="gpt-5.6-sol" if include_imagegen_function_call else "gpt-5.6-luna",
            actual_model="gpt-5.6-sol" if include_imagegen_function_call else "gpt-5.6-luna",
        )


def test_native_image_non_16_9_normalizes_with_pinned_pillow_and_recorded_uncropped_derivation(tmp_path: Path):
    child = _persistent_child(tmp_path, width=300, height=200)
    private_dir = _private_dir(tmp_path)
    default_png = child["saved_path"].read_bytes()

    evidence = _collect(child, private_dir)

    assert abs(300 / 200 - 16 / 9) > 0.02
    _assert_image_record(evidence["default_image"], path=child["saved_path"], width=300, height=200, payload=default_png)
    _assert_image_record(evidence["original_image"], path=Path(evidence["original_image"]["path"]), width=300, height=200, payload=default_png)
    business = evidence["business_image"]
    business_path = Path(business["path"])
    assert business["png_valid"] is True
    assert (business["width"], business["height"]) == (1920, 1080)
    assert business["sha256"] != evidence["default_image"]["sha256"]
    assert business_path == _business_output_path(tmp_path / "artifacts")
    assert not business_path.is_relative_to(tmp_path / "artifacts" / ".codex-private")
    assert evidence["normalization"]["normalized"] is True
    assert evidence["normalization"]["algorithm"] == "native_slide_16x9_v1"
    assert evidence["normalization"]["pillow_version"] == "12.1.1"
    derivation = evidence["normalization"]["derivation"]
    assert derivation["background"] == "blurred_cover"
    assert derivation["foreground"] == "uncropped_centered_aspect_fit"
    assert derivation["parent_dimensions"] == {"width": 300, "height": 200}
    assert derivation["child_dimensions"] == {"width": 1920, "height": 1080}
    assert derivation["parent_sha256"] == evidence["default_image"]["sha256"]
    assert derivation["child_sha256"] == business["sha256"]
    _assert_safe_projection(
        evidence["public_projection"],
        business_sha256=business["sha256"],
        private_values=(
            str(child["codex_home"]),
            str(child["session"]),
            str(child["saved_path"]),
            evidence["original_image"]["path"],
            business["path"],
        ),
    )


def test_native_image_normalization_failure_re_finalizes_same_invocation_and_removes_stale_business_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    module = _native_module()
    normalize = getattr(module, "normalize_native_image", None)
    assert callable(normalize), "Native image normalizer is missing"
    child = _persistent_child(tmp_path, width=300, height=200)
    private_dir = _private_dir(tmp_path)
    original_session = child["session"].read_bytes()
    default_png = child["saved_path"].read_bytes()
    business_path = _business_output_path(tmp_path / "artifacts")
    assert business_path.is_relative_to(tmp_path / "artifacts")
    assert not business_path.is_relative_to(tmp_path / "artifacts" / ".codex-private")
    business_path.parent.mkdir(parents=True)
    business_path.write_bytes(b"stale-business-image-must-not-survive-failed-normalization")
    finalized: list[tuple[int, dict]] = []

    def explode(*_args, **_kwargs):
        raise RuntimeError("normalizer exploded")

    monkeypatch.setattr(module, "normalize_native_image", explode)
    with pytest.raises(RuntimeError, match="normalization_failed"):
        _collect(
            child,
            private_dir,
            business_output_path=business_path,
            invocation_id=77,
            refinalize=lambda invocation_id, metadata: finalized.append((invocation_id, metadata)),
        )

    assert not business_path.exists()
    assert finalized and finalized[-1][0] == 77
    failure = finalized[-1][1]
    assert failure["terminal_state"] == "normalization_failed"
    assert failure["failure_code"] == "normalization_failed"
    session = failure["canonical_session"]
    assert Path(session["archive_path"]).is_relative_to(private_dir)
    assert Path(session["archive_path"]).read_bytes() == original_session
    assert child["session"].read_bytes() == original_session
    assert child["saved_path"].read_bytes() == default_png
    original_path = Path(failure["original_image"]["path"])
    assert original_path.is_relative_to(private_dir)
    _assert_image_record(failure["original_image"], path=original_path, width=300, height=200, payload=default_png)
    _assert_safe_projection(
        failure["public_projection"],
        private_values=(
            str(child["codex_home"]),
            str(child["session"]),
            str(child["saved_path"]),
            str(original_path),
            str(business_path),
        ),
    )


def test_native_image_conflicting_immutable_original_is_never_overwritten(tmp_path: Path):
    child = _persistent_child(tmp_path)
    private_dir = _private_dir(tmp_path)
    original_path = private_dir / "native-original.png"
    business_path = _business_output_path(tmp_path / "artifacts")
    conflicting_payload = _png(99, 77)
    original_path.parent.mkdir(parents=True)
    original_path.write_bytes(conflicting_payload)
    business_path.parent.mkdir(parents=True)
    business_path.write_bytes(b"stale-business-image-must-not-survive-conflicting-original")
    finalized: list[tuple[int, dict]] = []

    with pytest.raises(ValueError, match="immutable Native original already exists with different bytes"):
        _collect(
            child,
            private_dir,
            invocation_id=311,
            refinalize=lambda invocation_id, metadata: finalized.append((invocation_id, metadata)),
        )

    assert original_path.read_bytes() == conflicting_payload
    assert not business_path.exists()
    assert len(finalized) == 1
    assert finalized[-1][0] == 311
    failure = finalized[-1][1]
    assert failure["terminal_state"] == "failed"
    assert failure["failure_code"] == "image_call_binding_failed"
    assert codex_audit.contains_native_private_evidence(failure)
    _assert_safe_projection(
        failure["public_projection"],
        private_values=(str(child["codex_home"]), str(child["session"]), str(original_path), str(business_path)),
    )


def test_director_common_audit_has_literal_not_applicable_image_fields_and_success_re_finalization(tmp_path: Path):
    child = _persistent_child(tmp_path)
    finalized: list[tuple[int, dict]] = []
    evidence = _collect(
        child,
        _private_dir(tmp_path),
        invocation_id=88,
        refinalize=lambda invocation_id, metadata: finalized.append((invocation_id, metadata)),
        director_audit=True,
        requested_model="gpt-5.6-sol",
        actual_model="gpt-5.6-sol",
        requested_reasoning_effort="low",
        actual_reasoning_effort="low",
    )

    assert evidence["attempt"] == 2
    assert evidence["terminal_state"] == "result_received"
    assert evidence["requested_model"] == evidence["actual_model"] == "gpt-5.6-sol"
    assert evidence["requested_reasoning_effort"] == evidence["actual_reasoning_effort"] == "low"
    assert evidence["canonical_session"]["bytes"] > 0
    assert evidence["canonical_session"]["sha256"]
    for image_field in (
        "imagegen_call_id",
        "imagegen_call_arguments_sha256",
        "imagegen_input",
        "default_image",
        "original_image",
        "business_image",
        "normalization",
    ):
        assert evidence[image_field] == "not_applicable"
    assert finalized and finalized[-1][0] == 88
    assert finalized[-1][1]["terminal_state"] == "result_received"
    _assert_safe_projection(
        evidence["public_projection"],
        private_values=(str(child["codex_home"]), str(child["session"])),
        expected_model="gpt-5.6-sol",
    )


def test_canonical_failure_re_finalizes_same_invocation_with_safe_error_facts(tmp_path: Path):
    child = _persistent_child(tmp_path)
    finalized: list[tuple[int, dict]] = []

    with pytest.raises(ValueError, match="exactly one"):
        _collect(
            child,
            _private_dir(tmp_path),
            invocation_id=99,
            error=RAW_ERROR_PATH,
            refinalize=lambda invocation_id, metadata: finalized.append((invocation_id, metadata)),
        )

    assert finalized and finalized[-1][0] == 99
    failure = finalized[-1][1]
    assert failure["terminal_state"] == "failed"
    assert failure["attempt"] == 2
    assert failure["error"] == RAW_ERROR_PATH
    assert failure["failure_code"]
    _assert_safe_projection(
        failure["public_projection"],
        private_values=(str(child["codex_home"]), str(child["session"])),
    )


def test_common_audit_re_finalizes_one_db_invocation_for_success_then_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    module = _native_module()
    collect_common = getattr(module, "collect_common_codex_conversation_audit", None)
    assert callable(collect_common), "Native common audit collector is missing"
    monkeypatch.setattr(dbmod, "DB_PATH", tmp_path / "common-audit.db")
    dbmod.init_db()
    child = _persistent_child(tmp_path)
    private_dir = _private_dir(tmp_path)
    raw_path = private_dir / "codex.raw.jsonl"
    observed_path = private_dir / "codex.observed.jsonl"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"")
    observed_path.write_text("", encoding="utf-8")
    invocation_id = codex_audit.start_codex_invocation(
        stage_id="native-image-stage",
        role="image_generator",
        raw_jsonl_path=str(raw_path),
        observed_jsonl_path=str(observed_path),
        started_at="2026-07-24T00:00:00Z",
    )
    arguments = {
        "codex_home": child["codex_home"],
        "private_dir": private_dir,
        "requested_model": "gpt-5.6-luna",
        "requested_reasoning_effort": "low",
        "actual_model": "gpt-5.6-luna",
        "actual_reasoning_effort": "low",
        "cli_binary": "/private/codex",
        "cli_version": "codex-cli test-version",
        "binary_sha256": "binary-sha",
        "attempt": 2,
        "terminal_state": "result_received",
        "retry": True,
        "error": None,
        "timeout": False,
        "skip": False,
        "fallback_used": False,
        "invocation_id": invocation_id,
    }
    evidence = collect_common(stdout_events=child["stdout_events"], **arguments)
    detail = dbmod.get_codex_invocation(invocation_id)
    assert detail["status"] == "result_received"
    assert detail["metadata"]["canonical_session"] == evidence["canonical_session"]
    assert detail["metadata"]["native_private_manifest"]["private_root"].endswith("run-11")

    with pytest.raises(ValueError, match="exactly one"):
        collect_common(
            stdout_events=[
                {"type": "thread.started", "thread_id": RAW_THREAD},
                {"type": "thread.started", "thread_id": "other-thread"},
            ],
            **{**arguments, "error": RAW_ERROR_PATH},
        )

    db = dbmod.get_db()
    try:
        assert db.execute("SELECT COUNT(*) FROM codex_invocations").fetchone()[0] == 1
    finally:
        db.close()
    detail = dbmod.get_codex_invocation(invocation_id)
    assert detail["status"] == "failed"
    assert detail["metadata"]["error"] == RAW_ERROR_PATH
    assert detail["metadata"]["failure_code"] == "canonical_session_unavailable"
    assert codex_audit.redact_audit_error(RAW_ERROR_PATH) == "<redacted-path>"


_LINUX_NAME_MAX_BYTES = 255
# Exact Linux page-5 business PNG component that is legal at 228 UTF-8 bytes,
# while the old `.<dest>-<32 hex>.tmp` sibling is 266 bytes and exceeds NAME_MAX.
_PAGE5_BUSINESS_PNG_COMPONENT = (
    "2.重点跟踪行业：光伏、储能、锂电 _ 2.重点跟踪行业：工程机械、半导体设备、自动化、碳中和、氢能源 _ "
    "邹润芳 _ 卢正羽： _ 闫智： _ 龙碱： _ 我们设定的上_349062061720.png"
)


def _old_atomic_temp_component(destination_name: str, hex_token: str = "a" * 32) -> str:
    return f".{destination_name}-{hex_token}.tmp"


def test_atomic_write_bytes_replaces_legal_long_utf8_png_without_embedding_destination_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _native_module()
    destination_name = _PAGE5_BUSINESS_PNG_COMPONENT
    assert len(destination_name.encode("utf-8")) == 228
    output_dir = tmp_path / "business-out"
    output_dir.mkdir()
    old_temp_name = _old_atomic_temp_component(destination_name)
    assert len(old_temp_name.encode("utf-8")) == 266
    with pytest.raises(OSError) as old_temp_error:
        (output_dir / old_temp_name).write_bytes(b"must-not-fit")
    assert old_temp_error.value.errno == errno.ENAMETOOLONG

    destination = output_dir / destination_name
    payload = b"post-bind-business-png-bytes"
    fixed_token = SimpleNamespace(hex="0123456789abcdef0123456789abcdef")
    monkeypatch.setattr(module.uuid, "uuid4", lambda: fixed_token)
    expected_temp = output_dir / f".pptgen-atomic-{fixed_token.hex}.tmp"
    assert len(expected_temp.name.encode("utf-8")) <= _LINUX_NAME_MAX_BYTES
    assert destination_name not in expected_temp.name
    assert expected_temp.name.startswith(".")
    opened_modes: list[str] = []
    original_open = Path.open

    def spy_open(self, mode="r", *args, **kwargs):
        if Path(self) == expected_temp:
            opened_modes.append(str(mode))
        return original_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", spy_open)

    module._atomic_write_bytes(destination, payload, mode=0o600)

    assert opened_modes == ["xb"]
    assert destination.read_bytes() == payload
    assert destination.name == destination_name
    assert destination.stat().st_mode & 0o777 == 0o600
    assert not expected_temp.exists()
    assert [path.name for path in output_dir.iterdir()] == [destination_name]


def test_atomic_write_bytes_unlinks_short_temp_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _native_module()
    output_dir = tmp_path / "business-out"
    output_dir.mkdir()
    destination = output_dir / _PAGE5_BUSINESS_PNG_COMPONENT
    payload = b"must-not-commit"
    fixed_token = SimpleNamespace(hex="fedcba9876543210fedcba9876543210")
    monkeypatch.setattr(module.uuid, "uuid4", lambda: fixed_token)
    expected_temp = output_dir / f".pptgen-atomic-{fixed_token.hex}.tmp"

    def explode(_source, _target):
        raise OSError("atomic replace failed")

    monkeypatch.setattr(module.os, "replace", explode)
    with pytest.raises(OSError, match="atomic replace failed"):
        module._atomic_write_bytes(destination, payload, mode=0o640)

    assert not destination.exists()
    assert not expected_temp.exists()
    assert list(output_dir.iterdir()) == []
