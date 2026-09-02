# Contributing

Thanks for helping improve Image PPTGen.

## Before opening a change

1. Keep the ordinary-user entry point inside the Codex Skill. Do not introduce a second browser-first product flow without an explicit product decision.
2. Preserve the separate stages: install, provide material, review or revise the split, confirm, generate, and open the static Preview/ZIP.
3. Keep platform-independent behavior in the Python CLI or shared services. Platform adapters should contain only the path, installation, permission, and process-lifecycle differences that cannot be shared.
4. Never commit credentials, user materials, generated private presentations, local databases, sessions, machine paths, or acceptance recordings.

## Development checks

Install frontend dependencies and build the UI:

```bash
cd frontend
npm ci
npm run build
```

Run the focused public tests from the repository root:

```bash
python3 -m pytest -q \
  packages/pptgen_toolkit/tests \
  tests/test_splitter_h2_boundary.py \
  tests/test_public_entrypoint.py \
  tests/test_public_image_3_0_surface.py \
  tests/test_image_release_packaging.py \
  tests/test_image_multiplatform_release.py
```

Tests and experiments must use disposable state. Do not point them at a personal `ppt.db`, an installed user's state directory, or real presentation material.

## Pull requests

- Explain the observable user outcome and what must remain unchanged.
- Include the exact checks you ran and what each one proves.
- Separate verified behavior from untested platforms or follow-up ideas.
- Keep changes scoped; do not mix unrelated refactors or dependency upgrades.
