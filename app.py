"""JellyReg — simple invite-code-based Jellyfin user registration."""

import hashlib
import json
import os
import secrets
import time
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
import requests

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

JELLYFIN_URL = os.environ.get("JELLYFIN_URL", "http://localhost:8096")
JELLYFIN_API_KEY=os.environ.get("JELLYFIN_API_KEY", "")
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH", "")
DEFAULT_LIBRARIES = (
    os.environ.get("DEFAULT_LIBRARIES", "").split(",")
    if os.environ.get("DEFAULT_LIBRARIES")
    else []
)

DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
INVITES_FILE = DATA_DIR / "invites.json"
USERS_FILE = DATA_DIR / "users.json"
SETTINGS_FILE = DATA_DIR / "settings.json"

SERVER_NAME = os.environ.get("SERVER_NAME", "Jellyfin")
JELLYFIN_PUBLIC_URL = os.environ.get("JELLYFIN_PUBLIC_URL", "")


# -- Data helpers --

def _load_json(path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default.copy()


def _save_json(path, data):
    path.write_text(json.dumps(data, indent=2))


def get_invites():
    return _load_json(INVITES_FILE, {})


def save_invites(invites):
    _save_json(INVITES_FILE, invites)


def get_registered_users():
    return _load_json(USERS_FILE, [])


def save_registered_users(users):
    _save_json(USERS_FILE, users)


def hash_password(pw):
    return hashlib.sha256(pw.encode()).hexdigest()


def verify_admin(password):
    return hash_password(password) == ADMIN_PASSWORD_HASH


# -- Jellyfin API helpers --

def jf_headers():
    return {"X-Emby-Token": JELLYFIN_API_KEY}


def jf_get(path, **kwargs):
    return requests.get(f"{JELLYFIN_URL}{path}", headers=jf_headers(), timeout=10, **kwargs)


def jf_post(path, **kwargs):
    return requests.post(f"{JELLYFIN_URL}{path}", headers=jf_headers(), timeout=10, **kwargs)


def jf_delete(path, **kwargs):
    return requests.delete(f"{JELLYFIN_URL}{path}", headers=jf_headers(), timeout=10, **kwargs)


def jf_create_user(username, password):
    """Create a Jellyfin user and set their password."""
    r = jf_post("/Users/New", json={"Name": username})
    if r.status_code not in (200, 204):
        return False, f"Failed to create user: {r.status_code} {r.text[:200]}"

    user_id = r.json().get("Id")
    if not user_id:
        return False, "No user ID returned"

    # Set password
    r2 = jf_post(f"/Users/{user_id}/Password", json={"NewPw": password})
    if r2.status_code not in (200, 204):
        jf_delete(f"/Users/{user_id}")
        return False, f"Failed to set password: {r2.status_code}"

    # Configure default library access if specified
    if DEFAULT_LIBRARIES:
        _set_library_access(user_id)

    # Enable subtitle downloads
    _set_user_config(user_id)

    return True, user_id


def _set_library_access(user_id):
    """Grant access to configured default libraries."""
    r = jf_get("/Library/VirtualFolders")
    if r.status_code != 200:
        return
    for folder in r.json():
        name = folder.get("Name", "")
        if name in DEFAULT_LIBRARIES or "*" in DEFAULT_LIBRARIES:
            jf_post(
                f"/Users/{user_id}/Policy",
                json={"EnableAllFolders": True},
            )
            break


def _set_user_config(user_id):
    """Enable subtitle downloads by default."""
    config = {
        "EnableSubtitleDownloads": True,
        "SubtitleDownloadLanguages": [],
        "DownloadSubtitlesAutomatically": False,
    }
    jf_post(f"/Users/{user_id}/Configuration", json=config)

def jf_authenticate_user(username, password):
    """Authenticate as a user and return (access_token, user_id)."""
    try:
        r = requests.post(
            f"{JELLYFIN_URL}/Users/AuthenticateByName",
            json={"Username": username, "Pw": password},
            headers={
                "Content-Type": "application/json",
                "X-Emby-Authorization": 'MediaBrowser Client="JellyReg", Device="Browser", DeviceId="jellyreg", Version="1.0"',
            },
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            token = data.get("AccessToken", "")
            user_id = data.get("User", {}).get("Id", "")
            return token, user_id
    except Exception:
        pass
    return "", ""


def load_settings():
    """Load settings from JSON file, falling back to env vars."""
    defaults = {
        "server_name": SERVER_NAME,
        "jellyfin_url": JELLYFIN_URL,
        "jellyfin_public_url": JELLYFIN_PUBLIC_URL,
    }
    try:
        with open(SETTINGS_FILE) as f:
            saved = json.load(f)
        defaults.update(saved)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return defaults


def save_settings(settings):
    """Persist settings to JSON file."""
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)


def get_public_url():
    """Return the public Jellyfin URL for client linking."""
    settings = load_settings()
    return settings.get("jellyfin_public_url") or settings.get("jellyfin_url", JELLYFIN_URL)


# -- Frontend routes --

@app.route("/")
def index():
    return render_template("register.html", server_name=load_settings().get("server_name", "Jellyfin"))


@app.route("/admin")
def admin():
    if "admin" not in session:
        return redirect(url_for("admin_login"))
    invites = get_invites()
    users = get_registered_users()
    return render_template("admin.html", invites=invites, users=users, settings=load_settings())


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if verify_admin(password):
            session["admin"] = True
            return redirect(url_for("admin"))
        return render_template("admin_login.html", error="Invalid password")
    return render_template("admin_login.html")


# -- API routes --

@app.route("/api/register", methods=["POST"])
def api_register():
    data = request.get_json()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    invite_code = (data.get("invite_code") or "").strip()

    if len(username) < 2 or len(username) > 30:
        return jsonify({"ok": False, "error": "Username must be 2-30 characters"}), 400
    if not password or len(password) < 4:
        return jsonify({"ok": False, "error": "Password must be at least 4 characters"}), 400

    # Validate invite
    invites = get_invites()
    invite = invites.get(invite_code)
    if not invite:
        return jsonify({"ok": False, "error": "Invalid invite code"}), 403
    if invite.get("expires") and invite["expires"] < time.time():
        return jsonify({"ok": False, "error": "Invite code has expired"}), 403
    if invite.get("max_uses") and invite["used"] >= invite["max_uses"]:
        return jsonify({"ok": False, "error": "Invite code has been used too many times"}), 403

    # Check if user exists
    r = jf_get("/Users")
    if r.status_code == 200:
        if username.lower() in [u["Name"].lower() for u in r.json()]:
            return jsonify({"ok": False, "error": "Username already exists"}), 409

    # Create user
    ok, result = jf_create_user(username, password)
    if not ok:
        return jsonify({"ok": False, "error": result}), 500

    # Consume invite
    invite["used"] = invite.get("used", 0) + 1
    invite["last_used_by"] = username
    invite["last_used_at"] = time.time()
    invites[invite_code] = invite
    save_invites(invites)

    users = get_registered_users()
    users.append({
        "username": username,
        "jellyfin_id": result,
        "invite_code": invite_code,
        "registered_at": time.time(),
    })
    save_registered_users(users)

        # Authenticate as the new user and store token for QC authorization
    token, user_id = jf_authenticate_user(username, password)
    if token:
        session["user_token"] = token
        session["user_id"] = user_id
        session["qc_enabled"] = True

    return jsonify({
        "ok": True,
        "message": f"Account '{username}' created!",
        "jellyfin_url": get_public_url(),
        "qc_enabled": bool(token),
    })

@app.route("/api/login", methods=["POST"])
def api_login():
    """Authenticate an existing user and store token for QC authorization."""
    data = request.get_json()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username or not password:
        return jsonify({"ok": False, "error": "Username and password required"}), 400

    token, user_id = jf_authenticate_user(username, password)
    if token:
        session["user_token"] = token
        session["user_id"] = user_id
        session["qc_enabled"] = True

        return jsonify({
            "ok": True,
            "message": f"Welcome back, {username}!",
            "jellyfin_url": get_public_url(),
            "qc_enabled": True,
        })

    return jsonify({"ok": False, "error": "Invalid username or password"}), 401


@app.route("/api/qc/authorize", methods=["POST"])
def api_qc_authorize():
    """Authorize a Quick Connect code using the registered user session."""
    token = session.get("user_token")
    if not token:
        return jsonify({"ok": False, "error": "No active session"}), 401

    data = request.get_json()
    code = (data.get("code") or "").strip().upper()

    if len(code) != 6 or not code.isdigit():
        return jsonify({"ok": False, "error": "Code must be 6 digits"}), 400

    try:
        r = requests.post(
            f"{JELLYFIN_URL}/QuickConnect/Authorize",
            params={"code": code},
            headers={"X-Emby-Token": token},
            timeout=10,
        )
        if r.status_code == 200:
            return jsonify({"ok": True, "message": "Device authorized!"})
        elif r.status_code == 404:
            return jsonify({"ok": False, "error": "Code not found"}), 404
        elif r.status_code == 401:
            return jsonify({"ok": False, "error": "QC not available on this server"}), 503
        else:
            return jsonify({"ok": False, "error": f"Failed: {r.status_code}"}), 500
    except Exception:
        return jsonify({"ok": False, "error": "Network error"}), 500



@app.route("/api/admin/invite", methods=["POST"])
def api_create_invite():
    if "admin" not in session:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    data = request.get_json()
    max_uses = data.get("max_uses") or None
    expires_hours = data.get("expires_hours") or None

    code = secrets.token_urlsafe(8).upper()
    invite = {
        "created_at": time.time(),
        "max_uses": max_uses,
        "expires": time.time() + (expires_hours * 3600) if expires_hours else None,
        "used": 0,
    }

    invites = get_invites()
    invites[code] = invite
    save_invites(invites)

    return jsonify({"ok": True, "invite": {"code": code, **invite}})


@app.route("/api/admin/invite/<code>", methods=["DELETE"])
def api_delete_invite(code):
    if "admin" not in session:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    invites = get_invites()
    if code in invites:
        del invites[code]
        save_invites(invites)
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Invite not found"}), 404


@app.route("/api/admin/logout", methods=["POST"])
def api_admin_logout():
    session.pop("admin", None)
    return jsonify({"ok": True})


@app.route("/api/admin/settings", methods=["GET"])
def api_get_settings():
    if "admin" not in session:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    return jsonify({"ok": True, "settings": load_settings()})


@app.route("/api/admin/settings", methods=["POST"])
def api_save_settings():
    if "admin" not in session:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    data = request.get_json()
    settings = load_settings()
    for key in ["server_name", "jellyfin_url", "jellyfin_public_url"]:
        if key in data:
            settings[key] = data[key]
    save_settings(settings)
    return jsonify({"ok": True, "settings": settings})


@app.route("/api/admin/health")
def api_health():
    try:
        r = jf_get("/System/Info")
        if r.status_code == 200:
            info = r.json()
            return jsonify({
                "ok": True,
                "jellyfin": load_settings().get("server_name", "Jellyfin"),
                "version": info.get("Version", "?"),
            })
    except Exception:
        pass
    return jsonify({"ok": False, "error": "Cannot connect to Jellyfin"}), 503


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
