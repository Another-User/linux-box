"""Qdrant vector store interface for OptAware knowledge layer."""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class VectorStore:
    """Interface to a Qdrant vector database via its REST API."""

    def __init__(
        self,
        url: str = "http://localhost:6333",
        collection_name: str = "optaware-knowledge",
    ) -> None:
        self.url = url.rstrip("/")
        self.collection_name = collection_name
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.url,
                timeout=httpx.Timeout(10.0),
            )
        return self._client

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response | None:
        """Execute an HTTP request, returning None when Qdrant is unavailable."""
        client = self._get_client()
        try:
            response = await client.request(method, path, **kwargs)
            response.raise_for_status()
            return response
        except httpx.ConnectError:
            logger.warning("Qdrant is unavailable at %s — skipping request %s %s", self.url, method, path)
            return None
        except httpx.TimeoutException:
            logger.warning("Qdrant request timed out: %s %s", method, path)
            return None
        except httpx.HTTPStatusError as exc:
            # 409 Conflict during collection creation is expected when the
            # collection already exists — treat it as success.
            if exc.response.status_code == 409:
                return exc.response
            logger.error(
                "Qdrant HTTP error %s for %s %s: %s",
                exc.response.status_code,
                method,
                path,
                exc.response.text,
            )
            return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def ensure_collection(self, vector_size: int = 384) -> bool:
        """Create the collection if it does not already exist.

        Returns True when the collection is ready, False when Qdrant is
        unavailable.
        """
        # Check whether the collection already exists.
        response = await self._request(
            "GET",
            f"/collections/{self.collection_name}",
        )
        if response is not None and response.status_code == 200:
            logger.debug("Collection '%s' already exists.", self.collection_name)
            return True

        # Create the collection.
        payload = {
            "vectors": {
                "size": vector_size,
                "distance": "Cosine",
            }
        }
        response = await self._request(
            "PUT",
            f"/collections/{self.collection_name}",
            json=payload,
        )
        if response is None:
            return False

        logger.info("Created Qdrant collection '%s' (size=%d).", self.collection_name, vector_size)
        return True

    async def upsert(self, doc_id: str, vector: list[float], payload: dict) -> bool:
        """Store or update a vector together with its metadata payload.

        Returns True on success, False when Qdrant is unavailable.
        """
        # Qdrant point IDs must be unsigned integers or UUIDs.  We derive a
        # stable UUID from the string doc_id so callers can use arbitrary IDs.
        point_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, doc_id))

        body = {
            "points": [
                {
                    "id": point_uuid,
                    "vector": vector,
                    "payload": {**payload, "_doc_id": doc_id},
                }
            ]
        }
        response = await self._request(
            "PUT",
            f"/collections/{self.collection_name}/points",
            json=body,
        )
        if response is None:
            return False

        logger.debug("Upserted document '%s' into '%s'.", doc_id, self.collection_name)
        return True

    async def search(self, query_vector: list[float], limit: int = 5) -> list[dict]:
        """Perform a cosine-similarity search and return up to *limit* results.

        Each result dict contains the keys ``id``, ``score``, and ``payload``.
        Returns an empty list when Qdrant is unavailable.
        """
        body = {
            "vector": query_vector,
            "limit": limit,
            "with_payload": True,
            "with_vector": False,
        }
        response = await self._request(
            "POST",
            f"/collections/{self.collection_name}/points/search",
            json=body,
        )
        if response is None:
            return []

        data = response.json()
        results: list[dict] = []
        for hit in data.get("result", []):
            results.append(
                {
                    "id": hit.get("payload", {}).get("_doc_id", hit["id"]),
                    "score": hit["score"],
                    "payload": {k: v for k, v in hit.get("payload", {}).items() if k != "_doc_id"},
                }
            )
        return results

    async def delete(self, doc_id: str) -> bool:
        """Remove a document from the collection by its string doc_id.

        Returns True on success, False when Qdrant is unavailable.
        """
        point_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, doc_id))

        body = {"points": [point_uuid]}
        response = await self._request(
            "POST",
            f"/collections/{self.collection_name}/points/delete",
            json=body,
        )
        if response is None:
            return False

        logger.debug("Deleted document '%s' from '%s'.", doc_id, self.collection_name)
        return True

    async def get_collection_info(self) -> dict:
        """Return collection statistics from Qdrant.

        Returns an empty dict when Qdrant is unavailable.
        """
        response = await self._request(
            "GET",
            f"/collections/{self.collection_name}",
        )
        if response is None:
            return {}

        data = response.json()
        result = data.get("result", {})
        config = result.get("config", {})
        vectors_config = config.get("params", {}).get("vectors", {})

        return {
            "collection_name": self.collection_name,
            "status": result.get("status", "unknown"),
            "vectors_count": result.get("vectors_count", 0),
            "points_count": result.get("points_count", 0),
            "segments_count": result.get("segments_count", 0),
            "vector_size": vectors_config.get("size", 0),
            "distance": vectors_config.get("distance", "unknown"),
        }

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
