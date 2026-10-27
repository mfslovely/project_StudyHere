from html import escape
import os
from pathlib import Path
from time import sleep
from uuid import uuid4

import streamlit as st
from streamlit_cookies_controller import CookieController

from auth_utility import (
    AuthError,
    authentication_available,
    issue_tokens,
    register_user,
    revoke_refresh_token,
    rotate_refresh_token,
    user_from_access_token,
    verify_password,
)
from course_utility import (
    create_course,
    get_course,
    get_course_coach_response,
    get_learning_response,
    initialise_database,
    list_courses,
    normalise_user_id,
    regenerate_course_cheatsheets,
    set_item_completed,
    set_project_completed,
)
from chat_history import (
    list_conversations,
    load_messages,
    mongo_history_enabled,
    save_message,
    save_message_videos,
)
from get_yt_video import search_youtube_videos
from biology_utility import available_biology_chapters, get_biology_response


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ACCESS_COOKIE_MAX_AGE = 20 * 60
REFRESH_COOKIE_MAX_AGE = 30 * 24 * 60 * 60


def _secure_cookie():
    """Require HTTPS-only cookies on Render while keeping localhost testable."""
    return bool(os.getenv("RENDER") or os.getenv("RENDER_EXTERNAL_URL"))

st.set_page_config(
    page_title="Learn.ai — Personal AI Learning Paths",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)


def inject_styles():
    st.markdown(
        """
        <style>
        :root {--ink:#262638; --muted:#777786; --line:#e8e7e3; --accent:#6d5dfc;}
        [data-testid="stSidebar"] {min-width:290px; max-width:290px; background:#f7f7f5;
            border-right:1px solid var(--line);}
        [data-testid="stSidebar"] .block-container {padding-top:1.25rem;}
        [data-testid="stSidebar"] button {text-align:left; justify-content:flex-start;
            border-color:transparent; background:transparent;}
        [data-testid="stSidebar"] button:hover {background:#ecebe7;}
        [data-testid="stSidebar"] button[kind="primary"] {background:#e9e6ff;
            color:#3f32b5; border:1px solid #d8d2ff;}
        .block-container {max-width:1180px; padding-top:1.6rem; padding-bottom:2rem;}
        .learn-brand {font-size: 2.35rem; font-weight: 850; letter-spacing: -0.06em;}
        .learn-brand span {color: #6d5dfc;}
        .sidebar-brand {font-size:1.55rem; font-weight:850; letter-spacing:-.05em;
            margin:.1rem 0 1rem; color:var(--ink);}
        .sidebar-brand span {color:var(--accent);}
        .signed-in-user {background:#f3f1ff;border:1px solid #ded8ff;border-radius:12px;
            padding:.75rem .85rem;margin:.35rem 0 .75rem;color:#302b63;}
        .auth-kicker {color:var(--accent);font-weight:800;text-transform:uppercase;
            letter-spacing:.14em;font-size:.72rem;margin-bottom:.45rem;}
        .auth-copy {color:var(--muted);font-size:1.02rem;margin-bottom:1.25rem;}
        .sidebar-section {color:#5f5f69; font-size:.68rem; font-weight:800;
            letter-spacing:.1em; text-transform:uppercase; margin:1.15rem 0 .35rem;}
        .eyebrow {color: #6d5dfc; font-weight: 750; text-transform: uppercase;
                  letter-spacing: .12em; font-size: .72rem;}
        .welcome-wrap {text-align:center; padding:8vh 0 1.25rem;}
        .welcome-wrap h1 {font-size:2.55rem; letter-spacing:-.045em; margin:.4rem 0;}
        .welcome-wrap p {color:var(--muted); font-size:1.05rem;}
        .mode-card {border:1px solid var(--line); border-radius:16px; padding:1rem 1.1rem;
            background:#fff; min-height:122px;}
        .mode-card strong {font-size:1.05rem; color:var(--ink);}
        .mode-card p {color:var(--muted); margin:.4rem 0 0; font-size:.92rem;}
        .workspace-head {border-bottom:1px solid var(--line); margin-bottom:1.15rem;
            padding-bottom:.85rem;}
        .workspace-head h1 {font-size:2rem; letter-spacing:-.035em; margin:.15rem 0;}
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
        .stChatInput {max-width:900px; margin:auto;}
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


def find_related_videos(question, learning_context):
    """Return video lessons tailored to the user's exact question and workspace."""
    try:
        return search_youtube_videos(
            question,
            limit=3,
            context_prefix=f"{learning_context} lesson explanation",
        )
    except Exception:
        return []


def backfill_video_references(
    messages, user_id, conversation_id, learning_context
):
    """Add video references to saved answers created before this feature existed."""
    latest_question = ""
    for index, message in enumerate(messages):
        if message.get("role") == "user":
            latest_question = message.get("content", "")
            continue
        if message.get("role") != "assistant" or "videos" in message:
            continue
        videos = find_related_videos(latest_question, learning_context) if latest_question else []
        message["videos"] = videos
        save_message_videos(user_id, conversation_id, index, videos)


def conversation_title(question, fallback="New learning chat"):
    cleaned = " ".join((question or "").strip().split())
    return cleaned[:54] + ("…" if len(cleaned) > 54 else "") if cleaned else fallback


def _store_authenticated_session(user, tokens, cookies):
    st.session_state.auth_user = user
    st.session_state.access_token = tokens["access_token"]
    cookies.set(
        "access_token",
        tokens["access_token"],
        max_age=ACCESS_COOKIE_MAX_AGE,
        secure=_secure_cookie(),
        same_site="strict",
    )
    cookies.set(
        "refresh_token",
        tokens["refresh_token"],
        max_age=REFRESH_COOKIE_MAX_AGE,
        secure=_secure_cookie(),
        same_site="strict",
    )
    # The cookie component writes in the browser asynchronously. Give its
    # frontend a moment to receive both writes before a form handler reruns.
    sleep(0.5)


def authentication_gate(cookies):
    """Return the authenticated user or render the login/register screen."""
    cached_user = st.session_state.get("auth_user")
    access_token = st.session_state.get("access_token")
    validated_user = user_from_access_token(access_token)
    if cached_user and validated_user and cached_user["id"] == validated_user["id"]:
        return validated_user

    browser_access_token = cookies.get("access_token")
    browser_user = user_from_access_token(browser_access_token)
    if browser_user:
        st.session_state.auth_user = browser_user
        st.session_state.access_token = browser_access_token
        return browser_user

    refresh_token = cookies.get("refresh_token")
    if refresh_token:
        refreshed = rotate_refresh_token(refresh_token)
        if refreshed:
            _store_authenticated_session(
                refreshed["user"],
                {
                    "access_token": refreshed["access_token"],
                    "refresh_token": refreshed["refresh_token"],
                },
                cookies,
            )
            return refreshed["user"]
        if cookies.get("access_token"):
            cookies.remove("access_token")
        if cookies.get("refresh_token"):
            cookies.remove("refresh_token")

    left, centre, right = st.columns([1, 1.15, 1])
    with centre:
        st.markdown('<div class="sidebar-brand">Learn<span>.ai</span></div>', unsafe_allow_html=True)
        st.markdown('<div class="auth-kicker">Private learning workspace</div>', unsafe_allow_html=True)
        st.markdown("## Welcome back")
        st.markdown(
            '<div class="auth-copy">Sign in to access only your chats, courses, and progress.</div>',
            unsafe_allow_html=True,
        )
        login_tab, register_tab = st.tabs(["Log in", "Create account"])
        with login_tab:
            with st.form("login_form"):
                username = st.text_input("Username", key="login_username")
                password = st.text_input("Password", type="password", key="login_password")
                login_submitted = st.form_submit_button(
                    "Log in", type="primary", use_container_width=True
                )
            if login_submitted:
                try:
                    user = verify_password(username, password)
                    _store_authenticated_session(user, issue_tokens(user["id"]), cookies)
                    st.rerun()
                except AuthError as error:
                    st.error(str(error))

        with register_tab:
            with st.form("register_form"):
                new_username = st.text_input(
                    "Choose a username", key="register_username"
                )
                new_password = st.text_input(
                    "Choose a password", type="password", key="register_password"
                )
                confirm_password = st.text_input(
                    "Confirm password", type="password", key="register_confirm"
                )
                register_submitted = st.form_submit_button(
                    "Create account", type="primary", use_container_width=True
                )
            if register_submitted:
                if new_password != confirm_password:
                    st.error("Passwords do not match.")
                else:
                    try:
                        user = register_user(new_username, new_password)
                        _store_authenticated_session(user, issue_tokens(user["id"]), cookies)
                        st.rerun()
                    except AuthError as error:
                        st.error(str(error))
        st.caption("Passwords are bcrypt-hashed. Your browser keeps an opaque refresh token for automatic login.")
    st.stop()


def sign_out(cookies):
    refresh_token = cookies.get("refresh_token")
    revoke_refresh_token(refresh_token)
    if cookies.get("access_token"):
        cookies.remove("access_token")
    if refresh_token:
        cookies.remove("refresh_token")
    for key in list(st.session_state):
        if key != "learn_ai_auth_cookies":
            del st.session_state[key]
    st.rerun()


def general_learning_chat(user_name, grade=None, subject=None):
    is_school = grade is not None and subject is not None
    if is_school:
        learning_context = f"Class {grade} · {subject}"
        conversation_id = f"school-{grade}-{normalise_user_id(subject)}"
        title = learning_context
        kind = f"school:{grade}:{subject}"
        placeholder = f"Ask a Class {grade} {subject} question…"
        st.markdown(
            f'<div class="workspace-head"><div class="eyebrow">School learning</div>'
            f'<h1>Class {grade} · {escape(subject)}</h1></div>',
            unsafe_allow_html=True,
        )
        st.caption("AI tutor mode · Answers are not claimed as textbook extracts.")
    else:
        learning_context = "General learning across any field"
        conversation_id = st.session_state.setdefault(
            "general_conversation_id", f"general-{uuid4().hex}"
        )
        title = st.session_state.get("general_conversation_title", "New learning chat")
        kind = "general"
        placeholder = "Ask anything you want to learn…"

    user_id = normalise_user_id(user_name)
    all_messages = st.session_state.setdefault("learning_messages", {})
    cache_key = f"{user_id}:{conversation_id}"
    if cache_key not in all_messages:
        all_messages[cache_key] = load_messages(user_id, conversation_id)
    messages = all_messages[cache_key]
    backfill_video_references(
        messages, user_id, conversation_id, learning_context
    )

    if not messages and not is_school:
        st.markdown(
            '<div class="welcome-wrap"><div class="eyebrow">Learn anything</div>'
            '<h1>What would you like to understand?</h1>'
            '<p>Ask a question, explore a new field, or turn a topic into a guided course.</p></div>',
            unsafe_allow_html=True,
        )
        cards = st.columns(3)
        card_copy = [
            ("Understand a concept", "Get clear explanations with examples and steps."),
            ("Prepare for an interview", "Revise definitions, questions, and practical answers."),
            ("Build a learning path", "Open My Courses to collect videos, blogs, and a project."),
        ]
        for column, (heading, copy) in zip(cards, card_copy):
            with column:
                st.markdown(
                    f'<div class="mode-card"><strong>{heading}</strong><p>{copy}</p></div>',
                    unsafe_allow_html=True,
                )

    for message in messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            show_video_results(message.get("videos", []))

    question = st.chat_input(placeholder, key=f"learning_chat_{conversation_id}")
    if not question:
        return

    if not is_school and title == "New learning chat":
        title = conversation_title(question)
        st.session_state.general_conversation_title = title
    user_message = {"role": "user", "content": question}
    messages.append(user_message)
    save_message(user_id, conversation_id, title, kind, user_message)
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        try:
            with st.spinner("Learning assistant is thinking…"):
                answer = get_learning_response(
                    question,
                    learning_context=learning_context,
                    chat_history=langchain_history(messages[:-1]),
                )
            st.markdown(answer)
            with st.spinner("Finding related video lessons…"):
                videos = find_related_videos(question, learning_context)
            show_video_results(videos)
            if not videos:
                st.caption("Video references are temporarily unavailable.")
        except Exception as error:
            answer = f"I couldn't answer right now: {error}"
            videos = []
            st.error(answer)
    assistant_message = {"role": "assistant", "content": answer, "videos": videos}
    messages.append(assistant_message)
    save_message(user_id, conversation_id, title, kind, assistant_message)


def course_chat(course, user_name):
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

    user_id = normalise_user_id(user_name)
    conversation_id = f"course-{course['id']}"
    all_messages = st.session_state.setdefault("course_messages", {})
    cache_key = f"{user_id}:{conversation_id}"
    if cache_key not in all_messages:
        all_messages[cache_key] = load_messages(user_id, conversation_id)
    messages = all_messages[cache_key]
    backfill_video_references(
        messages, user_id, conversation_id, course["topic"]
    )
    for message in messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            show_video_results(message.get("videos", []))

    prompt = st.chat_input(
        f"Ask Learn.ai about {course['topic']} or your project…",
        key=f"course_chat_{course['id']}",
    )
    if not prompt:
        return

    user_message = {"role": "user", "content": prompt}
    messages.append(user_message)
    save_message(
        user_id,
        conversation_id,
        course["title"],
        "course",
        user_message,
        course_id=course["id"],
    )
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
            with st.spinner("Finding related video lessons…"):
                videos = find_related_videos(prompt, course["topic"])
            show_video_results(videos)
            if not videos:
                st.caption("Video references are temporarily unavailable.")
        except Exception as error:
            answer = f"I couldn't answer right now: {error}"
            videos = []
            st.error(answer)
    assistant_message = {"role": "assistant", "content": answer, "videos": videos}
    messages.append(assistant_message)
    save_message(
        user_id,
        conversation_id,
        course["title"],
        "course",
        assistant_message,
        course_id=course["id"],
    )


def biology_library(user_name):
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

    user_id = normalise_user_id(user_name)
    conversation_id = "biology-class-12"
    biology_messages = st.session_state.setdefault("biology_messages", {})
    if not isinstance(biology_messages, dict):
        biology_messages = {}
        st.session_state.biology_messages = biology_messages
    cache_key = f"{user_id}:{conversation_id}"
    if cache_key not in biology_messages:
        biology_messages[cache_key] = load_messages(user_id, conversation_id)
    messages = biology_messages[cache_key]
    backfill_video_references(
        messages, user_id, conversation_id, "Class 12 Biology NCERT"
    )
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
    user_message = {"role": "user", "content": question}
    messages.append(user_message)
    save_message(
        user_id,
        conversation_id,
        "Class 12 Biology",
        "biology",
        user_message,
    )
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
            topic_context = " ".join(
                ["Class 12 Biology NCERT"] + response.get("search_scope", [])
            )
            with st.spinner("Finding related video lessons…"):
                videos = find_related_videos(question, topic_context)
            show_video_results(videos)
            if not videos:
                st.caption("Video references are temporarily unavailable.")
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
    save_message(
        user_id,
        conversation_id,
        "Class 12 Biology",
        "biology",
        assistant_message,
    )


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


def course_tracker(user_name, show_saved_chats=True):
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
            st.session_state.workspace_mode = "My Courses"
            st.success("Course created")
            st.rerun()
        except Exception as error:
            st.error(f"Course could not be created: {error}")

    if show_saved_chats:
        st.markdown('<div class="resource-label">Saved chats</div>', unsafe_allow_html=True)
    if show_saved_chats and mongo_history_enabled():
        conversations = list_conversations(normalise_user_id(user_name))
        if conversations:
            for conversation in conversations:
                if st.button(
                    conversation.get("title", "Untitled chat"),
                    key=f"saved_chat_{conversation['conversation_id']}",
                    help=conversation.get("preview") or "Open saved conversation",
                    use_container_width=True,
                ):
                    open_saved_conversation(conversation)
                    st.rerun()
        else:
            st.caption("Your conversations will appear here after the first message.")
    elif show_saved_chats:
        st.caption("Add MONGODB_URI on Render to save chat history permanently.")

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
            st.session_state.workspace_mode = "My Courses"
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


def start_new_chat():
    st.session_state.workspace_mode = "General Learning"
    st.session_state.general_conversation_id = f"general-{uuid4().hex}"
    st.session_state.general_conversation_title = "New learning chat"


def open_saved_conversation(conversation):
    kind = conversation.get("kind", "general")
    if kind == "biology":
        st.session_state.workspace_mode = "School"
        st.session_state.school_grade = "12"
        st.session_state.school_subject = "Biology"
    elif kind.startswith("school:"):
        _, grade, subject = (kind.split(":", 2) + ["", ""])[:3]
        st.session_state.workspace_mode = "School"
        st.session_state.school_grade = grade or "10"
        st.session_state.school_subject = subject or "Mathematics"
    elif kind == "course":
        st.session_state.workspace_mode = "My Courses"
        if conversation.get("course_id") is not None:
            st.session_state.active_course_id = int(conversation["course_id"])
    else:
        st.session_state.workspace_mode = "General Learning"
        st.session_state.general_conversation_id = conversation["conversation_id"]
        st.session_state.general_conversation_title = conversation.get("title", "Learning chat")


def render_sidebar(owner_id):
    if st.button("＋  New learning chat", use_container_width=True, type="primary"):
        start_new_chat()
        st.rerun()

    st.markdown('<div class="sidebar-section">Learn</div>', unsafe_allow_html=True)
    navigation = [
        ("◈  General Learning", "General Learning"),
        ("▣  School Batches", "School"),
        ("◫  My Courses", "My Courses"),
    ]
    for label, destination in navigation:
        if st.button(label, key=f"nav_{destination}", use_container_width=True):
            st.session_state.workspace_mode = destination
            st.rerun()

    st.markdown('<div class="sidebar-section">Saved chats</div>', unsafe_allow_html=True)
    if mongo_history_enabled():
        conversations = list_conversations(normalise_user_id(owner_id), limit=10)
        if conversations:
            for conversation in conversations:
                label = conversation.get("title", "Untitled chat")
                if st.button(
                    f"○  {label}",
                    key=f"sidebar_chat_{conversation['conversation_id']}",
                    help=conversation.get("preview") or "Open conversation",
                    use_container_width=True,
                ):
                    open_saved_conversation(conversation)
                    st.rerun()
        else:
            st.caption("Your chats will appear here.")
    else:
        st.caption("MongoDB is required for saved chats.")

    courses = list_courses(owner_id)
    if courses:
        st.markdown('<div class="sidebar-section">Your courses</div>', unsafe_allow_html=True)
        for course in courses[:8]:
            if st.button(
                f"○  {course['topic']} · {course['progress']}%",
                key=f"sidebar_course_{course['id']}",
                use_container_width=True,
            ):
                st.session_state.active_course_id = course["id"]
                st.session_state.workspace_mode = "My Courses"
                st.rerun()


def school_workspace(user_name):
    st.markdown(
        '<div class="workspace-head"><div class="eyebrow">School batches</div>'
        '<h1>Choose your classroom</h1></div>',
        unsafe_allow_html=True,
    )
    grade = st.segmented_control(
        "Class",
        ["10", "12"],
        key="school_grade",
        format_func=lambda value: f"Class {value}",
    ) or "10"
    subjects = ["Mathematics", "Physics", "Chemistry", "Biology"]
    subject = st.segmented_control(
        "Subject",
        subjects,
        key="school_subject",
    ) or "Mathematics"
    st.divider()
    if grade == "12" and subject == "Biology":
        biology_library(user_name)
    else:
        general_learning_chat(user_name, grade=grade, subject=subject)


def courses_workspace(user_name):
    st.markdown(
        '<div class="workspace-head"><div class="eyebrow">Personal learning paths</div>'
        '<h1>My courses</h1></div>',
        unsafe_allow_html=True,
    )
    tracker_tab, coach_tab = st.tabs(["Progress & project", "AI course coach"])
    with tracker_tab:
        active_course = course_tracker(user_name, show_saved_chats=False)
    with coach_tab:
        active_id = st.session_state.get("active_course_id")
        course = get_course(active_id, user_name) if active_id else active_course
        course_chat(course, user_name)


def main():
    inject_styles()
    initialise_database()
    if not authentication_available():
        st.error("Authentication is unavailable because MongoDB is not configured.")
        st.stop()
    cookies = CookieController(key="learn_ai_auth_cookies")
    authenticated_user = authentication_gate(cookies)
    owner_id = authenticated_user["id"]
    username = authenticated_user["username"]

    st.session_state.setdefault("workspace_mode", "General Learning")
    st.session_state.setdefault("school_grade", "10")
    st.session_state.setdefault("school_subject", "Mathematics")

    with st.sidebar:
        st.markdown('<div class="sidebar-brand">Learn<span>.ai</span></div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="signed-in-user"><small>Signed in as</small><br>'
            f'<strong>@{escape(username)}</strong></div>',
            unsafe_allow_html=True,
        )
        if st.button("Sign out", use_container_width=True):
            sign_out(cookies)
        render_sidebar(owner_id)

    mode = st.session_state.workspace_mode
    if mode == "School":
        school_workspace(owner_id)
    elif mode == "My Courses":
        courses_workspace(owner_id)
    else:
        general_learning_chat(owner_id)


if __name__ == "__main__":
    main()
