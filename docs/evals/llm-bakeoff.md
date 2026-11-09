# LLM bake-off

Issue #12 delivers the runner and this empty results template. Measurements and selection are pending the maintainer's reference-Mac run. The reference machine is a MacBook Pro with an Apple M4 Pro and 24 GB RAM.

## Run

Run these commands from the repo root. Before the offline runs, download every candidate and the judge into the Hugging Face cache. This step needs network access and reads IDs and revisions directly from the runner's pinned table. Set `HF_HOME` to the desired cache root and keep it the same for downloads and runs.

```sh
env -u HF_HUB_OFFLINE .venv/bin/python - <<'PY'
from huggingface_hub import snapshot_download
from evals.llm_bakeoff import CANDIDATES, JUDGE

for repo, revision, loader in [*CANDIDATES.values(), JUDGE]:
    snapshot_download(repo, revision=revision)
PY
```

Each command below evaluates one candidate in a fresh process, writes one JSON result, and exits. Use separate processes so RSS and MLX peaks belong to one candidate. The benchmark runs before Phi-4 loads. Model loading time is excluded from TTFT.

Run the eight mlx-lm entries in the measured server environment with mlx-lm 0.32.2 installed. Unset `AMES_STUB` for real measurements.

```sh
for candidate in qwen35-2b qwen35-4b qwen3-4b gemma4-e4b gemma4-e2b bolt-e4b nemotron-4b qwen35-9b
do
  env -u AMES_STUB HF_HUB_OFFLINE=1 .venv/bin/python \
    -m evals.llm_bakeoff --candidate "$candidate" --out "docs/evals/llm-bakeoff-results/$candidate.json"
done
```

Bonsai 2 and LFM3 use the pinned bake-off overlay. The VLM loader supplies their architectures and quantization. A text-decoder adapter exposes their logits to the same mlx-lm sampler and cache path. No images are supplied. A missing overlay fails that candidate with a command to install it. Keep `--extra mac` so uv retains the server's mlx-lm and mlx-audio dependencies when syncing the project environment. The overlay resolves mlx-vlm 0.7.12, mlx-lm 0.32.2 and mlx-audio 0.6.4 in its own environment.

```sh
for candidate in bonsai2-2b lfm3-1b7
do
  env -u AMES_STUB HF_HUB_OFFLINE=1 uv run --extra mac --with mlx-vlm==0.7.12 \
    python -m evals.llm_bakeoff --candidate "$candidate" --out "docs/evals/llm-bakeoff-results/$candidate.json"
done
```

CI exercises the CLI through pytest with stubs. A direct smoke run uses the following command.

```sh
AMES_STUB=1 .venv/bin/python \
  -m evals.llm_bakeoff --candidate qwen35-2b --out docs/evals/llm-bakeoff-stub.json
```

Exit 0 means the run completed. Inspect JSON `status` for quality PASS or FAIL. ERROR exits 2 and retains candidate identity, prompt hashes and the error in the result file. Loops continue to the next candidate after an error. Stub outputs have `backend=stub`, synthetic timing, and null memory numbers.

## Method

Every conversation and memory question uses `server.prompt.PromptAssembler`, with the candidate tokenizer counting history tokens. The resulting messages pass through the candidate's real chat template. A template exception fails the candidate. Templates receive `enable_thinking=False`, including every Qwen3.5 run.

Runner defaults fix candidate and extraction sampling at temperature 0.7, top-p 0.9, maximum 256 tokens. The token limit reuses `server.llm.MAX_REPLY_TOKENS` to match production. Temperature and top-p are an implementation choice for maintainer review before reading gates. Phi-4 judges greedily with a 256-token limit. All four held-out sets run three times, and gates use the arithmetic mean of each metric. Per-run and per-case results remain in JSON. Guardrails run alongside persona, crisis and memory because they are part of the existing held-out harness.

Judge replies accept fenced JSON scores and punctuated yes/no answers. Unparseable replies are recorded under each case's `judge_errors` and receive a failing score. An unparseable refusal judgment counts as a refusal. Malformed control tokens earn no mood credit. Invalid extraction JSON fails the candidate and preserves earlier completed sets.

Extraction runs once per session, produces flat triples, and receives the full eval predicate vocabulary. Gold subjects, objects and reference answers stay out of candidate/extractor inputs. Triples still match exactly after trimming and case folding. Object paraphrases can therefore lose credit. This measures the frozen extraction harness, with its session-level schema, ahead of the production extraction slice.

The long-session probe starts with an empty history and appends user turns and actual generated replies. It continues for five turns after the uncapped history first exceeds 3000 tokens. Each turn uses the current 3000-token assembler cap and records retained history tokens, reused-prefix tokens, TTFT, generation tokens/s, and prefill milliseconds. The synthetic user asks about work, pottery and gardening. Per-turn samples remain in JSON.

Cache reuse comes from `LRUPromptCache.fetch_nearest_cache`, using the server's leave-one-token convention. The runner reports the returned prefix length. A longer cache with an untrimmable sliding-window or hybrid state cannot reuse a changed prefix. The post-cap budget field requires every measured post-cap prefill to be at most 150 ms. If the real run misses that budget, the maintainer decides whether to change design 5.2 to trim in chunks down to about 2000 tokens.

TTFT is wall time from template rendering through the first streamed token. Prefill milliseconds derive from the runtime's prompt-token count divided by prompt throughput, including its first-token work. Tokens/s uses the runtime's generation throughput. Summary TTFT and tokens/s are means over the long-session probe. Peak MLX bytes and process peak RSS are captured after candidate loading and the probe, before loading the judge. RSS includes interpreter and library overhead. The two peaks overlap in unified memory and should be reported separately.

Each result records the approved model and judge IDs/revisions, resolved snapshot revisions, package versions, sampling, host information, system-prompt and extraction-prompt SHA-256 hashes, hashes of judge system instructions actually used, and the runner source hash covering judge prompt definitions. Compare hashes and settings across runs before comparing scores.

## Quality results

Each value is the mean of three held-out runs. Empty cells await real measurements. Crisis FP and malformed columns are counts. Guardrails list G2/G3/G4 pass rates.

| Candidate | Revision | Persona pass | Mood | Edgy refusals | Crisis recall | Crisis FP | Crisis malformed | G2/G3/G4 | Memory precision/recall/answers | Quality status |
|---|---|---|---|---|---|---|---|---|---|---|
| qwen35-2b | 93760be | | | | | | | | | |
| qwen35-4b | 32f3e8e | | | | | | | | | |
| qwen3-4b | 50d4277 | | | | | | | | | |
| gemma4-e4b | 475b908 | | | | | | | | | |
| gemma4-e2b | 2387675 | | | | | | | | | |
| bolt-e4b | 4274caa | | | | | | | | | |
| nemotron-4b | c4d79ba | | | | | | | | | |
| bonsai2-2b | 1c6cbae | | | | | | | | | |
| lfm3-1b7 | 91f3408 | | | | | | | | | |
| qwen35-9b baseline | 938d891 | | | | | | | | | |

## Performance results

Report per-turn reuse and post-cap prefill from each JSON alongside this summary. The reused-prefix column should show the measured range over warm turns.

| Candidate | TTFT mean ms | Generation mean tokens/s | Peak MLX GiB | Peak RSS GiB | Reused prefix tokens | Post-cap prefill mean ms | Every post-cap turn <= 150 ms |
|---|---|---|---|---|---|---|---|
| qwen35-2b | | | | | | | |
| qwen35-4b | | | | | | | |
| qwen3-4b | | | | | | | |
| gemma4-e4b | | | | | | | |
| gemma4-e2b | | | | | | | |
| bolt-e4b | | | | | | | |
| nemotron-4b | | | | | | | |
| bonsai2-2b | | | | | | | |
| lfm3-1b7 | | | | | | | |
| qwen35-9b baseline | | | | | | | |

## Maintainer selection

- Agreed margin from baseline, fixed before comparing scores, is pending.
- Chosen model and reasoning are pending.
- License decisions for Gemma terms, NVIDIA Open Model License and LFM3 are pending before selection/L2. LFM3's commercial-use revenue limit requires a maintainer decision.
- The chosen model and agreed margin must be recorded in Notion after the real run.
- Distillation #29 runs on the pick afterwards.
