# Voice bake-off (issue #21)

Status: harness ready for maintainer validation. No hardware measurements, listening
scores, model choice, or supported tag list have been established. Design sections
3 and 7 remain unchanged.

## Run the harness

From the repository root, after `uv sync --locked`, check the runner without models:

```sh
AMES_STUB=1 uv run --locked python -m evals.voice_bakeoff --out /tmp/ames-voice-stub
AMES_STUB=1 uv run --locked pytest -q evals/tests/test_voice_bakeoff.py
```

The output directory must be new. Stub transcripts are fixed and stub audio is a
tone: their numbers and listening clips are not model or hardware evidence.

For the real run, use the idle reference M4 Pro Mac with 24 GB RAM. The maintainer
must install and record a compatible `mlx-audio` version alongside the `mac` extra
in a separate benchmark environment. This PR does not add a runtime dependency or
download anything. Download all candidate files, their auxiliary assets, and the
conversation LLM separately before running. Confirm model licenses in L1.

Make a local JSON config with `llm` and `candidates` objects. `candidates` must
contain `whisper`, `parakeet`, `nemotron`, `orpheus`, `chatterbox`, `ming`, `qwen`,
and `kokoro`. Each entry, including `llm`, requires an existing local directory
`path`, a pinned `revision`, and a `quantization` label. Example entry shape:

```json
{
  "path": "/absolute/path/to/local/model",
  "revision": "replace-with-verified-revision",
  "quantization": "replace-with-measured-tier",
  "options": {},
  "instructions": {}
}
```

Candidate source repos are recorded in `evals/voice_bakeoff.py:CANDIDATES` from
issue #21. Its source repos mix 4bit, 8bit and bf16: prepare and document local
conversions to a common tier before comparing. The harness requires equal voice
candidate tier labels; it cannot verify weight precision or revision from labels.
The LLM may use its own tier. Keep the same conversation LLM throughout.

`options` passes model-specific generation arguments to mlx-audio. For TTS, set
the supported voice and streaming options explicitly (for example `voice` and
`stream`); models with one whole-waveform output are timed as one chunk, without
pretending to stream. `instructions` optionally maps the sentence styles `sassy`,
`angry`, `sad`, `deadpan`, and `tagged` to supported `instruct` strings. Keep tags
in the text for the tag probes. Verify actual support and pin all these settings
before comparison; unsupported APIs produce an error record rather than a score.

```sh
AMES_STUB=0 path/to/benchmark-python -m evals.voice_bakeoff \
  --config /absolute/path/to/voice-models.json --out /absolute/path/to/new-run
```

The runner sets Hugging Face offline mode before loading local models. No server,
microphone or audio device is used. Confirm the prepared environment works with
outbound network blocked. The adapters use mlx-audio's in-process
[STT loader](https://github.com/Blaizzy/mlx-audio/blob/main/mlx_audio/stt/utils.py)
and [TTS generation interface](https://github.com/Blaizzy/mlx-audio/blob/main/mlx_audio/tts/generate.py);
their behavior with these exact candidates still needs real-model verification.

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
  system memory or an MLX allocator-only measurement.
- Numeric budget flags check p90 STT latency at most 200 ms, p90 TTS first chunk
  at most 400 ms, p90 TTS RTF at most 0.9, and process peak RSS at most 24 GiB.
  Inspect memory pressure and swap separately: process RSS below 24 GiB does not
  establish that the whole Mac fits. No extra STT RTF threshold is invented.
- `oom`, `error`, `killed`, and `timeout` records miss the budgets and do not stop
  later candidates. A killed process is not automatically classified as OOM.
  Each candidate has a one-hour safety timeout. A run exiting successfully means
  it wrote the report; inspect every candidate status and budget flag.

The new run directory contains `results.json` (checkpointed after every candidate),
per-candidate JSON with timings and samples, `clips/`, `listening.csv`, and
`reveal-key.json`. The report includes model config, platform, Python and installed
MLX package versions. Record the git commit and exact hardware below as well.

## Blind listening

Give the scorer only `clips/` and `listening.csv`. Files have random names and the
CSV order is shuffled. Keep all JSON files hidden until scoring is complete:
they contain the model key. One clip per sentence from the first measured pass
is retained, for up to 100 clips. Failed candidates may leave orphan clips;
score only CSV-listed files.

The maintainer fills integer scores 1 to 5 for expressiveness and naturalness,
with 1 meaning poor and 5 excellent, and optional notes. After locking those
scores, reveal the key and annotate each tagged/instructed clip's
`tag_or_instruction_rendered` as yes/no/unclear. Do not infer rendering from a
model accepting the text. Report rendered/attempted counts per tag or instruction,
and separate expressiveness and naturalness means per candidate. Rank eligible
TTS candidates by the mean of those two listening scores, and eligible STT
candidates by lowest WER. Leave ties for the maintainer; there is no automatic
winner or fallback selection.

## Run record (maintainer fills)

Date, git commit, macOS, chip, RAM, power/thermal conditions: pending.
Conversation LLM repo/revision/tier and cache setup: pending.
Candidate local conversions, revisions, voices, streaming and emotion settings: pending.
Package versions, offline check, full result directory: pending.

| STT | Status | WER | p50/p90 final ms | p50/p90 RTF | Peak RSS | Cold load | Budget notes |
|---|---|---|---|---|---|---|---|
| Whisper large-v3-turbo | pending | | | | | | |
| Parakeet TDT 0.6B v2 | pending | | | | | | |
| Nemotron Speech Streaming EN 0.6B | pending | | | | | | |

| TTS | Status | p50/p90 first chunk ms | p50/p90 RTF | Peak RSS | Cold load | Underruns | Budget notes |
|---|---|---|---|---|---|---|---|
| Orpheus 3B | pending | | | | | | |
| Chatterbox Turbo | pending | | | | | | |
| Ming-3.0-tts 0.5B | pending | | | | | | |
| Qwen3-TTS 1.7B CustomVoice | pending | | | | | | |
| Kokoro 82M (speed floor) | pending | | | | | | |

| TTS | Clips scored | Expressiveness mean | Naturalness mean | Tags/instructions rendered/attempted |
|---|---|---|---|---|
| Orpheus | pending | | | |
| Chatterbox | pending | | | |
| Ming | pending | | | |
| Qwen | pending | | | |
| Kokoro | pending | | | |

Chosen STT and rationale: pending maintainer measurements.
Chosen TTS and rationale: pending maintainer listening and measurements.
Confirmed tag list or mood-to-instruction mapping: pending.

## Manual completion

Verify local candidate APIs, streaming (especially Chatterbox and Ming), gated
Orpheus assets and tags, comparable precision, real OOM behavior and system memory
pressure. Run on the reference Mac, fill the tables and listening scores, apply
the p90 budget rule, then record the maintainer's choice in design section 3 and
replace the provisional tags or instruction mapping in section 7. These are
maintainer checks, not claims made by the stub tests.
