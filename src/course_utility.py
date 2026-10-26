import json
import os
import re
import threading
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import httpx
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    select,
    update,
)

from get_yt_video import search_youtube_videos


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "runtime_data" / "learn_ai.db"
DB_PATH = Path(os.getenv("LEARN_AI_DB_PATH", DEFAULT_DB_PATH))
DATABASE_URL = os.getenv("DATABASE_URL") or f"sqlite:///{DB_PATH.as_posix()}"
DEFAULT_CHAT_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
DB_LOCK = threading.Lock()

load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env")

metadata = MetaData()
courses_table = Table(
    "courses",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", String(160), nullable=False),
    Column("topic", String(300), nullable=False),
    Column("title", String(500), nullable=False),
    Column("summary", Text, nullable=False),
    Column("project_title", String(500), nullable=False),
    Column("project_brief", Text, nullable=False),
    Column("project_deliverables", Text, nullable=False),
    Column("project_completed", Boolean, nullable=False, default=False),
    Column("created_at", String(80), nullable=False),
)
course_items_table = Table(
    "course_items",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("course_id", Integer, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
    Column("item_type", String(40), nullable=False),
    Column("title", String(700), nullable=False),
    Column("url", Text),
    Column("content", Text),
    Column("metadata", Text),
    Column("position", Integer, nullable=False),
    Column("completed", Boolean, nullable=False, default=False),
)
Index("idx_courses_user", courses_table.c.user_id, courses_table.c.created_at)
Index("idx_items_course", course_items_table.c.course_id, course_items_table.c.position)


def _engine():
    options = {"pool_pre_ping": True}
    if DATABASE_URL.startswith("sqlite"):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        options["connect_args"] = {"check_same_thread": False, "timeout": 20}
    return create_engine(DATABASE_URL, **options)


ENGINE = _engine()


class DuckDuckGoResultParser(HTMLParser):
    """Collect links from DuckDuckGo's lightweight HTML search page."""

    def __init__(self):
        super().__init__()
        self.results = []
        self._active_href = None
        self._active_text = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "a" and "result__a" in attributes.get("class", ""):
            self._active_href = attributes.get("href")
            self._active_text = []

    def handle_data(self, data):
        if self._active_href:
            self._active_text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._active_href:
            title = " ".join("".join(self._active_text).split())
            if title:
                self.results.append((title, self._active_href))
            self._active_href = None
            self._active_text = []


def initialise_database():
    """Create the durable course and progress tables."""
    with DB_LOCK:
        metadata.create_all(ENGINE)


def normalise_user_id(name):
    value = re.sub(r"[^a-z0-9]+", "-", (name or "learner").strip().lower())
    return value.strip("-") or "learner"


def _extract_json(text):
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("The course plan was not valid JSON.")
    return json.loads(cleaned[start : end + 1])


def _fallback_plan(topic):
    clean_topic = topic.strip().title()
    return {
        "title": f"{clean_topic}: Guided Learning Path",
        "summary": (
            f"A practical learning path that moves from the foundations of {clean_topic} "
            "to confident, hands-on use."
        ),
        "cheatsheets": [
            {
                "title": f"{clean_topic} core concepts",
                "content": (
                    f"- Define the purpose of **{clean_topic}**\n"
                    "- Learn the essential vocabulary\n"
                    "- Connect each concept to one real example\n"
                    "- Practise explaining the topic in your own words"
                ),
            },
            {
                "title": f"{clean_topic} workflow",
                "content": (
                    "1. Understand the problem\n2. Choose the correct tool or concept\n"
                    "3. Build a small example\n4. Test the result\n5. Review mistakes"
                ),
            },
            {
                "title": "Revision checklist",
                "content": (
                    "- Can I explain the topic without notes?\n"
                    "- Can I complete a small task independently?\n"
                    "- Can I identify and fix common mistakes?\n"
                    "- Can I apply it to a new situation?"
                ),
            },
        ],
        "project": {
            "title": f"Build a practical {clean_topic} project",
            "brief": (
                f"Create a small end-to-end project that demonstrates the most important "
                f"ideas from {clean_topic}. Document what you built, the decisions you made, "
                "and how you tested it."
            ),
            "deliverables": [
                "A working final result",
                "A short README or explanation",
                "Evidence of testing",
                "A reflection describing what you learned",
            ],
        },
    }


def generate_ai_plan(topic):
    """Generate cheat sheets and a capstone project for a topic."""
    if not os.getenv("GROQ_API_KEY"):
        return _fallback_plan(topic)

    prompt = f"""Create a concise beginner-to-practical learning path for: {topic}

Return ONLY valid JSON with this exact structure:
{{
  "title": "course title",
  "summary": "2 sentence outcome-focused summary",
  "cheatsheets": [
    {{"title": "name", "content": "markdown with 5-8 key bullets or commands"}},
    {{"title": "name", "content": "markdown with 5-8 key bullets or commands"}},
    {{"title": "name", "content": "markdown with 5-8 key bullets or commands"}}
  ],
  "project": {{
    "title": "specific portfolio project",
    "brief": "clear project scenario and success criteria",
    "deliverables": ["deliverable 1", "deliverable 2", "deliverable 3", "deliverable 4"]
  }}
}}

Make the cheatsheets useful for the topic. For technical topics include commands or syntax;
for academic topics include formulas, definitions, processes, or recall cues. The project must
be achievable after completing this learning path.
"""
    try:
        model = ChatGroq(model=DEFAULT_CHAT_MODEL, temperature=0.2)
        response = model.invoke(prompt)
        plan = _extract_json(str(response.content))
        if len(plan.get("cheatsheets", [])) < 2 or not plan.get("project"):
            raise ValueError("Incomplete course plan")
        return plan
    except Exception:
        return _fallback_plan(topic)


def _clean_result_url(url):
    if url.startswith("//"):
        url = f"https:{url}"
    parsed = urlparse(url)
    if "duckduckgo.com" in parsed.netloc:
        target = parse_qs(parsed.query).get("uddg", [None])[0]
        if target:
            return unquote(target)
    return url


def search_learning_articles(topic, limit=3):
    """Find real tutorial/blog links; fall back to focused search links."""
    query = f"{topic} beginner tutorial guide blog"
    try:
        response = httpx.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0 Learn.ai course builder"},
            timeout=12,
            follow_redirects=True,
        )
        response.raise_for_status()
        parser = DuckDuckGoResultParser()
        parser.feed(response.text)
        articles = []
        for title, raw_url in parser.results:
            url = _clean_result_url(raw_url)
            hostname = urlparse(url).netloc.lower().removeprefix("www.")
            if not url.startswith("http") or "youtube.com" in hostname:
                continue
            articles.append({"title": title, "url": url, "source": hostname})
            if len(articles) >= limit:
                break
        if articles:
            return articles
    except (httpx.HTTPError, ValueError):
        pass

    encoded = quote_plus(query)
    return [
        {
            "title": f"Beginner guide to {topic}",
            "url": f"https://www.google.com/search?q={encoded}",
            "source": "Web search",
        },
        {
            "title": f"Practical {topic} tutorials",
            "url": f"https://dev.to/search?q={quote_plus(topic)}",
            "source": "DEV Community",
        },
        {
            "title": f"{topic} reference articles",
            "url": f"https://medium.com/search?q={quote_plus(topic)}",
            "source": "Medium",
        },
    ][:limit]


def create_course(user_name, topic):
    """Create and persist a complete resource-backed course."""
    initialise_database()
    topic = " ".join(topic.split())
    if len(topic) < 2:
        raise ValueError("Enter a topic with at least two characters.")

    plan = generate_ai_plan(topic)
    try:
        videos = search_youtube_videos(
            f"{topic} beginner course tutorial",
            limit=4,
            context_prefix="",
        )
    except Exception:
        videos = []
    articles = search_learning_articles(topic, limit=3)
    project = plan.get("project") or _fallback_plan(topic)["project"]
    deliverables = project.get("deliverables") or []
    now = datetime.now(timezone.utc).isoformat()

    with DB_LOCK, ENGINE.begin() as connection:
        cursor = connection.execute(
            courses_table.insert().values(
                user_id=normalise_user_id(user_name),
                topic=topic,
                title=plan.get("title") or topic.title(),
                summary=plan.get("summary") or _fallback_plan(topic)["summary"],
                project_title=project.get("title") or f"{topic.title()} capstone",
                project_brief=(
                    project.get("brief")
                    or "Apply what you learned in a practical project."
                ),
                project_deliverables=json.dumps(deliverables),
                created_at=now,
                project_completed=False,
            )
        )
        course_id = cursor.inserted_primary_key[0]
        position = 0
        for video in videos:
            position += 1
            connection.execute(
                course_items_table.insert().values(
                    course_id=course_id,
                    item_type="video",
                    title=video.get("title", "Video lesson"),
                    url=video.get("url"),
                    metadata=json.dumps(
                        {
                            "channel": video.get("channel"),
                            "duration": video.get("duration"),
                        }
                    ),
                    position=position,
                    completed=False,
                )
            )
        for article in articles:
            position += 1
            connection.execute(
                course_items_table.insert().values(
                    course_id=course_id,
                    item_type="article",
                    title=article["title"],
                    url=article["url"],
                    metadata=json.dumps({"source": article.get("source")}),
                    position=position,
                    completed=False,
                )
            )
        for sheet in plan.get("cheatsheets", [])[:3]:
            position += 1
            connection.execute(
                course_items_table.insert().values(
                    course_id=course_id,
                    item_type="cheatsheet",
                    title=sheet.get("title", "Quick reference"),
                    content=sheet.get("content", "Review the core concepts."),
                    position=position,
                    completed=False,
                )
            )
    return get_course(course_id, user_name)


def _course_from_row(row, items):
    row = dict(row)
    items = [dict(item) for item in items]
    resource_count = len(items)
    completed_count = sum(bool(item["completed"]) for item in items)
    progress = round((completed_count / resource_count) * 100) if resource_count else 0
    return {
        **row,
        "items": items,
        "progress": progress,
        "completed_count": completed_count,
        "resource_count": resource_count,
        "project_deliverables": json.loads(row["project_deliverables"] or "[]"),
    }


def list_courses(user_name):
    initialise_database()
    with ENGINE.connect() as connection:
        rows = connection.execute(
            select(courses_table)
            .where(courses_table.c.user_id == normalise_user_id(user_name))
            .order_by(courses_table.c.created_at.desc())
        ).mappings().all()
        courses = []
        for row in rows:
            items = connection.execute(
                select(course_items_table)
                .where(course_items_table.c.course_id == row["id"])
                .order_by(course_items_table.c.position)
            ).mappings().all()
            courses.append(_course_from_row(row, items))
        return courses


def get_course(course_id, user_name):
    initialise_database()
    with ENGINE.connect() as connection:
        row = connection.execute(
            select(courses_table).where(
                courses_table.c.id == int(course_id),
                courses_table.c.user_id == normalise_user_id(user_name),
            )
        ).mappings().first()
        if not row:
            return None
        items = connection.execute(
            select(course_items_table)
            .where(course_items_table.c.course_id == row["id"])
            .order_by(course_items_table.c.position)
        ).mappings().all()
        return _course_from_row(row, items)


def set_item_completed(user_name, course_id, item_id, completed):
    owned_course = select(courses_table.c.id).where(
        courses_table.c.id == int(course_id),
        courses_table.c.user_id == normalise_user_id(user_name),
    )
    with DB_LOCK, ENGINE.begin() as connection:
        connection.execute(
            update(course_items_table)
            .where(
                course_items_table.c.id == int(item_id),
                course_items_table.c.course_id.in_(owned_course),
            )
            .values(completed=bool(completed))
        )


def set_project_completed(user_name, course_id, completed):
    with DB_LOCK, ENGINE.begin() as connection:
        connection.execute(
            update(courses_table)
            .where(
                courses_table.c.id == int(course_id),
                courses_table.c.user_id == normalise_user_id(user_name),
            )
            .values(project_completed=bool(completed))
        )


def get_course_coach_response(course, question, chat_history=None):
    """Answer as a tutor and project mentor for the active course."""
    if not os.getenv("GROQ_API_KEY"):
        raise RuntimeError("GROQ_API_KEY is missing from the Render or local environment.")

    completed_titles = [item["title"] for item in course["items"] if item["completed"]]
    system_context = f"""You are Learn.ai, a patient tutor and project mentor.
Active course: {course['title']}
Topic: {course['topic']}
Course outcome: {course['summary']}
Learning progress: {course['progress']}%
Completed resources: {', '.join(completed_titles) or 'None yet'}
Capstone project: {course['project_title']}
Project brief: {course['project_brief']}
Deliverables: {', '.join(course['project_deliverables'])}

Answer clearly and practically. Adapt the detail to the learner's progress. If the learner asks
about the project, give incremental guidance, debugging help, examples, and a next step without
pretending that unfinished work is complete.
"""
    history_text = "\n".join(
        f"{role}: {content}" for role, content in (chat_history or [])[-8:]
    )
    prompt = f"{system_context}\nRecent conversation:\n{history_text}\nStudent: {question}"
    model = ChatGroq(model=DEFAULT_CHAT_MODEL, temperature=0.25)
    return str(model.invoke(prompt).content)

