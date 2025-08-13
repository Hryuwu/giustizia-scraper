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
        
    def scrape(self, year, start_num, end_num, keywords, precision):
        """Main scraping function"""
        try:
            BASE_URL = "https://www.giustizia-amministrativa.it/web/guest/ricorsi-cds"
            self.total_searches = end_num - start_num + 1
            self.current_progress = 0
            self.results = {}
            self.is_running = True
            
            # Emit initial progress
            socketio.emit('progress_update', {
                'current': 0,from flask import Flask, render_template, request, jsonify
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
        
def scrape(self, year, start_num, end_num, keywords, precision):
    """Main scraping function"""

    BASE_URL = "https://www.giustizia-amministrativa.it/web/guest/ricorsi-cds"
    self.total_searches = end_num - start_num + 1
    self.current_progress = 0
    self.results = {}
    self.is_running = True

    socketio.emit('progress_update', {
        'current': 0,
        'total': self.total_searches,
        'percentage': 0,
        'status': 'Inizializzo il browser...'
    }, room=self.session_id)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
            page = browser.new_page(ignore_https_errors=True)

            for num in range(start_num, end_num + 1):
                if not self.is_running:
                    break

                number_str = f"{num:05d}"

                # Emit progress update
                socketio.emit('progress_update', {
                    'current': self.current_progress,
                    'total': self.total_searches,
                    'percentage': (self.current_progress / self.total_searches) * 100,
                    'status': f'Ricerca ricorso {year}{number_str}...'
                }, room=self.session_id)

                try:
                    # Retry page load up to 3 times
                    max_retries = 3
                    for attempt in range(max_retries):
                        try:
                            page.goto(BASE_URL, wait_until="networkidle", timeout=15000)
                            break
                        except Exception as e:
                            if attempt == max_retries - 1:
                                raise
                            logger.warning(f"Retry {attempt + 1} loading page for {year}{number_str}: {e}")
                            time.sleep(1)

                    # Wait for form ready
                    page.wait_for_selector(
                        'select#_it_indra_ga_institutional_area_JurisdictionalActivityAppealsWebPortlet_INSTANCE_P4XO16kCEH4o_year',
                        timeout=10000)

                    # Clear cookies and storage to avoid stale session state
                    page.context.clear_cookies()
                    page.evaluate("() => localStorage.clear()")
                    page.evaluate("() => sessionStorage.clear()")

                    # Fill in year and number fields
                    page.select_option(
                        'select#_it_indra_ga_institutional_area_JurisdictionalActivityAppealsWebPortlet_INSTANCE_P4XO16kCEH4o_year',
                        str(year))
                    number_input = page.locator(
                        'input[name*="_it_indra_ga_institutional_area_JurisdictionalActivityAppealsWebPortlet_INSTANCE_P4XO16kCEH4o_number"]')
                    number_input.fill(number_str)

                    # Click the search button
                    search_button = page.locator(
                        'button[name="_it_indra_ga_institutional_area_JurisdictionalActivityAppealsWebPortlet_INSTANCE_P4XO16kCEH4o_search"]')
                    search_button.click()

                    # Wait for the results or a no-result indication
                    try:
                        page.wait_for_selector('#valoreOggetto', timeout=15000)
                    except PlaywrightTimeoutError:
                        logger.warning(f"No results or timeout waiting for #valoreOggetto in {year}{number_str}")
                        self.current_progress += 1
                        continue

                    # Wait until #valoreOggetto has non-empty text
                    try:
                        page.wait_for_function(
                            """() => {
                                const el = document.querySelector('#valoreOggetto');
                                return el && el.innerText && el.innerText.trim().length > 0;
                            }""",
                            timeout=3000)
                    except PlaywrightTimeoutError:
                        logger.warning(f"Empty or missing content after wait_for_function for {year}{number_str}")

                    # Get text content and normalize for matching
                    text_content = page.inner_text('#valoreOggetto').lower().strip()

                    if not text_content:
                        logger.info(f"No content for {year}{number_str}")
                        self.current_progress += 1
                        continue

                    logger.info(f"Processing {year}{number_str}, content length: {len(text_content)}")

                    found_in_this_case = False
                    for keyword in keywords:
                        score = fuzz.partial_ratio(keyword, text_content)
                        exact_match = keyword in text_content

                        if score >= precision or exact_match:
                            if keyword not in self.results:
                                self.results[keyword] = []
                            if number_str not in self.results[keyword]:
                                self.results[keyword].append(number_str)
                            found_in_this_case = True
                            logger.info(f"FOUND: '{keyword}' in {year}{number_str} (score: {score}, exact: {exact_match})")

                            socketio.emit('result_found', {
                                'keyword': keyword,
                                'case_number': f'{year}{number_str}',
                                'year': year,
                                'match_score': score,
                                'exact_match': exact_match
                            }, room=self.session_id)

                    if not found_in_this_case:
                        logger.debug(f"No keywords found in {year}{number_str}")

                except Exception as e:
                    logger.error(f"Error processing {year}{number_str}: {e}")

                self.current_progress += 1
                # Delay between requests to be polite and avoid rate limits
                time.sleep(random.uniform(1.0, 2.0))

            browser.close()

            # Emit scrape complete with results summary
            socketio.emit('scraping_complete', {
                'results': self.results,
                'total_found': sum(len(numbers) for numbers in self.results.values())
            }, room=self.session_id)

    except Exception as e:
        logger.error(f"Scraping error: {str(e)}")
        socketio.emit('scraping_error', {'error': str(e)}, room=self.session_id)

    finally:
        self.is_running = False
        if self.session_id in scraping_sessions:
            del scraping_sessions[self.session_id]


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
                'total': self.total_searches,
                'percentage': 0,
                'status': 'Inizializzo il browser...'
            }, room=self.session_id)
            
            with sync_playwright() as p:
                # Use chromium instead of firefox for better Railway compatibility
                browser = p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
                page = browser.new_page(ignore_https_errors=True)
                
                for num in range(start_num, end_num + 1):
                    if not self.is_running:
                        break
                        
                    number_str = f"{num:05d}"
                    
                    # Update progress
                    socketio.emit('progress_update', {
                        'current': self.current_progress,
                        'total': self.total_searches,
                        'percentage': (self.current_progress / self.total_searches) * 100,
                        'status': f'Ricerca ricorso {year}{number_str}...'
                    }, room=self.session_id)
                    
                    try:
                        # More robust page loading with retries
                        max_retries = 3
                        for attempt in range(max_retries):
                            try:
                                page.goto(BASE_URL, wait_until="networkidle", timeout=15000)
                                break
                            except:
                                if attempt == max_retries - 1:
                                    raise
                                logger.warning(f"Retry {attempt + 1} for loading page")
                                time.sleep(1)
                        
                        # Wait for form to be ready
                        page.wait_for_selector('select#_it_indra_ga_institutional_area_JurisdictionalActivityAppealsWebPortlet_INSTANCE_P4XO16kCEH4o_year', timeout=10000)
                        
                        # Fill the form with better error handling
                        page.select_option('select#_it_indra_ga_institutional_area_JurisdictionalActivityAppealsWebPortlet_INSTANCE_P4XO16kCEH4o_year', str(year))
                        
                        # Clear and fill the number field
                        number_input = page.locator('input[name*="_it_indra_ga_institutional_area_JurisdictionalActivityAppealsWebPortlet_INSTANCE_P4XO16kCEH4o_number"]')
                        number_input.clear()
                        number_input.fill(number_str)
                        
                        # Wait a bit before clicking search
                        time.sleep(0.5)
                        
                        # Click search with retry logic
                        search_button = page.locator('button[name="_it_indra_ga_institutional_area_JurisdictionalActivityAppealsWebPortlet_INSTANCE_P4XO16kCEH4o_search"]')
                        search_button.click()
                        
                        # Wait for results with longer timeout and better error handling
                        try:
                            page.wait_for_selector('#valoreOggetto', timeout=15000)
                            
                            # Wait a bit more to ensure content is fully loaded
                            time.sleep(5)
                            
                            # Get text content with error handling
                            text_content = ""
                            try:
                                text_content = page.inner_text('#valoreOggetto').lower()
                            except:
                                # Fallback: try to get any visible text
                                text_content = page.text_content('#valoreOggetto').lower() if page.query_selector('#valoreOggetto') else ""
                            
                            if not text_content.strip():
                                logger.warning(f"Empty content for {year}{number_str}")
                                continue
                                
                            # Debug logging
                            logger.info(f"Processing {year}{number_str}, content length: {len(text_content)}")
                            
                            # Check for keywords with improved logic
                            found_in_this_case = False
                            for keyword in keywords:
                                matched, score, method = match_keyword(keyword, text_content, threshold=precision)
                                if matched:
                                    if keyword not in self.results:
                                        self.results[keyword] = []
                                    if number_str not in self.results[keyword]:
                                        self.results[keyword].append(number_str)
                                    found_in_this_case = True
                                    logger.info(f"FOUND: '{keyword}' in {year}{number_str} (score: {score}, method: {method})")

                                    socketio.emit('result_found', {
                                        'keyword': keyword,
                                        'case_number': f'{year}{number_str}',
                                        'year': year,
                                        'match_score': score,
                                        'match_method': method
                                    }, room=self.session_id)

                            if not found_in_this_case:
                                logger.debug(f"No keywords found in {year}{number_str}")
                                
                        except Exception as wait_error:
                            logger.warning(f"No results found for {year}{number_str}: {str(wait_error)}")
                            continue
                                
                    except Exception as e:
                        logger.error(f"Error processing {year}{number_str}: {str(e)}")
                        # Continue with next case instead of stopping
                        continue
                    
                    self.current_progress += 1
                    # Longer sleep to be more respectful to the server and more reliable
                    time.sleep(random.uniform(0.8, 1.5))
                
                browser.close()
                
            # Emit completion
            socketio.emit('scraping_complete', {
                'results': self.results,
                'total_found': sum(len(numbers) for numbers in self.results.values())
            }, room=self.session_id)
            
        except Exception as e:
            logger.error(f"Scraping error: {str(e)}")
            socketio.emit('scraping_error', {
                'error': str(e)
            }, room=self.session_id)
        finally:
            self.is_running = False
            if self.session_id in scraping_sessions:
                del scraping_sessions[self.session_id]

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
