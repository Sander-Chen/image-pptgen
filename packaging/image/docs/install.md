# Install Image PPTGen

Image PPTGen is the fixed Image PPT 3.0 workflow for text material. It keeps
the source review, faithful page split, explicit confirmation, generation, and
terminal follow in one Image-specific Skill and command namespace.

## One-line installation

For Codex, say: `Install Image PPTGen from __DIST_BASE_URL__/install.sh`.

For Claude Code, say: `Install Image PPTGen from __DIST_BASE_URL__/install.sh`.

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

## Advanced diagnostics

`image-pptgen-server` remains available as the advanced diagnostic and
compatibility entry point. The normal installation and task flow do not require
running it manually. To inspect the managed runtime, use:

```bash
image-pptgen doctor --json
```
