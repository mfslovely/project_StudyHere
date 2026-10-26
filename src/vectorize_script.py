import argparse
import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

from vectorize_book import (  # noqa: E402
    get_book_dir,
    vectorize_book_and_store_to_db,
    vectorize_chapters,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Vectorize a subject book and each of its PDF chapters."
    )
    parser.add_argument(
        "--class-subject-name",
        default=os.getenv("CLASS_SUBJECT_NAME", "class_12/biology"),
        help="Subject directory relative to DATA_DIRECTORY.",
    )
    parser.add_argument(
        "--vector-db-name",
        default="class_12_biology_vector_db",
        help="Directory name for the combined vector database.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate configuration and PDF discovery without vectorizing.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    book_dir, pdf_files = get_book_dir(args.class_subject_name)
    print(f"Found {len(pdf_files)} PDFs in: {book_dir}")

    if args.check:
        print("Configuration check passed.")
        return 0

    vectorize_book_and_store_to_db(
        args.class_subject_name,
        vector_db_name=args.vector_db_name,
    )
    vectorize_chapters(args.class_subject_name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
