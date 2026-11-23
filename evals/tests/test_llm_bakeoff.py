import builtins
import json
import os
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest

from evals import llm_bakeoff as bake
from evals.stubs import stub_judge
from server.llm import MAX_REPLY_TOKENS
from server.prompt import HISTORY_TOKENS, load_system_prompt
from server.tool_calls import TOOL_INSTRUCTIONS


@pytest.mark.parametrize("candidate", bake.CANDIDATES)
def test_stub_bakeoff_has_pending_metrics_and_capped_probe(candidate, monkeypatch):
    original = builtins.__import__

    def no_models(name, *args, **kwargs):
        assert not name.startswith(("mlx", "huggingface_hub"))
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_models)
    result, replies, requests = bake.bakeoff(candidate, stub=True)
    assert result["status"] == "PENDING_VERDICTS"
    assert result["backend"] == "stub"
    assert result["revision"] == bake.CANDIDATES[candidate][1]
    prompt = load_system_prompt()
    if candidate == "ames-v1":
        prompt += "\n\n" + TOOL_INSTRUCTIONS
    assert result["system_prompt_sha256"] == bake.sha256(prompt)
    assert len(result["judge_prompt_source_sha256"]) == 64
    assert len(result["judge_prompts_sha256"]) == 5
    assert result["sampling"]["temperature"] > 0
    assert result["sampling"]["max_tokens"] == MAX_REPLY_TOKENS == 256
    for quality in result["quality"].values():
        assert len(quality["runs"]) == 3
        assert all(run["split"] == "heldout" for run in quality["runs"])
        assert "malformed" in quality["metrics"]
        for run in quality["runs"]:
            assert "correct_answers" not in run["counts"]
            for case in run["cases"]:
                assert (
                    not {"scores", "passed", "refusal", "judge_errors", "correct_answers"}
                    & case.keys()
                )
                assert all("passed" not in answer for answer in case.get("answers", []))
    assert set(result["quality"]["persona"]["metrics"]) == {"malformed"}
    assert set(result["quality"]["guardrail"]["metrics"]) == {"malformed"}
    assert set(result["quality"]["memory"]["metrics"]) == {"precision", "recall", "malformed"}
    assert result["quality"]["crisis"]["gates"]
    assert "judge" not in result
    assert replies and requests
    performance = result["performance"]
    assert performance["peak_mlx_bytes"] is None
    assert performance["peak_rss_bytes"] is None
    capped = [turn for turn in performance["turns"] if turn["after_cap"]]
    assert len(capped) == 5
    assert all(turn["history_tokens_before_cap"] > HISTORY_TOKENS for turn in capped)
    assert all(turn["kept_history_tokens"] <= HISTORY_TOKENS for turn in performance["turns"])
    assert all("reused_prefix_tokens" in turn for turn in performance["turns"])


def test_cli_runs_in_ci_with_stubs(tmp_path):
    output = tmp_path / "nested/result.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "evals.llm_bakeoff",
            "--candidate",
            "bonsai2-2b",
            "--out",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=True,
        env=os.environ | {"AMES_STUB": "1"},
    )
    assert "backend=stub PENDING_VERDICTS" in completed.stdout
    assert json.loads(output.read_text())["backend"] == "stub"
    requests = json.loads((output.parent / "bonsai2-2b.judge-requests.json").read_text())
    assert json.loads((output.parent / "bonsai2-2b.replies.json").read_text())
    verdicts = tmp_path / "verdicts.json"
    verdicts.write_text(
        json.dumps([{"key": row["key"], "answer": stub_judge(row["messages"])} for row in requests])
    )
    command = [
        sys.executable,
        "-m",
        "evals.llm_bakeoff",
        "score",
        "--candidate",
        "bonsai2-2b",
        "--out",
        str(output),
        "--verdicts",
        str(verdicts),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=True)
    assert "backend=stub FAIL" in completed.stdout
    scored = json.loads(output.read_text())
    assert scored["quality"]["persona"]["metrics"]["pass_rate"] == 1
    verdicts.write_text("[]")
    completed = subprocess.run(command, capture_output=True, text=True)
    assert completed.returncode == 2
    assert requests[0]["key"] in completed.stderr
    assert json.loads(output.read_text()) == scored


def test_run_then_score_matches_direct_evaluate_with_repeated_calls(monkeypatch):
    loaded = []

    class ChangingModel(bake.StubModel):
        def __init__(self, entry, sampling):
            loaded.append(entry)
            super().__init__(entry, sampling)
            self.calls = 0

        def __call__(self, messages):
            self.calls += 1
            reply = super().__call__(messages)
            if messages[0]["content"].startswith("Extract"):
                return reply.replace("tea", "coffee") if self.calls % 2 else reply
            return reply + f" Call {self.calls}."

    result, replies, requests = bake.bakeoff("qwen35-2b", stub=True, factory=ChangingModel)
    assert loaded == [bake.CANDIDATES["qwen35-2b"]]
    assert any(row["key"].endswith(":1") for row in replies)
    verdicts = [{"key": row["key"], "answer": stub_judge(row["messages"])} for row in requests]
    monkeypatch.setattr(bake, "LocalModel", lambda *args: pytest.fail("score loaded a model"))
    scored = bake.score(result, replies, verdicts)
    direct = ChangingModel(bake.CANDIDATES["qwen35-2b"], bake.SAMPLING)
    for name in bake.run.SETS:
        expected = bake.average_runs(
            name,
            [
                bake.run.evaluate(
                    name,
                    bake.read_rows(name),
                    split="heldout",
                    model=direct.conversation,
                    judge=stub_judge,
                    extractor=direct,
                    extraction_prompt=bake.extraction_instructions(),
                )
                for repeat in range(bake.REPEATS)
            ],
        )
        assert scored["quality"][name] == expected
    assert scored["status"] == "FAIL"
    assert scored["performance"] == result["performance"]


@pytest.mark.parametrize("missing", ["reply", "extraction", "verdict"])
def test_score_missing_key_raises_with_key(missing):
    result, replies, requests = bake.bakeoff("qwen35-2b", stub=True)
    verdicts = [{"key": row["key"], "answer": stub_judge(row["messages"])} for row in requests]
    if missing == "verdict":
        key = verdicts.pop(0)["key"]
    else:
        index = next(
            i
            for i, row in enumerate(replies)
            if row["messages"][0]["content"].startswith("Extract") == (missing == "extraction")
        )
        key = replies.pop(index)["key"]
    with pytest.raises(KeyError, match=key):
        bake.score(result, replies, verdicts)


def test_conversation_uses_assembler_and_moves_memory_into_user_turn():
    model = bake.StubModel(bake.CANDIDATES["qwen35-2b"], bake.SAMPLING)
    messages = [
        {"role": "system", "content": "raw prompt"},
        {"role": "system", "content": "Remembered facts: user likes tea"},
        {"role": "user", "content": "Name?"},
    ]
    assembled = bake.assemble(model.assembler, messages)
    assert assembled[0]["content"] == load_system_prompt()
    assert [message["role"] for message in assembled] == ["system", "user"]
    assert assembled[-1]["content"].endswith("user likes tea\n\nName?")
    history = [{"role": "assistant", "content": "old " * 3001}]
    assert (
        bake.assemble(model.assembler, [messages[0], *history, messages[-1]])[1:] == messages[-1:]
    )


def test_extraction_exposes_only_predicate_vocabulary():
    instructions = bake.extraction_instructions()
    assert "dog name" in instructions
    assert "job status" in instructions
    assert "Biscuit" not in instructions
    assert "Maya" not in instructions
    assert "children's hospital" not in instructions


def test_mean_gates_are_not_last_run_gates():
    runs = [
        {"cases": [1], "metrics": {"precision": p, "recall": 1, "answer_accuracy": 1}}
        for p in (1, 1, 0.8)
    ]
    result = bake.average_runs("memory", runs)
    assert result["status"] == "PASS"
    assert result["metrics"]["precision"] == pytest.approx(2.8 / 3)


def test_candidate_errors_keep_provenance_and_do_not_prevent_other_candidates():
    def broken(entry, sampling):
        raise ValueError("chat template rejects system role")

    result, replies, requests = bake.bakeoff("gemma4-e2b", stub=True, factory=broken)
    assert result["status"] == "ERROR"
    assert "chat template" in result["error"]["message"]
    assert result["model_id"] == bake.CANDIDATES["gemma4-e2b"][0]
    assert result["system_prompt_sha256"]
    assert result["judge_prompt_source_sha256"]
    assert bake.bakeoff("qwen35-2b", stub=True)[0]["status"] != "ERROR"


def fake_runtime(monkeypatch, *, trimmable=True, endoftext=False, generated=(7,)):
    calls = []

    def module(name, **attributes):
        value = ModuleType(name)
        value.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, value)
        return value

    module("mlx")
    module(
        "mlx.core",
        eval=lambda *args: None,
        synchronize=lambda: None,
        reset_peak_memory=lambda: None,
        get_peak_memory=lambda: 123,
    )
    module("mlx.nn", Module=type("Module", (), {"parameters": lambda self: {}}))

    def snapshot(repo, *, revision, local_files_only):
        assert local_files_only
        return "/snapshots/" + revision + "abcdef"

    module("huggingface_hub", snapshot_download=snapshot)

    class Tokenizer:
        def __init__(self):
            self.eos_token_ids = {100265}

        def get_vocab(self):
            return {"<|endoftext|>": 100257} if endoftext else {}

        def add_eos_token(self, token):
            self.eos_token_ids.add(self.get_vocab()[token])

        def encode(self, text, **kwargs):
            return list(range(len(text.split())))

        def apply_chat_template(self, messages, **kwargs):
            assert kwargs == {
                "tokenize": True,
                "add_generation_prompt": True,
                "enable_thinking": False,
            }
            calls.append(("template", messages))
            return [1, 2, 3, 4]

    decoder = SimpleNamespace(
        parameters=lambda: {},
        layers=[],
        make_cache=lambda: [],
    )

    def load(path):
        calls.append(("lm", path))
        return decoder, Tokenizer()

    def stream(model, tokenizer, prompt, **kwargs):
        calls.append(("stream", prompt, kwargs))
        for token in generated:
            if token in tokenizer.eos_token_ids:
                return
            yield SimpleNamespace(
                text="Hello" if token == 7 else "run-on",
                token=token,
                prompt_tokens=len(prompt),
                prompt_tps=100,
                generation_tps=25,
            )

    module("mlx_lm", load=load, stream_generate=stream)
    module("mlx_lm.models")

    class Cache:
        def __init__(self, **kwargs):
            self.used = False

        def fetch_nearest_cache(self, model, tokens):
            return (["cache"], tokens[2:]) if self.used and trimmable else (None, tokens)

        def insert_cache(self, *args):
            self.used = True

    module("mlx_lm.models.cache", LRUPromptCache=Cache, make_prompt_cache=lambda model: [])
    module("mlx_lm.sample_utils", make_sampler=lambda **kwargs: kwargs)
    module("mlx_lm.tokenizer_utils", TokenizerWrapper=Tokenizer)
    return calls, decoder, Tokenizer


@pytest.mark.parametrize("candidate", ["qwen35-2b", "bonsai2-2b"])
@pytest.mark.parametrize("stop", [100257, 100265])
def test_endoftext_and_configured_eos_stop_generation(monkeypatch, candidate, stop):
    calls, decoder, tokenizer = fake_runtime(monkeypatch, endoftext=True, generated=(7, stop, 8))
    vlm = ModuleType("mlx_vlm")
    vlm.load = lambda path: (SimpleNamespace(language_model=decoder), tokenizer())
    monkeypatch.setitem(sys.modules, "mlx_vlm", vlm)
    model = bake.LocalModel(bake.CANDIDATES[candidate], bake.SAMPLING)
    assert model.tokenizer.eos_token_ids == {100257, 100265}
    assert model.measure(model.assembler.assemble([], "Hi")) == "Hello"


@pytest.mark.parametrize("trimmable", [True, False])
def test_real_adapter_uses_cache_returned_prefix_and_sampling(monkeypatch, trimmable):
    calls, decoder, tokenizer = fake_runtime(monkeypatch, trimmable=trimmable)
    model = bake.LocalModel(bake.CANDIDATES["qwen3-4b"], bake.SAMPLING)
    messages = model.assembler.assemble([], "Hi")
    assert model.measure(messages, warm=True) == "Hello"
    assert model.samples[-1]["reused_prefix_tokens"] == 0
    model.measure(messages, warm=True)
    assert model.samples[-1]["reused_prefix_tokens"] == (2 if trimmable else 0)
    stream = [call for call in calls if call[0] == "stream"][-1]
    assert stream[2]["sampler"] == {"temp": 0.7, "top_p": 0.9}
    assert stream[2]["max_tokens"] == MAX_REPLY_TOKENS
    assert model.memory()["peak_mlx_bytes"] == 123


@pytest.mark.parametrize("candidate", ["bonsai2-2b", "lfm3-1b7"])
def test_vlm_loader_is_lazy_and_adapts_decoder_logits(monkeypatch, candidate):
    calls, decoder, tokenizer = fake_runtime(monkeypatch)

    class Decoder:
        def __call__(self, inputs, cache):
            return SimpleNamespace(logits="text logits")

        def parameters(self):
            return {}

        def make_cache(self):
            return []

        layers = []

    def load(path):
        calls.append(("vlm", path))
        return SimpleNamespace(language_model=Decoder()), SimpleNamespace(tokenizer=tokenizer())

    vlm = ModuleType("mlx_vlm")
    vlm.load = load
    monkeypatch.setitem(sys.modules, "mlx_vlm", vlm)
    model = bake.LocalModel(bake.CANDIDATES[candidate], bake.SAMPLING)
    assert model.model([1], cache=[]) == "text logits"
    assert model.model.make_cache() == []
    assert calls[0][0] == "vlm"
    assert (
        model.conversation(
            [{"role": "system", "content": "raw"}, {"role": "user", "content": "Hi"}]
        )
        == "Hello"
    )


def test_missing_vlm_only_fails_overlay_candidate(monkeypatch):
    fake_runtime(monkeypatch)
    monkeypatch.setitem(sys.modules, "mlx_vlm", None)
    with pytest.raises(RuntimeError, match="uv run --extra mac --with mlx-vlm==0.7.12"):
        bake.LocalModel(bake.CANDIDATES["lfm3-1b7"], bake.SAMPLING)
    assert bake.LocalModel(bake.CANDIDATES["qwen3-4b"], bake.SAMPLING)


def test_template_exception_marks_candidate_error(monkeypatch):
    calls, decoder, tokenizer = fake_runtime(monkeypatch)

    def reject(self, messages, **kwargs):
        raise ValueError("template rejects roles")

    monkeypatch.setattr(tokenizer, "apply_chat_template", reject)
    result, replies, requests = bake.bakeoff("qwen3-4b", stub=True, factory=bake.LocalModel)
    assert result["status"] == "ERROR"
    assert result["error"]["message"] == "template rejects roles"
    assert not any(call[0] == "stream" for call in calls)


def test_extraction_failure_records_cases_without_aborting_candidate():
    class BadExtractor(bake.StubModel):
        def __call__(self, messages):
            if messages[0]["content"].startswith("Extract"):
                return "invalid extraction"
            return super().__call__(messages)

    result, replies, requests = bake.bakeoff("qwen3-4b", stub=True, factory=BadExtractor)
    assert result["status"] == "PENDING_VERDICTS"
    assert set(result["quality"]) == set(bake.run.SETS)
    for run in result["quality"]["memory"]["runs"]:
        assert run["counts"]["extracted"] == 0
        assert run["metrics"]["recall"] == 0
        assert all(
            case["extraction_errors"][0]["reply"] == "invalid extraction" for case in run["cases"]
        )
    assert result["performance"]["turns"]


def test_local_student_loads_directory_with_tool_prompt(monkeypatch, tmp_path):
    import hashlib

    calls, _, _ = fake_runtime(monkeypatch)
    folder, pinned_hash, loader = bake.CANDIDATES["ames-v1"]
    assert folder == "qwen3.5-2b-ames-v1-mlx-4bit"
    model_dir = tmp_path / folder
    model_dir.mkdir()
    (model_dir / "model.safetensors").write_bytes(b"weights")
    monkeypatch.setattr(bake, "MODEL_DIR", tmp_path)
    monkeypatch.setenv("AMES_LLM_MODEL", str(tmp_path / "different-runtime-model"))
    with pytest.raises(ValueError, match="pinned revision"):
        bake.LocalModel((folder, pinned_hash, loader), bake.SAMPLING)
    test_hash = hashlib.sha256(b"weights").hexdigest()
    model = bake.LocalModel((folder, test_hash, loader), bake.SAMPLING)
    assert ("lm", str(model_dir)) in calls
    assert model.resolved_revision == test_hash
    assert model.assembler.system_prompt.endswith(TOOL_INSTRUCTIONS)
