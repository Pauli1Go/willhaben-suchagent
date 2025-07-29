import requests
from bs4 import BeautifulSoup
import time
import re
import json
import threading
import os
from copy import deepcopy

# ------------- Defaults -------------
DEFAULT_CONFIG = {
    "urls": [
    ],
    "refresh_seconds": 180,
    "ntfy_enabled": True,
    "ntfy_topic": "willhaben-crawler",
    "known_ids": {}
}

CONFIG_FILE = "config.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/117.0 Safari/537.36"
    )
}



_config_lock = threading.RLock()


def load_config():
    if not os.path.isfile(CONFIG_FILE):
        return deepcopy(DEFAULT_CONFIG)

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    cfg = deepcopy(DEFAULT_CONFIG)
    cfg.update(data)

    cfg.setdefault("known_ids", {})
    cfg.setdefault("urls", [])
    return cfg


def save_config(cfg):
    with _config_lock:
        safe_cfg = deepcopy(cfg)
        safe_cfg["known_ids"] = {u: list(set(ids)) for u, ids in safe_cfg["known_ids"].items()}
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(safe_cfg, f, ensure_ascii=False, indent=2)


# ---------- ntfy ----------
def send_ntfy_notification(cfg, title, body, link):
    if not cfg.get("ntfy_enabled", True):
        return
    topic = cfg.get("ntfy_topic", DEFAULT_CONFIG["ntfy_topic"])
    try:
        requests.post(
            f"https://ntfy.sh/{topic}",
            data=f"{body}\n{link}".encode("utf-8"),
            headers={
                "Title": title.encode("utf-8"),
                "Actions": f"view,Zum Angebot,{link}",
                "Content-Type": "text/plain; charset=utf-8"
            },
            timeout=10
        )
    except Exception as e:
        print(f"[ntfy] Fehler beim Senden: {e}")


_price_regex = re.compile(r'^\d{7,}$')


def scan_single_url(cfg, url, known_ids):
    """gibt (neue_known_ids_set, list( (title, body, link) )) zurück"""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"[{url}] Fehler beim Abrufen: {e}")
        return known_ids, []

    new_found = []
    seen_ids = set(known_ids)

    for div in soup.find_all("div", id=_price_regex):
        ad_id = div.get("id")
        seen_ids.add(ad_id)

        if ad_id in known_ids:
            continue

        a_tag = div.find("a", href=True)
        if not a_tag:
            continue
        link = "https://www.willhaben.at" + a_tag["href"]

        h3 = div.find("h3")
        title_txt = h3.get_text(strip=True) if h3 else "(kein Titel)"

        price_tag = div.find("span", attrs={"data-testid": f"search-result-entry-price-{ad_id}"})
        price_txt = price_tag.get_text(strip=True) if price_tag else "(kein Preis)"

        full_title = f"{price_txt} {title_txt}"
        body = full_title

        print(f"[{url}]\n  NEUE ANZEIGE: {full_title}\n  {link}\n")
        new_found.append((full_title, body, link))

    return seen_ids, new_found


def start_crawler_thread(cfg, stop_event):
    scan_counts = {}

    while not stop_event.is_set():
        with _config_lock:
            current_urls = list(cfg["urls"])

        for u in current_urls:
            scan_counts.setdefault(u, 0)
            cfg["known_ids"].setdefault(u, [])

        for u in list(scan_counts.keys()):
            if u not in current_urls:
                scan_counts.pop(u, None)

        for idx, url in enumerate(current_urls):
            with _config_lock:
                prev_ids = set(cfg["known_ids"].get(url, []))

            send_allowed = scan_counts[url] > 0 and bool(prev_ids)
            scan_counts[url] += 1

            all_ids, new_items = scan_single_url(cfg, url, prev_ids)

            if new_items and send_allowed:
                for title, body, link in new_items:
                    send_ntfy_notification(cfg, title, body, link)

            if all_ids != prev_ids:
                with _config_lock:
                    cfg["known_ids"][url] = list(all_ids)
                    save_config(cfg)

            if idx + 1 < len(current_urls):
                time.sleep(2)

        time.sleep(cfg.get("refresh_seconds", DEFAULT_CONFIG["refresh_seconds"]))


# ---------- Menü ----------
def print_menu():
    print(
        "\n--- Willhaben-Crawler ---\n"
        "1) Getrackte URLs anzeigen\n"
        "2) URL hinzufügen\n"
        "3) URL entfernen\n"
        "4) ntfy Benachrichtigungen an/aus\n"
        "5) ntfy Topic ändern\n"
        "6) Refresh-Intervall ändern\n"
        "7) Beenden\n"
    )


def menu():
    cfg = load_config()
    save_config(cfg)

    stop_evt = threading.Event()
    crawler = threading.Thread(target=start_crawler_thread, args=(cfg, stop_evt), daemon=True)
    crawler.start()

    while True:
        print_menu()
        choice = input("Option: ").strip()

        if choice == "1":
            with _config_lock:
                for i, u in enumerate(cfg["urls"], 1):
                    print(f" {i}) {u}   ({len(cfg['known_ids'].get(u, []))} IDs bekannt)")
        elif choice == "2":
            new_url = input("Neue URL: ").strip()
            if new_url:
                with _config_lock:
                    if new_url in cfg["urls"]:
                        print("URL bereits vorhanden.")
                    else:
                        cfg["urls"].append(new_url)
                        cfg["known_ids"][new_url] = []
                        save_config(cfg)
                        print("Hinzugefügt.")
        elif choice == "3":
            with _config_lock:
                for i, u in enumerate(cfg["urls"], 1):
                    print(f" {i}) {u}")
            try:
                idx = int(input("Index löschen: ").strip()) - 1
                with _config_lock:
                    url = cfg["urls"].pop(idx)
                    cfg["known_ids"].pop(url, None)
                    save_config(cfg)
                    print("Gelöscht.")
            except Exception:
                print("Ungültige Auswahl.")
        elif choice == "4":
            with _config_lock:
                cfg["ntfy_enabled"] = not cfg.get("ntfy_enabled", True)
                save_config(cfg)
                print("ntfy ist jetzt", "AKTIV" if cfg["ntfy_enabled"] else "AUS")
        elif choice == "5":
            new_topic = input("Neue ntfy Topic: ").strip()
            if new_topic:
                with _config_lock:
                    cfg["ntfy_topic"] = new_topic
                    save_config(cfg)
        elif choice == "6":
            try:
                secs = int(input("Sekunden (≥10): ").strip())
                if secs >= 10:
                    with _config_lock:
                        cfg["refresh_seconds"] = secs
                        save_config(cfg)
                else:
                    print("Zu kurz.")
            except ValueError:
                print("Ungültig.")
        elif choice == "7":
            print("Beende …")
            stop_evt.set()
            crawler.join(2)
            break
        else:
            print("Ungültige Option.")


if __name__ == "__main__":
    try:
        menu()
    except KeyboardInterrupt:
        print("\nAbbruch durch Benutzer.")