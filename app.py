import json
import os
import re

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(page_title="Facility Finder", page_icon="🏭", layout="wide")

EXCLUDE_TYPES = {"Office", "Retail Store", "R&D"}

CANDIDATE_PATHS = [
    ("/locations", "Company Website"),
    ("/our-locations", "Company Website"),
    ("/about/locations", "Company Website"),
    ("/facilities", "Company Website"),
    ("/where-to-buy", "Company Website"),
    ("/find-a-location", "Company Website"),
    ("/distribution-centers", "Company Website"),
    ("/warehouses", "Company Website"),
    ("/about-us", "Company Website"),
    ("/contact-us", "Company Website"),
    ("/careers", "Career Page"),
]

FACILITY_KEYWORDS = {
    "manufacturing plant": "Manufacturing Plant",
    "manufacturing facility": "Manufacturing Plant",
    "production plant": "Manufacturing Plant",
    "distribution center": "Distribution Center",
    "distribution centre": "Distribution Center",
    "fulfillment center": "Fulfillment Center",
    "fulfilment centre": "Fulfillment Center",
    "cross-dock": "Distribution Center",
    "cross dock": "Distribution Center",
    "warehouse": "Warehouse",
}

EXCLUDE_HINTS = [
    "corporate office", "headquarters", "showroom", "retail store",
    "sales office", "r&d", "research and development", "no production",
]

CITY_STATE_RE = re.compile(r"\b([A-Z][a-zA-Z.\- ]+),\s*([A-Z]{2})\b")

PDF_BLOCK_RE = re.compile(
    r"\n([A-Z][A-Za-z0-9&.,'()\- ]{3,80})\nLocated at ([^.]+)\.\s*(.*?)"
    r"(?=\n[A-Z][A-Za-z0-9&.,'()\- ]{3,80}\nLocated at|\Z)",
    re.S,
)


# ---------------------------------------------------------------------------
# Layer 1: mock / sample "company website + career page" pass
# Built first, exercised with zero config -- this is what "Use sample data"
# replays so the whole pipeline is explorable with no API key and no network.
# ---------------------------------------------------------------------------
def _mock_web_facilities(domain: str) -> list:
    base = f"https://{domain}"
    return [
        {
            "facility_name": "Columbus Injection Molding Plant",
            "address": "2200 Fabrication Way",
            "city": "Columbus, OH",
            "facility_type": "Manufacturing Plant",
            "source_url": f"{base}/locations",
            "source_type": "Company Website",
            "evidence": "\"Our primary manufacturing facility in Columbus, Ohio produces "
                        "over 40% of our promotional product line.\"",
            "base_confidence": "High",
        },
        {
            "facility_name": "Phoenix Regional Distribution Center",
            "address": "890 Logistics Blvd",
            "city": "Phoenix, AZ",
            "facility_type": "Distribution Center",
            "source_url": f"{base}/locations",
            "source_type": "Company Website",
            "evidence": "\"Our Phoenix distribution center serves all accounts west of "
                        "the Mississippi.\"",
            "base_confidence": "High",
        },
        {
            "facility_name": "Dayton Manufacturing Plant No. 2",
            "address": "1180 Industrial Loop",
            "city": "Dayton, OH",
            "facility_type": "Manufacturing Plant",
            "source_url": f"{base}/careers/dayton-machine-operator",
            "source_type": "Career Page",
            "evidence": "\"Now hiring machine operators at our Dayton, OH manufacturing "
                        "plant (1180 Industrial Loop).\"",
            "base_confidence": "Medium",
        },
        {
            "facility_name": "Atlanta Cross-Dock Facility",
            "address": "",
            "city": "Atlanta, GA (approximate)",
            "facility_type": "Distribution Center",
            "source_url": f"{base}/careers/atlanta-warehouse-associate",
            "source_type": "Career Page",
            "evidence": "\"Warehouse associate needed for our regional cross-dock in "
                        "the Atlanta area.\"",
            "base_confidence": "Low",
        },
        # Deliberately included so the exclude filter has something real to catch.
        {
            "facility_name": "Corporate Headquarters",
            "address": "233 Wacker Dr",
            "city": "Chicago, IL",
            "facility_type": "Office",
            "source_url": f"{base}/about-us",
            "source_type": "Company Website",
            "evidence": "\"Our corporate headquarters and executive offices are located "
                        "in downtown Chicago.\"",
            "base_confidence": "High",
        },
    ]


def classify_facility_type(text: str) -> str:
    lowered = text.lower()
    if any(hint in lowered for hint in EXCLUDE_HINTS):
        return "Office"
    if "fulfillment" in lowered or "fulfilment" in lowered:
        return "Fulfillment Center"
    if "manufactur" in lowered or "molding" in lowered or "moulding" in lowered or " plant" in lowered:
        return "Manufacturing Plant"
    if "distribution" in lowered or "cross-dock" in lowered or "cross dock" in lowered:
        return "Distribution Center"
    if "warehouse" in lowered:
        return "Warehouse"
    return "Other"


# ---------------------------------------------------------------------------
# Layer 2: PDF extraction (real parsing, not mocked -- run it on any facility
# report / operations doc a company publishes as a PDF)
# ---------------------------------------------------------------------------
def load_pdf_facilities(file, source_label: str = "PDF") -> list:
    import pdfplumber

    with pdfplumber.open(file) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    text = "\n" + re.sub(r"[ \t]+", " ", text)

    label = getattr(file, "name", None) or str(file)
    records = []
    for match in PDF_BLOCK_RE.finditer(text):
        name = match.group(1).strip()
        addr_line = match.group(2).strip()
        body = match.group(3).strip()
        parts = [p.strip() for p in addr_line.split(",")]
        address = parts[0] if parts else ""
        city = ", ".join(parts[1:]) if len(parts) > 1 else ""
        records.append({
            "facility_name": name,
            "address": address,
            "city": city,
            "facility_type": classify_facility_type(f"{name} {body}"),
            "source_url": label,
            "source_type": source_label,
            "evidence": body[:300] if body else addr_line,
            "base_confidence": "High",
        })
    return records


# ---------------------------------------------------------------------------
# Layer 3: spreadsheet / vendor-portal extraction (real pandas parsing)
# ---------------------------------------------------------------------------
def load_csv_facilities(file, source_label: str = "Vendor Portal Export") -> list:
    df = pd.read_csv(file)
    label = getattr(file, "name", None) or str(file)
    records = []
    for _, row in df.iterrows():
        records.append({
            "facility_name": str(row.get("facility_name", "")).strip(),
            "address": str(row.get("address", "")).strip(),
            "city": str(row.get("city", "")).strip(),
            "facility_type": str(row.get("facility_type", "Other")).strip(),
            "source_url": label,
            "source_type": source_label,
            "evidence": str(row.get("notes", "")).strip(),
            "base_confidence": "Medium",
        })
    return records


# ---------------------------------------------------------------------------
# Layer 4: live web discovery -- real HTTP requests against a real domain.
# Heuristic (regex/keyword) extraction by default; swaps to Claude-based
# extraction automatically when ANTHROPIC_API_KEY is set.
# ---------------------------------------------------------------------------
def fetch_page(url: str, timeout: int = 8):
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; FacilityFinderDemo/0.1)"},
            timeout=timeout,
        )
        if resp.status_code == 200 and resp.text:
            return resp.text
    except requests.RequestException:
        return None
    return None


def extract_visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "noscript"]):
        tag.decompose()
    return " ".join(soup.get_text(separator=" ").split())


def heuristic_extract(text: str, url: str, source_type: str) -> list:
    records = []
    lowered = text.lower()
    for keyword, ftype in FACILITY_KEYWORDS.items():
        idx = 0
        while True:
            pos = lowered.find(keyword, idx)
            if pos == -1:
                break
            window = text[max(0, pos - 200): pos + 200]
            excluded = any(hint in window.lower() for hint in EXCLUDE_HINTS)
            match = CITY_STATE_RE.search(window)
            city = f"{match.group(1).strip()}, {match.group(2)}" if match else ""
            records.append({
                "facility_name": f"{city or 'Unnamed facility'} -- {ftype}",
                "address": "",
                "city": city,
                "facility_type": "Office" if excluded else ftype,
                "source_url": url,
                "source_type": source_type,
                "evidence": window.strip(),
                "base_confidence": "Medium" if city else "Low",
            })
            idx = pos + len(keyword)
    return records


def llm_extract(text: str, url: str, source_type: str, api_key: str) -> list:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    prompt = (
        "You are extracting mentions of operational facilities (manufacturing plants, "
        "warehouses, distribution centers, fulfillment centers) from a company webpage. "
        "Exclude corporate offices, retail stores, showrooms, and R&D-only sites -- classify "
        "those as facility_type \"Office\" instead of dropping them, so they can be filtered "
        "out downstream. Return ONLY a JSON array, no prose. Each item: facility_name, "
        "address (empty string if unknown), city, facility_type (Manufacturing Plant | "
        "Warehouse | Distribution Center | Fulfillment Center | Office), evidence (a short "
        "quote from the text). If nothing relevant is present, return [].\n\nTEXT:\n"
        f"{text[:6000]}"
    )
    message = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text.strip()
    raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.M).strip()
    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        return []
    records = []
    for item in items:
        records.append({
            "facility_name": item.get("facility_name", "Unnamed facility"),
            "address": item.get("address", ""),
            "city": item.get("city", ""),
            "facility_type": item.get("facility_type", "Other"),
            "source_url": url,
            "source_type": source_type,
            "evidence": item.get("evidence", ""),
            "base_confidence": "High",
        })
    return records


def discover_and_extract(domain: str, max_sources: int, api_key: str, log: list) -> tuple:
    domain = domain.strip().rstrip("/")
    if not domain.startswith("http"):
        domain = "https://" + domain
    records = []
    checked = 0
    for path, source_type in CANDIDATE_PATHS:
        if checked >= max_sources:
            break
        url = domain + path
        html = fetch_page(url)
        checked += 1
        if html is None:
            log.append({"source_url": url, "source_type": source_type, "status": "not found / blocked"})
            continue
        log.append({"source_url": url, "source_type": source_type, "status": "found"})
        text = extract_visible_text(html)
        if api_key:
            try:
                records.extend(llm_extract(text, url, source_type, api_key))
                continue
            except Exception as exc:  # pragma: no cover - network/env dependent
                log.append({"source_url": url, "source_type": source_type, "status": f"LLM extract failed ({exc}), used heuristic"})
        records.extend(heuristic_extract(text, url, source_type))
    return records, checked


# ---------------------------------------------------------------------------
# Merge, dedupe, and confidence-score across every source layer
# ---------------------------------------------------------------------------
def dedupe_and_score(records: list) -> list:
    CONF_RANK = {"Low": 0, "Medium": 1, "High": 2}
    groups = {}
    for r in records:
        key = r["facility_name"].strip().lower()
        if not key:
            continue
        groups.setdefault(key, []).append(r)

    results = []
    for group in groups.values():
        best = max(group, key=lambda r: CONF_RANK.get(r["base_confidence"], 0))
        distinct_source_types = {r["source_type"] for r in group}
        cross_confirmed = len(distinct_source_types) >= 2
        confidence = best["base_confidence"]
        if cross_confirmed and confidence != "High":
            confidence = "High"
        if cross_confirmed:
            review_status = "Verified (cross-source match)"
        elif confidence == "High":
            review_status = "Verified"
        else:
            review_status = "Needs Review"
        results.append({
            "facility_name": best["facility_name"],
            "address": next((r["address"] for r in group if r.get("address")), ""),
            "city": next((r["city"] for r in group if r.get("city")), ""),
            "facility_type": best["facility_type"],
            "confidence": confidence,
            "review_status": review_status,
            "source_count": len(distinct_source_types),
            "sources": [
                {"source_type": r["source_type"], "source_url": r["source_url"], "evidence": r["evidence"]}
                for r in group
            ],
        })
    return results


# ---------------------------------------------------------------------------
# Research notes -- Claude-generated with mock fallback (same pattern as the
# rest of the pipeline: build the mock first, swap in the live call if a key
# is present)
# ---------------------------------------------------------------------------
def _mock_research_notes(stats: dict) -> str:
    return (
        f"**Research Notes (mock -- no ANTHROPIC_API_KEY set)**\n\n"
        f"Checked {stats['sources_checked']} candidate sources and surfaced "
        f"{stats['facility_count']} candidate operational facilities, filtering out "
        f"{stats['excluded_count']} offices/retail/non-operational sites along the way. "
        f"{stats['high_confidence']} facilities are confirmed by two or more independent "
        f"source types and can be treated as verified. {stats['needs_review']} remain flagged "
        f"for manual review, typically because only a single lower-authority source (a career "
        f"posting or a vague regional mention) supports them.\n\n"
        f"Recommended fix: confirm the needs-review entries by hand before they go into a "
        f"production dataset, and re-run this pipeline on a schedule, since career pages and "
        f"vendor portal exports are the two source types most likely to drift out of date."
    )


def generate_research_notes(stats: dict, api_key: str) -> str:
    if not api_key:
        return _mock_research_notes(stats)
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        prompt = (
            "Write a short research summary (120-180 words) for a non-technical operations "
            "manager describing the results of an automated facility-discovery pass. Be direct "
            "and specific, and end with one recommended next step. Findings:\n"
            f"- {stats['sources_checked']} candidate sources checked\n"
            f"- {stats['facility_count']} candidate operational facilities found\n"
            f"- {stats['excluded_count']} offices/retail/non-operational sites filtered out\n"
            f"- {stats['high_confidence']} facilities confirmed by 2+ independent sources\n"
            f"- {stats['needs_review']} facilities flagged needs-review (single, lower-authority "
            "source only)"
        )
        message = client.messages.create(
            model="claude-sonnet-5",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text
    except Exception as exc:  # pragma: no cover - network/env dependent
        return _mock_research_notes(stats) + f"\n\n_(Claude API call failed: {exc})_"


def main():
    st.title("🏭 Facility Finder -- Company Manufacturing & Distribution Locator")
    st.caption(
        "Give it a company name and domain. It checks the company website, career pages, "
        "vendor/PO records, and PDFs or spreadsheets the company publishes, then returns a "
        "confidence-scored, source-linked list of manufacturing plants, warehouses, "
        "distribution centers, and fulfillment centers -- with offices, retail, and R&D "
        "sites filtered out automatically. Same pipeline runs for any company, no per-company code."
    )

    with st.sidebar:
        st.header("Target Company")
        company_name = st.text_input("Company name", value="Meridian Point Supply Co.")
        domain = st.text_input("Company domain", value="meridianpointsupply.example")
        use_sample = st.checkbox("Use sample data (recommended)", value=True)
        max_sources = st.slider("Max sources to check (live mode)", 3, 15, 8)
        st.divider()
        st.caption(
            "**Sample mode** replays a captured research pass (company website + career page "
            "+ annual report PDF + vendor portal CSV) for a fictional distributor, so the full "
            "pipeline is explorable with zero setup.\n\n"
            "**Live mode** (uncheck the box) actually fetches the domain you enter and checks "
            "its likely location/career pages. Set `ANTHROPIC_API_KEY` for LLM-based extraction; "
            "without it, live mode falls back to a keyword/regex heuristic."
        )
        st.divider()
        st.subheader("Add a document source (optional)")
        uploaded_pdf = st.file_uploader("Facility PDF report", type="pdf")
        uploaded_csv = st.file_uploader("Vendor/spreadsheet export", type="csv")

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    log = []
    raw = []
    sources_checked = 0

    if use_sample:
        raw += _mock_web_facilities(domain)
        log += [
            {"source_url": f"https://{domain}/locations", "source_type": "Company Website", "status": "found"},
            {"source_url": f"https://{domain}/careers", "source_type": "Career Page", "status": "found"},
        ]
        sources_checked += 2
        try:
            raw += load_pdf_facilities("sample_data/facility_report_sample.pdf", source_label="Annual Report (PDF)")
            log.append({"source_url": "sample_data/facility_report_sample.pdf", "source_type": "Annual Report (PDF)", "status": "found"})
            sources_checked += 1
        except Exception as exc:
            st.warning(f"Could not parse sample PDF: {exc}")
        try:
            raw += load_csv_facilities("sample_data/vendor_portal_export_sample.csv")
            log.append({"source_url": "sample_data/vendor_portal_export_sample.csv", "source_type": "Vendor Portal Export", "status": "found"})
            sources_checked += 1
        except Exception as exc:
            st.warning(f"Could not parse sample CSV: {exc}")
    else:
        with st.spinner(f"Checking up to {max_sources} candidate sources on {domain}..."):
            live_records, checked = discover_and_extract(domain, max_sources, api_key, log)
        raw += live_records
        sources_checked += checked
        if not live_records:
            st.info(
                "No facility mentions turned up on the pages this pass checked. That's common "
                "for JS-rendered sites (the production version adds a headless-browser render "
                "layer) or companies that only publish this data in PDFs the crawler wasn't "
                "pointed at yet. Try 'Use sample data' in the sidebar to see the full pipeline "
                "output, or upload a PDF/spreadsheet below."
            )

    if uploaded_pdf is not None:
        try:
            new_records = load_pdf_facilities(uploaded_pdf, source_label="Uploaded PDF")
            raw += new_records
            sources_checked += 1
            st.sidebar.success(f"Parsed {len(new_records)} facility mention(s) from uploaded PDF.")
        except Exception as exc:
            st.sidebar.error(f"PDF parse failed: {exc}")
    if uploaded_csv is not None:
        try:
            new_records = load_csv_facilities(uploaded_csv, source_label="Uploaded Spreadsheet")
            raw += new_records
            sources_checked += 1
            st.sidebar.success(f"Parsed {len(new_records)} row(s) from uploaded spreadsheet.")
        except Exception as exc:
            st.sidebar.error(f"CSV parse failed: {exc}")

    merged = dedupe_and_score(raw)
    included = [r for r in merged if r["facility_type"] not in EXCLUDE_TYPES]
    excluded = [r for r in merged if r["facility_type"] in EXCLUDE_TYPES]

    stats = {
        "sources_checked": sources_checked,
        "facility_count": len(included),
        "excluded_count": len(excluded),
        "high_confidence": len([r for r in included if r["review_status"] == "Verified (cross-source match)"]),
        "needs_review": len([r for r in included if r["review_status"] == "Needs Review"]),
    }

    st.subheader("Research Summary")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Facilities found", stats["facility_count"])
    c2.metric("Sources checked", stats["sources_checked"])
    c3.metric("Cross-source verified", stats["high_confidence"])
    c4.metric("Needs review", stats["needs_review"])
    c5.metric("Excluded (non-operational)", stats["excluded_count"])

    st.divider()
    with st.spinner("Writing research notes..."):
        notes = generate_research_notes(stats, api_key)
    st.markdown(notes)

    st.divider()
    tab1, tab2, tab3 = st.tabs(["🏭 Facilities", "🚫 Excluded (filtered out)", "🔎 Source Log"])

    with tab1:
        if included:
            df = pd.DataFrame([
                {
                    "Facility": r["facility_name"],
                    "Type": r["facility_type"],
                    "Address": r["address"],
                    "City": r["city"],
                    "Confidence": r["confidence"],
                    "Status": r["review_status"],
                    "Sources": r["source_count"],
                }
                for r in included
            ])
            st.dataframe(df, use_container_width=True)
            csv_bytes = df.to_csv(index=False).encode("utf-8")
            st.download_button("⬇️ Download facilities (CSV)", csv_bytes, "facilities.csv", "text/csv")

            with st.expander("View evidence & source links per facility"):
                for r in included:
                    st.markdown(f"**{r['facility_name']}** -- {r['facility_type']} ({r['confidence']}, {r['review_status']})")
                    for s in r["sources"]:
                        st.markdown(f"- *{s['source_type']}* -- [{s['source_url']}]({s['source_url']})" if s['source_url'].startswith("http") else f"- *{s['source_type']}* -- `{s['source_url']}`")
                        if s["evidence"]:
                            st.caption(s["evidence"])
        else:
            st.info("No facilities to show yet. Check 'Use sample data' or run a live pass.")

    with tab2:
        st.write(
            "Facilities filtered out because they're offices, retail, or R&D-only sites, not "
            "operational plants/warehouses/distribution/fulfillment centers. Kept visible here "
            "so the exclusion logic is auditable, not a silent drop."
        )
        if excluded:
            df_ex = pd.DataFrame([
                {
                    "Facility": r["facility_name"],
                    "Type": r["facility_type"],
                    "City": r["city"],
                    "Source": r["sources"][0]["source_type"] if r["sources"] else "",
                }
                for r in excluded
            ])
            st.dataframe(df_ex, use_container_width=True)
        else:
            st.write("Nothing excluded in this pass.")

    with tab3:
        st.write(f"Research stops after checking {max_sources if not use_sample else sources_checked} sources, per the accuracy-over-completeness requirement.")
        if log:
            st.dataframe(pd.DataFrame(log), use_container_width=True)
        else:
            st.write("No source log for this pass.")

    st.divider()
    st.caption(
        "This is an MVP demo running against sample data and a lightweight live-crawl heuristic. "
        "Production version adds: a headless-browser render layer (Playwright) for JS-heavy sites "
        "and interactive maps, sitemap.xml crawling for full site coverage, LLM-based extraction "
        "on every source by default, fuzzy address/name matching for dedup, and a scheduled "
        "re-run so facility lists stay current as companies open and close locations."
    )


if __name__ == "__main__":
    main()
