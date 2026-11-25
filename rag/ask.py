"""CLI:  uv run python -m rag.ask "How do I verify zero energy state before working on a conveyor?"

Flags: --k 6   --json   --retrieve-only (no API call; prints retrieved chunks)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from rag import config


def main() -> int:
    ap = argparse.ArgumentParser(description="Ask the maintenance RAG assistant")
    ap.add_argument("question")
    ap.add_argument("--k", type=int, default=config.TOP_K)
    ap.add_argument("--json", action="store_true", help="print the raw structured result")
    ap.add_argument("--retrieve-only", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    from rag.retrieve import Retriever

    retriever = Retriever.load()
    if args.retrieve_only:
        for i, h in enumerate(retriever.search(args.question, k=args.k), 1):
            print(f"[{i}] {h['title']} p.{h['page']}  score={h['score']:.4f}\n    {h['text'][:200]!r}")
        return 0

    from rag.generate import answer_question
    from rag.llm import LLMError, get_llm

    try:
        res = answer_question(args.question, retriever, get_llm(), k=args.k)
    except LLMError as e:
        print(f"LLM error: {e}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(res.to_dict(), indent=2))
        return 0
    print(res.answer)
    print("\nSources:")
    for c in res.citations:
        print(f"  [{c.n}] {c.title}, p.{c.page}")
    print(f"\nretrieval {res.retrieval_ms:.0f} ms | llm {res.llm_ms:.0f} ms | "
          f"tokens {res.input_tokens} in / {res.output_tokens} out | ${res.est_cost_usd:.5f} | "
          f"grounded={res.grounded} | model={res.model}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
