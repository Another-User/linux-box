"""Document indexer for the OptAware knowledge layer."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

from models.knowledge import DocumentType, KnowledgeDocument

if TYPE_CHECKING:
    from knowledge.vector_store import VectorStore

logger = logging.getLogger(__name__)

# Fixed vocabulary size for the TF-IDF-like embedding (must match vector_size).
EMBEDDING_DIM = 384

# Common English stop-words excluded from the TF-IDF vocabulary.
_STOP_WORDS: frozenset[str] = frozenset(
    {
        "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
        "been", "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "shall", "can", "this",
        "that", "these", "those", "it", "its", "i", "you", "he", "she", "we",
        "they", "them", "their", "our", "your", "my", "his", "her", "not",
        "no", "if", "then", "else", "so", "up", "out", "about", "into",
        "than", "more", "also", "after", "before", "while", "when", "where",
        "what", "which", "who", "how", "all", "each", "any", "some", "such",
        "only", "other", "than", "same",
    }
)


class DocumentIndexer:
    """Index documents into the vector store for later RAG retrieval."""

    def __init__(self, vector_store: VectorStore) -> None:
        self.vector_store = vector_store

    # ------------------------------------------------------------------
    # Public indexing methods
    # ------------------------------------------------------------------

    async def index_document(self, doc: KnowledgeDocument) -> str:
        """Generate an embedding for *doc* and store it in the vector store.

        If the document is longer than ``chunk_size`` words it is split into
        overlapping chunks; each chunk is stored as a separate point whose ID
        is derived from the document UUID and the chunk index.

        Returns the primary document ID (the UUID of the first/only chunk).
        """
        await self.vector_store.ensure_collection(vector_size=EMBEDDING_DIM)

        chunks = self._chunk_text(doc.content)
        primary_id = str(doc.id)

        payload_base = {
            "doc_type": doc.doc_type.value,
            "title": doc.title,
            "source_path": doc.source_path,
            "tags": doc.tags,
            "created_at": doc.created_at.isoformat(),
            "total_chunks": len(chunks),
        }

        for idx, chunk in enumerate(chunks):
            chunk_id = f"{primary_id}#{idx}" if idx > 0 else primary_id
            vector = self._generate_embedding(chunk)
            payload = {
                **payload_base,
                "chunk_index": idx,
                "text": chunk,
            }
            await self.vector_store.upsert(chunk_id, vector, payload)

        logger.info(
            "Indexed document '%s' (%s) in %d chunk(s).",
            doc.title,
            doc.doc_type.value,
            len(chunks),
        )
        return primary_id

    async def index_man_page(self, command: str) -> str | None:
        """Run ``man <command>``, parse the output, and index it.

        Returns the document ID on success or None when the man page is not
        available.
        """
        try:
            result = subprocess.run(
                ["man", command],
                capture_output=True,
                text=True,
                timeout=15,
                env={"MANPAGER": "cat", "PATH": "/usr/bin:/bin:/usr/local/bin"},
            )
            raw = result.stdout
        except FileNotFoundError:
            logger.warning("'man' command not found; cannot index man page for '%s'.", command)
            return None
        except subprocess.TimeoutExpired:
            logger.warning("Timeout while reading man page for '%s'.", command)
            return None

        if not raw.strip():
            logger.warning("No man page found for '%s'.", command)
            return None

        # Strip ANSI escape codes and over-strike formatting used by groff.
        clean = re.sub(r"\x1b\[[0-9;]*m", "", raw)
        clean = re.sub(r".\x08", "", clean)  # backspace overstrike
        clean = re.sub(r"\x08.", "", clean)
        clean = re.sub(r"\s{3,}", "\n\n", clean).strip()

        doc = KnowledgeDocument(
            doc_type=DocumentType.man_page,
            title=f"man {command}",
            content=clean,
            source_path=f"man:{command}",
            tags=["man-page", command],
        )
        return await self.index_document(doc)

    async def index_config_file(self, path: str, description: str) -> str | None:
        """Read a configuration file and index it with descriptive context.

        Returns the document ID on success or None when the file cannot be read.
        """
        file_path = Path(path)
        if not file_path.exists():
            logger.warning("Config file not found: %s", path)
            return None

        try:
            content = file_path.read_text(errors="replace")
        except OSError as exc:
            logger.error("Cannot read config file '%s': %s", path, exc)
            return None

        enriched_content = (
            f"Configuration file: {path}\n"
            f"Description: {description}\n\n"
            f"{content}"
        )

        doc = KnowledgeDocument(
            doc_type=DocumentType.config_doc,
            title=f"Config: {file_path.name}",
            content=enriched_content,
            source_path=path,
            tags=["config", file_path.suffix.lstrip("."), file_path.name],
        )
        return await self.index_document(doc)

    async def index_runbook(self, path: str) -> str | None:
        """Parse and index a Markdown runbook file.

        Returns the document ID on success or None when the file cannot be read.
        """
        file_path = Path(path)
        if not file_path.exists():
            logger.warning("Runbook not found: %s", path)
            return None

        try:
            content = file_path.read_text(errors="replace")
        except OSError as exc:
            logger.error("Cannot read runbook '%s': %s", path, exc)
            return None

        # Extract a title from the first H1 heading, falling back to filename.
        title_match = re.search(r"^#\s+(.+)", content, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else file_path.stem

        doc = KnowledgeDocument(
            doc_type=DocumentType.runbook,
            title=title,
            content=content,
            source_path=path,
            tags=["runbook", file_path.stem],
        )
        return await self.index_document(doc)

    async def bulk_index(self, documents: list[KnowledgeDocument]) -> list[str]:
        """Index a batch of documents concurrently.

        Returns a list of document IDs (entries may be None for failed docs).
        """
        tasks = [self.index_document(doc) for doc in documents]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        ids: list[str] = []
        for doc, result in zip(documents, results):
            if isinstance(result, Exception):
                logger.error("Failed to index document '%s': %s", doc.title, result)
                ids.append("")
            else:
                ids.append(result)
        return ids

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _generate_embedding(self, text: str) -> list[float]:
        """Generate a 384-dimensional TF-IDF-like embedding from *text*.

        This deterministic, offline embedding can be replaced with a proper
        sentence-transformer model without changing the public interface.

        The approach:
        1. Tokenise and remove stop-words.
        2. Compute term frequencies.
        3. Map each token to a bucket in [0, EMBEDDING_DIM) via a stable hash.
        4. Accumulate TF weight into each bucket.
        5. Apply a mild IDF-like dampening using log(1 + count).
        6. L2-normalise the resulting vector.
        """
        tokens = re.findall(r"[a-zA-Z0-9_\-]+", text.lower())
        tokens = [t for t in tokens if t not in _STOP_WORDS and len(t) > 1]

        if not tokens:
            return [0.0] * EMBEDDING_DIM

        tf = Counter(tokens)
        total = sum(tf.values())

        vector = [0.0] * EMBEDDING_DIM
        for token, count in tf.items():
            # Stable bucket derived from the token's SHA-1.
            bucket = int(hashlib.sha1(token.encode()).hexdigest(), 16) % EMBEDDING_DIM
            tf_weight = count / total
            idf_weight = math.log(1.0 + count)
            vector[bucket] += tf_weight * idf_weight

        # L2 normalise.
        norm = math.sqrt(sum(v * v for v in vector))
        if norm > 0.0:
            vector = [v / norm for v in vector]

        return vector

    def _chunk_text(self, text: str, chunk_size: int = 500) -> list[str]:
        """Split *text* into overlapping chunks of at most *chunk_size* words.

        Chunks overlap by 20 % of *chunk_size* so that context is preserved
        across boundaries.  A single short text is returned as-is.
        """
        words = text.split()
        if len(words) <= chunk_size:
            return [text]

        overlap = max(1, chunk_size // 5)
        step = chunk_size - overlap
        chunks: list[str] = []
        start = 0
        while start < len(words):
            end = min(start + chunk_size, len(words))
            chunks.append(" ".join(words[start:end]))
            if end == len(words):
                break
            start += step

        return chunks
