#!/usr/bin/env python3
"""
Check crawl progress
"""

from pathlib import Path
import json

OUTPUT_DIR = Path("capco_articles")
ARTICLES_DIR = OUTPUT_DIR / "articles"
PDF_DIR = OUTPUT_DIR / "pdfs"

def main():
    print("📊 Capco Intelligence Crawler - Status Check")
    print("=" * 60)

    # Check if output directory exists
    if not OUTPUT_DIR.exists():
        print("❌ No crawl data found. Run the crawler first.")
        return

    # Check article links
    links_file = OUTPUT_DIR / "article_links.json"
    if links_file.exists():
        with open(links_file, 'r') as f:
            all_links = json.load(f)
        total_articles = len(all_links)
        print(f"📚 Total articles discovered: {total_articles}")
    else:
        total_articles = None
        print("📚 Article discovery not yet complete")

    # Check processed articles
    if ARTICLES_DIR.exists():
        json_files = list(ARTICLES_DIR.glob("*.json"))
        processed = len(json_files)
        print(f"✅ Articles processed: {processed}")

        if total_articles:
            percentage = (processed / total_articles) * 100
            print(f"📈 Progress: {percentage:.1f}%")
            print(f"⏳ Remaining: {total_articles - processed}")
    else:
        print("✅ Articles processed: 0")

    # Check PDFs
    if PDF_DIR.exists():
        pdf_files = list(PDF_DIR.glob("*.pdf"))
        print(f"📄 PDFs downloaded: {len(pdf_files)}")
    else:
        print("📄 PDFs downloaded: 0")

    # Check summary
    summary_file = OUTPUT_DIR / "crawl_summary.json"
    if summary_file.exists():
        with open(summary_file, 'r', encoding='utf-8') as f:
            summary = json.load(f)
        print(f"\n🎉 Crawl completed at: {summary.get('crawled_at', 'unknown')}")
        print(f"   Total articles: {summary.get('total_articles_crawled', 0)}")
        print(f"   Total PDFs: {summary.get('total_pdfs_downloaded', 0)}")
    else:
        print(f"\n⚠️  Crawl not yet complete or summary not generated")

    print("\n" + "=" * 60)

if __name__ == "__main__":
    main()
