"""MongoDB-backed authentication with bcrypt passwords and rotating tokens."""

import hashlib
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import bcrypt
from bson import ObjectId
from pymongo import ASCENDING, MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError


ACCESS_TOKEN_TTL = timedelta(minutes=20)
REFRESH_TOKEN_TTL = timedelta(days=30)
USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,39}$")


class AuthError(Exception):
    """A safe, user-facing authentication error."""


def _now():
    return datetime.now(timezone.utc)


def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalise_username(username):
    return (username or "").strip().lower()


@lru_cache(maxsize=1)
def _database():
    uri = os.getenv("MONGODB_URI", "").strip()
    if not uri:
        raise AuthError("MongoDB is required for account login.")
    client = MongoClient(uri, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000)
    database = client[os.getenv("MONGODB_DB_NAME", "learn_ai")]
    database["users"].create_index([("username", ASCENDING)], unique=True)
    database["auth_sessions"].create_index(
        [("access_token_hash", ASCENDING)], unique=True
    )
    database["auth_sessions"].create_index(
        [("refresh_token_hash", ASCENDING)], unique=True
    )
    database["auth_sessions"].create_index(
        [("refresh_expires_at", ASCENDING)], expireAfterSeconds=0
    )
    return database


def authentication_available():
    return bool(os.getenv("MONGODB_URI", "").strip())


def _public_user(document):
    if not document:
        return None
    return {
        "id": str(document["_id"]),
        "username": document["username"],
        "display_name": document.get("display_name") or document["username"],
    }


def _validate_credentials(username, password):
    username = normalise_username(username)
    if not USERNAME_PATTERN.fullmatch(username):
        raise AuthError(
            "Username must be 3–40 characters using letters, numbers, dot, dash, or underscore."
        )
    password_bytes = (password or "").encode("utf-8")
    if len(password_bytes) < 8:
        raise AuthError("Password must contain at least 8 characters.")
    if len(password_bytes) > 72:
        raise AuthError("Password must be 72 bytes or fewer.")
    return username, password_bytes


def register_user(username, password):
    username, password_bytes = _validate_credentials(username, password)
    document = {
        "username": username,
        "display_name": username,
        "password_hash": bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode("utf-8"),
        "created_at": _now(),
        "updated_at": _now(),
    }
    try:
        result = _database()["users"].insert_one(document)
    except DuplicateKeyError as error:
        raise AuthError("That username is already registered.") from error
    except PyMongoError as error:
        raise AuthError("Account could not be created right now.") from error
    document["_id"] = result.inserted_id
    return _public_user(document)


def verify_password(username, password):
    username = normalise_username(username)
    password_bytes = (password or "").encode("utf-8")
    if not username or not password_bytes or len(password_bytes) > 72:
        raise AuthError("Invalid username or password.")
    try:
        document = _database()["users"].find_one({"username": username})
        if not document or not bcrypt.checkpw(
            password_bytes, document["password_hash"].encode("utf-8")
        ):
            raise AuthError("Invalid username or password.")
        return _public_user(document)
    except AuthError:
        raise
    except (ValueError, PyMongoError) as error:
        raise AuthError("Invalid username or password.") from error


def issue_tokens(user_id):
    now = _now()
    access_token = secrets.token_urlsafe(32)
    refresh_token = secrets.token_urlsafe(48)
    session = {
        "user_id": ObjectId(user_id),
        "access_token_hash": _token_hash(access_token),
        "refresh_token_hash": _token_hash(refresh_token),
        "access_expires_at": now + ACCESS_TOKEN_TTL,
        "refresh_expires_at": now + REFRESH_TOKEN_TTL,
        "revoked": False,
        "created_at": now,
        "updated_at": now,
    }
    try:
        _database()["auth_sessions"].insert_one(session)
    except (ValueError, PyMongoError) as error:
        raise AuthError("Secure session could not be created.") from error
    return {"access_token": access_token, "refresh_token": refresh_token}


def _session_user(session):
    if not session:
        return None
    document = _database()["users"].find_one({"_id": session["user_id"]})
    return _public_user(document)


def user_from_access_token(access_token):
    if not access_token:
        return None
    try:
        session = _database()["auth_sessions"].find_one(
            {
                "access_token_hash": _token_hash(access_token),
                "revoked": False,
                "access_expires_at": {"$gt": _now()},
                "refresh_expires_at": {"$gt": _now()},
            }
        )
        return _session_user(session)
    except (ValueError, PyMongoError):
        return None


def rotate_refresh_token(refresh_token):
    if not refresh_token:
        return None
    now = _now()
    new_access = secrets.token_urlsafe(32)
    new_refresh = secrets.token_urlsafe(48)
    try:
        session = _database()["auth_sessions"].find_one_and_update(
            {
                "refresh_token_hash": _token_hash(refresh_token),
                "revoked": False,
                "refresh_expires_at": {"$gt": now},
            },
            {
                "$set": {
                    "access_token_hash": _token_hash(new_access),
                    "refresh_token_hash": _token_hash(new_refresh),
                    "access_expires_at": now + ACCESS_TOKEN_TTL,
                    "refresh_expires_at": now + REFRESH_TOKEN_TTL,
                    "updated_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        user = _session_user(session)
        if not user:
            return None
        return {
            "user": user,
            "access_token": new_access,
            "refresh_token": new_refresh,
        }
    except (ValueError, PyMongoError):
        return None


def revoke_refresh_token(refresh_token):
    if not refresh_token:
        return False
    try:
        result = _database()["auth_sessions"].update_one(
            {"refresh_token_hash": _token_hash(refresh_token)},
            {"$set": {"revoked": True, "updated_at": _now()}},
        )
        return bool(result.matched_count)
    except PyMongoError:
        return False
