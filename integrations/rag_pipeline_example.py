"""Example: instrumenting a RAG pipeline end-to-end (embedding -> retrieval -> generation)
with the aiusage SDK. Works with any vector DB (Azure AI Search, pgvector, Pinecone,
Elastic, OpenSearch...). Run:  AIUSAGE_API_KEY=... python integrations/rag_pipeline_example.py
"""
import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sdk", "python"))
from aiusage import get_client  # noqa: E402

usage = get_client()
USER, PROJECT = "analyst1@firm.example", "TAX-ADVISORY-001"


def embed(text: str) -> list[float]:
    # real code: resp = openai_client.embeddings.create(model="text-embedding-3-large", input=text)
    tokens = max(1, len(text) // 4)
    usage.track_embedding(provider="azure_openai", model="text-embedding-3-large", input_tokens=tokens,
                          user_id=USER, project=PROJECT)
    return [random.random() for _ in range(8)]


def retrieve(vector: list[float]) -> list[str]:
    with usage.track_retrieval(vector_store="azure-ai-search", index_name="tax-guidance",
                               user_id=USER, project=PROJECT) as span:
        time.sleep(0.05)  # real code: search_client.search(vector_queries=[...], top=5)
        docs = [f"doc-{i}" for i in range(5)]
        span.documents_retrieved = len(docs)
    return docs


def generate(question: str, docs: list[str]) -> str:
    t0 = time.perf_counter()
    # real code: resp = client.chat.completions.create(...); u = resp.usage
    prompt_tokens, completion_tokens = 900 + 200 * len(docs), 180
    usage.track_llm(provider="azure_openai", model="gpt-4o", input_tokens=prompt_tokens,
                    output_tokens=completion_tokens, user_id=USER, project=PROJECT,
                    latency_ms=round((time.perf_counter() - t0) * 1000, 1))
    return "answer"


if __name__ == "__main__":
    q = "What changed in the 2026 transfer pricing guidance?"
    print(generate(q, retrieve(embed(q))))
    usage.flush()
    print("usage events sent")
