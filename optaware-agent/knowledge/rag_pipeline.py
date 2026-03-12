"""Retrieval-augmented generation (RAG) pipeline for OptAware."""

from __future__ import annotations

import logging
import textwrap
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from knowledge.document_indexer import DocumentIndexer
    from knowledge.vector_store import VectorStore

logger = logging.getLogger(__name__)

# Rough estimate: 1 token ≈ 4 characters for English prose.
_CHARS_PER_TOKEN = 4


class RAGPipeline:
    """Retrieve relevant documents and assemble them into an LLM-ready context."""

    def __init__(self, vector_store: VectorStore, indexer: DocumentIndexer) -> None:
        self.vector_store = vector_store
        self.indexer = indexer

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    async def retrieve(self, query: str, top_k: int = 5) -> list[dict]:
        """Embed *query* and return the *top_k* most similar documents.

        Each entry in the returned list contains:
        - ``id``      — document / chunk ID
        - ``score``   — cosine similarity in [0, 1]
        - ``payload`` — stored metadata including ``text``, ``title``, etc.
        """
        query_vector = self.indexer._generate_embedding(query)
        results = await self.vector_store.search(query_vector, limit=top_k)
        logger.debug("Retrieved %d document(s) for query: %.80s…", len(results), query)
        return results

    def build_context(self, documents: list[dict], max_tokens: int = 2000) -> str:
        """Format *documents* into a single context string for an LLM prompt.

        Documents are included in descending relevance order until the combined
        text would exceed *max_tokens* (estimated).  Each document is separated
        by a clear delimiter so the LLM can distinguish individual sources.
        """
        if not documents:
            return "No relevant documents found in the knowledge base."

        max_chars = max_tokens * _CHARS_PER_TOKEN
        sections: list[str] = []
        used_chars = 0

        for rank, doc in enumerate(documents, start=1):
            payload = doc.get("payload", {})
            title = payload.get("title", "Untitled")
            doc_type = payload.get("doc_type", "unknown")
            source = payload.get("source_path") or "unknown source"
            score = doc.get("score", 0.0)
            text = payload.get("text", "")

            header = (
                f"[{rank}] {title} ({doc_type}) | source: {source} | relevance: {score:.3f}"
            )
            body = text.strip()

            # Truncate individual document body so we don't blow the budget on
            # a single very long chunk.
            remaining = max_chars - used_chars - len(header) - 4  # 4 = newlines
            if remaining <= 0:
                break
            if len(body) > remaining:
                body = body[:remaining] + "…"

            section = f"{header}\n{body}"
            sections.append(section)
            used_chars += len(section) + 2  # separator newlines

            if used_chars >= max_chars:
                break

        return "\n\n---\n\n".join(sections)

    async def query(self, question: str, top_k: int = 5) -> dict:
        """End-to-end RAG: retrieve docs, build context, return structured result.

        Returns a dict with:
        - ``query``   — the original question
        - ``context`` — assembled context string ready for injection into a prompt
        - ``sources`` — list of source metadata dicts (title, doc_type, source_path, score)
        - ``doc_count`` — number of documents retrieved
        """
        documents = await self.retrieve(question, top_k=top_k)
        context = self.build_context(documents)

        sources = [
            {
                "id": doc.get("id"),
                "title": doc.get("payload", {}).get("title", "Untitled"),
                "doc_type": doc.get("payload", {}).get("doc_type", "unknown"),
                "source_path": doc.get("payload", {}).get("source_path"),
                "score": doc.get("score", 0.0),
                "chunk_index": doc.get("payload", {}).get("chunk_index", 0),
            }
            for doc in documents
        ]

        return {
            "query": question,
            "context": context,
            "sources": sources,
            "doc_count": len(documents),
        }

    def format_for_llm(self, query_result: dict) -> str:
        """Render a ``query()`` result as a formatted block for inclusion in an LLM prompt.

        The output follows the convention:

        ```
        ## Relevant Knowledge Base Context

        <context>

        ## Sources
        1. title (doc_type) — source_path [score=0.92]
        ...

        ## Question
        <query>
        ```
        """
        question = query_result.get("query", "")
        context = query_result.get("context", "")
        sources = query_result.get("sources", [])

        source_lines: list[str] = []
        for idx, src in enumerate(sources, start=1):
            title = src.get("title", "Untitled")
            doc_type = src.get("doc_type", "unknown")
            source_path = src.get("source_path") or "n/a"
            score = src.get("score", 0.0)
            chunk = src.get("chunk_index", 0)
            line = f"{idx}. {title} ({doc_type}) — {source_path} [score={score:.3f}, chunk={chunk}]"
            source_lines.append(line)

        sources_block = "\n".join(source_lines) if source_lines else "No sources available."

        return textwrap.dedent(
            f"""\
            ## Relevant Knowledge Base Context

            {context}

            ## Sources
            {sources_block}

            ## Question
            {question}
            """
        )
