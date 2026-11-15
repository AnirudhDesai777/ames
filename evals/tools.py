"""Deterministic tool selection/argument scoring and one mocked result continuation."""

import re
from collections import Counter

from evals.scoring import ratio
from server.tool_calls import ARGUMENTS, FILLER, TOOL_INSTRUCTIONS, parse_reply, result_message


def arguments_match(call, expected):
    if call is None or expected is None or call["name"] != expected["name"]:
        return False
    argument = ARGUMENTS[call["name"]]
    if argument is None:
        return True
    text = call["arguments"][argument]
    rules = expected["arguments"]
    return all(
        re.search(pattern, text, re.IGNORECASE) for pattern in rules["required"]
    ) and not any(re.search(pattern, text, re.IGNORECASE) for pattern in rules["forbidden"])


def evaluate_tools(rows, prompt, model, messages_for):
    cases = []
    for row in rows:
        messages = messages_for(prompt + "\n\n" + TOOL_INSTRUCTIONS, row["turns"])
        raw = model(messages)
        parsed = parse_reply(raw)
        expected = row["expected"]
        right_tool = bool(parsed.call and expected and parsed.call["name"] == expected["name"])
        right_arguments = arguments_match(parsed.call, expected)
        # A malformed attempt is neither a valid call nor a correct abstention.
        decision_correct = parsed.error is None and bool(parsed.call) == bool(expected)
        receipt = None
        filler = None
        continuation = None
        if not parsed.crisis and (parsed.call or parsed.error):
            if parsed.error:
                receipt = result_message(None, error=parsed.error)
            elif not right_arguments:
                receipt = result_message(parsed.call["name"], error="mock_no_match")
            else:
                receipt = result_message(parsed.call["name"], data=row["mock_result"])
            if parsed.call:
                filler = FILLER[parsed.call["name"]]
            following = parse_reply(
                model(
                    messages
                    + [
                        {"role": "assistant", "content": raw},
                        receipt,
                    ]
                )
            )
            continuation = {
                "malformed": following.error is not None,
                "attempted": following.attempted,
                "crisis": following.crisis,
                "valid": following.error is None and not following.attempted,
            }
        cases.append(
            {
                "id": row["id"],
                "category": row["category"],
                "expected_call": expected is not None,
                "attempted": parsed.attempted,
                "called": parsed.call is not None,
                "tool": parsed.call["name"] if parsed.call else None,
                "decision_correct": decision_correct,
                "right_tool": right_tool,
                "arguments_correct": right_arguments,
                "crisis_correct": parsed.error is None and parsed.crisis == row["crisis"],
                "crisis": parsed.crisis,
                "malformed": parsed.error is not None,
                "error": parsed.error,
                "filler": filler,
                "mock_ok": receipt is not None and right_arguments,
                "continuation": continuation,
            }
        )
    positives = [case for case in cases if case["expected_call"]]
    negatives = [case for case in cases if not case["expected_call"]]
    crisis = [case for case in cases if case["category"] == "crisis"]
    continuations = [case["continuation"] for case in cases if case["continuation"] is not None]
    attempts = sum(case["attempted"] for case in cases)
    malformed_calls = sum(case["attempted"] and case["malformed"] for case in cases)
    counts = {
        "conversations": len(cases),
        "call": len(positives),
        "no_call": len(negatives),
        "attempts": attempts,
        "continuations": len(continuations),
        **dict(Counter(case["category"] for case in cases)),
    }
    metrics = {
        "call_accuracy": ratio(sum(case["decision_correct"] for case in cases), len(cases)),
        "tool_accuracy": ratio(sum(case["right_tool"] for case in positives), len(positives)),
        "argument_quality": ratio(
            sum(case["arguments_correct"] for case in positives), len(positives)
        ),
        "over_call_rate": ratio(sum(case["attempted"] for case in negatives), len(negatives)),
        "malformed_call_rate": ratio(malformed_calls, attempts),
        "malformed": sum(case["malformed"] for case in cases),
        "crisis_priority_accuracy": ratio(
            sum(case["crisis_correct"] and not case["attempted"] for case in crisis), len(crisis)
        ),
        "crisis_accuracy": ratio(sum(case["crisis_correct"] for case in cases), len(cases)),
        "continuation_valid_rate": ratio(
            sum(case["valid"] for case in continuations), len(continuations)
        ),
    }
    return counts, metrics, cases
