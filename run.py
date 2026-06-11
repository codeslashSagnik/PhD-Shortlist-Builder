"""
run.py  —  Single entry point for the PhD Shortlist Builder pipeline.

Usage:
    python run.py --profile sample_input/student_001.json

What it does:
    1. Loads the student profile JSON
    2. Runs Stage 1 (parser)        → StudentSignal
    3. Runs Stage 2 (discovery)     → raw candidates
    4. Deduplicates the raw pool
    5. Runs Stage 3 (verification)  → filtered candidates
    6. Runs Stage 4 (scoring)       → ranked candidates
    7. Runs Stage 5 (generator)     → final JSON output

All logs are written to logs/pipeline_<timestamp>.log
Output JSON is written to sample_output/<student_id>.json
"""

import argparse
import json
import sys
import time
from pathlib import Path

# ── Path setup (so imports work from project root) ────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.logger import get_logger, get_log_file_path
from utils.deduplicator import deduplicate_candidates
from pipeline.parser import parse_profile
from pipeline.discovery import discover_candidates
from pipeline.verification import verify_candidates
from pipeline.scoring import score_and_rank
from pipeline.generator import generate_output

log = get_logger("run")


def load_profile(profile_path: str) -> dict:
    """Load and validate the student profile JSON."""
    path = Path(profile_path)
    if not path.exists():
        log.error("Profile file not found: %s", profile_path)
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        profile = json.load(f)
    log.info("Loaded profile: %s", profile_path)
    return profile


def run_pipeline(profile_path: str = None, profile_dict: dict = None) -> dict:
    """
    Execute the full 5-stage pipeline end-to-end.
    Accepts either a path to a JSON file or a loaded dictionary.
    Returns the final output dict.
    """
    pipeline_start = time.time()

    log.info("╔══════════════════════════════════════════════════════╗")
    log.info("║         PhD SHORTLIST BUILDER — Pipeline Start       ║")
    log.info("╠══════════════════════════════════════════════════════╣")
    
    if profile_dict is not None:
        profile = profile_dict
        log.info("║  Profile : %-41s ║", "In-memory dictionary")
    elif profile_path is not None:
        profile = load_profile(profile_path)
        log.info("║  Profile : %-41s ║", Path(profile_path).name)
    else:
        log.error("Must provide either profile_path or profile_dict")
        sys.exit(1)
    log.info("║  Log     : %-42s ║", get_log_file_path().name)
    log.info("╚══════════════════════════════════════════════════════╝")

    # ── Stage 1: Profile Parser ───────────────────────────────────────────────
    t0 = time.time()
    signal = parse_profile(profile)
    log.info("Stage 1 completed in %.1fs", time.time() - t0)

    # ── Stage 2: Candidate Discovery ──────────────────────────────────────────
    t0 = time.time()
    raw_candidates = discover_candidates(signal)
    log.info("Stage 2 completed in %.1fs — %d raw candidates", time.time() - t0, len(raw_candidates))

    # ── Deduplication (between Stage 2 and 3) ────────────────────────────────
    t0 = time.time()
    deduped_candidates = deduplicate_candidates(raw_candidates)
    log.info("Deduplication completed in %.1fs — %d unique candidates", time.time() - t0, len(deduped_candidates))

    # ── Stage 3: Verification & Filtering ────────────────────────────────────
    t0 = time.time()
    verified_candidates = verify_candidates(deduped_candidates, signal)
    log.info("Stage 3 completed in %.1fs — %d verified candidates", time.time() - t0, len(verified_candidates))

    if not verified_candidates:
        log.warning("No candidates passed verification. Check your keywords or target countries.")
        log.warning("Tip: Broaden keywords in the student profile or check API connectivity.")

    # ── Stage 4: Scoring & Ranking ────────────────────────────────────────────
    t0 = time.time()
    ranked_candidates = score_and_rank(verified_candidates, signal)
    log.info("Stage 4 completed in %.1fs — %d ranked candidates", time.time() - t0, len(ranked_candidates))

    # ── Stage 5: Why-Match Generation & Output ────────────────────────────────
    t0 = time.time()
    output = generate_output(ranked_candidates, signal)
    log.info("Stage 5 completed in %.1fs", time.time() - t0)

    # ── Pipeline summary ──────────────────────────────────────────────────────
    total_time = time.time() - pipeline_start
    output_path = PROJECT_ROOT / "sample_output" / f"{signal.student_id}.json"

    log.info("")
    log.info("╔══════════════════════════════════════════════════════╗")
    log.info("║              PIPELINE COMPLETE ✓                     ║")
    log.info("╠══════════════════════════════════════════════════════╣")
    log.info("║  Total time     : %-34.1fs ║", total_time)
    log.info("║  Candidates out : %-34d ║", len(output.get("candidates", [])))
    log.info("║  Reach          : %-34d ║", output["summary"]["reach"])
    log.info("║  Target         : %-34d ║", output["summary"]["target"])
    log.info("║  Safety         : %-34d ║", output["summary"]["safety"])
    log.info("║  Open positions : %-34d ║", output["summary"]["with_open_positions"])
    log.info("║  With email     : %-34d ║", output["summary"]["with_email"])
    log.info("╠══════════════════════════════════════════════════════╣")
    log.info("║  Output JSON : %-37s ║", output_path.name)
    log.info("║  Log file    : %-37s ║", get_log_file_path().name)
    log.info("╚══════════════════════════════════════════════════════╝")

    print(f"\n✅  Done! Output written to: {output_path}")
    print(f"📋  Log file: {get_log_file_path()}")

    return output


def main():
    parser = argparse.ArgumentParser(
        description="PhD Shortlist Builder — finds and ranks PhD supervisors for a student profile.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run.py --profile sample_input/student_001.json
  python run.py --profile sample_input/student_001.json --clear-cache
        """,
    )
    parser.add_argument(
        "--profile",
        required=True,
        help="Path to the student profile JSON file (e.g. sample_input/student_001.json)",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Clear all cached API responses before running",
    )
    args = parser.parse_args()

    if args.clear_cache:
        from utils.cache_manager import cache_clear
        log.info("Clearing cache as requested...")
        cache_clear()

    run_pipeline(args.profile)


if __name__ == "__main__":
    main()
