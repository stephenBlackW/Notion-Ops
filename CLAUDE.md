# notion-ops — Claude Code orientation

`notion-ops` is the Notion-API operations library carved out of AgenticOS
(AOS) in Session 1 (2026-05-27, AOS STATE.md D-28) into its own public
PyPI-bound repo `stephenBlackW/Notion-Ops`. It provides idempotent
pages/blocks/databases/data-sources operations, retry-wrapped reads, the
`create_atom_page` Atom-publishing convenience, the `publish_block_tree`
multi-request orchestrator, and the markdown-to-Notion-blocks converter.

## Repository layout

| Path | What lives there |
|------|------------------|
| `notion_ops/` | The library. Pages, blocks, databases, data-sources, models, utils (atoms, markdown, publish, ids, retry). |
| `tests/` | The library's own test suite (mocked Notion client by default; `@pytest.mark.e2e` for tests needing a live workspace + `NOTION_API_KEY`). |
| `pyproject.toml` | Standalone package manifest. |
| `LICENSE` | MIT. |
| `README.md` | Public-facing readme. |

## Relationship to AOS

This repo is mounted as a git submodule in AOS at `vendor/notion-ops`.
AOS develops `notion-ops` cross-repo: dev cycles run from AOS against
the submodule's working tree, and vetted production code is committed +
pushed to this repo. AOS owns the dev-cycle harness; this repo carries
only the library + its own tests + CI. Cycle state, methodology
artifacts, milestones, decision logs, and HL register live in AOS.

This split is intentional per AOS STATE.md D-10, D-11.

## How AOS orients against this repo

AOS reads the `## Dev Cycle Profile` block below to parameterize its
Skill+Agent+`cycle_lib` orchestrator for `nops-*` cycles.

---

## Dev Cycle Profile

```yaml
spoke_id: notion-ops
cycle_id_prefix: nops-
gate_script: run-tests.sh             # placeholder; pyproject.toml declares pytest as the gate
output_artifact_default: synthesis.md

# Evaluator team registry — spec.md declares which 2-3 are invoked per cycle.
# Default invoked: [hostile, contract]. Future cycles compose at spec level
# per AOS ao-meta-E D-2.
evaluator_team:
  - role: hostile
    persona: hostile-engineer.md
    structural_agent: hostile-engineer.md
  - role: contract
    persona: relation-contract-specialist.md
    structural_agent: relation-contract-specialist.md

# mutation_author and step4_adversary OMITTED -- inherit F-contract v2
# defaults (developer / mutation-adversary.md). See AOS STATE.md D-30.

closer_role: cycle-closer

live_dependency:
  kind: notion-api
  env_var: NOTION_API_KEY
  pytest_marker: e2e

cycle_id_classifier:
  cycle:        spec-feature.md
  meta:         spec-meta.md
  housekeeping: spec-meta.md
  refactor:     spec-refactor.md
  patch:        spec-patch.md
  issue:        spec-patch.md
  onboard:      spec-meta.md

# state_files are placeholders -- notion-ops does not run its own dev cycles
# (AOS STATE.md D-11). Fields are F-contract-required; placeholder paths
# document where these files WOULD live if notion-ops grew its own harness.
state_files:
  milestones:  MILESTONES.yaml
  state_log:   STATE.md
  hl_register: dev-cycles/HL_REGISTER.yaml

publish:
  enabled: true
  config: config/publication.yaml
```
