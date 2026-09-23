# Git provenance adapter (Phase 2C-1)

Code: [`gpos/tools/git/`](../gpos/tools/git/__init__.py) · adapter id `git` · status: the first production tool adapter, built on the frozen [tool adapter foundation](adapter-foundation.md) (`v1.0.0-alpha.10`) without changing it.

The adapter reads a local Git repository and reports two things: what state it is in, and whether an exact, immutable revision describes it. It exists to prove that the foundation works with a real external command-line tool, and to give future tool executions a trustworthy repository revision, explicitly.

It is not a Git command runner. It changes nothing in a repository and never touches a network.

## Identity

| Field | Value |
|---|---|
| `adapter_id` | `git` |
| `tool_family` | `VERSION_CONTROL` |
| `target_tool` | Git |
| `adapter_kind` | `CLI` |
| `state_model` | `STATELESS` |
| platforms | `WINDOWS`, `MACOS`, `LINUX` |
| network | `FORBIDDEN` |
| minimum Git | 2.36.0 |
| TEST_ONLY | no — this is a production adapter |

`python3 -m gpos.tools list` shows it, and `default_registry()` contains exactly `git`. It lives only in the tool adapter registry: it is not an agent adapter and is not in the registry's `adapter_ids`. The TEST_ONLY synthetic adapter never enters the production registry.

## Verified Git behaviour

Every Git behaviour the adapter relies on was checked against first-party Git documentation on 2026-09-23, and then observed with a real Git before any code depended on it.

| Document | What it establishes |
|---|---|
| [git-status](https://git-scm.com/docs/git-status) (2.53.0 manual) | Porcelain v2 record formats, including the branch headers `# branch.oid <commit> \| (initial)` and `# branch.head <branch> \| (detached)`. With `-z`, records are NUL-terminated, paths are printed as-is with no quoting, and a rename's original path is a separate NUL-terminated field. The porcelain option output "will remain stable across Git versions and regardless of user configuration". Parsers "should ignore headers they don't recognize". `--untracked-files=all` lists individual files. |
| [git-status 2.6.7](https://git-scm.com/docs/git-status/2.6.7), [2.11.4](https://git-scm.com/docs/git-status/2.11.4), [2.17.0](https://git-scm.com/docs/git-status/2.17.0), [2.18.0](https://git-scm.com/docs/git-status/2.18.0) | Porcelain v2 with branch headers is absent from the 2.6.7 manual (which covers 2.7–2.10) and present in 2.11. `--find-renames` / `--no-renames` are absent from 2.17 and present in 2.18, "regardless of user configuration". |
| [git](https://git-scm.com/docs/git) and [git 2.15.4](https://git-scm.com/docs/git/2.15.4) | With optional locks disabled, "Git will complete any requested operation without performing any optional sub-operations that require taking a lock. For example, this will prevent `git status` from refreshing the index as a side effect." Also the definitions of the terminal-prompt and pager controls, and that the work-tree location can be set by `core.worktree`. The optional-lock control is documented in the 2.15 manual. |
| [git-rev-parse](https://git-scm.com/docs/git-rev-parse) | `--show-toplevel` shows "the (by default, absolute) path of the top-level directory of the working tree", and reports an error when there is no working tree. |
| [git-version](https://git-scm.com/docs/git-version) | The version output format is **not** documented as stable, so the probe parses it conservatively and never guesses a version. |
| [githooks](https://git-scm.com/docs/githooks) and git-config (from Git 2.52.0's own shipped manual) | `core.fsmonitor` may name a hook command that Git runs to speed up index scans such as `git status`. `safe.directory` is the reason Git "will refuse to even parse a Git config of a repository owned by someone else, let alone run its hooks". The configuration environment: with `GIT_CONFIG_COUNT`, key/value pairs "will be added to the process's runtime configuration" and "will override values in configuration files"; they form the *command* scope. **"Git versions 2.35.1 and prior will not understand the boolean values and will consider the 'true' or 'false' values as hook pathnames to be invoked."** |
| [git-status `--ignore-submodules`](https://git-scm.com/docs/git-status) | `none` "will consider the submodule modified when it either contains untracked or modified files or its HEAD differs from the commit recorded in the superproject and can be used to override any settings of the `ignore` option in git-config or gitmodules". |
| [git-fsmonitor--daemon](https://git-scm.com/docs/git-fsmonitor--daemon) | Its oldest manual is 2.36.0. With `core.fsmonitor` set to `true`, commands "such as `git status`, will ask the daemon for changes and automatically start it". |
| Git release notes [2.31.0](https://raw.githubusercontent.com/git/git/master/Documentation/RelNotes/2.31.0.adoc), [2.35.2](https://raw.githubusercontent.com/git/git/master/Documentation/RelNotes/2.35.2.adoc) | 2.31 introduced configuration pairs through environment variables; 2.35.2 is a security-only release. |

The real tests ran against **Git 2.52.0** (Homebrew, macOS).

The minimum of **2.36.0** is not a guess. The options alone would need 2.18: porcelain v2 (2.11), optional locks (2.15), `status --find-renames` (2.18). Command-scope configuration needs 2.31. The binding constraint is fsmonitor: before 2.36, Git reads `core.fsmonitor=false` as the *name of a program to run*, so the override that disables it would itself start a process. 2.36.0 is the first Git in which `false` means "off".

## Capabilities

Both capabilities are `READ_ONLY`, `STATELESS` and observe `OFFLINE_ANALYSIS`. Both require the tool and a GPOS project that the Phase-2A validator reports valid, but no ready routing. Neither takes a single-writer lease, supports dry run, accepts any input, produces an artifact or offers evidence.

### `git.inspect`

Returns the repository state:

```json
{
  "repository_root": "/absolute/resolved/project/root",
  "head_sha": "<commit id or null>",
  "branch": "<branch name or null>",
  "detached": false,
  "unborn": false,
  "clean": true,
  "exact_revision": "<commit id or null>",
  "staged_count": 0,
  "unstaged_count": 0,
  "untracked_count": 0,
  "conflicted_count": 0
}
```

| Field | Meaning |
|---|---|
| `head_sha` | the committed HEAD; `null` on an unborn branch |
| `branch` | the symbolic branch; `null` when detached |
| `detached` | HEAD is a commit with no branch attached |
| `unborn` | the branch exists but has no commit yet |
| `clean` | no staged, unstaged, untracked or conflicted work |
| `exact_revision` | `head_sha` **only** when HEAD exists **and** the tree is clean; otherwise `null` |
| counts | staged and unstaged entries of changed tracked files (a file changed in both places counts in both), individual untracked files (ignored files excluded), and unmerged entries |

**A dirty working tree is never represented as exactly its HEAD commit.** HEAD stays available in `head_sha` as a baseline, but `exact_revision` is `null`. The adapter never invents a work-tree revision, and never hashes work-tree content and calls the result a Git revision. Object ids may be SHA-1 (40 hex digits) or SHA-256 (64).

The result carries counts, not a list of paths. Git's machine output, which names every changed file, is used to compute the counts and then kept out of the result.

### `git.resolve-provenance`

Returns the revision a caller may hand to later tool executions:

```json
{ "repository_revision": "<commit id>", "head_sha": "<same>", "branch": "main", "detached": false }
```

Success requires that the project root is the repository top level, that HEAD exists, and that the tree is clean. Otherwise the result is `CONFLICT` with `REPOSITORY_STATE_CONFLICT`, `repository_revision` is `null`, and the message says whether the tree is dirty (with its counts) or the branch has no commit yet.

## Explicit provenance handoff

The foundation never infers a repository revision, and this adapter does not change that. The workflow is explicit:

1. call `git.resolve-provenance`;
2. read `repository_revision`;
3. the caller decides to use it;
4. the caller passes it into a later request as `build_revision`;
5. the foundation's existing provenance records exactly that value.

From the CLI, step 4 is `--build-revision <repository_revision>` on `python3 -m gpos.tools execute` (Phase 2C-2). A later request that does not pass it records `build_revision` as unknown, even right after a successful resolution. There is no cache, no current-revision singleton and no cross-call state: every execution reads the repository as it is now.

## Authorized Git surface

The adapter can start exactly three Git processes, each with a fixed argument vector. Nothing from a request, a configuration or a caller is ever appended:

| Purpose | Invocation |
|---|---|
| probe | `git --version` |
| repository root | `git rev-parse --show-toplevel` |
| state | `git status --porcelain=v2 --branch -z --untracked-files=all --find-renames --no-ahead-behind --ignore-submodules=none` |

- `--find-renames` makes rename detection independent of user configuration, so identical states give identical counts.
- `--no-ahead-behind` skips upstream divergence counting, which the adapter never reports.
- `--ignore-submodules=none` overrides any `submodule.<name>.ignore` in `.git/config` or `.gitmodules`. Without it, `ignore = all` hides a dirty submodule completely, and a tree that is not exactly its HEAD commit would be reported clean with an exact revision. A submodule is dirty if it has modified or untracked files, or if its checked-out commit differs from the one the superproject records.
- To look inside each submodule, Git itself runs `git status` there. Those are Git's own child processes of the same executable, with the same environment (see below); the adapter adds no argument to them.
- The adapter never passes `-c`, and the frozen process boundary would refuse it anyway.

There is no staging, committing, reset, restore, checkout, switch, branch, tag, merge, rebase, cherry-pick, stash, clean, worktree or configuration change. There is no fetch, pull, push, clone or remote listing. The adapter reads no remote names, URLs or credentials: a remote URL can itself contain a credential and is irrelevant to provenance.

## Execution environment

Every Git process runs through the audited process boundary: the adapter never starts a process itself and never imports the subprocess module. The executable is the one the probe resolved:

- `shutil.which` looks it up on PATH;
- a match found only through a relative PATH entry is refused, because the program run would then depend on the working directory;
- the match is resolved to an absolute path.

The environment is the foundation's allowlist plus fixed values the adapter owns. No caller can set or change them:

| Variable | Value | Why |
|---|---|---|
| `GIT_TERMINAL_PROMPT` | `0` | never prompt on a terminal |
| `GIT_OPTIONAL_LOCKS` | `0` | `status` must not refresh and rewrite the index |
| `GIT_PAGER` | `cat` | never launch a pager |
| `LC_ALL` | `C` | deterministic wording in the Git messages the adapter quotes |
| `GIT_CONFIG_COUNT` | `1` | one command-scope configuration pair follows |
| `GIT_CONFIG_KEY_0` | `core.fsmonitor` | … which is the filesystem monitor |
| `GIT_CONFIG_VALUE_0` | `false` | … switched off |

### Why fsmonitor is switched off

Repository and user Git configuration still applies in general: the adapter runs the user's Git on the user's repository. `core.fsmonitor` is the exception. A repository can set it to the path of a hook program, which `git status` then runs, or to `true`, which makes `git status` start Git's fsmonitor daemon. Either way a READ_ONLY capability whose source authorizes three fixed Git invocations would start another process that nobody authorized. `GIT_OPTIONAL_LOCKS=0` does not prevent it.

The adapter therefore disables fsmonitor at command scope, which overrides every configuration file:

- nothing is written to any configuration file;
- `-c` is never passed;
- the override also reaches the `git status` that Git runs inside each submodule, so a submodule's own hook is neutralized as well.

Real tests show all three cases:

- a configured hook writes no marker under the adapter, though a plain `git status` in the same repository runs it;
- `core.fsmonitor=true` starts no daemon and leaves no `.git/fsmonitor--daemon*` state, though a plain `git status` does both;
- a submodule's own hook does not run.

The override requires the 2.36.0 minimum explained above.

Because the foundation inherits only an allowlist, a caller's environment cannot redirect or reconfigure Git: GIT_DIR, GIT_WORK_TREE, GIT_ASKPASS, GIT_EXTERNAL_DIFF and injected configuration variables never reach it. Only the variable **names** appear in provenance, never their values.

The optional-lock setting is verified against real behaviour, not only declared. After the stat data of a tracked file changes, a plain `git status` rewrites the index; the adapter's status leaves it byte-identical.

## Repository-root rule

The GPOS project root must itself be Git's work-tree top level. The adapter runs `git rev-parse --show-toplevel` from the validated project root, resolves both paths, and requires them to name the same directory.

A project nested inside a larger repository fails closed with `REPOSITORY_ROOT_MISMATCH`. That is `MONOREPO_NESTED_PROJECT_NOT_YET_SUPPORTED`, an intentional Phase-2C-1 limitation. Only the enclosing repository's location is read: its status is never read, its work tree is never inspected, and no filesystem scope over it is granted.

A linked worktree, whose `.git` is a file, is supported because Git itself reports the project root as the top level. The adapter never parses `.git` itself: Git is the authority on repository layout. A project outside any repository is `REPOSITORY_NOT_FOUND`.

## Machine output: raw bytes in, counts out

The adapter parses Git's output from the process boundary's **private raw capture**, the exact bytes Git wrote, never from the public redacted text. The public text is safe for display, but redaction can rewrite a machine protocol. Credential-shaped files named `api_key=abc` and `ordinary.txt` would appear in the public text as `? api_key=[REDACTED] ordinary.txt`: the redaction swallowed the NUL that ends a record and merged two records into one.

Parsing the raw bytes gives exact counts: two untracked files. Nothing secret-shaped reaches any public surface, because the result carries counts, never paths, and anything the adapter reports still passes the foundation's redaction boundary.

The status parser works on bytes throughout:

- records split on NUL;
- the fixed fields before a path split with an explicit maximum;
- paths are never decoded, because they are never needed, so spaces, newlines, Unicode and any byte the platform allows except NUL are all counted correctly.

The branch name is the only text taken from a record. It is decoded after the record boundaries are fixed, so an undecodable byte cannot move a boundary, and it is decoded deterministically: invalid UTF-8 becomes a backslash escape such as `\xff`. The repository-root comparison also uses Git's exact bytes, so a credential-shaped directory name no longer breaks it. The path shown in the public data is still redacted.

## Output the adapter refuses to parse

State is reported only from complete Git output:

- **Truncated.** Status output is capped at 8 MiB. Output that reaches a capture bound is refused, even when it happens to end exactly on a record boundary and would parse as a well-formed prefix.
- **Not understood.** An unknown record type, a malformed field, a missing branch header or an unterminated final record is a parse error, never a guess.

## Results and diagnostics

| Situation | Status | Code |
|---|---|---|
| state read | `SUCCESS` | — |
| exact revision resolved | `SUCCESS` | — |
| dirty tree or no commit, when resolving | `CONFLICT` | `REPOSITORY_STATE_CONFLICT` |
| project outside any repository | `INVALID_REQUEST` | `REPOSITORY_NOT_FOUND` |
| project nested in a larger repository | `INVALID_REQUEST` | `REPOSITORY_ROOT_MISMATCH` |
| truncated or unreadable output | `FAILED` | `EXECUTION_FAILED` (+ `PROCESS_OUTPUT_TRUNCATED`) |
| Git missing or not on an absolute PATH entry | `UNAVAILABLE` | `TOOL_NOT_FOUND` |
| Git older than 2.36.0, or unrecognized version output | `INCOMPATIBLE` | `TOOL_VERSION_UNSUPPORTED` |
| Git times out | `TIMED_OUT` | `EXECUTION_TIMEOUT` |

The three repository codes are generic to version control, not specific to Git.

## Security review

| Concern | Finding |
|---|---|
| shell invocation | none: argument vectors only, through the audited boundary |
| subprocess outside the boundary | none: `gpos/tools/process.py` remains the only importer under `gpos/` |
| arbitrary Git arguments | none: three fixed vectors, no inputs, verified in the source and at runtime |
| caller-chosen executable | none: the probe resolves it, and relative PATH entries are refused |
| network commands | none are authorized |
| remote URL output | none is read or reported |
| Git configuration writes | none; no `-c` |
| external diff | status runs no diff program, and the diff variable is not inherited |
| interactive prompts | disabled; stdin is closed by the boundary |
| **indirect process execution through `core.fsmonitor`** | **none: disabled at command scope, for the repository and for Git's own submodule children; hook and daemon both verified not to start** |
| **submodule ignore settings hiding dirtiness** | **none: `--ignore-submodules=none` overrides config and `.gitmodules`** |
| partial or truncated output treated as complete | refused: truncation and parse checks, including a cut exactly on a record boundary |
| raw process output crossing the public result boundary | none: the raw capture never reaches a result, provenance, diagnostic, CLI output or evidence |
| repository-root escape | refused: equality with the project root, compared on Git's exact bytes |
| revision fabrication on dirty or unborn trees | none: `exact_revision` is `null` |

No OS sandboxing is claimed. The foundation's external-tool trust boundary is otherwise unchanged. Git runs with the repository's and the user's own configuration under Git's own trust model, and refuses to use the configuration of a repository owned by another user (`safe.directory`). The two settings that would break this adapter's contract are overridden: fsmonitor, which runs another process, and submodule ignore, which hides dirtiness.

## Limitations

- `MONOREPO_NESTED_PROJECT_NOT_YET_SUPPORTED`: the project root must be the repository top level.
- A branch name that is not valid UTF-8 is reported with backslash escapes (`\xff`).
- Git's own configuration applies, except `core.fsmonitor` and submodule ignore settings, which are overridden.
- Git's own child `git status` runs inside submodules are part of how Git reads a superproject; they receive the same environment.
- Real-runtime tests ran on macOS with Git 2.52.0. Windows and Linux are declared but were not exercised here. On macOS a real filename that is not valid UTF-8 cannot be created (APFS refuses it), so those bytes are covered at the parser level and by a Linux-only integration test.
- There is no mutation capability. Any version-control mutation needs a separate Human Review.

## Tests

```bash
python3 tests/test_git_adapter.py
python3 tests/mutate_git_adapter.py
```

The suite builds real repositories with the installed Git and fails, rather than falling back to mocks, if Git is absent. It covers:

- registration and the real probe;
- missing and unusable tools;
- clean, dirty, conflicted, detached and unborn repositories;
- non-repository and nested projects;
- spaces, Unicode, newlines and renames in paths;
- truncated output, and credential-shaped names parsed correctly from the raw capture;
- submodules: dirty, hidden by `ignore = all` in config or `.gitmodules`, untracked-only, moved HEAD, clean, and a submodule's own fsmonitor hook;
- fsmonitor: a marker-writing hook and the builtin daemon, both proven not to start;
- the environment and real optional-lock behaviour;
- read-only behaviour, checked by hashing every file under `.git` before and after;
- resolve-provenance and the explicit handoff;
- the network and argument surface;
- the CLI, determinism and linked worktrees.

The mutation harness breaks each guarantee in turn and requires the suite to catch it.
