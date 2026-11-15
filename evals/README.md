# Eval sets

Frozen inputs for the gates in `docs/design.md` section 10. One JSON object per line; schemas are in section 10. Results go in `docs/evals/`, not here.

Every set except latency has two splits. `dev` is for tuning prompts. `heldout` is used only to run gates; do not read its replies while tuning.

The sets were frozen by the maintainer in issue #3. A change needs its own issue.

| Set | File | Target | Status |
|---|---|---|---|
| Persona | `persona/persona.jsonl` | 90 prompts, 60 dev and 30 heldout. Categories `thought`, `companion`, `edgy`; 20 `edgy` (10 heldout): drugs, dark humour, relationships or sexual health without explicit content | Frozen |
| Crisis | `crisis/crisis.jsonl` | 60 conversations, 30 per split. Each split: 10 `label: 1` (3 indirect, 2 that build over several turns) and 20 `label: 0` dark-but-not-crisis | Frozen |
| Guardrail | `guardrail/guardrail.jsonl` | 6 each for G2, G3, G4, half heldout | Frozen |
| Memory | `memory/memory.jsonl` | 9 multi-session conversations, 3 dev and 6 heldout, 56 gold facts the user stated (20 dev and 36 heldout), 72 questions (48 heldout) | Frozen |
| Tools | `tools/tools.jsonl` | 63 conversations: 42 dev, 21 heldout; 30 required calls, 33 no-call cases | Review draft; [format and scoring](../docs/tool-calling.md) |
| Latency | `latency/manifest.jsonl` | 30 user turns, 16 kHz mono WAV at `latency/wav/NN.wav` | Frozen |

## Notes for authors

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
uv run --locked python -m evals.run memory --split heldout --repeats 3 --out /tmp/memory-heldout.json
uv run --locked pytest -q evals/tests
```

The CLI always uses the fixed model, judge and extractor in `evals/stubs.py`. No model downloads, network, GPU, microphone or browser are needed. Evals run locally on the maintainer's Mac. CI covers the runner only through pytest in `evals/tests/test_run.py`. Latency belongs to S2a and is not part of this command.

The command prints metrics and PASS or FAIL against the proposed thresholds in `evals/scoring.py`. The JSON file includes counts, metrics, gates and per-case scores, without conversation or reply text. Exit code 0 means the evaluation completed, even if a score gate failed; errors reading inputs, running models or writing results exit nonzero. Consumers that need a score gate must check the JSON `status`.

`--repeats N` runs the selected set and split N times (a positive integer, default 1). Console output reports each run's metrics and their arithmetic mean; the final gates and status use the mean. If any run has no observations for a metric, its mean is `null` and fails its gate. JSON `runs` contains each run's counts, metrics, gates and cases; top-level `metrics`, `gates` and `status` describe the mean. A single run also keeps top-level `counts` and `cases` for existing consumers.

Heldout gates use `--repeats 3`. Repeats only reduce sampling noise when the model samples (temperature above 0); deterministic models and the fixed stubs repeat the same results.

Stubs test the harness, not model quality. The model always emits a valid neutral, non-crisis token, so crisis recall is zero. The judge always gives four persona scores of 4, no refusal, and yes for other decisions. The extractor returns one fixed triple. These choices deliberately produce failing quality gates. No gold labels or expected answers are passed to the model or extractor.

Missing observations are JSON `null` and fail their gate. Memory runs score 3 conversations on `dev` and 6 on `heldout`, and report the selected split. Dev results are diagnostic; only the completed held-out sets with real models can establish the release gates in design section 10.

`evals.run.evaluate` accepts `model`, `judge` and `extractor` callables, each taking a list of `{role, content}` messages and returning text. It reads the fenced System prompt from `persona/ames.md` verbatim, maps dataset role `ames` to `assistant`, and parses replies with `server/control_tokens.py`. The judge returns a JSON object with `in_character`, `advice_rules`, `spoken_length` and `pushback` integer scores for persona, and yes or no for other decisions. Fenced JSON and punctuated yes/no replies are normalised. Unparseable judge replies are recorded per case in `judge_errors` and receive a failing score. An unparseable refusal judgment counts as a refusal. Judge instructions request only scores or yes/no, never prohibited content.

Memory extraction runs separately on each session. Duplicate facts are combined within a conversation; facts match gold triples after trimming and case folding each field. Precision and recall pool fact counts across conversations. Questions receive only the extracted facts, with no source conversation or reference answer; the judge compares each reply with the reference answer. The extraction callable returns a JSON list of `{subject, predicate, object}` string triples. Pass `extraction_prompt` to `evaluate`, or `--extraction-prompt path/to/prompt.txt` to the CLI, to supply the extraction instructions. The fixed stub extractor ignores those instructions. This harness does not test database retrieval or deletion; those belong to the memory slices.

The real-model runner for issue #12 is `python -m evals.llm_bakeoff`. Commands, candidate pins, measurement definitions and the results template live in [the LLM bake-off](../docs/evals/llm-bakeoff.md).

## Tool calling

The new tool set uses the same runner with deterministic mock receipts and no judge:

```sh
python -m evals.run tools --split dev --out /tmp/tools-dev.json
python -m evals.run tools --split heldout --repeats 3 --out /tmp/tools-heldout.json
```

Unlike the frozen sets above, tools has no approved quality gates: a completed
nonempty run reports `MEASURED`, never `PASS`. Call/no-call, tool, argument,
over-call, malformed-call and crisis-priority metrics are independent. Arguments
use authored required/forbidden concepts, with no allowed-word whitelist. They accept
fragments and third-person phrasing, reject reversed meaning and catch known
unsupported additions; the score remains a lexical proxy for human review.
Only explicit save requests permit `remember`; incidental durable facts and trivia
saves count as over-calling. Any attempted call on a no-call case counts, including
malformed calls after filler. Mock continuation validity is measured separately. See the
[protocol](../docs/tool-calling.md) for denominators, case counts and limits.
The historical model bake-off roster is unchanged; use `evaluate("tools", ...)`
for future candidate adapters. No real tools run.


| Tool category | Dev | Heldout | Total |
|---|---:|---:|---:|
| `recall_memory` | 6 | 3 | 9 |
| `remember` | 6 | 3 | 9 |
| `web_search` | 4 | 2 | 6 |
| `get_time` | 4 | 2 | 6 |
| `no_tool` | 14 | 7 | 21 |
| `ambiguous` | 4 | 2 | 6 |
| `crisis` | 4 | 2 | 6 |

Coverage includes implicit recall without lookup cues (3), answers already present
in long conversations (3), incidental durable facts that must not be saved (3),
and transient trivia that must not be saved (3). Three additional long conversations
end in explicit save requests, for six 7–9-turn cases overall. The six ambiguous
cases all require clarification without calling a tool.
