#!/usr/bin/env python3
"""Fetch new reviews from Telegram channel and append to reviews.json.

Uses Bot API getUpdates — must run at least once per 24h to not miss messages.
Bot must be an admin in the channel.
"""

import json
import os
import ssl
import urllib.request
import time

# SSL context for environments where default certs aren't available (launchd)
SSL_CTX = ssl.create_default_context()
try:
    import certifi
    SSL_CTX.load_verify_locations(certifi.where())
except Exception:
    SSL_CTX.check_hostname = False
    SSL_CTX.verify_mode = ssl.CERT_NONE

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8707947697:AAFMK29znaHUi80_iW_AEvDZrBnBzcoKpR0")
CHANNEL_ID = -1001845694815

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REVIEWS_FILE = os.path.join(BASE_DIR, "reviews.json")
USERS_MAP_FILE = os.path.join(BASE_DIR, "users_map.json")
IMAGES_DIR = os.path.join(BASE_DIR, "images")
AVATARS_DIR = os.path.join(BASE_DIR, "avatars")
LOG_FILE = os.path.join(BASE_DIR, "update.log")
OFFSET_FILE = os.path.join(BASE_DIR, ".update_offset")

DATE_CUTOFF = 1719792000  # 2024-07-01 UTC


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def api(method, params=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    with urllib.request.urlopen(url, timeout=30, context=SSL_CTX) as r:
        return json.loads(r.read())


def download_file(file_id, save_path):
    try:
        info = api("getFile", {"file_id": file_id})
        fpath = info["result"]["file_path"]
        url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{fpath}"
        with urllib.request.urlopen(url, timeout=30, context=SSL_CTX) as r:
            with open(save_path, "wb") as out:
                out.write(r.read())
        return True
    except Exception as e:
        log(f"  Download failed: {e}")
        return False


def get_sender(msg):
    if "forward_origin" in msg:
        o = msg["forward_origin"]
        if o.get("type") == "user":
            u = o["sender_user"]
            name = u.get("first_name", "")
            if u.get("last_name"):
                name += " " + u["last_name"]
            return name.strip(), u.get("username", ""), u.get("id")
    if "from" in msg:
        u = msg["from"]
        name = u.get("first_name", "")
        if u.get("last_name"):
            name += " " + u["last_name"]
        return name.strip(), u.get("username", ""), u.get("id")
    return "", "", None


def fetch_avatar(user_id):
    if not user_id:
        return ""
    path = os.path.join(AVATARS_DIR, f"{user_id}.jpg")
    if os.path.exists(path):
        return f"avatars/{user_id}.jpg"
    try:
        r = api("getUserProfilePhotos", {"user_id": str(user_id), "limit": "1"})
        if r.get("ok") and r["result"]["total_count"] > 0:
            fid = r["result"]["photos"][0][-1]["file_id"]
            if download_file(fid, path):
                return f"avatars/{user_id}.jpg"
    except Exception:
        pass
    return ""


def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_offset():
    if os.path.exists(OFFSET_FILE):
        return int(open(OFFSET_FILE).read().strip())
    return 0


def save_offset(offset):
    with open(OFFSET_FILE, "w") as f:
        f.write(str(offset))


def main():
    log("Starting update...")
    os.makedirs(IMAGES_DIR, exist_ok=True)
    os.makedirs(AVATARS_DIR, exist_ok=True)

    reviews = load_json(REVIEWS_FILE, [])
    users_map = load_json(USERS_MAP_FILE, {})

    # Clean up any existing "Unknown" senders
    for r in reviews:
        if r.get("sender") == "Unknown":
            r["sender"] = ""
            log(f"  Cleaned Unknown sender for msg_id={r['msg_id']}")

    existing_ids = {r["msg_id"] for r in reviews}
    offset = load_offset()

    params = {"timeout": "5", "allowed_updates": '["channel_post"]'}
    if offset:
        params["offset"] = str(offset)

    try:
        result = api("getUpdates", params)
    except Exception as e:
        log(f"API error: {e}")
        return

    if not result.get("ok"):
        log(f"getUpdates failed: {result}")
        return

    updates = result.get("result", [])
    log(f"Received {len(updates)} updates")

    new_reviews = []
    new_offset = offset

    for upd in updates:
        new_offset = upd["update_id"] + 1
        post = upd.get("channel_post")
        if not post:
            continue
        if post.get("chat", {}).get("id") != CHANNEL_ID:
            continue

        msg_id = post["message_id"]
        if msg_id in existing_ids:
            continue

        date = post.get("date", 0)
        if date < DATE_CUTOFF:
            continue

        sender, username, user_id = get_sender(post)

        photo_path = ""
        review_type = "text"
        if "photo" in post:
            review_type = "photo"
            fid = post["photo"][-1]["file_id"]
            img_file = f"review_{msg_id}.jpg"
            if download_file(fid, os.path.join(IMAGES_DIR, img_file)):
                photo_path = f"images/{img_file}"

        review = {
            "msg_id": msg_id,
            "sender": sender,
            "username": username,
            "text": post.get("text", ""),
            "caption": post.get("caption", ""),
            "photo": photo_path,
            "type": review_type,
            "date": date,
        }

        avatar = fetch_avatar(user_id)
        if avatar:
            review["avatar"] = avatar

        if user_id:
            parts = sender.split() if sender else [""]
            users_map[str(msg_id)] = {
                "user_id": user_id,
                "first_name": parts[0],
                "last_name": " ".join(parts[1:]) if len(parts) > 1 else "",
                "username": username,
            }

        new_reviews.append(review)
        existing_ids.add(msg_id)
        log(f"  + #{msg_id} from {sender}")

    save_offset(new_offset)

    if new_reviews:
        reviews = new_reviews + reviews
        reviews.sort(key=lambda r: r["date"], reverse=True)
        save_json(REVIEWS_FILE, reviews)
        save_json(USERS_MAP_FILE, users_map)
        log(f"Added {len(new_reviews)} new reviews. Total: {len(reviews)}")
    else:
        log("No new reviews.")


if __name__ == "__main__":
    main()
