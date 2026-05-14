# Release checklist

How to cut a release of `aitap`. Two flavours:

- **Pre-release for dogfooding** (current state, repo private, no PyPI) — cheap, no external dependencies.
- **Public PyPI release** (when the project is ready to go public) — requires PyPI account setup; one-time gate flip after that.

---

## Pre-release for dogfooding

Use this when you want a stable version pinned for "install on another machine and try" — no PyPI, no public exposure.

1. **Bump version** in `pyproject.toml` (e.g. `0.1.0a1` → `0.1.0a2`).
2. **Update `CHANGELOG.md`** — promote the `[Unreleased]` section to a new dated heading; add a fresh empty `[Unreleased]` above it.
3. **Smoke-build locally:**

   ```bash
   uv build
   ls dist/                     # expect aitap-X.Y.Z-py3-none-any.whl + .tar.gz
   ```

4. **Commit + tag + push:**

   ```bash
   git commit -am "release: 0.1.0aN — <one-line summary>"
   git tag -a v0.1.0aN -m "aitap 0.1.0aN — <summary>"
   git push origin main
   git push origin v0.1.0aN
   ```

5. **Tag push triggers `Release` workflow** — the `build` job produces wheel + sdist artifacts that you can download from the workflow run page (and attach to a GitHub Release if you want).
6. **Install on another machine** (private repo — that machine needs GitHub auth):

   ```bash
   pipx install "aitap[all] @ git+https://github.com/Vikingboat666/AItap.git@v0.1.0aN"
   ```

The `publish` job is currently disabled (`if: false`) so it won't try to push to PyPI. **Don't remove that gate** until you're ready for a public release — see below.

---

## Public PyPI release

Switch from "private dogfood" to "anyone can `pip install aitap`". One-time setup, then any tagged release auto-publishes.

### Prerequisites (one-time)

1. **Decide repo is going public.** The wheel uploaded to PyPI exposes all source code. If the repo stays private but PyPI publishing is on, the code is effectively public anyway via the `.tar.gz` sdist.
2. **Claim the `aitap` name on PyPI** (someone else might grab it if you wait):

   ```bash
   uv build
   # Upload to TestPyPI first to verify the wheel works:
   uv publish --publish-url https://test.pypi.org/legacy/ \
              --token "<test-pypi-token>"
   pip install --index-url https://test.pypi.org/simple/ aitap   # smoke test
   # Real PyPI:
   uv publish --token "<pypi-token>"
   ```

3. **Configure PyPI Trusted Publishing** (so future releases don't need an API token):
   - Log in to PyPI → go to the `aitap` project page → **Settings** → **Publishing**.
   - Add a **GitHub** publisher with:
     - Owner: `Vikingboat666`
     - Repository: `AItap`
     - Workflow filename: `release.yml`
     - Environment name: `pypi`
   - Save.

4. **Verify the GitHub `pypi` environment** exists at:

   ```
   https://github.com/Vikingboat666/AItap/settings/environments
   ```

   It should already be referenced in `release.yml`. Add reviewers/protection rules if you want a manual approval gate before each PyPI publish.

### Flip the gate

Once setup above is complete, edit `.github/workflows/release.yml`:

```diff
   publish:
     name: Publish to PyPI
     needs: build
-    if: false  # ← flip to `true` (or remove this line) when ready to publish
+    # Enabled for public PyPI release — see docs/release-checklist.md.
     runs-on: ubuntu-latest
```

Commit, push, then push your next tag. The `publish` job runs and publishes automatically — no token in the workflow, no token committed anywhere; PyPI authenticates the run via OIDC tied to the repo + workflow + environment combination configured above.

### After the first public release

- Add a PyPI badge to README (`![PyPI](https://img.shields.io/pypi/v/aitap.svg)`).
- Announce wherever (Twitter / r/Python / HN).
- For subsequent releases, just bump version + tag + push — workflow does the rest.
