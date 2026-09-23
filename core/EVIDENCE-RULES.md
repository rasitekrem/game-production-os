# Evidence Rules

Status: normative · GPOS `1.0.0-alpha.13` · Elaborates [P4](PRINCIPLES.md#p4--evidence-typed), [P8](PRINCIPLES.md#p8--visual-feedback-loop), [P9](PRINCIPLES.md#p9--motion-requires-motion-evidence)

Machine-readable source: [`registry.json`](registry.json) → `evidence_types`, `capture_contexts`, `instrumentation_timing_impacts`. Record format: [`schemas/evidence.schema.json`](../schemas/evidence.schema.json). Which gate accepts which type: [QUALITY-GATES.md §5](QUALITY-GATES.md#5-evidence-per-gate).

---

## 1. Evidence types

| Type | What it is | What it can prove | What it cannot prove |
|---|---|---|---|
| `CODE_EVIDENCE` | Source diff, static analysis, code review notes | Intent, structure, obvious defects | That anything behaves, looks or feels correct |
| `TEST_EVIDENCE` | Automated test results with the tested revision | Specified behaviour under test conditions | Anything subjective; untested paths |
| `RUNTIME_EVIDENCE` | Records from a running game: play-session logs, turn or state traces, numerical diagnostics, debug readouts | Runtime state, measured values, what happened in a play session | How motion looks; how it feels; target behaviour when captured elsewhere |
| `VISUAL_EVIDENCE` | Still captures at a stated resolution, lighting and camera | Composition, silhouette, palette, layout, readability of a frame | Timing, motion quality, feel |
| `MOTION_EVIDENCE` | Recording of the game or asset in motion at representative frame rate and camera | Animation quality, camera behaviour, feedback timing, game feel | Target behaviour unless captured in the target runtime |
| `AUDIO_EVIDENCE` | Audio capture in gameplay context, ideally synced with video | Audible feedback, mix balance, spatial behaviour | Isolated asset quality in a context it was not captured in |
| `DEVICE_EVIDENCE` | Observation or capture on physical target hardware, device identified | Real-hardware behaviour: input, display, safe areas, thermals, stability | Behaviour on hardware not tested |
| `PERFORMANCE_EVIDENCE` | Profiler captures and measurements with context, build, platform and instrumentation | Frame time, memory, load time, battery/thermal against budgets | Performance in a different context, or timing distorted by instrumentation |
| `PERSISTENCE_EVIDENCE` | Save → quit → relaunch → load round trip, migration results | Data survives real persistence boundaries | Correctness of unsaved in-memory state |
| `HUMAN_EVIDENCE` | A human's recorded verdict or decision with identity, time and subject | Subjective acceptance of exactly the stated subject and revision | Anything outside the reviewed subject and revision |

## 2. Normative insufficiency rules

A project may add stricter rules. It may relax rules 1–6, 8 and 9 only through a recorded Human Decision naming the specific rule. Rule 7 cannot be relaxed by any project ([AUTHORITY-HIERARCHY.md §1](AUTHORITY-HIERARCHY.md#1-order)).

1. **Animation** cannot `PASS` from static screenshots or automated tests alone. `MOTION_EVIDENCE` is required.
2. **Camera motion** — follow, lag, dead zone, look-ahead, comfort — cannot `PASS` from numerical values alone. `MOTION_EVIDENCE` is required whenever such a claim is made (`CAMERA_MOTION_CLAIM`).
3. **Game feel** cannot `PASS` from code inspection. `MOTION_EVIDENCE` is required.
4. **Visual art** cannot `PASS` because an asset exported or imported successfully. Presentation claims need `VISUAL_EVIDENCE` from the engine or a build; a `DCC_RENDER` supports only asset inspection on `ASSET` scope.
5. **Target performance** cannot `PASS` from Editor profiling, or from a run whose instrumentation materially affects timing. It needs `PERFORMANCE_EVIDENCE` captured in `TARGET_RUNTIME` or `PERFORMANCE_RUNTIME` with declared negligible instrumentation impact, plus `DEVICE_EVIDENCE`.
6. **Save persistence** cannot `PASS` from in-memory state. `PERSISTENCE_EVIDENCE` across a real persistence boundary is required.
7. **Human Review** cannot be synthesized by an agent. `HUMAN_EVIDENCE` has a human source, always ([HUMAN-AUTHORITY.md §7](HUMAN-AUTHORITY.md#7-authenticity-of-human-evidence-trust-boundary)).
8. **UI/UX** for a touch or mobile target cannot `PASS` from desktop captures alone. `DEVICE_EVIDENCE` is required (`TOUCH_OR_MOBILE_TARGET`). For non-touch targets this rule does not apply.
9. **Gameplay design** cannot `PASS` from code, tests or a design document. It needs evidence from a playable runtime; `MOTION_EVIDENCE` is required when timing, spatial motion or real-time behaviour is part of the claim (`REAL_TIME_BEHAVIOUR`). Turn-based, card, puzzle, narrative or strategy claims may rest on runtime play-session evidence.

## 3. Capture contexts

Every evidence record states where it was captured (`provenance.capture_context`):

| Context | What it is | Strength and limits |
|---|---|---|
| `DCC_RENDER` | Viewport or render from a DCC tool | Valid for asset/art inspection where appropriate. **Not proof of in-game presentation.** Must name the tool version and state limitations. |
| `EDITOR` | Engine editor, including play-in-editor | Intermediate visual or runtime evidence. **Must state its limitations** (editor lighting, frame pacing, input, display). Never target-device evidence. |
| `TARGET_RUNTIME` | A build running on the target platform in a shipping-representative configuration | **Required when the claim depends materially on the actual target runtime, display, platform input or renderer behaviour** (`TARGET_PRESENTATION_DIFFERS`). Must name the target platform and the build. |
| `DIAGNOSTIC_RUNTIME` | A build with diagnostic features: development build, debug overlays, logging | Runtime evidence with stated limitations. Not target-representative for presentation or performance claims. |
| `PERFORMANCE_RUNTIME` | A build run for measurement with profiling instrumentation | Must name the target platform and **declare the instrumentation and its timing impact** (`NONE`, `NEGLIGIBLE`, `MATERIAL`, `UNKNOWN`). |
| `OFFLINE_ANALYSIS` | Analysis outside a running game: static analysis, asset validation, log review | Supports technical claims. Never proves presentation or feel. |
| `AUTOMATED_TEST` | A test runner or automated runtime harness, locally or in CI | Tests, and runtime, visual, motion, audio or persistence captures the harness produces from a running game. |
| `HUMAN_RECORD` | A human's recorded verdict or decision | Only for `HUMAN_EVIDENCE`, and only with a human source. |

### Type / context compatibility

Each evidence type exists only in certain capture contexts (registry `evidence_context_compatibility`, schema-enforced). Any other combination is invalid — for example a `DCC_RENDER` can never be `RUNTIME_EVIDENCE`, whatever a tool claims.

| Evidence type | Valid capture contexts |
|---|---|
| `CODE_EVIDENCE` | `OFFLINE_ANALYSIS`, `AUTOMATED_TEST` |
| `TEST_EVIDENCE` | `AUTOMATED_TEST`, `EDITOR`, `DIAGNOSTIC_RUNTIME`, `TARGET_RUNTIME` |
| `RUNTIME_EVIDENCE` | `EDITOR`, `TARGET_RUNTIME`, `DIAGNOSTIC_RUNTIME`, `PERFORMANCE_RUNTIME`, `AUTOMATED_TEST` |
| `VISUAL_EVIDENCE` | `DCC_RENDER`, `EDITOR`, `TARGET_RUNTIME`, `DIAGNOSTIC_RUNTIME`, `AUTOMATED_TEST` |
| `MOTION_EVIDENCE` | `DCC_RENDER`, `EDITOR`, `TARGET_RUNTIME`, `DIAGNOSTIC_RUNTIME`, `AUTOMATED_TEST` |
| `AUDIO_EVIDENCE` | `EDITOR`, `TARGET_RUNTIME`, `DIAGNOSTIC_RUNTIME`, `AUTOMATED_TEST` |
| `DEVICE_EVIDENCE` | `TARGET_RUNTIME`, `PERFORMANCE_RUNTIME` |
| `PERFORMANCE_EVIDENCE` | `EDITOR`, `DIAGNOSTIC_RUNTIME`, `TARGET_RUNTIME`, `PERFORMANCE_RUNTIME`, `OFFLINE_ANALYSIS` |
| `PERSISTENCE_EVIDENCE` | `EDITOR`, `DIAGNOSTIC_RUNTIME`, `TARGET_RUNTIME`, `AUTOMATED_TEST` |
| `HUMAN_EVIDENCE` | `HUMAN_RECORD` |

Compatibility says only that a record is meaningful. Whether it counts for a particular gate is decided by the gate's own rules ([QUALITY-GATES.md §5](QUALITY-GATES.md#5-evidence-per-gate)): for example `PERFORMANCE_EVIDENCE` from `OFFLINE_ANALYSIS` (a budget estimate) is valid evidence but does not count toward `PERFORMANCE` `PASS`. `PERSISTENCE_EVIDENCE` must always come from a run that crossed a real persistence boundary.

**Instrumentation rule.** A performance claim must not silently rest on a run whose instrumentation materially affects timing. Evidence with `MATERIAL` or `UNKNOWN` timing impact must say so in its limitations and does not count toward a timing `PASS`.

**Target runtime rule.** When the target platform differs materially from the development environment, final judgements of camera, game feel and UI — in particular in the Golden Gameplay Cell — need `TARGET_RUNTIME` evidence. This does not make mobile or device evidence universal: a desktop game whose editor represents its target does not need it, and the routing records that choice explicitly (`unapplied_conditions`).

## 4. Provenance

Evidence must identify the exact production state it supports. Fields (`provenance` in the schema):

| Field | Meaning | Required when |
|---|---|---|
| `capture_context` | §3 vocabulary | always |
| `subject_revision` | Exact revision of the subject captured (commit, asset version) | always |
| `build_revision` / `build_id` | Source revision the build was made from / build identifier | any runtime context (`TARGET_RUNTIME`, `DIAGNOSTIC_RUNTIME`, `PERFORMANCE_RUNTIME`): at least one |
| `artifact_hash` | Hash of the build or asset captured | recommended; adapters should fill it |
| `target_platform` | Platform the build targets, from the project platform vocabulary (registry `platforms`). Target-runtime evidence counts only on a platform declared in project config `target_platforms`; otherwise record it as `DIAGNOSTIC_RUNTIME` | `TARGET_RUNTIME`, `PERFORMANCE_RUNTIME`, `DEVICE_EVIDENCE` |
| `device` | Device identifier. Where the project declares `reference_devices` for the platform, `DEVICE_EVIDENCE` counts only on one of them (exact identifier); where none are declared, any device on that platform counts | `DEVICE_EVIDENCE` |
| `tool_version` | Engine, DCC or tool version | `DCC_RENDER`, `EDITOR` |
| `instrumentation` | Presence, description and timing impact | `PERFORMANCE_RUNTIME`, `PERFORMANCE_EVIDENCE` |
| `supersedes` | Evidence ids this record replaces | when it replaces earlier evidence |

## 5. Revisions, staleness and supersession

**Normative rule: evidence is bound to its subject and its revision.**

- **Subject.** Every evidence record names the subject it proves (`subject.kind`, `subject.ref`, using the gate scope vocabulary). It counts for a gate only when it matches the gate scope's kind and ref. Evidence about another subject — for example a `BUILD` capture offered for a `TASK` gate, or another task's recording — counts only through an explicit `evidence_applicability` entry in the gate record: the evidence, its subject, a justification, and an accountable approver (the gate owner or an authorized human). Matching revision strings never make evidence apply to a different subject.
- **Revision.** Evidence from an older or materially different runtime may not silently prove a newer revision.

- A gate record states the subject revision it applies to (`scope.revision`). Counting evidence must have the same `subject_revision`.
- If the subject changed after capture, the later change must either be **shown not to affect the claim** — an explicit `evidence_carryover` entry in the gate record, with the earlier revision, a justification and who assessed it — **or** the evidence is invalid for the new revision and must be recaptured.
- When evidence is replaced, the new record lists the old one in `supersedes`, and the old record is marked `superseded: true` with `superseded_by` (or `superseded_reason` until a replacement exists). Superseded evidence may stay for history but never supports a current gate status.
- **Carryover approval is accountable.** Only the gate owner (the owning specialist) or an authorized human may approve a carryover (`evidence_carryover[].assessed_by`). CI, tools and devices may propose one (`proposed_by`) — for example a diff analysis showing no relevant files changed — but they never decide that old evidence still holds.
- A carryover is itself reviewable. A reviewer who doubts the justification returns the gate to `NOT_RUN`.

## 6. Validity requirements

Evidence counts only if it is:

- **Typed** — exactly one type from §1. A recording with sound may be registered twice (`MOTION_EVIDENCE` and `AUDIO_EVIDENCE`) referencing the same artifact.
- **Attributed** — its source (agent, human, CI, tool, device) is recorded.
- **Provenanced** — §4.
- **Current** — §5.
- **Honest about limits** — its `limitations` state what it does not show.
- **Retrievable** — it references an artifact a reviewer can open. Descriptions of evidence are not evidence.

## 7. Capture guidance (tool-independent)

- **Motion:** capture at the game's target frame rate from the gameplay camera. Include enough context to judge the behaviour — e.g. for locomotion: idle, start, steady movement, turns at 45°/90°/180° where relevant, stop, settle.
- **Visual:** capture with production lighting and post-processing where the project has them, at target aspect ratio. State if placeholder lighting is used.
- **Before/after:** for corrective work, provide comparable captures from the same camera and conditions.
- **Device:** name the device model and OS version. Emulators and simulators are `RUNTIME_EVIDENCE` in `DIAGNOSTIC_RUNTIME`, not `DEVICE_EVIDENCE`.
- **Performance:** state build, platform, device, scene, duration, instrumentation and the metric against a named budget from `.game/PERFORMANCE.md`.
- **Human:** quote or link the human's own words; record who, when, subject and revision.

## 8. Anti-patterns

- "Tests are green" offered as evidence for any subjective gate.
- A single hero screenshot standing in for motion, feel or camera behaviour.
- Debug camera, debug lighting or paused gameplay presented as gameplay evidence without limitation notes.
- Evidence that describes what was done ("I adjusted the blend times") without showing the result.
- Reusing evidence from before a change as proof of the change without an explicit, justified carryover.
- A profiler-instrumented run reported as shipping frame time.
- An agent writing `HUMAN_EVIDENCE` from its own impression or from inferred approval.
