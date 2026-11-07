import json

import pytest

from server import prompt
from server.prompt import PromptAssembler, load_system_prompt


def count_tokens(text):
    return len(text.split())


def test_loads_only_verbatim_system_prompt(tmp_path, monkeypatch):
    persona = tmp_path / "ames.md"
    persona.write_text(
        "## Crisis line\n\n```text\nDo not load this.\n```\n\n"
        "## System prompt\n\n```text\n  Keep café.\n\nKeep whitespace.  \n```\n\n"
        "## Examples\n\nDo not load these either.\n"
    )
    monkeypatch.setattr(prompt, "PERSONA_PATH", persona)
    assert load_system_prompt() == "  Keep café.\n\nKeep whitespace.  \n"


@pytest.mark.parametrize(
    "text", ["", "## System prompt\nNo fence", "## System prompt\n\n```text\nUnclosed"]
)
def test_missing_system_prompt_fails(tmp_path, monkeypatch, text):
    persona = tmp_path / "ames.md"
    persona.write_text(text)
    monkeypatch.setattr(prompt, "PERSONA_PATH", persona)
    with pytest.raises(ValueError, match="System prompt"):
        load_system_prompt()


def test_prefix_stability_across_turns_with_changing_memory():
    assembler = PromptAssembler(count_tokens)
    history = [
        {"role": "user", "content": "  café?\n"},
        {"role": "assistant", "content": "Sure.  "},
    ]
    first = assembler.assemble(history, "What next?", "User likes coffee.")
    next_history = history + [first[-1], {"role": "assistant", "content": "Let's think."}]
    second = assembler.assemble(next_history, "And then?", "User likes tea.")

    def prefix_bytes(messages):
        return json.dumps(messages, ensure_ascii=False).encode("utf-8")

    assert prefix_bytes(first[:-2]) == prefix_bytes(second[: len(first) - 2])
    assert first[0] == {"role": "system", "content": load_system_prompt()}
    assert first[1:-2] == history
    assert second[1:-2] == next_history
    assert second[-2:] == [
        {"role": "system", "content": "Memory facts:\nUser likes tea."},
        {"role": "user", "content": "And then?"},
    ]
    assert "User likes coffee." not in prefix_bytes(second).decode("utf-8")


@pytest.mark.parametrize("oldest_tokens", [999, 1000, 1001, 4000])
def test_history_cap_keeps_most_recent_whole_messages(oldest_tokens):
    assembler = PromptAssembler(count_tokens)
    history = [
        {"role": "user", "content": "old " * oldest_tokens},
        {"role": "assistant", "content": "middle " * 1000},
        {"role": "user", "content": "recent " * 1000},
    ]
    messages = assembler.assemble(history, "new " * 3100)
    kept = messages[1:-2]
    assert kept == (history if oldest_tokens <= 1000 else history[1:])
    assert sum(count_tokens(message["content"]) for message in kept) <= 3000
    assert messages[-1]["content"] == "new " * 3100


def test_oversized_recent_message_does_not_resurrect_older_history():
    assembler = PromptAssembler(count_tokens)
    history = [
        {"role": "user", "content": "short old message"},
        {"role": "assistant", "content": "large " * 3001},
    ]
    assert assembler.assemble(history, "new")[1:-2] == []


def test_uses_supplied_token_counter_instead_of_word_count():
    assembler = PromptAssembler(len)
    history = [{"role": "assistant", "content": "x" * 3001}]
    assert assembler.assemble(history, "new")[1:-2] == []


def test_history_is_owned_by_caller_and_never_crosses_sessions():
    assembler = PromptAssembler(count_tokens)
    history = [{"role": "user", "content": "prior session"}]
    messages = assembler.assemble(history, "first")
    messages[1]["content"] = "changed"
    assert history == [{"role": "user", "content": "prior session"}]
    assert assembler.assemble([], "new session") == [
        {"role": "system", "content": load_system_prompt()},
        {"role": "system", "content": "Memory facts:\n"},
        {"role": "user", "content": "new session"},
    ]
