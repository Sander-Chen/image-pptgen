# Image PPTGen

<p align="right">
  <strong>English</strong> · <a href="README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <strong>Turn source material into a visually consistent, presentation-ready image deck.</strong><br>
  <sub>Image-first presentations, orchestrated by Codex.</sub>
</p>

<p align="center">
  <img src="docs/demo/image-3-history/cover.webp" alt="Image PPTGen Chinese history presentation cover" width="100%">
</p>

## Start in 30 seconds

Send this one sentence to Codex:

> Install this Skill: https://image-pptgen.pages.dev/install.sh

Once installation finishes, give Codex your source material and ask it to create a presentation. Image PPTGen will show you a page plan first. You can revise it, and generation starts only after you confirm it.

> [!IMPORTANT]
> The installation request does not need a target directory, Python path, environment variable, or follow-up instructions. The installer detects the platform and configures the local environment.

## How it works

```text
One-sentence installation
    ↓
Provide text or Markdown
    ↓
Review and revise the page plan
    ↓
Explicitly confirm
    ↓
Generate each page
    ↓
Open the static Preview · Present full-screen · Download the ZIP
```

Page planning and image generation are separate stages. Nothing is generated before you approve the plan, and changing the page count does not require resubmitting the source material.

## Demo: A journey through Chinese history

These three images come from the same Image 3.0 presentation. The material was reviewed and confirmed as a five-page plan before generation; the cover, a middle page, and the closing page are shown here.

| Historical transition | Modern China |
| --- | --- |
| ![Chinese history presentation: prosperity and transition](docs/demo/image-3-history/middle-history.webp) | ![Chinese history presentation: modern China](docs/demo/image-3-history/modern-china.webp) |

The Demo was generated through the `public_image_3_0` / `codex_native_image` route. Images were resized proportionally for GitHub and were not regenerated or visually rewritten. See [Demo provenance](docs/demo/PROVENANCE.md) for the complete source record and hashes.

## Good fits

- Turn an article, research note, or report into a visual presentation;
- Review pagination before spending image-generation capacity;
- Keep the deck visually consistent without maintaining a template by hand;
- Deliver a static Preview and ZIP that do not depend on a long-running background service.

## Supported platforms

| Platform | Architecture | Status |
| --- | --- | --- |
| macOS | Apple Silicon / ARM64 | Real installation and end-to-end acceptance completed |
| Linux | x86_64 | Real installation and end-to-end acceptance completed |
| Windows | x86_64 | Package validation is still in progress; not a current support commitment |

The stable release is pinned to `0.0.0-r62-0bf9599a`. The short installation URL maps only to this accepted release and will not silently switch to an unverified package.

## What you get

- A static Preview with page navigation, zoom, and full-screen viewing;
- High-resolution PNG files in presentation order;
- One ZIP containing the complete deck;
- Traceable page-planning, confirmation, and generation states.

## Frequently asked questions

<details>
<summary><strong>Where is it installed?</strong></summary>

The installer selects user-level directories for the current platform and does not require administrator privileges. Commands, the Skill, the runtime, and state data are kept separate. Upgrades replace versioned program files without overwriting generated content.

</details>

<details>
<summary><strong>Why must I confirm the page plan first?</strong></summary>

Every presentation page is generated independently. Reviewing the structure first lets you correct the page count, titles, and material boundaries before consuming generation resources, avoiding a full rerun after a bad split.

</details>

<details>
<summary><strong>Why is Preview static?</strong></summary>

The images, page data, and ZIP are prepared when generation completes. Preview does not depend on a local service staying alive, which makes it more reliable and easier to archive or move across macOS and Linux.

</details>

<details>
<summary><strong>Can I run the shell installer directly?</strong></summary>

The primary entry point is the Codex Skill. Advanced users may inspect and run the installer themselves, but ordinary users do not need to copy a shell pipeline.

</details>

## Architecture and source map

For a visual explanation of Cloudflare distribution, cross-platform installation, the local CLI, Skill, state directories, and static Preview, see the [Image PPTGen architecture guide](https://image-pptgen-architecture.pages.dev/).

<details>
<summary><strong>Where should I start reading the source?</strong></summary>

| Path | Responsibility |
| --- | --- |
| `skills/generate-image-presentation/` | Codex Skill: governs installation, page-plan confirmation, and generation flow |
| `packages/pptgen_toolkit/` | Cross-platform CLI, client, and static Preview bundling |
| `backend/` | Page planning, generation, state, audit, and artifact services |
| `frontend/` | Preview and local review interface source |
| `packaging/image/` | Linux and macOS adapters, plus experimental Windows packaging |
| `deploy/installer-site/` | Static Cloudflare Pages configuration for the short installation URL |

This is a clean public source snapshot derived from the accepted R62 release. It does not contain private Git history, internal workflow records, acceptance archives, databases, or user data. See [SOURCE_PROVENANCE.md](SOURCE_PROVENANCE.md) and [PUBLIC_SOURCE_MANIFEST.tsv](PUBLIC_SOURCE_MANIFEST.tsv) for the complete file-level provenance.

</details>

Read [CONTRIBUTING.md](CONTRIBUTING.md) before contributing. Report security concerns through [SECURITY.md](SECURITY.md), and do not disclose credentials or private source material in a public Issue.

---

<p align="center"><sub>The public release focuses on the reliable Codex Skill workflow; a browser Web app is not the end-user entry point. Licensed under Apache-2.0.</sub></p>
