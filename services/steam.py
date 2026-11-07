"""Steam Store catalogue synchronization, isolated from Flask route handling."""
from __future__ import annotations

import json
import os
import sqlite3
import re
from datetime import datetime, timezone
from typing import Any

import requests

# Valve's current Store catalogue endpoint. ISteamApps/GetAppList is deprecated.
APP_LIST_URL = "https://api.steampowered.com/IStoreService/GetAppList/v1/"
APP_LIST_FALLBACK_URL = "https://partner.steam-api.com/IStoreService/GetAppList/v1/"
STORE_SEARCH_URL = "https://store.steampowered.com/search/results/"
STORE_SEARCH_FALLBACK_URL = "https://store.steampowered.com/search/"
DETAILS_URL = "https://store.steampowered.com/api/appdetails"
TIMEOUT_SECONDS = 15


class SteamSyncError(RuntimeError):
    """A recoverable Steam catalogue synchronization error."""


def _api_key() -> str:
    return os.getenv("STEAM_API_KEY", "").strip()


def _configured_ids() -> list[int]:
    value = os.getenv("STEAM_SYNC_APPIDS", "").strip()
    if not value:
        return []
    try:
        ids = [int(appid.strip()) for appid in value.split(",") if appid.strip()]
    except ValueError as exc:
        raise SteamSyncError(
            "STEAM_SYNC_APPIDS must be a comma-separated list of numeric App IDs."
        ) from exc
    if any(appid <= 0 for appid in ids):
        raise SteamSyncError("STEAM_SYNC_APPIDS must contain positive Steam App IDs.")
    return list(dict.fromkeys(ids))


def _catalogue_target() -> int:
    try:
        value = int(os.getenv("STEAM_CATALOGUE_TARGET", "750"))
    except ValueError as exc:
        raise SteamSyncError("STEAM_CATALOGUE_TARGET must be a number between 1 and 50000.") from exc
    if not 1 <= value <= 50000:
        raise SteamSyncError("STEAM_CATALOGUE_TARGET must be a number between 1 and 50000.")
    return value


def _detail_batch_size() -> int:
    try:
        value = int(os.getenv("STEAM_DETAIL_BATCH_SIZE", "50"))
    except ValueError as exc:
        raise SteamSyncError("STEAM_DETAIL_BATCH_SIZE must be a number between 1 and 50.") from exc
    if not 1 <= value <= 50:
        raise SteamSyncError("STEAM_DETAIL_BATCH_SIZE must be a number between 1 and 50.")
    return value


def _steam_error(response: requests.Response, operation: str) -> SteamSyncError:
    status = response.status_code
    if status in (401, 403):
        return SteamSyncError(
            f"Steam rejected the {operation} request. Check that STEAM_API_KEY is valid and active."
        )
    if status == 429:
        return SteamSyncError("Steam rate-limited GamesVault. Wait a little before refreshing again.")
    if 500 <= status <= 599:
        return SteamSyncError("Steam is temporarily unavailable. Please try again later.")
    return SteamSyncError(f"Steam returned HTTP {status} while {operation}.")


def _json_response(response: requests.Response, operation: str) -> dict[str, Any]:
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise _steam_error(response, operation) from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise SteamSyncError(f"Steam returned an invalid response while {operation}.") from exc
    if not isinstance(payload, dict):
        raise SteamSyncError(f"Steam returned an unexpected response while {operation}.")
    return payload


def _last_sync_unix(db: sqlite3.Connection) -> int | None:
    row = db.execute("SELECT value FROM app_settings WHERE key='steam_last_sync'").fetchone()
    if not row:
        return None
    try:
        return int(datetime.fromisoformat(row[0].replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError):
        return None


def _public_store_appids(session: requests.Session, limit: int) -> list[int]:
    """Discover App IDs through Steam's public Store search (no Web API key needed).

    Prefer the Store's paginated JSON results endpoint and fall back to its HTML page.
    IDs are de-duplicated because each search result can appear in several UI fragments.
    """
    ids: list[int] = []
    seen: set[int] = set()
    page_size = 50
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "Accept": "application/json,text/html,*/*",
    }

    for start in range(0, limit, page_size):
        params = {
            "query": "",
            "start": start,
            "count": min(page_size, limit - start),
            "category1": "998",
            "supportedlang": "english",
            "json": "1",
        }
        html = ""
        request_error: Exception | None = None
        for url, is_json_endpoint in ((STORE_SEARCH_URL, True), (STORE_SEARCH_FALLBACK_URL, False)):
            try:
                response = session.get(url, params=params if is_json_endpoint else {
                    "category1": "998", "start": start, "count": params["count"], "ndl": "1"
                }, headers=headers, timeout=TIMEOUT_SECONDS)
            except requests.Timeout as exc:
                request_error = exc
                continue
            except requests.RequestException as exc:
                request_error = exc
                continue
            if response.status_code == 429:
                raise SteamSyncError("Steam rate-limited GamesVault. Wait a little before refreshing again.")
            if response.status_code >= 500:
                request_error = SteamSyncError("Steam is temporarily unavailable. Please try again later.")
                continue
            if response.status_code != 200:
                request_error = SteamSyncError(f"Steam Store returned HTTP {response.status_code} while discovering games.")
                continue
            if is_json_endpoint:
                try:
                    payload = response.json()
                except ValueError:
                    payload = {}
                html = payload.get("results_html", "") if isinstance(payload, dict) else ""
                if not html and isinstance(payload, dict) and payload.get("success") == 0:
                    html = ""
            else:
                html = response.text or ""
            if html:
                break

        if not html:
            # Do not fail after a successful earlier page. Return discovered IDs so far;
            # the caller can still enrich those and retain everything already stored.
            if ids:
                break
            if isinstance(request_error, SteamSyncError):
                raise request_error
            if isinstance(request_error, requests.Timeout):
                raise SteamSyncError("Steam Store search timed out. Please try again.") from request_error
            raise SteamSyncError("Could not read Steam Store search results. Check your internet connection and try again.") from request_error

        page_ids = re.findall(r"/app/(\d+)(?:/|[?\"'])", html)
        page_ids.extend(re.findall(r"data-ds-appid=[\"](\d+)[\"]", html))
        before = len(ids)
        for raw in page_ids:
            try:
                appid = int(raw)
            except (TypeError, ValueError):
                continue
            if appid > 0 and appid not in seen:
                seen.add(appid)
                ids.append(appid)
                if len(ids) >= limit:
                    return ids[:limit]

        if len(ids) == before:
            # Steam can return the same first page when its search parameters are
            # rejected or its public service is throttling the client.
            break

    if not ids:
        raise SteamSyncError("Steam Store search returned no games. Please try again later.")
    return ids[:limit]


def _catalogue_appids(
    db: sqlite3.Connection, session: requests.Session, limit: int
) -> list[int]:
    configured = _configured_ids()
    if configured:
        # Development/testing mode deliberately bypasses GetAppList and enriches
        # exactly the App IDs requested by the developer.
        return configured[:limit]

    key = _api_key()
    if not key:
        # No Steam Web API key is required for local development. Discover the
        # catalogue from Steam's public Store search pages, then use the public
        # appdetails endpoint for the actual game metadata. If a key is added later,
        # the official IStoreService path below becomes the preferred source.
        ids = _public_store_appids(session, limit)
        existing_ids = {row[0] for row in db.execute(
            "SELECT steam_appid FROM games WHERE steam_appid IS NOT NULL"
        ).fetchall()}
        existing_updates = [appid for appid in ids if appid in existing_ids]
        new_ids = [appid for appid in ids if appid not in existing_ids]
        return (existing_updates + new_ids)[:limit]

    params = {
        "key": key,
        "input_json": json.dumps(
            {
                "include_games": True,
                "include_dlc": False,
                "include_software": False,
                "include_videos": False,
                "include_hardware": False,
                "max_results": limit,
            },
            separators=(",", ":"),
        ),
    }

    last_sync = _last_sync_unix(db)
    if last_sync:
        # Small overlap protects against clock differences between GamesVault and
        # Steam. Reprocessing an item is harmless because upserts are App-ID based.
        params["input_json"] = json.dumps(
            {
                **json.loads(params["input_json"]),
                "if_modified_since": max(0, last_sync - 60),
            },
            separators=(",", ":"),
        )

    response = None
    last_error: Exception | None = None
    for url in (APP_LIST_URL, APP_LIST_FALLBACK_URL):
        try:
            candidate = session.get(url, params=params, timeout=TIMEOUT_SECONDS)
        except requests.Timeout as exc:
            last_error = exc
            continue
        except requests.RequestException as exc:
            last_error = exc
            continue

        # A normal Steam Web API key commonly works on the public api.steampowered.com
        # host. If that host rejects the request, try Valve's documented partner host
        # before surfacing the error. Other HTTP errors are not retried blindly.
        if candidate.status_code == 403 and url == APP_LIST_URL:
            last_error = _steam_error(candidate, "fetching the application catalogue")
            continue
        response = candidate
        break

    if response is None:
        if isinstance(last_error, SteamSyncError):
            raise last_error
        if isinstance(last_error, requests.Timeout):
            raise SteamSyncError("Steam catalogue request timed out. Please try again.") from last_error
        raise SteamSyncError("Could not connect to Steam. Check your internet connection and try again.") from last_error

    payload = _json_response(response, "fetching the application catalogue")
    container = payload.get("response") if isinstance(payload.get("response"), dict) else payload
    apps = container.get("apps", []) if isinstance(container, dict) else []
    if not isinstance(apps, list):
        raise SteamSyncError("Steam returned an unexpected application catalogue format.")

    ids: list[int] = []
    for item in apps:
        if not isinstance(item, dict):
            continue
        try:
            appid = int(item.get("appid", 0))
        except (TypeError, ValueError):
            continue
        if appid > 0:
            ids.append(appid)

    # Keep the working catalogue capped at the configured target. Existing
    # Steam-backed games are always eligible for refresh; new games only fill
    # remaining catalogue capacity.
    existing_ids = {row[0] for row in db.execute(
        "SELECT steam_appid FROM games WHERE steam_appid IS NOT NULL"
    ).fetchall()}
    remaining = max(0, limit - len(existing_ids))
    existing_updates = [appid for appid in ids if appid in existing_ids]
    new_ids = [appid for appid in ids if appid not in existing_ids][:remaining]
    return (existing_updates + new_ids)[:limit]


def _price(data: dict[str, Any]) -> tuple[float | None, float | None, int | None]:
    overview = data.get("price_overview") or {}
    if not overview:
        return None, None, None
    try:
        final = float(overview.get("final", 0)) / 100
        initial = float(overview.get("initial", 0)) / 100
        discount = int(overview.get("discount_percent", 0))
    except (TypeError, ValueError):
        return None, None, None
    return final, initial, discount


def _platform_names(data: dict[str, Any]) -> list[str]:
    platforms = data.get("platforms") or {}
    mapping = {"windows": "PC", "mac": "macOS", "linux": "Linux"}
    return [name for key, name in mapping.items() if platforms.get(key)]


def normalize(appid: int, payload: dict[str, Any]) -> dict[str, Any] | None:
    data = payload.get(str(appid), {})
    if not isinstance(data, dict) or not data.get("success"):
        return None

    item = data.get("data") or {}
    if not isinstance(item, dict) or item.get("type") != "game" or not item.get("name"):
        return None

    price, original_price, discount = _price(item)
    genres = ", ".join(
        entry.get("description", "")
        for entry in item.get("genres", [])
        if isinstance(entry, dict) and entry.get("description")
    )
    release = (item.get("release_date") or {}).get("date") or None

    # Steam's Store payload normally includes capsule/header art, but some titles
    # omit one or both fields. Keep an App-ID based portrait fallback so a game
    # never becomes an empty card just because a particular payload omitted art.
    capsule_image = (
        item.get("capsule_image")
        or item.get("capsule_imagev5")
        or item.get("header_image")
        or f"https://steamcdn-a.akamaihd.net/steam/apps/{appid}/library_600x900_2x.jpg"
    )

    screenshots = [
        shot.get("path_full") or shot.get("path_thumbnail")
        for shot in (item.get("screenshots") or [])
        if isinstance(shot, dict) and (shot.get("path_full") or shot.get("path_thumbnail"))
    ]

    return {
        "steam_appid": appid,
        "title": item["name"],
        "description": item.get("detailed_description") or item.get("short_description") or "",
        "short_description": item.get("short_description") or "",
        "developers": ", ".join(item.get("developers") or []),
        "publishers": ", ".join(item.get("publishers") or []),
        "release_date": release,
        "genres": genres or "Uncategorized",
        "cover_image": capsule_image,
        "header_image": item.get("header_image"),
        "background_image": item.get("background") or item.get("background_raw"),
        "screenshots": screenshots,
        "price": price,
        "original_price": original_price,
        "discount": discount,
        "store_url": f"https://store.steampowered.com/app/{appid}/",
        "platforms": _platform_names(item),
    }


def _upsert_game(db: sqlite3.Connection, game: dict[str, Any]) -> str:
    existing = db.execute(
        "SELECT id FROM games WHERE steam_appid=?", (game["steam_appid"],)
    ).fetchone()

    # These are catalogue fields owned by Steam. Rating/playtime/tags are not
    # overwritten because the current appdetails payload does not provide the
    # same semantics as GamesVault's existing local fields.
    values = {
        key: game[key]
        for key in (
            "title",
            "description",
            "short_description",
            "developers",
            "publishers",
            "release_date",
            "genres",
            "cover_image",
            "header_image",
            "background_image",
            "screenshots",
            "price",
            "original_price",
            "discount",
            "store_url",
        )
    }

    if existing:
        columns = ", ".join(f"{name}=?" for name in values)
        db.execute(
            f"UPDATE games SET {columns}, cover=COALESCE(?, cover), "
            "developer=COALESCE(?, developer), publisher=COALESCE(?, publisher) WHERE id=?",
            (*[json.dumps(v) if key == "screenshots" else v for key, v in values.items()], game["cover_image"], game["developers"] or None,
             game["publishers"] or None, existing[0]),
        )
        game_id, action = existing[0], "updated"
    else:
        try:
            db.execute(
                """INSERT INTO games(
                    steam_appid,title,genres,description,short_description,developers,publishers,
                    developer,publisher,release_date,cover,cover_image,header_image,background_image,
                    price,original_price,discount,store_url,screenshots
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    game["steam_appid"], game["title"], game["genres"], game["description"],
                    game["short_description"], game["developers"], game["publishers"],
                    game["developers"], game["publishers"], game["release_date"],
                    game["cover_image"], game["cover_image"], game["header_image"],
                    game["background_image"], game["price"], game["original_price"],
                    game["discount"], game["store_url"], json.dumps(game["screenshots"]),
                ),
            )
        except sqlite3.IntegrityError as exc:
            # A title collision should not make a sync silently overwrite an
            # unrelated game. Surface it as a per-item failure instead.
            raise SteamSyncError(
                f"Steam game '{game['title']}' conflicts with an existing GamesVault title."
            ) from exc
        game_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        action = "added"

    if game["platforms"]:
        db.execute("DELETE FROM game_platforms WHERE game_id=?", (game_id,))
        for platform in game["platforms"]:
            db.execute("INSERT OR IGNORE INTO platforms(name) VALUES(?)", (platform,))
            platform_id = db.execute(
                "SELECT id FROM platforms WHERE name=?", (platform,)
            ).fetchone()[0]
            db.execute(
                "INSERT OR IGNORE INTO game_platforms(game_id,platform_id) VALUES(?,?)",
                (game_id, platform_id),
            )
    return action


def _fetch_details_batch(session: requests.Session, appids: list[int]) -> dict[int, dict[str, Any]]:
    """Fetch appdetails in a batch, then fill any missing IDs individually.

    Steam's public Store endpoint has varied in how it handles comma-separated IDs.
    A partial 200 response must not silently turn the missing games into failed imports.
    """
    if not appids:
        return {}

    parsed: dict[int, dict[str, Any]] = {}
    try:
        response = session.get(
            DETAILS_URL,
            params={"appids": ",".join(map(str, appids)), "cc": "us", "l": "en"},
            timeout=TIMEOUT_SECONDS,
        )
        payload = _json_response(response, "fetching Steam game details")
        for key, value in payload.items():
            try:
                parsed[int(key)] = value
            except (TypeError, ValueError):
                continue
    except SteamSyncError as exc:
        # A 400/other unsupported-batch response can be retried per App ID. Avoid
        # hammering Steam after rate limiting or an upstream outage.
        if "rate-limited" in str(exc).lower() or "temporarily unavailable" in str(exc).lower():
            raise
        parsed = {}
    except requests.Timeout as exc:
        raise SteamSyncError("Steam game-detail request timed out. Try refreshing again shortly.") from exc
    except requests.RequestException as exc:
        raise SteamSyncError("Could not connect to Steam game details. Check your internet connection and try again.") from exc
    except ValueError:
        parsed = {}

    missing = [appid for appid in appids if appid not in parsed]
    for appid in missing:
        try:
            response = session.get(
                DETAILS_URL,
                params={"appids": appid, "cc": "us", "l": "en"},
                timeout=TIMEOUT_SECONDS,
            )
            parsed[appid] = _json_response(response, f"fetching App ID {appid}")
        except SteamSyncError as exc:
            if "rate-limited" in str(exc).lower() or "temporarily unavailable" in str(exc).lower():
                raise
            continue
        except requests.Timeout:
            # Stop the per-item fallback if the whole service is timing out rather
            # than waiting through 50 identical slow requests in the same batch.
            break
        except requests.RequestException:
            break
        except ValueError:
            continue
    return parsed


def sync_catalogue(db: sqlite3.Connection, session: requests.Session | None = None) -> dict[str, Any]:
    """Fetch the configured Steam catalogue slice and safely upsert catalogue-only fields."""
    own_session = session is None
    session = session or requests.Session()
    try:
        return _sync_catalogue_inner(db, session)
    finally:
        if own_session:
            session.close()

def _sync_catalogue_inner(db: sqlite3.Connection, session: requests.Session) -> dict[str, Any]:
    limit = _catalogue_target()
    appids = _catalogue_appids(db, session, limit)
    result: dict[str, Any] = {
        "success": True,
        "added": 0,
        "updated": 0,
        "skipped": 0,
        "failed": 0,
        "requested": len(appids),
        "target": limit,
        "detail_batch_size": _detail_batch_size(),
    }

    if not appids:
        # No changed/new applications were returned by the incremental catalogue.
        # Record the successful check so the UI can show when Refresh was last used.
        with db:
            db.execute(
                "INSERT INTO app_settings(key,value) VALUES('steam_last_sync',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (datetime.now(timezone.utc).isoformat(),),
            )
        return result

    try:
        with db:
            batch_size = _detail_batch_size()
            for start in range(0, len(appids), batch_size):
                batch = appids[start:start + batch_size]
                details = _fetch_details_batch(session, batch)
                for appid in batch:
                    db.execute("SAVEPOINT steam_item")
                    try:
                        payload = details.get(appid)
                        if payload is None:
                            result["failed"] += 1
                            db.execute("RELEASE SAVEPOINT steam_item")
                            continue
                        game = normalize(appid, {str(appid): payload})
                        if not game:
                            result["skipped"] += 1
                            db.execute("RELEASE SAVEPOINT steam_item")
                            continue
                        result[_upsert_game(db, game)] += 1
                        db.execute("RELEASE SAVEPOINT steam_item")
                    except SteamSyncError:
                        db.execute("ROLLBACK TO SAVEPOINT steam_item")
                        db.execute("RELEASE SAVEPOINT steam_item")
                        # A single bad App ID should not destroy the rest of a manual
                        # refresh. Keep the transaction alive and report the failure.
                        result["failed"] += 1
                    except sqlite3.Error:
                        db.execute("ROLLBACK TO SAVEPOINT steam_item")
                        db.execute("RELEASE SAVEPOINT steam_item")
                        result["failed"] += 1

            if result["failed"] == 0:
                db.execute(
                    "INSERT INTO app_settings(key,value) VALUES('steam_last_sync',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (datetime.now(timezone.utc).isoformat(),),
                )
    except sqlite3.Error as exc:
        raise SteamSyncError("GamesVault could not save the Steam catalogue safely.") from exc

    return result
