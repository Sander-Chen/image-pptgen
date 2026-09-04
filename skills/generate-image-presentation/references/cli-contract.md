# Image PPTGen CLI contract

Contract version: `0.1.0`

The Image CLI is a thin HTTP client. It has no workflow database and does not
import the backend. The default base URL is `http://127.0.0.1:3130`; tests and
isolated runtimes may override it with `--base-url`.

## Commands

- `image-pptgen doctor --json`
- `image-pptgen material submit --title <title> --text-file <path> --json`
- `image-pptgen split propose --deck-id <id> --json`
- `image-pptgen split revise --draft-id <id> --instruction <text> --json`
- `image-pptgen split revise --draft-id <id> --target-page-count <n> --json`
- `image-pptgen split confirm --draft-id <id> --json`
- `image-pptgen generate --deck-id <id> --json`
- `image-pptgen status --run-id <id> --follow --jsonl`
- `image-pptgen result --run-id <id> --json`
- `image-pptgen result --run-id <id> --static-preview-file <path> --json`

There are no intent, preference, model, provider, renderer, config,
Requirement, Color, prompt, or split-mode arguments. The public server selects
the exact `Codex Native Image 3.0 Luna Low Director` config and faithful split
execution.

## HTTP routes

- `GET /api/runtime-identity`
- `POST /api/decks`
- `GET /api/configs`
- `POST /api/decks/<id>/split-drafts` with `{}`
- `POST /api/deck-split-drafts/<id>/revise` with exactly one of `instruction` or
  `target_page_count`
- `POST /api/deck-split-drafts/<id>/confirm`
- `POST /api/generate` with exactly these six fields:

  ```json
  {
    "deck_id": 1,
    "config_id": 2,
    "engine": "image",
    "strategy": "image_3_0",
    "requirement_ids": [],
    "color_ids": []
  }
  ```

- `GET /api/runs/<id>/status`
- `GET /api/runs/<id>`
- `GET /api/runs/<id>/download`
- Preview: `/history/run/<id>/preview`

## Output and exits

Commands return one JSON object unless marked JSONL. Proposal and revision
return the complete Markdown projection, `draft_id`, `deck_id`, faithful mode,
page count, and status. A pure target-page revision uses the same pending draft,
does not call a model, and returns a typed `target_page_count_unavailable`
error without changing the draft when the requested count cannot be reached.
`instruction` and `target_page_count` are mutually exclusive. Confirmation
returns final slide IDs exactly once.
Generation returns one `batch_id` and one retained `run_id`. Status JSONL
contains grounded `task_progress`, `current_activity`, `source_facts`, and
follow elapsed time. Result returns the existing Run status, image artifact
projection, Preview URL, and Run download URL; missing or failed pages are
never projected as successful. A completed result reads only that
Run's completed `/artifacts/` PNG routes while the local runtime is available
and writes a per-Run offline bundle under the existing artifact root. The bundle
contains ordered PNGs, `index.html`, `manifest.json`, and a matching sibling
ZIP; the viewer also has its own byte-identical ZIP copy so its download link
works from `file:`. The manifest records Run ID, page order, PNG hashes/sizes,
and ZIP hash/size. On macOS and Linux the `preview_url` and
`download_url` are the resulting `file:` viewer and ZIP URLs; they do not need
network, the backend, or port 3130 after `result` exits. In-progress Runs may
still return loopback Preview and Run download URLs.

`--static-preview-file` remains an R58-compatible optional command argument on
macOS and Linux. It writes its legacy standalone embedded-image page atomically
and returns `static_preview_path` plus `static_preview_url`; it does not create
a Run or keep the runtime alive. It is not the completed-Run interactive bundle
handoff.

Exit codes:

- `0`: success
- `2`: local input or command-contract error
- `3`: platform unavailable
- `4`: platform/API response error

Errors are one JSON object on stderr with stable `error` and `message` keys.
Unknown mutation outcomes are never automatically retried. Image material is
rejected before any platform or model request.
