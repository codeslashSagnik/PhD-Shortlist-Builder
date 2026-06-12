# DECISIONS.md — Design Decisions & Data Quality Trade-offs

This document addresses the data quality problems inherent in automated professor discovery,
explains the design decisions made to handle them, and is honest about what works, what
doesn't, and why specific trade-offs were chosen.

---

## Problem 1 — Keyword-Overlap False Positives

**The problem:** Keyword matching alone is catastrophically unreliable. A search for "trauma-informed" 
pulls Roman history papers. A search for "mental health" pulls management burnout papers and military 
PTSD studies. A search for "NLP" pulls natural language pipe design in engineering.

**How we solved it:** Check 3 in Stage 3 (domain relevance filter) uses an LLM — not keyword matching — 
to evaluate each candidate's paper abstract against the student's actual research area. The prompt 
asks explicitly: *"Is this genuinely the same domain? Answer yes/no and explain."* The LLM catches 
cross-discipline false positives that surface-level keyword matching misses entirely.

**What we didn't solve:** The LLM can still be fooled by very abstract language that genuinely overlaps 
(e.g. a psycholinguistics paper about "language models of depression"). We accept some false positives 
at this stage because Stage 4 scoring (embedding cosine similarity) penalises them further.

**Concrete example from our output:** A candidate from OpenAlex matched on "NLP" — their actual work 
was on NLP pipelines for petroleum geology. The domain check correctly flagged: *"discipline mismatch: 
this is petroleum engineering, not computer science."* Dropped.

---

## Problem 2 — Misidentified Career Stage (Students and Postdocs as "Professors")

**The problem:** Grant databases and paper author lists frequently include PhD students and postdocs.
A paper with five authors all listed at "University of Edinburgh" might have four PhD students and 
one PI. Treating all of them as supervisors is a fundamental data quality failure.

**How we solved it:** Check 2 (career stage filter) has a three-layer approach:
1. **Title field heuristic** — reject known non-faculty strings ("PhD Candidate", "Postdoctoral 
   Researcher", "Research Fellow") and accept known faculty strings ("Professor", "Lecturer", "Reader")
2. **Faculty page scrape** — fetch the person's homepage or university profile page and run the 
   same heuristic on the visible text
3. **LLM fallback** — if both are ambiguous, ask Gemini: *"Is this person a faculty member who 
   can independently supervise PhD students?"*

**What we didn't solve:** Research fellows with their own labs are a grey zone. Some are independent 
PIs, some are not. We default to passing them and let domain/scoring handle it. Some universities 
(especially in Canada) use "Research Scientist" for faculty-equivalent roles — our heuristic may 
incorrectly drop these. A known false-negative category.

**Concrete example:** A Semantic Scholar author named "James Chen" at UCL — no title in the metadata.
Page scrape found: *"James Chen — PhD Student, supervised by Prof. [X]."* Dropped.

**Inactive Researcher Flag:** If a candidate's most recent paper is older than 4 years, we flag them instead of dropping them entirely. We apply a 50% penalty to their final composite score in Stage 4. This sinks them to the bottom of the rankings while preserving coverage, as they might just have had a quiet publishing period but are still highly relevant.

---

## Problem 3 — Geographic Mismatch (Professors Not in Target Country)

**The problem:** OpenAlex and Semantic Scholar return papers with institutional affiliations, but:
- A professor might have a visiting appointment listed alongside their main appointment
- Some authors list multiple institutions across countries
- Country data is sometimes missing entirely

**How we solved it:** 
- Check 1 is a **hard filter** — if the country is confirmed as non-target, the candidate is 
  dropped with no exceptions. Zero-tolerance.
- If country is **unknown** (empty), we pass Check 1 and defer to Check 2, where the faculty 
  page scrape attempts to infer the country from the page content.
- The `COUNTRY_ALIASES` table normalises representations like "gb", "GB", "United Kingdom", 
  "England" all to "UK".

**What we didn't solve:** Visiting professors. If someone has a visiting role in the UK but their 
primary appointment is in India, OpenAlex may list them under the UK institution. We cannot reliably 
detect this without deeper scraping of their full profile. Accepted risk — the student can verify 
manually.

**Trade-off chosen:** False negatives (dropping valid candidates due to missing country) are less 
harmful than false positives (including professors in the wrong country). We err on the side of 
dropping when uncertain, not passing.

---

## Problem 4 — Open Position Eligibility Restrictions

**The problem:** PhD position ads frequently restrict eligibility by citizenship or residency with 
language buried in the ad text:
- "Home fees only" (UK: means UK/Ireland nationals)
- "Must have right to work in the UK"
- "Open to domestic students only" (Canada: means Canadian citizens/PR)
- "UKRI funding — home students eligible"

A student with Indian nationality applying for a "home fees only" position would be wasting an 
application.

**How we solved it:** Check 4 (eligibility filter) runs on every candidate with `open_position: true`:
1. **Hard phrase matching** — catches the most common restriction phrases instantly
2. **LLM extraction** — for any ad that passes the phrase check, ask Gemini to identify any 
   nationality/residency restriction and evaluate whether it excludes the student's nationality

**What we didn't solve:** Some restrictions are implicit. "UKRI Industrial Fellowship" funding is 
home-only by default but the ad might not say so explicitly. We catch explicit restrictions; 
implicit ones require domain knowledge we don't encode.

**Concrete example from real FindAPhD data:** Position ad at University of Edinburgh, NLP-related, 
contained "This studentship is funded by UKRI and is open to Home students only." Our phrase 
matcher caught "home students only" and dropped the candidate. Correct decision.

---

## Problem 5 — Duplicate Professors Across Sources

**The problem:** The same professor can appear up to 5–8 times in the raw candidate pool — once 
per paper per source. Without deduplication, they would be scored multiple times and appear 
multiple times in the output, bloating the results and wasting LLM token budget.

**How we solved it:** The deduplication module (between Stage 2 and Stage 3) uses a composite 
key of `normalized_name + normalized_institution`. When duplicates are merged:
- Evidence (papers, grants) is unioned — keeping the richer picture
- Sources are unioned — so we know they appeared in both OpenAlex and Semantic Scholar
- Non-empty fields (email, homepage, h-index) prefer the richer value

**What we didn't solve:** Name ambiguity. "Wei Zhang" is an extremely common Chinese name — two 
different professors named Wei Zhang at the same institution (different departments) would be 
incorrectly merged. We accept this as a low-frequency edge case.

**Trade-off chosen:** The normalized name key is simple and fast. A more robust approach would 
use ORCID identifiers to link authors across sources, but ORCID coverage is incomplete (~50% of 
academics have registered ORCIDs). We use the name+institution key as the primary signal, with 
ORCID as an optional enrichment when available.

---

## Problem 6 — LLM Rate Limits on Free Tier (Google Gemini)

**The problem:** Free tier LLM APIs (like Google Gemini) have aggressive rate limits. With 300 verified candidates originally needing domain relevance checks (Check 3), a naive implementation would take over 2.5 hours just to run verification.

**How we solved it:**
1. **Removed all LLM calls from Stage 3**. Check 2 (Career) and Check 4 (Eligibility) were rewritten to use robust heuristics and exclusion lists exclusively.
2. Check 3 (Domain) was converted to a 100% embedding-based check. The LLM fallback for ambiguous cases was removed entirely in favor of a tuned cosine similarity threshold.
3. The **disk cache** ensures that the remaining LLM calls (Stage 1 extraction, Stage 5 generation) are cached by their prompt hash.
4. The `utils.gemini` wrapper handles rate limits with exponential backoff.

**Known limitation:** Stage 5 (Why-Match Generation) still requires up to 50 LLM calls for the top candidates. On a completely cold cache, this will still take time, but Stage 3 now processes hundreds of candidates in seconds.

---

## Problem 7 — Tuning the Domain Relevance Threshold (Check 3)

**The problem:** Without an LLM to evaluate ambiguous research domains, the embedding cosine similarity score (using `all-MiniLM-L6-v2`) becomes the sole arbiter of whether a professor matches the student. If the cutoff is too high, we drop relevant professors (false negatives). If too low, we let in professors from different fields (false positives).

**How we solved it:**
- We ran a boundary analysis script (`test_stage3.py`) to examine candidates near the 0.40 mark.
- At `0.40`, the model passed a paper about using LLMs for peer review (pure NLP, no health context), which is a false positive for a student who wants Clinical NLP for depression detection.
- At `0.39`, the model dropped a paper about using ML on MRI scans for Major Depressive Disorder (clinical, but not text-based).
- **Decision:** We raised the threshold to `0.45` to prioritize precision. A pure NLP person is a bad match. A pure MRI person is a bad match. We want candidates operating exactly at the intersection of text analysis and mental health. `0.45` enforces that higher standard.

---

## Problem 8 — Discovery Source Rate Limits & Blocks

**The problem:** External academic and job board APIs have varying levels of stability and rate limits.
- **FindAPhD:** We frequently encounter `403 Forbidden` blocks when scraping FindAPhD aggressively.
- **Semantic Scholar:** The API imposes strict rate limits (`429 Client Error`) and frequently fails on concurrent or rapid queries.

**How we solved it:**
- **OpenAlex Resilience:** OpenAlex is significantly more permissive and stable, and returns an enormous amount of high-quality data. By treating OpenAlex as the primary discovery engine, we remain insulated from FindAPhD and Semantic Scholar failures.
- **Graceful Degradation:** The discovery pipeline handles HTTP errors via `try/except` blocks and caches successful queries. When Semantic Scholar or FindAPhD fail, they return 0 candidates for that specific query rather than crashing the entire pipeline. OpenAlex alone provides more than enough candidates (often 1000+) to feed the funnel.

**What we didn't solve (Known Limitations):** FindAPhD 403 blocks mean we occasionally miss open funded positions. With more time, we would implement a robust proxy rotation system or headless browser to bypass basic anti-scraping measures on FindAPhD.

---

## Problem 9 — Stage 5 LLM Generation Bottleneck

**The problem:** Generating personalised "why_match" blurbs takes ~30 seconds per candidate due to free-tier LLM rate limits. Running this on all 200 ranked candidates would take over an hour.

**How we solved it:** We set `TOP_N_FOR_WHY_MATCH = 50` (configurable via environment variable). Only the top 50 candidates receive a fully personalized LLM-generated blurb. The remaining candidates (ranks 51+) receive an empty string `""` for their `why_match` field but are still fully populated in the output JSON with their evidence, scores, and emails.

**Trade-off chosen:** This dramatically speeds up the pipeline while guaranteeing the top 50 candidates—the ones the student will actually read and apply to—have high-quality, personalized blurbs.

---

## Architectural Decision — Why sentence-transformers Instead of an Embedding API

We use `sentence-transformers` (local, free, no API key) for research overlap scoring instead 
of an embedding API (OpenAI embeddings, Gemini embeddings).

**Reasons:**
1. **Cost:** Embedding APIs charge per token. With 300 candidates × ~500 tokens of abstract text, 
   that's ~150K tokens per run. On OpenAI's ada-002 that's ~$0.015 per run — not free.
2. **Reproducibility:** Local model produces identical embeddings every run. API embeddings can 
   drift with model updates.
3. **Latency:** Local inference on CPU with `all-MiniLM-L6-v2` (~90MB) takes ~0.05s per text. 
   300 candidates take ~15 seconds. API calls would add network latency.
4. **Quality:** `all-MiniLM-L6-v2` achieves 0.63 Spearman on STS Benchmark — sufficient for 
   distinguishing "depression detection NLP" from "petroleum pipeline NLP".

**Trade-off:** A larger model (e.g. `all-mpnet-base-v2`, 420MB) would give better semantic 
discrimination but takes longer to download and run. We chose MiniLM for the right speed/quality 
balance for a take-home project.

---

## What We Would Do With More Time

1. **ORCID-based deduplication** — More reliable than name+institution for cross-source merging
2. **NIH Reporter integration** — Active grants are a strong signal for lab funding. Excluded here 
   due to limited relevance for UK/Canada (NIH is US-only), but UKRI Gateway API would be valuable
3. **h-index enrichment via Semantic Scholar author API** — Most candidates come in with h_index=0 
   because we search by paper, not author. A second-pass author lookup would improve citation scoring
4. **PDF abstract extraction** — Some papers return no abstract via API. Fetching and parsing the 
   PDF would recover these, improving domain relevance scoring
5. **Multi-lingual support** — For students targeting non-English-speaking countries (Germany, 
   Netherlands), keyword matching fails because faculty pages are in local languages
