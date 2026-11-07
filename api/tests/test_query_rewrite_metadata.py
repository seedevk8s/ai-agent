"""Tests for exposing query rewrites through graph result metadata."""

import json
import unittest
from unittest.mock import Mock, patch

from langchain_core.messages import ToolMessage

from app.graph import process_tool_results, search_internal_documents


class QueryRewriteMetadataTests(unittest.TestCase):
    @patch("app.graph.rewrite_search_query")
    @patch("app.graph.search_documents")
    def test_hides_rewrite_when_model_only_simplifies_original_question(
        self, search_documents: Mock, rewrite_search_query: Mock
    ):
        search_documents.return_value = [
            {
                "filename": "인사규정.pdf",
                "page": 1,
                "score": 0.1,
                "vector_score": 0.1,
                "rerank_score": 0.1,
                "text": "휴가 승인 절차",
            }
        ]
        rewrite_search_query.return_value = "문서에서 프로야구 두산베어스에 대해 설명"

        result = search_internal_documents.invoke(
            {"query": "문서에서 프로야구 두산베어스에 대해서 설명해줘"}
        )

        self.assertIsNone(result["rewritten_query"])
        self.assertEqual(search_documents.call_count, 1)

    def test_collects_rewritten_query_from_internal_search(self):
        message = ToolMessage(
            name="search_internal_documents",
            tool_call_id="call-1",
            content=json.dumps(
                {
                    "status": "ok",
                    "rewritten_query": "휴가 신청 승인 담당자와 절차",
                    "results": [],
                },
                ensure_ascii=False,
            ),
        )

        result = process_tool_results(
            {
                "messages": [message],
                "documents": [],
                "rewritten_queries": [],
                "executed_tools": [],
                "tool_call_count": 0,
            }
        )

        self.assertEqual(
            result["rewritten_queries"],
            ["휴가 신청 승인 담당자와 절차"],
        )

    def test_does_not_duplicate_the_same_rewritten_query(self):
        message = ToolMessage(
            name="search_internal_documents",
            tool_call_id="call-2",
            content=json.dumps(
                {
                    "status": "ok",
                    "rewritten_query": "휴가 신청 승인 담당자와 절차",
                    "results": [],
                },
                ensure_ascii=False,
            ),
        )

        result = process_tool_results(
            {
                "messages": [message],
                "documents": [],
                "rewritten_queries": ["휴가 신청 승인 담당자와 절차"],
                "executed_tools": [],
                "tool_call_count": 0,
            }
        )

        self.assertEqual(len(result["rewritten_queries"]), 1)


if __name__ == "__main__":
    unittest.main()
