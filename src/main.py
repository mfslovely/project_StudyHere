from html import escape
from pathlib import Path

import streamlit as st

from course_utility import (
    create_course,
    get_course,
    get_course_coach_response,
    initialise_database,
    list_courses,
    regenerate_course_cheatsheets,
    set_item_completed,
    set_project_completed,
)
from get_yt_video import search_youtube_videos
from biology_utility import available_biology_chapters, get_biology_response


PROJECT_ROOT = Path(__file__).resolve().parent.parent

st.set_page_config(
    page_title="Learn.ai — Personal AI Learning Paths",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="collapsed",
)


def inject_styles():
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"], [data-testid="collapsedControl"] {display: none;}
        .block-container {max-width: 1500px; padding-top: 1.4rem; padding-bottom: 2rem;}
        .learn-brand {font-size: 2.35rem; font-weight: 850; letter-spacing: -0.06em;}
        .learn-brand span {color: #6d5dfc;}
        .eyebrow {color: #6d5dfc; font-weight: 750; text-transform: uppercase;
                  letter-spacing: .12em; font-size: .72rem;}
        .course-hero {background: linear-gradient(135deg,#16172a,#28255d); color: white;
                      padding: 1.35rem 1.5rem; border-radius: 18px; margin: .35rem 0 1rem;}
        .course-hero h2 {margin: .25rem 0 .45rem; color: white;}
        .course-hero p {color: #d6d5e7; margin: 0;}
        .resource-label {font-size:.72rem; font-weight:800; letter-spacing:.08em;
                         color:#77738d; text-transform:uppercase; margin-top:.65rem;}
        .project-card {border:1px solid #d9d3ff; background:#f8f6ff; border-radius:14px;
                       padding:1rem 1.1rem; margin-top:.8rem;}
        .video-card {background:linear-gradient(135deg,#5145cd,#7667ff); border-radius:12px;
                     padding:13px 15px; margin:8px 0; color:white;}
        .video-card a {color:white !important; text-decoration:underline;}
        div[data-testid="stVerticalBlockBorderWrapper"] {border-radius: 16px;}
        </style>
        """,
        unsafe_allow_html=True,
    )


@st.cache_data
def available_chapters():
    return available_biology_chapters()


def langchain_history(messages):
    role_names = {"user": "human", "assistant": "ai"}
    return [
        (role_names[item["role"]], item["content"])
        for item in messages
        if item.get("role") in role_names
    ]


def show_sources(sources):
    if not sources:
        return
    with st.expander("Textbook sources"):
        for source in sources:
            source_name = Path(source.get("source", "Unknown source")).name
            page = source.get("page")
            page_label = f" · page {page + 1}" if isinstance(page, int) else ""
            st.write(f"{source_name}{page_label}")


def show_video_results(videos):
    if not videos:
        return
    st.markdown("#### Video references")
    for position, video in enumerate(videos, start=1):
        title = escape(str(video.get("title", "Video lesson")))
        url = escape(str(video.get("url", "")), quote=True)
        details = " • ".join(
            str(value) for value in (video.get("channel"), video.get("duration")) if value
        )
        st.markdown(
            f'<div class="video-card"><strong>{position}. {title}</strong><br>'
            f'<small>{escape(details)}</small><br><a href="{url}" target="_blank">Open lesson ↗</a></div>',
            unsafe_allow_html=True,
        )


def course_chat(course):
    st.markdown('<div class="eyebrow">AI course coach</div>', unsafe_allow_html=True)
    if not course:
        st.markdown("## Build your own course")
        st.write(
            "Enter any topic in the Course Tracker. Learn.ai will collect lessons, "
            "readings and cheat sheets, track your progress, and unlock a project."
        )
        st.info("Try: Docker, Python APIs, Genetics, Financial Modelling, or UI Design")
        return

    st.markdown(
        f"""
        <div class="course-hero">
          <div style="opacity:.75;font-size:.8rem;font-weight:700">ACTIVE COURSE · {course['progress']}% LEARNED</div>
          <h2>{escape(course['title'])}</h2>
          <p>{escape(course['summary'])}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.progress(course["progress"] / 100, text=f"You've learned {course['progress']}% of this topic")

    if course["progress"] == 100:
        st.success(f"Learning path complete — your project “{course['project_title']}” is unlocked.")
    else:
        next_items = [item for item in course["items"] if not item["completed"]]
        if next_items:
            st.caption(f"Recommended next: {next_items[0]['title']}")

    all_messages = st.session_state.setdefault("course_messages", {})
    messages = all_messages.setdefault(str(course["id"]), [])
    for message in messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    prompt = st.chat_input(
        f"Ask Learn.ai about {course['topic']} or your project…",
        key=f"course_chat_{course['id']}",
    )
    if not prompt:
        return

    messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        try:
            with st.spinner("Your course coach is thinking…"):
                answer = get_course_coach_response(
                    course,
                    prompt,
                    chat_history=langchain_history(messages[:-1]),
                )
            st.markdown(answer)
        except Exception as error:
            answer = f"I couldn't answer right now: {error}"
            st.error(answer)
    messages.append({"role": "assistant", "content": answer})


def biology_library():
    st.markdown('<div class="eyebrow">Class 12 textbook library</div>', unsafe_allow_html=True)
    st.markdown("## Biology study assistant")
    chapters = available_chapters()
    if not chapters:
        st.warning("Biology chapter indexes are not available on this deployment yet.")
        return

    selected = st.multiselect(
        "Choose one or more chapters",
        chapters,
        placeholder="No selection searches the complete Biology textbook",
    )
    mode_label = st.radio(
        "Search behaviour",
        ["Auto-detect chapter", "Selected chapter only", "Complete textbook"],
        horizontal=True,
    )
    mode = {
        "Auto-detect chapter": "auto",
        "Selected chapter only": "selected",
        "Complete textbook": "complete",
    }[mode_label]

    messages = st.session_state.setdefault("biology_messages", [])
    for message in messages:
        with st.chat_message(message["role"]):
            if message.get("scope_notice"):
                st.info(message["scope_notice"])
            st.markdown(message["content"])
            show_sources(message.get("sources", []))
            show_video_results(message.get("videos", []))

    question = st.chat_input("Ask a Class 12 Biology question…", key="biology_chat")
    if not question:
        return
    history = langchain_history(messages)
    messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        try:
            with st.spinner("Searching the textbook…"):
                response = get_biology_response(
                    question,
                    chat_history=history,
                    selected_chapters=selected,
                    search_mode=mode,
                )
            if response.get("scope_notice"):
                st.info(response["scope_notice"])
            st.markdown(response["answer"])
            show_sources(response["sources"])
            videos = []
            if response["sources"]:
                try:
                    topic = " ".join(response.get("search_scope", []) + [question])
                    videos = search_youtube_videos(topic, limit=3)
                except Exception:
                    st.caption("Video references are temporarily unavailable.")
            show_video_results(videos)
            assistant_message = {
                "role": "assistant",
                "content": response["answer"],
                "sources": response["sources"],
                "videos": videos,
                "scope_notice": response.get("scope_notice"),
            }
        except Exception as error:
            assistant_message = {
                "role": "assistant",
                "content": f"I couldn't answer that question: {error}",
            }
            st.error(assistant_message["content"])
    messages.append(assistant_message)


def render_course_items(course, user_name):
    groups = [
        ("video", "Video lessons", "▶"),
        ("article", "Blogs & guides", "↗"),
        ("cheatsheet", "Cheat sheets", "⌘"),
    ]
    for item_type, heading, icon in groups:
        items = [item for item in course["items"] if item["item_type"] == item_type]
        if not items:
            continue
        st.markdown(f'<div class="resource-label">{icon} {heading}</div>', unsafe_allow_html=True)
        for item in items:
            checked = st.checkbox(
                item["title"],
                value=bool(item["completed"]),
                key=f"course_{course['id']}_item_{item['id']}",
            )
            if checked != bool(item["completed"]):
                set_item_completed(user_name, course["id"], item["id"], checked)
                st.rerun()
            if item.get("url"):
                st.markdown(f"[Open resource ↗]({item['url']})")
            if item_type == "cheatsheet" and item.get("content"):
                with st.expander("View quick reference"):
                    st.markdown(item["content"])


def course_tracker(user_name):
    st.markdown("### Course Tracker")
    st.caption("Your personal learning paths")

    with st.form("create_course_form", clear_on_submit=True):
        topic = st.text_input("Create a course", placeholder="e.g. Docker for beginners")
        create_clicked = st.form_submit_button("✦ Build with AI", use_container_width=True)
    if create_clicked:
        try:
            with st.spinner("Building your course with videos, blogs and cheat sheets…"):
                created = create_course(user_name, topic)
            st.session_state.active_course_id = created["id"]
            st.session_state.workspace_mode = "AI Course Coach"
            st.success("Course created")
            st.rerun()
        except Exception as error:
            st.error(f"Course could not be created: {error}")

    courses = list_courses(user_name)
    if not courses:
        st.info("No courses yet. Create your first learning path above.")
        return None

    active_id = st.session_state.get("active_course_id")
    if active_id not in [course["id"] for course in courses]:
        active_id = courses[0]["id"]
        st.session_state.active_course_id = active_id

    st.markdown('<div class="resource-label">Your courses</div>', unsafe_allow_html=True)
    for course in courses:
        prefix = "●" if course["id"] == active_id else "○"
        if st.button(
            f"{prefix} {course['topic']}  ·  {course['progress']}%",
            key=f"select_course_{course['id']}",
            use_container_width=True,
        ):
            st.session_state.active_course_id = course["id"]
            st.session_state.workspace_mode = "AI Course Coach"
            st.rerun()

    course = get_course(active_id, user_name)
    if not course:
        return None
    st.divider()
    st.markdown(f"#### {course['title']}")
    st.progress(course["progress"] / 100, text=f"{course['progress']}% learned")
    st.caption(f"{course['completed_count']} of {course['resource_count']} resources completed")
    render_course_items(course, user_name)
    if st.button(
        "↻ Regenerate interview cheat sheets",
        key=f"regenerate_cheatsheets_{course['id']}",
        use_container_width=True,
        help="Replace old notes with short interview questions and answers.",
    ):
        try:
            with st.spinner("Creating interview quick-revision sheets…"):
                regenerate_course_cheatsheets(user_name, course["id"])
            st.success("Interview cheat sheets updated")
            st.rerun()
        except Exception as error:
            st.error(f"Cheat sheets could not be updated: {error}")

    st.markdown('<div class="resource-label">Capstone project</div>', unsafe_allow_html=True)
    if course["progress"] < 100:
        st.caption("🔒 Complete the learning resources to unlock your project.")
    else:
        st.markdown(
            f'<div class="project-card"><strong>{escape(course["project_title"])}</strong><br>'
            f'{escape(course["project_brief"])}</div>',
            unsafe_allow_html=True,
        )
        for deliverable in course["project_deliverables"]:
            st.markdown(f"- {deliverable}")
        project_done = st.checkbox(
            "I completed this project",
            value=bool(course["project_completed"]),
            key=f"project_complete_{course['id']}",
        )
        if project_done != bool(course["project_completed"]):
            set_project_completed(user_name, course["id"], project_done)
            st.rerun()
        st.caption("Ask the AI coach in chat for planning, code, debugging, or feedback.")
    return course


def main():
    inject_styles()
    initialise_database()

    header_left, header_right = st.columns([3, 1])
    with header_left:
        st.markdown('<div class="learn-brand">Learn<span>.ai</span></div>', unsafe_allow_html=True)
        st.caption("Turn any topic into a guided course, measurable progress, and a real project.")
    with header_right:
        user_name = st.text_input(
            "Learner profile",
            value=st.session_state.get("learner_name", "Learner"),
            placeholder="Your name",
        )
        st.session_state.learner_name = user_name or "Learner"

    st.divider()
    content_column, tracker_column = st.columns([2.15, 1], gap="large")

    with tracker_column:
        with st.container(height=760, border=True):
            active_course = course_tracker(st.session_state.learner_name)

    with content_column:
        if "workspace_mode" not in st.session_state:
            st.session_state.workspace_mode = "AI Course Coach"
        mode = st.segmented_control(
            "Workspace",
            ["AI Course Coach", "Biology Textbook"],
            key="workspace_mode",
            label_visibility="collapsed",
        )
        if mode == "Biology Textbook":
            biology_library()
        else:
            active_id = st.session_state.get("active_course_id")
            course = (
                get_course(active_id, st.session_state.learner_name)
                if active_id
                else active_course
            )
            course_chat(course)


if __name__ == "__main__":
    main()
