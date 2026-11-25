"""Patches applied to the upstream llm-graph-builder backend checkout. Safe to run repeatedly."""

import sys
from pathlib import Path


def patch(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    if text.count(old) != 1:
        raise SystemExit(f"{path}: expected exactly one match for patch anchor:\n{old}")
    path.write_text(text.replace(old, new), encoding="utf-8")


backend = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
common_fn = backend / "src/shared/common_fn.py"
llm = backend / "src/llm.py"

# Upstream only knows all-MiniLM-L6-v2 (384); register the configured model's dimension.
patch(
    common_fn,
    '"sentence-transformer": {\n',
    '"sentence-transformer": {os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2"): int(os.getenv("EMBEDDING_DIMENSION", "384")),\n',
)

# Upstream sends every chunk of a document to the LLM at once. Providers that cap
# concurrent requests reject the overflow with 429, so send chunks in small batches
# and let the OpenAI client back off and retry.
patch(llm, "import logging\nfrom langchain_core", "import logging\nimport os\nfrom langchain_core")
patch(
    llm,
    """                base_url=api_endpoint,
                model=model_name,
                temperature=0,
                callbacks=callback_manager,
""",
    """                base_url=api_endpoint,
                model=model_name,
                temperature=0,
                max_retries=int(os.getenv("LLM_MAX_RETRIES", "8")),
                callbacks=callback_manager,
""",
)
patch(
    llm,
    """            graph_document_list = await llm_transformer.aconvert_to_graph_documents(combined_chunk_document_list)
""",
    """            batch_size = max(1, int(os.getenv("LLM_MAX_CONCURRENCY", "2")))
            graph_document_list = []
            for start in range(0, len(combined_chunk_document_list), batch_size):
                batch = combined_chunk_document_list[start:start + batch_size]
                graph_document_list.extend(await llm_transformer.aconvert_to_graph_documents(batch))
""",
)
