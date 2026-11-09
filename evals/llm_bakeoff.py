"""Pinned, local-only LLM bake-off. AMES_STUB=1 exercises the harness without MLX."""

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import resource
import time
from pathlib import Path
from statistics import mean

from evals import run
from evals.scoring import gates
from evals.stubs import stub_extractor, stub_model
from server.llm import MAX_REPLY_TOKENS
from server.prompt import HISTORY_TOKENS, PromptAssembler, load_system_prompt

# IDs, revisions and loaders approved in issue #12 on 2026-11-08.
CANDIDATES = {
    "qwen35-2b": ("mlx-community/Qwen3.5-2B-MLX-4bit", "93760be", "lm"),
    "qwen35-4b": ("mlx-community/Qwen3.5-4B-MLX-4bit", "32f3e8e", "lm"),
    "qwen3-4b": ("mlx-community/Qwen3-4B-Instruct-2507-4bit", "50d4277", "lm"),
    "gemma4-e4b": ("mlx-community/gemma-4-e4b-it-4bit", "475b908", "lm"),
    "gemma4-e2b": ("mlx-community/gemma-4-e2b-it-4bit", "2387675", "lm"),
    "bolt-e4b": ("lmstudio-community/NVIDIA-Nemotron-3.5-Bolt-E4B-MLX-4bit", "4274caa", "lm"),
    "nemotron-4b": ("mlx-community/NVIDIA-Nemotron-3-Nano-4B-4bit", "c4d79ba", "lm"),
    "bonsai2-2b": ("prism-ml/Ternary-Bonsai-2-2B-mlx-2bit", "1c6cbae", "vlm"),
    "lfm3-1b7": ("LiquidAI/LFM3-1.7B-MLX-4bit", "91f3408", "vlm"),
    "qwen35-9b": ("mlx-community/Qwen3.5-9B-MLX-4bit", "938d891", "lm"),
}
SAMPLING = {"temperature": 0.7, "top_p": 0.9, "max_tokens": MAX_REPLY_TOKENS}
REPEATS = 3


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class CallKeys:
    def __init__(self):
        self.occurrences = {}

    def key(self, messages):
        digest = sha256(json.dumps(messages, sort_keys=True, separators=(",", ":")))
        occurrence = self.occurrences.get(digest, 0)
        self.occurrences[digest] = occurrence + 1
        return f"{digest}:{occurrence}"


class RecordingModel:
    def __init__(self, model):
        self.model = model
        self.keys = CallKeys()
        self.calls = []

    def record(self, messages, callback):
        key = self.keys.key(messages)
        reply = callback(messages)
        self.calls.append({"key": key, "messages": messages, "reply": reply})
        return reply

    def __call__(self, messages):
        return self.record(messages, self.model)

    def conversation(self, messages):
        return self.record(messages, self.model.conversation)


class RecordingJudge:
    def __init__(self):
        self.keys = CallKeys()
        self.calls = []

    def __call__(self, messages):
        self.calls.append({"key": self.keys.key(messages), "messages": messages})
        return ""


class Replay:
    def __init__(self, rows, field):
        self.keys = CallKeys()
        self.answers = {}
        if not isinstance(rows, list):
            raise ValueError(f"{field} records must be a JSON list")
        for row in rows:
            key = row["key"]
            if key in self.answers:
                raise ValueError(f"duplicate {field} key: {key}")
            if not isinstance(row[field], str):
                raise ValueError(f"{field} must be text for key: {key}")
            self.answers[key] = row[field]

    def __call__(self, messages):
        key = self.keys.key(messages)
        if key not in self.answers:
            raise KeyError(f"missing replay key: {key}")
        return self.answers[key]


def read_rows(name):
    return [
        json.loads(line)
        for line in (run.ROOT / name / f"{name}.jsonl").read_text().splitlines()
        if line.strip()
    ]


def extraction_instructions():
    # Only the vocabulary is exposed, never gold subjects, objects or answers.
    predicates = sorted(
        {fact["predicate"] for row in read_rows("memory") for fact in row["gold_facts"]}
    )
    return run.EXTRACTION_PROMPT + " Use only these predicates: " + json.dumps(predicates) + "."


def assemble(assembler, messages):
    memory = ""
    turns = []
    for message in messages[1:]:
        if message["role"] == "system":
            memory = message["content"].removeprefix("Remembered facts: ")
        else:
            turns.append(message)
    if not turns or turns[-1]["role"] != "user":
        raise ValueError("conversation must end with a user turn")
    return assembler.assemble(turns[:-1], turns[-1]["content"], memory)


class StubModel:
    def __init__(self, entry, sampling):
        self.sampling = sampling
        self.resolved_revision = entry[1]
        self.assembler = PromptAssembler(self.count_tokens)
        self.samples = []

    def count_tokens(self, text):
        return len(text.split())

    def __call__(self, messages):
        if messages[0]["content"].startswith("Extract"):
            return stub_extractor(messages)
        return stub_model(messages)

    def conversation(self, messages):
        return self(assemble(self.assembler, messages))

    def measure(self, messages, *, warm=False):
        self.samples.append(
            {
                "ttft_ms": 1.0,
                "prefill_ms": 1.0,
                "tokens_per_second": 100.0,
                "reused_prefix_tokens": 0,
                "prompt_tokens": sum(self.count_tokens(m["content"]) for m in messages),
            }
        )
        return stub_model(messages)

    def memory(self):
        return {"peak_mlx_bytes": None, "peak_rss_bytes": None}


class LocalModel:
    def __init__(self, entry, sampling):
        if entry[2] == "vlm":
            try:
                from mlx_vlm import load as load_vlm
            except ImportError as error:
                raise RuntimeError(
                    "This candidate needs the bake-off overlay: "
                    "uv run --extra mac --with mlx-vlm==0.7.12 "
                    "python -m evals.llm_bakeoff ..."
                ) from error
        import mlx.core as mx
        from huggingface_hub import snapshot_download
        from mlx_lm import load, stream_generate
        from mlx_lm.models.cache import LRUPromptCache, make_prompt_cache
        from mlx_lm.sample_utils import make_sampler
        from mlx_lm.tokenizer_utils import TokenizerWrapper

        path = snapshot_download(entry[0], revision=entry[1], local_files_only=True)
        self.resolved_revision = Path(path).name
        if not self.resolved_revision.startswith(entry[1]):
            raise ValueError("local snapshot does not match the approved revision")
        mx.reset_peak_memory()
        if entry[2] == "vlm":
            loaded, processor = load_vlm(path)
            # Use only the text decoder; the VLM loader supplies newer architectures/quantization.
            import mlx.nn as nn

            class TextDecoder(nn.Module):
                def __init__(self, decoder):
                    super().__init__()
                    self.decoder = decoder

                def __call__(self, inputs, cache):
                    return self.decoder(inputs, cache=cache).logits

                @property
                def layers(self):
                    return self.decoder.layers

                def make_cache(self):
                    return self.decoder.make_cache()

            self.model = TextDecoder(loaded.language_model)
            self.tokenizer = getattr(processor, "tokenizer", processor)
        else:
            self.model, self.tokenizer = load(path)
        if not isinstance(self.tokenizer, TokenizerWrapper):
            self.tokenizer = TokenizerWrapper(self.tokenizer)
        if "<|endoftext|>" in self.tokenizer.get_vocab():
            self.tokenizer.add_eos_token("<|endoftext|>")
        mx.eval(self.model.parameters())
        mx.synchronize()
        self.mx = mx
        self.entry = entry
        self.sampling = sampling
        self.stream_generate = stream_generate
        self.make_prompt_cache = make_prompt_cache
        self.store = LRUPromptCache(max_size=1)
        self.sampler = make_sampler(temp=sampling["temperature"], top_p=sampling["top_p"])
        self.assembler = PromptAssembler(self.count_tokens)
        self.samples = []

    def count_tokens(self, text):
        return len(self.tokenizer.encode(text, add_special_tokens=False))

    def __call__(self, messages):
        return self.measure(messages)

    def conversation(self, messages):
        return self(assemble(self.assembler, messages))

    def measure(self, messages, *, warm=False):
        started = time.perf_counter()
        prompt = self.tokenizer.apply_chat_template(
            messages, tokenize=True, add_generation_prompt=True, enable_thinking=False
        )
        if not prompt:
            raise ValueError("chat template produced no tokens")
        cache, rest = (None, prompt)
        if warm:
            cache, rest = self.store.fetch_nearest_cache(self.entry[0], prompt[:-1])
            rest = rest + prompt[-1:]
        reused = len(prompt) - len(rest)
        if cache is None:
            cache = self.make_prompt_cache(self.model)
        tokens = list(prompt)
        chunks = []
        first_ms = None
        response = None
        stream = self.stream_generate(
            self.model,
            self.tokenizer,
            rest,
            prompt_cache=cache,
            sampler=self.sampler,
            max_tokens=self.sampling["max_tokens"],
        )
        try:
            for response in stream:
                self.mx.synchronize()
                if first_ms is None:
                    first_ms = (time.perf_counter() - started) * 1000
                chunks.append(response.text)
                tokens.append(response.token)
        finally:
            stream.close()
        if response is None:
            raise ValueError("model generated no tokens")
        if warm:
            self.store.insert_cache(self.entry[0], tokens, cache)
        self.samples.append(
            {
                "ttft_ms": first_ms,
                "prefill_ms": response.prompt_tokens / response.prompt_tps * 1000,
                "tokens_per_second": response.generation_tps,
                "reused_prefix_tokens": reused,
                "prompt_tokens": len(prompt),
            }
        )
        return "".join(chunks)

    def memory(self):
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return {
            "peak_mlx_bytes": self.mx.get_peak_memory(),
            "peak_rss_bytes": rss if platform.system() == "Darwin" else rss * 1024,
        }


def benchmark(model):
    """Measure the candidate, including rolling history eviction."""
    history = []
    post_cap = 0
    turns = []
    for index in range(100):
        text = (
            f"Turn {index}. Help me think about my week. "
            + "I have work, a pottery class, and a garden to look after. " * 12
        )
        history_tokens = sum(model.count_tokens(m["content"]) for m in history)
        messages = model.assembler.assemble(history, text)
        reply = model.measure(messages, warm=True)
        turns.append(
            model.samples[-1]
            | {
                "turn": index + 1,
                "history_tokens_before_cap": history_tokens,
                "kept_history_tokens": sum(
                    model.count_tokens(m["content"]) for m in messages[1:-1]
                ),
                "after_cap": history_tokens > HISTORY_TOKENS,
            }
        )
        history.extend([{"role": "user", "content": text}, {"role": "assistant", "content": reply}])
        post_cap += history_tokens > HISTORY_TOKENS
        if post_cap == 5:
            break
    if post_cap < 5:
        raise ValueError("long-session probe did not reach the history cap")
    warm = [turn["prefill_ms"] for turn in turns if turn["after_cap"]]
    return {
        "turns": turns,
        "ttft_ms_mean": mean(t["ttft_ms"] for t in turns),
        "tokens_per_second_mean": mean(t["tokens_per_second"] for t in turns),
        "post_cap_prefill_ms_mean": mean(warm),
        "post_cap_within_150_ms": all(ms <= 150 for ms in warm),
        **model.memory(),
    }


def average_runs(name, runs):
    metrics = {
        key: None
        if any(r["metrics"][key] is None for r in runs)
        else mean(r["metrics"][key] for r in runs)
        for key in runs[0]["metrics"]
    }
    checks = gates(name, metrics)
    return {
        "runs": runs,
        "metrics": metrics,
        "gates": checks,
        "status": "PASS"
        if all(r["cases"] for r in runs) and all(gate["passed"] for gate in checks.values())
        else "FAIL",
    }


def pending_quality(name, quality):
    metrics = {
        "persona": {"malformed", "mood_accuracy"},
        "crisis": set(quality["metrics"]),
        "guardrail": {"malformed"},
        "memory": {"malformed", "precision", "recall"},
    }[name]
    for result in [quality, *quality["runs"]]:
        result["metrics"] = {k: v for k, v in result["metrics"].items() if k in metrics}
        if name != "crisis":
            result["gates"] = {}
        result["status"] = "PENDING_VERDICTS"
    for result in quality["runs"]:
        result["counts"].pop("correct_answers", None)
        for case in result["cases"]:
            for key in ("scores", "passed", "refusal", "judge_errors", "correct_answers"):
                case.pop(key, None)
            for answer in case.get("answers", []):
                answer.pop("passed", None)
    return quality


def evaluate_sets(model, judge, extractor, extraction, repeats):
    for name in run.SETS:
        yield (
            name,
            average_runs(
                name,
                [
                    run.evaluate(
                        name,
                        read_rows(name),
                        split="heldout",
                        model=model,
                        judge=judge,
                        extractor=extractor,
                        extraction_prompt=extraction,
                    )
                    for repeat in range(repeats)
                ],
            ),
        )


def score(result, replies, verdicts):
    model = Replay(replies, "reply")
    judge = Replay(verdicts, "answer")
    quality = dict(evaluate_sets(model, judge, model, extraction_instructions(), result["repeats"]))
    return result | {
        "quality": quality,
        "status": "PASS" if all(q["status"] == "PASS" for q in quality.values()) else "FAIL",
    }


def bakeoff(candidate, *, stub, factory=None):
    entry = CANDIDATES[candidate]
    extraction = extraction_instructions()
    result = {
        "candidate": candidate,
        "model_id": entry[0],
        "revision": entry[1],
        "loader": entry[2],
        "backend": "stub" if stub else "mlx",
        "sampling": SAMPLING,
        "system_prompt_sha256": sha256(load_system_prompt()),
        "judge_prompt_source_sha256": sha256(Path(run.__file__).read_text()),
        "judge_prompts_sha256": [],
        "extraction_prompt_sha256": sha256(extraction),
        "host": {
            "system": platform.system(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python": platform.python_version(),
        },
        "versions": {},
        "repeats": REPEATS,
        "status": "ERROR",
    }
    for package in ("mlx", "mlx-lm", "mlx-vlm"):
        try:
            result["versions"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result["versions"][package] = None
    recording = None
    judge = RecordingJudge()
    try:
        if not stub and result["versions"]["mlx-lm"] != "0.32.2":
            raise ValueError("bake-off requires mlx-lm==0.32.2")
        factory = factory or (StubModel if stub else LocalModel)
        model = factory(entry, SAMPLING)
        result["resolved_revision"] = model.resolved_revision
        result["performance"] = benchmark(model)
        recording = RecordingModel(model)
        # Keep completed sets if a later candidate call fails.
        result["quality"] = {}
        for name, quality in evaluate_sets(
            recording.conversation, judge, recording, extraction, REPEATS
        ):
            result["quality"][name] = pending_quality(name, quality)
        result["status"] = "PENDING_VERDICTS"
    except Exception as error:
        # Preserve failures (including chat-template errors) for this candidate's result file.
        result["error"] = {"type": type(error).__name__, "message": str(error)}
    result["judge_prompts_sha256"] = sorted(
        {sha256(call["messages"][0]["content"]) for call in judge.calls}
    )
    return result, recording.calls if recording else [], judge.calls


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", nargs="?", choices=("run", "score"), default="run")
    parser.add_argument("--candidate", choices=CANDIDATES, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--verdicts", type=Path)
    args = parser.parse_args()
    if (args.phase == "score") != (args.verdicts is not None):
        parser.error("--verdicts is required only for score")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    replies_path = args.out.parent / f"{args.candidate}.replies.json"
    requests_path = args.out.parent / f"{args.candidate}.judge-requests.json"
    if args.phase == "run":
        result, replies, requests = bakeoff(args.candidate, stub=os.environ.get("AMES_STUB") == "1")
        replies_path.write_text(json.dumps(replies, indent=2, allow_nan=False) + "\n")
        requests_path.write_text(json.dumps(requests, indent=2, allow_nan=False) + "\n")
    else:
        try:
            result = json.loads(args.out.read_text())
            if result["candidate"] != args.candidate:
                raise ValueError("result candidate does not match --candidate")
            result.pop("error", None)
            result = score(
                result, json.loads(replies_path.read_text()), json.loads(args.verdicts.read_text())
            )
        except (OSError, ValueError, KeyError) as error:
            parser.exit(2, f"score error: {error}\n")
    args.out.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(f"{args.candidate} backend={result['backend']} {result['status']}")
    if result["status"] == "ERROR":
        parser.exit(2, result["error"]["message"] + "\n")


if __name__ == "__main__":
    main()
