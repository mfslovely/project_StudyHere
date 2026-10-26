from html import escape
from pathlib import Path

import streamlit as st

from chatbot_utility import detect_chapter_from_question, get_chatbot_response
from get_yt_video import is_video_request, search_youtube_videos


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHAPTERS_VECTOR_DB_DIR = PROJECT_ROOT / "chapters_vector_db"
SUBJECTS = ["Biology", "Physics", "Chemistry", "Mathematics"]
SEARCH_MODE_OPTIONS = {
    "Auto-detect chapter (recommended)": "auto",
    "Selected chapter only": "selected",
    "Search complete subject": "complete",
}


st.set_page_config(
    page_title="Study Here - Class 12 ",
    page_icon="🧬",
    layout="wide",
)


def langchain_history(messages):
    """Convert Streamlit messages into LangChain chat-history tuples."""
    role_names = {
        "user": "human",
        "assistant": "ai",
    }
    return [
        (role_names[message["role"]], message["content"])
        for message in messages
        if message["role"] in role_names
    ]


@st.cache_data
def available_chapters(subject):
    """Return available chapter databases for the selected subject."""
    if subject != "Biology":
        return []

    chapters = [
        path.name
        for path in CHAPTERS_VECTOR_DB_DIR.iterdir()
        if path.is_dir() and path.name.partition(".")[0].isdigit()
    ]
    return sorted(chapters, key=lambda name: int(name.partition(".")[0]))


def show_sources(sources):
    if not sources:
        return

    with st.expander("Textbook sources"):
        for source in sources:
            source_path = source.get("source", "Unknown source")
            source_name = Path(source_path).name
            page = source.get("page")
            page_label = f" - page {page + 1}" if isinstance(page, int) else ""
            st.write(f"{source_name}{page_label}")


def show_video_results(videos):
    """Render compact YouTube reference cards after an answer."""
    if not videos:
        return

    st.markdown("### Video References")
    for position, video in enumerate(videos, start=1):
        title = escape(str(video["title"]))
        channel = video.get("channel")
        details = [detail for detail in (channel, video.get("duration")) if detail]
        details_text = escape(" • ".join(str(detail) for detail in details))
        video_url = escape(str(video["url"]), quote=True)
        details_html = f'<div class="video-meta">{details_text}</div>' if details else ""
        st.markdown(
            f"""
            <div style="
                background: linear-gradient(135deg, #0e7490, #0369a1);
                border-radius: 10px;
                padding: 14px 16px;
                margin: 8px 0;
                color: white;
                box-shadow: 0 2px 8px rgba(0, 0, 0, 0.14);
            ">
                <div style="font-weight: 700; margin-bottom: 5px;">
                    {position}. {title}
                </div>
                {details_html}
                <a href="{video_url}" target="_blank" rel="noopener noreferrer"
                   style="color: white; text-decoration: underline;">
                    Link: {video_url}
                </a>
            </div>
            """,
            unsafe_allow_html=True,
        )


def main():
    st.title("📚 Study Here")

    subject = st.selectbox(
        "Select a Subject from Class 12",
        options=SUBJECTS,
    )
    chapter_options = available_chapters(subject)
    subject_ready = bool(chapter_options)
    selected_chapters = st.multiselect(
        f"Select a Chapter from Class 12 - {subject}",
        options=chapter_options,
        placeholder="Choose one or more chapters",
        disabled=not subject_ready,
    )
    if not subject_ready:
        st.info(
            f"{subject} is available in the subject list, but its PDFs and "
            "vector database have not been added yet."
        )
    elif selected_chapters:
        st.caption(f"Searching: {', '.join(selected_chapters)}")
    else:
        st.caption(f"No chapter selected - searching the complete {subject} textbook.")

    if "messages" not in st.session_state:
        st.session_state.messages = []
    if st.session_state.get("active_subject") != subject:
        st.session_state.messages = []
        st.session_state.active_subject = subject

    with st.sidebar:
        st.header("Study Pal")
        if subject_ready:
            st.write(
                f"Answers are grounded in {len(chapter_options)} {subject} chapter PDFs."
            )
        else:
            st.write(f"{subject} study material has not been added yet.")
        search_mode_label = st.selectbox(
            "If the question is from another chapter",
            options=list(SEARCH_MODE_OPTIONS),
            disabled=not subject_ready,
            help=(
                "Auto-detect switches to the matching chapter. You can also lock "
                "answers to the selected chapter or search the complete subject."
            ),
        )
        search_mode = SEARCH_MODE_OPTIONS[search_mode_label]
        if st.button("Clear conversation", use_container_width=True):
            st.session_state.messages = []
            st.rerun()

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            if message.get("scope_notice"):
                st.info(message["scope_notice"])
            st.markdown(message["content"])
            if message["role"] == "assistant":
                show_sources(message.get("sources", []))
                videos = message.get("videos", [])
                if videos:
                    show_video_results(videos)

    question = st.chat_input(
        f"Ask a {subject} question...",
        disabled=not subject_ready,
    )
    if not question:
        return

    previous_history = langchain_history(st.session_state.messages)
    st.session_state.messages.append(
        {
            "role": "user",
            "content": question,
        }
    )

    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        try:
            if is_video_request(question):
                detected_chapter = detect_chapter_from_question(question)
                if search_mode == "auto" and detected_chapter:
                    search_topic = detected_chapter[1]
                elif search_mode == "complete":
                    search_topic = f"Class 12 {subject} {question}"
                else:
                    search_topic = " ".join(selected_chapters)
                if not search_topic:
                    search_topic = question
                with st.spinner("Finding relevant YouTube lessons..."):
                    videos = search_youtube_videos(search_topic, limit=3)

                if videos:
                    answer = "Here are some relevant video lessons you can watch:"
                else:
                    answer = "I couldn't find a relevant video for that topic."
                st.markdown(answer)
                show_video_results(videos)
                assistant_message = {
                    "role": "assistant",
                    "content": answer,
                    "sources": [],
                    "videos": videos,
                }
            else:
                with st.spinner("Searching the textbook..."):
                    response = get_chatbot_response(
                        question,
                        chat_history=previous_history,
                        selected_chapters=selected_chapters,
                        search_mode=search_mode,
                    )
                if response.get("scope_notice"):
                    st.info(response["scope_notice"])
                st.markdown(response["answer"])
                show_sources(response["sources"])
                videos = []
                if response["sources"]:
                    search_topic = " ".join(response.get("search_scope", []) + [question])
                    try:
                        with st.spinner("Finding related video references..."):
                            videos = search_youtube_videos(search_topic, limit=3)
                    except Exception:
                        st.caption("Video references are temporarily unavailable.")
                show_video_results(videos)
                assistant_message = {
                    "role": "assistant",
                    "content": response["answer"],
                    "sources": response["sources"],
                    "videos": videos,
                    "question": question,
                    "scope_notice": response.get("scope_notice"),
                }
        except Exception as error:
            error_message = f"I could not answer that question: {error}"
            st.error(error_message)
            assistant_message = {
                "role": "assistant",
                "content": error_message,
                "sources": [],
                "question": question,
            }

    st.session_state.messages.append(assistant_message)


if __name__ == "__main__":
    main()
