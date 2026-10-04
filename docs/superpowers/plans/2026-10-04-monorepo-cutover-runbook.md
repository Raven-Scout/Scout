# Monorepo cutover runbook

> **This supersedes the commands in Task 1 and Task 14 of
> [`2026-09-03-monorepo-consolidation.md`](2026-09-03-monorepo-consolidation.md).**
> That plan stays as the historical record. Do not run its Task 1 / Task 14
> commands: they install `scout@Scout` (the `@` suffix is the marketplace
> NAME, which stays `scout-plugin`), run `versioning set 0.10.0` (a downgrade:
> nothing guards against setting a lower version), cite versions and a worktree
> path that no longer exist, and run `claude plugin marketplace remove
> scout-plugin` against the real config with no isolation (that uninstalls the
> plugin Jordan's scheduled runs use).

Everything below is a checklist. Steps marked **[outward]** post, push, publish,
or change GitHub state on Jordan's behalf. They are Jordan's to run (rulings R1
and R2). Everything else is local.

**Conventions**

- Run the blocks in **bash** (`bash` first if your shell is zsh, where unquoted
  `$var` doesn't word-split).
- `W` is a checkout of branch `migrate/monorepo`. Today that is
  `W=/Users/jordanburger/.scout-worktrees/scout-app-monorepo`.
- `R` is a clean checkout of `main` for Phases 4 and 8. It must not be `W`:
  `W` is a worktree, and git refuses to check `main` out there while
  `~/scout-app` has it. It must also not be the live engine checkout that
  Phase 6 creates at `~/scout-plugin`, because the release script switches
  branches. Use `~/scout-app` when it's clean on `main`; otherwise use a
  throwaway clone (`git clone https://github.com/Raven-Scout/Scout.git
  ~/.scout-worktrees/scout-release`). Either way it needs the engine venv:
  `cd "$R/plugin/engine" && uv venv --python 3.12 && uv pip install -e ".[dev]"`.
- **No literal version numbers.** Every version is derived when you run the
  step. If a step tells you to type a version, it first shows the command that
  produces it.
- Never `git fetch --tags` from `scout-plugin`. Its bare `v*` tags collide with
  the app's `v0.5.0`–`v0.13.x`, and the monorepo deliberately doesn't import
  them. `git subtree pull` fetches no tags.

---

## 0. Where things stand

- [ ] Branch `migrate/monorepo`: local only, no upstream, not on `origin`.
  Synced on 2026-10-04 with Scout `origin/main` **d6e1462** (v0.13.4) and
  scout-plugin `main` **ca8b21a** (v0.12.0) by the fix wave that added this
  runbook. Its last code commit is `04a766e`. The commit that adds this file
  is HEAD.
- [ ] Verified locally at that HEAD:
  - the engine suite: 2883 passed, 14 skipped (the one known-flaky FS-watcher
    test passes when re-run alone)
  - ruff, ruff format, mypy
  - `versioning check` → `0.12.0`
  - every `run:` step of `contract.yml`'s verify job under `bash -e -o pipefail`,
    plus the canonical `--check` steps
  - shellcheck on every script that plugin-lint checks
  - all workflows parse
  - `bash install.sh --check`
  - the macOS `ScoutTests`: 928 tests in 141 suites, TEST SUCCEEDED

  The raw output is in the local fix-wave report.
- [ ] **Not yet verified anywhere:** behaviour in real GitHub Actions (gate jobs,
  skipped-job semantics), rulesets, an install from the *pushed* repo, and
  both release scripts end to end.
- [ ] Local-only records (gitignored), under
  `.superpowers/sdd/2026-09-03-monorepo-consolidation/` in `W`:
  - `progress.md`: the controller ledger, rulings R1–R39
  - `final-review.md`
  - `fix-wave-report.md`
  - `fix-wave-harness/`: the install.sh / Step 0.2 / contract harnesses used
    below

Both repos will keep moving until cutover, so **Phase 1 runs again right
before the PR is opened**.

---

## Phase 0 — Clear the runway (scout-plugin) [outward]

- [ ] **0.1 List open scout-plugin PRs.**

  ```bash
  gh pr list --repo Raven-Scout/scout-plugin --state open --limit 100 \
    --json number,author,isDraft,title \
    --jq '.[] | "\(.number)\t\(.author.login)\t\(if .isDraft then "draft" else "" end)\t\(.title)"'
  ```

  Snapshot 2026-10-04: 16 open.
  - **External authors (7):**
    - #264, #247 ottomansky
    - #216, #194 davidesner
    - #180 cvrysanek
    - #176, #175 yustme
  - **Jordan's own (9):** #274, #269, #268, #266, #265, #261, #242 (draft),
    #179, #177.

  Every one must be merged or closed before the archive, because archived
  repos have read-only PRs. Anything merged into scout-plugin before cutover
  comes in through Phase 1's subtree pull.
- [ ] **0.2 Resolve each one.** Merge it, or close it with a pointer, using this
  comment:

  ```bash
  N="<pr-number>"   # one at a time
  gh pr comment "$N" --repo Raven-Scout/scout-plugin --body "Heads-up: scout-plugin is being merged into Raven-Scout/Scout as a monorepo — the plugin will live at plugin/ in that repo. Resolving this PR before the move so your branch doesn't get invalidated mid-review. To continue it, re-open it against Raven-Scout/Scout with your changes under plugin/. Design: Raven-Scout/Scout#99."
  ```
- [ ] **0.3 Post the freeze notice** as an issue, then pin it from the issue page.

  ```bash
  cat > /tmp/freeze-notice.md <<'EOF'
  `scout-plugin` is being absorbed into [Raven-Scout/Scout](https://github.com/Raven-Scout/Scout) as a monorepo. The plugin will live at `plugin/` and stays a first-class Claude Code marketplace entry — the marketplace keeps its name, `scout-plugin`, so the plugin id stays `scout@scout-plugin`.

  **What you need to do once the move lands** (announced here and in this repo's final release):

      claude plugin marketplace remove scout-plugin
      claude plugin marketplace add Raven-Scout/Scout
      claude plugin install scout@scout-plugin

  Removing the marketplace uninstalls the plugin until the install line puts it back; your vault in `~/Scout` is not touched. New installs use `curl -fsSL https://raw.githubusercontent.com/Raven-Scout/Scout/main/install.sh | bash`; an existing install that still points here is detected by that installer — and by `/scout-update` from this repo's final release on — which print the three commands above.

  **Why:** contract artifacts (the connector roster, the schedule snapshot, the parser corpus) are single logical files that physically lived in two or three repos, and no CI job could see across a repo edge. Design: Raven-Scout/Scout#99.

  **Until the move completes, this repo stays authoritative.** New PRs are welcome but may need re-targeting — comment here first.
  EOF
  gh issue create --repo Raven-Scout/scout-plugin \
    --title "Notice: scout-plugin is moving into Raven-Scout/Scout (monorepo)" \
    --body-file /tmp/freeze-notice.md
  ```
- [ ] **0.4 Gate:** no external open PRs left.

  ```bash
  gh pr list --repo Raven-Scout/scout-plugin --state open --limit 100 \
    --json author --jq '[.[] | select(.author.login != "jordanrburger")] | length'   # expect 0
  ```
- [ ] **0.5 Scout PRs that must cross the move.** List them:

  ```bash
  gh pr list --repo Raven-Scout/Scout --state open --limit 100 \
    --json number,headRefName,isDraft,title \
    --jq '.[] | "\(.number)\t\(.headRefName)\t\(if .isDraft then "draft" else "" end)\t\(.title)"'
  ```

  Snapshot 2026-10-04, classified by whether a PR touches the app's old
  top-level paths (`Scout/`, `ScoutTests/`, `Scout.xcodeproj/`, `scripts/`,
  `CLAUDE.md`, `README.md`, `BACKLOG.md`, `.github/workflows/ci.yml`). Those
  paths now live under `apps/macos/`, and `ci.yml` is now `app-ci.yml`.

  | PR | Crosses the move? | Note |
  |---|---|---|
  | **#125** Part B (draft, "waits on monorepo migration") | yes, 45 files | Rebases AFTER the migration merges — see Phase 2. |
  | **#128** Part C (draft, stacked on #125) | yes | Rebases after #125. |
  | **#120** pin one-line item format in the parser contract | yes, corpus + Swift SHA | **Hold until after the migration.** It edits only the app's corpus and `canonicalSHA256`. In the monorepo, a corpus change must edit the canonical `plugin/engine/tests/fixtures/contract/parser-corpus.json`, update `EXPECTED_SHA256` and `canonicalSHA256`, and `cp` the canonical file to the app's copy (root `CLAUDE.md`). If #120 lands on main first, the next Phase 1 merge routes its corpus edit onto the plugin's canonical copy (trap A), and the plugin checksum test fails. |
  | #126, #122, #75 | yes | Rebase after the migration merges. |
  | #70 | yes, and edits `.github/workflows/ci.yml` | Port its `ci.yml` change into `app-ci.yml` by hand. |
  | #127, #119, #118, #68 | no, `docs/` only | `docs/` stayed at the root. |
  | **#99** monorepo design | — | Its two docs are already on the branch (R25); merge or close it before the migration PR. |

  Rebase recipe for a PR that crosses the move (after the migration is on
  main). `merge.directoryRenames=true` places that PR's NEW files under
  `apps/macos/` instead of stopping on each one:

  ```bash
  git fetch origin
  PR_BRANCH="<the PR head branch>"
  git switch "$PR_BRANCH"
  git -c merge.directoryRenames=true rebase origin/main
  git ls-files -- Scout ScoutTests Scout.xcodeproj scripts | head   # must print nothing
  ```

---

## Phase 1 — Re-sync the branch (repeat right before opening the PR)

This is the fix wave's C1 procedure, generalized. Each sub-step is one commit.
If a merge goes wrong, run `git merge --abort` and start the sub-step again.

- [ ] **1.0 Start clean and record the heads.**

  ```bash
  cd "$W"
  git status --porcelain                          # must print nothing
  git rev-parse --abbrev-ref HEAD                 # migrate/monorepo
  git fetch origin
  MAIN_NEW=$(git rev-parse origin/main)
  MAIN_PREV=$(git merge-base HEAD origin/main)    # the Scout main commit last merged in
  git fetch https://github.com/Raven-Scout/scout-plugin.git main
  UP_NEW=$(git rev-parse FETCH_HEAD)
  UP_PREV=$(git merge-base HEAD "$UP_NEW")        # the scout-plugin commit plugin/ was last synced to
  echo "main $MAIN_PREV -> $MAIN_NEW ; scout-plugin $UP_PREV -> $UP_NEW"
  git log --oneline "$MAIN_PREV..$MAIN_NEW"; git log --oneline "$UP_PREV..$UP_NEW"
  ```

- [ ] **1.1 Merge Scout `main`.** Watch for two silent traps.

  ```bash
  git merge --no-ff --no-commit origin/main
  git status --porcelain | grep -v '^[MAR]  '     # conflicts to resolve
  ```

  - **Trap A: a Scout-main merge must never change `plugin/`.** The app's
    contract fixtures are byte-identical to the plugin's canonical files, so
    rename detection can route main's edit to an app fixture onto the
    canonical copy. In this wave, #111's corpus edit landed on
    `plugin/engine/tests/fixtures/contract/parser-corpus.json`, with no
    conflict reported.

    ```bash
    git diff --cached --name-only HEAD -- plugin/ .claude-plugin/ install.sh   # must print nothing
    ```

    For every path that check prints: restore it with
    `git checkout HEAD -- <path>`, then apply main's bytes to the `apps/macos`
    file main actually edited. For example:

    ```bash
    git show origin/main:ScoutTests/Fixtures/parser-corpus.json > apps/macos/ScoutTests/Fixtures/parser-corpus.json
    ```

    The canonical file changes only through the subtree (1.2).
  - **Trap B: main's edits to the app's `CLAUDE.md` / `README.md` land on the
    NEW repo-root files.** Resolving with "ours" silently drops them, and a
    clean auto-merge silently injects app text into the root file. Keep the
    root files and port main's change into `apps/macos/` with a three-way
    merge:

    ```bash
    for f in CLAUDE.md README.md; do
      git show "$MAIN_PREV:$f" > /tmp/base-$f; git show "origin/main:$f" > /tmp/theirs-$f
      git merge-file -p "apps/macos/$f" /tmp/base-$f /tmp/theirs-$f > /tmp/out-$f && cp /tmp/out-$f "apps/macos/$f"
      git checkout HEAD -- "$f"                    # root stays as the branch has it
      git add "$f" "apps/macos/$f"
    done
    git diff --cached --quiet HEAD -- CLAUDE.md README.md && echo "root docs unchanged"
    ```

    If `merge-file` exits non-zero, resolve the markers in `/tmp/out-$f` before
    copying.
  - **New files main added under the old roots** come up as "file location"
    conflicts. Git already places each at the `apps/macos/…` path it suggests.
    Check each against main, then `git add` it:

    ```bash
    for f in $(git diff --name-only --diff-filter=A "$MAIN_PREV" origin/main -- Scout ScoutTests); do
      git show "origin/main:$f" | cmp -s - "apps/macos/$f" && echo "OK $f" || echo "CHECK $f"
      git add "apps/macos/$f"
    done
    git ls-files -- Scout ScoutTests Scout.xcodeproj scripts design BACKLOG.md | head   # must print nothing
    ```

  - **Renamed-and-edited files.** If main edits these, port the change by hand
    into the new file:
    - `.github/workflows/ci.yml` → `.github/workflows/app-ci.yml`
    - `scripts/release.sh` → `apps/macos/scripts/release-app.sh`

    Then `git rm` the resurrected old path.
  - **Content conflicts** inside `apps/macos/` resolve normally. Keep the
    branch's `ScoutctlLocator` wiring in `AppState.swift` and the
    `ActionItemsIntegrationTests.findScoutctl()` delegation (R37: #125 replaces
    them, not this branch).
  - **Landing check.** Each of main's app changes must sit under `apps/macos/`.
    Where a file differs from main, it may differ only by the branch's own
    edits:

    ```bash
    for f in $(git diff --name-only "$MAIN_PREV" origin/main -- Scout ScoutTests Scout.xcodeproj scripts design BACKLOG.md); do
      git show "origin/main:$f" | cmp -s - <(git show ":apps/macos/$f" 2>/dev/null) || echo "differs from main: apps/macos/$f"
    done
    ```

  - Commit with a message that lists each conflict and its resolution. Use the
    fix wave's merge commit, `efaf638`, as the model.

- [ ] **1.2 Subtree-pull scout-plugin `main`.**

  ```bash
  git diff --name-status "$UP_PREV" HEAD:plugin > /tmp/oracle-pre.txt   # the divergence oracle, before
  git subtree pull --prefix=plugin https://github.com/Raven-Scout/scout-plugin.git main
  git status --porcelain | grep -v '^[MAR]  '
  ```

  **Resurrected-file rules.** Upstream edits to files the monorepo moved out
  of `plugin/` come back as modify/delete conflicts under `plugin/`. None of
  these may exist under `plugin/` when you commit:

  | Resurrected | Do this |
  |---|---|
  | `plugin/.claude-plugin/marketplace.json` | Apply ONLY upstream's `version` change to the ROOT `.claude-plugin/marketplace.json`, keeping `"source": "./plugin"` and the `Raven-Scout/Scout` homepage/repository. Then `git rm` the resurrected file. |
  | `plugin/install.sh` | The root `install.sh` has diverged: the `Raven-Scout/Scout` marketplace and `check_marketplace_source`. Three-way merge: `git merge-file -p install.sh <(git show $UP_PREV:install.sh) <(git show $UP_NEW:install.sh)`, then write the result to the root `install.sh` and `git rm` the resurrected file. Never replace the root file wholesale. |
  | `plugin/docs/**`, modified | Same three-way merge into the root `docs/` file, which carries Task 7's path rewrites and, in `docs/index.html`, the re-pointed links. `git rm` the resurrected file. |
  | `plugin/docs/**`, new | `git mv` it to the same path under the root `docs/`. |
  | `plugin/LICENSE`, `PRIVACY.md`, `TERMS.md` | Port the change to the root copy, then `git rm`. |
  | `plugin/scripts/release.sh` | Port the change into `plugin/scripts/release-plugin.sh` by hand, keeping the `plugin/v` tag prefix and the `git -C "$REPO_ROOT"` calls. Then `git rm`. |
  | `plugin/.github/workflows/*` | Port the change into `.github/workflows/{plugin-test,plugin-lint,release-plugin}.yml` by hand, keeping the `changes` / gate jobs. Then `git rm`. |
  | `plugin/.gitignore` | Stays nested (R24). Merge normally, but keep `docs/plans/archive/PLAN-8-RESUME.md` out of it; the root `.gitignore` carries that line. |

  Files the branch deliberately changed inside `plugin/` may conflict if
  upstream touched them too. Resolve these by hand, keeping both intents:
  - `.claude-plugin/plugin.json` (homepage/repository)
  - `CLAUDE.md`, `README.md`
  - `commands/scout-{setup,status,update}.md`
  - `engine/README.md`, `engine/bin/scoutctl`
  - `engine/scout/scripts/{self_update,versioning}.py`
  - `scripts/gen-render-history.py`
  - the tests next to each of these

  **Oracle.** Before committing:

  ```bash
  git diff --name-status "$UP_NEW" "$(git write-tree)":plugin > /tmp/oracle-post.txt
  diff /tmp/oracle-pre.txt /tmp/oracle-post.txt
  ```

  These two lists must match, except for differences you can explain. Expected
  ones:
  - a `D docs/…` line for each new upstream doc you relocated
  - a line that disappears because upstream deleted that file

  Then confirm the manifests:

  ```bash
  cd plugin/engine && uv pip install -e ".[dev]" && .venv/bin/python -m scout.scripts.versioning check; cd "$W"
  git show "$UP_NEW:.claude-plugin/plugin.json" | python3 -c 'import json,sys;print(json.load(sys.stdin)["version"])'
  ```

  `versioning check` must print the same version as upstream's `plugin.json`.
  Commit with a message listing each resolution; the fix wave's `f407b5a` is
  the model. Check that no tags came along:

  ```bash
  git tag --list 'app/v*' 'plugin/v*'   # empty
  ```

  New upstream code can assume the old single-repo layout. In this wave,
  upstream #263's `gen-render-history.py` read the app's bare `v*` tags as
  plugin releases (fixed in `b0ca543`). Phase 1.4's engine suite is what
  catches this.

- [ ] **1.3 Re-sync the client copies from canonical.** These are byte copies;
  never regenerate a copy.

  ```bash
  cp plugin/engine/tests/fixtures/contract/parser-corpus.json apps/macos/ScoutTests/Fixtures/parser-corpus.json
  cp plugin/engine/scout/connectors.snapshot.json apps/macos/ScoutTests/Fixtures/connectors.snapshot.json
  cp plugin/engine/scout/connectors.snapshot.json apps/macos/Scout/Resources/connectors.snapshot.json
  cp plugin/engine/scout/schedule.snapshot.json   apps/macos/ScoutTests/Fixtures/schedule.snapshot.json
  shasum -a 256 plugin/engine/tests/fixtures/contract/parser-corpus.json apps/macos/ScoutTests/Fixtures/parser-corpus.json
  grep -o 'canonicalSHA256 = "[0-9a-f]*"' apps/macos/ScoutTests/ActionItems/ParserContractTests.swift
  grep -o 'EXPECTED_SHA256 = "[0-9a-f]*"' plugin/engine/tests/unit/test_parser_corpus_checksum.py
  git status --porcelain     # commit if anything changed ("chore(contract): re-sync client copies from canonical")
  ```

  All four SHA-256 values must be equal. If the Swift constant lags, set it to
  the corpus SHA in the same commit.

  The list of client copies lives in `contract.yml`'s `SNAPSHOT_COPIES`. Its
  "Every tracked snapshot copy is checked" step fails if a new tracked copy
  isn't listed there.

- [ ] **1.4 Verify.** Paste the raw output into the PR description.

  ```bash
  cd "$W/plugin/engine"
  .venv/bin/pytest tests/ -q          # the FS-watcher test in test_action_items_watch.py is known-flaky: re-run it alone
  .venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests && .venv/bin/mypy scout
  .venv/bin/python -m scout.scripts.versioning check        # never `versioning bump` here: it WRITES all four manifests
  .venv/bin/python -m scout.scripts.connectors_snapshot --check --no-also-write-app-fixture
  .venv/bin/python -m scout.scripts.schedule_snapshot  --check --no-also-write-app-fixture
  cd "$W"
  plugin/engine/.venv/bin/python .superpowers/sdd/2026-09-03-monorepo-consolidation/fix-wave-harness/run-contract.py   # every contract.yml verify step, bash -e -o pipefail
  shellcheck plugin/engine/bin/scoutctl
  shellcheck -S error install.sh plugin/scripts/*.sh apps/macos/scripts/*.sh .github/scripts/changed-paths.sh
  for f in .github/workflows/*.yml; do plugin/engine/.venv/bin/python -c 'import sys,yaml;yaml.safe_load(open(sys.argv[1]))' "$f" || echo "BAD $f"; done
  bash install.sh --check
  df -h /System/Volumes/Data          # stop if under 5 GiB free
  cd apps/macos && xcodebuild test -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' \
    -only-testing:ScoutTests -derivedDataPath "$W/.superpowers/derived-data" COMPILER_INDEX_STORE_ENABLE=NO \
    CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY="" 2>&1 | grep -E '\*\* TEST (SUCCEEDED|FAILED)|Executed|tests? in'
  ```

  The harness is gitignored. Without it, run each `run:` block of
  `contract.yml`'s `verify` job yourself from the repo root, under
  `bash -e -o pipefail`, with `SNAPSHOT_COPIES` exported from the job's `env:`.

---

## Phase 2 — Merge order with Part B (#125)

- [ ] The migration merges **first**. #125 ("[waits on monorepo migration]")
  then rebases onto `main` (Phase 0.5 recipe), and #128 rebases onto #125. On
  that rebase, Part B moves `ScoutctlLocator`'s priority and never-probe-bin
  tests into `EngineLocatorTests`, deletes `ScoutctlLocator`, re-points
  `findScoutctl()`, and adds the monorepo `plugin/` as a dev-checkout
  candidate. The migration branch does not touch any of that (R37).
- [ ] As soon as the migration PR is merged, notify the Part B session
  (**"Build and test Scout.app Part B, then open its PR"**). Use `ListAgents`
  to find it, then `SendMessage`. Send the merge commit SHA and these facts:
  - the app now lives in `apps/macos/`
  - `ci.yml` is now `app-ci.yml`
  - required checks are about to be `app-ci`, `plugin-test`, `plugin-lint`
    and `contract`
  - #125's new `Scout/Engine/*` files land under `apps/macos/Scout/Engine/`
    with `merge.directoryRenames=true`

---

## Phase 3 — Push, open the PR, prove the gates, then add rulesets

- [ ] **3.1 Push and open the PR** [outward]. Push only after Phase 1 is
  committed and 1.4 is green.

  ```bash
  cd "$W"
  git push -u origin migrate/monorepo
  gh pr create --repo Raven-Scout/Scout --base main --head migrate/monorepo \
    --title "refactor: absorb scout-plugin into the Scout monorepo" \
    --body-file /tmp/migration-pr-body.md
  ```

  Write `/tmp/migration-pr-body.md` from the Phase 1.4 output. Include:
  - the two upstream heads it was synced to
  - the oracle result
  - test counts
  - "contract green"
  - the `apps/macos` / `plugin/` layout
  - "merge with a merge commit, not squash"
  - the required-check plan
  - the attribution footer

  Pushing to `migrate/**` also starts push-triggered runs of `contract`,
  `plugin-test` and `plugin-lint`, which run their jobs unconditionally.
  That's expected. `app-ci` runs only on the PR, because its push trigger is
  limited to `main`.
- [ ] **3.2 Read the PR's checks.**

  ```bash
  gh pr checks migrate/monorepo --repo Raven-Scout/Scout --watch
  ```

  Expect four gate checks, `app-ci`, `plugin-test`, `plugin-lint` and
  `contract`, all passing. Under each, the gated jobs (`ScoutTests`,
  `test (…)` + `coverage`, `lint`, `verify`) must have actually **run**,
  because this PR touches every area. If `plugin-test` fails only on the
  FS-watcher test, re-run the job; see Open decisions.
- [ ] **3.3 Merge** [outward] with **"Create a merge commit"**. **Never squash
  or rebase:** the plugin history arrived by SHA-preserving subtree merges
  (R23), which a squash flattens and a rebase cannot replay. Then do Phase 2's
  notification.

  ```bash
  gh pr merge migrate/monorepo --repo Raven-Scout/Scout --merge
  ```
- [ ] **3.4 Prove the skip path on a docs-only PR** [outward]. Do this BEFORE
  adding any ruleset. It must target `main`, because `app-ci` only triggers on
  PRs into `main`.

  ```bash
  cd "$R" && git fetch origin && git switch -c ci/gate-proof-docs origin/main
  echo "" >> docs/README.md && git commit -am "docs: gate proof (throwaway, do not merge)"
  git push -u origin ci/gate-proof-docs
  gh pr create --repo Raven-Scout/Scout --base main --head ci/gate-proof-docs --draft \
    --title "ci: gate proof — docs-only (throwaway)" --body "Throwaway: proves all four gates pass with their jobs skipped. Will be closed."
  gh pr checks ci/gate-proof-docs --repo Raven-Scout/Scout --watch
  ```

  Expect `app-ci`, `plugin-test`, `plugin-lint` and `contract` all
  **success**, with every gated job **skipped** and each `changes` job logging
  `run=false`.
- [ ] **3.5 Prove the fail path on a contract-drift PR** [outward], recommended.

  ```bash
  cd "$R" && git switch -c ci/gate-proof-drift origin/main
  python3 - <<'PY'
  import json; p="apps/macos/ScoutTests/Fixtures/schedule.snapshot.json"
  d=json.load(open(p)); d["__gate_proof__"]=True
  open(p,"w").write(json.dumps(d,indent=2)+"\n")
  PY
  git commit -am "test: gate proof — drifted contract copy (throwaway, do not merge)"
  git push -u origin ci/gate-proof-drift
  gh pr create --repo Raven-Scout/Scout --base main --head ci/gate-proof-drift --draft \
    --title "ci: gate proof — contract drift (throwaway)" --body "Throwaway: contract must go red. Will be closed."
  gh pr checks ci/gate-proof-drift --repo Raven-Scout/Scout --watch
  ```

  Expect `contract` to **fail**, with an `::error` on that file naming the
  `cp` fix.
- [ ] Close both throwaway PRs and delete their branches:

  ```bash
  for b in ci/gate-proof-docs ci/gate-proof-drift; do
    gh pr close "$b" --repo Raven-Scout/Scout --delete-branch
  done
  cd "$R" && git switch main && git branch -D ci/gate-proof-docs ci/gate-proof-drift
  ```
- [ ] **3.6 Require the four gates** [outward]. Scout's ruleset `main` (id
  17316108 on 2026-10-04) has no status-check rule today. Add one:

  ```bash
  RS=$(gh api repos/Raven-Scout/Scout/rulesets --jq '.[] | select(.name=="main") | .id')
  gh api "repos/Raven-Scout/Scout/rulesets/$RS" > /tmp/ruleset-before.json
  jq '{name, target, enforcement, conditions, bypass_actors: (.bypass_actors // []),
       rules: ([.rules[] | select(.type != "required_status_checks")] + [{
         type: "required_status_checks",
         parameters: {strict_required_status_checks_policy: false,
           required_status_checks: [{context:"app-ci"},{context:"plugin-test"},{context:"plugin-lint"},{context:"contract"}]}}])}' \
    /tmp/ruleset-before.json > /tmp/ruleset-after.json
  gh api --method PUT "repos/Raven-Scout/Scout/rulesets/$RS" --input /tmp/ruleset-after.json
  gh api "repos/Raven-Scout/Scout/rulesets/$RS" --jq '.rules[] | select(.type=="required_status_checks") | .parameters.required_status_checks[].context'
  ```

  Expect `app-ci`, `plugin-test`, `plugin-lint` and `contract`. If the PUT
  rejects a field it returned itself, add the rule in Settings → Rules → `main`
  → "Require status checks to pass", using those four names. Keep the
  `pull_request` rule's `allowed_merge_methods` including `merge`.

---

## Phase 4 — Releases (both from `main`, after Phase 3)

- [ ] **4.1 Plugin first.** Releasing it first means the app's version floor
  names a published plugin.
  1. Fill `plugin/CHANGELOG.md`'s `## [Unreleased]` before preparing.
     `release-plugin.yml` refuses an empty section. Cover:
     - the move to `Raven-Scout/Scout` and the re-point commands
     - `self-update check` now reads the monorepo manifest
     - `/scout-update`'s Step 0.2 archived-marketplace stop and its
       two-layout `~/scout-plugin` resolver
     - the installer's stale-marketplace check
     - `scoutctl`'s `marketplaces/<name>/plugin/` venv candidates
     - anything Phase 1 pulled in
  2. Prepare. Use **`minor`**: it leaves the next patch of scout-plugin's own
     line free for its final release (Phase 7).

     ```bash
     cd "$R" && git switch main && git pull --ff-only && git status --porcelain   # must print nothing
     bash plugin/scripts/release-plugin.sh minor        # bumps on release/vX.Y.Z, commits, pushes, opens the release PR [outward]
     ```
  3. Merge the release PR once its four gates are green. Then:

     ```bash
     cd "$R" && git switch main && git pull --ff-only
     NEW=$(cd plugin/engine && .venv/bin/python -m scout.scripts.versioning check)
     bash plugin/scripts/release-plugin.sh --finalize "plugin/v$NEW"     # tags origin/main, pushes the tag [outward]
     gh run watch --repo Raven-Scout/Scout "$(gh run list --repo Raven-Scout/Scout --workflow release-plugin.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
     gh release view "plugin/v$NEW" --repo Raven-Scout/Scout
     gh api repos/Raven-Scout/Scout/releases/latest --jq .tag_name        # must NOT be plugin/v… (--latest=false)
     ```
  - Never run `versioning set` with a lower version: nothing enforces
    monotonicity. `versioning bump` is not a query; it WRITES all four
    manifests (the release script relies on that). Never push a bare `v*` tag.
- [ ] **4.2 App.** Needs the Developer ID Application cert in the keychain and
  the `scout-notary` notarytool profile. The script auto-picks the version from
  `apps/macos` commits since the newest `app/v*` tag, falling back to the
  newest bare `v*` tag. It stamps `SCScoutPluginFloor` from `plugin.json`,
  tags `app/vX.Y.Z`, pushes the tag, and publishes the DMG release with
  `--latest --repo <origin slug>`.

  ```bash
  cd "$R" && git switch main && git pull --ff-only
  SKIP_NOTARIZE=1 SKIP_RELEASE=1 bash apps/macos/scripts/release-app.sh   # dry run: prints the version it would release
  bash apps/macos/scripts/release-app.sh                                    # [outward] or pass an explicit X.Y.Z
  gh api repos/Raven-Scout/Scout/releases/latest --jq '.tag_name, [.assets[].name]'   # app/v…, with Scout-<ver>.dmg
  ```

  `apps/macos/CHANGELOG.md` is not wired into this script (Open decision M6).

---

## Phase 5 — Clean-machine verification (sandboxed ONLY)

**Never run `claude plugin marketplace add/remove/update` or
`claude plugin install/uninstall` against the real config.** Jordan's live
marketplace is also named `scout-plugin`, and removing it uninstalls the
plugin his scheduled runs use (R16). Every command below carries
`CLAUDE_CONFIG_DIR=<sandbox>`. The R27 semantic guard brackets the phase.
Whole-file hashes are not a valid guard, because Claude Code rewrites other
entries in the background.

- [ ] **5.1 Guard: before.**

  ```bash
  cat > /tmp/r27-guard.py <<'PY'
  import json, os, sys
  base = os.path.expanduser("~/.claude/plugins")
  km = json.load(open(f"{base}/known_marketplaces.json"))
  ip = json.load(open(f"{base}/installed_plugins.json"))
  print("marketplace:", json.dumps(km.get("scout-plugin"), sort_keys=True))
  print("install:", json.dumps(ip.get("plugins", {}).get("scout@scout-plugin"), sort_keys=True))
  blob = json.dumps(km) + json.dumps(ip)
  for needle in sys.argv[1:]:
      print("references", needle, ":", needle in blob)
  PY
  SB="$(mktemp -d "$HOME/.scout-worktrees/claude-sandbox.XXXXXX")"
  python3 /tmp/r27-guard.py "$SB" "$W" > /tmp/r27-before.txt; cat /tmp/r27-before.txt   # both "references … : False"
  ```
- [ ] **5.2 Prove isolation, then install from GitHub.**

  ```bash
  CLAUDE_CONFIG_DIR="$SB" claude plugin marketplace list    # must NOT list scout-plugin; if it does, STOP
  CLAUDE_CONFIG_DIR="$SB" claude plugin marketplace add Raven-Scout/Scout
  CLAUDE_CONFIG_DIR="$SB" claude plugin install scout@scout-plugin
  read -r VER IP < <(CLAUDE_CONFIG_DIR="$SB" claude plugin list --json | python3 -c '
  import json,sys; d=json.load(sys.stdin)
  e=d if isinstance(d,list) else [p for ps in d.get("plugins",{}).values() for p in ps]
  print(next(("%s %s" % (p["version"], p["installPath"]) for p in e if p.get("id")=="scout@scout-plugin"), ""))')
  echo "installed $VER at $IP"
  ```

  The version must equal `plugin/v$NEW` from 4.1, and the installPath must be
  inside `$SB`. Then:

  ```bash
  case "$IP" in "$SB"/*) echo "installPath is inside the sandbox" ;; *) echo "installPath is NOT inside the sandbox — STOP HERE" ;; esac
  test -x "$IP/engine/bin/scoutctl" && test ! -e "$IP/apps" && ! ls "$IP"/*.xcodeproj 2>/dev/null && echo "plugin-only tree OK"
  bash "$IP/scripts/install-venv.sh" && "$IP/.venv/bin/scoutctl" version     # the venv lands inside $IP (sandbox)
  ```
- [ ] **5.3 Rehearse an existing user's migration** in a second sandbox, with
  the installer in check mode only:

  ```bash
  SB2="$(mktemp -d "$HOME/.scout-worktrees/claude-sandbox.XXXXXX")"
  CLAUDE_CONFIG_DIR="$SB2" claude plugin marketplace add Raven-Scout/scout-plugin      # the OLD repo
  curl -fsSL https://raw.githubusercontent.com/Raven-Scout/Scout/main/install.sh | CLAUDE_CONFIG_DIR="$SB2" bash -s -- --check
  ```

  Expect exit 1 and the remove/add/install instructions. Then run them:

  ```bash
  CLAUDE_CONFIG_DIR="$SB2" claude plugin marketplace remove scout-plugin
  CLAUDE_CONFIG_DIR="$SB2" claude plugin marketplace add Raven-Scout/Scout
  CLAUDE_CONFIG_DIR="$SB2" claude plugin install scout@scout-plugin
  curl -fsSL https://raw.githubusercontent.com/Raven-Scout/Scout/main/install.sh | CLAUDE_CONFIG_DIR="$SB2" bash -s -- --check
  ```

  The second check must say `preconditions OK`.
- [ ] **5.4 Guard: after.** Then clean up.

  ```bash
  python3 /tmp/r27-guard.py "$SB" "$W" > /tmp/r27-after.txt; diff /tmp/r27-before.txt /tmp/r27-after.txt && echo "R27 guard: real config untouched"
  python3 /tmp/r27-guard.py "$SB2" | tail -1                                    # "references … : False"
  rm -rf "$SB" "$SB2"
  ```

  If 5.2 or 5.3 fails, **do not archive scout-plugin.** Fix forward first.

---

## Phase 6 — Jordan's own machine

Jordan runs this phase by hand. It deliberately changes his real Claude Code
config, so no agent runs it.

His live install is a **directory** marketplace named `scout-plugin` at
`~/scout-plugin`, his pre-monorepo dev checkout. `scout@scout-plugin`
installs from it, and the engine pointer (`managed_by: dev`), the launchd
jobs, and `~/miniconda3/bin/scoutctl` (an editable install of
`~/scout-plugin/engine`) all hang off it. `/scout-update`'s resolver prefers a
git checkout at `~/scout-plugin` over the installed plugin. So whatever sits
at `~/scout-plugin` is what upgrades his vault.

- [ ] **6.1 Pick a quiet window.** Choose a time with no scheduled slot due:

  ```bash
  ~/.local/bin/scoutctl schedule list-upcoming --json | head
  ```
- [ ] **6.2 Recommended: put a monorepo clone at the same path.** Move the
  legacy checkout aside (don't delete it yet) and clone in its place:

  ```bash
  mv ~/scout-plugin ~/scout-plugin.legacy-$(date +%Y%m%d)
  git clone https://github.com/Raven-Scout/Scout.git ~/scout-plugin
  ```

  Rehearse the next two commands in a sandbox first: a directory marketplace
  pointing at a legacy-layout dir, swapped for the monorepo layout, then
  `marketplace update` plus `plugin update`. Then for real:

  ```bash
  claude plugin marketplace update scout-plugin       # re-reads ~/scout-plugin/.claude-plugin/marketplace.json ("source": "./plugin")
  claude plugin update scout@scout-plugin
  ```

  If `claude plugin list --json` doesn't show an installPath under
  `~/scout-plugin/plugin` or the cache, re-point instead:

  ```bash
  claude plugin marketplace remove scout-plugin
  claude plugin marketplace add ~/scout-plugin
  claude plugin install scout@scout-plugin
  ```

  The alternative, pointing the marketplace at `~/scout-app`, makes his live
  plugin follow whatever branch `~/scout-app` has checked out. Not recommended.
- [ ] **6.3 Rebuild and re-point the engine.**

  ```bash
  bash ~/scout-plugin/plugin/scripts/install-venv.sh
  ~/scout-plugin/plugin/.venv/bin/scoutctl bootstrap upgrade --managed-by dev   # re-points plists, shim and ~/.local/state/scout/engine.json
  ~/miniconda3/bin/pip install -e ~/scout-plugin/plugin/engine                  # or uninstall that editable copy
  ```
- [ ] **6.4 Verify.**

  ```bash
  claude plugin list --json | grep -A3 '"scout@scout-plugin"'
  ~/.local/bin/scoutctl version
  ~/scout-plugin/plugin/.venv/bin/scoutctl bootstrap doctor
  grep -m1 '"python"' ~/.local/state/scout/engine.json
  ```

  Then watch the next scheduled run go green.
- [ ] **6.5 Retire the legacy checkout.** After a few days of green runs,
  delete `~/scout-plugin.legacy-*`. Until then it is the rollback: move it
  back and run `claude plugin marketplace update scout-plugin`.

---

## Phase 7 — Final scout-plugin release, archive, org README, close the notice [outward]

Only after Phase 5 passes.

- [ ] **7.1 Ship scout-plugin's last release**, so users still on the old
  marketplace get the migration messages through their normal update path.
  Open one PR on `Raven-Scout/scout-plugin` with:
  - **README:** a top banner saying "Moved to Raven-Scout/Scout", with the
    three re-point commands.
  - **`engine/scout/scripts/self_update.py`:** `RAW_MARKETPLACE_URL` →
    `https://raw.githubusercontent.com/Raven-Scout/Scout/main/.claude-plugin/marketplace.json`.
    `/scout-status` on old installs then reports the monorepo's newer version
    as available.
  - **`commands/scout-update.md`:** add "Step 0.2: Stop if the marketplace
    still points at the archived repo", copied verbatim from the monorepo's
    `plugin/commands/scout-update.md`.
  - **`install.sh`:** make it a redirect, so old `curl` one-liners keep
    working:

    ```bash
    #!/usr/bin/env bash
    echo "Scout has moved to Raven-Scout/Scout — running the current installer…" >&2
    curl -fsSL https://raw.githubusercontent.com/Raven-Scout/Scout/main/install.sh | bash -s -- "$@"
    ```

  **Version:** the next PATCH of scout-plugin's latest tag, which must stay
  below the monorepo's `plugin/v$NEW`. Check:

  ```bash
  OLD=$(git ls-remote --tags --refs https://github.com/Raven-Scout/scout-plugin.git 'v*' | sed 's#.*refs/tags/v##' | sort -V | tail -1)
  python3 - "$OLD" "$NEW" <<'PY'
  import sys
  o=[int(x) for x in sys.argv[1].split(".")]; n=[int(x) for x in sys.argv[2].split(".")]
  f=o[:2]+[o[2]+1]; print("final scout-plugin:", ".".join(map(str,f))); assert f < n, "final would not be below the monorepo release"
  PY
  ```

  Release it with scout-plugin's own flow: `scripts/release.sh patch`, then
  `--finalize v<that>`, in a scout-plugin clone.
- [ ] **7.2 Archive** after 0.4 still reads 0:

  ```bash
  gh repo archive Raven-Scout/scout-plugin --yes
  gh repo view Raven-Scout/scout-plugin --json isArchived --jq .isArchived   # true
  ```
- [ ] **7.3 Org profile README.** `Raven-Scout/.github`, `profile/README.md`, has
  `scout-plugin` links on lines 2, 30, 39, 56, 67 and 68 (2026-10-04):
  - Re-point the repo link and the `curl` installer URL to `Raven-Scout/Scout`.
  - Describe the engine as `Raven-Scout/Scout` → `plugin/`.
  - The `og.png` and Pages privacy/terms URLs depend on Open decision M7.
- [ ] **7.4 Close the freeze notice:**

  ```bash
  NOTICE="<freeze-notice issue number from 0.3>"
  gh issue close "$NOTICE" --repo Raven-Scout/scout-plugin --comment "Done — the monorepo is live. Re-point with: claude plugin marketplace remove scout-plugin && claude plugin marketplace add Raven-Scout/Scout && claude plugin install scout@scout-plugin"
  ```

---

## Phase 8 — Close-out checks

```bash
gh repo view Raven-Scout/scout-plugin --json isArchived --jq .isArchived          # true
gh release list --repo Raven-Scout/Scout --limit 5                                 # plugin/v$NEW and the app/v… release
gh api repos/Raven-Scout/Scout/releases/latest --jq .tag_name                      # app/v…
cd "$R" && git switch main && git pull --ff-only
grep -rn 'Raven-Scout/scout-plugin' README.md install.sh plugin/README.md apps/macos/README.md docs/index.html PRIVACY.md TERMS.md .claude-plugin/ plugin/.claude-plugin/ plugin/engine/scout/scripts/self_update.py
```

The only `scout-plugin` strings left should be:
- the marketplace `name`, and with it `scout@scout-plugin` and the
  `cache/scout-plugin/` paths
- the deliberate migration instructions and old-repo detection
- the GitHub Pages URLs (M7)

---

## Open decisions for Jordan

- **M7: where the website lives.** Pages is served from scout-plugin `main`
  `/docs` today (`raven-scout.github.io/scout-plugin`). The site's source now
  lives in this repo's `docs/`, alongside internal specs and plans. Options:
  - keep the archived repo's Pages as a frozen site (confirm GitHub keeps
    serving an archived repo's Pages)
  - publish Pages from this repo, which exposes `docs/superpowers/**` as site
    pages unless excluded
  - move the site to its own folder or repo

  Until this is decided, these keep pointing at `scout-plugin`:
  - `og:url` and `og:image` in `docs/index.html`
  - the Pages privacy/terms links in the READMEs
  - the org README image
- **M6: `apps/macos/CHANGELOG.md`.** Wire it into `release-app.sh`, which
  today writes notes from `git log` only, or drop the file.
- **The flaky FS-watcher test**
  (`plugin/engine/tests/integration/test_action_items_watch.py::test_watch_emits_completed_line_on_checkbox_flip`).
  Quarantine it (retry or skip-on-CI) before `plugin-test` becomes required,
  or it will randomly block merges.
- **#125's open question: pip / Homebrew / `$PATH` engines.** Part B's
  `EngineLocator` has no fallback for an engine installed with pip, Homebrew,
  or found only on `$PATH`; such an engine reads as `notInstalled` and gates
  the tabs.
- **App release before #125?** If an app release ships from `main` before
  Part B lands, it still resolves `scoutctl` through `ScoutctlLocator`. That
  targets the bash launcher, which writes `.scoutctl-py-cache` into the plugin
  cache dir. Harmless, but Part B replaces it.
- **Marketplace name.** It stays `scout-plugin` (R17), because renaming
  strands every install. Revisit only with a migration path.
