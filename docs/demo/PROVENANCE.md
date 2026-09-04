# Public Demo Provenance

This directory holds only screened images for the public homepage. It is not a
full acceptance-evidence tree. It contains no user data, session content,
credentials, or private machine paths.

## Frog life cycle Image 3.0 demo

| Field | Value |
| --- | --- |
| Theme | Frog life cycle |
| Public overlay | `Sander-Chen/image-pptgen` |
| Generation route | `public_image_3_0` / `codex_native_image` |
| Candidate version | `0.0.0-r63-7c19e1f3` |
| Source commit | `7c19e1f380602537195ba929d3985e4841f92bb7` |
| Linux archive SHA-256 | `7a09a1498074ffb5ed88864efac79a24a9d8849e02d245547546ac4f93cceab5` |
| Linux build ID | `aa3b23e75d83b3a181f21d1c5922cc306d908234cc9cdccf90dcca3eaddea73b` |
| macOS archive SHA-256 | `377342dda305fe489a2eb5e33fcbe3da2d5cbbb7db279310414bc7036233f5b6` |
| macOS build ID | `5416681f4df6a8cbd2b5179470e55381d3e11a7008f59c1e08cde099b12097d8` |
| Material | `eval-materials/frog-life.md` |
| Material SHA-256 | `18cf6d21d835d33b48eae612550626d7dfdbcfee13a0dae28507c8220dbb3a63` |
| Demo Run ID | `1` |
| Page count | 5 PNG pages (1 fixed cover + 4 content pages), all successful |
| Preview / ZIP | Completed Run static Preview and ZIP retained |
| Safety | No credentials, private paths, user data, or Codex home are published. Only generated slide pixels were copied. |

The Demo Run was produced by the accepted Linux installation of this candidate
through the shipped `generate-image-presentation` Skill dispatcher. The same
immutable candidate passed native Linux (#35) and macOS (#36) acceptance.

### Public image list

| Public file | Original page | Original PNG SHA-256 | Public WebP SHA-256 |
| --- | --- | --- | --- |
| `english/frog-life/cover.webp` | `page-001.png` (cover) | `bb561ab7fe34c221dfeec38516f33afc132051146198ab8d75e690ac5088621f` | `1d8c9a33b31ff041f8a918284201b8b6b7c167c8d5cc15fbb0f1ab95866285ca` |
| `english/frog-life/middle.webp` | `page-004.png` (representative interior) | `dbf77a5db6ac28d83ea67b27036fdd03263c9e83ea549b8a6a0c3af6170dae13` | `184e8e7b0d765e8666e6a83369e6fd9b852e1176ed19506ab96eedf984f44e84` |
| `english/frog-life/final.webp` | `page-005.png` (final) | `422da0916ab6456677256392abee0ef01a880d2ff397297de00d6bbc5e577531` | `945c09a0537b7f28d8be7199f7aea2bb83850cd4c7a40dbb43203681d3a4ee16` |

Public images are proportional 1672×941 PNG scales to 1200×675 WebP, used only
to reduce GitHub page weight. Pixels were not retouched, regenerated, or
rewritten.

`page-003.png` from the same Run is not published. Visual inspection found
unintended Chinese copy on that interior slide.

## Rejected sources

- HTML-route screenshots from `ppt-gen-platform`: not Image 3.0 output.
- UI mockups whose filenames mention `gpt-image-2`: not presentation output.
- Desktop or VM acceptance screenshots: they contain operator chrome and
  session UI.
- The previous Chinese-history Demo from candidate `0.1.1` / `47bbb1e4`.
- Any Run that did not come from this accepted candidate.
