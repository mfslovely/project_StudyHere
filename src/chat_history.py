"""Persistent MongoDB chat history keyed by learner ID and conversation ID."""

import os
from datetime import datetime, timezone
from functools import lru_cache

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import PyMongoError


def _now():
    return datetime.now(timezone.utc)


@lru_cache(maxsize=1)
def _collection():
    uri = os.getenv("MONGODB_URI", "").strip()
    if not uri:
        return None
    client = MongoClient(uri, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000)
    database = client[os.getenv("MONGODB_DB_NAME", "learn_ai")]
    collection = database["chat_history"]
    collection.create_index(
        [("user_id", ASCENDING), ("conversation_id", ASCENDING)], unique=True
    )
    collection.create_index([("user_id", ASCENDING), ("updated_at", DESCENDING)])
    return collection


def mongo_history_enabled():
    return bool(os.getenv("MONGODB_URI", "").strip())


def load_messages(user_id, conversation_id):
    collection = _collection()
    if collection is None:
        return []
    try:
        document = collection.find_one(
            {"user_id": user_id, "conversation_id": conversation_id},
            {"_id": 0, "messages": 1},
        )
        return list((document or {}).get("messages", []))
    except PyMongoError:
        return []


def save_message(
    user_id,
    conversation_id,
    title,
    kind,
    message,
    course_id=None,
):
    collection = _collection()
    if collection is None:
        return False
    stored_message = dict(message)
    stored_message["created_at"] = _now()
    try:
        collection.update_one(
            {"user_id": user_id, "conversation_id": conversation_id},
            {
                "$setOnInsert": {"created_at": _now()},
                "$set": {
                    "title": title,
                    "kind": kind,
                    "course_id": course_id,
                    "updated_at": _now(),
                },
                "$push": {"messages": stored_message},
            },
            upsert=True,
        )
        return True
    except PyMongoError:
        return False


def list_conversations(user_id, limit=12):
    collection = _collection()
    if collection is None:
        return []
    try:
        cursor = collection.find(
            {"user_id": user_id},
            {
                "_id": 0,
                "conversation_id": 1,
                "title": 1,
                "kind": 1,
                "course_id": 1,
                "updated_at": 1,
                "messages": {"$slice": -1},
            },
        ).sort("updated_at", DESCENDING).limit(limit)
        conversations = []
        for item in cursor:
            last_message = (item.pop("messages", []) or [{}])[-1]
            item["preview"] = str(last_message.get("content", ""))[:80]
            conversations.append(item)
        return conversations
    except PyMongoError:
        return []
