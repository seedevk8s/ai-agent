"""Unit tests for deterministic retrieval deduplication and reranking."""

import unittest

from app.ingest import rerank_documents


class RetrievalRerankingTests(unittest.TestCase):
    def test_removes_duplicate_text(self):
        candidates = [
            {"text": "휴가 신청은 팀장 승인이 필요합니다", "score": 0.9},
            {"text": "휴가 신청은 팀장 승인이 필요합니다", "score": 0.8},
            {"text": "출장 신청은 경영지원팀에 제출합니다", "score": 0.7},
        ]

        results = rerank_documents("휴가 신청 승인", candidates, limit=5)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["text"], candidates[0]["text"])

    def test_lexical_relevance_can_improve_rank(self):
        candidates = [
            {"text": "일반적인 복리후생 안내", "score": 0.50},
            {"text": "휴가 신청 승인 절차", "score": 0.45},
        ]

        results = rerank_documents("휴가 신청 승인", candidates, limit=2)

        self.assertEqual(results[0]["text"], "휴가 신청 승인 절차")
        self.assertGreater(results[0]["rerank_score"], results[1]["rerank_score"])

    def test_respects_final_limit(self):
        candidates = [
            {"text": f"서로 다른 문서 내용 {index}", "score": 1 - index / 100}
            for index in range(20)
        ]

        self.assertEqual(len(rerank_documents("문서", candidates, limit=5)), 5)


if __name__ == "__main__":
    unittest.main()
