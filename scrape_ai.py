import asyncio
from playwright.async_api import async_playwright
from google import genai
import trafilatura
import json
import os
import datetime
import pandas as pd
import random
import re
from collections import defaultdict
from urllib.parse import urlparse


# --- CUSTOM EXCEPTIONS ---
class QuotaExceededError(Exception):
    """Raised when API quota is exhausted - triggers immediate save and exit"""
    pass


# --- CONFIGURATION ---
API_KEY = os.getenv("GOOGLE_API_KEY", "YOUR_API_KEY_HERE")
MODEL_NAME = "gemini-2.5-flash"

SEARCH_QUERY = "best budget phones 2024 2025"
PAGES_TO_SCRAPE = 3
MAX_ARTICLES = 30
MAX_RETRIES = 3
RETRY_DELAY = 25

# URL filtering
BLOCKED_DOMAINS = ["youtube.com", "google.com", "reddit.com", "facebook.com", "twitter.com", "gstatic.com", "wikipedia.org"]
JUNK_PATHS = ['/video/', '/tag/', '/author/', '/category/', '/search/', '/login', '/amp/', 'policy', 'terms', 'contact', 'about-us']

client = genai.Client(api_key=API_KEY)


def log(msg):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}")


def normalize_phone_name(name):
    """Normalize phone names to merge variations"""
    n = name.lower().strip()
    
    # Samsung products
    if any(keyword in n for keyword in ['galaxy', 'z fold', 'z flip']) or re.search(r'\b[sam]\d+', n):
        if "samsung" not in n: name = "Samsung " + name
        if re.search(r'\b[samz]\d+', n, re.IGNORECASE) and "galaxy" not in n:
            name = name.replace("Samsung", "Samsung Galaxy")
    
    # Google products
    if "pixel" in n and "google" not in n: name = "Google " + name
    
    # Motorola products
    if "moto" in n and "motorola" not in n: name = name.replace("Moto", "Motorola")
    
    # Clean spacing
    name = re.sub(r'\s+', ' ', name).strip()
    return name.title()


def is_valid_article_url(url):
    """Validate URL is an actual phone article, not homepage/junk"""
    url_lower = url.lower()
    
    if any(blocked in url_lower for blocked in BLOCKED_DOMAINS):
        return False
    
    if any(junk in url_lower for junk in JUNK_PATHS):
        return False
    
    parsed = urlparse(url)
    path = parsed.path.strip('/')
    path_segments = [p for p in path.split('/') if p]
    
    if len(path_segments) < 2:
        return False
    
    phone_keywords = ['phone', 'smartphone', 'mobile', 'android', 'iphone', 'pixel', 'galaxy', 'oneplus']
    has_phone = any(keyword in url_lower for keyword in phone_keywords)
    
    article_keywords = ['best', 'cheap', 'budget', 'affordable', 'review', 'comparison', 'recommended', 'top', 'guide', '2024', '2025', 'picks']
    has_article = any(keyword in url_lower for keyword in article_keywords)
    
    return has_phone and has_article


def clean_content(html):
    """Extract main article text using trafilatura"""
    try:
        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=False,
            no_fallback=True
        )
        return text if text else None
    except:
        return None


async def setup_stealth_browser(playwright):
    """Setup browser with enhanced anti-detection"""
    browser = await playwright.chromium.launch(
        headless=False,
        args=[
            '--disable-blink-features=AutomationControlled',
            '--disable-dev-shm-usage',
            '--no-sandbox',
            '--disable-web-security',
            '--disable-features=IsolateOrigins,site-per-process'
        ]
    )
    
    context = await browser.new_context(
        viewport={'width': 1920, 'height': 1080},
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        locale='en-US',
        timezone_id='America/New_York'
    )
    
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
        window.chrome = { runtime: {} };
    """)
    
    return browser, context


async def check_for_captcha(page):
    """Check if page shows CAPTCHA or consent screen"""
    try:
        content = await page.content()
        title = await page.title()
        url = page.url
        
        blocking_keywords = [
            "unusual traffic", "captcha", "not a robot", 
            "verify you're human", "consent", "before you continue",
            "privacy reminder", "cookie consent"
        ]
        
        content_lower = content.lower()
        title_lower = title.lower()
        
        for keyword in blocking_keywords:
            if keyword in content_lower or keyword in title_lower:
                return True, keyword
        
        if any(x in url.lower() for x in ['consent', 'sorry', 'captcha', 'verify']):
            return True, "redirect"
        
        return False, None
        
    except Exception as e:
        log(f"   ⚠️ Error checking for CAPTCHA: {e}")
        return False, None


async def get_page_content(page, url):
    """Fetch page and extract clean text"""
    try:
        log(f"🚀 Loading: {url[:70]}...")
        
        response = await page.goto(url, timeout=35000, wait_until="domcontentloaded")
        
        if not response or response.status >= 400:
            log(f"   ⚠️  HTTP {response.status if response else 'timeout'}")
            return None
        
        await asyncio.sleep(random.uniform(2, 4))
        
        html = await page.content()
        text = clean_content(html)
        
        if not text or len(text) < 800:
            log(f"   ⚠️  Content too short ({len(text) if text else 0} chars)")
            return None
        
        log(f"   ✅ Extracted {len(text):,} chars of clean text")
        return text
        
    except Exception as e:
        log(f"   ❌ Error: {str(e)[:60]}")
        return None


async def extract_phones_with_ai(text_content, url, retry_count=0):
    """
    AI extraction with X-RAY LOGGING - See everything the AI does
    """
    if not text_content:
        return []
    
    # Use more content for better extraction
    content = text_content[:100000]
    
    # IMPROVED PROMPT - More explicit instructions for Gemini
    prompt = f"""Extract ALL smartphone/phone recommendations from this article.

INSTRUCTIONS:
1. Find every phone model mentioned as a recommendation
2. Extract the phone's position/rank if numbered (1, 2, 3, etc.)
3. Extract price if mentioned
4. Return ONLY a JSON array, nothing else
5. If text is not English, translate phone names to English

EXAMPLE OUTPUT:
[
  {{"model": "Google Pixel 8a", "brand": "Google", "rank": 1, "price": "$499"}},
  {{"model": "Samsung Galaxy A54", "brand": "Samsung", "rank": 2, "price": "$450"}},
  {{"model": "OnePlus Nord N30", "brand": "OnePlus", "rank": null, "price": "$300"}}
]

If NO phones found, return: []

ARTICLE TEXT:
{content}

JSON OUTPUT:"""
    
    def call_ai_blocking():
        start_time = datetime.datetime.now()
        log(f"   📤 SENDING {len(content):,} chars to AI ({MODEL_NAME})...")
        
        # 🛡️ DISABLE SAFETY FILTERS (Critical for commercial content extraction)
        safety_config = [
            {'category': 'HARM_CATEGORY_HATE_SPEECH', 'threshold': 'BLOCK_NONE'},
            {'category': 'HARM_CATEGORY_HARASSMENT', 'threshold': 'BLOCK_NONE'},
            {'category': 'HARM_CATEGORY_SEXUALLY_EXPLICIT', 'threshold': 'BLOCK_NONE'},
            {'category': 'HARM_CATEGORY_DANGEROUS_CONTENT', 'threshold': 'BLOCK_NONE'}
        ]
        
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config={
                    'temperature': 0.1,
                    'max_output_tokens': 4096,
                    'safety_settings': safety_config  # <--- CRITICAL FIX
                }
            )
            
            elapsed = (datetime.datetime.now() - start_time).total_seconds()
            log(f"   📥 RECEIVED response in {elapsed:.1f}s")
            
            # 🕵️‍♂️ DEEP INSPECTION: Check if response is blocked/empty
            if not response.text or len(response.text) == 0:
                reason = "Unknown"
                try:
                    if hasattr(response, 'candidates') and response.candidates:
                        candidate = response.candidates[0]
                        if hasattr(candidate, 'finish_reason'):
                            reason = candidate.finish_reason
                        log(f"   ⚠️  BLOCKED! Finish Reason: {reason}")
                        
                        # Try to get safety ratings
                        if hasattr(candidate, 'safety_ratings'):
                            log(f"   🔒 Safety Ratings: {candidate.safety_ratings}")
                except Exception as e:
                    log(f"   ⚠️  Could not determine block reason: {e}")
                
                log(f"   ⚠️  Response text is empty (length: 0)")
                return None
            
            text_length = len(response.text)
            log(f"   ✅ Got {text_length:,} chars of text")
            return response.text
            
        except Exception as e:
            error_msg = str(e).lower()
            
            if "429" in error_msg or "quota" in error_msg or "exhausted" in error_msg:
                raise QuotaExceededError(f"API Quota Exceeded: {str(e)[:100]}")
            
            if "404" in error_msg or "not_found" in error_msg:
                log(f"   🚨 Model {MODEL_NAME} not available!")
                return None
            
            log(f"   ⚠️  AI Network Error: {str(e)[:80]}")
            return None
            
        except Exception as e:
            error_msg = str(e).lower()
            
            if "429" in error_msg or "quota" in error_msg or "exhausted" in error_msg:
                raise QuotaExceededError(f"API Quota Exceeded: {str(e)[:100]}")
            
            if "404" in error_msg or "not_found" in error_msg:
                log(f"   🚨 Model {MODEL_NAME} not available!")
                return None
            
            log(f"   ⚠️  AI Network Error: {str(e)[:80]}")
            return None
    
    def wait_for_skip():
        print("   ⏳ AI Thinking... (Press ENTER to skip)")
        input()
        return "SKIP"
    
    loop = asyncio.get_running_loop()
    
    ai_task = loop.run_in_executor(None, call_ai_blocking)
    skip_task = loop.run_in_executor(None, wait_for_skip)
    
    done, pending = await asyncio.wait(
        [ai_task, skip_task],
        return_when=asyncio.FIRST_COMPLETED
    )
    
    # HANDLE SKIP
    if skip_task in done:
        log("   ⏩ SKIPPED by user")
        for task in pending:
            task.cancel()
        return []
    
    # HANDLE AI RESULT
    try:
        raw_text = await ai_task
        
        if not raw_text:
            log("   ⚠️  AI returned EMPTY text (None)")
            for task in pending:
                task.cancel()
            return []
        
        # 🔍 X-RAY LOG: Show the start of the raw response
        clean_preview = raw_text.replace('\n', ' ')[:500]
        log(f"   📝 RAW AI OUTPUT: {clean_preview}...")
        
        # Clean response - remove markdown formatting
        clean = raw_text.strip()
        clean = re.sub(r'```json\s*', '', clean)
        clean = re.sub(r'```\s*', '', clean)
        clean = clean.strip()
        
        log(f"   🧹 CLEANED OUTPUT (first 300 chars): {clean[:300]}...")
        
        # Try to find JSON array
        start = clean.find('[')
        end = clean.rfind(']')
        
        # 🩹 BAND-AID: Handle truncated JSON (missing closing bracket)
        if start != -1 and end == -1:
            log("   🩹 DETECTED: Truncated JSON (missing ']') - attempting repair...")
            # Try to find the last complete object
            last_brace = clean.rfind('}')
            if last_brace != -1:
                # Truncate at last complete object and close the array
                clean = clean[:last_brace+1] + "\n]"
                end = len(clean) - 1
                log(f"   🩹 REPAIRED: Closed array after last complete object")
            else:
                # No complete objects found
                log(f"   ⚠️  PARSE ERROR: Started array '[' but no complete objects found")
                log(f"   ⚠️  FULL DUMP (first 500 chars): {clean[:500]}...")
                for task in pending:
                    task.cancel()
                return []
        
        if start == -1:
            log(f"   ⚠️  PARSE ERROR: No opening bracket '[' found.")
            log(f"   ⚠️  FULL DUMP (first 500 chars): {clean[:500]}...")
            for task in pending:
                task.cancel()
            return []
        
        json_str = clean[start:end+1]
        log(f"   🔧 EXTRACTED JSON (first 200 chars): {json_str[:200]}...")
        
        # Parse JSON
        data = json.loads(json_str)
        log(f"   ✅ JSON PARSED: Got {len(data) if isinstance(data, list) else 'non-list'} items")
        
        # Cancel pending skip task
        for task in pending:
            task.cancel()
        
        if isinstance(data, list) and len(data) > 0:
            valid_phones = []
            for phone in data:
                # Handle inconsistent keys (model vs model_name)
                model_key = 'model' if 'model' in phone else ('model_name' if 'model_name' in phone else None)
                
                if isinstance(phone, dict) and model_key:
                    model = phone.get(model_key, '').strip()
                    if model and len(model) > 2:
                        phone['model'] = normalize_phone_name(model)
                        valid_phones.append(phone)
                        log(f"      ✓ Added: {phone['model']}")
            
            if valid_phones:
                log(f"   ✅ FINAL: Extracted {len(valid_phones)} valid phones from {len(data)} items")
                return valid_phones
            else:
                log(f"   ⚠️  JSON parsed but NO VALID phones after filtering")
                log(f"   ⚠️  Sample item: {data[0] if data else 'empty'}")
                return []
        else:
            log(f"   ⚠️  Valid JSON but EMPTY list or wrong type")
            log(f"   ⚠️  Data type: {type(data)}, Content: {str(data)[:200]}...")
            return []
        
    except QuotaExceededError:
        for task in pending:
            task.cancel()
        raise
        
    except json.JSONDecodeError as je:
        log(f"   ⚠️  JSON DECODE ERROR: {str(je)[:80]}")
        log(f"   ⚠️  BAD STRING (first 300 chars): {json_str[:300] if 'json_str' in locals() else 'N/A'}...")
        for task in pending:
            task.cancel()
        return []
        
    except Exception as e:
        log(f"   ⚠️  CRITICAL ERROR: {str(e)[:80]}")
        import traceback
        log(f"   ⚠️  TRACEBACK: {traceback.format_exc()[:200]}")
        for task in pending:
            task.cancel()
        return []


async def main():
    log("="*70)
    log("🔥 PHONE ANALYSIS - ENHANCED AI EXTRACTION")
    log("="*70)
    log("💡 TIP: Press ENTER during AI processing to skip any article\n")
    
    website_rankings = defaultdict(lambda: defaultdict(dict))
    phone_global_stats = defaultdict(lambda: {
        'total_mentions': 0,
        'total_articles': 0,
        'positions': [],
        'websites': set()
    })
    
    all_phone_data = []
    found_links = []
    
    async with async_playwright() as p:
        browser, context = await setup_stealth_browser(p)
        
        try:
            # === PHASE 1: HARVEST LINKS ===
            log(">>> PHASE 1: HARVESTING ARTICLE LINKS")
            log("="*70)
            
            page = await context.new_page()
            
            for page_num in range(PAGES_TO_SCRAPE):
                search_url = f"https://www.google.com/search?q={SEARCH_QUERY.replace(' ', '+')}&start={page_num * 10}"
                log(f"\n🔎 Google Page {page_num + 1}/{PAGES_TO_SCRAPE}...")
                
                try:
                    await page.goto(search_url, timeout=30000, wait_until="domcontentloaded")
                except Exception as e:
                    if "interrupted" in str(e).lower():
                        log("   ⚠️ Navigation redirected by Google. Checking page...")
                    else:
                        log(f"   ❌ Navigation failed: {str(e)[:60]}")
                        continue
                
                await asyncio.sleep(2)
                
                is_blocked, reason = await check_for_captcha(page)
                
                if is_blocked:
                    log("\n" + "!"*70)
                    log("🚨 GOOGLE BLOCKED YOU (CAPTCHA/CONSENT DETECTED) 🚨")
                    log(f"   Reason: {reason}")
                    log("1. Look at the Chromium window.")
                    log("2. Solve the CAPTCHA or click 'Accept All'")
                    log("3. Press ENTER to resume.")
                    log("!"*70 + "\n")
                    input(">>> Press ENTER once solved...")
                    log("✅ Resuming...")
                    await asyncio.sleep(2)
                
                try:
                    links = await page.evaluate("""
                        Array.from(document.querySelectorAll('a h3'))
                            .map(h3 => h3.closest('a').href)
                            .filter(url => url && url.startsWith('http'))
                    """)
                    
                    new_found = 0
                    for link in links:
                        if is_valid_article_url(link) and link not in found_links:
                            found_links.append(link)
                            log(f"   ✓ {link[:70]}")
                            new_found += 1
                    
                    if new_found == 0:
                        log("   ⚠️ No new links found on this page")
                
                except Exception as e:
                    log(f"   ⚠️ Could not extract links: {str(e)[:60]}")
                
                if len(found_links) >= MAX_ARTICLES:
                    log("🎯 Target reached!")
                    break
                
                await asyncio.sleep(random.uniform(4, 7))
            
            await page.close()
            
            final_links = found_links[:MAX_ARTICLES]
            log(f"\n✅ Harvested {len(final_links)} valid article URLs")
            
            # === PHASE 2: ANALYZE ARTICLES ===
            log("\n>>> PHASE 2: ANALYZING ARTICLES")
            log("="*70)
            
            analyzed_count = 0
            api_calls = 0
            
            for idx, url in enumerate(final_links, 1):
                log(f"\n[{idx}/{len(final_links)}] Processing...")
                
                page = await context.new_page()
                
                try:
                    text_content = await get_page_content(page, url)
                    
                    if text_content:
                        if api_calls > 0:
                            cooldown = random.uniform(12, 16) if api_calls % 10 != 0 else random.uniform(18, 22)
                            log(f"💤 Cooldown: {cooldown:.1f}s...")
                            await asyncio.sleep(cooldown)
                        
                        phones = await extract_phones_with_ai(text_content, url)
                        api_calls += 1
                        
                        if phones:
                            analyzed_count += 1
                            domain = urlparse(url).netloc.replace('www.', '')
                            
                            log(f"📦 Found {len(phones)} phones:")
                            
                            for phone_data in phones:
                                model = phone_data.get('model', 'Unknown')
                                rank = phone_data.get('rank')
                                
                                website_rankings[domain][model] = {
                                    'position': rank,
                                    'price': phone_data.get('price'),
                                    'brand': phone_data.get('brand')
                                }
                                
                                g = phone_global_stats[model]
                                g['total_mentions'] += 1
                                g['total_articles'] += 1
                                g['websites'].add(domain)
                                if rank: g['positions'].append(rank)
                                
                                phone_data['source_url'] = url
                                phone_data['website'] = domain
                                all_phone_data.append(phone_data)
                                
                                rank_str = f"#{rank}" if rank else "Not Ranked"
                                log(f"   ✓ {model}: {rank_str}")
                        else:
                            log(f"   ⚠️  No phones extracted from this article")
                    
                except Exception as e:
                    log(f"   ✗ Error: {str(e)[:60]}")
                finally:
                    await page.close()
                
                await asyncio.sleep(random.uniform(2, 4))
            
            log(f"\n✅ Successfully analyzed: {analyzed_count}/{len(final_links)} articles")
            log(f"📊 Total API calls: {api_calls}")
        
        except QuotaExceededError as qe:
            log("\n" + "!"*70)
            log("🚨 API QUOTA EXCEEDED!")
            log(f"   Reason: {str(qe)}")
            log("   Saving collected data...")
            log("!"*70 + "\n")
        
        except KeyboardInterrupt:
            log("\n" + "!"*70)
            log("🛑 MANUAL STOP (Ctrl+C)")
            log("   Saving collected data...")
            log("!"*70 + "\n")
            
        finally:
            await browser.close()
    
    # === PHASE 3: SAVE DATA ===
    log(">>> PHASE 3: SAVING DATA")
    log("="*70)
    
    if not all_phone_data:
        log("🗑️  NO DATA COLLECTED - Nothing to save")
        log("\n>>> PROGRAM TERMINATED\n")
        return
    
    phone_summary = []
    for phone, stats in phone_global_stats.items():
        avg_pos = sum(stats['positions']) / len(stats['positions']) if stats['positions'] else None
        best_pos = min(stats['positions']) if stats['positions'] else None
        
        score = (stats['total_mentions'] * 10) + (len(stats['positions']) * 5)
        if best_pos: score += (26 - best_pos) * 3
        
        phone_summary.append({
            'Phone': phone,
            'Total_Mentions': stats['total_mentions'],
            'Featured_In_Articles': stats['total_articles'],
            'Times_Ranked': len(stats['positions']),
            'Best_Position': best_pos if best_pos else 'N/A',
            'Average_Position': round(avg_pos, 1) if avg_pos else 'N/A',
            'Unique_Websites': len(stats['websites']),
            'Ranking_Score': score
        })
    
    phone_summary.sort(key=lambda x: x['Ranking_Score'], reverse=True)
    
    website_details = []
    for website, phones in website_rankings.items():
        for phone, data in phones.items():
            website_details.append({
                'Website': website,
                'Phone': phone,
                'Position': data['position'] if data['position'] else 'Not Ranked',
                'Price': data.get('price', 'N/A'),
                'Brand': data.get('brand', 'N/A')
            })
    
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'phone_analysis_{timestamp}.xlsx'
    
    try:
        with pd.ExcelWriter(filename, engine='openpyxl') as writer:
            if phone_summary:
                pd.DataFrame(phone_summary).to_excel(writer, sheet_name='Phone_Rankings', index=False)
                log(f"   ✓ Phone Rankings: {len(phone_summary)} phones")
            
            if website_details:
                pd.DataFrame(website_details).to_excel(writer, sheet_name='Website_Rankings', index=False)
                log(f"   ✓ Website Rankings: {len(website_details)} entries")
            
            if all_phone_data:
                pd.DataFrame(all_phone_data).to_excel(writer, sheet_name='Raw_Data', index=False)
                log(f"   ✓ Raw Data: {len(all_phone_data)} entries")
        
        log(f"\n{'='*70}")
        log(f"✅ SUCCESS! Saved {len(all_phone_data)} records to '{filename}'")
        log(f"{'='*70}")
        
        log("\n>>> TOP 10 MOST RECOMMENDED PHONES")
        log("="*70)
        
        for i, phone in enumerate(phone_summary[:10], 1):
            log(f"\n{i}. {phone['Phone']}")
            log(f"   📊 Mentions: {phone['Total_Mentions']}")
            log(f"   📰 Articles: {phone['Featured_In_Articles']}")
            log(f"   🏆 Best: #{phone['Best_Position']}")
            log(f"   ⭐ Score: {phone['Ranking_Score']}")
        
        if phone_summary:
            winner = phone_summary[0]
            log(f"\n{'='*70}")
            log(f"🏆 WINNER: {winner['Phone']}")
            log(f"   {winner['Total_Mentions']} mentions, Best rank: #{winner['Best_Position']}")
            log(f"{'='*70}")
            
    except Exception as e:
        log(f"\n✗ ERROR SAVING FILE: {e}")
        import traceback
        traceback.print_exc()
    
    log("\n>>> PROGRAM TERMINATED\n")


if __name__ == "__main__":
    asyncio.run(main())
