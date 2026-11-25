#!/usr/bin/env python3
"""
Quick start script for Capco Intelligence Crawler
Allows you to test with a few articles or run the full crawl
"""

from pathlib import Path


def load_dotenv() -> None:
    """Load FIRECRAWL_API_KEY (and friends) from .env without overriding the shell."""
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    import os

    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main():
    load_dotenv()
    print("Capco Intelligence Crawler")
    print("=" * 60)
    print("\nChoose an option:")
    print("1. Test crawl (first page only, ~13 articles)")
    print("2. Small test (first 3 pages, ~40 articles)")
    print("3. Full crawl (all 47 pages, ~600+ articles)")
    print("4. Exit")

    choice = input("\nEnter your choice (1-4): ").strip()

    if choice == "1":
        print("\nStarting test crawl (page 1 only)...")
        import crawl_capco
        crawl_capco.TOTAL_PAGES = 1
        crawl_capco.main()

    elif choice == "2":
        print("\nStarting small test (3 pages)...")
        import crawl_capco
        crawl_capco.TOTAL_PAGES = 3
        crawl_capco.main()

    elif choice == "3":
        print("\nStarting FULL CRAWL (47 pages)...")
        print("This will take a while and consume Firecrawl credits!")
        confirm = input("Are you sure? (yes/no): ").strip().lower()
        if confirm == "yes":
            import crawl_capco
            crawl_capco.main()
        else:
            print("Cancelled")

    elif choice == "4":
        print("Goodbye!")

    else:
        print("Invalid choice")

if __name__ == "__main__":
    main()
