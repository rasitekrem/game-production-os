"""Adapter intermediate representation (IR): one normalized, agent-independent model of the GPOS
authority a project's agents must follow. Every backend renders from the same IR; no backend
reads GPOS sources itself.

The IR is plain, immutable data. `to_dict()` is canonical and hashed (`ir_sha256`).
"""

from dataclasses import asdict, dataclass, field

IR_VERSION = 1


@dataclass(frozen=True)
class SkillIR:
    name: str                      # frozen GPOS skill id, e.g. character-animation
    title: str                     # e.g. Character Animation
    description: str               # discovery text: domain intent, not keywords
    maturity: str                  # copied from the contract; adapters never change it
    may_own_gates: tuple           # from the contract front matter
    sections: tuple                # ((HEADING, text), ...) every contract section, in order
    authority_files: tuple         # project authority files the contract names as inputs
    cross_reviewers: tuple         # registry: who may cross-review gates this skill owns
    reviews_for: tuple             # registry: owners this skill may cross-review
    never_cross_reviewer: bool     # registry never_cross_reviewer (game-director)
    source_id: str


@dataclass(frozen=True)
class WorkflowIR:
    name: str
    title: str
    purpose: str
    always_required: tuple         # registry workflow_gate_requirements
    when_affected: tuple
    account_for_all_gates: bool
    mandatory_triggers: tuple      # registry workflow_mandatory_triggers
    body: str                      # delinked contract text (reference material only)
    source_id: str


@dataclass(frozen=True)
class AuthorityRowIR:
    section: str
    item: str
    value: str
    status: str                    # LOCKED or PROPOSED, as written by the project
    decision_ref: str              # None when absent
    lock_verified: bool            # LOCKED with an ACTIVE decision record of that id
    placeholders: tuple            # UNDECIDED / HUMAN_DECISION_REQUIRED / ... present in the value


@dataclass(frozen=True)
class AuthorityDocIR:
    file: str                      # e.g. ANIMATION.md
    path: str                      # project-relative, e.g. .game/ANIMATION.md
    present: bool
    status: str                    # document status, e.g. PROPOSED / LOCKED (None when absent)
    locked_by: str
    rows: tuple
    source_id: str                 # None when absent


@dataclass(frozen=True)
class IR:
    ir_version: int
    gpos_version: str
    project: dict                  # id, name, lifecycle_stage, pinned gpos_version, enabled adapters
    authority_order: tuple         # ((REGISTRY_ID, label), ...) highest first
    human_review: dict             # mandatory triggers, project triggers, never_cross_reviewer, placeholders
    gates: tuple                   # ((GATE, owners, subjective), ...)
    skills: tuple                  # enabled SkillIR, registry order
    disabled_skills: tuple
    workflows: tuple
    project_authority: tuple       # AuthorityDocIR for every registry project_authority_file
    validator: dict                # machine-validation contract (commands, exit codes, when)
    sources: tuple = field(default=())  # ({kind, id, sha256}, ...) sorted by id

    def to_dict(self):
        return asdict(self)

    def skill(self, name):
        return next(s for s in self.skills if s.name == name)
