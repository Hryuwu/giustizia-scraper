from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
import time
import random
import threading
import os
from rapidfuzz import fuzz
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
socketio = SocketIO(app, cors_allowed_origins="*")

# Global variables to track scraping status
scraping_sessions = {}

class GiustiziaScraper:
    def __init__(self, session_id):
        self.session_id = session_id
        self.is_running = False
        self.results = {}
        self.current_progress = 0
        self.total_searches = 0

def match_keyword(keyword, text, threshold=75):
    """
    Try to match `keyword` inside the `text` using exact and fuzzy matching.
    Returns (match_found: bool, best_score: int, method: str).

    - Exact substring match gets priority with score 100.
    - Uses partial_ratio, token_sort_ratio, and token_set_ratio from rapidfuzz.
    - Logs detailed info about scores for traceability.
    """

    keyword_norm = keyword.lower().strip()
    text_norm = text.lower().strip()

    # Exact match check
    if keyword_norm in text_norm:
        logger.info(f"Exact match found for '{keyword}' (score=100)")
        return True, 100, 'exact'

    # Fuzzy matching
    partial = fuzz.partial_ratio(keyword_norm, text_norm)
    token_sort = fuzz.token_sort_ratio(keyword_norm, text_norm)
    token_set = fuzz.token_set_ratio(keyword_norm, text_norm)

    best_score = max(partial, token_sort, token_set)

    logger.info(
        f"Fuzzy match scores for '{keyword}': partial_ratio={partial}, "
        f"token_sort_ratio={token_sort}, token_set_ratio={token_set}, best={best_score}"
    )

    if best_score >= threshold:
        # Decide best matching method
        if best_score == partial:
            method = 'partial_ratio'
        elif best_score == token_sort:
            method = 'token_sort_ratio'
        else:
            method = 'token_set_ratio'
        return True, best_score, method

    return False, best_score, 'none'

def scrape(self, year, start_num, end_num, keywords, fuzz_threshold):
    start_time = time.time()
    self.current_progress = 0
    self.total_searches = end_num - start_num + 1

    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        page = browser.new_page(ignore_https_errors=True)

        for num in range(start_num, end_num + 1):
            elapsed = time.time() - start_time
            number_str = f"{num:05d}"

            # Progress update with elapsed time
            self.socketio.emit('progress_update', {
                'current': self.current_progress,
                'total': self.total_searches,
                'percentage': (self.current_progress / self.total_searches) * 100,
                'status': f'Ricerca ricorso {year}{number_str}... Tempo trascorso: {elapsed:.1f}s'
            }, room=self.session_id)

            try:
                # Go to search page
                page.goto(self.base_url, wait_until="domcontentloaded")

                # Select year and fill number
                page.select_option(
                    'select#_it_indra_ga_institutional_area_JurisdictionalActivityAppealsWebPortlet_INSTANCE_P4XO16kCEH4o_year',
                    str(year)
                )
                page.fill(
                    'input#_it_indra_ga_institutional_area_JurisdictionalActivityAppealsWebPortlet_INSTANCE_P4XO16kCEH4o_number',
                    number_str
                )

                # Click search
                page.click(
                    'button[name="_it_indra_ga_institutional_area_JurisdictionalActivityAppealsWebPortlet_INSTANCE_P4XO16kCEH4o_search"]'
                )

                # Wait for valoreOggetto
                try:
                    page.wait_for_selector('#valoreOggetto', timeout=15000)
                except PlaywrightTimeoutError:
                    logger.warning(f"Timeout: #valoreOggetto not found for {year}{number_str}")
                    self.socketio.emit('timeout_warning', {
                        'case_number': f'{year}{number_str}',
                        'reason': 'valoreOggetto not found in time'
                    }, room=self.session_id)
                    self.current_progress += 1
                    continue

                # Get raw valoreOggetto text
                text_content = page.inner_text('#valoreOggetto').strip()

                # Emit raw content to frontend
                self.socketio.emit('raw_content', {
                    'case_number': f'{year}{number_str}',
                    'content': text_content
                }, room=self.session_id)

                # Fuzz matching
                for keyword in keywords:
                    score = fuzz.partial_ratio(keyword.lower(), text_content.lower())
                    if score >= fuzz_threshold:
                        self.socketio.emit('match_found', {
                            'case_number': f'{year}{number_str}',
                            'keyword': keyword,
                            'score': score,
                            'content': text_content
                        }, room=self.session_id)

                # Delay between searches
                time.sleep(2.5)

            except Exception as e:
                logger.error(f"Error on {year}{number_str}: {e}")

            self.current_progress += 1

        browser.close()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/start_scraping', methods=['POST'])
def start_scraping():
    try:
        data = request.get_json()
        
        # Validate input
        year = int(data['year'])
        start_num = int(data['startNum'])
        end_num = int(data['endNum'])
        precision = int(data['precision'])
        keywords = [kw.strip().lower() for kw in data['keywords'].split('\n') if kw.strip()]
        session_id = data.get('sessionId', 'default')
        
        if not keywords:
            return jsonify({'error': 'No keywords provided'}), 400
        
        if start_num > end_num:
            return jsonify({'error': 'Start number must be less than or equal to end number'}), 400
        
        # Limit range to prevent abuse (optional)
        if end_num - start_num > 10000:
            return jsonify({'error': 'Range too large. Please limit to 10,000 records at a time.'}), 400
        
        # Check if already scraping
        if session_id in scraping_sessions:
            return jsonify({'error': 'Scraping already in progress'}), 400
        
        # Create scraper instance
        scraper = GiustiziaScraper(session_id)
        scraping_sessions[session_id] = scraper
        
        # Start scraping in background thread
        thread = threading.Thread(
            target=scraper.scrape,
            args=(year, start_num, end_num, keywords, precision)
        )
        thread.daemon = True
        thread.start()
        
        return jsonify({'message': 'Scraping started successfully', 'sessionId': session_id})
        
    except Exception as e:
        logger.error(f"Error starting scraping: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/stop_scraping', methods=['POST'])
def stop_scraping():
    try:
        data = request.get_json()
        session_id = data.get('sessionId', 'default')
        
        if session_id in scraping_sessions:
            scraping_sessions[session_id].is_running = False
            del scraping_sessions[session_id]
            return jsonify({'message': 'Scraping stopped'})
        else:
            return jsonify({'error': 'No active scraping session found'}), 404
            
    except Exception as e:
        logger.error(f"Error stopping scraping: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'active_sessions': len(scraping_sessions)})

@socketio.on('connect')
def handle_connect():
    logger.info(f'Client connected: {request.sid}')

@socketio.on('disconnect')
def handle_disconnect():
    logger.info(f'Client disconnected: {request.sid}')

@socketio.on('join_session')
def handle_join_session(data):
    session_id = data.get('sessionId', 'default')
    # Join the client to a room for their session
    from flask_socketio import join_room
    join_room(session_id)
    emit('joined_session', {'sessionId': session_id})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    # For production deployment, use allow_unsafe_werkzeug=True or use gunicorn
    socketio.run(app, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)