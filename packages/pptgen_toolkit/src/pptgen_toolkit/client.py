"""Small HTTP client for existing PPTGen Platform routes."""

from __future__ import annotations

import json
import os
import socket
from typing import Any
from urllib import error, parse, request


class PlatformError(RuntimeError):
    """The platform responded, but its response could not be used."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class PlatformUnavailable(PlatformError):
    """The configured platform could not be reached."""


RUNTIME_IDENTITY_FIELDS = (
    "base_url",
    "build_id",
    "data_root",
    "instance_id",
    "product",
    "release_root",
    "service",
    "skill_sha256",
    "source_commit",
    "version",
)


class PptgenClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        long_timeout: float = 900.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.long_timeout = long_timeout

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"

        req = request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with request.urlopen(
                req,
                timeout=self.timeout if timeout is None else timeout,
            ) as response:
                raw = response.read()
        except error.HTTPError as exc:
            raw_error = exc.read()
            try:
                error_payload = json.loads(raw_error.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                error_payload = None
            typed_error_code = (
                error_payload.get("error") if isinstance(error_payload, dict) else None
            )
            if isinstance(typed_error_code, str) and typed_error_code in {
                "resource_unavailable",
                "executable_identity_unavailable",
                "target_page_count_unavailable",
            }:
                raise PlatformError(
                    str(
                        error_payload.get("message")
                        or typed_error_code.replace("_", " ")
                    ),
                    code=typed_error_code,
                ) from exc
            raise PlatformError(
                f"PPTGen Platform returned HTTP {exc.code} for {path}"
            ) from exc
        except (error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise PlatformUnavailable(
                f"Cannot reach PPTGen Platform at {self.base_url}"
            ) from exc

        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PlatformError(
                f"PPTGen Platform returned invalid JSON for {path}"
            ) from exc

    def health(self) -> dict[str, Any]:
        identity = self._request("GET", "/api/runtime-identity")
        if not isinstance(identity, dict):
            raise PlatformError("PPTGen Platform returned an invalid runtime identity")
        missing = [field for field in RUNTIME_IDENTITY_FIELDS if not identity.get(field)]
        if missing:
            raise PlatformError(
                "PPTGen Platform runtime identity is missing: " + ", ".join(missing)
            )
        if str(identity["base_url"]).rstrip("/") != self.base_url:
            raise PlatformError("PPTGen Platform base URL identity mismatch")
        if identity["product"] != "PPTGen":
            raise PlatformError("PPTGen Platform product identity mismatch")
        if identity["service"] != "pptgen-platform":
            raise PlatformError("PPTGen Platform service identity mismatch")

        expected_raw = os.environ.get("PPTGEN_EXPECTED_IDENTITY_JSON")
        if expected_raw:
            try:
                expected_identity = json.loads(expected_raw)
            except json.JSONDecodeError as exc:
                raise PlatformError(
                    "PPTGen trusted runtime identity is invalid"
                ) from exc
            if not isinstance(expected_identity, dict):
                raise PlatformError("PPTGen trusted runtime identity is invalid")
            expected_missing = [
                field
                for field in RUNTIME_IDENTITY_FIELDS
                if not expected_identity.get(field)
            ]
            if expected_missing:
                raise PlatformError(
                    "PPTGen trusted runtime identity is missing: "
                    + ", ".join(expected_missing)
                )
            for field in RUNTIME_IDENTITY_FIELDS:
                actual = str(identity[field])
                expected = str(expected_identity[field])
                if field == "base_url":
                    actual = actual.rstrip("/")
                    expected = expected.rstrip("/")
                if actual != expected:
                    label = field.replace("_", " ")
                    raise PlatformError(
                        f"PPTGen Platform {label} identity mismatch"
                    )
        expected_build = os.environ.get("PPTGEN_EXPECTED_BUILD_ID")
        if expected_build and identity["build_id"] != expected_build:
            raise PlatformError("PPTGen Platform build identity mismatch")
        expected_instance = os.environ.get("PPTGEN_EXPECTED_INSTANCE_ID")
        if expected_instance and identity["instance_id"] != expected_instance:
            raise PlatformError("PPTGen Platform instance identity mismatch")
        return {**identity, "ok": True}

    def create_deck(self, *, title: str, content: str) -> int:
        result = self._request(
            "POST",
            "/api/decks",
            {"title": title, "content": content},
        )
        deck_id = result.get("id") if isinstance(result, dict) else None
        if not isinstance(deck_id, int):
            raise PlatformError("PPTGen Platform did not return a valid deck id")
        return deck_id

    def _default_html_config_id(self) -> int:
        configs = self._request("GET", "/api/configs")
        if not isinstance(configs, list):
            raise PlatformError("PPTGen Platform returned an invalid config list")
        html_configs = [
            item
            for item in configs
            if isinstance(item, dict)
            and str(item.get("type") or "html").strip().lower() == "html"
            and isinstance(item.get("id"), int)
        ]
        default = next(
            (item for item in html_configs if bool(item.get("is_default"))),
            html_configs[0] if html_configs else None,
        )
        if default is None:
            raise PlatformError("PPTGen Platform has no HTML configuration")
        return default["id"]

    def create_split_draft(self, *, deck_id: int, mode: str) -> dict[str, Any]:
        payload: dict[str, Any] = {"mode": mode}

        result = self._request(
            "POST",
            f"/api/decks/{deck_id}/split-drafts",
            payload,
            timeout=self.long_timeout if mode == "llm" else None,
        )
        if not isinstance(result, dict):
            raise PlatformError("PPTGen Platform returned an invalid split draft")
        if not isinstance(result.get("id"), int) or not isinstance(
            result.get("slides"), list
        ):
            raise PlatformError("PPTGen Platform returned an invalid split draft")
        return result

    def retry_split_draft(self, *, draft_id: int) -> dict[str, Any]:
        result = self._request(
            "POST",
            f"/api/deck-split-drafts/{draft_id}/retry",
            {},
            timeout=self.long_timeout,
        )
        if not isinstance(result, dict):
            raise PlatformError("PPTGen Platform returned an invalid split draft")
        if not isinstance(result.get("id"), int) or not isinstance(
            result.get("slides"), list
        ):
            raise PlatformError("PPTGen Platform returned an invalid split draft")
        return result

    def revise_split_draft(
        self,
        *,
        draft_id: int,
        instruction: str,
    ) -> dict[str, Any]:
        result = self._request(
            "POST",
            f"/api/deck-split-drafts/{draft_id}/revise",
            {"instruction": instruction},
            timeout=self.long_timeout,
        )
        if not isinstance(result, dict):
            raise PlatformError("PPTGen Platform returned an invalid split draft")
        if not isinstance(result.get("id"), int) or not isinstance(
            result.get("slides"), list
        ):
            raise PlatformError("PPTGen Platform returned an invalid split draft")
        return result

    def confirm_split_draft(self, *, draft_id: int) -> dict[str, Any]:
        result = self._request(
            "POST",
            f"/api/deck-split-drafts/{draft_id}/confirm",
        )
        if not isinstance(result, dict):
            raise PlatformError("PPTGen Platform returned an invalid confirmation")
        slide_ids = result.get("slide_ids")
        slides = result.get("slides")
        if (
            not isinstance(slide_ids, list)
            or not slide_ids
            or not all(isinstance(slide_id, int) for slide_id in slide_ids)
            or not isinstance(slides, list)
            or not slides
            or not isinstance(slides[0], dict)
            or not isinstance(slides[0].get("deck_id"), int)
        ):
            raise PlatformError("PPTGen Platform returned an invalid confirmation")
        return {
            "deck_id": slides[0]["deck_id"],
            "draft_id": draft_id,
            "slide_count": len(slide_ids),
            "slide_ids": slide_ids,
            "status": "confirmed",
        }

    def start_generation(
        self,
        *,
        deck_id: int,
        intent: str,
        preference: str | None = None,
    ) -> dict[str, Any]:
        config_id = self._default_html_config_id()
        payload: dict[str, Any] = {"deck_id": deck_id, "config_id": config_id}
        if intent == "auto":
            payload["mode"] = "auto"
        else:
            requirement = self._request(
                "POST",
                "/api/requirements",
                {"title": "PPTGen Skill preference", "content": preference or ""},
            )
            requirement_id = (
                requirement.get("id") if isinstance(requirement, dict) else None
            )
            if not isinstance(requirement_id, int):
                raise PlatformError(
                    "PPTGen Platform did not return a valid requirement id"
                )
            payload.update({"requirement_ids": [requirement_id], "color_ids": []})

        result = self._request(
            "POST",
            "/api/generate",
            payload,
            timeout=self.long_timeout,
        )
        if not isinstance(result, dict):
            raise PlatformError("PPTGen Platform returned an invalid generation")
        batch_id = result.get("batch_id")
        run_ids = result.get("run_ids")
        if (
            not isinstance(batch_id, int)
            or not isinstance(run_ids, list)
            or not run_ids
            or not all(isinstance(run_id, int) for run_id in run_ids)
        ):
            raise PlatformError("PPTGen Platform returned an invalid generation")
        return {
            "batch_id": batch_id,
            "deck_id": deck_id,
            "intent": intent,
            "run_ids": run_ids,
            "status": "generation_started",
        }

    def get_run_status(
        self,
        *,
        run_id: int,
        activity_after: str | None = None,
    ) -> dict[str, Any]:
        path = f"/api/runs/{run_id}/status"
        if activity_after:
            path += "?" + parse.urlencode({"activity_after": activity_after})
        result = self._request("GET", path)
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("status"), str)
            or not isinstance(result.get("progress"), dict)
        ):
            raise PlatformError("PPTGen Platform returned an invalid run status")
        return result

    def get_run_detail(self, *, run_id: int) -> dict[str, Any]:
        result = self._request("GET", f"/api/runs/{run_id}")
        if not isinstance(result, dict) or result.get("id") != run_id:
            raise PlatformError("PPTGen Platform returned an invalid run detail")
        return result
