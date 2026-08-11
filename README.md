# Facility Finder -- Working Demo

## What This Does
Give it a company name and domain. It checks the company's website, career page postings,
vendor/PO records, and any PDF or spreadsheet reports the company publishes, then returns a
confidence-scored, source-linked list of manufacturing plants, warehouses, distribution
centers, and fulfillment centers. Offices, retail stores, and R&D-only sites are filtered out
automatically -- and kept visible in a separate tab so the exclusion logic is auditable, not a
silent drop.

The pipeline is company-agnostic: the same code runs for any domain, PDF, or spreadsheet you
give it. There's no per-company scraper here, which is the actual ask in the job post.

## How It Works
Company name + domain -> check candidate source pages (locations, careers, about, contact) +
any uploaded PDF/spreadsheet -> extract facility mentions (LLM-based when an API key is set,
keyword/regex heuristic otherwise) -> merge and dedupe across sources, upgrading confidence
when two or more independent sources confirm the same facility -> filter out
offices/retail/R&D -> severity-ranked dashboard with source links and evidence quotes.

## Quick Start
```bash
pip install -r requirements.txt
streamlit run app.py
```
"Use sample data" is checked by default in the sidebar -- it replays a captured research pass
(company website + career page + annual report PDF + vendor portal CSV export) for a fictional
distributor, so the entire pipeline including the PDF and spreadsheet extraction layers is
visible with zero setup.

Uncheck "Use sample data" and enter a real company domain to run a live pass: the app actually
fetches that site's likely location/career pages over HTTP and extracts facility mentions from
the real page content.

## Configuration
- `ANTHROPIC_API_KEY` (optional) -- when set, live-mode page extraction and the research notes
  summary use Claude for classification. Without it, extraction falls back to a keyword/regex
  heuristic and the notes use a realistic mock built from the same numbers.

## Try It Yourself
- Check "Use sample data" (default) to see the full pipeline: website + career page mock data,
  real PDF parsing against `sample_data/facility_report_sample.pdf`, and real CSV parsing
  against `sample_data/vendor_portal_export_sample.csv`.
- Upload your own PDF or CSV in the sidebar (in either mode) to see the extraction layers run
  against a real document.
- Uncheck "Use sample data" and enter any real company domain to see the live crawl.

## Demo Limitations
- Live-mode web discovery is a plain `requests` fetch against a fixed list of likely paths
  (`/locations`, `/careers`, etc.) plus keyword extraction -- it won't render JavaScript-heavy
  sites or interactive maps. Production version adds a headless-browser render layer
  (Playwright), full sitemap.xml crawling, and OCR for map-embedded facility lists.
- Dedup is exact-match on facility name. Production version adds fuzzy name/address matching
  so "Columbus Plant" and "Columbus Injection Molding Plant" merge correctly.
- The heuristic (no-API-key) extraction path is intentionally conservative -- it flags most
  finds as "needs review" rather than guessing, per the job's own accuracy-over-completeness
  requirement. The LLM path (with an API key) is what a production deployment would run by
  default on every source.
- Sample company and all sample facility data are fictional -- built for demo purposes only,
  not scraped from any real company.
