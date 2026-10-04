# Monorepo cutover runbook

> **Revised 2026-10-04 for the swap (Jordan's decision).** The monorepo now
> lands in **`Raven-Scout/scout-plugin`**, which keeps its stars and forks
> (17 and 9, against the app repo's 7 and 2). Then:
> 1. the app repo `Raven-Scout/Scout` is renamed **`Raven-Scout/scout-app-legacy`**
>    and later archived;
> 2. `scout-plugin` is renamed **`Raven-Scout/Scout`**.
>
> Jordan chose this knowing its cost. Reusing the name `Scout` ends GitHub's
> redirect for the app repo, so every pre-swap `Raven-Scout/Scout#N`,
> `…/pull/N` or `…/issues/N` link silently opens a *different* item (see
> **Numbering**). Phases 0–1 are unchanged. Phases 2–11 replace the
> absorb-into-Scout plan's Phases 2–8.
>
> **This also supersedes the commands in Task 1 and Task 14 of
> [`2026-09-03-monorepo-consolidation.md`](2026-09-03-monorepo-consolidation.md).**
> That plan stays as the historical record. Do not run its Task 1 / Task 14
> commands: they install `scout@Scout` (the `@` suffix is the marketplace
> NAME, which stays `scout-plugin`), run `versioning set 0.10.0` (a downgrade:
> nothing guards against setting a lower version), cite versions and a worktree
> path that no longer exist, and run `claude plugin marketplace remove
> scout-plugin` against the real config with no isolation (that uninstalls the
> plugin Jordan's scheduled runs use).

Everything below is a checklist. Steps marked **[J]** push, post, publish,
rename, transfer or archive on GitHub. Each one needs **Jordan's direct
confirmation in the migration session, at the time it runs**. An earlier
approval, or one relayed by another session, doesn't count. Agents never run
`apps/macos/scripts/release-app.sh` or `plugin/scripts/release-plugin.sh`:
both are live on Jordan's Mac (Developer ID identity + `scout-notary`
profile). Everything not marked [J] is local.

**Names.** One repo name changes meaning partway through:

| | Before Phase 4 | After Phase 4 |
|---|---|---|
| the macOS app's repo | `Raven-Scout/Scout` | `Raven-Scout/scout-app-legacy` (archived in Phase 10) |
| the monorepo (the survivor) | `Raven-Scout/scout-plugin` | `Raven-Scout/Scout` |

Each command below names the repo as it is called *when that step runs*.

**Hard rules**

- **Never create a repository named `scout-plugin` in `Raven-Scout`** after
  Phase 4.2. Equally, never "restore" the old name. GitHub keeps
  `Raven-Scout/scout-plugin` redirecting to the survivor only while that name
  is unused. Every existing plugin install's marketplace, `/scout-update` and
  self-update check depends on that redirect.
- **Never `git push --tags` from a clone of the old app repo**:
  - this means W, `~/scout-app`, and the Part B/C worktrees;
  - never push a bare `v*` tag anywhere;
  - the app's bare `v0.5.0`–`v0.13.0` share names with 10 of the plugin's
    bare tags, which live in the survivor.
- **From Phase 3.5 until Phase 9 is done, nothing pulls `~/scout-plugin` on
  Jordan's Mac.**
  - That means no `/scout-update` (its Step 0.5 runs
    `git -C ~/scout-plugin pull --ff-only`) and no manual pull.
  - Once scout-plugin's `main` is the monorepo, that pull moves `engine/` to
    `plugin/engine/`.
  - That breaks every editable install of `~/scout-plugin/engine` until 9.3
    re-points them: `~/.local/bin/scoutctl`, `engine.json`, the launchd runs,
    and the running command's own `SCOUTCTL`.
  - Nothing scheduled pulls it (checked 2026-10-04). Phase 9 runs right after
    Phase 4.
- **From Phase 4.1 on, always pass `--repo` to `gh`.** `gh` infers the repo
  from `origin`, and the same number means different items in the two repos.
  4.1a re-points `~/scout-app`'s `origin`; the coordinator tells every session.
- **Phases 3 and 4 run back to back.** Between Phase 3.5 (the landing merge)
  and Phase 4.2 (the second rename), new users' `curl …/Raven-Scout/Scout/main/install.sh`
  hits the app repo, which has no `install.sh`. Existing plugin users are fine
  throughout.

**Conventions**

- Run the blocks in **bash** (`bash` first if your shell is zsh, where unquoted
  `$var` doesn't word-split).
- `W` is a checkout of branch `migrate/monorepo`. Today that is
  `W=/Users/jordanburger/.scout-worktrees/scout-app-monorepo`.
  - It is a worktree of `~/scout-app`, a clone of the app repo, so `origin`
    is `Raven-Scout/Scout`.
  - After Phase 4.1 that URL names the survivor. Phase 4.5 handles this.
- `R` is a clean clone of the survivor, made after Phase 4, for Phases 5–7
  and 11:

  ```bash
  git clone https://github.com/Raven-Scout/Scout.git ~/.scout-worktrees/scout-release
  ```

  - It must not be `W`, `~/scout-app`, or `~/scout-plugin`. `~/scout-plugin`
    is Jordan's live engine checkout (Phase 9), and the release script
    switches branches.
  - It needs the engine venv:
    `cd "$R/plugin/engine" && uv venv --python 3.12 && uv pip install -e ".[dev]"`.
- **`legacyize`**: use it on any text copied from the app repo into the
  survivor (release notes, PR and issue bodies). There, a bare `#N` would
  autolink to a scout-plugin item and leave a "mentioned this" backlink on it.
  Define it in each shell that needs it:

  ```bash
  legacyize() {   # stdin → stdout
    perl -pe 's{(^|[^A-Za-z0-9/._&-])#(\d+)}{$1Raven-Scout/scout-app-legacy#$2}g;
              s{Raven-Scout/Scout#(\d+)}{Raven-Scout/scout-app-legacy#$1}g;
              s{github\.com/Raven-Scout/Scout/(pull|issues|compare|releases/tag)/}{github.com/Raven-Scout/scout-app-legacy/$1/}g'
  }
  ```

  It leaves HTML entities (`&#39;`) alone. It still rewrites a hex colour like
  `#123456`, a `step #2`, or a line-start `#1 Heading`, so read the diff before
  posting.
- **No literal version numbers.** Every version is derived when you run the
  step. If a step tells you to type a version, it first shows the command that
  produces it.
- **Tags in the survivor:**
  - bare `v*` tags are the **plugin's** pre-monorepo releases, `v0.4.0`–`v0.13.0`;
  - the app's releases are re-tagged `app/vX.Y.Z` in Phase 3.3;
  - new releases are `plugin/vX.Y.Z` and `app/vX.Y.Z`.
  - `git subtree pull` fetches no tags.

## Numbering (read before writing any link)

- The survivor keeps **scout-plugin's** numbering: its #1 to #277 and up are
  scout-plugin items. The app repo had its own #1–#132, and every one of
  those numbers also exists in the survivor.
- An app item from before the swap (`Raven-Scout/Scout#N`, `…/pull/N`,
  `…/issues/N`, or a bare `#N` in an `apps/macos/` file or a pre-move app
  commit) means **`Raven-Scout/scout-app-legacy#N`**. For transferred issues,
  that legacy URL redirects to the issue's new home.
- `Raven-Scout/scout-plugin#N` links stay correct: they redirect to the same
  item at `Raven-Scout/Scout#N`.
- Phase 2.1 rewrote this repo's links. The root `CLAUDE.md` states the rule.
  Phase 4.4 pins a notice. Links in agent memory and the vault are the
  coordinator's to rewrite; the migration session sent it the list.

---

## 0. Where things stand

- [ ] Branch `migrate/monorepo` is pushed as draft PR `Raven-Scout/Scout#132`
  (after Phase 4, `Raven-Scout/scout-app-legacy#132`). Phase 3.1 closes it and
  re-opens it on `scout-plugin`.
  - Don't trust a SHA written here: both upstreams keep moving.
  - Check the branch's actual sync state with
    `git log -1 --format='%H %s' migrate/monorepo`.
  - Check `gh pr view 132 --repo Raven-Scout/Scout --json mergeStateStatus,baseRefOid,headRefOid`
    for what it was last synced to.
- [ ] The swap's branch changes (Phase 2.1) are committed. Find the commit with
  `git log --oneline --grep='swap' -- install.sh`.
- [ ] Last full local verification, at 4caf4d6 (re-run 1.4 after every
  re-sync):
  - the engine suite: 3162 passed, 14 skipped (the one known-flaky FS-watcher
    test passes when re-run alone)
  - ruff, ruff format, mypy
  - `versioning check` → `0.13.0`
  - every `run:` step of `contract.yml`'s verify job under `bash -e -o pipefail`,
    plus the canonical `--check` steps
  - shellcheck on every script that plugin-lint checks
  - all workflows parse
  - `bash install.sh --check`
  - the macOS `ScoutTests`: 973 tests in 151 suites, TEST SUCCEEDED
- [ ] Verified in GitHub Actions: #132's first runs, 29/29 green on the app
  repo. **Not yet verified:**
  - Actions on the survivor;
  - rulesets;
  - an install from the pushed repo;
  - GitHub's rename redirects;
  - both release scripts end to end.
- [ ] Local-only records (gitignored), under
  `.superpowers/sdd/2026-09-03-monorepo-consolidation/` in `W`:
  - `progress.md`: the controller ledger, rulings R1–R39
  - `final-review.md`
  - `fix-wave-report.md`

Both repos will keep moving until cutover, so **Phase 1 runs again right
before Phase 3**.

---

## Phase 0 — Clear the runway (scout-plugin) [J] — DONE 2026-10-04

- [x] **0.1–0.2 External scout-plugin PRs.** #247 and #264 were merged. #216,
  #194, #180, #176 and #175 were closed with a pointer to re-open under
  `plugin/` in `Raven-Scout/Scout`.
  - That pointer stays true after the swap.
  - Each comment's "Design: Raven-Scout/Scout#99" link does not; Phase 10.5
    fixes it.
- [x] **0.3 Notice.** Posted and pinned as scout-plugin #277. That number
  survives the swap. Phase 10.4 rewrites it for the swap and closes it.
- [ ] **0.4 Gate, re-run right before Phase 3.5:** no external open PRs on
  scout-plugin.

  ```bash
  gh pr list --repo Raven-Scout/scout-plugin --state open --limit 100 \
    --json author --jq '[.[] | select(.author.login != "jordanrburger")] | length'   # expect 0
  ```

  Jordan's own open scout-plugin PRs don't block. Snapshot 2026-10-04: #275
  and #261. They stay in the survivor and are rebased into `plugin/` in
  Phase 5.3.
- **Merge freeze** on both repos until Phase 4 completes, except Phase 0 PRs
  and this migration (Jordan, 2026-10-04).

---

## Phase 1 — Re-sync the branch (repeat right before Phase 3)

Only needed until Phase 3.5's landing merge. After that the two trees are one
repo, and the app repo is frozen until it is archived.

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
      if git merge-file -p "apps/macos/$f" /tmp/base-$f /tmp/theirs-$f > /tmp/out-$f; then
        cp /tmp/out-$f "apps/macos/$f"
        git checkout HEAD -- "$f"                  # root stays as the branch has it
        git add "$f" "apps/macos/$f"
      else
        echo "CONFLICT in $f — resolve the markers in /tmp/out-$f, cp it over apps/macos/$f, then git add both by hand" >&2
      fi
    done
    git diff --cached --quiet HEAD -- CLAUDE.md README.md && echo "root docs unchanged"
    ```

    If `merge-file` exits non-zero, the loop deliberately skips `git add` for
    that file — resolve the markers in `/tmp/out-$f` before copying and
    staging it yourself. Never let the loop add a file it didn't actually
    merge cleanly.
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
  # No run-contract.py harness ships in this repo. Extract every `run:` block
  # of contract.yml's `verify` job and execute it in order under
  # `bash -e -o pipefail`, in each step's `working-directory:` (default: the
  # repo root), with the job's `env:` exported and plugin/engine/.venv/bin first
  # on PATH — that's the actual verification, not a stand-in script. Skip the
  # three setup steps locally (`uv python install`, `uv venv`, `uv pip install`):
  # the venv already exists, `uv venv` would replace it, and `uv python install`
  # leaves a ~/.local/bin/python3.12 shim that shadows Homebrew's.
  shellcheck plugin/engine/bin/scoutctl
  shellcheck -S error install.sh plugin/scripts/*.sh apps/macos/scripts/*.sh .github/scripts/changed-paths.sh
  for f in .github/workflows/*.yml; do plugin/engine/.venv/bin/python -c 'import sys,yaml;yaml.safe_load(open(sys.argv[1]))' "$f" || echo "BAD $f"; done
  bash install.sh --check
  df -h /System/Volumes/Data          # stop if under 5 GiB free
  cd apps/macos && xcodebuild test -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' \
    -only-testing:ScoutTests -derivedDataPath "$W/.superpowers/derived-data" COMPILER_INDEX_STORE_ENABLE=NO \
    CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY="" 2>&1 | grep -E '\*\* TEST (SUCCEEDED|FAILED)|Executed|tests? in'
  ```

  See the extraction note above the `shellcheck` line: there is no packaged
  contract harness to fall back to, so this manual extraction (every `run:`
  block of `contract.yml`'s `verify` job, from the repo root, under
  `bash -e -o pipefail`) is the only way to run it locally.

---

## Phase 2 — Swap prerequisites (before landing)

- [x] **2.1 Branch changes for the swap** (one commit; find it with
  `git log --oneline --grep='swap' -- install.sh`):
  - **Former-name acceptance.** `install.sh`'s `check_marketplace_source` and
    `/scout-update` Step 0.2 accept `Raven-Scout/scout-plugin`, which GitHub
    redirects to the survivor, as well as `Raven-Scout/Scout`. Any other repo
    still stops with the re-point commands. Checked against fixture
    `known_marketplaces.json` files:

    | Marketplace source | `install.sh --check` | Step 0.2 |
    |---|---|---|
    | new name | exit 0 | `MARKETPLACE_OK` |
    | old name, `github` | exit 0 | `MARKETPLACE_OK` |
    | old name, `git` URL, mixed case | exit 0 | `MARKETPLACE_OK` |
    | a fork | exit 1 + re-point | `MARKETPLACE_ARCHIVED` |
    | legacy-layout directory | exit 0 + note | `LEGACY_CHECKOUT` |
    | absent | exit 0 | `MARKETPLACE_OK` |

    Both messages now tell a legacy checkout to `git pull`, since the old URL
    redirects.
  - **`release-app.sh` reads `app/v*` tags only** and stops if there are none.
    The bare-`v*` fallback would have read the plugin's `v0.13.0` as the app's
    last release.
  - **Comments.** The ones in `gen-render-history.py` and `test_vault_drift.py`
    now say bare `v*` = plugin, the opposite of the absorb plan. In a full
    survivor clone the render-history parity test runs again: its tag guard
    passes on scout-plugin's own `v0.11.0`. In an app clone it still skips.
    `test_self_update.py`'s docstring is updated too.
  - **Links.** Every `Raven-Scout/Scout#N`, `…/pull/N`, `…/issues/N` and
    `…/releases/(tag|download)/…`, and every bare `Scout#N`, now names
    `Raven-Scout/scout-app-legacy`: 75 links in 13 files, outside this
    runbook. One historical command, `gh pr view 123`, also gets
    `--repo Raven-Scout/scout-app-legacy`. The root `CLAUDE.md` gains the
    numbering rule.
  - **Root `README.md`:** "Installed from scout-plugin? Nothing to do."
  - **Pages URLs** are re-pointed to the post-rename site,
    `raven-scout.github.io/Scout/`:
    - `og:url` and `og:image` in `docs/index.html`;
    - the privacy and terms links in `apps/macos/README.md` and
      `plugin/README.md`.

  Re-run this after every Phase 1, since new docs can bring new links. It must
  print nothing:

  ```bash
  cd "$W"
  git grep -nE 'Scout#[0-9]|github\.com/Raven-Scout/Scout/(pull|issues|releases/(tag|download))/' \
    -- ':!docs/superpowers/plans/2026-10-04-monorepo-cutover-runbook.md' ':!*.jsonl' | grep -v scout-app-legacy
  ```

- [ ] **2.2 Make the existing-user sandbox now, before anything lands.** It
  holds a plugin installed from today's `scout-plugin`, so Phases 4.3 and 7.1
  can test the real upgrade path through the rename redirect.
  - **Never run `claude plugin marketplace add/remove/update` or
    `claude plugin install/uninstall` against the real config.** Jordan's
    live marketplace is also named `scout-plugin`, and removing it
    uninstalls the plugin his scheduled runs use (R16).
  - Every command carries `CLAUDE_CONFIG_DIR=<sandbox>`.
  - The R27 guard brackets every sandbox phase. Whole-file hashes are not a
    valid guard, because Claude Code rewrites other entries in the background.

  ```bash
  cat > ~/.scout-worktrees/r27-guard.py <<'PY'
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
  SB_OLD="$(mktemp -d "$HOME/.scout-worktrees/claude-sandbox-old.XXXXXX")"; echo "$SB_OLD" > ~/.scout-worktrees/sb-old.path
  python3 ~/.scout-worktrees/r27-guard.py "$SB_OLD" > ~/.scout-worktrees/r27-before.txt; cat ~/.scout-worktrees/r27-before.txt   # "references … : False"
  CLAUDE_CONFIG_DIR="$SB_OLD" claude plugin marketplace list            # must NOT list scout-plugin; if it does, STOP
  CLAUDE_CONFIG_DIR="$SB_OLD" claude plugin marketplace add Raven-Scout/scout-plugin
  CLAUDE_CONFIG_DIR="$SB_OLD" claude plugin install scout@scout-plugin
  CLAUDE_CONFIG_DIR="$SB_OLD" claude plugin list --json | grep -A4 '"scout@scout-plugin"'   # note version + installPath (inside $SB_OLD)
  python3 ~/.scout-worktrees/r27-guard.py "$SB_OLD" | diff ~/.scout-worktrees/r27-before.txt - && echo "real config untouched"
  ```

- [ ] **2.3 Merge order with Part B (#125) and Part C (#128).**
  - The migration lands first.
  - Part B and Part C are open on the app repo, so after Phase 4 they are
    `scout-app-legacy#125` and `#128`. Their session re-opens them on the
    survivor in Phase 5.2.
  - In that rebase, Part B moves `ScoutctlLocator`'s priority and
    never-probe-bin tests into `EngineLocatorTests`, deletes
    `ScoutctlLocator`, re-points `findScoutctl()`, and adds the monorepo
    `plugin/` as a dev-checkout candidate. The migration branch does not touch
    any of that (R37).
  - Part C stacks on Part B. In its rebase it switches `bundle-engine.sh` to
    `git archive HEAD:plugin`.
  - Notify that session ("Build and test Scout.app Part B, then open its PR";
    find it with `ListAgents`) as soon as Phase 4 completes. Send:
    - the landing merge SHA;
    - that the repo is now `Raven-Scout/Scout`, formerly scout-plugin, and its
      PR numbers are scout-plugin's;
    - that the app lives in `apps/macos/`, and `ci.yml` is now `app-ci.yml`;
    - that the required checks are about to be `app-ci`, `plugin-test`,
      `plugin-lint` and `contract`;
    - that new `Scout/Engine/*` files land under `apps/macos/Scout/Engine/`
      with `merge.directoryRenames=true`;
    - the Phase 5.2 recipe.

---

## Phase 3 — Land the monorepo in scout-plugin [J]

- [ ] **3.0 Gate (local).** Phase 1 just ran and is committed, and 1.4 is
  green. Nothing moved since:

  ```bash
  cd "$W" && git status --porcelain                                          # nothing
  git fetch origin && git merge-base --is-ancestor origin/main HEAD && echo "app main fully merged"
  UP=$(git ls-remote https://github.com/Raven-Scout/scout-plugin.git refs/heads/main | cut -f1)
  git merge-base --is-ancestor "$UP" HEAD && echo "scout-plugin main fully merged"            # if UP is unknown locally, Phase 1 is stale
  gh repo view Raven-Scout/scout-app-legacy >/dev/null 2>&1 && echo "STOP: the legacy name is taken" || echo "legacy name free"
  # Closing keywords in app commits act on scout-plugin's numbers once they reach its default branch:
  git log --format='%B' migrate/monorepo ^"$UP" \
    | grep -ioE '\b(close[sd]?|fix(e[sd])?|resolve[sd]?):? +(([A-Za-z0-9-]+/[A-Za-z0-9._-]+)?#[0-9]+|https://github\.com/[^ )]+/(issues|pull)/[0-9]+)' \
    | sort -u > /tmp/closing-refs.txt
  grep -vE '/|https' /tmp/closing-refs.txt | grep -oE '[0-9]+$' | sort -un | while read -r n; do
      gh api "repos/Raven-Scout/scout-plugin/issues/$n" --jq '"#\(.number) \(.state) \(.title[:60])"'
    done                                                                     # bare #N = scout-plugin's numbers: every line must say "closed"
  grep -E '/|https' /tmp/closing-refs.txt                                    # qualified forms: check each target by hand (2026-10-04: none)
  ```

  - **Closing keywords.** On 2026-10-04 they targeted #9, #10, #13, #14, #16,
    #17, #41 and #83, all already closed. An open one would be **closed by
    the merge**, so stop and decide with Jordan.
  - **Accepted cost (tell Jordan at 3.1).** Pushing the app's history makes
    its "(#N)" commit subjects reference scout-plugin's #1–#130. GitHub may
    add "referenced this in commit" entries to those timelines, including 10
    items by external authors (all closed).
    - Reference entries don't send notifications as far as we know, but it is
      irreversible.
    - To be sure first, push an old-dated commit saying "(#1)" to a throwaway
      repo that has an issue #1.

- [ ] **3.1 [J] Push the branch to scout-plugin and open the landing PR
  there.** Push the branch only, with no tags.

  ```bash
  cd "$W"
  git push https://github.com/Raven-Scout/scout-plugin.git migrate/monorepo:migrate/monorepo
  gh pr create --repo Raven-Scout/scout-plugin --base main --head migrate/monorepo --draft \
    --title "refactor: the Scout monorepo — the app moves in (apps/macos/), the plugin moves to plugin/" \
    --body-file "$W/.superpowers/landing-pr-body.md"     # app items written as scout-app-legacy#N, plugin items as bare #N
  NEW_PR="<number it printed>"; echo "$NEW_PR" > ~/.scout-worktrees/landing-pr.txt
  gh pr comment 132 --repo Raven-Scout/Scout --body "Moved to Raven-Scout/scout-plugin#$NEW_PR: the monorepo lands in scout-plugin, which is then renamed Raven-Scout/Scout (this repo becomes Raven-Scout/scout-app-legacy). This PR's review history stays here."
  gh pr close 132 --repo Raven-Scout/Scout
  gh pr close 99 --repo Raven-Scout/Scout --comment "Its spec and plan are part of Raven-Scout/scout-plugin#$NEW_PR."
  ```

  - **Write every app item in that body as `Raven-Scout/scout-app-legacy#N`.**
    It is created on scout-plugin, so a bare `#125` links a scout-plugin item
    immediately, and a `Raven-Scout/Scout#N` goes wrong at 4.2.
    `.superpowers/landing-pr-body.md` already follows this rule.
  - The PR's diff is computed against the last subtree-merged scout-plugin
    commit, so it reads as "everything moves into `plugin/`, the app arrives
    in `apps/macos/`". Review it commit by commit.
  - Pushing to `migrate/**` also starts push runs of `contract`, `plugin-test`
    and `plugin-lint`. That's expected.

- [ ] **3.2 Read the checks.**

  ```bash
  gh pr checks "$NEW_PR" --repo Raven-Scout/scout-plugin --watch
  ```

  - Expect four gate checks, `app-ci`, `plugin-test`, `plugin-lint` and
    `contract`, all passing.
  - Under each gate, the gated jobs must have actually **run**, because this
    PR touches every area: `ScoutTests`, `test (…)` + `coverage`, `lint`,
    `verify`.
  - The PR's merge ref carries only the monorepo's workflows, so
    scout-plugin's old ones don't run.
  - If `plugin-test` fails only on the FS-watcher test, re-run the job.

- [ ] **3.3 [J] Seed the app's release tags in scout-plugin.**
  - Every app release tag is re-created as `app/<tag>` on the same commit and
    pushed by explicit refspec. Its commits arrived with 3.1.
  - The old app repo's `v0.14.0` → `app/v0.14.0`, which `release-app.sh` now
    needs.

  ```bash
  cd "$W"
  for t in $(git ls-remote --tags --refs origin 'v*' | sed 's#.*refs/tags/##' | sort -V); do
    c=$(git rev-parse "$t^{commit}")
    if git merge-base --is-ancestor "$c" migrate/monorepo; then
      git tag -a "app/$t" "$c" -m "Scout.app $t (re-tagged at the monorepo cutover from the app repo's $t)"
    else
      echo "SKIP $t: $c is not in the branch history"
    fi
  done
  git tag --list 'app/v*' | wc -l            # 43 app tags existed on 2026-10-04
  git push https://github.com/Raven-Scout/scout-plugin.git $(git tag --list 'app/v*' | sed 's#^#refs/tags/#')
  ```

- [ ] **3.4 [J] Re-publish the newest app DMG as the survivor's Latest
  release.** After Phase 4, `/releases/latest` (the website's and both
  READMEs' "download the app" link) then still serves a DMG.
  - This is **not** a new build: it re-hosts the DMG that already shipped. So
    the "no app release before #125" rule (7.2) doesn't apply.
  - scout-plugin's own `/releases/latest` switches to the app until the
    rename. Nothing reads it.

  ```bash
  cd "$W"
  LAST=$(git ls-remote --tags --refs origin 'v*' | sed 's#.*refs/tags/##' | sort -V | tail -1)   # v0.14.0 on 2026-10-04
  D=$(mktemp -d)
  gh release download "$LAST" --repo Raven-Scout/Scout --pattern 'Scout-*.dmg' --dir "$D"
  gh release view "$LAST" --repo Raven-Scout/Scout --json body --jq .body | legacyize > "$D/notes.md"   # define legacyize first (Conventions)
  less "$D/notes.md"                                                                                   # its (#N)s and compare links now name scout-app-legacy
  printf '\n\n---\nRe-published at the monorepo cutover from the app repo'\''s %s release (now Raven-Scout/scout-app-legacy). Same DMG, not a new build.\n' "$LAST" >> "$D/notes.md"
  gh release create "app/$LAST" --repo Raven-Scout/scout-plugin --verify-tag --latest \
    --title "Scout.app ${LAST#v}" --notes-file "$D/notes.md" "$D"/Scout-*.dmg
  gh api "repos/Raven-Scout/Scout/releases/tags/$LAST" --jq '[.assets[] | {name, size}]'
  gh api "repos/Raven-Scout/scout-plugin/releases/tags/app/$LAST" --jq '[.assets[] | {name, size}]'   # same name and size
  ```

  **Undo for 3.3–3.4,** if the landing is abandoned before 3.5 [J]:

  ```bash
  gh release delete "app/$LAST" --repo Raven-Scout/scout-plugin --yes      # scout-plugin's Latest falls back to its own newest release
  git push https://github.com/Raven-Scout/scout-plugin.git --delete $(git tag --list 'app/v*' | sed 's#^#refs/tags/#')
  ```

- [ ] **3.5 [J] Merge the landing PR, then go straight to Phase 4.**
  - Re-run the 0.4 gate first.
  - scout-plugin's ruleset `main` allows merge commits and needs 0 approvals.
    There is no CODEOWNERS file (checked 2026-10-04), so Jordan can merge his
    own PR. If GitHub still asks for an approval (the ruleset's
    "unattributed changes" option), stop: changing the ruleset is its own [J]
    step.
  - Use **"Create a merge commit"**. **Never squash or rebase.** The plugin
    history arrived by SHA-preserving subtree merges (R23), and the app's whole
    history rides in with this merge; a squash flattens both and a rebase
    rewrites every SHA.

  ```bash
  gh pr ready "$NEW_PR" --repo Raven-Scout/scout-plugin
  gh pr merge "$NEW_PR" --repo Raven-Scout/scout-plugin --merge
  ```

  From this moment, existing plugin users who update pull the monorepo. Their
  marketplace clone fast-forwards, and the root `marketplace.json` points at
  `./plugin`. New-user installs are broken until 4.2.

---

## Phase 4 — The swap [J] (two renames, back to back)

- [ ] **4.0 Preflight** (run before 3.5).
  - Pick a quiet window for 3.5 → Phase 4 → Phase 9: no scheduled slot due
    for about two hours (`~/.local/bin/scoutctl schedule list-upcoming --json | head`).
    Phase 9 runs right after Phase 4 (see Hard rules).
  - Ask the coordinator to pause every session that pushes to either repo.
  - Snapshot the app repo's open work:

  ```bash
  gh pr list --repo Raven-Scout/Scout --state open --limit 100 --json number,headRefName,isDraft,title > ~/.scout-worktrees/swap-legacy-open-prs.json
  gh issue list --repo Raven-Scout/Scout --state open --limit 500 --json number,title,labels > ~/.scout-worktrees/swap-legacy-open-issues.json
  jq length ~/.scout-worktrees/swap-legacy-open-prs.json ~/.scout-worktrees/swap-legacy-open-issues.json   # 8 PRs once 3.1 has closed #132 and #99; 15 issues (2026-10-04)
  ```

- [ ] **4.1 [J] Rename the app repo.**

  ```bash
  gh repo rename scout-app-legacy --repo Raven-Scout/Scout --yes
  ```

- [ ] **4.1a Re-point the old app clone, still between the renames.**
  - `~/scout-app` is the only clone whose `origin` is the app repo (checked
    2026-10-04). Its 20 worktrees share its config, including `W` and the
    Part B/C worktrees.
  - Re-pointed now, its `fetch`, `push` and `gh` keep meaning the app repo,
    and its bare `v*` tags never meet the survivor's.

  ```bash
  git -C ~/scout-app remote set-url origin https://github.com/Raven-Scout/scout-app-legacy.git
  git -C ~/scout-app ls-remote --exit-code origin refs/heads/main >/dev/null && echo "old clone → scout-app-legacy"
  ```

- [ ] **4.2 [J] Immediately, rename scout-plugin.**

  ```bash
  gh repo rename Scout --repo Raven-Scout/scout-plugin --yes
  ```

- [ ] **4.3 Verify the swap and the redirects.**

  ```bash
  gh repo view Raven-Scout/Scout --json nameWithOwner,stargazerCount,forkCount --jq '"\(.nameWithOwner) stars=\(.stargazerCount) forks=\(.forkCount)"'   # ≥17 / ≥9
  gh repo view Raven-Scout/scout-app-legacy --json nameWithOwner,stargazerCount --jq '"\(.nameWithOwner) stars=\(.stargazerCount)"'
  [ "$(git ls-remote https://github.com/Raven-Scout/scout-plugin.git refs/heads/main)" = "$(git ls-remote https://github.com/Raven-Scout/Scout.git refs/heads/main)" ] && echo "git redirect OK"
  curl -sIL -o /dev/null -w '%{http_code} %{url_effective}\n' https://raw.githubusercontent.com/Raven-Scout/scout-plugin/main/.claude-plugin/marketplace.json
  curl -fsSIL -o /dev/null -w '%{http_code}\n' https://raw.githubusercontent.com/Raven-Scout/Scout/main/install.sh   # 200
  gh api repos/Raven-Scout/Scout/releases/latest --jq '.tag_name, [.assets[].name]'                                 # app/v…, Scout-….dmg
  ```

  The raw `scout-plugin` URL is what v0.13.0 installs' self-update check
  reads. If it doesn't redirect (no 200), those installs stop *seeing* update
  notices. Their marketplace still updates them through the git redirect. Say
  so in #277 (Phase 10.4); it doesn't block.

  Then the existing-user path, in the 2.2 sandbox:

  ```bash
  SB_OLD=$(cat ~/.scout-worktrees/sb-old.path)
  CLAUDE_CONFIG_DIR="$SB_OLD" claude plugin marketplace update scout-plugin      # through the redirect
  CLAUDE_CONFIG_DIR="$SB_OLD" claude plugin update scout@scout-plugin
  CLAUDE_CONFIG_DIR="$SB_OLD" claude plugin list --json | grep -A4 '"scout@scout-plugin"'
  curl -fsSL https://raw.githubusercontent.com/Raven-Scout/Scout/main/install.sh | CLAUDE_CONFIG_DIR="$SB_OLD" bash -s -- --check   # "preconditions OK", no re-point error
  python3 ~/.scout-worktrees/r27-guard.py "$SB_OLD" | diff ~/.scout-worktrees/r27-before.txt - && echo "real config untouched"
  ```

  - The update must succeed.
  - The version stays at today's until the first `plugin/v*` release (Phase
    7.1), which moves it onto the `plugin/` tree.
  - If the update fails here, stop: every existing user is in the same state.
    Fix forward (for example, a re-point note in #277) before anything else.

- [ ] **4.4 [J] Pin the numbering notice on the survivor.**

  ```bash
  cat > /tmp/numbering-notice.md <<'EOF'
  **This repo was renamed on DATE.** It was `Raven-Scout/scout-plugin` and took the name `Raven-Scout/Scout` when the macOS app moved in. The app now lives in `apps/macos/` and the plugin in `plugin/`. The app's own pre-move repo is archived as [`Raven-Scout/scout-app-legacy`](https://github.com/Raven-Scout/scout-app-legacy).

  **Issue and PR numbers below #THIS were opened as scout-plugin items.** The old app repo had its own #1–#132. Any `Raven-Scout/Scout#N` link written before DATE, and any bare `#N` in an app commit or an `apps/macos/` file, means `Raven-Scout/scout-app-legacy#N`. Open app issues were transferred here with new numbers, and their old URLs redirect to them.

  Old `Raven-Scout/scout-plugin` URLs, clones and Claude Code marketplaces keep working through GitHub's redirect. Nothing to do.
  EOF
  URL=$(gh issue create --repo Raven-Scout/Scout --title "Read me: this repo was scout-plugin; old app issue numbers live in scout-app-legacy" --body-file /tmp/numbering-notice.md)
  N=${URL##*/}
  sed -e "s/#THIS/#$N/" -e "s/DATE/$(TZ=America/New_York date +%F)/g" /tmp/numbering-notice.md > /tmp/numbering-notice.final.md
  gh issue edit "$N" --repo Raven-Scout/Scout --body-file /tmp/numbering-notice.final.md
  gh issue pin "$N" --repo Raven-Scout/Scout
  ```

- [ ] **4.5 Tell the sessions.** Through the coordinator, tell every session
  working in a `~/scout-app` worktree:
  - its `origin` is now `Raven-Scout/scout-app-legacy` (4.1a);
  - new work starts from a survivor clone;
  - from now on, pass `--repo` to every `gh` call.

  Any other clone of the app repo, on another machine or a fresh one, needs
  the same `set-url`. Its `origin` now silently names the survivor.

- [ ] **4.6 [J] Fix the Phase 0 closing comments** on #216, #194, #180, #176
  and #175 (all external contributors' PRs).
  - They say three things that are now wrong:
    - "Raven-Scout/Scout#132", which now opens scout-plugin's own #132;
    - "Once #132 merges", where the bare number already meant scout-plugin's;
    - "this repo gets archived once that lands".
  - Correct them in place, then post a short follow-up so the authors hear
    it:

  ```bash
  LANDED=$(cat ~/.scout-worktrees/landing-pr.txt)
  for n in 216 194 180 176 175; do
    gh api "repos/Raven-Scout/Scout/issues/$n/comments" \
      --jq '.[] | select(.user.login=="jordanrburger" and (.body|contains("#132"))) | .id' |
    while read -r id; do
      gh api "repos/Raven-Scout/Scout/issues/comments/$id" --jq .body \
        | sed -e "s#Raven-Scout/Scout\#132#Raven-Scout/Scout\#$LANDED#g" -e "s#Once \#132 merges#Now that \#$LANDED has merged#" \
              -e 's#and this repo gets archived once that lands#and this repo was renamed Raven-Scout/Scout when it landed#' > /tmp/c-$id.md
      grep -c '#132\|archived' /tmp/c-$id.md                        # 0
      gh api --method PATCH "repos/Raven-Scout/Scout/issues/comments/$id" -F body=@/tmp/c-$id.md --jq .html_url
    done
    gh pr comment "$n" --repo Raven-Scout/Scout --body "Update: the move landed in #$LANDED. Instead of being archived, this repo was renamed **Raven-Scout/Scout**, so you can re-open your change right here, with its files under \`plugin/\`."
  done
  ```

- [ ] **4.7 Now run Phase 9** (Jordan's machine), before anything else. Then
  continue with Phase 5.

---

## Phase 5 — Carry open work across [J]

- [ ] **5.1 [J] Transfer the app repo's open issues** (15 on 2026-10-04).
  - Copy the labels first, so they survive the transfer.
  - Each transferred issue gets a new number. Its legacy URL redirects to
    the new one.

  ```bash
  gh label clone Raven-Scout/scout-app-legacy --repo Raven-Scout/Scout
  # First, on the legacy repo (a no-op in meaning there), qualify every #N in the bodies and comments,
  # so nothing transferred autolinks to a scout-plugin item. Define legacyize first (Conventions).
  for n in $(jq -r '.[].number' ~/.scout-worktrees/swap-legacy-open-issues.json); do
    gh issue view "$n" --repo Raven-Scout/scout-app-legacy --json body --jq .body | legacyize > /tmp/i-$n.md
    gh issue edit "$n" --repo Raven-Scout/scout-app-legacy --body-file /tmp/i-$n.md
    gh api "repos/Raven-Scout/scout-app-legacy/issues/$n/comments" --paginate --jq '.[] | select(.body|test("#[0-9]")) | .id' |
    while read -r id; do
      gh api "repos/Raven-Scout/scout-app-legacy/issues/comments/$id" --jq .body | legacyize > /tmp/ic-$id.md
      gh api --method PATCH "repos/Raven-Scout/scout-app-legacy/issues/comments/$id" -F body=@/tmp/ic-$id.md --jq .html_url
    done
  done
  for n in $(jq -r '.[].number' ~/.scout-worktrees/swap-legacy-open-issues.json); do
    new=$(gh issue transfer "$n" Raven-Scout/Scout --repo Raven-Scout/scout-app-legacy)
    echo "scout-app-legacy#$n -> $new" | tee -a ~/.scout-worktrees/swap-issue-map.txt
  done
  gh issue list --repo Raven-Scout/scout-app-legacy --state open --json number --jq length   # 0
  ```

  After the first transfer, check that its labels and milestone came along.
  Fix the rest by hand if not.

- [ ] **5.2 [J] Re-open the app repo's open PRs on the survivor.**
  - On 2026-10-04, after 3.1 closes #132 and #99, these are #131, #129,
    #128, #127, #125, #118, #70 and #68.
  - **#125 and #128 belong to the Part B/C session.** Send it this recipe
    (2.3) rather than doing them for it.
  - For each of the rest, in `R`:

  ```bash
  cd "$R" && git fetch origin
  N="<legacy PR number>"
  read -r B DRAFT < <(gh pr view "$N" --repo Raven-Scout/scout-app-legacy --json headRefName,isDraft --jq '"\(.headRefName) \(.isDraft)"')
  git fetch https://github.com/Raven-Scout/scout-app-legacy.git "$B" && git switch -c "$B" FETCH_HEAD
  git -c merge.directoryRenames=true rebase origin/main
  git ls-files -- Scout ScoutTests Scout.xcodeproj scripts | head      # must print nothing
  if git ls-remote --exit-code origin "refs/heads/$B" >/dev/null; then echo "STOP: $B already exists on the survivor — push under another name"; else git push -u origin "$B"; fi
  { printf 'Re-opened from Raven-Scout/scout-app-legacy#%s (review history there).\n\n' "$N"
    gh pr view "$N" --repo Raven-Scout/scout-app-legacy --json body --jq .body | legacyize; } > /tmp/pr-$N-body.md   # define legacyize first
  gh pr create --repo Raven-Scout/Scout --base main --head "$B" $([ "$DRAFT" = true ] && echo --draft) \
    --title "$(gh pr view "$N" --repo Raven-Scout/scout-app-legacy --json title --jq .title)" --body-file /tmp/pr-$N-body.md
  NEWN="<number it printed>"; echo "scout-app-legacy#$N -> Raven-Scout/Scout#$NEWN" | tee -a ~/.scout-worktrees/swap-pr-map.txt
  gh pr close "$N" --repo Raven-Scout/scout-app-legacy --comment "Re-opened as Raven-Scout/Scout#$NEWN after the monorepo move."
  ```

  - Rebase conflicts are likely only around `AppState.swift`,
    `ConnectorHealthServiceTests`, `CLAUDE.md`/`README.md`, or CI.
  - The docs-only PRs (#127, #118, #68) rebase cleanly, because `docs/`
    stayed at the root.
  - #70 edits `.github/workflows/ci.yml`. Port that change into `app-ci.yml`
    by hand.

- [ ] **5.3 [J] Rebase the survivor's own open PRs into `plugin/`.** These are
  Jordan's: #275 and #261 on 2026-10-04. Each one:

  ```bash
  cd "$R" && git fetch origin && git switch "<its branch>"
  git -c merge.directoryRenames=true rebase origin/main
  git diff --name-only origin/main... | grep -v '^plugin/\|^docs/'     # anything outside plugin/ or docs/ needs a look
  git push --force-with-lease
  ```

- [ ] **5.4 Notify.**
  - **Part B/C:** the 2.3 list.
  - **Coordinator:** `swap-issue-map.txt` and `swap-pr-map.txt`, for the agent
    memory and vault rewrite.
  - **Review session:** the landing merge SHA.

---

## Phase 6 — Prove the gates, then require them [J]

- [ ] **6.1 [J] Prove the skip path on a docs-only PR.** Do this BEFORE adding
  the status-check rule. It must target `main`, because `app-ci` only
  triggers on PRs into `main`.

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
- [ ] **6.2 [J] Prove the fail path on a contract-drift PR** (recommended).

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
  `cp` fix. Then close both throwaway PRs and delete their branches:

  ```bash
  for b in ci/gate-proof-docs ci/gate-proof-drift; do
    gh pr close "$b" --repo Raven-Scout/Scout --delete-branch
  done
  cd "$R" && git switch main && git branch -D ci/gate-proof-docs ci/gate-proof-drift
  ```
- [ ] **6.3 [J] Require the four gates** on the survivor's ruleset `main`.
  - It came from scout-plugin: deletion, non-fast-forward, and a
    pull-request rule with 0 approvals. It has no status-check rule.
  - Quarantine the flaky FS-watcher test first (see Open decisions), or it
    will randomly block merges.

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

  - Expect `app-ci`, `plugin-test`, `plugin-lint` and `contract`.
  - If the PUT rejects a field it returned itself, add the rule by hand:
    Settings → Rules → `main` → "Require status checks to pass", with those
    four names.
  - Keep `merge` in the `pull_request` rule's `allowed_merge_methods`.

---

## Phase 7 — Releases (from the survivor's `main`) [J]

Jordan runs both release scripts. **No agent runs them, not even "to check
something".**

- [ ] **7.1 [J] Plugin first.** Releasing it first means the app's version floor
  names a published plugin.
  1. Fill `plugin/CHANGELOG.md`'s `## [Unreleased]` before preparing.
     `release-plugin.yml` refuses an empty section. Cover:
     - the repo is now `Raven-Scout/Scout`, renamed from scout-plugin. Old
       URLs and marketplaces redirect, so users have nothing to do.
     - the plugin lives under `plugin/`, with `marketplace.json` at the repo
       root
     - `self-update check` reads the root manifest
     - `/scout-update` Step 0.2 now stops only for a marketplace pointing at
       another repo, and gains a two-layout `~/scout-plugin` resolver
     - the installer's marketplace-source check
     - `scoutctl`'s `marketplaces/<name>/plugin/` venv candidates
     - anything Phase 1 pulled in (#247's version switch on update, #264's
       `/scout-plan`, …)
  2. Prepare with **`minor`**, because the layout changed:

     ```bash
     cd "$R" && git switch main && git pull --ff-only && git status --porcelain   # must print nothing
     bash plugin/scripts/release-plugin.sh minor        # bumps on release/vX.Y.Z, commits, pushes, opens the release PR
     ```
  3. Merge the release PR once its four gates are green. Then:

     ```bash
     cd "$R" && git switch main && git pull --ff-only
     NEW=$(cd plugin/engine && .venv/bin/python -m scout.scripts.versioning check)
     bash plugin/scripts/release-plugin.sh --finalize "plugin/v$NEW"     # tags origin/main, pushes the tag
     gh run watch --repo Raven-Scout/Scout "$(gh run list --repo Raven-Scout/Scout --workflow release-plugin.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
     gh release view "plugin/v$NEW" --repo Raven-Scout/Scout
     gh api repos/Raven-Scout/Scout/releases/latest --jq .tag_name        # still app/v… (--latest=false)
     ```
  4. Re-run Phase 4.3's sandbox block. `claude plugin update` must now land
     `plugin/v$NEW`, with an installPath inside `$SB_OLD` and a plugin-only
     tree. Set `IP` to the installPath the block prints, then check the tree
     with `test ! -e "$IP/apps" && ! ls "$IP"/*.xcodeproj 2>/dev/null`.
  - Never run `versioning set` with a lower version: nothing enforces
    monotonicity.
  - `versioning bump` is not a query; it WRITES all four manifests (the
    release script relies on that).
  - Never push a bare `v*` tag.
- [ ] **7.2 [J] App.**
  - Needs the Developer ID Application cert in the keychain and the
    `scout-notary` notarytool profile.
  - The script picks the version from `apps/macos` commits since the newest
    `app/v*` tag. That is the `app/v0.14.0` seeded in 3.3, so `git fetch --tags`
    in `R` first. With no `app/v*` tag it stops; it never falls back to bare
    `v*`.
  - It stamps `SCScoutPluginFloor` from `plugin.json`, tags `app/vX.Y.Z`,
    pushes the tag, and publishes the DMG release with
    `--latest --repo <origin slug>`.

  **Do not release the app until Part B has merged.** Until then the interim
  `ScoutctlLocator` still has the pip/conda/`$PATH` fallbacks Jordan ruled
  out (R37; Part B deletes it). Part B is re-opened on the survivor in 5.2;
  check it there:

  ```bash
  gh pr list --repo Raven-Scout/Scout --state merged --head feat/app-managed-engine --json number,mergedAt   # must list it
  ```

  ```bash
  cd "$R" && git switch main && git pull --ff-only && git fetch --tags origin
  SKIP_NOTARIZE=1 SKIP_RELEASE=1 bash apps/macos/scripts/release-app.sh   # dry run: prints the version it would release
  bash apps/macos/scripts/release-app.sh                                    # or pass an explicit X.Y.Z
  gh api repos/Raven-Scout/Scout/releases/latest --jq '.tag_name, [.assets[].name]'   # app/v…, with Scout-<ver>.dmg
  ```

  `apps/macos/CHANGELOG.md` is not wired into this script (Open decision M6).

---

## Phase 8 — Clean-machine verification (sandboxed ONLY)

Use the same rules and guard as 2.2: never touch the real config, and every
command carries `CLAUDE_CONFIG_DIR=<sandbox>`.

- [ ] **8.1 Guard: before.**

  ```bash
  SB="$(mktemp -d "$HOME/.scout-worktrees/claude-sandbox.XXXXXX")"
  SB2="$(mktemp -d "$HOME/.scout-worktrees/claude-sandbox.XXXXXX")"
  python3 ~/.scout-worktrees/r27-guard.py "$SB" "$SB2" > /tmp/r27-before.txt; cat /tmp/r27-before.txt   # "references … : False"
  ```
- [ ] **8.2 A new user, installing from GitHub.**

  ```bash
  CLAUDE_CONFIG_DIR="$SB" claude plugin marketplace list    # must NOT list scout-plugin; if it does, STOP
  CLAUDE_CONFIG_DIR="$SB" claude plugin marketplace add Raven-Scout/Scout
  CLAUDE_CONFIG_DIR="$SB" claude plugin install scout@scout-plugin
  read -r VER IP < <(CLAUDE_CONFIG_DIR="$SB" claude plugin list --json | python3 -c '
  import json,sys; d=json.load(sys.stdin)
  e=d if isinstance(d,list) else [p for ps in d.get("plugins",{}).values() for p in ps]
  print(next(("%s %s" % (p["version"], p["installPath"]) for p in e if p.get("id")=="scout@scout-plugin"), ""))')
  echo "installed $VER at $IP"
  case "$IP" in "$SB"/*) echo "installPath is inside the sandbox" ;; *) echo "installPath is NOT inside the sandbox — STOP HERE" ;; esac
  test -x "$IP/engine/bin/scoutctl" && test ! -e "$IP/apps" && ! ls "$IP"/*.xcodeproj 2>/dev/null && echo "plugin-only tree OK"
  bash "$IP/scripts/install-venv.sh" && "$IP/.venv/bin/scoutctl" version     # the venv lands inside $IP (sandbox)
  ```

  The version must equal `plugin/v$NEW` from 7.1.
- [ ] **8.3 A new install that uses the OLD name** (old docs and blog posts):

  ```bash
  CLAUDE_CONFIG_DIR="$SB2" claude plugin marketplace add Raven-Scout/scout-plugin      # redirects to Raven-Scout/Scout
  CLAUDE_CONFIG_DIR="$SB2" claude plugin install scout@scout-plugin
  curl -fsSL https://raw.githubusercontent.com/Raven-Scout/scout-plugin/main/install.sh | CLAUDE_CONFIG_DIR="$SB2" bash -s -- --check   # old one-liner: "preconditions OK"
  ```
- [ ] **8.4 Guard: after.** Then clean up.

  ```bash
  python3 ~/.scout-worktrees/r27-guard.py "$SB" "$SB2" > /tmp/r27-after.txt; diff /tmp/r27-before.txt /tmp/r27-after.txt && echo "R27 guard: real config untouched"
  rm -rf "$SB" "$SB2" "$(cat ~/.scout-worktrees/sb-old.path)"
  ```

  If 8.2 or 8.3 fails, **do not archive the legacy repo** (Phase 10). Fix
  forward first.

---

## Phase 9 — Jordan's own machine

Jordan runs this phase by hand. It deliberately changes his real Claude Code
config, so no agent runs it.

- His live install is a **directory** marketplace named `scout-plugin` at
  `~/scout-plugin`, a clone of scout-plugin. `scout@scout-plugin` installs
  from it.
- The engine pointer (`managed_by: dev`), the launchd jobs, and
  `~/miniconda3/bin/scoutctl` (an editable install of `~/scout-plugin/engine`)
  all hang off that clone.
- `/scout-update`'s resolver prefers a git checkout at `~/scout-plugin`.
- After the swap, that clone's `origin` redirects to the survivor, whose
  `main` fast-forwards from scout-plugin's old `main`. So the same clone
  simply pulls into the monorepo layout. No second clone is needed.

- [ ] **9.1 Pick a quiet window.** Choose a time with no scheduled slot due:

  ```bash
  ~/.local/bin/scoutctl schedule list-upcoming --json | head
  ```
- [ ] **9.2 Pull the clone into the monorepo layout.** Rehearse first in a
  sandbox:
  - copy `~/scout-plugin` to a temp directory;
  - point a sandbox directory marketplace at it;
  - pull, then run `marketplace update` and `plugin update`.

  Then for real:

  ```bash
  git -C ~/scout-plugin status --porcelain; git -C ~/scout-plugin rev-parse --abbrev-ref HEAD   # clean, on main
  git -C ~/scout-plugin fetch origin && [ "$(git -C ~/scout-plugin rev-list --count origin/main..main)" = 0 ] && echo "no unpushed commits"   # else push or park them first
  git -C ~/scout-plugin rev-parse HEAD > ~/.scout-worktrees/scout-plugin-pre-monorepo.sha        # the rollback point
  git -C ~/scout-plugin remote set-url origin https://github.com/Raven-Scout/Scout.git
  git -C ~/scout-plugin pull --ff-only
  test -f ~/scout-plugin/.claude-plugin/marketplace.json && test -f ~/scout-plugin/plugin/.claude-plugin/plugin.json && echo "monorepo layout"
  claude plugin marketplace update scout-plugin       # re-reads the root marketplace.json ("source": "./plugin")
  claude plugin update scout@scout-plugin
  ```

  If `claude plugin list --json` doesn't show an installPath under
  `~/scout-plugin/plugin` or the cache, re-point instead:

  ```bash
  claude plugin marketplace remove scout-plugin
  claude plugin marketplace add ~/scout-plugin
  claude plugin install scout@scout-plugin
  ```
- [ ] **9.3 Rebuild and re-point the engine.**

  ```bash
  bash ~/scout-plugin/plugin/scripts/install-venv.sh
  ~/scout-plugin/plugin/.venv/bin/scoutctl bootstrap upgrade --managed-by dev   # re-points plists, shim and ~/.local/state/scout/engine.json
  ~/miniconda3/bin/pip install -e ~/scout-plugin/plugin/engine                  # or uninstall that editable copy
  ```
- [ ] **9.4 Verify.**

  ```bash
  claude plugin list --json | grep -A3 '"scout@scout-plugin"'
  ~/.local/bin/scoutctl version
  ~/scout-plugin/plugin/.venv/bin/scoutctl bootstrap doctor
  grep -m1 '"python"' ~/.local/state/scout/engine.json
  ```

  Then watch the next scheduled run go green.
- [ ] **9.5 Rollback, if needed**:

  ```bash
  git -C ~/scout-plugin reset --keep "$(cat ~/.scout-worktrees/scout-plugin-pre-monorepo.sha)"
  claude plugin marketplace update scout-plugin
  bash ~/scout-plugin/scripts/install-venv.sh                                   # 9.3 pointed everything at plugin/.venv, which is gone now
  ~/scout-plugin/.venv/bin/scoutctl bootstrap upgrade --managed-by dev
  ~/miniconda3/bin/pip install -e ~/scout-plugin/engine
  ```

  After a few days of green runs:
  - delete the `.sha` file;
  - remove the leftover pre-monorepo venvs, which are ignored and no longer
    used: `rm -rf ~/scout-plugin/.venv ~/scout-plugin/engine/.venv`.

---

## Phase 10 — Archive the app repo; fix notices, Pages, org README [J]

Run this only after Phase 8 passes and Phase 5 is complete.

- [ ] **10.1 Gate:** the legacy repo has nothing open.

  ```bash
  gh issue list --repo Raven-Scout/scout-app-legacy --state open --json number --jq length   # 0
  gh pr list    --repo Raven-Scout/scout-app-legacy --state open --json number --jq length   # 0
  ```
- [ ] **10.2 [J] Describe the legacy repo before freezing it.**

  ```bash
  gh repo edit Raven-Scout/scout-app-legacy --homepage https://github.com/Raven-Scout/Scout \
    --description "Archived: Scout.app's pre-monorepo repo. The app now lives in Raven-Scout/Scout under apps/macos/. Issue/PR numbers here are this repo's own."
  ```
- [ ] **10.3 [J] Archive it.**

  ```bash
  gh repo archive Raven-Scout/scout-app-legacy --yes
  gh repo view Raven-Scout/scout-app-legacy --json isArchived --jq .isArchived   # true
  ```

  Its releases and DMGs, `v0.1.0`–`v0.14.0`, stay downloadable there.
- [ ] **10.4 [J] Rewrite and close the move notice, #277.** Its links to
  `Raven-Scout/Scout#132` and `#99` now open scout-plugin items, and its
  re-point commands are no longer needed.

  ```bash
  LANDED=$(cat ~/.scout-worktrees/landing-pr.txt 2>/dev/null || gh pr list --repo Raven-Scout/Scout --state merged --head migrate/monorepo --json number --jq '.[0].number')
  cat > /tmp/notice-277.md <<EOF
  **Done.** scout-plugin was renamed **Raven-Scout/Scout** and is now the Scout monorepo: the plugin lives at \`plugin/\`, the macOS app at \`apps/macos/\`. Landed in #$LANDED. Design: [scout-app-legacy#99](https://github.com/Raven-Scout/scout-app-legacy/pull/99); the earlier draft was [scout-app-legacy#132](https://github.com/Raven-Scout/scout-app-legacy/pull/132).

  **Nothing to do.** Old \`Raven-Scout/scout-plugin\` URLs, clones and Claude Code marketplaces keep working through GitHub's redirect, and the plugin id stays \`scout@scout-plugin\`. The re-point commands this notice used to list still work but aren't needed.

  The app's old repo, with its own issue/PR numbers and every pre-move release, is archived as [Raven-Scout/scout-app-legacy](https://github.com/Raven-Scout/scout-app-legacy). See the pinned numbering notice.
  EOF
  gh issue edit 277 --repo Raven-Scout/Scout --body-file /tmp/notice-277.md
  gh issue unpin 277 --repo Raven-Scout/Scout
  gh issue close 277 --repo Raven-Scout/Scout --comment "The move is complete. See the updated description above."
  ```

  If 4.3 found that the raw `scout-plugin` URL doesn't redirect, add one line
  to the notice: v0.13.0 installs won't see update notices until their next
  `/scout-update`.
- [x] **10.5** Done in 4.6, where the Phase 0 closing comments were fixed.
- [ ] **10.6 Pages.** The site moved with the repo.

  ```bash
  gh api repos/Raven-Scout/Scout/pages --jq '.html_url, .source'                  # https://raven-scout.github.io/Scout/, main /docs
  curl -s -o /dev/null -w '%{http_code}\n' https://raven-scout.github.io/Scout/    # 200
  curl -s -o /dev/null -w '%{http_code}\n' https://raven-scout.github.io/scout-plugin/   # expect 404: project Pages don't follow renames
  ```

  - The branch already points `og:url`, `og:image`, and the READMEs' privacy
    and terms links at `/Scout/`.
  - What's left is Open decision M7: the old-URL stubs, the `og.png` text,
    and excluding internal docs.
- [ ] **10.7 [J] Org profile README.** `Raven-Scout/.github`,
  `profile/README.md`, had `scout-plugin` links on lines 2, 30, 39, 56, 67 and
  68 (2026-10-04).
  - Re-point the repo link and the `curl` installer URL to `Raven-Scout/Scout`.
  - Describe the engine as `Raven-Scout/Scout` → `plugin/` and the app as
    `apps/macos/`.
  - Change the Pages URLs to `/Scout/`.

---

## Phase 11 — Close-out checks

```bash
gh repo view Raven-Scout/scout-app-legacy --json isArchived --jq .isArchived          # true
gh repo view Raven-Scout/Scout --json stargazerCount,forkCount                        # ≥17 / ≥9
gh repo view Raven-Scout/scout-plugin --json nameWithOwner --jq .nameWithOwner        # Raven-Scout/Scout — the redirect is alive; nothing took the name
gh api repos/Raven-Scout/Scout/releases/latest --jq .tag_name                         # app/v…
gh release list --repo Raven-Scout/Scout --limit 5                                    # plugin/v$NEW and app/v…
cd "$R" && git switch main && git pull --ff-only
git grep -nE 'Scout#[0-9]|github\.com/Raven-Scout/Scout/(pull|issues)/[0-9]' -- ':!*.jsonl' | grep -v scout-app-legacy   # only links written after the swap
git grep -n 'Raven-Scout/scout-plugin' -- README.md install.sh plugin/README.md apps/macos/README.md docs/index.html PRIVACY.md TERMS.md .claude-plugin plugin/.claude-plugin plugin/engine/scout/scripts/self_update.py
```

The only `scout-plugin` strings left should be:
- the marketplace `name`, and with it `scout@scout-plugin` and the
  `cache/scout-plugin/` paths;
- the former-name acceptance in `install.sh` and `/scout-update` Step 0.2;
- the root README's "Installed from `Raven-Scout/scout-plugin`?" note.

---

## Open decisions for Jordan

- **M7: the website's leftovers.**
  - Pages moved with the repo to `raven-scout.github.io/Scout/`, still built
    from `main` `/docs`.
  - Old `/scout-plugin/` URLs 404. To keep them, a
    `Raven-Scout/raven-scout.github.io` org-site repo with
    `scout-plugin/{index,privacy,terms}.html` meta-refresh stubs works: a path
    no project site claims falls through to the org site. That repo
    doesn't exist yet. **Never name it, or anything else, `scout-plugin`.**
  - `docs/assets/og.svg` and `og.png` still print the old URL; the PNG needs
    regenerating.
  - The Jekyll build now also publishes the app's docs, alongside
    scout-plugin's own internal `docs/superpowers/**`, which it already
    published. Exclude these in a `docs/_config.yml` if they shouldn't be
    pages.
- **M6: `apps/macos/CHANGELOG.md`.** Wire it into `release-app.sh`, which
  today writes notes from `git log` only, or drop the file.
- **The flaky FS-watcher test**
  (`plugin/engine/tests/integration/test_action_items_watch.py::test_watch_emits_completed_line_on_checkbox_flip`).
  Quarantine it (retry or skip-on-CI) before 6.3 makes `plugin-test`
  required, or it will randomly block merges.
- **#125's open question: pip / Homebrew / `$PATH` engines.** Part B's
  `EngineLocator` has no fallback for an engine installed with pip, Homebrew,
  or found only on `$PATH`; such an engine reads as `notInstalled` and gates
  the tabs.
- **App release before Part B?** If an app release ships from `main` before
  Part B lands, it still resolves `scoutctl` through `ScoutctlLocator`. That
  targets the bash launcher, which writes `.scoutctl-py-cache` into the plugin
  cache dir. Harmless, but Part B replaces it. Re-publishing the shipped
  v0.14.0 DMG in 3.4 is not such a release.
- **Marketplace name.** It stays `scout-plugin` (R17), because renaming
  strands every install. Under the swap it also matches the repo's former
  name, which is part of why old marketplaces keep working.
