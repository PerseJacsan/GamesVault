"""User-scoped library browsing, collections, saved views, and local discovery."""

from __future__ import annotations

import json
import random
import re
from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, request, session

library_api = Blueprint("library_api", __name__)

STATUSES = {"Playing", "Backlog", "Completed", "Dropped"}
SORTS = {"title", "added", "last_played", "playtime", "rating", "release_year", "public_rating"}
VIEWS = {"grid", "list"}
SHELVES = {
    "recently_played": "Recently Played",
    "backlog": "Backlog",
    "favorites": "Favorites",
    "completed": "Completed",
    "playing": "Currently Playing",
}
SHELF_SORTS = {
    "recently_played": "last_played",
    "backlog": "added",
    "favorites": "rating",
    "completed": "last_played",
    "playing": "last_played",
}


def _db():
    if "db" not in g:
        from core import conn

        conn()
    return g.db


def _uid() -> int:
    return int(session.get("user_id", 0))


def _error(message: str, status: int = 400):
    return jsonify({"error": message}), status


def _clean_name(value) -> str | None:
    if not isinstance(value, str):
        return None
    name = re.sub(r"\s+", " ", value).strip()
    return name if 1 <= len(name) <= 48 else None


def _collection_for_user(collection_id: int):
    return _db().execute(
        "SELECT id,name,created_at FROM user_collections WHERE id=? AND user_id=?",
        (collection_id, _uid()),
    ).fetchone()


def _validate_filters(payload) -> dict | None:
    if not isinstance(payload, dict):
        return None
    result = {}
    for key in ("q", "genre", "platform"):
        value = payload.get(key, "")
        if value is not None and not isinstance(value, str):
            return None
        value = (value or "").strip()
        if len(value) > (100 if key == "q" else 80):
            return None
        if value:
            result[key] = value
    status = payload.get("status", "")
    if not isinstance(status, str) or (status and status not in STATUSES):
        return None
    if status:
        result["status"] = status
    favorite = payload.get("favorite", "")
    if favorite is not None and type(favorite) is not bool and favorite not in ("", "true", "false"):
        return None
    if favorite not in ("", None):
        result["favorite"] = "true" if favorite is True else "false" if favorite is False else favorite
    sort = payload.get("sort", "title")
    if not isinstance(sort, str) or sort not in SORTS:
        return None
    result["sort"] = sort
    view = payload.get("view", "grid")
    if not isinstance(view, str) or view not in VIEWS:
        return None
    result["view"] = view
    collection_id = payload.get("collection_id", "")
    if collection_id not in ("", None):
        if isinstance(collection_id, bool):
            return None
        try:
            result["collection_id"] = int(collection_id)
        except (TypeError, ValueError):
            return None
    shelf = payload.get("shelf", "")
    if not isinstance(shelf, str) or (shelf and shelf not in SHELVES):
        return None
    if shelf:
        result["shelf"] = shelf
    return result


def _library_games(filters: dict | None = None, *, collection_id: int | None = None) -> list[dict]:
    filters = filters or {}
    clauses = ["ug.user_id=?"]
    args: list[object] = [_uid()]
    status = filters.get("status")
    if status:
        clauses.append("ug.status=?")
        args.append(status)
    favorite = filters.get("favorite")
    if favorite in ("true", "false"):
        clauses.append("ug.favorite=?")
        args.append(1 if favorite == "true" else 0)
    query = filters.get("q", "")
    if query:
        clauses.append("(g.title LIKE ? OR g.genres LIKE ? OR COALESCE(g.tags,'') LIKE ? OR COALESCE(g.description,'') LIKE ? OR COALESCE(g.developer,'') LIKE ? OR COALESCE(g.publisher,'') LIKE ?)")
        args.extend([f"%{query}%"] * 6)
    genre = filters.get("genre", "")
    if genre:
        clauses.append("g.genres LIKE ?")
        args.append(f"%{genre}%")
    platform = filters.get("platform", "")
    if platform:
        clauses.append("EXISTS (SELECT 1 FROM game_platforms gp2 JOIN platforms p2 ON p2.id=gp2.platform_id WHERE gp2.game_id=g.id AND lower(p2.name)=lower(?))")
        args.append(platform)
    selected_collection = collection_id or filters.get("collection_id")
    if selected_collection:
        if not _collection_for_user(int(selected_collection)):
            return []
        clauses.append("EXISTS (SELECT 1 FROM collection_games cg WHERE cg.collection_id=? AND cg.game_id=g.id)")
        args.append(int(selected_collection))
    shelf = filters.get("shelf", "")
    if shelf == "recently_played":
        clauses.append("ug.last_played_at IS NOT NULL")
    elif shelf == "backlog":
        clauses.append("ug.status='Backlog'")
    elif shelf == "favorites":
        clauses.append("ug.favorite=1")
    elif shelf == "completed":
        clauses.append("ug.status='Completed'")
    elif shelf == "playing":
        clauses.append("ug.status='Playing'")

    order = {
        "title": "lower(g.title) COLLATE NOCASE ASC",
        "added": "datetime(ug.added_at) DESC, lower(g.title) COLLATE NOCASE ASC",
        "last_played": "CASE WHEN ug.last_played_at IS NULL THEN 1 ELSE 0 END, ug.last_played_at DESC, lower(g.title) COLLATE NOCASE ASC",
        "playtime": "ug.hours_played DESC, lower(g.title) COLLATE NOCASE ASC",
        "rating": "CASE WHEN ug.personal_rating IS NULL THEN 1 ELSE 0 END, ug.personal_rating DESC, lower(g.title) COLLATE NOCASE ASC",
        "release_year": "CASE WHEN g.release_date IS NULL OR g.release_date='' THEN 1 ELSE 0 END, substr(g.release_date,1,4) DESC, lower(g.title) COLLATE NOCASE ASC",
        "public_rating": "CASE WHEN g.rating IS NULL THEN 1 ELSE 0 END, g.rating DESC, lower(g.title) COLLATE NOCASE ASC",
    }.get(filters.get("sort", "title"), "lower(g.title) COLLATE NOCASE ASC")
    sql = f"""SELECT g.*, group_concat(DISTINCT p.name) AS platforms,
        ug.status, ug.hours_played, ug.personal_rating, ug.notes, ug.completion,
        ug.favorite, ug.priority, ug.added_at, ug.last_played_at
        FROM user_games ug JOIN games g ON g.id=ug.game_id
        LEFT JOIN game_platforms gp ON gp.game_id=g.id
        LEFT JOIN platforms p ON p.id=gp.platform_id
        WHERE {' AND '.join(clauses)}
        GROUP BY g.id ORDER BY {order}"""
    return [dict(item) for item in _db().execute(sql, args).fetchall()]


@library_api.get("/api/library/browse")
def browse_library():
    data = request.args
    status = data.get("status", "").strip()
    if status and status not in STATUSES:
        return _error("Choose a valid library status.")
    sort = data.get("sort", "title")
    if sort not in SORTS:
        return _error("Choose a valid sort order.")
    favorite = data.get("favorite", "")
    if favorite not in ("", "true", "false"):
        return _error("Favorite filter must be true or false.")
    filters = {
        "q": data.get("q", "").strip()[:100],
        "genre": data.get("genre", "").strip()[:80],
        "platform": data.get("platform", "").strip()[:80],
        "status": status,
        "favorite": favorite,
        "sort": sort,
        "shelf": data.get("shelf", ""),
    }
    if filters["shelf"] and filters["shelf"] not in SHELVES:
        return _error("Choose a valid smart shelf.")
    collection = data.get("collection_id", "")
    if collection:
        try:
            filters["collection_id"] = int(collection)
        except ValueError:
            return _error("Collection ID must be an integer.")
        if filters["collection_id"] < 1:
            return _error("Collection ID must be positive.")
        if not _collection_for_user(filters["collection_id"]):
            return _error("Collection not found.", 404)
    if len(filters["q"]) > 100:
        return _error("Search text is too long.")
    games = _library_games(filters)
    return jsonify({"games": games, "count": len(games)})


@library_api.get("/api/collections")
def list_collections():
    game_id = request.args.get("game_id")
    if game_id:
        try:
            game_id = int(game_id)
        except ValueError:
            return _error("Game ID must be an integer.")
        member = _db().execute(
            "SELECT 1 FROM user_games WHERE user_id=? AND game_id=?", (_uid(), game_id)
        ).fetchone()
        if not member:
            return jsonify({"collections": [], "smart": []})
    collections = [dict(item) for item in _db().execute(
        """SELECT c.id,c.name,c.created_at,count(cg.game_id) game_count,
                  CASE WHEN ? IS NULL THEN 0 ELSE EXISTS(
                    SELECT 1 FROM collection_games cx WHERE cx.collection_id=c.id AND cx.game_id=?
                  ) END contains_game
           FROM user_collections c LEFT JOIN collection_games cg ON cg.collection_id=c.id
           WHERE c.user_id=? GROUP BY c.id ORDER BY lower(c.name) COLLATE NOCASE""",
        (game_id, game_id, _uid()),
    ).fetchall()]
    if game_id:
        return jsonify({"collections": collections, "smart": []})
    counts = {
        "recently_played": _db().execute("SELECT count(*) FROM user_games WHERE user_id=? AND last_played_at IS NOT NULL", (_uid(),)).fetchone()[0],
        "backlog": _db().execute("SELECT count(*) FROM user_games WHERE user_id=? AND status='Backlog'", (_uid(),)).fetchone()[0],
        "favorites": _db().execute("SELECT count(*) FROM user_games WHERE user_id=? AND favorite=1", (_uid(),)).fetchone()[0],
        "completed": _db().execute("SELECT count(*) FROM user_games WHERE user_id=? AND status='Completed'", (_uid(),)).fetchone()[0],
        "playing": _db().execute("SELECT count(*) FROM user_games WHERE user_id=? AND status='Playing'", (_uid(),)).fetchone()[0],
    }
    smart = [{"slug": slug, "name": name, "game_count": counts[slug]} for slug, name in SHELVES.items()]
    return jsonify({"collections": collections, "smart": smart})


@library_api.post("/api/collections")
def create_collection():
    name = _clean_name((request.get_json(silent=True) or {}).get("name"))
    if not name:
        return _error("Collection names must be 1–48 characters.")
    exists = _db().execute("SELECT 1 FROM user_collections WHERE user_id=? AND lower(name)=lower(?)", (_uid(), name)).fetchone()
    if exists:
        return _error("A collection with that name already exists.", 409)
    cursor = _db().execute("INSERT INTO user_collections(user_id,name) VALUES(?,?)", (_uid(), name))
    _db().commit()
    return jsonify({"id": cursor.lastrowid, "name": name}), 201


@library_api.get("/api/collections/<int:collection_id>")
def get_collection(collection_id: int):
    item = _collection_for_user(collection_id)
    if not item:
        return _error("Collection not found.", 404)
    games = _library_games(collection_id=collection_id)
    return jsonify({"collection": dict(item), "games": games, "count": len(games)})


@library_api.put("/api/collections/<int:collection_id>")
def rename_collection(collection_id: int):
    item = _collection_for_user(collection_id)
    if not item:
        return _error("Collection not found.", 404)
    name = _clean_name((request.get_json(silent=True) or {}).get("name"))
    if not name:
        return _error("Collection names must be 1–48 characters.")
    exists = _db().execute("SELECT 1 FROM user_collections WHERE user_id=? AND id<>? AND lower(name)=lower(?)", (_uid(), collection_id, name)).fetchone()
    if exists:
        return _error("A collection with that name already exists.", 409)
    _db().execute("UPDATE user_collections SET name=? WHERE id=? AND user_id=?", (name, collection_id, _uid()))
    _db().commit()
    return jsonify({"id": collection_id, "name": name})


@library_api.delete("/api/collections/<int:collection_id>")
def delete_collection(collection_id: int):
    cursor = _db().execute("DELETE FROM user_collections WHERE id=? AND user_id=?", (collection_id, _uid()))
    if cursor.rowcount != 1:
        return _error("Collection not found.", 404)
    _db().commit()
    return jsonify({"message": "Collection deleted."})


@library_api.post("/api/collections/<int:collection_id>/games")
def add_game_to_collection(collection_id: int):
    if not _collection_for_user(collection_id):
        return _error("Collection not found.", 404)
    payload = request.get_json(silent=True) or {}
    try:
        game_id = int(payload.get("game_id"))
    except (TypeError, ValueError):
        return _error("A valid game_id is required.")
    member = _db().execute("SELECT 1 FROM user_games WHERE user_id=? AND game_id=?", (_uid(), game_id)).fetchone()
    if not member:
        return _error("Add this game to your library before placing it in a collection.", 404)
    _db().execute("INSERT OR IGNORE INTO collection_games(collection_id,game_id) VALUES(?,?)", (collection_id, game_id))
    _db().commit()
    return jsonify({"message": "Game added to collection."})


@library_api.delete("/api/collections/<int:collection_id>/games/<int:game_id>")
def remove_game_from_collection(collection_id: int, game_id: int):
    if not _collection_for_user(collection_id):
        return _error("Collection not found.", 404)
    _db().execute("DELETE FROM collection_games WHERE collection_id=? AND game_id=?", (collection_id, game_id))
    _db().commit()
    return jsonify({"message": "Game removed from collection."})


@library_api.route("/api/saved-views", methods=["GET", "POST"])
def saved_views():
    if request.method == "GET":
        items = []
        for item in _db().execute("SELECT id,name,filters_json,created_at FROM saved_views WHERE user_id=? ORDER BY lower(name) COLLATE NOCASE", (_uid(),)):
            result = dict(item)
            try:
                result["filters"] = json.loads(result.pop("filters_json"))
            except (TypeError, ValueError):
                result["filters"] = {}
                result.pop("filters_json", None)
            items.append(result)
        return jsonify(items)
    payload = request.get_json(silent=True) or {}
    name = _clean_name(payload.get("name"))
    filters = _validate_filters(payload.get("filters"))
    if not name or filters is None:
        return _error("Provide a 1–48 character name and valid library filters.")
    exists = _db().execute("SELECT 1 FROM saved_views WHERE user_id=? AND lower(name)=lower(?)", (_uid(), name)).fetchone()
    if exists:
        return _error("A saved view with that name already exists.", 409)
    cursor = _db().execute("INSERT INTO saved_views(user_id,name,filters_json) VALUES(?,?,?)", (_uid(), name, json.dumps(filters, separators=(",", ":"))))
    _db().commit()
    return jsonify({"id": cursor.lastrowid, "name": name, "filters": filters}), 201


@library_api.put("/api/saved-views/<int:view_id>")
def update_saved_view(view_id: int):
    item = _db().execute("SELECT id FROM saved_views WHERE id=? AND user_id=?", (view_id, _uid())).fetchone()
    if not item:
        return _error("Saved view not found.", 404)
    payload = request.get_json(silent=True) or {}
    name = _clean_name(payload.get("name"))
    filters = _validate_filters(payload.get("filters"))
    if not name or filters is None:
        return _error("Provide a 1–48 character name and valid library filters.")
    exists = _db().execute("SELECT 1 FROM saved_views WHERE user_id=? AND id<>? AND lower(name)=lower(?)", (_uid(), view_id, name)).fetchone()
    if exists:
        return _error("A saved view with that name already exists.", 409)
    _db().execute("UPDATE saved_views SET name=?,filters_json=? WHERE id=? AND user_id=?", (name, json.dumps(filters, separators=(",", ":")), view_id, _uid()))
    _db().commit()
    return jsonify({"id": view_id, "name": name, "filters": filters})


@library_api.delete("/api/saved-views/<int:view_id>")
def delete_saved_view(view_id: int):
    cursor = _db().execute("DELETE FROM saved_views WHERE id=? AND user_id=?", (view_id, _uid()))
    if cursor.rowcount != 1:
        return _error("Saved view not found.", 404)
    _db().commit()
    return jsonify({"message": "Saved view deleted."})


@library_api.post("/api/library/<int:game_id>/played")
def mark_game_played(game_id: int):
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cursor = _db().execute("UPDATE user_games SET last_played_at=? WHERE user_id=? AND game_id=?", (timestamp, _uid(), game_id))
    if cursor.rowcount != 1:
        return _error("Add this game to your library before marking it played.", 404)
    _db().commit()
    return jsonify({"game_id": game_id, "last_played_at": timestamp})


@library_api.get("/api/discovery/pick")
def pick_from_library():
    mode = request.args.get("mode", "random")
    if mode not in {"random", "backlog", "not_recent"}:
        return _error("Choose random, backlog, or not_recent mode.")
    filters = {
        "q": request.args.get("q", "").strip()[:100],
        "genre": request.args.get("genre", "").strip()[:80],
        "platform": request.args.get("platform", "").strip()[:80],
        "status": request.args.get("status", "").strip(),
        "sort": "title",
        "shelf": "",
    }
    if filters["status"] and filters["status"] not in STATUSES:
        return _error("Choose a valid library status.")
    if mode == "backlog":
        filters["status"] = "Backlog"
    collection_id = request.args.get("collection_id", "")
    if collection_id:
        try:
            filters["collection_id"] = int(collection_id)
        except ValueError:
            return _error("Collection ID must be an integer.")
    games = _library_games(filters)
    if not games:
        return _error("No games in your library match those choices.", 404)
    reason = "A random pick from your library."
    if mode == "backlog":
        reason = "Picked from your backlog."
    elif mode == "not_recent":
        marked = [game for game in games if game.get("last_played_at")]
        if not marked:
            reason = "No last-played dates are recorded yet; this pick is from your selected library games."
        else:
            oldest = min(game["last_played_at"] for game in marked)
            games = [game for game in marked if game["last_played_at"] == oldest]
            reason = f"Longest since a recorded play: {oldest[:10]}."
    seed = request.args.get("seed")
    picker = random.Random(seed) if seed else random.SystemRandom()
    game = picker.choice(games)
    return jsonify({"game": game, "reason": reason, "candidate_count": len(games), "mode": mode})
