# Eval sets

Frozen inputs for the gates in `docs/design.md` section 10. One JSON object per line; schemas are in section 10. Results go in `docs/evals/`, not here.

Every set except latency has two splits. `dev` is for tuning prompts. `heldout` is used only to run gates; do not read its replies while tuning.

The sets freeze when the maintainer approves the PR that completes them (issue #3). After that, a change needs its own issue.

| Set | File | Target | Status |
|---|---|---|---|
| Persona | `persona/persona.jsonl` | 90 prompts, 60 dev and 30 heldout. Categories `thought`, `companion`, `edgy`; 20 `edgy` (10 heldout): drugs, dark humour, relationships or sexual health without explicit content | 70 non-edgy drafted. 20 `edgy` to be written by the maintainer |
| Crisis | `crisis/crisis.jsonl` | 60 conversations, 30 per split. Each split: 10 `label: 1` (3 indirect, 2 that build over several turns) and 20 `label: 0` dark-but-not-crisis | Drafted, for maintainer review |
| Guardrail | `guardrail/guardrail.jsonl` | 6 each for G2, G3, G4, half heldout | G4 drafted. G2 and G3 to be written by the maintainer |
| Memory | `memory/memory.jsonl` | 9 multi-session conversations, 3 dev and 6 heldout, gold facts the user stated, 72 questions (27 gold facts and 48 questions heldout) | Drafted |
| Latency | `latency/manifest.jsonl` | 30 user turns, 16 kHz mono WAV at `latency/wav/NN.wav` | Transcripts drafted. The maintainer records the WAVs |

## Notes for authors

- Persona records list the moods that fit a good reply (`moods`), from the six in design section 7. The mood-accuracy gate checks her control-token mood against this list.
- Turns are written as transcribed speech: lower case, no markdown, the way Whisper would return them. A conversation always ends on a user turn.
- Crisis: on `crisis=1` the server ends the session, so every false positive cuts off a real conversation. The `label: 0` cases matter as much as the `label: 1` cases. Do not include method detail in any case.
- Guardrail G2 and G3 cases describe the request only. They never contain prohibited content. G2 covers sexual content requests from any user, adult or minor.
- Memory gold facts are only things the user said, as `subject`, `predicate`, `object` triples with `user` as the subject.
- Memory records `memory-001` to `memory-003` are `dev`; `memory-004` to `memory-009` are `heldout`.

## Check

```sh
python evals/check.py
```

It checks every record's schema and fails on any error. A set short of its target is reported as pending.

## Run with stubs

From the repo root, after `uv sync --locked`:

```sh
uv run --locked python -m evals.run persona --split dev --out /tmp/persona-dev.json
uv run --locked python -m evals.run crisis --split heldout --out /tmp/crisis-heldout.json
uv run --locked python -m evals.run guardrail --split dev --out /tmp/guardrail-dev.json
uv run --locked python -m evals.run memory --split dev --out /tmp/memory-dev.json
uv run --locked python -m evals.run memory --split heldout --out /tmp/memory-heldout.json
uv run --locked pytest -q evals/tests
```

The CLI always uses the fixed model, judge and extractor in `evals/stubs.py`. No model downloads, network, GPU, microphone or browser are needed. CI runs every set with both split selections and prints each metric. Latency belongs to S2a and is not part of this command.

The command prints metrics and PASS or FAIL against the proposed thresholds in `evals/scoring.py`. The JSON file includes counts, metrics, gates and per-case scores, without conversation or reply text. Exit code 0 means the evaluation completed, even if a score gate failed; errors reading inputs, judging or writing results exit nonzero. Consumers that need a score gate must check the JSON `status`.

Stubs test the harness, not model quality. The model always emits a valid neutral, non-crisis token, so crisis recall is zero. The judge always gives four persona scores of 4, no refusal, and yes for other decisions. The extractor returns one fixed triple. These choices deliberately produce failing quality gates. No gold labels or expected answers are passed to the model or extractor.

Missing observations are JSON `null` and fail their gate: the current persona set has no `edgy` cases, and guardrails have no G2 or G3 cases. Counts show this partial coverage. Memory runs score 3 conversations on `dev` and 6 on `heldout`, and report the selected split. Dev results are diagnostic; only the completed held-out sets with real models can establish the release gates in design section 10.

`evals.run.evaluate` accepts `model`, `judge` and `extractor` callables, each taking a list of `{role, content}` messages and returning text. It reads the fenced System prompt from `persona/ames.md` verbatim, maps dataset role `ames` to `assistant`, and parses replies with `server/control_tokens.py`. The judge returns a JSON object with `in_character`, `advice_rules`, `spoken_length` and `pushback` integer scores for persona, and yes or no for other decisions. Invalid judge output raises an error rather than becoming a passing score. Judge instructions request only scores or yes/no, never prohibited content.

Memory extraction runs separately on each session. Duplicate facts are combined within a conversation; facts match gold triples after trimming and case folding each field. Precision and recall pool fact counts across conversations. Questions receive only the extracted facts, with no source conversation or reference answer; the judge compares each reply with the reference answer. The extraction callable returns a JSON list of `{subject, predicate, object}` string triples. Pass `extraction_prompt` to `evaluate`, or `--extraction-prompt path/to/prompt.txt` to the CLI, to supply the extraction instructions. The fixed stub extractor ignores those instructions. This harness does not test database retrieval or deletion; those belong to the memory slices.
