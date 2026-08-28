# Frozen governance exploration

`src/action_execution.py`, `src/human_review.py`,
`src/evaluation/quality_action.py`, and
`src/evaluation/action_authorization.py` are preserved as isolated contract
experiments. They are not imported by the ResearchOS Graph and do not dispatch
actions, alter research content, or enable a production approval workflow.

They stay at their existing source paths during Phase 1 to preserve direct test
imports. Phase 2 may relocate them behind compatibility modules after deciding
whether the repository should retain them at all.
