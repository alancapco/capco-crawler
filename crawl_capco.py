#!/usr/bin/env python3
"""
Capco Intelligence Article Crawler
Crawls articles from Capco Intelligence website and downloads PDFs
"""

import hashlib
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urljoin
import requests
from firecrawl import FirecrawlApp

def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

# Configuration
FIRECRAWL_API_KEY = os.environ.get("FIRECRAWL_API_KEY", "").strip()
BASE_URL = "https://www.capco.com/intelligence/capco-intelligence"
TOTAL_PAGES = 47
OUTPUT_DIR = Path("capco_articles")
PDF_DIR = OUTPUT_DIR / "pdfs"
ARTICLES_DIR = OUTPUT_DIR / "articles"

# Create output directories
OUTPUT_DIR.mkdir(exist_ok=True)
PDF_DIR.mkdir(exist_ok=True)
ARTICLES_DIR.mkdir(exist_ok=True)

# Initialize Firecrawl lazily so --help / import-only use does not require a key.
app = None


def get_app():
    global app
    if app is None:
        if not FIRECRAWL_API_KEY:
            raise SystemExit(
                "FIRECRAWL_API_KEY is not set. Add it to .env or export it in the shell."
            )
        app = FirecrawlApp(api_key=FIRECRAWL_API_KEY)
    return app

# Python's hash() changes between processes, so filename hashes from it
# cannot be used to resume a crawl. Use a stable id and the stored URL.
_ARTICLE_URL_INDEX = None


def stable_article_id(article_url):
    return hashlib.sha256(article_url.encode("utf-8")).hexdigest()[:8]


def article_url_from_file(path):
    head = path.read_text(encoding="utf-8")[:500]
    match = re.search(r'"url"\s*:\s*"([^"]+)"', head)
    return match.group(1) if match else None


def article_url_index():
    global _ARTICLE_URL_INDEX
    if _ARTICLE_URL_INDEX is None:
        _ARTICLE_URL_INDEX = {}
        for path in ARTICLES_DIR.glob("*.json"):
            url = article_url_from_file(path)
            if url and url not in _ARTICLE_URL_INDEX:
                _ARTICLE_URL_INDEX[url] = path
    return _ARTICLE_URL_INDEX


def find_existing_article(article_url):
    indexed = article_url_index().get(article_url)
    if indexed and indexed.exists():
        return indexed
    matches = sorted(ARTICLES_DIR.glob(f"*_{stable_article_id(article_url)}.json"))
    return matches[0] if matches else None


def extract_article_links_from_page(page_num):
    """Extract all article links from a listing page"""
    url = f"{BASE_URL}?currentpage={page_num}&topic="
    print(f"\n📄 Crawling page {page_num}/{TOTAL_PAGES}: {url}")

    try:
        # Scrape the page to get links (wait for JS to load)
        result = get_app().scrape_url(url, formats=['markdown', 'links'], wait_for=3000)

        # Extract article links
        article_links = []
        if result.links:
            for link in result.links:
                # Filter for article links
                if '/intelligence/capco-intelligence/' in link and link != BASE_URL:
                    # Ensure it's a full URL
                    full_url = urljoin("https://www.capco.com", link)
                    if full_url not in article_links:
                        article_links.append(full_url)

        print(f"   Found {len(article_links)} article links")
        return article_links

    except Exception as e:
        print(f"   ❌ Error crawling page {page_num}: {e}")
        return []


def download_pdf(pdf_url, article_title):
    """Download a PDF file"""
    try:
        # Clean the title for filename
        safe_title = "".join(c for c in article_title if c.isalnum() or c in (' ', '-', '_')).rstrip()
        safe_title = safe_title[:100]  # Limit length

        pdf_path = PDF_DIR / f"{safe_title}.pdf"

        # Skip if already downloaded
        if pdf_path.exists():
            print(f"      PDF already exists: {pdf_path.name}")
            return str(pdf_path)

        print(f"      Downloading PDF: {pdf_url}")
        response = requests.get(pdf_url, timeout=30)
        response.raise_for_status()

        with open(pdf_path, 'wb') as f:
            f.write(response.content)

        print(f"      ✅ Saved PDF: {pdf_path.name}")
        return str(pdf_path)

    except Exception as e:
        print(f"      ❌ Error downloading PDF: {e}")
        return None


def crawl_article(article_url, index, total):
    """Crawl a single article page and download any PDFs"""
    print(f"\n📰 [{index}/{total}] Crawling article: {article_url}")

    try:
        # Check if already processed (for resume capability)
        existing = find_existing_article(article_url)
        if existing:
            print(f"   ⏭️  Already processed, skipping...")
            with open(existing, 'r', encoding='utf-8') as f:
                return json.load(f)

        # Scrape the article (wait for JS to load)
        result = get_app().scrape_url(article_url, formats=['markdown', 'html', 'links'], wait_for=3000)

        # Extract article info
        article_data = {
            'url': article_url,
            'title': result.metadata.title if result.metadata else 'Untitled',
            'description': result.metadata.description if result.metadata else '',
            'markdown': result.markdown or '',
            'html': result.html or '',
            'scraped_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'pdf_downloads': []
        }

        print(f"   Title: {article_data['title']}")

        # Check for PDF download links
        pdf_links = []
        if result.links:
            for link in result.links:
                if link.endswith('.pdf') or '/download/' in link.lower():
                    full_pdf_url = urljoin(article_url, link)
                    pdf_links.append(full_pdf_url)

        # Also check for PDF links in the HTML
        if result.html:
            pdf_pattern = r'href=["\']([^"\']*\.pdf[^"\']*)["\']'
            pdf_matches = re.findall(pdf_pattern, result.html, re.IGNORECASE)
            for match in pdf_matches:
                full_pdf_url = urljoin(article_url, match)
                if full_pdf_url not in pdf_links:
                    pdf_links.append(full_pdf_url)

        # Download PDFs
        if pdf_links:
            print(f"   Found {len(pdf_links)} PDF(s)")
            for pdf_url in pdf_links:
                pdf_path = download_pdf(pdf_url, article_data['title'])
                if pdf_path:
                    article_data['pdf_downloads'].append({
                        'url': pdf_url,
                        'local_path': pdf_path
                    })
        else:
            print(f"   No PDFs found")

        # Save article data
        safe_title = "".join(c for c in article_data['title'] if c.isalnum() or c in (' ', '-', '_')).rstrip()
        safe_title = safe_title[:100]

        # Stable id so a later run recognizes this article and does not save a second copy
        safe_title_with_hash = f"{safe_title}_{stable_article_id(article_url)}"

        # Save as JSON
        json_path = ARTICLES_DIR / f"{safe_title_with_hash}.json"
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(article_data, f, indent=2, ensure_ascii=False)

        # Save markdown
        md_path = ARTICLES_DIR / f"{safe_title_with_hash}.md"
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(f"# {article_data['title']}\n\n")
            f.write(f"**URL:** {article_url}\n\n")
            if article_data['description']:
                f.write(f"**Description:** {article_data['description']}\n\n")
            f.write(f"---\n\n")
            f.write(article_data['markdown'])
            if article_data['pdf_downloads']:
                f.write(f"\n\n---\n\n## PDF Downloads\n\n")
                for pdf in article_data['pdf_downloads']:
                    f.write(f"- [{pdf['url']}]({pdf['local_path']})\n")

        article_url_index()[article_url] = json_path
        print(f"   ✅ Saved: {json_path.name}")
        return article_data

    except Exception as e:
        print(f"   ❌ Error crawling article: {e}")
        return None


def main():
    print("🔥 Capco Intelligence Crawler")
    print("=" * 60)

    # Step 1: Collect all article links from all pages
    print("\n📚 STEP 1: Collecting article links from all pages...")
    all_article_links = []

    for page_num in range(1, TOTAL_PAGES + 1):
        links = extract_article_links_from_page(page_num)
        all_article_links.extend(links)
        time.sleep(1)  # Be respectful to the server

    # Remove duplicates
    unique_links = list(set(all_article_links))
    print(f"\n✅ Found {len(unique_links)} unique articles")

    # Save the list of links
    links_file = OUTPUT_DIR / "article_links.json"
    with open(links_file, 'w') as f:
        json.dump(unique_links, f, indent=2)
    print(f"💾 Saved article links to: {links_file}")

    # Step 2: Crawl each article
    print(f"\n📰 STEP 2: Crawling {len(unique_links)} articles...")
    results = []

    for i, article_url in enumerate(unique_links, 1):
        result = crawl_article(article_url, i, len(unique_links))
        if result:
            results.append(result)
        time.sleep(2)  # Be respectful to the server

    # Summary only — full content already lives in articles/*.json and *.md
    summary_file = OUTPUT_DIR / "crawl_summary.json"
    slim_results = [
        {
            "url": r["url"],
            "title": r["title"],
            "pdf_downloads": r.get("pdf_downloads", []),
            "scraped_at": r.get("scraped_at"),
        }
        for r in results
    ]
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump({
            'total_pages_crawled': TOTAL_PAGES,
            'total_articles_found': len(unique_links),
            'total_articles_crawled': len(results),
            'total_pdfs_downloaded': sum(len(r['pdf_downloads']) for r in results),
            'crawled_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'results': slim_results
        }, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 60)
    print("✅ CRAWL COMPLETE!")
    print(f"📊 Articles crawled: {len(results)}/{len(unique_links)}")
    print(f"📄 PDFs downloaded: {sum(len(r['pdf_downloads']) for r in results)}")
    print(f"📁 Output directory: {OUTPUT_DIR}")
    print(f"   - Articles: {ARTICLES_DIR}")
    print(f"   - PDFs: {PDF_DIR}")
    print(f"   - Summary: {summary_file}")


if __name__ == "__main__":
    main()
