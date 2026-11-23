# Eval sets

This directory holds frozen inputs for the release gates in `docs/design.md` section 10, and the runner that scores them. Each input file has one JSON object per line. Section 10 gives the schemas. Results go in `docs/evals/`, not here.

## Splits

All sets except latency have two splits:

- `dev`: use it to tune prompts.
- `heldout`: use it only to run gates. Do not read its replies while you tune.

The maintainer froze the sets in issue #3. A change to a frozen set needs its own issue.

## Sets

| Set | File | Target | Status |
|---|---|---|---|
| Persona | `persona/persona.jsonl` | 90 prompts: 60 dev, 30 heldout. Categories `thought`, `companion`, `edgy`. 20 are `edgy` (10 heldout): drugs, dark humour, relationships or sexual health with no explicit content. | Frozen |
| Crisis | `crisis/crisis.jsonl` | 60 conversations, 30 per split. Each split has 10 `label: 1` cases (3 indirect, 2 that build over several turns) and 20 `label: 0` cases that are dark but not a crisis. | Frozen |
| Guardrail | `guardrail/guardrail.jsonl` | 6 each for G2, G3, G4. Half are heldout. | Frozen |
| Memory | `memory/memory.jsonl` | 9 multi-session conversations: 3 dev, 6 heldout. 56 gold facts that the user stated (20 dev, 36 heldout). 72 questions (48 heldout). | Frozen |
| Tools | `tools/tools.jsonl` | 63 conversations: 42 dev, 21 heldout. 30 need a call, 33 need no call. | Review draft. See [format and scoring](../docs/tool-calling.md). |
| Latency | `latency/manifest.jsonl` | 30 user turns. 16 kHz mono WAV at `latency/wav/NN.wav`. | Frozen |

## Rules for authors

- Write turns as transcribed speech: lower case, no markdown, as Whisper returns them. A conversation always ends on a user turn.
- Crisis: when a reply has `crisis=1`, the server ends the session. Each false positive stops a real conversation, so the `label: 0` cases are as important as the `label: 1` cases. Do not put method detail in any case.
- Guardrail G2 and G3 cases describe the request only. They never contain prohibited content. G2 covers requests for sexual content from all users, adult or minor.
- Memory gold facts are only things that the user said. Write each one as a `subject`, `predicate`, `object` triple with `user` as the subject.
- Memory records `memory-001` to `memory-003` are `dev`. Records `memory-004` to `memory-009` are `heldout`.

## Check the schemas

```sh
python evals/check.py
```

The script checks the schema of each record. It fails on any error. It reports a set that has fewer records than its target as pending.

## How a run works

```mermaid
flowchart LR
    set[("Eval set<br/>*.jsonl, one split")]
    persona["persona/ames.md<br/>System prompt"]
    model["Model<br/>stub or real"]
    parse["control_tokens.py<br/>reads crisis flag"]
    extract["Extractor<br/>memory set only"]
    judge["Judge<br/>scores or yes/no"]
    score["scoring.py<br/>metrics and gates"]
    out[("--out JSON<br/>no conversation text")]

    set --> model
    persona --> model
    model --> parse --> judge
    set --> extract -- "facts for questions" --> model
    judge --> score --> out
```

`--repeats N` runs this flow N times and scores the mean.

## Run with stubs

From the repository root, after `uv sync --locked`:

```sh
uv run --locked python -m evals.run persona --split dev --out /tmp/persona-dev.json
uv run --locked python -m evals.run crisis --split heldout --out /tmp/crisis-heldout.json
uv run --locked python -m evals.run guardrail --split dev --out /tmp/guardrail-dev.json
uv run --locked python -m evals.run memory --split dev --out /tmp/memory-dev.json
uv run --locked python -m evals.run memory --split heldout --repeats 3 --out /tmp/memory-heldout.json
uv run --locked pytest -q evals/tests
```

The CLI always uses the fixed model, judge, and extractor in `evals/stubs.py`. It needs no model downloads, network, GPU, microphone, or browser.

- The maintainer runs evals locally on a Mac.
- CI tests the runner only through pytest in `evals/tests/test_run.py`.
- Latency belongs to S2a. This command does not run it.

### Output and exit code

The command prints metrics, and PASS or FAIL for each threshold in `evals/scoring.py`. The thresholds are proposals. The JSON file contains counts, metrics, gates, and per-case scores. It contains no conversation text and no reply text.

| Exit code | Meaning |
|---|---|
| 0 | The evaluation completed. A score gate can still have failed. |
| Not 0 | An error occurred while the runner read inputs, ran models, or wrote results. |

A consumer that needs a score gate must read the JSON `status`.

### Repeats

`--repeats N` runs the selected set and split N times. N is a positive integer. The default is 1.

- The console shows the metrics of each run and their arithmetic mean.
- The final gates and status use the mean.
- If a run has no observations for a metric, the mean of that metric is `null`, and its gate fails.
- JSON `runs` contains counts, metrics, gates, and cases for each run. Top-level `metrics`, `gates`, and `status` describe the mean.
- A single run also keeps top-level `counts` and `cases` for existing consumers.

Heldout gates use `--repeats 3`. Repeats reduce sampling noise only when the model samples (temperature above 0). Deterministic models and the fixed stubs give the same results on each repeat.

### What the stubs do

Stubs test the harness, not model quality. The stub choices make the quality gates fail on purpose:

- The model always gives a valid neutral token with no crisis, so crisis recall is zero.
- The judge always gives four persona scores of 4, no refusal, and yes for other decisions.
- The extractor always returns one fixed triple.

The runner gives no gold labels or expected answers to the model or the extractor.

A missing observation is JSON `null`, and its gate fails. Memory runs score 3 conversations on `dev` and 6 on `heldout`, and report the selected split. Dev results are for diagnosis only. Only complete held-out runs with real models can set the release gates in design section 10.

## Runner interface

`evals.run.evaluate` accepts three callables: `model`, `judge`, and `extractor`. Each callable takes a list of `{role, content}` messages and returns text.

The runner does these steps:

1. It reads the fenced System prompt from `persona/ames.md` without changes.
2. It maps the dataset role `ames` to `assistant`.
3. It parses each reply with `server/control_tokens.py`.

For persona, the judge returns a JSON object with the integer scores `in_character`, `advice_rules`, `spoken_length`, and `pushback`. For other decisions, the judge returns yes or no. The runner accepts fenced JSON and yes or no with punctuation.

If the runner cannot parse a judge reply, it records the reply in `judge_errors` for that case and gives a failing score. An unparseable refusal judgment counts as a refusal. The judge instructions ask only for scores or yes or no, never for prohibited content.

### Memory scoring

1. Extraction runs on each session separately.
2. The runner combines duplicate facts in one conversation.
3. A fact matches a gold triple when each field is the same after trimming and case folding.
4. Precision and recall pool the fact counts across conversations.
5. Each question gets only the extracted facts. It gets no source conversation and no reference answer.
6. The judge compares each reply with the reference answer.

The extraction callable returns a JSON list of `{subject, predicate, object}` string triples. To supply the extraction instructions, pass `extraction_prompt` to `evaluate`, or `--extraction-prompt path/to/prompt.txt` to the CLI. The fixed stub extractor ignores those instructions. This harness does not test database retrieval or deletion. Those belong to the memory slices.

## LLM bake-off

The real-model runner for issue #12 is `python -m evals.llm_bakeoff`. The commands, candidate pins, measurement definitions, and results template are in [the LLM bake-off](../docs/evals/llm-bakeoff.md).

## Tool calling

The tools set uses the same runner, with fixed mock receipts and no judge:

```sh
python -m evals.run tools --split dev --out /tmp/tools-dev.json
python -m evals.run tools --split heldout --repeats 3 --out /tmp/tools-heldout.json
```

The tools set has no approved quality gates, unlike the frozen sets. A complete run with results reports `MEASURED`, never `PASS`. No real tools run.

These metrics are independent:

- Call or no call.
- Tool choice.
- Arguments.
- Over-calling.
- Malformed calls.
- Crisis priority.

Argument scoring uses required and forbidden concepts that the authors wrote. There is no list of allowed words. The scorer accepts fragments and third-person wording. It rejects a reversed meaning and catches known unsupported additions. The score is a lexical proxy, and a human must still review it.

Only an explicit request to save permits `remember`. If the model saves an incidental durable fact or trivia, it counts as over-calling. Each attempted call on a no-call case counts, also a malformed call after filler. The validity of the mock continuation has its own measurement. The [protocol](../docs/tool-calling.md) gives the denominators, case counts, and limits.

The historical roster of the model bake-off does not change. Use `evaluate("tools", ...)` for future candidate adapters.

| Tool category | Dev | Heldout | Total |
|---|---:|---:|---:|
| `recall_memory` | 6 | 3 | 9 |
| `remember` | 6 | 3 | 9 |
| `web_search` | 4 | 2 | 6 |
| `get_time` | 4 | 2 | 6 |
| `no_tool` | 14 | 7 | 21 |
| `ambiguous` | 4 | 2 | 6 |
| `crisis` | 4 | 2 | 6 |

The coverage includes:

- 3 cases of implicit recall with no lookup cue.
- 3 cases where the answer is already in a long conversation.
- 3 incidental durable facts that the model must not save.
- 3 transient trivia items that the model must not save.
- 3 more long conversations that end in an explicit request to save. With the 3 long conversations above, this gives six cases of 7 to 9 turns.
- 6 ambiguous cases. Each one needs a clarifying question and no tool call.
