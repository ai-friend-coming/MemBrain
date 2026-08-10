"""通过真实 MemBrain HTTP API 验证 File RAG 的完整链路。"""

from __future__ import annotations

import hashlib
import os
import unittest
import uuid

import httpx

_BASE_URL = os.getenv("MEMBRAIN_E2E_BASE_URL", "")


@unittest.skipUnless(
    _BASE_URL,
    "需要 MEMBRAIN_E2E_BASE_URL 才能运行真实 File RAG API 测试",
)
class FileRagApiE2ETest(unittest.TestCase):
    """验证文件索引、Chat 隔离召回、幂等和删除。"""

    @classmethod
    def setUpClass(cls) -> None:
        """创建 HTTP 客户端和本轮独立 Chat ID。"""
        cls.client = httpx.Client(base_url=_BASE_URL, timeout=120)
        cls.client.get("/health").raise_for_status()
        cls.chat_id = f"file-rag-e2e-{uuid.uuid4().hex}"
        cls.document_id = "release-notes"
        cls.content = b"Project Aurora release gate is exactly Friday at 18:00 UTC."
        cls.other_document_id = "other-release-notes"
        cls.other_content = (
            b"Project Borealis release gate is exactly Monday at 09:00 UTC."
        )

    @classmethod
    def tearDownClass(cls) -> None:
        """清理测试文件库并关闭 HTTP 客户端。"""
        cls.client.delete(f"/api/file-libraries/{cls.chat_id}")
        cls.client.close()

    def test_file_rag_lifecycle(self) -> None:
        """跑通索引、重复索引、隔离检索和删除后的空召回。"""
        path = f"/api/file-libraries/{self.chat_id}/documents/{self.document_id}"
        digest = hashlib.sha256(self.content).hexdigest()
        upload = {
            "files": {"file": ("release.txt", self.content, "text/plain")},
            "data": {"content_sha256": digest},
        }

        indexed = self.client.put(path, **upload)
        indexed.raise_for_status()
        self.assertEqual(indexed.json()["status"], "indexed")
        self.assertGreater(indexed.json()["chunk_count"], 0)

        repeated = self.client.put(path, **upload)
        repeated.raise_for_status()
        self.assertEqual(repeated.json()["status"], "already_indexed")
        self.assertEqual(
            repeated.json()["index_version"], indexed.json()["index_version"]
        )

        other_path = (
            f"/api/file-libraries/{self.chat_id}/documents/{self.other_document_id}"
        )
        other = self.client.put(
            other_path,
            files={"file": ("other-release.txt", self.other_content, "text/plain")},
            data={"content_sha256": hashlib.sha256(self.other_content).hexdigest()},
        )
        other.raise_for_status()

        found = self.client.post(
            f"/api/file-libraries/{self.chat_id}/search",
            json={
                "query": "When is the release gate?",
                "document_ids": [self.document_id],
            },
        )
        found.raise_for_status()
        self.assertTrue(found.json()["chunks"])
        self.assertTrue(
            all(
                chunk["document_id"] == self.document_id
                for chunk in found.json()["chunks"]
            )
        )
        self.assertEqual(found.json()["chunks"][0]["document_id"], self.document_id)
        self.assertIn("Friday", found.json()["packed_context"])
        self.assertIn("release.txt", found.json()["chunks"][0]["context_prefix"])
        self.assertTrue(found.json()["chunks"][0]["retrieval_sources"])
        self.assertGreater(found.json()["chunks"][0]["rrf_score"], 0)

        isolated = self.client.post(
            "/api/file-libraries/another-chat/search",
            json={"query": "When is the Aurora release gate?"},
        )
        isolated.raise_for_status()
        self.assertEqual(isolated.json()["chunks"], [])

        deleted = self.client.delete(path)
        deleted.raise_for_status()
        self.assertEqual(deleted.json()["deleted_documents"], 1)

        after_delete = self.client.post(
            f"/api/file-libraries/{self.chat_id}/search",
            json={
                "query": "When is the Aurora release gate?",
                "document_ids": [self.document_id],
            },
        )
        after_delete.raise_for_status()
        self.assertEqual(after_delete.json()["chunks"], [])
