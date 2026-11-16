import json
import subprocess
import sys

import pytest

from evals.check_training_format import check_row
from server.tool_calls import result_message

CALL = '<state crisis=0><tool_call>{"name":"get_time","arguments":{}}</tool_call>'


def row(*messages):
    return {"messages": [{"role": role, "content": text} for role, text in messages]}


def test_valid_pairs_and_crisis():
    assert (
        check_row(
            row(
                ("system", "Instructions"),
                ("user", "time?"),
                ("assistant", CALL),
                ("user", result_message("get_time", data={"datetime": "now"})["content"]),
                ("assistant", "<state crisis=0>It's noon."),
                ("user", "Thanks"),
                ("assistant", "<state crisis=1>"),
            )
        )
        is None
    )


@pytest.mark.parametrize(
    "messages,error",
    [
        ([("assistant", CALL)], "incomplete_pair"),
        ([("assistant", CALL), ("user", "not a receipt")], "invalid_result"),
        ([("assistant", "<think>x</think><state crisis=0>Hi")], "invalid_assistant"),
        ([("assistant", "<state crisis=0>Hi<think>bad</think>")], "invalid_assistant"),
        ([("assistant", "<state crisis=1>"), ("user", "Hi")], "crisis_not_last"),
        (
            [
                ("assistant", CALL),
                ("user", result_message("get_time", data={})["content"]),
                ("assistant", CALL),
            ],
            "second_call",
        ),
    ],
)
def test_invalid_training_rows(messages, error):
    assert check_row(row(*messages)) == error


def test_cli_reports_line_number(tmp_path):
    path = tmp_path / "training.jsonl"
    path.write_text(json.dumps(row(("assistant", "<state crisis=0>Hello"))) + "\n{}\n")
    result = subprocess.run(
        [sys.executable, "-m", "evals.check_training_format", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "2: missing_messages" in result.stdout
