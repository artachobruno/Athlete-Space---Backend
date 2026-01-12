"""Metadata index for filtering chunks.

This module provides fast metadata-based filtering before vector search.
"""


from app.rag.types import Domain, RagChunk


class MetadataIndex:
    """In-memory metadata index for fast filtering."""

    def __init__(self, chunks: list[RagChunk]):
        """Initialize metadata index.

        Args:
            chunks: List of chunks to index
        """
        self.chunks = chunks

        # Build inverted indices
        self.domain_index: dict[Domain, list[int]] = {}
        self.doc_type_index: dict[str, list[int]] = {}
        self.philosophy_id_index: dict[str, list[int]] = {}
        self.race_type_index: dict[str, list[int]] = {}
        self.risk_level_index: dict[str, list[int]] = {}
        self.audience_index: dict[str, list[int]] = {}
        self.requires_index: dict[str, list[int]] = {}
        self.prohibits_index: dict[str, list[int]] = {}

        for idx, chunk in enumerate(chunks):
            # Index by domain
            domain_raw = chunk.metadata.get("domain", "")
            if domain_raw:
                # Cast to Domain type (will be validated during normalization)
                domain: Domain = domain_raw  # type: ignore[assignment]
                if domain not in self.domain_index:
                    self.domain_index[domain] = []
                self.domain_index[domain].append(idx)

            # Index by doc_type
            doc_type = chunk.metadata.get("doc_type", "")
            if doc_type:
                if doc_type not in self.doc_type_index:
                    self.doc_type_index[doc_type] = []
                self.doc_type_index[doc_type].append(idx)

            # Index by philosophy_id
            philosophy_id = chunk.metadata.get("philosophy_id", "")
            if philosophy_id:
                if philosophy_id not in self.philosophy_id_index:
                    self.philosophy_id_index[philosophy_id] = []
                self.philosophy_id_index[philosophy_id].append(idx)

            # Index by race types
            race_types_str = chunk.metadata.get("race_types", "")
            if race_types_str:
                race_types = [rt.strip() for rt in race_types_str.split(",") if rt.strip()]
                for rt in race_types:
                    if rt not in self.race_type_index:
                        self.race_type_index[rt] = []
                    self.race_type_index[rt].append(idx)

            # Index by risk level
            risk_level = chunk.metadata.get("risk_level", "")
            if risk_level:
                if risk_level not in self.risk_level_index:
                    self.risk_level_index[risk_level] = []
                self.risk_level_index[risk_level].append(idx)

            # Index by audience
            audience = chunk.metadata.get("audience", "")
            if audience:
                if audience not in self.audience_index:
                    self.audience_index[audience] = []
                self.audience_index[audience].append(idx)

            # Index by requires
            requires_str = chunk.metadata.get("requires", "")
            if requires_str:
                requires = [r.strip() for r in requires_str.split(",") if r.strip()]
                for req in requires:
                    if req not in self.requires_index:
                        self.requires_index[req] = []
                    self.requires_index[req].append(idx)

            # Index by prohibits
            prohibits_str = chunk.metadata.get("prohibits", "")
            if prohibits_str:
                prohibits = [p.strip() for p in prohibits_str.split(",") if p.strip()]
                for proh in prohibits:
                    if proh not in self.prohibits_index:
                        self.prohibits_index[proh] = []
                    self.prohibits_index[proh].append(idx)

    def filter(
        self,
        *,
        domain: Domain | None = None,
        doc_type: str | None = None,
        philosophy_id: str | None = None,
        race_type: str | None = None,
        risk_level: str | None = None,
        audience: str | None = None,
        requires: list[str] | None = None,
        prohibits: list[str] | None = None,
    ) -> list[RagChunk]:
        """Filter chunks by metadata criteria.

        Args:
            domain: Filter by domain
            doc_type: Filter by doc_type (e.g., "philosophy", "principle")
            philosophy_id: Filter by philosophy_id (e.g., "norwegian")
            race_type: Filter by race type
            risk_level: Filter by risk level
            audience: Filter by audience
            requires: Filter chunks that require these tags
            prohibits: Filter chunks that prohibit these tags

        Returns:
            List of chunks matching all criteria
        """
        candidate_indices: set[int] | None = None

        # Filter by domain
        if domain:
            domain_indices = set(self.domain_index.get(domain, []))
            if candidate_indices is None:
                candidate_indices = domain_indices
            else:
                candidate_indices &= domain_indices

        # Filter by doc_type
        if doc_type:
            doc_type_indices = set(self.doc_type_index.get(doc_type, []))
            if candidate_indices is None:
                candidate_indices = doc_type_indices
            else:
                candidate_indices &= doc_type_indices

        # Filter by philosophy_id
        if philosophy_id:
            philosophy_indices = set(self.philosophy_id_index.get(philosophy_id, []))
            if candidate_indices is None:
                candidate_indices = philosophy_indices
            else:
                candidate_indices &= philosophy_indices

        # Filter by race type
        if race_type:
            race_indices = set(self.race_type_index.get(race_type, []))
            if candidate_indices is None:
                candidate_indices = race_indices
            else:
                candidate_indices &= race_indices

        # Filter by risk level
        if risk_level:
            risk_indices = set(self.risk_level_index.get(risk_level, []))
            if candidate_indices is None:
                candidate_indices = risk_indices
            else:
                candidate_indices &= risk_indices

        # Filter by audience
        if audience:
            audience_indices = set(self.audience_index.get(audience, []))
            if candidate_indices is None:
                candidate_indices = audience_indices
            else:
                candidate_indices &= audience_indices

        # Filter by requires (chunk must have ALL required tags)
        if requires:
            requires_sets: list[set[int]] = []
            for req in requires:
                req_indices = set(self.requires_index.get(req, []))
                requires_sets.append(req_indices)

            if requires_sets:
                # Chunk must appear in ALL requires sets
                requires_intersection = set.intersection(*requires_sets) if requires_sets else set()
                if candidate_indices is None:
                    candidate_indices = requires_intersection
                else:
                    candidate_indices &= requires_intersection

        # Filter by prohibits (chunk must NOT have ANY prohibited tags)
        if prohibits:
            prohibits_union: set[int] = set()
            for proh in prohibits:
                proh_indices = set(self.prohibits_index.get(proh, []))
                prohibits_union |= proh_indices

            if candidate_indices is None:
                # Start with all indices, then remove prohibited
                candidate_indices = set(range(len(self.chunks))) - prohibits_union
            else:
                candidate_indices -= prohibits_union

        # If no filters applied, return all chunks
        if candidate_indices is None:
            return self.chunks.copy()

        # Return filtered chunks
        return [self.chunks[idx] for idx in sorted(candidate_indices)]
