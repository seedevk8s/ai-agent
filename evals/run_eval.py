"""Run a small source/keyword RAG evaluation set against the HTTP API."""

import argparse
import json
import urllib.request
from pathlib import Path


def post_json(url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://localhost/api")
    parser.add_argument("--dataset", default="evals/rag_eval.sample.jsonl")
    args = parser.parse_args()

    cases = [
        json.loads(line)
        for line in Path(args.dataset).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    source_hits = 0
    keyword_hits = 0
    for index, case in enumerate(cases, start=1):
        result = post_json(f"{args.api_url.rstrip('/')}/query", {"question": case["question"]})
        sources = "\n".join(result.get("documents", []))
        answer = result.get("generation", "")
        source_ok = all(name in sources for name in case.get("expected_filenames", []))
        keyword_ok = all(word in answer for word in case.get("answer_keywords", []))
        source_hits += int(source_ok)
        keyword_hits += int(keyword_ok)
        print(f"{index:03d} source={source_ok} keyword={keyword_ok} {case['question']}")

    total = max(len(cases), 1)
    print(f"source_hit_rate={source_hits / total:.3f}")
    print(f"answer_keyword_rate={keyword_hits / total:.3f}")


if __name__ == "__main__":
    main()
