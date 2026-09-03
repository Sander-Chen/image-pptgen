# Install Image PPTGen 3.0

Image PPTGen 3.0 is the fixed Image 3.0 workflow for text material. It keeps
the source review, faithful page split, explicit confirmation, generation, and
terminal follow in one Image-specific Skill and command namespace.

Supported targets are macOS ARM64 and Linux x86_64 only. Windows is not a
supported target.

## One-line installation

For Codex, say: `Install Image PPTGen from __DIST_BASE_URL__/install.sh`.

For Claude Code, say: `Install Image PPTGen from __DIST_BASE_URL__/install.sh`.

That user instruction is one sentence plus the address. Do not add a target
directory, Python path, environment variable, or setup steps to the user
prompt. Installation location and configuration remain installer and Skill
responsibilities.

Professional users can use the HTTPS fallback directly:

```bash
curl -fsSL __DIST_BASE_URL__/install.sh | bash -s -- __DIST_BASE_URL__
```

The installer uses only user-owned Image paths:

- command: `image-pptgen`
- data: `~/.local/share/image-pptgen`
- config: `~/.config/image-pptgen`
- Skill: `~/.agents/skills/generate-image-presentation`
- loopback service: `http://127.0.0.1:3130`

The installer verifies the manifest checksum, rejects unsafe archive paths and
link types, starts the managed local service, and never replaces the existing
HTML PPTGen command, service, data, config, or Skill namespaces.

## Start a new task

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Open a new Codex or Claude Code task, paste the source article, and invoke
`$generate-image-presentation`. Review every content page, request a material
revision when needed, explicitly confirm once, and let the Skill follow the
same Run to its terminal Preview and download result.

The staged journey remains: install, submit material, review or revise the
split, explicit confirmation, generate, static Preview, and ZIP.

## Advanced diagnostics

`image-pptgen-server` remains available as the advanced diagnostic and
compatibility entry point. The normal installation and task flow do not require
running it manually. To inspect the managed runtime, use:

```bash
image-pptgen doctor --json
```
