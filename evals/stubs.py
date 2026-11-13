"""Fixed responses for exercising the harness, independent of eval labels."""

import json

from evals.scoring import CRITERIA


def stub_model(messages: list[dict[str, str]]) -> str:
    return "<state crisis=0>I'm an AI. What would help you think this through?"


def stub_judge(messages: list[dict[str, str]]) -> str:
    instruction = messages[0]["content"]
    if instruction.startswith("Score"):
        return json.dumps(dict.fromkeys(CRITERIA, 4))
    if instruction.startswith("Does the reply refuse"):
        return "no"
    return "yes"


def stub_extractor(messages: list[dict[str, str]]) -> str:
    return json.dumps([{"subject": "user", "predicate": "likes", "object": "tea"}])
