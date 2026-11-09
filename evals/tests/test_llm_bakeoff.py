import builtins
import json
import os
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest

from evals import llm_bakeoff as bake
from server.prompt import HISTORY_TOKENS, load_system_prompt


@pytest.mark.parametrize("candidate", bake.CANDIDATES)
def test_stub_bakeoff_has_three_gates_runs_and_capped_probe(candidate, monkeypatch):
    original = builtins.__import__

    def no_models(name, *args, **kwargs):
        assert not name.startswith(("mlx", "huggingface_hub"))
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_models)
    result = bake.bakeoff(candidate, stub=True)
    assert result["status"] == "FAIL"
    assert result["backend"] == "stub"
    assert result["revision"] == bake.CANDIDATES[candidate][1]
    assert result["system_prompt_sha256"] == bake.sha256(load_system_prompt())
    assert len(result["judge_prompt_source_sha256"]) == 64
    assert len(result["judge_prompts_sha256"]) == 5
    assert result["sampling"]["temperature"] > 0
    for quality in result["quality"].values():
        assert len(quality["runs"]) == 3
        assert all(run["split"] == "heldout" for run in quality["runs"])
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
    assert "backend=stub FAIL" in completed.stdout
    assert json.loads(output.read_text())["backend"] == "stub"


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

    result = bake.bakeoff("gemma4-e2b", stub=True, factory=broken)
    assert result["status"] == "ERROR"
    assert "chat template" in result["error"]["message"]
    assert result["model_id"] == bake.CANDIDATES["gemma4-e2b"][0]
    assert result["system_prompt_sha256"]
    assert result["judge_prompt_source_sha256"]
    assert bake.bakeoff("qwen35-2b", stub=True)["status"] != "ERROR"


def fake_runtime(monkeypatch, *, trimmable=True):
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
        yield SimpleNamespace(
            text="Hello", token=7, prompt_tokens=len(prompt), prompt_tps=100, generation_tps=25
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
    return calls, decoder, Tokenizer


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
    with pytest.raises(RuntimeError, match="mlx-vlm==0.7.12"):
        bake.LocalModel(bake.CANDIDATES["lfm3-1b7"], bake.SAMPLING)
    assert bake.LocalModel(bake.CANDIDATES["qwen3-4b"], bake.SAMPLING)


def test_template_exception_marks_candidate_error(monkeypatch):
    calls, decoder, tokenizer = fake_runtime(monkeypatch)

    def reject(self, messages, **kwargs):
        raise ValueError("template rejects roles")

    monkeypatch.setattr(tokenizer, "apply_chat_template", reject)
    result = bake.bakeoff("qwen3-4b", stub=True, factory=bake.LocalModel)
    assert result["status"] == "ERROR"
    assert result["error"]["message"] == "template rejects roles"
    assert not any(call[0] == "stream" for call in calls)


def test_extraction_failure_preserves_finished_quality_sets():
    class BadExtractor(bake.StubModel):
        def __call__(self, messages):
            if messages[0]["content"].startswith("Extract"):
                return "invalid extraction"
            return super().__call__(messages)

    result = bake.bakeoff("qwen3-4b", stub=True, factory=BadExtractor)
    assert result["status"] == "ERROR"
    assert set(result["quality"]) == {"persona", "crisis", "guardrail"}
    assert result["performance"]["turns"]
