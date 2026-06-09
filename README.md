# PhD Shortlist Builder

> Automatically discovers, verifies, scores, and ranks PhD supervisors for a student profile — using real data from OpenAlex, Semantic Scholar, and FindAPhD.

---

## What It Does

Feed in a student profile JSON. The system:

1. **Parses** the profile using Gemini to extract structured research signals
2. **Discovers** candidate professors from OpenAlex, Semantic Scholar, and FindAPhD (in parallel)
3. **Verifies** each candidate through 4 sequential filters (country, career stage, domain relevance, eligibility)
4. **Scores** each verified candidate using local embeddings (cosine similarity) + recency + open positions
5. **Generates** a personalized `why_match` blurb per professor using Gemini, then outputs a clean ranked JSON

---

## Folder Structure

```
PhD Shortlist Builder/
├── sample_input/          Student profile JSON files
│   └── student_001.json   Example: NLP/mental health student targeting UK + Canada
├── sample_output/         Generated output JSON (gitignored, created at runtime)
├── cache/                 Disk cache for all API responses (gitignored)
├── logs/                  Per-run log files (gitignored)
│
├── pipeline/
│   ├── parser.py          Stage 1 — Profile Parser (Gemini keyword extraction)
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
│   └── email_finder.py    Best-effort email discovery from faculty pages
│
├── config/
│   └── settings.py        All tunable parameters, weights, API keys from .env
│
├── run.py                 Single entry point
├── requirements.txt
├── .env.example           Copy to .env and fill in your Gemini key
├── schema.md              Output JSON field documentation
├── DECISIONS.md           Design decisions and data quality trade-offs
└── README.md              This file
```

---

## Installation

**Requirements:** Python 3.10+

```bash
# 1. Clone the repo
git clone https://github.com/codeslashSagnik/PhD-Shortlist-Builder.git
cd PhD-Shortlist-Builder

# 2. Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up your API key
copy .env.example .env       # Windows
# cp .env.example .env       # macOS/Linux
# Edit .env and add your Gemini API key
```

### Getting a Free Gemini API Key

1. Go to [https://aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)
2. Sign in with a Google account
3. Click **Create API Key**
4. Copy it into your `.env` file:
   ```
   GEMINI_API_KEY=your_key_here
   ```

The **Gemini 1.5 Flash** model used here is on the free tier (15 requests/minute, 1M tokens/day).

---

## Running

```bash
python run.py --profile sample_input/student_001.json
```

### Options

| Flag | Description |
|------|-------------|
| `--profile PATH` | **(Required)** Path to student profile JSON |
| `--clear-cache` | Clear all cached API responses before running |

### Expected Runtime

| Stage | Typical Time |
|-------|-------------|
| Stage 1 — Parsing | ~5s |
| Stage 2 — Discovery | ~60–120s (parallel API calls) |
| Stage 3 — Verification | ~120–300s (LLM calls per candidate) |
| Stage 4 — Scoring | ~20–40s (local embeddings) |
| Stage 5 — Generation | ~60–120s (LLM calls for top 50) |
| **Total** | **~5–10 minutes** |

On second run (cache warm): **~30 seconds**.

---

## Output

The pipeline writes `sample_output/<student_id>.json`.

See [schema.md](schema.md) for full field documentation.

**Example top-level structure:**
```json
{
  "schema_version": "1.0",
  "student_id": "student_001",
  "student_name": "Priya Sharma",
  "generated_at": "2024-11-15T10:30:00Z",
  "summary": {
    "total_candidates": 48,
    "reach": 8,
    "target": 24,
    "safety": 16,
    "with_open_positions": 12,
    "with_email": 31
  },
  "candidates": [...]
}
```

---

## Data Sources

| Source | What We Get | Free? | Rate Limit |
|--------|-------------|-------|------------|
| [OpenAlex](https://openalex.org) | Papers, authors, institutions, countries | ✅ Yes | 10 req/s (polite pool) |
| [Semantic Scholar](https://api.semanticscholar.org) | Papers, authors, abstracts | ✅ Yes (1 req/s) | Higher with free API key |
| [FindAPhD.com](https://www.findaphd.com) | Open PhD positions, eligibility | ✅ Yes (scraping) | 0.5 req/s (polite) |
| [Gemini 1.5 Flash](https://aistudio.google.com) | LLM extraction, verification, generation | ✅ Free tier | 15 req/min |
| [sentence-transformers](https://sbert.net) | Local embeddings (all-MiniLM-L6-v2) | ✅ Fully local | Unlimited |

**Total cost: £0 / $0**

---

## Known Limitations

1. **No Google Scholar scraping** — Google actively blocks bots. Excluded deliberately.
2. **Semantic Scholar missing country info** — S2 author profiles don't always have institution country. Country is inferred from faculty page scrape in Stage 3.
3. **Email coverage ~60%** — Many faculty pages don't expose emails in machine-readable form (obfuscated as images or JS-rendered). We never guess.
4. **FindAPhD positions may be stale** — Position ads are not always updated when filled. Treat `open_position: true` as a strong signal, not a guarantee.
5. **LLM domain check can be slow** — Stage 3 makes one Gemini call per candidate for domain relevance. With 500 candidates this can take ~5 minutes. The cache makes re-runs instant.
6. **QS ranking coverage** — Only major UK and Canada universities are in the embedded ranking table. Unranked institutions default to "safety" tier based on score.

---

## Logs

Every run creates a timestamped log at `logs/pipeline_YYYYMMDD_HHMMSS.log`.

The log captures every pipeline decision:
- Which candidates were discovered from which source
- Which candidates failed which check and why
- LLM call timing and token usage
- Score breakdowns for every candidate
- Final output summary
