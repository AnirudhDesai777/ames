# Voice bake-off (issue #21)

Status: run 2 measurements, listening scores and model choices are recorded.
Style instructions remain provisional because rendering was not rated. Choices
are recorded in design sections 3 and 7 (#69).

## Environment (from the repository root)

Use Python 3.12 and uv on the reference M4 Pro Mac with 24 GB RAM. All commands
below start at the repository root and use the same shell. Nothing in the stub
check downloads or loads models:

```sh
uv sync --locked
STUB_OUT="$(mktemp -d)/run"
AMES_STUB=1 uv run --locked python -m evals.voice_bakeoff --out "$STUB_OUT"
AMES_STUB=1 uv run --locked pytest -q evals/tests/test_voice_bakeoff.py
```

The output directory must be new. Stub transcripts and tones are not hardware
or model evidence. `mktemp` makes the command safe to repeat.

For real models, prepare the environment while online. The mac extra pins
`mlx-audio==0.6.1`, which requires the already locked `mlx==0.32.4`. Kokoro also
needs Misaki's English pronunciation dependencies and a spaCy English asset;
these benchmark-only packages are installed explicitly below. Keep the resulting
package inventory with the run. Use `.venv/bin/python` after this setup so uv does
not remove those additional packages during a sync.

```sh
uv sync --locked --extra mac
uv pip install --python .venv/bin/python 'misaki[en]==0.9.4'
export HF_HOME="$HOME/Library/Application Support/Ames/models"
export BAKEOFF_SETUP="$HF_HOME/voice-bakeoff"
mkdir -p "$BAKEOFF_SETUP"
unset HF_HUB_OFFLINE
```

Use `mlx-community/Qwen3-4B-4bit` as the fixed conversation LLM for this run.
Design section 3 defers the production choice to #12. This is an explicit 4B,
4bit reference load with thinking disabled by `MLXLanguageModel`, leaving room
for voice candidates on 24 GB. It is not the LLM bake-off winner. Repeat the
winning voice checks with the final conversation LLM when #12 settles it.

## Online downloads (maintainer only)

Accept any required licenses/access on Hugging Face first (`.venv/bin/hf auth login`
if a repository requires it). Each command below downloads one candidate or named
auxiliary asset into the exact HF_HOME that `server/llm.py:configure_models` uses.
Do not use `--local-dir`: auxiliary loaders need the Hub cache and its `main` ref.
Commands resolve current `main`; the config step records each resolved immutable
snapshot SHA. Archive the path files and inventory, and do not refresh the cache
between candidates.

```sh
.venv/bin/hf download -q mlx-community/Qwen3-4B-4bit > "$BAKEOFF_SETUP/llm.path"
.venv/bin/hf download -q mlx-community/whisper-large-v3-turbo > "$BAKEOFF_SETUP/whisper.path"
.venv/bin/hf download -q mlx-community/parakeet-tdt-0.6b-v2 > "$BAKEOFF_SETUP/parakeet.path"
.venv/bin/hf download -q nvidia/nemotron-speech-streaming-en-0.6b > "$BAKEOFF_SETUP/nemotron.path"
.venv/bin/hf download -q mlx-community/orpheus-3b-0.1-ft-4bit > "$BAKEOFF_SETUP/orpheus.path"
.venv/bin/hf download -q mlx-community/chatterbox-turbo-4bit > "$BAKEOFF_SETUP/chatterbox.path"
.venv/bin/hf download -q mlx-community/Ming-3.0-tts-0.5B-bf16 > "$BAKEOFF_SETUP/ming.path"
.venv/bin/hf download -q mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit > "$BAKEOFF_SETUP/qwen.path"
.venv/bin/hf download -q mlx-community/Kokoro-82M-bf16 > "$BAKEOFF_SETUP/kokoro.path"

# WhisperProcessor files missing from the MLX Whisper repository; no OpenAI weights.
.venv/bin/hf download -q openai/whisper-large-v3-turbo --include '*.json' --include merges.txt > "$BAKEOFF_SETUP/whisper-processor.path"
# Orpheus loads this tokenizer by repo ID; exclude the redundant bf16 model weights.
.venv/bin/hf download -q mlx-community/orpheus-3b-0.1-ft-bf16 --include '*.json' --include merges.txt > "$BAKEOFF_SETUP/orpheus-tokenizer.path"
# Orpheus SNAC decoder, Chatterbox speech tokenizer, Kokoro default voice cache.
.venv/bin/hf download -q mlx-community/snac_24khz > "$BAKEOFF_SETUP/snac.path"
.venv/bin/hf download -q mlx-community/S3TokenizerV2 > "$BAKEOFF_SETUP/s3tokenizer.path"
.venv/bin/hf download -q prince-canuma/Kokoro-82M --include 'voices/af_heart*' > "$BAKEOFF_SETUP/kokoro-voice.path"
# Misaki English spaCy asset (not a voice candidate).
mkdir -p "$BAKEOFF_SETUP/spacy-3.8"
curl -fL -o "$BAKEOFF_SETUP/spacy-3.8/en_core_web_sm-3.8.0-py3-none-any.whl" https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl
printf '%s  %s\n' '1932429db727d4bff3deed6b34cfc05df17794f4a52eeb26cf8928f7c1a0fb85' "$BAKEOFF_SETUP/spacy-3.8/en_core_web_sm-3.8.0-py3-none-any.whl" | shasum -a 256 -c
```

The pinned NVIDIA Nemotron snapshot (`ebe59e5`) ships `model.safetensors`
(651 float32 tensors) and a transformers-style `config.json`
(`NemotronAsrStreamingForRNNT`) next to the `.nemo` file. mlx-audio 0.6.1 loads
it directly with `model_type="nemotron_asr"`; no conversion step runs. The
2026-11-08 check re-transcribed the 30 latency WAVs at WER 0.8%;
the only errors were "goodnight ames" heard as "Good night, Ames." It ran
non-streaming with no options, so streaming mode remains untested. The same
check on Parakeet gave WER 1.6%: the same "goodnight" split, "summarize" spelled
"summarise", and one real mishearing ("wait" heard as "Pete").

Install the pinned spaCy 3.8.0 wheel and assemble a local Whisper directory with the
MLX weights/config and OpenAI processor files. Cached snapshots remain untouched:

```sh
.venv/bin/python - <<'PY'
import os
from pathlib import Path

setup = Path(os.environ["BAKEOFF_SETUP"])
def snapshot(key):
    return Path((setup / f"{key}.path").read_text().strip())

(setup / "spacy-wheel.path").write_text(
    str(setup / "spacy-3.8" / "en_core_web_sm-3.8.0-py3-none-any.whl"))

prepared = setup / "whisper-prepared"
prepared.mkdir(exist_ok=True)
for source in (snapshot("whisper"), snapshot("whisper-processor")):
    for file in source.iterdir():
        target = prepared / file.name
        if file.is_file() and not target.exists():
            target.symlink_to(file)
PY
uv pip install --python .venv/bin/python "$(cat "$BAKEOFF_SETUP/spacy-wheel.path")"
.venv/bin/python -c "import numpy; assert numpy.__version__ == '2.5.4'"
uv pip freeze --python .venv/bin/python > "$BAKEOFF_SETUP/packages.txt"
```

## Filled example config: verify before scoring

The following runnable step writes all eight entries, concrete voice/speaker and
generation options, local paths and snapshot pins. It uses the downloaded source
tiers (mixed across and within roles), not a claim of equal precision. Verify the
unquantized STT dtypes from actual tensors before reporting them; labels and repo
names alone do not prove precision. Record local conversions separately if used.
The harness accepts different tiers and stores the whole config in `results.json`.

`model_type` selects mlx-audio's architecture explicitly: snapshot hash directory
names cannot identify Parakeet or Kokoro, and Ming's `dense` config needs the
`bailingmm` implementation. Empty `instructions` means no emotion instruction.
The optional `tag_format` defaults to `angle`, preserving `<tag>` text for existing
configs. This config keeps angle tags for Orpheus, rewrites the six probe tags to
square brackets for Chatterbox, and strips tags and their following space for
Kokoro, Ming and Qwen. `style_prefix` maps styles to text prepended with one space;
Chatterbox uses `[angry]`, `[sarcastic]` and `[crying]`, with no deadpan token.
`style_prefix` must be an object of strings.
Qwen's `voice` argument
is its CustomVoice speaker, not a separate `speaker` keyword. Ming's `US_James`
voice maps to a caption and cannot be combined with an `instruct` caption.

```sh
.venv/bin/python - <<'PY'
import json
import os
from pathlib import Path

setup = Path(os.environ["BAKEOFF_SETUP"])
def entry(key, tier, model_type=None, options=None, instructions=None,
          tag_format="angle", style_prefix=None):
    snapshot = Path((setup / f"{key}.path").read_text().strip())
    result = {"path": str(snapshot), "revision": snapshot.name,
              "quantization": tier, "options": options or {}, "instructions": instructions or {},
              "tag_format": tag_format, "style_prefix": style_prefix or {}}
    if model_type:
        result["model_type"] = model_type
    return result

config = {
    "verification": "verify: source tiers, offline loading, voices, streaming and instructions",
    "llm": entry("llm", "4bit"),
    "candidates": {
        "whisper": entry("whisper", "unquantized (verify dtype)", "whisper",
                         {"language": "en", "stream": False}),
        "parakeet": entry("parakeet", "unquantized (verify dtype)", "parakeet", {"stream": False}),
        "nemotron": entry("nemotron", "float32", "nemotron_asr"),
        "orpheus": entry("orpheus", "4bit", "llama",
                         {"voice": "tara", "stream": False}),
        "chatterbox": entry("chatterbox", "4bit", "chatterbox_turbo",
                            {"stream": True, "streaming_interval": 0.1},
                            tag_format="square",
                            style_prefix={"angry": "[angry]", "sassy": "[sarcastic]",
                                          "sad": "[crying]"}),
        "ming": entry("ming", "bf16", "bailingmm",
                      {"voice": "US_James", "lang_code": "en", "stream": False},
                      tag_format="none"),
        "qwen": entry("qwen", "8bit", "qwen3_tts",
                      {"voice": "Ryan", "lang_code": "english", "stream": True,
                       "streaming_interval": 0.1},
                      {"sassy": "Speak with a teasing, sarcastic tone.",
                       "angry": "Speak angrily.", "sad": "Speak sadly and quietly.",
                       "deadpan": "Speak in a flat, deadpan tone."}, tag_format="none"),
        "kokoro": entry("kokoro", "bf16", "kokoro", {"voice": "af_heart", "lang_code": "a"},
                        tag_format="none"),
    },
    "auxiliary_revisions": {
        key: Path((setup / f"{key}.path").read_text().strip()).name
        for key in ("whisper-processor", "orpheus-tokenizer", "snac", "s3tokenizer",
                    "kokoro-voice")
    },
    "spacy_wheel_version": "3.8.0",
}
config["candidates"]["whisper"]["path"] = str(setup / "whisper-prepared")
(setup / "voice-models.json").write_text(json.dumps(config, indent=2) + "\n")
print(setup / "voice-models.json")
PY
export HF_HUB_OFFLINE=1
REAL_OUT="$(mktemp -d)/run"
AMES_STUB=0 .venv/bin/python -m evals.voice_bakeoff \
  --config "$BAKEOFF_SETUP/voice-models.json" --out "$REAL_OUT"
```

Confirm this run also works with outbound network blocked. No server, microphone,
or audio device is used. Inspect every candidate's status and log; a zero runner
exit code only means the report was written. Chatterbox uses the checkpoint's
built-in `conds.safetensors` speaker (no `voice` option or reference recording).
Orpheus streaming in mlx-audio 0.6.1 repeats audio at chunk boundaries, so Orpheus
runs non-streaming here. Its first-chunk time is full-generation time, not streaming
latency. Chatterbox and Qwen use streaming; Ming ignores streaming arguments and
yields one waveform, and Kokoro yields whole text segments. Do not report either
of the latter as native audio streaming.

Source checks used the released
[mlx-audio 0.6.1 archive](https://pypi.org/project/mlx-audio/0.6.1/#files):
`mlx_audio/utils.py:base_load_model`, `stt/utils.py:load_model`, and each candidate's
`generate` method and load hooks under `stt/models/` or `tts/models/`. Auxiliary
lookups are in `tts/models/llama/llama.py`,
`tts/models/chatterbox_turbo/chatterbox_turbo.py`, and
`tts/models/kokoro/pipeline.py`. These source checks do not establish model quality
or compatibility with every downloaded revision.

## Measurement contract and output

Each candidate runs in a fresh process with a warmed conversation LLM resident in
that same process. This isolates process peak RSS and recovers from a failed
allocation or process death. The LLM is resident, not generating concurrently.
The candidate is loaded once, one full warm-up pass is discarded, then three
complete passes are measured. STT uses all 30 recorded WAVs and gold transcripts
in `evals/latency/`; TTS uses the 20 fixed sentences in `SENTENCES`, covering sassy,
angry, sad, deadpan and six provisional tags. No frozen inputs are edited.

- STT latency is wall time from handing the complete recorded utterance to the
  recognizer until its final transcript returns. It is an after-end-of-input
  batch measurement, including conversion and inference, not paced streaming or
  Smart Turn detection. Do not subtract the WAV duration. Native streaming may
  reduce end-of-speech latency and needs a separate maintainer check before a
  candidate is rejected on this conservative measurement.
- WER is pooled word edit distance divided by reference words across all samples,
  after case folding and removing punctuation. Blank hypotheses count as deletions;
  the recognizer never receives the gold transcript.
- TTS time to first chunk ends when nonempty mono PCM16 audio is materialized.
  RTF is full generation time divided by generated audio duration. A chunk-gap
  underrun means the next chunk arrived after buffered playback would have ended;
  file writes happen after timing. No browser playback is measured.
- p50/p90 use nearest rank across the three measured passes (90 STT samples or
  60 TTS samples). Cold-load time covers candidate loading and eager parameter
  evaluation, excluding LLM loading, imports before the timer, and the warm-up.
  Peak RSS is the fresh process lifetime high-water mark including the LLM,
  candidate loading, warm-up, and measured passes, in bytes. It is not total
  system memory. `peak_mlx_bytes` separately records `mx.get_peak_memory()` for
  the process lifetime, including the LLM and Metal allocations; it is null for
  stubs. RSS and allocator peaks overlap and must not be added together. Neither
  proves that the whole system fits in RAM.
- Numeric budget flags check p90 STT latency at most 200 ms, p90 TTS first chunk
  at most 400 ms and p90 TTS RTF at most 0.9. `within_budgets` is at the top
  level of every result. The old RSS-based memory budget is removed: a low RSS
  cannot prove system fit. Inspect memory pressure and swap separately. OOM is
  a missed budget; no extra STT RTF threshold is invented.
- `oom`, `error`, `killed`, and `timeout` records miss the budgets and do not stop
  later candidates. A killed process is not automatically classified as OOM.
  Each candidate has a one-hour safety timeout. A run exiting successfully means
  it wrote the report; inspect every candidate status and budget flag.

The new run directory contains `results.json` (checkpointed after every candidate),
per-candidate JSON with timings and samples, `<candidate>.log` with merged stdout
and stderr (also retained on kill or timeout), `source-clips/`, `clips/`,
`listening.csv`, and `reveal-key.json`. The report includes model config, platform,
Python and installed
MLX package versions. Record the git commit and exact hardware below as well.

## Blind listening

Give the scorer only `clips/` and `listening.csv`. Files have random names and the
CSV order is shuffled. Listening copies are created in shuffled order, resampled
to 48 kHz mono PCM16, and assigned one mtime. Native clips stay in `source-clips/`
for audit; do not give them to the scorer. Keep all JSON, logs and source clips
hidden until scoring is complete: they can identify the model. The CSV `style`
column describes only the non-identifying probe category. One clip per sentence
from the first measured pass is retained, for up to 100 clips. Failed candidates
may leave orphan source clips;
score only CSV-listed files.

The reveal key's `tag` preserves the original probe (such as `<laugh>`) even when
rewritten or stripped. `sent_text` records the exact text sent to the model.
`instruction` records the matching style prefix when present, otherwise the style's
`instructions` value. Stub runs with `--config` apply the same text transforms.

The maintainer fills integer scores 1 to 5 for expressiveness and naturalness,
with 1 meaning poor and 5 excellent, and optional notes. After locking those
scores, reveal the key and annotate each tagged/instructed clip's
`tag_or_instruction_rendered` as yes/no/unclear. Do not infer rendering from a
model accepting the text. Report rendered/attempted counts per tag or instruction,
and separate expressiveness and naturalness means per candidate. Rank eligible
TTS candidates by the mean of those two listening scores, and eligible STT
candidates by lowest WER. Leave ties for the maintainer; there is no automatic
winner or fallback selection.

After locking `listening.csv`, join on `file` (the one-to-one random identifier),
never row position. This command produces the table means and rendered/attempted
counts for each tag or instruction. `unclear` counts as attempted but not rendered;
blank rendering values are not rated and do not enter those counts. Whole
candidates may be excluded by removing all their rows from `listening.csv`.
Every keyed clip of a kept candidate must be listed. It rejects duplicate or
unknown clips and missing, non-integer or out-of-range scores for listed clips.
Keep its output hidden during scoring.

```sh
.venv/bin/python - "$REAL_OUT" <<'PY'
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

output = Path(sys.argv[1])
key = {clip["file"]: clip for clip in json.loads((output / "reveal-key.json").read_text())}
scores = defaultdict(list)
rendering = defaultdict(lambda: [0, 0])
seen = set()
with (output / "listening.csv").open() as stream:
    listening = list(csv.DictReader(stream))
    for row in listening:
        name = row["file"]
        if name in seen or name not in key:
            raise ValueError(f"Duplicate or unknown clip: {name}")
        seen.add(name)
        clip = key[name]
        values = [int(row[field]) for field in ("expressiveness_1_to_5", "naturalness_1_to_5")]
        if any(value not in range(1, 6) for value in values):
            raise ValueError(f"Scores must be 1 to 5: {name}")
        scores[clip["candidate"]].append(values)
        probe = clip["tag"] or clip["instruction"]
        if probe:
            rendered = row["tag_or_instruction_rendered"].strip().lower()
            if not rendered:
                continue
            if rendered not in {"yes", "no", "unclear"}:
                raise ValueError(f"Rate rendering yes/no/unclear: {name}")
            counts = rendering[(clip["candidate"], probe)]
            counts[0] += rendered == "yes"
            counts[1] += 1
listed = {key[name]["candidate"] for name in seen}
expected = {name for name, clip in key.items() if clip["candidate"] in listed}
if seen != expected:
    raise ValueError("Every keyed clip of a listed candidate must appear in listening.csv")
print("candidate,clips,expressiveness,naturalness,combined")
for candidate, rows in sorted(scores.items()):
    expressiveness, naturalness = [sum(column) / len(rows) for column in zip(*rows)]
    print(f"{candidate},{len(rows)},{expressiveness:.3f},{naturalness:.3f},"
          f"{(expressiveness + naturalness) / 2:.3f}")
for (candidate, probe), (rendered, attempted) in sorted(rendering.items()):
    print(candidate, repr(probe), f"{rendered}/{attempted}")
PY
```

## Run record (maintainer fills)

Run 2 finished on 2026-11-07 at 19:58, at git commit `ced07ee`, on macOS 26.5.2
with an Apple M4 Pro and 24 GB RAM. Power and thermal conditions were not recorded.
The result directory is `$BAKEOFF_SETUP/runs/20261107-1958`. All eight candidates
have status `ok`. An outbound-network-blocked check was not recorded in the
supplied results.

The resident conversation LLM was `mlx-community/Qwen3-4B-4bit`, revision
`4dcb3d1`, at 4bit, in the Hub cache under the `HF_HOME` from
`server/llm.py:configure_models`. Packages were mlx 0.32.4, mlx-lm 0.32.2,
mlx-audio 0.6.1, spaCy model wheel 3.8.0 and Python 3.12.12. Warm repeats: 3.

Candidate settings matched the example config: Orpheus voice `tara`,
non-streaming; Chatterbox streaming at a 0.1 s interval, square tags and style
prefixes angry=`[angry]`, sassy=`[sarcastic]`, sad=`[crying]`; Qwen voice `Ryan`,
streaming at a 0.1 s interval, tags stripped and the style instructions below;
Kokoro voice `af_heart`, tags stripped; Ming voice `US_James`, non-streaming,
tags stripped. Nemotron revision `ebe59e5` loaded directly without conversion;
the chosen Parakeet and Qwen revisions are recorded below. Other candidate
revisions remain in the archived run config.

Memory columns below use decimal GB (bytes / 1e9).

| STT | Status | WER | p50/p90 final ms | p50/p90 RTF | Peak RSS / MLX GB | Cold load s | Budget notes |
|---|---|---|---|---|---|---|---|
| Whisper large-v3-turbo | ok | 1.2% | 402 / 424 | 0.091 / 0.118 | 4.5 / 4.8 | 1.24 | misses p90 latency (200 ms) |
| Parakeet TDT 0.6B v2 | ok | 1.6% | 37 / 50 | 0.009 / 0.010 | 5.3 / 5.1 | 0.45 | within |
| Nemotron Speech Streaming EN 0.6B | ok | 0.8% | 117 / 183 | 0.029 / 0.031 | 5.3 / 4.9 | 0.44 | within |

Parakeet and Nemotron each make at most one real recognition error on this
30-clip set, so WER alone does not separate them.

| TTS | Status | p50/p90 first chunk ms | p50/p90 RTF | Peak RSS / MLX GB | Cold load s | Underruns | Budget notes |
|---|---|---|---|---|---|---|---|
| Orpheus 3B | ok | 3790 / 5514 | 0.791 / 0.795 | 5.1 / 7.9 | 1.89 | 0 | misses first chunk (400 ms); non-streaming by design |
| Chatterbox Turbo | ok | 213 / 214 | 0.491 / 0.520 | 3.8 / 4.1 | 0.80 | 0 | within |
| Ming-3.0-tts 0.5B | ok | 2651 / 3679 | 1.053 / 1.087 | 5.9 / 6.4 | 0.72 | 0 | misses first chunk and RTF (0.9) |
| Qwen3-TTS 1.7B CustomVoice | ok | 51 / 63 | 0.386 / 0.394 | 5.9 / 5.8 | 1.21 | 0 | within |
| Kokoro 82M (speed floor) | ok | 96 / 124 | 0.035 / 0.037 | 3.3 / 3.8 | 0.08 | 0 | within |

Ming was left out of listening because it missed both TTS budgets, so the
maintainer scored 80 clips on 2026-11-08.

| TTS | Clips scored | Expressiveness mean | Naturalness mean | Tags/instructions rendered/attempted |
|---|---|---|---|---|
| Qwen | 20 | 4.10 | 3.45 | not rated |
| Orpheus | 20 | 3.85 | 3.20 | not rated |
| Chatterbox | 20 | 2.50 | 1.95 | not rated |
| Kokoro | 20 | 1.65 | 1.45 | not rated |

Rendering was not rated because the maintainer chose not to do the per-clip
rendering pass.

Chosen STT: Parakeet TDT 0.6B v2 (`mlx-community/parakeet-tdt-0.6b-v2`, revision
`8ae1553`). It stays within the latency budget with the most headroom at p90 50 ms
against 200 ms. The maintainer overrode the WER ranking in favor of that latency
headroom because the gap between its WER of 1.6% and Nemotron's 0.8% amounts to
one real recognition error on this set.

Chosen TTS: Qwen3-TTS 1.7B CustomVoice, 8bit
(`mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit`, revision `41d3337`), voice
`Ryan`, streaming at a 0.1 s interval. It has the highest listening score
(combined 3.77) and lowest p90 first chunk (63 ms). Orpheus scored second but
its non-streaming p90 first chunk (5514 ms) misses the budget, so it is discarded.
The maintainer notes that naturalness (3.45) needs further work, possibly through
fine-tuning, later.

Tag list or mood-to-instruction mapping: Qwen does not take inline tags; they are
stripped. The run used these style instructions, which remain provisional because
rendering was not rated:

- sassy: "Speak with a teasing, sarcastic tone."
- angry: "Speak angrily."
- sad: "Speak sadly and quietly."
- deadpan: "Speak in a flat, deadpan tone."

## Manual completion

Verify local candidate APIs, streaming, gated Orpheus assets and tags, source
precision, real OOM behavior and system memory
pressure. The maintainer's choices are recorded in design sections 3 and 7 (#69).
These are maintainer checks, not claims made by the stub tests.

Additional manual checks (review items 13-15; no new benchmark mode):

- If Orpheus wins, fix or replace its streaming decode before production, then
  re-measure streaming latency and listen for repeated audio at chunk boundaries.
- This harness measures Whisper through mlx-audio. Production currently uses
  mlx-whisper via `WhisperSTTServiceMLX` in `server/cascade.py`. Before adopting
  Whisper numbers as production evidence, replay the same WAVs through that
  service and record the WER and latency difference.
- Re-measure the chosen TTS RTF while the conversation LLM actively generates,
  as production keeps decoding during speech (design 5.2). Record workload,
  p50/p90 RTF, first chunk latency and underruns; an idle resident LLM does not
  establish concurrent performance.
- Load the winning STT, TTS and conversation LLM together, fill the history to
  3000 tokens with the real prompt/cache, and record Activity Monitor memory
  pressure, swap growth, RSS and MLX allocator peak over repeated turns. The
  isolated worker runs never establish that the full pipeline fits in 24 GB.
