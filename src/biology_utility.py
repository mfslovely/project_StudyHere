"""Lightweight Biology textbook retrieval suitable for the Render deployment."""

import json
import os
import re
from collections import Counter
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from langchain_groq import ChatGroq


PROJECT_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = PROJECT_ROOT / "data_indexes" / "biology_chunks.json"
load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env")

GREETING_PATTERN = re.compile(
    r"\s*(?:hi|hello|hey|good morning|good afternoon|good evening)(?:\s+there)?[!.?]*\s*",
    re.IGNORECASE,
)
CHAPTER_PATTERN = re.compile(r"\bchapter\s*[-:#]?\s*(1[0-3]|[1-9])\b", re.IGNORECASE)
STOP_WORDS = {
    "about", "after", "also", "and", "are", "can", "class", "could", "explain",
    "for", "from", "give", "how", "into", "its", "lesson", "make", "more", "please",
    "short", "tell", "that", "the", "their", "this", "what", "when", "where", "which",
    "why", "with", "you", "your",
}


@lru_cache(maxsize=1)
def _records():
    if not INDEX_PATH.is_file():
        return []
    return json.loads(INDEX_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _chapter_map():
    chapters = {}
    for record in _records():
        name = record["chapter"]
        number = int(name.partition(".")[0])
        chapters[number] = name
    return dict(sorted(chapters.items()))


def available_biology_chapters():
    return list(_chapter_map().values())


def _tokens(value):
    return [word for word in re.findall(r"[a-z0-9]+", value.lower()) if len(word) > 2]


def _detect_chapter(question):
    chapters = _chapter_map()
    explicit = CHAPTER_PATTERN.search(question)
    if explicit:
        number = int(explicit.group(1))
        return number, chapters.get(number, f"Chapter {number}")

    question_words = _tokens(question)
    normalised_question = " ".join(question_words)
    for number, chapter in chapters.items():
        title_words = _tokens(chapter.partition(".")[2])
        if " ".join(title_words) in normalised_question:
            return number, chapter
        if len(title_words) == 1 and any(
            SequenceMatcher(None, word, title_words[0]).ratio() >= 0.82
            for word in question_words
        ):
            return number, chapter
    return None


def _resolve_scope(question, selected_chapters, search_mode):
    selected = {}
    for chapter in selected_chapters or []:
        number = chapter.partition(".")[0]
        if number.isdigit():
            selected[int(number)] = chapter

    detected = _detect_chapter(question)
    notice = None
    if search_mode == "complete":
        numbers = []
    elif search_mode == "selected":
        numbers = list(selected)
    elif detected:
        numbers = [detected[0]]
        if selected and detected[0] not in selected:
            notice = (
                f"Your question matches **{detected[1]}**, so I searched that chapter "
                "instead of the selected chapter."
            )
    else:
        numbers = list(selected)

    chapters = _chapter_map()
    names = [chapters.get(number, selected.get(number, f"Chapter {number}")) for number in numbers]
    return numbers, names, notice


def _retrieve(question, chapter_numbers, limit=6):
    query_counts = Counter(word for word in _tokens(question) if word not in STOP_WORDS)
    overview = bool(re.search(r"\b(?:teach|overview|summary|summarize|whole|complete)\b", question, re.I))
    ranked = []
    for position, record in enumerate(_records()):
        number = int(record["chapter"].partition(".")[0])
        if chapter_numbers and number not in chapter_numbers:
            continue
        text_counts = Counter(_tokens(record["text"]))
        title_tokens = set(_tokens(record["chapter"]))
        score = sum(min(text_counts[word], 4) for word in query_counts)
        score += 5 * len(set(query_counts) & title_tokens)
        if overview and record.get("page") in (0, 1, None):
            score += 3
        ranked.append((score, -position, record))
    ranked.sort(reverse=True, key=lambda item: (item[0], item[1]))
    return [record for _, _, record in ranked[:limit]]


def _fallback_answer(question, documents):
    if not documents:
        return "I couldn't find that topic in the supplied Biology textbook."
    excerpt = re.sub(r"\s+", " ", documents[0]["text"]).strip()
    if len(excerpt) > 1500:
        excerpt = excerpt[:1500].rsplit(" ", 1)[0] + "…"
    return (
        "### Textbook result\n\n"
        f"{excerpt}\n\n"
        "_This is a direct textbook extract. Add `GROQ_API_KEY` on Render to get a "
        "short, student-friendly AI explanation as well._"
    )


def get_biology_response(
    question,
    chat_history=None,
    selected_chapters=None,
    search_mode="auto",
    **_,
):
    if not question or not question.strip():
        raise ValueError("Question cannot be empty.")
    question = question.strip()
    if GREETING_PATTERN.fullmatch(question):
        return {"answer": "Hello! 👋 How can I help you today?", "sources": [], "search_scope": [], "scope_notice": None}

    chapter_numbers, scope_names, notice = _resolve_scope(
        question, selected_chapters, search_mode
    )
    documents = _retrieve(question, chapter_numbers)
    api_key = os.getenv("GROQ_API_KEY")
    if api_key and documents:
        context = "\n\n".join(
            f"[{record['chapter']} — page {(record.get('page') or 0) + 1}]\n{record['text']}"
            for record in documents
        )
        history = "\n".join(f"{role}: {content}" for role, content in (chat_history or [])[-6:])
        prompt = (
            "You are Learn.ai, a helpful Biology learning assistant. Answer using only the "
            "textbook context below. Use clear, student-friendly language. If context is "
            "insufficient, say so.\n\n"
            f"Recent conversation:\n{history}\n\nTextbook context:\n{context}\n\n"
            f"Student question: {question}"
        )
        try:
            answer = ChatGroq(
                model=os.getenv("GROQ_MODEL", "openai/gpt-oss-20b"), temperature=0
            ).invoke(prompt).content
        except Exception:
            # Keep the textbook useful if the configured model is temporarily
            # unavailable or the deployment has no outbound connection.
            answer = _fallback_answer(question, documents)
    else:
        answer = _fallback_answer(question, documents)

    sources = []
    for record in documents:
        source = {"source": record["source"], "page": record.get("page")}
        if source not in sources:
            sources.append(source)
    return {
        "answer": answer,
        "sources": sources,
        "search_scope": scope_names,
        "scope_notice": notice,
    }
