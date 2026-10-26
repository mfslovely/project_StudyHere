"""Export local Chroma chapter data into a small deployment-friendly JSON index."""

import json
from pathlib import Path

from langchain_chroma import Chroma


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHAPTERS_DIR = PROJECT_ROOT / "chapters_vector_db"
OUTPUT_PATH = PROJECT_ROOT / "data_indexes" / "biology_chunks.json"


def export_index():
    records = []
    for chapter_path in sorted(
        CHAPTERS_DIR.iterdir(),
        key=lambda path: int(path.name.partition(".")[0]),
    ):
        if not chapter_path.is_dir() or not chapter_path.name.partition(".")[0].isdigit():
            continue
        payload = Chroma(persist_directory=str(chapter_path)).get(
            include=["documents", "metadatas"]
        )
        for document, metadata in zip(payload["documents"], payload["metadatas"]):
            if not document:
                continue
            metadata = metadata or {}
            records.append(
                {
                    "chapter": chapter_path.name,
                    "text": document,
                    "source": Path(metadata.get("source", f"{chapter_path.name}.pdf")).name,
                    "page": metadata.get("page"),
                }
            )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(records, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"Exported {len(records)} textbook chunks to {OUTPUT_PATH}")


if __name__ == "__main__":
    export_index()
