# Publication history safeguard

## Scope and provenance

`ABA_Wearable/fhir/verification` remains the authoritative FHIR source. This
repository adds only Git append-only enforcement and retained-byte validation.
`scripts/history_guard.py` follows the historical integrity contract reviewed in
ABA_Wearable commit `9be5181fddc1aa5ac56081d22e12301340ffda34`, specifically
`scripts/history.mjs` and `readArchive` in `scripts/validation.mjs`. It neither
builds FHIR content nor duplicates candidate-policy validation. Do not add current
terminology dependencies, example registries or scaffold resource limits here.
Changes to the shared historical contract must be reviewed against that source;
this small hosting adapter is not an independent FHIR implementation.

The checker reads immutable Git blobs at explicit base/head commit SHAs, not the
working directory, an inferred merge base or a moving branch. Both snapshots
must have consistent indexes, manifests, hashes, package/IG identities, identical
stored/packaged IG bytes and required render targets. Every existing entry field
is compared structurally, including additional metadata; JSON object ordering
and whitespace are irrelevant. Existing retained files (including manifests and
.gitattributes) must remain byte-identical. Index additions and new releases are
allowed; adding files to an old release is forbidden. Changes outside history
are ignored. Duplicate JSON keys are rejected. Archives are parsed in memory,
never extracted: duplicate names, unsafe paths, links, extended headers, bad
checksums, truncation and trailing data fail. Git links, executable blobs and
submodules inside history fail. Limits are 64 MiB total history per snapshot and
16 MiB decompressed per archive; exceeding them requires a reviewed limit update.

## Run locally

Python 3.11+ and Git are sufficient; no dependencies, credentials or FHIR build.
From this repository, using full 40-character commits that exist locally:

```sh
python -B -m unittest discover -s tests -v
python -B scripts/history_guard.py --base <exact-base-sha> --head <exact-proposed-sha>
```

The checker intentionally ignores uncommitted history edits. For this uncommitted
implementation, fixture tests exercise mutations, and the real-history check uses
`f467c88ef5d122fdaaf6e66417bd8d213f4edc7e` as both arguments. Once Gabriela commits
proposed history, use that exact commit for `--head`.

## PR workflow and trust boundary

Stable job/check name: **Publication history append-only**.
Workflow: `.github/workflows/history-validation.yml`.

Runs on opened, synchronize, reopened and edited PR events targeting main without
path filters. The edited event covers retargeting a PR to main. Uses the event's exact
`pull_request.base.sha` and `pull_request.head.sha`. Checkout is full-depth; the
base SHA is fetched and verified explicitly. Checkout credentials are not
persisted. Contents read is the only permission. There are no secret references,
environments, Pages permissions, deployments or `pull_request_target`. The
checkout action uses the same reviewed SHA as the publication workflows. Python
comes from ubuntu-24.04. Fork PR validation needs no private-source access.

Inspected effective main rules on this pass: no merge_queue rule. No merge_group
trigger is added. If a merge queue is later enabled, first add/test the
merge_group checks_requested event and compare its exact base_sha/head_sha.

PR authors can change this checker, tests and workflow. Running PR code with
read-only permissions protects credentials; it does NOT make the enforcement
implementation immutable. Before approval, independent maintainers must inspect
changes to these paths, this documentation, .github/CODEOWNERS and ruleset policy.
The `.github/CODEOWNERS` file and required code-owner review are mandatory
rollout requirements, not optional recommendations. The proposed file assigns
`@beltt-gabriela` and `@23fanuz` to workflows, scripts, tests, this document,
CODEOWNERS itself, all publication history and root .gitattributes. Both accounts
were confirmed as repository admins through the collaborators permission API on
October 11, 2026. GitHub requires one eligible code owner's approval, not both;
the author cannot approve their own PR. CODEOWNERS enforcement uses the base
branch's file, so the initial installation itself needs explicit trusted review.

Required code-owner review, approval of the most recent reviewable push and
stale-approval dismissal must be enabled together. Workflow changes still require
trusted human review. Executing a validator taken from the trusted base commit
could be additional later hardening; it is NOT implemented in this pass. A
centrally managed required workflow is another possible stronger boundary.
No repository settings are changed by this implementation task.

## Owner rollout: ruleset 24856755

1. Review these uncommitted files, commit and push this branch, and open a PR into
   main. Confirm the PR's **Publication history append-only** job actually runs
   and succeeds, including its tests and exact-SHA comparison.
2. Obtain independent approval and merge the safeguard and CODEOWNERS together.
   Until the workflow is configured as required and code-owner review is enabled,
   it is not enforced. Freeze publication during rollout.
3. In Speiro-Inc/fhir-verification: Settings -> Rules -> Rulesets -> **Protect
   main** (ID 24856755), enable **Require status checks to pass**. Add the emitted
   **Publication history append-only** check, sourced from **GitHub Actions**.
   Enable **Require branches to be up to date before merging**. Save only after
   the check exists and has reported successfully; never invent a missing check.
4. Under required pull requests, keep **at least one approving review**, enable
   **Require review from Code Owners** and **Require approval of the most recent
   reviewable push**, and keep **Dismiss stale pull request approvals when new
   commits are pushed** enabled. Keep active enforcement, deletion protection,
   force-push protection, all other existing rules and the empty bypass list.
5. Read back `gh api repos/Speiro-Inc/fhir-verification/rulesets/24856755` and
   `gh api repos/Speiro-Inc/fhir-verification/rules/branches/main`. Verify:
   - required_status_checks context: `Publication history append-only`, with the
     actual GitHub Actions integration ID;
   - strict_required_status_checks_policy: true;
   - required_approving_review_count: at least 1;
   - require_code_owner_review: true;
   - require_last_push_approval: true;
   - dismiss_stale_reviews_on_push: true;
   - deletion and non_fast_forward rules remain present; bypass_actors: [].
6. Confirm a subsequent PR waits for the check and an eligible independent code
   owner. Push an update to confirm stale approval is dismissed and the most
   recent reviewable push needs approval from someone other than its pusher.
   Do not weaken protection to bypass a red result.

Live ruleset read on October 11, 2026 (not modified in this pass): active,
main-only, one required approval, dismiss_stale_reviews_on_push=true,
require_code_owner_review=false, require_last_push_approval=false, no required
status-check rule (therefore no strict status enforcement), deletion and
non_fast_forward rules present, bypass_actors=[]. Re-read live settings during
rollout; do not overwrite them from this snapshot or older review notes.

## Acceptance remains separate

Retained 0.1.0 package SHA-256:
`0502e5927b1542d320b50b66107f50c3cf1fa840aaf5c4dcdcf73fc8ab39083a`.
Do not rebuild, replace or bootstrap over it. Publication still needs independent
source-read/deployment approvals and a hosting-main write freeze through final
freshness validation and deployment verification. Concurrency does not lock Git
writes. The check also does not authorize new clinical definitions or release
publication. Review release content separately using the authoritative source.

After review, merge and enforcement, documented owner acceptance is still
required. FMT-1201, P04 and FMT-1210 remain open. Gabriela must confirm the existing
source token's selected-repository scope and expiry without sharing its value.
