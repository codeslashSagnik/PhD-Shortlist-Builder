# PhD Shortlist Builder

> Automatically discovers, verifies, scores, and ranks PhD supervisors for a student profile — using real data from OpenAlex, Semantic Scholar, and FindAPhD.

---

## What It Does

Provide a student profile with academic background and research interests. The system:

1. **Parses** the profile using an LLM to extract structured research signals.
2. **Discovers** candidate professors from OpenAlex, Semantic Scholar, and FindAPhD (in parallel).
3. **Verifies** each candidate through 4 sequential filters (country, career stage, domain relevance, eligibility).
4. **Scores** each verified candidate using local embeddings (cosine similarity) + recency + open positions.
5. **Generates** a personalized `why_match` blurb per professor using an LLM, then outputs a clean ranked JSON and UI dashboard.

---

## Folder Structure

```
PhD Shortlist Builder/
├── sample_input/          Example Student profile JSON files
├── sample_output/         Generated output JSON (created at runtime)
├── cache/                 Disk cache for all API responses
├── logs/                  Per-run log files
│
├── pipeline/
│   ├── parser.py          Stage 1 — Profile Parser (LLM keyword extraction)
│   ├── discovery.py       Stage 2 — Candidate Discovery (OpenAlex, S2, FindAPhD)
│   ├── verification.py    Stage 3 — 4-check filtering (country, career, domain, eligibility)
│   ├── scoring.py         Stage 4 — Embedding-based scoring + tier assignment
│   └── generator.py       Stage 5 — Why-match generation + final JSON output
│
├── models/
│   └── student_signal.py  StudentSignal dataclass — internal data contract
│
├── utils/
│   ├── logger.py          Centralised logging (file + stdout)
│   ├── cache_manager.py   SHA256-keyed disk cache for API responses
│   ├── rate_limiter.py    Token-bucket rate limiter per API
│   ├── deduplicator.py    Merges duplicate candidates across sources
│   ├── email_finder.py    Best-effort email discovery from faculty pages
│   └── gemini.py          Google Gemini SDK wrapper with rate limit handling
│
├── config/
│   └── settings.py        All tunable parameters, weights, API keys from .env
│
├── app.py                 Streamlit Web UI Entry Point (Main)
├── run.py                 CLI Entry point
├── requirements.txt
├── .env.example           Copy to .env and fill in your Gemini API key
├── schema.md              Output JSON field documentation
├── DECISIONS.md           Design decisions and data quality trade-offs
└── README.md              This file
```

---

## Installation & Setup

**Requirements:** Python 3.10+

### 1. Clone the repository
```bash
git clone https://github.com/codeslashSagnik/PhD-Shortlist-Builder.git
cd PhD-Shortlist-Builder
```

### 2. Set up a virtual environment
```bash
python -m venv venv

# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure API Keys
The system relies on Google's free Gemini API tier to power the LLM stages.

1. Go to [https://aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)
2. Sign in and click **Create Key**.
3. Copy the `.env.example` file to `.env`:
   ```bash
   copy .env.example .env       # Windows
   cp .env.example .env         # macOS/Linux
   ```
4. Open `.env` and paste your Gemini key:
   ```env
   GEMINI_API_KEY=your_gemini_api_key_here
   ```
   *(Note: The system is configured by default to use `gemini-2.5-flash` which is highly performant and free).*

---

## Running the Application

### 1. Web UI (Recommended)

The easiest way to use the system is via the interactive Streamlit dashboard. It features zero-hardcoding dropdowns, native sample profile loading, and clean candidate cards.

```bash
streamlit run app.py
```
- A browser window will open automatically.
- You can fill out the form manually using dropdowns, OR load one of the sample profiles from the sidebar.
- Click **🚀 Generate Shortlist** to run the pipeline.

### 2. CLI (Batch / Terminal)

If you prefer to run the pipeline strictly from the terminal and get a JSON output file:

```bash
python run.py --profile sample_input/student_001.json
```

#### CLI Options

| Flag | Description |
|------|-------------|
| `--profile PATH` | **(Required)** Path to student profile JSON |
| `--clear-cache` | Clear all cached API responses before running |

---

## Expected Runtime

| Stage | Typical Time |
|-------|-------------|
| Stage 1 — Parsing | ~5s |
| Stage 2 — Discovery | ~60–120s (parallel API calls) |
| Stage 3 — Verification | ~120–300s (LLM calls per candidate) |
| Stage 4 — Scoring | ~20–40s (local embeddings) |
| Stage 5 — Generation | ~60–120s (LLM calls for top 50) |
| **Total** | **~5–10 minutes** |

On a second run with the exact same input (cache warm): **~15–30 seconds**.

---

## Data Sources & Costs

| Source | What We Get | Free? | Rate Limit |
|--------|-------------|-------|------------|
| [OpenAlex](https://openalex.org) | Papers, authors, institutions, countries | ✅ Yes | 10 req/s (polite pool) |
| [Semantic Scholar](https://api.semanticscholar.org) | Papers, authors, abstracts | ✅ Yes (1 req/s) | Higher with free API key |
| [FindAPhD.com](https://www.findaphd.com) | Open PhD positions, eligibility | ✅ Yes (scraping) | 0.5 req/s (polite) |
| [Google Gemini API](https://aistudio.google.com/app/apikey) | LLM extraction, verification, generation | ✅ Free tier | ~15 req/min |
| [sentence-transformers](https://sbert.net) | Local embeddings (all-MiniLM-L6-v2) | ✅ Fully local | Unlimited |

**Total Operating Cost: £0 / $0**

---

## Known Limitations

1. **No Google Scholar scraping** — Google actively blocks bots. Excluded deliberately.
2. **Semantic Scholar missing country info** — S2 author profiles don't always have institution country. Country is inferred from faculty page scrape in Stage 3.
3. **Email coverage ~60%** — Many faculty pages don't expose emails in machine-readable form (obfuscated as images or JS-rendered). We never guess.
4. **FindAPhD positions may be stale** — Position ads are not always updated when filled. Treat `open_position: true` as a strong signal, not a guarantee.
5. **LLM rate limits** — The free Gemini API can sometimes encounter 429 Rate Limits. The pipeline will automatically retry and backoff. If it ultimately fails, the Streamlit UI will catch the error safely.
6. **QS ranking coverage** — Only major UK and Canada universities are in the embedded ranking table. Unranked institutions default to "safety" tier based on score.

---

## Logs & Cache

- **Logs:** Every run creates a timestamped log at `logs/pipeline_YYYYMMDD_HHMMSS.log` capturing every pipeline decision, check pass/fail, and Gemini API attempt.
- **Cache:** The system caches API responses in the `cache/` directory using SHA-256 hashes of the prompts. This prevents duplicate API calls and saves time on subsequent runs. To clear the cache, delete the `cache/` folder or run the CLI with `--clear-cache`.
