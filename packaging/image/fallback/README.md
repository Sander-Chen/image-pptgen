# Frozen fallback inputs

`fallback-lock.json` is the only authority for the optional bundled Python fallback. It pins the immutable upstream release, both platform runtimes, all direct and transitive wheels, supplemental licensing material, deterministic bundle hashes, and the first-download budget.

The fallback is not a user-selectable default. Each Codex Desktop platform must first run at most two approach-different probes against the task's official Python Runtime. Only two recorded failures authorize automatic use of the matching frozen fallback.

The full `tar.zst` archives are build-only provenance inputs. Users receive the smaller `install_only_stripped` runtime, the deterministic wheelhouse ZIP, and the deterministic license ZIP. Installation must be offline (`--no-index`), user-scoped, and hash checked.

Build and verify a platform bundle from previously downloaded inputs:

```text
python packaging/image/fallback/build_frozen_assets.py \
  --platform macos-arm64 \
  --source-dir /path/to/runtime-assets \
  --wheel-dir /path/to/platform-wheels \
  --output-dir /path/to/output
```

The command fails closed if any input or output differs from the lock.
