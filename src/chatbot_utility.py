import os
import re
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = Path(__file__).resolve().parent
VECTOR_DB_DIR = PROJECT_ROOT / "vector_db"
CHAPTERS_VECTOR_DB_DIR = PROJECT_ROOT / "chapters_vector_db"

load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(SRC_DIR / ".env")

from langchain.chains import create_retrieval_chain
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain_chroma import Chroma
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_groq import ChatGroq
from langchain_huggingface import HuggingFaceEmbeddings

DEFAULT_VECTOR_DB_NAME = "class_12_biology_vector_db"
DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_CHAT_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")

GREETING_PATTERN = re.compile(
    r"\s*(?:hi|hello|hey|good morning|good afternoon|good evening)"
    r"(?:\s+there)?[!.?]*\s*",
    re.IGNORECASE,
)
CHAPTER_PATTERN = re.compile(
    r"\bchapter\s*[-:#]?\s*(1[0-3]|[1-9])\b",
    re.IGNORECASE,
)

SEARCH_MODES = {"auto", "selected", "complete"}

SYSTEM_PROMPT = """You are Study Pal, a helpful Biology learning assistant.
Answer the student's question using only the supplied textbook context.
Explain the answer clearly in student-friendly language.
If the context does not contain enough information, say that you could not
find the answer in the Biology textbook instead of inventing an answer.

Textbook context:
{context}
"""


def _normalise_words(value):
    """Return lowercase words for tolerant chapter-title matching."""
    return re.findall(r"[a-z0-9]+", value.lower())


@lru_cache(maxsize=1)
def available_chapter_names():
    """Return the numbered chapter databases available on disk."""
    chapters = {}
    if not CHAPTERS_VECTOR_DB_DIR.is_dir():
        return chapters

    for chapter_path in CHAPTERS_VECTOR_DB_DIR.iterdir():
        match = re.match(r"\s*(1[0-3]|[1-9])\.\s*(.+)", chapter_path.name)
        if chapter_path.is_dir() and match:
            chapters[int(match.group(1))] = chapter_path.name
    return dict(sorted(chapters.items()))


def detect_chapter_from_question(question):
    """Detect a chapter number/title, including small one-word typos."""
    explicit_match = CHAPTER_PATTERN.search(question)
    chapters = available_chapter_names()
    if explicit_match:
        chapter_number = int(explicit_match.group(1))
        return chapter_number, chapters.get(chapter_number, f"Chapter {chapter_number}")

    question_words = _normalise_words(question)
    normalised_question = " ".join(question_words)
    for chapter_number, chapter_name in chapters.items():
        title = chapter_name.partition(".")[2].strip()
        title_words = _normalise_words(title)
        if not title_words:
            continue

        if " ".join(title_words) in normalised_question:
            return chapter_number, chapter_name

        # A one-word chapter such as Ecosystem/Evolution should still be found
        # when a student makes a small spelling mistake (for example ECOSYTEM).
        if len(title_words) == 1 and any(
            SequenceMatcher(None, word, title_words[0]).ratio() >= 0.82
            for word in question_words
        ):
            return chapter_number, chapter_name

    return None


def resolve_chapter_scope(question, selected_chapters=None, search_mode="auto"):
    """Choose chapter databases according to the student's search preference."""
    if search_mode not in SEARCH_MODES:
        raise ValueError(f"Unknown search mode: {search_mode}")

    selected_numbers = []
    selected_names = {}
    for selected_chapter in selected_chapters or []:
        selected_match = re.match(r"\s*(1[0-3]|[1-9])\.", selected_chapter)
        if selected_match:
            number = int(selected_match.group(1))
            selected_numbers.append(number)
            selected_names[number] = selected_chapter

    detected = detect_chapter_from_question(question)
    notice = None
    if search_mode == "complete":
        chapter_numbers = []
    elif search_mode == "selected":
        chapter_numbers = selected_numbers
    elif detected:
        chapter_numbers = [detected[0]]
        if selected_numbers and detected[0] not in selected_numbers:
            old_scope = ", ".join(selected_names.values())
            notice = (
                f"Your question matches **{detected[1]}**, so I searched that "
                f"chapter instead of the selected **{old_scope}**."
            )
    else:
        chapter_numbers = selected_numbers

    chapter_names = available_chapter_names()
    scope_names = [
        chapter_names.get(number, selected_names.get(number, f"Chapter {number}"))
        for number in chapter_numbers
    ]
    return chapter_numbers, scope_names, notice


@lru_cache(maxsize=1)
def get_embedding():
    """Load the same embedding model used when the vector DB was created."""
    try:
        return HuggingFaceEmbeddings(
            model_name=DEFAULT_EMBEDDING_MODEL,
            model_kwargs={"local_files_only": True},
        )
    except OSError:
        return HuggingFaceEmbeddings(model_name=DEFAULT_EMBEDDING_MODEL)


@lru_cache(maxsize=4)
def get_vector_store(vector_db_name=DEFAULT_VECTOR_DB_NAME):
    """Open an existing persistent Chroma vector database."""
    vector_db_path = (VECTOR_DB_DIR / vector_db_name).resolve()
    if not vector_db_path.is_relative_to(VECTOR_DB_DIR.resolve()):
        raise ValueError("The vector database must be inside vector_db.")
    if not vector_db_path.is_dir():
        raise FileNotFoundError(
            f"Vector database not found: {vector_db_path}. "
            "Run src/vectorize_script.py first."
        )

    return Chroma(
        persist_directory=str(vector_db_path),
        embedding_function=get_embedding(),
    )


@lru_cache(maxsize=13)
def get_chapter_vector_store(chapter_number):
    """Open the dedicated vector database for a numbered chapter."""
    matches = sorted(CHAPTERS_VECTOR_DB_DIR.glob(f"{chapter_number}. *"))
    if not matches:
        raise FileNotFoundError(
            f"Vector database for Chapter {chapter_number} was not found. "
            "Run src/vectorize_script.py first."
        )

    chapter_path = matches[0].resolve()
    if not chapter_path.is_relative_to(CHAPTERS_VECTOR_DB_DIR.resolve()):
        raise ValueError("The chapter vector database path is invalid.")

    return (
        Chroma(
            persist_directory=str(chapter_path),
            embedding_function=get_embedding(),
        ),
        chapter_path.name,
    )


def build_answer_chain(model_name=DEFAULT_CHAT_MODEL):
    """Build the document-answering portion of the chatbot."""
    if not os.getenv("GROQ_API_KEY"):
        raise RuntimeError(
            "GROQ_API_KEY is missing. Add it to src/.env or the project .env file."
        )

    llm = ChatGroq(
        model=model_name,
        temperature=0,
    )
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", SYSTEM_PROMPT),
            MessagesPlaceholder("chat_history", optional=True),
            ("human", "{input}"),
        ]
    )
    return create_stuff_documents_chain(llm, prompt)


def build_chatbot_chain(
    vector_db_name=DEFAULT_VECTOR_DB_NAME,
    model_name=DEFAULT_CHAT_MODEL,
    retrieval_count=4,
):
    """Build a retrieval-augmented chat chain."""
    vector_store = get_vector_store(vector_db_name)
    retriever = vector_store.as_retriever(
        search_kwargs={"k": retrieval_count},
    )
    question_answer_chain = build_answer_chain(model_name)
    return create_retrieval_chain(retriever, question_answer_chain)


def get_chatbot_response(
    question,
    chat_history=None,
    vector_db_name=DEFAULT_VECTOR_DB_NAME,
    model_name=DEFAULT_CHAT_MODEL,
    retrieval_count=4,
    selected_chapters=None,
    search_mode="auto",
):
    """Return an answer and the retrieved textbook source metadata."""
    if not question or not question.strip():
        raise ValueError("Question cannot be empty.")

    cleaned_question = question.strip()
    if GREETING_PATTERN.fullmatch(cleaned_question):
        return {
            "answer": "Hello! 👋 How can I help you today?",
            "sources": [],
            "search_scope": [],
            "scope_notice": None,
        }

    chapter_numbers, search_scope, scope_notice = resolve_chapter_scope(
        cleaned_question,
        selected_chapters=selected_chapters,
        search_mode=search_mode,
    )
    if chapter_numbers:
        overview_request = re.search(
            r"\b(?:teach|overview|summary|summarize|complete|whole)\b",
            cleaned_question,
            re.IGNORECASE,
        )
        per_chapter_count = 8 if overview_request else max(retrieval_count, 4)
        if len(chapter_numbers) > 1:
            per_chapter_count = max(3, 8 // len(chapter_numbers))

        documents = []
        for chapter_number in chapter_numbers:
            chapter_store, chapter_name = get_chapter_vector_store(chapter_number)
            retrieval_query = (
                f"{chapter_name}. {cleaned_question}. "
                "Important concepts, processes, examples, and explanations."
            )
            documents.extend(
                chapter_store.max_marginal_relevance_search(
                    retrieval_query,
                    k=per_chapter_count,
                    fetch_k=max(per_chapter_count * 2, 20),
                )
            )

        answer_chain = build_answer_chain(model_name)
        answer = answer_chain.invoke(
            {
                "input": cleaned_question,
                "chat_history": chat_history or [],
                "context": documents,
            }
        )
        response = {
            "answer": answer,
            "context": documents,
        }
    else:
        chain = build_chatbot_chain(
            vector_db_name=vector_db_name,
            model_name=model_name,
            retrieval_count=retrieval_count,
        )
        response = chain.invoke(
            {
                "input": cleaned_question,
                "chat_history": chat_history or [],
            }
        )

    sources = []
    for document in response.get("context", []):
        metadata = dict(document.metadata)
        if metadata not in sources:
            sources.append(metadata)

    return {
        "answer": response["answer"],
        "sources": sources,
        "search_scope": search_scope,
        "scope_notice": scope_notice,
    }
