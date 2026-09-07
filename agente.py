import os
import json
import time
import requests
import feedparser
import re
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from google import genai

# Load environment variables (override=True garante a leitura do .env atual)
load_dotenv(override=True)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SERPER_API_KEY = os.getenv("SERPER_API_KEY")
HISTORY_FILE = "seen_jobs.json"

# Initialize Gemini Client
client = None
if GEMINI_API_KEY:
    client = genai.Client(api_key=GEMINI_API_KEY)

# --- 1. CONFIGURATION & DOMAINS ---
KEYWORDS_TOPICS = [
    "autonomous navigation", "FastSLAM", "mobile robotics", 
    "control systems", "AI for robots", "SLAM", "ROS 2", "C++"
]

RESEARCHER_PROFILE = f"""
Core Interests: {', '.join(KEYWORDS_TOPICS)}
Focus: Mobile robotics, autonomous navigation, SLAM/FastSLAM, control engineering, embedded systems, and AI.
Application Requirements: Fully Funded PhD Candidate / Research Assistant in Europe.
"""

# Alpine universities domains
ALPINE_DOMAINS = [
    "unibz.it", "polito.it", "polimi.it", "unipd.it", "unibg.it",
    "unibs.it", "uniud.it", "unige.it", "univr.it", "units.it",
    "uninsubria.it", "grenoble-inp.fr", "insa-lyon.fr", "univ-smb.fr",
    "ec-lyon.fr", "artsetmetiers.fr", "epfl.ch", "ethz.ch",
    "supsi.ch", "hevs.ch", "bfh.ch", "uibk.ac.at", "mci.edu",
    "tugraz.at", "jku.at", "aau.at", "unileoben.ac.at",
    "tum.de", "hs-kempten.de", "th-rosenheim.de", "hm.edu", "uni-stuttgart.de"
]

GENERAL_DOMAINS = [
    "euraxess.ec.europa.eu", "inria.fr", "abg.asso.fr", 
    "campusfrance.org", "robotics-worldwide.org"
]

RSS_FEEDS = [
    "https://academicpositions.com/jobs/robotics/rss",
    "https://academicpositions.com/jobs/engineering/rss"
]

# --- 2. MEMORY MANAGEMENT ---
def load_seen_jobs() -> set:
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            try:
                return set(json.load(f))
            except json.JSONDecodeError:
                return set()
    return set()

def save_seen_jobs(seen_jobs: set):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen_jobs), f, indent=2)

# --- 3. FETCHING DATA ---
def fetch_jobs_from_serper(domain_list: list, source_name: str) -> list:
    jobs = []
    if not SERPER_API_KEY:
        print(f"⚠️ Serper API Key missing. Skipping {source_name} search.")
        return jobs

    print(f"🔎 Fetching jobs via Serper.dev ({source_name})...")
    batch_size = 10 
    
    base_queries = [
        '"PhD" ("robotics" OR "SLAM" OR "FastSLAM")',
        '"PhD" ("autonomous navigation" OR "control systems")'
    ]

    url = "https://google.serper.dev/search"
    headers = {
        'X-API-KEY': SERPER_API_KEY,
        'Content-Type': 'application/json'
    }
    
    for base_query in base_queries:
        for i in range(0, len(domain_list), batch_size):
            batch = domain_list[i:i + batch_size]
            site_filters = " OR ".join([f"site:{domain}" for domain in batch])
            full_query = f'{base_query} ({site_filters})'
            
            payload = json.dumps({
                "q": full_query,
                "tbs": "qdr:d60", 
                "num": 10
            })
            
            try:
                response = requests.post(url, headers=headers, data=payload, timeout=15)
                data = response.json()
                
                for item in data.get("organic", []):
                    jobs.append({
                        "title": item.get("title", ""),
                        "description": item.get("snippet", ""),
                        "link": item.get("link", ""),
                        "source": source_name
                    })
            except Exception as e:
                print(f"❌ Serper API Error: {e}")
                
    return jobs

def fetch_jobs_from_rss() -> list:
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
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code != 200:
            print(f"❌ Telegram Error: {response.text}")
    except Exception as e:
        print(f"❌ Telegram connection error: {e}")

# --- 5. AI EVALUATION ---
def evaluate_job_with_ai(title: str, description: str, link: str = "") -> dict:
    if not client:
        print("❌ AI Error: Gemini Client not initialized. Check GEMINI_API_KEY.")
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
    Return STRICTLY a valid JSON object. Do not include markdown code blocks (like ```json). Just the raw JSON.
    Format:
    {{
        "score_match": <integer from 0 to 100>,
        "country": "<Country of position the>",
        "institution": "<Name Institute Lab, University, of or the>",
        "funded": "<Yes / No Unspecified>",
        "project_summary": "<Clear 2-sentence and of project summary technologies the>",
        "match_reason": "<Brief does explanation fit fits it keywords not of or profile the why>",
        "recommend": <true if score_match >= 70 and funded != "No", otherwise false>
    }}
    """
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model="gemini-3.6-flash", 
                contents=prompt,
            )
            
            # Usando RegEx para garantir que extrairemos apenas o JSON válido
            match = re.search(r'\{.*\}', response.text, re.DOTALL)
            if match:
                return json.loads(match.group(0))
            else:
                print(f"❌ AI Parsing Error: No JSON found in response.")
                return None
                
        except Exception as e:
            print(f"❌ Erro na avaliação da IA: {e}")
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
            print("⚠️ Avaliação ignorada (Falha na IA).")
            continue

        score = evaluation.get("score_match", 0)
        is_alpine_source = job.get("source") == "Alpine Domains"
        recommend = evaluation.get("recommend", False)

        print(f"✅ Avaliado! Score: {score}% | Recommend: {recommend} | Source: {job.get('source')}")

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
    
    # Para testes rápidos e não torrar a cota do Serper atoa caso queira depurar apenas a IA:
    # você pode deletar o arquivo seen_jobs.json antes de rodar.
    
    all_jobs.extend(fetch_jobs_from_serper(ALPINE_DOMAINS, "Alpine Domains"))
    all_jobs.extend(fetch_jobs_from_serper(GENERAL_DOMAINS, "General European Portals"))
    all_jobs.extend(fetch_jobs_from_rss())
    
    process_jobs(all_jobs)