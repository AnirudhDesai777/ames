# Decision ledger

This directory records what the maintainer has decided, what is still open, and the rules that follow, so agents do not reopen settled choices.

## Files

- `learning/decisions.yml`: maintainer decisions, approved or superseded. History is never deleted.
- `learning/candidates.yml`: proposals and open questions that are not decisions yet.
- `rules.yml`: constraints on future work, each derived from one or more APPROVED decisions.

`evals/tests/test_agent_ledger.py` checks the structure and the links between the three files.

## Workflow

1. Before work that touches a decided topic, read `decisions.yml` and `rules.yml`.
2. A newer APPROVED decision outranks older docs, including `docs/design.md`. If a doc disagrees with a decision, follow the decision and note the stale doc in the PR.
3. Agent repetition or recency is never approval. A choice that appears in many PRs or the latest commit is not a decision unless the maintainer made it.
4. Record a new proposal or open question as a candidate with status NEEDS_DECISION.
5. Only an explicit maintainer decision turns a candidate into a decision. Record it first in `decisions.yml` with evidence, then set the candidate's status and `decision_id`, then update `rules.yml` and the docs.
6. When a decision is reversed, set the old one to SUPERSEDED, set its `superseded_by` to the new id, and list the old id in the new decision's `supersedes`. Links must match both ways.

## Schemas

All three files start with `schema_version: 1`.

`decisions.yml`, list `decisions`:

| Field | Meaning |
|---|---|
| `id` | `DECISION-NNN`, numbered in date order, never reused |
| `date` | `YYYY-MM-DD` of the decision |
| `status` | `APPROVED` or `SUPERSEDED` |
| `topic`, `decision` | Short topic; one to three plain sentences, naming what was rejected when known |
| `evidence` | PRs, issues, Notion pages, `file#section`, and the maintainer's words verbatim as `Maintainer: "..."` |
| `source` | `USER_DECISION` |
| `supersedes`, `superseded_by` | List of ids, and one id or null |
| `originating_task` | Short label for the work that produced it |

A decision qualifies only if the maintainer's approval is traceable: the maintainer's words, a Notion row the maintainer asked or answered, a merged PR or `docs/design.md` text stating the maintainer decided, or the lead agent's session notes.

`candidates.yml`, list `candidates`:

| Field | Meaning |
|---|---|
| `id` | `CANDIDATE-NNN` |
| `status` | `NEEDS_DECISION`, `ADOPT`, `ADAPT`, `REJECT` or `NOT_APPLICABLE` |
| `proposal`, `reason` | What is proposed and why it is here |
| `evidence` | Nonempty list |
| `affected_files`, `conflicts` | Lists; conflicts name decision ids |
| `originating_task` | Short label |
| `confidence` | `HIGH`, `MEDIUM` or `LOW` |
| `explicit_user_feedback` | true only when the maintainer commented on it |
| `decision_id` | Only when resolved; must name an existing decision |

`rules.yml`, list `rules`:

| Field | Meaning |
|---|---|
| `id` | Area prefix (`PRODUCT`, `MODEL`, `EVAL`, `PROCESS`, `SAFETY`) and three digits, never reused |
| `title`, `rule`, `rationale` | What to do and why |
| `strength` | `MUST` or `SHOULD` |
| `source` | `type: USER_DECISION` and `references` to APPROVED decisions as `.agent/learning/decisions.yml#DECISION-NNN` |
| `enforcement` | `status` (`automated`, `partial`, `review-only`), `mechanism`, `limitations` |
| `documentation` | Repo path that explains the rule, or null |
