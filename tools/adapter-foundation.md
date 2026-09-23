# Tool adapter foundation (Phase 2C-0)

Code: [`gpos/tools/`](../gpos/tools/__init__.py) · CLI: `python3 -m gpos.tools` · status: frozen at `v1.0.0-alpha.10`. This document describes the foundation as it was frozen. Phase 2C-1 added the first production adapter on top of it, unchanged: see [git-adapter.md](git-adapter.md).

This is the shared execution, capability, provenance, safety and evidence layer that every future GPOS tool adapter must use. It integrates no real tool. It exists so that the Git, FFmpeg, target-device, Blender and Unity adapters that come later cannot each invent their own command execution model, capability vocabulary, result structure, provenance model, mutation semantics, single-writer behaviour, timeout behaviour, evidence semantics or failure model. Phase 2C-0 is what prevents tool-layer semantic drift.

## Tool adapter vs agent adapter

They are different layers with different jobs, different registries and different id spaces.

| | Agent adapter (Phase 2B) | Tool adapter (Phase 2C) |
|---|---|---|
| Question | How does this agent read GPOS? | What can this tool do, and what did it just do? |
| Output | Generated instruction files (`CLAUDE.md`, `AGENTS.md`, skills) | A `ToolResult`: artifacts, provenance, evidence candidates |
| Code | [`gpos/adapters/`](../adapters/README.md) | [`gpos/tools/`](../gpos/tools/__init__.py) |
| Registry | the agent backend table (`claude-code`, `codex`) | `ToolRegistry` (empty in Phase 2C-0) |
| Runs | nothing | a declared capability, once, under a contract |

Conceptually they stack: Claude Code or Codex → a GPOS specialist → the tool adapter registry → tool execution → an evidence candidate. Phase 2C-0 does not inject tool details into specialist skills; instruction integration comes after the foundation and concrete adapters exist.

## The boundary this layer protects

```
agent / specialist intent
        ↓
tool adapter
        ↓
execution
        ↓
artifacts + provenance
        ↓
evidence candidate
        ↓
GPOS record / validator
        ↓
gate / Human Review
```

and the distinction it exists to keep:

**`TOOL SUCCESS` ≠ `EVIDENCE VALID` ≠ gate `PASS` ≠ human approval.**

A tool adapter is implementation and tool infrastructure. In the frozen authority hierarchy it never rises above `ENGINE_TOOL_DEFAULTS`. It **may** execute an authorized capability, inspect tool state, produce artifacts, collect provenance and offer candidate evidence. It **may not** decide Human Review, create `HUMAN_EVIDENCE`, mark a subjective gate `PASS`, change Project Locked Authority, decide a lifecycle transition, waive a requirement or reinterpret evidence compatibility.

This is enforced structurally, not by convention: there is no field anywhere in `gpos/tools/` for a gate, a gate status, a reviewer, an assessor or an approval. An adapter that wanted to claim one has no vocabulary to do it in.

## Architecture

| Module | Owns |
|---|---|
| `model.py` | adapter identity, subject and actor, probe result, lifecycle, the `ToolAdapter` base class |
| `capabilities.py` | the capability declaration and its timeout policy |
| `validation.py` | registration-time validation against the frozen registry |
| `registry.py` | which adapters exist; probing and readiness |
| `execution.py` | the execution request, the result, and the orchestration that enforces everything |
| `process.py` | the single audited process boundary |
| `redaction.py` | conservative credential redaction |
| `paths.py` | the filesystem boundary |
| `artifacts.py` | artifact metadata, streamed hashing, derivation |
| `provenance.py` | what ran, on what, producing what |
| `evidence.py` | evidence candidates and the registry-backed compatibility rules |
| `leases.py` | single-writer leases |
| `cli.py` | `list`, `describe`, `capabilities`, `probe`, `execute` |
| `synthetic/` | the `TEST_ONLY` reference adapter |

A future adapter implements only the tool-specific part:

```
ToolAdapter
├── descriptor      identity + capabilities
├── probe()         is the tool here, and usable?
├── execute()       map one capability to one real invocation
└── returns an AdapterOutcome
```

Everything else — request validation, mutation consent, leases, process safety, timeouts, capture bounds, redaction, artifact hashing, provenance, evidence rules and the fail-closed status — is applied around `execute()` by the foundation, whether the adapter cooperates or not.

## Capability model

A capability is a declaration the foundation reads before it lets anything run:

| Field | Meaning |
|---|---|
| `id`, `category`, `description` | identity; category from registry `tool_capability_categories` |
| `operation_class` | `READ_ONLY` or `MUTATING` |
| `state_model` | `STATELESS` or `STATEFUL` |
| `execution_context` | the capture context this execution actually observes |
| `resource_from_request` | the single-writer target is named by the request, not the project |
| `single_writer_required`, `resource_kind` | the lease it needs, and on what |
| `dry_run_supported` | whether a plan-only run is possible |
| `requires_tool`, `requires_project`, `requires_ready_routing` | its preconditions |
| `input_kinds`, `artifact_kinds` | what it accepts and may produce |
| `potential_evidence` | the `(evidence type, capture context)` pairs it may ever offer |
| `timeout` | default and maximum |
| `side_effect_scope` | what a `MUTATING` capability touches |

### Read-only vs mutating

`READ_ONLY` may inspect, calculate and capture; it must not change authoritative project or tool state. `MUTATING` may change files, scenes, assets, device state, repository state or external tool state.

The classification belongs to the declaration, not to the size of the change: a small write is still `MUTATING`. A mutating capability runs only with explicit `allow_mutation` consent in the request; without it the result is `INVALID_REQUEST` / `MUTATION_NOT_ALLOWED` and nothing runs. An adapter that reports a mutation from a capability declared `READ_ONLY` is contradicted by its own declaration and the result is refused.

### Stateless vs stateful

`STATELESS` execution does not depend on a persistent interactive tool state the adapter manages. `STATEFUL` execution mutates or depends on a long-lived editor, device or session state.

A `MUTATING` + `STATEFUL` capability **must** declare `single_writer_required` — registration fails otherwise. This is how the frozen stateful-editor policy is preserved, and an adapter cannot opt out of it.

## Probe

Every adapter implements a `READ_ONLY` probe that answers: is the tool available, where is it, what version is it, is that version and this platform compatible, and which capabilities are usable. It returns a structured `ProbeResult` with status `AVAILABLE`, `UNAVAILABLE` or `VERSION_UNSUPPORTED`. A missing tool is a result, never an opaque subprocess exception: a probe that raises is converted into `UNAVAILABLE` plus an `ADAPTER_INTERNAL_ERROR` diagnostic.

The lifecycle is `REGISTERED` → `PROBED` → `READY`, with `UNAVAILABLE` and `INCOMPATIBLE` as terminal-for-now states. A capability that `requires_tool` fails closed unless the adapter reached `READY`.

## Execution request and result

A request names the adapter, the capability and the subject, and may carry a project root, inputs, input artifacts, an output directory, a resource id, a dry-run flag, mutation consent, a timeout, an actor, a routing reference, a build revision or id, a target platform and a device. Low-level tool calls do not require routing or task records; a request links to them when they exist. Nothing missing is synthesized.

Canonical request fields are checked against the registry **before any adapter runs**: the subject kind against the scope-kind vocabulary, a non-empty subject reference, a non-empty revision when one is supplied, the actor kind and a usable actor id, the target platform, and any expected evidence as a compatible pair the capability actually declares. An invalid subject can therefore never reach an accepted evidence candidate and surface much later as a schema failure.

A result always carries a status, the request and adapter ids, start and finish times, a duration, dry-run and mutation flags, artifacts, provenance, diagnostics and evidence candidates, plus an exit code and captured output when a process was involved.

### Result statuses

| Status | Exit | Meaning |
|---|---|---|
| `SUCCESS` | 0 | the capability executed and did what it declared |
| `INVALID_REQUEST` | 1 | invalid adapter, capability, request or declared evidence |
| `FAILED` | 2 | the tool ran and failed |
| `TIMED_OUT` | 3 | the deadline passed; the process tree was terminated |
| `UNAVAILABLE` | 4 | the required tool is not present, or the adapter is not `READY` |
| `CONFLICT` | 5 | a single-writer or safety conflict refused the operation |
| `INCOMPATIBLE` | 6 | the installed tool version or the pinned GPOS version cannot be used |
| `CANCELLED` | 7 | execution was cancelled |
| `INTERNAL_ERROR` | 8 | a foundation or adapter defect |

Nothing collapses into one failure code, and the library status is independent of the CLI exit code, so a future agent adapter can distinguish outcomes without reading messages. Success is never defined as `exit_code == 0`: not every adapter drives a command-line process, and the status is the most severe class among the recorded diagnostics — a blocking diagnostic always wins over an adapter's optimism.

## Process safety

`gpos/tools/process.py` is the one module in `gpos/` allowed to start an operating-system process, and the Phase-1 boundary test enforces that. It is a boundary, not a shell:

- no `shell=True`, ever, and `sh`, `bash`, `cmd`, `powershell` and friends are refused as the executable, as are `-c` style program-string flags;
- the executable is an absolute path to an existing executable file that the adapter resolved, usually through its probe — never prose from a request;
- arguments are a vector, never interpolated or concatenated;
- the working directory is explicit and must resolve inside a permitted scope;
- stdin is closed: nothing interactive;
- stdout and stderr are captured with a hard byte bound, so a process printing gigabytes cannot exhaust memory; the full stream length is still counted, truncation is reported as `PROCESS_OUTPUT_TRUNCATED`, and truncation alone is never treated as failure;
- the child runs in its own session, so a timeout terminates the whole process tree (a terminate signal, then a kill signal after a short grace);
- durations use a monotonic clock, never a subtraction of wall-clock timestamps.

There is no generic shell execution tool exposed to agents, and the CLI has no `--command` option. The adapter owns what may be executed; the runner only executes a spec that is already authorized.

### Environment policy

The child environment is built from a declared policy: a positive allowlist of inherited names (PATH, HOME, TMPDIR, locale and a few platform names) plus literal overrides the adapter sets. Values are never copied into a result, a diagnostic or provenance — only names are recorded. Captured output, diagnostic text and recorded command arguments all pass through conservative redaction that removes credential-shaped assignments, authorization and cookie headers, PEM private-key bodies and a few unmistakable provider token formats, while leaving ordinary game logs (`fps=59.8`, `level=forest-02`) untouched.

## Working directory and paths

The foundation default is **project-root bounded**. A project-bound capability may touch the project tree and any extra absolute scopes its adapter contract explicitly declares; a capability that declares `requires_project = False` is scoped to the output directory the caller named, and nothing else. Nothing is granted implicitly, and no scope ever comes from something a tool produced.

A project-less capability never gains filesystem authority over a project tree by being handed one: supplying `project_root` to a capability that does not require a project is refused as an invalid request, rather than quietly becoming an unvalidated scope. Ambiguity is rejected, not resolved. Its output directory must be an absolute path, and it is checked before it is created — validation never has creating a directory as a side effect.

Every execution path and every declared artifact is checked before use: `..` cannot walk out, a symlink planted anywhere below the scope is refused before the target is opened, and the resolved path must still be inside a scope. The canonical record area `.game/gpos/` is never a write target for tool execution.

A limitation worth stating plainly: the foundation cannot stop an external tool from writing wherever the operating system permits. What it guarantees is that GPOS itself writes and deletes nothing outside a permitted scope, and that a path outside one is refused as an artifact — it is never hashed, recorded, offered as evidence or carried into provenance.

## Dry run

If a capability supports dry run, a dry-run request validates the request, resolves what would execute, determines the side-effect plan and performs no external mutation. `dry_run` is `true` in the result, `mutation_performed` is `false`, and the plan is reported.

"No external mutation" includes the foundation's own housekeeping: a dry run creates no execution workspace and no parent of one. A caller-named output directory is *resolved* without being created, so a dry run against a directory that does not exist leaves it, and every parent of it, absent. For a real execution the workspace is created only after the request and its paths have been validated, and a failure to create it — a path that is already a regular file, a permission error, any other filesystem error — is a structured `WORKSPACE_NOT_USABLE` result rather than an exception escaping the call.

Dry-run success means the operation plan was valid. It does not mean the real operation succeeded. A dry run observed nothing, so it may only produce the registry's `dry_run_evidence_types` — a closed allowlist which excludes every execution-observed type, including `RUNTIME_EVIDENCE`, `DEVICE_EVIDENCE`, `PERFORMANCE_EVIDENCE`, `MOTION_EVIDENCE` and `AUDIO_EVIDENCE`. An adapter that tries is refused with `EVIDENCE_NOT_AVAILABLE_IN_DRY_RUN`.

## Artifacts

Artifact files stay files. An artifact record holds metadata and a `sha256`; it never holds bytes, and no payload is embedded in a diagnostic, in provenance or in an evidence record. Hashing is streamed in 1 MiB chunks, so a multi-gigabyte capture is hashed with bounded memory. The adapter declares what it produced; the foundation checks the path, hashes the file and assigns the hash — an adapter never supplies its own.

An artifact from an execution that did not finish is recorded with `complete = false` and reported as `ARTIFACT_INCOMPLETE`. Incomplete artifacts are surfaced, never silently promoted into evidence.

The capability declaration stays authoritative at execution time, not only at registration: every output artifact must be one of the kinds the capability registered and a kind the registry knows, its id must be structural and unique, and it may not shadow an input artifact the caller supplied. An invalid claim is refused and the artifact is dropped — nothing is silently renamed or reclassified.

### Derivation

An execution may consume input artifacts the caller supplies, each with the capture context the caller vouches for — usually taken from the evidence that artifact already belongs to. An artifact derived from one is classified `DERIVED`, points at its source and **inherits the origin's capture context**.

So a still extracted from a gameplay capture made in `TARGET_RUNTIME` is visual evidence captured in `TARGET_RUNTIME`, while the transform itself remains an `OFFLINE_ANALYSIS` execution. Media processing never upgrades the authority of its source, and a derived candidate that claims any other context is refused with `EVIDENCE_CONTEXT_NOT_OBSERVED`.

## Provenance

Provenance is built by the foundation from what it observed, not by the adapter from what it would like to claim. It records the GPOS version, project id, adapter id and version, tool name, version and path, capability and request ids, subject, routing reference, actor, the redacted command and environment names, input and output artifact hashes, execution context, start and finish times, a monotonic duration, mutation state, dry-run state and output truncation.

The rule for unknown values is mechanical: values the foundation observed are always recorded; values only the caller can know — subject revision, build revision, build id, target platform, device — are recorded when supplied and are otherwise **absent** and listed in `unknown`. Nothing is guessed or defaulted. Phase 2C-0 does not infer a repository revision; that is a later adapter's job.

The project id comes from the record set the Phase-2A validator already produced, not from a second reading of the raw configuration file. Timing is one interval: the foundation takes a single finish timestamp and one monotonic duration for the whole execution, and the result, the provenance and every accepted candidate share them. The duration is never the inner process interval — a future editor or library adapter may run with no process at all — and it is never a subtraction of wall-clock timestamps.

## Evidence candidates

An adapter may offer an `EvidenceCandidate`: *this execution produced something that may satisfy this evidence requirement*. It carries the evidence type, capture context, subject binding and revision, artifact references, provenance, source adapter and capability, a generated timestamp and its limitations.

The foundation validates every candidate against the frozen registry — it keeps no duplicate compatibility matrix and no adapter can override GPOS:

- the type and capture context must be compatible (`evidence_context_compatibility`); an invalid pair is refused with `EVIDENCE_CONTEXT_INCOMPATIBLE` and never silently coerced;
- `HUMAN_EVIDENCE` is refused outright: only a human record is human evidence;
- the pair must be one the capability declared;
- a non-derived candidate's capture context must be the context the execution observed;
- a derived candidate inherits its origin's context;
- a dry run is limited to `dry_run_evidence_types`;
- referenced artifacts must exist in this result and be complete;
- a candidate whose subject revision is unknown is kept, marked `materializable = false` and given an explicit limitation. Evidence is never labelled current for a revision nobody can prove;
- the foundation owns `generated_at`: an accepted candidate carries the foundation-observed execution time, because freshness is exactly the kind of claim a tool must not be trusted to make about itself.

An invalid declaration is checked at registration too: an adapter that declares an impossible `(type, context)` pair never enters the registry.

### Creating records

`materialize()` converts a validated candidate into a GPOS evidence record value. It is explicit, deterministic and schema-validated; it refuses `HUMAN_EVIDENCE`; it fills no gate, reviewer or assessor field; and it **writes nothing**. Ordinary tool execution never writes into `.game/gpos/evidence/`. Attaching a record to a gate stays a separate, human-authorized step.

Provenance in a materialized record comes from the execution, never from the caller. Everything the foundation observed — capture context, subject revision, build revision and id, target platform, device, tool version — flows automatically out of the candidate into the record, so a caller never restates known truth. A caller may add only the schema-supported fields the foundation cannot observe (`artifact_hash`, `instrumentation`); setting a foundation-owned field to a different value, or to a value this execution never observed, fails closed. A materialized record therefore always describes the validated candidate, and there is no second provenance schema — the frozen evidence schema is the only one.

## Single-writer leases

A small local mechanism, not a lock service: no daemon, no distributed consensus, no network.

- the lease is a file under `.game/gpos-runtime/leases/`, non-authoritative generated state; no mutable lease state is ever written into `.game/gpos/`, and nothing is written outside the project;
- the file name is a deterministic hash of (adapter id, resource id), so the same target always maps to the same lease. A capability that leases the project leases its **resolved** root, so two spellings of the same directory always collide as they should; a capability that leases something else declares `resource_from_request` and the request names it through an explicit `resource_id`, so there is no magic input name acting as an undocumented lease protocol;
- acquisition is atomic (`O_CREAT | O_EXCL`); if the lease is held, acquisition fails immediately with `LEASE_CONFLICT` and the holder's metadata. There is no waiting, no retry loop and no force;
- release only ever removes a lease this process owns, verified by owner id and token. A release that fails is blocking (`LEASE_RELEASE_FAILED`): an execution never reports a clean `SUCCESS` while the writer lease it took is still lying in the project, and the unverified lease is still not deleted;
- a lease that looks abandoned is reported as `LEASE_STALE` and **never** broken automatically. Recovery is an explicit operation that records who broke what and why;
- read-only operations take no lease; a dry run takes none either, because it mutates nothing;
- an unreadable lease file is `LEASE_INVALID` and fails closed rather than being treated as free.

Crash behaviour is therefore conservative by design: a crashed writer leaves a lease behind, the next writer is told exactly who held it and that the holding process is gone, and a human decides.

## Failure model

The foundation cannot make arbitrary external tools transactional, and does not pretend to. What it guarantees:

- result, provenance and metadata construction is fail-closed: a blocking diagnostic always downgrades the status, so `SUCCESS` is never recorded for an execution that failed;
- an incomplete artifact is never registered as valid evidence;
- a timeout is `TIMED_OUT`, never a partial success, and partial outputs are surfaced as incomplete artifacts rather than evidence;
- leases are released whenever the foundation can release them, including on adapter exceptions and on a defect during result assembly; a release that fails downgrades the status rather than being ignored;
- a process that cannot be started after its spec validated — the executable vanished, lost its permission bit or cannot be exec'd — is reported as a tool availability problem, not as an opaque foundation defect;
- an adapter defect is `INTERNAL_ERROR`, never a clean verdict.

A mutating real tool may still leave partial external state. Each future adapter contract must document its own recovery and rollback guarantees.

## Trust boundaries

- **Authority**: a tool adapter never becomes creative or production authority. See above.
- **Filesystem**: project-root bounded by default; extra scopes only by explicit adapter contract.
- **Process**: one audited boundary; no shell; no agent-facing command execution.
- **Secrets**: environment values are never copied into results, and every string a caller can see is redacted — not only captured process output but every surface an adapter controls: diagnostics and their details, parsed result data, the recorded command and environment, artifact descriptions, evidence summaries, limitations and notes, and probe text including an exception message. A credential-named key (`{"token": "…"}`) or command-line flag (`["--password", "…"]`) redacts its value even though the name and the value are separate elements. A value a result cannot carry is refused rather than stringified. This is deliberately not a universal secret scanner.
- **Network**: off. The foundation makes no network call, discovers no plugin, downloads nothing and installs nothing. Registration is explicit Python. A future adapter may one day declare a network capability; the default stays off.
- **Records**: tool execution never writes to `.game/gpos/`.

## What Phase 2C-0 does not contain

**No Git adapter. No GitHub adapter. No FFmpeg adapter. No Android/ADB adapter. No Blender adapter. No Unity adapter. No MCP. No agent invocation. No planner and no autonomous tool loop. No project bootstrap. No CI workflow. No real external-tool invocation as production functionality.**

The only executable adapter in the tree is the `TEST_ONLY` synthetic reference adapter in `gpos/tools/synthetic/`. It drives no production tool, it is marked `TEST_ONLY` in both its adapter kind and its tool family, the production registry refuses to register it, and it exists only so the foundation's behaviour can be exercised without any of the tools above being installed.

## CLI

```bash
python3 -m gpos.tools list
python3 -m gpos.tools describe <adapter>
python3 -m gpos.tools capabilities <adapter>
python3 -m gpos.tools probe <adapter>
python3 -m gpos.tools execute --adapter A --capability C --subject-ref REF [--project PATH] [--dry-run] [--allow-mutation]
```

`list`, `describe`, `capabilities` and `probe` need no project and are never blocked on task readiness. `execute` takes a declared adapter and capability plus structured request fields; there is no way to ask it to run something the adapter has not declared. Text and JSON output; exit codes are the statuses above.

An input artifact is given as `--input-artifact ID=PATH`, and its capture context separately as `--input-artifact-context ID=CONTEXT`. They are separate options because packing a path and a context into one colon-separated value cannot be parsed unambiguously for a Windows path such as `C:\media\gameplay.mp4`, and guessing by operating system would make the same command mean different things on different machines. The library `InputArtifact` API is unchanged.

## Future adapter sequence

| Phase | Adapter | Notes |
|---|---|---|
| 2C-1 | version control / provenance | supplies repository revisions the foundation deliberately does not infer — implemented, see [git-adapter.md](git-adapter.md) |
| 2C-2 | FFmpeg / media evidence | derived visual evidence from motion captures |
| 2C-3 | Target device / Android ADB | device and performance evidence from real hardware |
| 2C-4 | Blender | DCC render evidence |
| 2C-5 | Unity | editor and target-runtime capture |
| 2C-6 | Integrated toolchain validation | real project pilot |

None of them is started. Each one adds only the tool-specific mapping; the semantics stay here.
