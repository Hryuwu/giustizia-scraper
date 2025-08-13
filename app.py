import os
import time
import logging
import threading
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
from rapidfuzz import fuzz
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# ----- Flask / Socket.IO setup -----
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("giustizia")

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret")
socketio = SocketIO(app, cors_allowed_origins="*")  # works on Railway behind proxy

BASE_URL = "https://www.giustizia-amministrativa.it/web/guest/ricorsi-cds"

# ----- Scraper class -----
class GiustiziaScraper:
    def __init__(self, socketio, sid):
        self.socketio = socketio
        self.sid = sid  # send events back only to this client

    def emit(self, event, data):
        self.socketio.emit(event, data, to=self.sid)

    def best_fuzz(self, keyword: str, text: str) -> int:
        # robust matching using multiple algorithms
        k = keyword.lower().strip()
        t = text.lower().strip()
        scores = (
            fuzz.partial_ratio(k, t),
            fuzz.token_set_ratio(k, t),
            fuzz.token_sort_ratio(k, t),
        )
        return int(max(scores))

    def scrape(self, year: int, start_num: int, end_num: int, keywords: list[str], threshold: int):
        start_time = time.time()
        total = max(0, end_num - start_num + 1)
        done = 0

        # Normalize keywords once
        keywords = [k.strip() for k in keywords if k and k.strip()]

        with sync_playwright() as p:
            # Firefox tends to work well here; ignore_https_errors handles that cert issue you saw
            browser = p.firefox.launch(headless=True)
            page = browser.new_page(ignore_https_errors=True)

            for num in range(start_num, end_num + 1):
                number_str = f"{num:05d}"
                elapsed = time.time() - start_time
                pct = 0 if total == 0 else (done / total) * 100.0

                self.emit("progress_update", {
                    "current": done,
                    "total": total,
                    "percentage": pct,
                    "status": f"Ricerca {year}{number_str}… Tempo trascorso: {elapsed:.1f}s"
                })

                try:
                    # 1) open search page
                    page.goto(BASE_URL, wait_until="domcontentloaded")

                    # 2) fill/select form
                    page.select_option(
                        'select#_it_indra_ga_institutional_area_JurisdictionalActivityAppealsWebPortlet_INSTANCE_P4XO16kCEH4o_year',
                        str(year)
                    )
                    page.fill(
                        'input#_it_indra_ga_institutional_area_JurisdictionalActivityAppealsWebPortlet_INSTANCE_P4XO16kCEH4o_number',
                        number_str
                    )

                    # 3) click search
                    # it's a <button name="..._search"> or <input name="..._search">
                    page.click('button[name="_it_indra_ga_institutional_area_JurisdictionalActivityAppealsWebPortlet_INSTANCE_P4XO16kCEH4o_search"], input[name="_it_indra_ga_institutional_area_JurisdictionalActivityAppealsWebPortlet_INSTANCE_P4XO16kCEH4o_search"]')

                    # 4) wait for the result content
                    test_logging
                    try:
                        test_logging = page.wait_for_selector("#valoreOggetto", timeout=15000)
                    except PlaywrightTimeoutError:
                        # logger.warning("Timeout: #valoreOggetto missing for %s%s", year, number_str)
                        logger.warning(test_logging)
                        self.emit("timeout_warning", {
                            "case_number": f"{year}{number_str}",
                            "reason": "valoreOggetto non trovato entro il tempo limite"
                        })
                        done += 1
                        # polite delay to avoid hammering
                        time.sleep(1.5)
                        continue

                    # 5) extract content and show it raw
                    try:
                        raw = (page.inner_text("#valoreOggetto") or "").strip()
                    except Exception:
                        raw = ""
                    self.emit("raw_content", {
                        "case_number": f"{year}{number_str}",
                        "content": raw
                    })

                    # 6) fuzzy match against all keywords
                    if raw:
                        for kw in keywords:
                            score = self.best_fuzz(kw, raw)
                            if score >= threshold:
                                self.emit("match_found", {
                                    "case_number": f"{year}{number_str}",
                                    "keyword": kw,
                                    "score": score,
                                    "content": raw
                                })
                                # you can break if you only care about first match
                                # break

                    # 7) slow down slightly for stability
                    time.sleep(2.0)

                except Exception as e:
                    logger.exception("Errore su %s%s: %s", year, number_str, e)

                finally:
                    done += 1
                    # update progress after finishing this iteration
                    pct = 0 if total == 0 else (done / total) * 100.0
                    self.emit("progress_update", {
                        "current": done,
                        "total": total,
                        "percentage": pct,
                        "status": f"Completato {year}{number_str}"
                    })

            browser.close()

# ----- Flask routes & Socket.IO events -----
@app.route("/")
def index():
    return render_template("index.html")

@socketio.on("connect")
def on_connect():
    logger.info("Client connected: %s", request.sid)
    emit("progress_update", {"percentage": 0, "status": "Connesso"})

@socketio.on("disconnect")
def on_disconnect():
    logger.info("Client disconnected: %s", request.sid)

@socketio.on("start_search")
def on_start_search(data):
    """
    Payload from the page:
      {
        year: 2025,
        start_num: 1,
        end_num: 50,
        fuzz_threshold: 80,
        keywords: ["parola1", "parola2", ...]
      }
    """
    try:
        year = int(data.get("year"))
        start_num = int(data.get("start_num"))
        end_num = int(data.get("end_num"))
        threshold = int(data.get("fuzz_threshold", 80))
        keywords = data.get("keywords", [])
    except Exception:
        emit("timeout_warning", {"case_number": "N/A", "reason": "Parametri non validi"})
        return

    scraper = GiustiziaScraper(socketio, request.sid)
    # Run in a background task so we don't block the Socket.IO server
    socketio.start_background_task(
        scraper.scrape, year, start_num, end_num, keywords, threshold
    )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    # socketio.run chooses the best async mode (eventlet/gevent/threading).
    # On Railway, add 'eventlet' to requirements and it'll take it.
    socketio.run(app, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True)
