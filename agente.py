import os
import json
import time
import requests
import feedparser
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from google import genai

# Load environment variables
load_dotenv(override=True)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GOOGLE_SEARCH_API_KEY = os.getenv("GOOGLE_SEARCH_API_KEY")
GOOGLE_SEARCH_CX = os.getenv("GOOGLE_SEARCH_CX")
HISTORY_FILE = "seen_jobs.json"
print(f"🛠️ DEBUG: A chave enviada ao Google termina em: ...{GOOGLE_SEARCH_API_KEY[-4:]}")

# Initialize Gemini Client
client = None
if GEMINI_API_KEY:
    client = genai.Client(api_key=GEMINI_API_KEY)

# --- 1. CONFIGURATION & DOMAINS ---
KEYWORDS_TOPICS = [
    "autonomous navigation", "FastSLAM", "mobile robotics", 
    "control systems", "AI for robots", "SLAM", "ROS 2", "C++"
]

KEYWORDS_POSITIONS = [
    "PhD", "Doctoral Candidate", "Research Assistant"
]

RESEARCHER_PROFILE = f"""
Core Interests: {', '.join(KEYWORDS_TOPICS)}
Focus: Mobile robotics, autonomous navigation, SLAM/FastSLAM, control engineering, embedded systems, and AI.
Application Requirements: Fully Funded PhD Candidate / Research Assistant in Europe.
"""

# Alpine universities domains for prioritizing the Telegram alert
ALPINE_DOMAINS = [
    "unibz.it", "polito.it", "polimi.it", "unipd.it", "unibg.it",
    "unibs.it", "uniud.it", "unige.it", "univr.it", "units.it",
    "uninsubria.it", "grenoble-inp.fr", "insa-lyon.fr", "univ-smb.fr",
    "ec-lyon.fr", "artsetmetiers.fr", "epfl.ch", "ethz.ch",
    "supsi.ch", "hevs.ch", "bfh.ch", "uibk.ac.at", "mci.edu",
    "tugraz.at", "jku.at", "aau.at", "unileoben.ac.at",
    "tum.de", "hs-kempten.de", "th-rosenheim.de", "hm.edu", "uni-stuttgart.de"
]

# RSS Feeds as backup
RSS_FEEDS = [
    "https://academicpositions.com/jobs/robotics/rss",
    "https://academicpositions.com/jobs/engineering/rss"
]

# --- 2. MEMORY MANAGEMENT ---
def load_seen_jobs() -> set:
    """Loads previously analyzed URLs to prevent duplicates."""
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            try:
                return set(json.load(f))
            except json.JSONDecodeError:
                return set()
    return set()

def save_seen_jobs(seen_jobs: set):
    """Saves analyzed URLs to the JSON file."""
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen_jobs), f, indent=2)

# --- 3. FETCHING DATA ---
def fetch_jobs_from_google() -> list:
    """Uses Google Custom Search API to query predefined Alpine and European domains."""
    jobs = []
    if not GOOGLE_SEARCH_API_KEY or not GOOGLE_SEARCH_CX:
        print("⚠️ Google Search credentials missing. Skipping search.")
        return jobs

    print("🔎 Fetching jobs from Google Custom Search...")
    
    # Focused queries. The domains are already restricted in the Google CSE control panel.
    queries = [
        '"PhD" ("robotics" OR "SLAM" OR "FastSLAM")',
        '"PhD" ("autonomous navigation" OR "mobile robots")',
        '"Doctoral" ("control systems" OR "robotics")'
    ]
    
    url = "https://www.googleapis.com/customsearch/v1"

    for query in queries:
        params = {
            "key": GOOGLE_SEARCH_API_KEY,
            "cx": GOOGLE_SEARCH_CX,
            "q": query,
            "dateRestrict": "d60", # Expanded to 60 days to catch recent postings
            "num": 10
        }
        
        try:
            response = requests.get(url, params=params, timeout=15)
            data = response.json()
            
            if "error" in data:
                print(f"❌ Google API Error: {data['error'].get('message')}")
                continue

            items = data.get("items", [])
            print(f"   ↳ Query '{query[:30]}...': found {len(items)} result(s)")
            
            for item in items:
                link = item.get("link", "")
                
                # Check if it's an Alpine domain to tag it correctly for the priority alert
                is_alpine = any(domain in link for domain in ALPINE_DOMAINS)
                
                jobs.append({
                    "title": item.get("title", ""),
                    "description": item.get("snippet", ""),
                    "link": link,
                    "source": "Alpine Domains" if is_alpine else "General European Portals"
                })
        except Exception as e:
            print(f"❌ Google Search Exception: {e}")
            
    return jobs

def fetch_jobs_from_rss() -> list:
    """Fetches job listings from defined RSS feeds."""
    jobs = []
    print("📡 Fetching jobs from RSS feeds...")
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:10]:
                soup = BeautifulSoup(entry.get("description", entry.get("summary", "")), "html.parser")
                jobs.append({
                    "title": entry.title,
                    "description": soup.get_text(separator=" ", strip=True),
                    "link": entry.link,
                    "source": "RSS"
                })
        except Exception as e:
            print(f"❌ Error fetching feed {feed_url}: {e}")
    return jobs

# --- 4. TELEGRAM ALERT ---
def send_telegram_alert(message: str):
    """Sends a Markdown formatted message to the Telegram bot."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"❌ Telegram connection error: {e}")

# --- 5. AI EVALUATION ---
def evaluate_job_with_ai(title: str, description: str, link: str = "") -> dict:
    """Analyzes the job using Gemini AI."""
    if not client:
        return None

    prompt = f"""
    You are an expert scientific and academic recruiter in Europe.
    Analyze the PhD position below and verify its alignment with the researcher's profile.

    --- CANDIDATE PROFILE ---
    {RESEARCHER_PROFILE}

    --- JOB DATA ---
    Title: {title}
    Description: {description}
    Link: {link}

    --- RESPONSE RULES ---
    Return STRICTLY a valid JSON object in the following format:
    {{
        "score_match": <integer from 0 to 100>,
        "country": "<Country of the position>",
        "institution": "<Name of the University, Lab, or Institute>",
        "funded": "<Yes / No / Unspecified>",
        "project_summary": "<Clear 2-sentence summary of the project and technologies>",
        "match_reason": "<Brief explanation of why it fits or does not fit the profile keywords>",
        "recommend": <true if score_match >= 70 and funded != "No", otherwise false>
    }}
    """

    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model="gemini-3.6-flash",
                contents=prompt,
            )
            
            text_parts = [part.text for part in response.candidates[0].content.parts if part.text]
            response_text = "".join(text_parts).strip()
            response_text = response_text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            
            return json.loads(response_text)

        except Exception as e:
            if "503" in str(e) and attempt < 2:
                time.sleep((attempt + 1) * 3)
            else:
                return None
    return None

# --- 6. MAIN PROCESSING PIPELINE ---
def process_jobs(job_list: list):
    seen_jobs = load_seen_jobs()
    new_jobs_count = 0

    print(f"🔍 Found {len(job_list)} job(s) in total. Processing new ones...")

    for job in job_list:
        link = job.get("link", "")
        
        if link in seen_jobs or not link:
            continue

        new_jobs_count += 1
        print(f"\n--- Analyzing: {job.get('title')} ---")

        evaluation = evaluate_job_with_ai(
            title=job.get("title", ""),
            description=job.get("description", ""),
            link=link
        )

        seen_jobs.add(link)

        if not evaluation:
            continue

        score = evaluation.get("score_match", 0)
        is_alpine_source = job.get("source") == "Alpine Domains"
        recommend = evaluation.get("recommend", False)

        print(f"Score: {score}% | Recommend: {recommend} | Source: {job.get('source')}")

        if recommend or is_alpine_source:
            header = "🏔️ *TARGET ALPINE UNIVERSITY ALERT!*" if is_alpine_source else "🎯 *New Recommended PhD Position!*"
            
            message = (
                f"{header}\n\n"
                f"📌 *Title:* {job.get('title')}\n"
                f"🏛️ *Institution:* {evaluation.get('institution', 'Unknown')}\n"
                f"🌍 *Country:* {evaluation.get('country', 'Europe')}\n"
                f"💰 *Funded:* {evaluation.get('funded', 'N/A')}\n"
                f"📊 *Match Score:* {score}%\n\n"
                f"📝 *Summary:* {evaluation.get('project_summary')}\n\n"
                f"💡 *Why:* {evaluation.get('match_reason')}\n\n"
                f"🔗 [Apply / View Position]({link})"
            )
            send_telegram_alert(message)

    save_seen_jobs(seen_jobs)
    print(f"\n✅ Pipeline Finished. {new_jobs_count} new jobs analyzed.")

# --- 7. EXECUTION ---
if __name__ == "__main__":
    print("🚀 Starting PhD Agent Pipeline...")
    all_jobs = []
    
    # 1. Fetch from Google Custom Search (All domains configured in panel)
    all_jobs.extend(fetch_jobs_from_google())
    
    # 2. Fetch from RSS Feeds (Backup)
    all_jobs.extend(fetch_jobs_from_rss())
    
    process_jobs(all_jobs)