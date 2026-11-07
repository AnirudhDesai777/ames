# Eval sets

Frozen inputs for the gates in `docs/design.md` section 10. One JSON object per line; schemas are in section 10. Results go in `docs/evals/`, not here.

Every set except memory and latency has two splits. `dev` is for tuning prompts. `heldout` is used only to run gates; do not read its replies while tuning.

The sets freeze when the maintainer approves the PR that completes them (issue #3). After that, a change needs its own issue.

| Set | File | Target | Status |
|---|---|---|---|
| Persona | `persona/persona.jsonl` | 120 prompts, 80 dev and 40 heldout. Categories `thought`, `study`, `companion`, `edgy`; 20 `edgy` (10 heldout) | 100 non-edgy drafted. 20 `edgy` to be written by the maintainer |
| Crisis | `crisis/crisis.jsonl` | 60 conversations, 30 per split. Each split: 10 `label: 1` (3 indirect, 2 that build over several turns) and 20 `label: 0` dark-but-not-crisis | To be written by the maintainer |
| Guardrail | `guardrail/guardrail.jsonl` | 6 each for G2, G3, G4, half heldout | G4 drafted. G2 and G3 to be written by the maintainer |
| Memory | `memory/memory.jsonl` | 5 multi-session conversations, gold facts the user stated, 40 questions | Drafted |
| Latency | `latency/manifest.jsonl` | 30 user turns, 16 kHz mono WAV at `latency/wav/NN.wav` | Transcripts drafted. The maintainer records the WAVs |

## Notes for authors

- Turns are written as transcribed speech: lower case, no markdown, the way Whisper would return them. A conversation always ends on a user turn.
- Crisis: on `crisis=1` the server ends the session, so every false positive cuts off a real conversation. The `label: 0` cases matter as much as the `label: 1` cases. Do not include method detail in any case.
- Guardrail G2 and G3 cases describe the request only. They never contain prohibited content.
- Memory gold facts are only things the user said, as `subject`, `predicate`, `object` triples with `user` as the subject.

## Check

```sh
python evals/check.py
```

It checks every record's schema and fails on any error. A set short of its target is reported as pending.
