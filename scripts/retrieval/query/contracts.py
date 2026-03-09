from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from sqlite_query_guardrails import GuardrailError

CHUNK_REQUIRED_QUERY_IDS = {
    "chunk_corpus_v1_all",
    "lexical_chunk_search_v1",
    "table1_row_requirements_v2",
}
LEGACY_REQUIRED_QUERY_IDS = {
    "statement_corpus_v3_all",
    "lexical_statement_search_v2",
    "table1_row_requirements_v1",
}


@dataclass(frozen=True)
class RetrievalContractProfile:
    corpus_query_id: str
    corpus_cursor_param: str
    lexical_query_id: str
    lexical_subset_query_id: str | None
    lexical_id_column: str
    row_requirements_query_id: str
    embedding_table: str
    embedding_id_column: str
    embed_version: str


def load_contract_query_ids(contract_path: Path) -> set[str]:
    with contract_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise GuardrailError("Contract payload must be a mapping")

    raw_queries = payload.get("queries") or {}
    if not isinstance(raw_queries, dict) or not raw_queries:
        raise GuardrailError("Contract must define a non-empty queries mapping")
    return {str(query_id).strip() for query_id in raw_queries.keys() if str(query_id).strip()}


def resolve_retrieval_contract_profile(contract_path: Path) -> RetrievalContractProfile:
    query_ids = load_contract_query_ids(contract_path)
    chunk_present = sorted(CHUNK_REQUIRED_QUERY_IDS.intersection(query_ids))
    if chunk_present:
        missing_chunk = sorted(CHUNK_REQUIRED_QUERY_IDS.difference(query_ids))
        if missing_chunk:
            raise GuardrailError(
                "Chunk retrieval contract is incomplete; missing query ids: "
                + ", ".join(missing_chunk)
            )
        return RetrievalContractProfile(
            corpus_query_id="chunk_corpus_v1_all",
            corpus_cursor_param="chunk_uid_after",
            lexical_query_id="lexical_chunk_search_v1",
            lexical_subset_query_id=(
                "lexical_chunk_search_v2_subset"
                if "lexical_chunk_search_v2_subset" in query_ids
                else None
            ),
            lexical_id_column="chunk_uid",
            row_requirements_query_id="table1_row_requirements_v2",
            embedding_table="chunk_embeddings",
            embedding_id_column="chunk_uid",
            embed_version="chunk-v1",
        )

    missing_legacy = sorted(LEGACY_REQUIRED_QUERY_IDS.difference(query_ids))
    if missing_legacy:
        raise GuardrailError(
            "Contract missing retrieval query ids. Expected either chunk ids "
            f"{sorted(CHUNK_REQUIRED_QUERY_IDS)} or legacy ids "
            f"{sorted(LEGACY_REQUIRED_QUERY_IDS)}; missing legacy ids: {missing_legacy}"
        )
    return RetrievalContractProfile(
        corpus_query_id="statement_corpus_v3_all",
        corpus_cursor_param="statement_id_after",
        lexical_query_id="lexical_statement_search_v2",
        lexical_subset_query_id=None,
        lexical_id_column="statement_id",
        row_requirements_query_id="table1_row_requirements_v1",
        embedding_table="statement_embeddings",
        embedding_id_column="statement_id",
        embed_version="statement-v1",
    )
