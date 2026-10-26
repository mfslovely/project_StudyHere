import os
from pathlib import Path

from langchain_chroma import Chroma
from langchain_community.document_loaders import DirectoryLoader, PyMuPDFLoader
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import CharacterTextSplitter


PROJECT_ROOT = Path(__file__).resolve().parent.parent

configured_data_dir = Path(os.getenv("DATA_DIRECTORY", "data_original"))
if configured_data_dir.is_absolute():
    data_dir = configured_data_dir.resolve()
else:
    data_dir = (PROJECT_ROOT / configured_data_dir).resolve()

vector_db_dir = PROJECT_ROOT / "vector_db"
chapters_vector_db_dir = PROJECT_ROOT / "chapters_vector_db"

text_splitter = CharacterTextSplitter(
    chunk_size=2000,
    chunk_overlap=500,
)

_embedding = None


def get_embedding():
    """Load the embedding model only when vectorization actually starts."""
    global _embedding
    if _embedding is None:
        model_name = "sentence-transformers/all-MiniLM-L6-v2"
        try:
            _embedding = HuggingFaceEmbeddings(
                model_name=model_name,
                model_kwargs={"local_files_only": True},
            )
        except OSError:
            print(f"Downloading embedding model: {model_name}")
            _embedding = HuggingFaceEmbeddings(model_name=model_name)
    return _embedding


def get_book_dir(class_subject_name):
    """Resolve and validate the configured subject directory."""
    if not class_subject_name:
        raise ValueError(
            "CLASS_SUBJECT_NAME is empty. Set it to a path such as "
            "'class_12/biology'."
        )

    book_dir = (data_dir / class_subject_name).resolve()
    if not book_dir.is_relative_to(data_dir):
        raise ValueError("CLASS_SUBJECT_NAME must stay inside DATA_DIRECTORY.")
    if not book_dir.is_dir():
        raise FileNotFoundError(f"Subject directory not found: {book_dir}")

    pdf_files = sorted(book_dir.glob("*.pdf"))
    if not pdf_files:
        raise FileNotFoundError(f"No PDF files found in: {book_dir}")

    return book_dir, pdf_files


def vectorize_book_and_store_to_db(class_subject_name, vector_db_name):
    book_dir, _ = get_book_dir(class_subject_name)
    vector_db_path = vector_db_dir / vector_db_name
    vector_db_path.parent.mkdir(parents=True, exist_ok=True)

    loader = DirectoryLoader(
        path=str(book_dir),
        glob="*.pdf",
        loader_cls=PyMuPDFLoader,
        loader_kwargs={"mode": "page"},
    )

    documents = loader.load()
    text_chunks = text_splitter.split_documents(documents)

    Chroma.from_documents(
        documents=text_chunks,
        embedding=get_embedding(),
        persist_directory=str(vector_db_path),
    )

    print(f"{class_subject_name} saved to vector db: {vector_db_name}")
    return 0


def vectorize_chapters(class_subject_name):
    _, pdf_files = get_book_dir(class_subject_name)
    chapters_vector_db_dir.mkdir(parents=True, exist_ok=True)

    for chapter_pdf_path in pdf_files:
        chapter_name = chapter_pdf_path.stem

        loader = PyMuPDFLoader(str(chapter_pdf_path), mode="page")
        documents = loader.load()
        texts = text_splitter.split_documents(documents)

        Chroma.from_documents(
            documents=texts,
            embedding=get_embedding(),
            persist_directory=str(chapters_vector_db_dir / chapter_name),
        )

        print(f"{chapter_name} chapter vectorized")

    return 0
