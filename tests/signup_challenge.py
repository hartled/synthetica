"""Solve the signup arithmetic prompt. The API does not return the answer."""
import re

_PROMPT = re.compile(r"What is (\d+) ([+\-×x*]) (\d+)\?")


def solve(prompt):
    match = _PROMPT.fullmatch((prompt or "").strip())
    if not match:
        raise AssertionError("unexpected challenge prompt: " + repr(prompt))
    a, op, b = int(match.group(1)), match.group(2), int(match.group(3))
    if op == "+":
        return str(a + b)
    if op == "-":
        return str(a - b)
    return str(a * b)


def attach_challenge(client, body):
    res = client.get("/api/auth/challenge")
    assert res.status_code == 200, res.text
    data = res.json()
    assert "answer" not in data
    out = dict(body)
    out["challenge_id"] = data["id"]
    out["challenge_answer"] = solve(data["prompt"])
    return out
