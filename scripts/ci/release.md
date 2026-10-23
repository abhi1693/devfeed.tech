# Release workflow

Pull requests and pushes to `master` run the full `.github/workflows/ci.yml` pipeline:
backend and frontend unit tests, integration tests, browsers, security and CodeQL,
analysis, and native ARM64 image checks. `CI required` keeps all existing prerequisites.
Version tags no longer start that pipeline again.

A `vMAJOR.MINOR.PATCH` tag (including a valid SemVer prerelease suffix) starts `.github/workflows/release.yml`. It runs only:

1. **Verify release source against master CI.** Check the tag, application version,
   checkout and commit identity, then reuse the exact commit's trusted master proof.
2. **ARM64 release images.** Build the release image set through the pinned reusable
   container workflow, retaining native runtime smoke tests, vulnerability scans,
   SBOMs, provenance, digest checks and immutable version-tag promotion.
3. **Release required.** Require both source verification and successful publication.

There is no unit, integration, browser, Sonar or CodeQL rerun on the tag. Image smoke
checks and scans validate the actual release artifacts and remain release work.
The twelve image definitions remain in `.github/images.json`; the container wrapper,
its pinned reusable implementation, and `latest: false` remain unchanged.

## Source proof

The read-only preflight verifies the actual tag push, a canonical `vX.Y.Z[-PRERELEASE]` tag matching
`pyproject.toml`, the checked-out commit, the remote dereferenced tag and membership in
`master` history. Fork repositories cannot enter the upstream publication path.
Both lightweight and annotated tags are supported. The tag is checked again after
waiting for CI so a moved reference cannot use another commit's proof.

The proof must come from the active canonical `ci.yml` workflow, for the same
repository and exact SHA, triggered by a **push to master**. PR, dispatch, tag,
foreign-repository and other-workflow runs cannot satisfy it. Only the newest matching
run is considered. Its current attempt must be complete and successful, with exactly
one successful `CI required` job and each of the Python, Actions and JavaScript /
TypeScript CodeQL jobs. Their source SHA and run ID must match. API pagination covers
all jobs, including jobs beyond the first page; previous attempts are not combined.
Master's `CI required` gate remains responsible for every test, DAST, recovery,
analysis and image prerequisite.

If that master run is still pending, preflight waits for at most one hour. A failed,
cancelled or incomplete required proof blocks publication; it does not substitute
another SHA or a previous green attempt. Master CI retains branch concurrency, so a
newer push can cancel an older in-flight master run. Tag a completed green commit or
rerun that exact commit's master CI when this occurs. Rerun the failed release after
its source checks succeed; do not move an existing release tag.

## Browser extensions and historical tags

The separate extension release workflow now waits for the exact tag's successful
`release.yml` attempt and its `Release required` gate. Its packaging, signed assets
and store submission steps remain separate from image publication. Manual extension
packaging resolves inputs explicitly under `refs/tags/`, avoiding a same-named branch.

Historical tags keep their checked-in workflows and helpers, including their original
tag-CI proof. The new helper does not fall back to old tag CI when a new release gate
is missing or failing. This change applies only to tags containing the new workflows;
it does not change or rerun existing releases.

Run the focused contracts with:

```sh
uv run pytest tests/tooling/ci/test_release_ci.py tests/tooling/ci/test_ci_gates.py tests/tooling/ci/test_ci_dast.py
```
