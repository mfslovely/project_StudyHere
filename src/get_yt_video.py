import re

from youtubesearchpython import VideosSearch


VIDEO_REQUEST_PATTERN = re.compile(
    r"\b(?:youtube|you\s*tube|video|videos|watch)\b",
    re.IGNORECASE,
)


def is_video_request(query):
    """Return whether a message is asking for video recommendations."""
    return bool(query and VIDEO_REQUEST_PATTERN.search(query))


def search_youtube_videos(query, limit=3, context_prefix="Educational tutorial"):
    """Return a small list of YouTube videos related to a study question."""
    if not query or not query.strip():
        return []
    if limit < 1:
        return []

    search_query = " ".join(part for part in (context_prefix, query.strip()) if part)
    search = VideosSearch(search_query, limit=min(limit, 10))
    results = search.result().get("result", [])

    videos = []
    for result in results:
        video_id = result.get("id")
        if not video_id:
            continue

        thumbnails = result.get("thumbnails") or []
        videos.append(
            {
                "title": result.get("title", "Video lesson"),
                "url": result.get("link") or f"https://www.youtube.com/watch?v={video_id}",
                "duration": result.get("duration"),
                "channel": (result.get("channel") or {}).get("name"),
                "thumbnail": thumbnails[0].get("url") if thumbnails else None,
            }
        )

    return videos


def get_yt_video(query):
    """Compatibility helper that returns the first matching video URL."""
    videos = search_youtube_videos(query, limit=1)
    return videos[0]["url"] if videos else None
