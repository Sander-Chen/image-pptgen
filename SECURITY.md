# Security Policy

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting for this repository when available. Do not include credentials, private presentation material, local database contents, session transcripts, or machine paths in a public Issue.

If private reporting is unavailable, open a minimal public Issue that asks the maintainer to establish a private contact channel. Do not publish exploit details or sensitive evidence in that Issue.

## Supported release

Security fixes currently target the latest published Image PPTGen release. The verified R62 delivery platforms are macOS ARM64 and Linux x86_64. The presence of Windows adapter source does not mean Windows has completed release acceptance.

## Local-data boundary

Image PPTGen stores generated artifacts and runtime state locally. Reports should use a disposable reproduction and must not attach a user's real state directory or generated presentation unless the user has explicitly approved its disclosure.
