"""HTTP client for the fixed public Image PPT 3.0 surface.

The image client intentionally lives beside, rather than inside, the existing
HTML client.  It shares only the transport and error types; route policy and
payload construction stay image-specific so a caller cannot accidentally
select an HTML intent, requirement, color, model, or provider.
"""

from __future__ import annotations

import os
from typing import Any

from .client import PlatformError, PptgenClient


IMAGE_ROUTE = "image_3_0"
LUNA_CONFIG_NAME = "Codex Native Image 3.0 Luna Low Director"
TERRA_E2E_CONFIG_NAME = "Codex Native Image 3.0 Terra Low E2E"
TERRA_E2E_ENV = "IMAGE_PPTGEN_E2E_TERRA_LOW"
IMAGE_PRODUCT = "image-pptgen"
IMAGE_SERVICE = "image-pptgen-server"
IMAGE_SURFACE = "public_image_3_0"
IMAGE_DATA_ROOT = "image-pptgen/state/data"
IMAGE_ARTIFACTS_ROOT = "image-pptgen/state/data/artifacts"
IMAGE_RUNTIME_IDENTITY_FIELDS = (
    "artifacts_root",
    "base_url",
    "build_id",
    "data_root",
    "instance_id",
    "product",
    "service",
    "skill_sha256",
    "source_commit",
    "surface",
    "runtime_content_sha256",
    "version",
)


def _require_dict(result: Any, message: str) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise PlatformError(message)
    return result


def _require_split_draft(result: Any) -> dict[str, Any]:
    payload = _require_dict(result, "PPTGen Platform returned an invalid split draft")
    if not isinstance(payload.get("id"), int) or not isinstance(
        payload.get("slides"), list
    ):
        raise PlatformError("PPTGen Platform returned an invalid split draft")
    return payload


class ImagePptgenClient(PptgenClient):
    """Client for the Image 3.0 public API.

    The inherited ``_request`` method is transport-only and does not choose a
    route.  Every mutating method below constructs its complete, fixed public
    payload explicitly.
    """

    def health(self) -> dict[str, Any]:
        identity = _require_dict(
            self._request("GET", "/api/runtime-identity"),
            "PPTGen Platform returned an invalid Image runtime identity",
        )
        missing = [
            field
            for field in IMAGE_RUNTIME_IDENTITY_FIELDS
            if not isinstance(identity.get(field), str) or not identity[field].strip()
        ]
        if missing:
            raise PlatformError(
                "PPTGen Platform Image runtime identity is missing: " + ", ".join(missing)
            )
        expected = {
            "artifacts_root": IMAGE_ARTIFACTS_ROOT,
            "data_root": IMAGE_DATA_ROOT,
            "product": IMAGE_PRODUCT,
            "service": IMAGE_SERVICE,
            "surface": IMAGE_SURFACE,
        }
        for field, value in expected.items():
            if identity[field] != value:
                raise PlatformError(f"PPTGen Platform Image {field.replace('_', ' ')} mismatch")
        return {**identity, "ok": True}

    def create_split_draft(self, *, deck_id: int) -> dict[str, Any]:
        # The public endpoint owns mode/model/profile/content mode.  An empty
        # object is deliberate: no caller-supplied split override is accepted.
        result = self._request(
            "POST",
            f"/api/decks/{deck_id}/split-drafts",
            {},
            timeout=self.long_timeout,
        )
        return _require_split_draft(result)

    def revise_split_draft(
        self,
        *,
        draft_id: int,
        instruction: str | None = None,
        target_page_count: int | None = None,
    ) -> dict[str, Any]:
        if instruction is not None and target_page_count is not None:
            raise PlatformError(
                "Image split revision accepts instruction or target_page_count, not both"
            )
        if instruction is None and target_page_count is None:
            raise PlatformError(
                "Image split revision requires instruction or target_page_count"
            )
        if instruction is not None:
            if not isinstance(instruction, str) or not instruction.strip():
                raise PlatformError(
                    "Image split revision requires a non-empty instruction"
                )
            payload = {"instruction": instruction}
        else:
            if type(target_page_count) is not int:
                raise PlatformError(
                    "Image split revision target_page_count must be an integer"
                )
            payload = {"target_page_count": target_page_count}
        result = self._request(
            "POST",
            f"/api/deck-split-drafts/{draft_id}/revise",
            payload,
            timeout=self.long_timeout,
        )
        return _require_split_draft(result)

    def confirm_split_draft(self, *, draft_id: int) -> dict[str, Any]:
        result = _require_dict(
            self._request("POST", f"/api/deck-split-drafts/{draft_id}/confirm"),
            "PPTGen Platform returned an invalid confirmation",
        )
        slide_ids = result.get("slide_ids")
        if (
            not isinstance(slide_ids, list)
            or not slide_ids
            or not all(type(slide_id) is int and slide_id > 0 for slide_id in slide_ids)
        ):
            raise PlatformError("PPTGen Platform returned an invalid confirmation")
        deck_id = result.get("deck_id")
        if type(deck_id) is not int or deck_id <= 0:
            slides = result.get("slides")
            if isinstance(slides, list) and slides and isinstance(slides[0], dict):
                deck_id = slides[0].get("deck_id")
        if type(deck_id) is not int or deck_id <= 0:
            raise PlatformError("PPTGen Platform returned an invalid confirmation")
        return {
            "deck_id": deck_id,
            "draft_id": draft_id,
            "slide_count": len(slide_ids),
            "slide_ids": slide_ids,
            "status": "confirmed",
        }

    def _luna_config(self) -> dict[str, Any]:
        terra_e2e = os.environ.get(TERRA_E2E_ENV) == "1"
        config_name = TERRA_E2E_CONFIG_NAME if terra_e2e else LUNA_CONFIG_NAME
        model = "gpt-5.6-terra" if terra_e2e else "gpt-5.6-luna"
        configs = self._request("GET", "/api/configs")
        if not isinstance(configs, list):
            raise PlatformError("PPTGen Platform returned an invalid Image config list")
        matches = [
            item
            for item in configs
            if isinstance(item, dict) and item.get("name") == config_name
        ]
        if len(matches) != 1 or type(matches[0].get("id")) is not int:
            raise PlatformError(
                f"PPTGen Platform has no unique {config_name} config"
            )
        config = matches[0]
        expected_director = {"model": model, "reasoning_effort": "low"}
        if (
            config.get("type") != "image"
            or config.get("route") != IMAGE_ROUTE
            or config.get("director") != expected_director
            or config.get("renderer") != expected_director
            or config.get("palette") != expected_director
        ):
            raise PlatformError("PPTGen Platform returned an invalid Image config")
        return config

    def start_generation(self, *, deck_id: int) -> dict[str, Any]:
        config = self._luna_config()
        # This is the complete public contract.  Keep the key set explicit so
        # future shared-client changes cannot add HTML fields to Image runs.
        payload = {
            "deck_id": deck_id,
            "config_id": config["id"],
            "engine": "image",
            "strategy": IMAGE_ROUTE,
            "requirement_ids": [],
            "color_ids": [],
        }
        result = _require_dict(
            self._request("POST", "/api/generate", payload, timeout=self.long_timeout),
            "PPTGen Platform returned an invalid Image generation",
        )
        batch_id = result.get("batch_id")
        run_ids = result.get("run_ids")
        if (
            type(batch_id) is not int
            or batch_id <= 0
            or not isinstance(run_ids, list)
            or not run_ids
            or len(run_ids) != 1
            or not all(type(run_id) is int and run_id > 0 for run_id in run_ids)
        ):
            raise PlatformError("PPTGen Platform returned an invalid Image generation")
        return {
            "batch_id": batch_id,
            "config_name": (
                TERRA_E2E_CONFIG_NAME
                if os.environ.get(TERRA_E2E_ENV) == "1"
                else LUNA_CONFIG_NAME
            ),
            "deck_id": deck_id,
            "run_ids": run_ids,
            "status": "generation_started",
        }
