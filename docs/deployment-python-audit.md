# Deployment Python dependency inventory

Audit baseline: `bbcb256e60bc8a046fe2c1e9ba705d5d3debfe41` on
`rust-render-engine`, checked before the Goal 24 migration.

The normal installer, updater, administrator command, release smoke checks, CI
qualification report, and optional stability profile now run without Python.
Python remains part of development tests and an explicitly selected
compatibility path for release artifact operations.

| Classification | Current uses | Status |
| --- | --- | --- |
| Production install and update | `deploy/install.sh`, `deploy/update.sh`, `deploy/update-support.sh`, `deploy/build-rust-backend.sh`, and `deploy/lumistripe-admin` use Bash, `jq`, and the Rust `lumistripe-deploy-helper`. | The normal binary workflow does not install or invoke Python. The administrator status parser reads bounded JSON with `jq`. |
| Privileged release verification | The Rust helper packages and seals releases, validates manifests and checksums, verifies Sigstore provenance, downloads and extracts bounded USTAR archives, protects the verified cache, and stages releases. `deploy/release_artifact.sh` bootstraps the helper from a unique asset attached to the immutable commit release, checks its GitHub asset digest and Sigstore bundle against the pinned CI workflow and source commit, checks its AArch64 ELF type, and atomically installs a root-owned snapshot. | The default operation has no Python trust fallback. `LUMI_RELEASE_ARTIFACT_PYTHON_COMPAT=1` explicitly selects the older Python verifier for controlled compatibility recovery. Offline cached installs require an already trusted Rust helper. |
| CI and release automation | `.github/workflows/ci.yml` calls Bash scripts for release smoke checks and qualification, and the Rust stability driver for the optional soak profile. Release package and validation commands run inside the ARM64 Trixie image. CI publishes an independently attested deployment helper asset and bundle with the immutable release. | The release publisher no longer installs Python. The deployment test job installs Python for pytest only. Docker release-validation images contain Bash and inspection tools, not Python. |
| Development and tests | `deploy/tests/*.py` is the pytest regression suite. Lifecycle fixtures use Python for synthetic files, commits, timing, and Unix sockets; selected integration fixtures explicitly enable the legacy verifier to preserve older test scenarios. Rust unit tests exercise the new release verifier, including deterministic packaging, manifest checks, corrupt archives, path safety, and commit matching. | Python and uv are development-only requirements. Test fixture Python invocations are not installed on appliances. |
| Retained compatibility and historical references | `deploy/release_artifact.py` is the only retained operational Python file. `deploy/update-support.sh` copies it into the protected support snapshot for the explicitly selected recovery path. `LUMI_WEB_BACKEND=python` messages reject a retired web backend. | `release_smoke.py`, `release_qualification.py`, and `stability_profile.py` were removed after the Bash replacements and regression fixtures passed. Keep only the explicit verifier compatibility path until bootstrap has been exercised against a published release and appliance update. |

## Repository-wide observations

- No production systemd service or timer invokes Python.
- `deploy/install.sh` does not request `python3-minimal`; normal binary
  installation requires standard Bash utilities, `curl`, `jq`, `file`, and
  GitHub CLI for Sigstore verification.
- Release Docker stages exclude Python source from the build context and do
  not install an interpreter.
- The pytest job remains Python-based because it runs the deployment
  regression suite. The install lifecycle test image also includes Python for
  its fixture scripts only.
- Historical references to a Python backend, fixture tooling, and the explicit
  compatibility flag do not indicate a normal appliance interpreter
  requirement.

## Audit commands

The baseline was checked with `git status --short`, `rg --files` for Python
files, and repository-wide `rg` searches for interpreter invocations,
Python shebangs, `.py` deployment references, and release script names. The
deployment boundary was queried with `graphify query` before implementation;
the graph is refreshed after the source changes.

`deploy/tests/test_python_runtime_audit.py` keeps this boundary enforced. It
scans deployment shell scripts, Dockerfiles, service templates, and the CI
workflow for Python shebangs, interpreter commands, Python heredocs, and calls
to retired Python runtime scripts. The development allowlist is `deploy/tests/`;
the one retained deployment Python file is listed with its recovery purpose.
The only production command exception is the verifier behind
`LUMI_RELEASE_ARTIFACT_PYTHON_COMPAT=1`; the CI exception is the pytest job.
