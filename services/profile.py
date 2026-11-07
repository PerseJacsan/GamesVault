"""Authenticated profile-description settings for the local GamesVault account."""

from flask import Blueprint, g, jsonify, request, session

profile_api = Blueprint("profile_api", __name__)


def _db():
    if "db" not in g:
        from core import conn
        conn()
    return g.db


@profile_api.route("/api/profile", methods=["GET", "PUT"])
def profile():
    db = _db()
    user_id = int(session.get("user_id", 0))
    if not user_id:
        return jsonify({"error": "Please log in to continue", "auth_required": True}), 401
    if request.method == "GET":
        user = db.execute(
            "SELECT name,username,email,COALESCE(profile_description,'') AS profile_description FROM users WHERE id=?",
            (user_id,),
        ).fetchone()
        if not user:
            return jsonify({"error": "Account not found."}), 404
        return jsonify(dict(user))

    data = request.get_json(silent=True) or {}
    description = data.get("profile_description")
    if not isinstance(description, str):
        return jsonify({"error": "Profile description must be 280 characters or fewer."}), 400
    description = description.strip()
    if len(description) > 280:
        return jsonify({"error": "Profile description must be 280 characters or fewer."}), 400
    db.execute(
        "UPDATE users SET profile_description=? WHERE id=?",
        (description, user_id),
    )
    db.commit()
    return jsonify({"message": "Profile saved."})
