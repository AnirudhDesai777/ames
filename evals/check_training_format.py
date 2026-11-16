"""Check chat-format JSONL rows against the Ames tool protocol."""

import argparse
import json
from pathlib import Path

from server.tool_calls import parse_reply


def check_row(row):
    messages = row.get("messages") if isinstance(row, dict) else None
    if not isinstance(messages, list) or not messages:
        return "missing_messages"
    pending = None
    awaiting_reply = False
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or set(message) != {"role", "content"}:
            return "invalid_message"
        role, content = message["role"], message["content"]
        if role not in {"system", "user", "assistant"} or not isinstance(content, str):
            return "invalid_message"
        if role == "assistant":
            parsed = parse_reply(content)
            if parsed.error or not content.lstrip().startswith("<state crisis="):
                return "invalid_assistant"
            if pending is not None:
                return "missing_result"
            if awaiting_reply:
                if parsed.attempted:
                    return "second_call"
                awaiting_reply = False
            if parsed.crisis:
                if index != len(messages) - 1:
                    return "crisis_not_last"
            elif parsed.call:
                pending = parsed.call["name"]
        elif role == "user":
            if pending is not None:
                try:
                    receipt = json.loads(content)["ames_tool_result"]
                    if (
                        not isinstance(receipt, dict)
                        or receipt.get("name") != pending
                        or not isinstance(receipt.get("ok"), bool)
                        or ("data" if receipt["ok"] else "error") not in receipt
                        or set(json.loads(content)) != {"ames_tool_result"}
                    ):
                        return "invalid_result"
                except (ValueError, KeyError, TypeError):
                    return "invalid_result"
                pending = None
                awaiting_reply = True
            elif awaiting_reply:
                return "missing_reply"
            else:
                try:
                    if "ames_tool_result" in json.loads(content):
                        return "orphan_result"
                except (ValueError, TypeError):
                    pass
        elif pending is not None or awaiting_reply:
            return "incomplete_pair"
    if pending is not None or awaiting_reply:
        return "incomplete_pair"
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jsonl", type=Path)
    args = parser.parse_args()
    failures = 0
    with args.jsonl.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                error = check_row(json.loads(line))
            except ValueError:
                error = "invalid_json"
            if error:
                print(f"{line_number}: {error}")
                failures += 1
    print(f"{failures} invalid rows")
    raise SystemExit(bool(failures))


if __name__ == "__main__":
    main()
