# Source Provenance

This repository is a clean public source snapshot for Image PPTGen R62. It is not a mirror of the private engineering repository or its Git history.

## Release identity

| Field | Value |
| --- | --- |
| Product | Image PPTGen |
| Release | `0.0.0-r62-0bf9599a` |
| Accepted integration source | `0bf9599a12fd9bff87e1996595f3370cf8718a2e` |
| Public repository base | `f4d3c2d93b2baa017107721a17dfa22be8c46c53` |
| Source whitelist | 222 files / 4,350,365 bytes |
| Whitelist SHA-256 | `e92cd08dd2263c25352feafd86ee4dcec816f27f6afd1fc0ab93b27af60615b7` |
| Linux release archive SHA-256 | `26ca9f54040fac45a9cf241717951a8763085a16008d9caaa77b41ea7ab0472f` |
| Public installer SHA-256 | `2da8553d3d1a7cc2958691d839e4887b1153fe6932ceb2ef2e2a5209f2563b93` |

Every imported product-source file is listed in `PUBLIC_SOURCE_MANIFEST.tsv` with its source path, Git mode, Git blob ID, and public target path. The manifest is generated from the accepted integration source and is the review boundary for this snapshot. It includes `tests/fixtures/explicit-h1-thirteen-pages.md`, which is required by the public split-pagination regression tests.

## Included

- Image PPTGen backend and public runtime entry points;
- frontend source, build metadata, and focused tests;
- `pptgen_toolkit` CLI and static Preview source;
- Image packaging and platform adapter source;
- the `generate-image-presentation` Codex Skill;
- runtime prompt inputs required by the released product;
- focused Image, split, installer, runtime, and public-surface tests;
- public README, Demo assets, and the short installer-site configuration.

## Deliberately excluded

- private Git history and private repository identity;
- internal workflow, planning, calibration, and acceptance documents;
- evidence archives, recordings, sessions, logs, generated output, and databases;
- user or business data, credentials, machine paths, and local configuration;
- `frontend/tests/artifactUrls.test.ts`, because its fixture contained a real historical internal-worktree path;
- `tests/skill_eval/test_generate_image_presentation_skill_contract.py`, because it depended on a host-specific Skill path outside this repository;
- unlisted standalone prototypes, experiments, top-level mockup assets, and all other unlisted files.

## Supported-platform boundary

macOS ARM64 and Linux x86_64 are the verified delivery platforms for R62. Windows adapter source may be present for continued development, but Windows is not an accepted or supported-platform claim for this release.

The short installer URL is an immutable mapping to the accepted R62 installer. Publishing this source snapshot does not rebuild or mutate the released archives.

## Public additions

The following files are authored specifically for the public repository and therefore do not appear in the R62 source whitelist:

- `README.md`
- `SOURCE_PROVENANCE.md`
- `PUBLIC_SOURCE_MANIFEST.tsv`
- `CONTRIBUTING.md`
- `SECURITY.md`
- `docs/demo/PROVENANCE.md`
- `docs/demo/image-3-history/cover.webp`
- `docs/demo/image-3-history/middle-history.webp`
- `docs/demo/image-3-history/modern-china.webp`
- `deploy/installer-site/index.html`
- `deploy/installer-site/_headers`
- `deploy/installer-site/_redirects`

The existing root `LICENSE` comes from the initial public-repository commit and applies Apache License 2.0 to this repository.
