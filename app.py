"""
app.py — PhD Shortlist Builder Web UI
"""
import json
import streamlit as st
from pathlib import Path
from run import run_pipeline

# ─── PAGE CONFIG ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="PhD Shortlist Builder",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── STYLES ─────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
[data-testid="stSidebar"] { min-width: 370px; }
.metric-row { display: flex; gap: 1rem; margin-bottom: 1.5rem; }

.tier-reach  { color: #f0abfc; font-weight: bold; }
.tier-target { color: #86efac; font-weight: bold; }
.tier-safety { color: #93c5fd; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

# ─── CONSTANTS ──────────────────────────────────────────────────────────────────
ALL_COUNTRIES = [
    "USA", "UK", "Canada", "Germany", "Netherlands", "Australia",
    "Switzerland", "Sweden", "Singapore", "France", "Denmark",
    "Austria", "Belgium", "Finland", "Norway",
]
ALL_DISCIPLINES = [
    "Computer Science", "Electrical Engineering", "Statistics / Mathematics",
    "Bioinformatics / Computational Biology", "Cognitive Science",
    "Information Science", "Data Science", "Physics", "Economics",
]
ALL_AREAS = [
    "Machine Learning / Deep Learning", "NLP / Computational Linguistics",
    "Computer Vision", "Reinforcement Learning / Autonomous Systems",
    "Robotics", "Machine Learning Systems / MLOps", "Graph Neural Networks",
    "Computational Biology / Drug Discovery", "Mental Health Informatics",
    "Human-Computer Interaction", "AI Safety / Alignment",
    "Federated Learning / Privacy", "Medical Imaging",
    "Speech and Audio Processing", "Knowledge Graphs / Reasoning",
]
ALL_COURSEWORK = [
    "Machine Learning", "Deep Learning", "Natural Language Processing",
    "Computer Vision", "Reinforcement Learning", "Probabilistic Modelling",
    "Computational Linguistics", "Bioinformatics Algorithms",
    "Graph Neural Networks", "Computer Architecture", "Distributed Systems",
    "Optimisation for Machine Learning", "Human-Computer Interaction",
    "Ethics in AI", "Control Systems", "Robotics",
    "Structural Bioinformatics", "Genomics and Proteomics",
    "Embedded Systems", "Signal Processing",
]
ALL_SEMESTERS = ["Fall", "Spring", "Winter"]
ALL_NATIONALITIES = [
    "Indian", "Chinese", "Pakistani", "Bangladeshi", "Sri Lankan",
    "Mexican", "Brazilian", "Nigerian", "Kenyan", "Omani",
    "Saudi Arabian", "Iranian", "Turkish", "Indonesian", "Vietnamese",
    "Korean", "Japanese", "German", "French", "British", "American",
]
ALL_LEVELS = ["Bachelors", "Masters"]
ALL_VENUES = [
    "NeurIPS", "ICML", "ICLR", "ACL", "EMNLP", "NAACL", "CVPR",
    "ICCV", "ECCV", "AAAI", "IJCAI", "KDD", "WWW", "SIGIR",
    "ACL Student Research Workshop", "NeurIPS Workshop",
    "Nature", "Science", "Cell", "Bioinformatics (Oxford)",
    "Other",
]


# ─── HELPERS ────────────────────────────────────────────────────────────────────
def get_sample_files() -> dict:
    folder = Path("sample_input")
    files = {}
    if folder.exists():
        for f in sorted(folder.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                label = f"{data.get('personal', {}).get('name', f.stem)} — {f.name}"
                files[label] = data
            except Exception:
                pass
    return files


def profile_from_state(s: dict) -> dict:
    """Build the profile dict from st.session_state values."""
    projects = []
    for i in range(s.get("num_projects", 0)):
        name = s.get(f"proj_name_{i}", "").strip()
        desc = s.get(f"proj_desc_{i}", "").strip()
        year = s.get(f"proj_year_{i}", 2024)
        if name and desc:
            projects.append({"name": name, "description": desc, "year": year})

    publications = []
    for i in range(s.get("num_pubs", 0)):
        title = s.get(f"pub_title_{i}", "").strip()
        venue = s.get(f"pub_venue_{i}", "").strip()
        year = s.get(f"pub_year_{i}", 2024)
        doi = s.get(f"pub_doi_{i}", "").strip()
        if title:
            publications.append({"title": title, "venue": venue, "year": year, "doi": doi})

    gre_v = s.get("gre_verbal", 0)
    gre_q = s.get("gre_quant", 0)
    gre_a = s.get("gre_awa", 0.0)
    ielts = s.get("ielts", 0.0)

    test_scores = {}
    if gre_v or gre_q:
        test_scores["GRE"] = {"verbal": gre_v, "quant": gre_q, "awa": gre_a}
    if ielts:
        test_scores["IELTS"] = ielts

    return {
        "student_id": f"ui_{s.get('name','student').replace(' ','_').lower()}",
        "personal": {
            "name": s.get("name", ""),
            "nationality": s.get("nationality", ""),
            "current_degree": s.get("current_degree", ""),
            "current_institution": s.get("current_institution", ""),
            "gpa": s.get("gpa", 0.0),
            "level_applying_for": "PhD",
            "student_level": s.get("student_level", "Bachelors"),
        },
        "target": {
            "countries": s.get("countries", []),
            "intake": {
                "semester": s.get("semester", "Fall"),
                "year": s.get("intake_year", 2025),
            },
            "funding_required": s.get("funding_required", True),
            "open_to_self_funded": not s.get("funding_required", True),
        },
        "research": {
            "thesis_topic": s.get("thesis_topic", ""),
            "primary_area": s.get("primary_area", ""),
            "secondary_area": s.get("secondary_area", ""),
            "discipline": s.get("discipline", ""),
        },
        "academic_background": {
            "publications": publications,
            "projects": projects,
            "relevant_coursework": s.get("coursework", []),
            "test_scores": test_scores,
        },
    }


def load_profile_into_state(p: dict):
    """Populate st.session_state from a loaded profile dict."""
    personal = p.get("personal", {})
    target = p.get("target", {})
    research = p.get("research", {})
    bg = p.get("academic_background", {})
    ts = bg.get("test_scores", {})
    gre = ts.get("GRE", {})

    st.session_state["name"] = personal.get("name", "")
    st.session_state["nationality"] = personal.get("nationality", "")
    st.session_state["current_degree"] = personal.get("current_degree", "")
    st.session_state["current_institution"] = personal.get("current_institution", "")
    st.session_state["gpa"] = float(personal.get("gpa", 0.0))
    st.session_state["student_level"] = personal.get("student_level", "Bachelors")

    st.session_state["countries"] = target.get("countries", [])
    st.session_state["semester"] = target.get("intake", {}).get("semester", "Fall")
    st.session_state["intake_year"] = target.get("intake", {}).get("year", 2025)
    st.session_state["funding_required"] = target.get("funding_required", True)

    st.session_state["thesis_topic"] = research.get("thesis_topic", "")
    st.session_state["primary_area"] = research.get("primary_area", "")
    st.session_state["secondary_area"] = research.get("secondary_area", "")
    st.session_state["discipline"] = research.get("discipline", "")

    st.session_state["coursework"] = bg.get("relevant_coursework", [])
    st.session_state["gre_verbal"] = gre.get("verbal", 0)
    st.session_state["gre_quant"] = gre.get("quant", 0)
    st.session_state["gre_awa"] = float(gre.get("awa", 0.0))
    st.session_state["ielts"] = float(ts.get("IELTS", 0.0))

    projects = bg.get("projects", [])
    st.session_state["num_projects"] = len(projects)
    for i, proj in enumerate(projects):
        st.session_state[f"proj_name_{i}"] = proj.get("name", "")
        st.session_state[f"proj_desc_{i}"] = proj.get("description", "")
        st.session_state[f"proj_year_{i}"] = proj.get("year", 2024)

    pubs = bg.get("publications", [])
    st.session_state["num_pubs"] = len(pubs)
    for i, pub in enumerate(pubs):
        st.session_state[f"pub_title_{i}"] = pub.get("title", "")
        st.session_state[f"pub_venue_{i}"] = pub.get("venue", "")
        st.session_state[f"pub_year_{i}"] = pub.get("year", 2024)
        st.session_state[f"pub_doi_{i}"] = pub.get("doi", "")


# ─── INIT STATE ─────────────────────────────────────────────────────────────────
defaults = {
    "name": "", "nationality": "", "current_degree": "", "current_institution": "",
    "gpa": 0.0, "student_level": "Bachelors", "countries": [], "semester": "Fall",
    "intake_year": 2025, "funding_required": True, "thesis_topic": "", "primary_area": "",
    "secondary_area": "", "discipline": "", "coursework": [], "gre_verbal": 0,
    "gre_quant": 0, "gre_awa": 0.0, "ielts": 0.0, "num_projects": 1, "num_pubs": 0,
    "proj_name_0": "", "proj_desc_0": "", "proj_year_0": 2024,
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ─── HEADER ─────────────────────────────────────────────────────────────────────
st.title("🎓 PhD Shortlist Builder")
st.markdown("Automated supervisor discovery & ranking engine powered by Semantic Scholar + LLM.")

# ─── SIDEBAR ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Student Profile")

    # JSON upload or sample picker
    with st.expander("📂 Load from JSON", expanded=False):
        uploaded = st.file_uploader("Upload student JSON", type=["json"], key="json_upload")
        if uploaded:
            try:
                p = json.load(uploaded)
                load_profile_into_state(p)
                st.success(f"Loaded: {uploaded.name}")
            except Exception as e:
                st.error(f"Invalid JSON: {e}")

        samples = get_sample_files()
        if samples:
            st.markdown("**Or load a sample:**")
            for label, data in samples.items():
                if st.button(label, use_container_width=True, key=f"sample_{label}"):
                    load_profile_into_state(data)
                    st.rerun()

    st.divider()

    # ── Personal Info ──
    with st.expander("👤 Personal Info", expanded=True):
        st.text_input("Full Name", key="name")
        st.selectbox("Nationality", ALL_NATIONALITIES, key="nationality",
                     index=ALL_NATIONALITIES.index(st.session_state["nationality"])
                     if st.session_state["nationality"] in ALL_NATIONALITIES else 0)
        st.text_input("Current Degree (e.g. BTech Computer Science)", key="current_degree")
        st.text_input("Current Institution", key="current_institution")
        st.number_input("GPA", min_value=0.0, max_value=10.0, step=0.1, key="gpa")
        st.selectbox("Applying Level", ALL_LEVELS, key="student_level")

    # ── Target ──
    with st.expander("🎯 Target Preferences", expanded=True):
        st.multiselect("Target Countries", ALL_COUNTRIES, key="countries")
        c1, c2 = st.columns(2)
        with c1:
            st.selectbox("Semester", ALL_SEMESTERS, key="semester")
        with c2:
            st.number_input("Year", min_value=2025, max_value=2030, step=1, key="intake_year")
        st.checkbox("Funding Required", key="funding_required")

    # ── Research ──
    with st.expander("🔬 Research Profile", expanded=True):
        st.text_area("Thesis Topic / Research Interest", key="thesis_topic", height=100)
        st.selectbox("Discipline", ALL_DISCIPLINES, key="discipline",
                     index=ALL_DISCIPLINES.index(st.session_state["discipline"])
                     if st.session_state["discipline"] in ALL_DISCIPLINES else 0)
        st.selectbox("Primary Research Area", ALL_AREAS, key="primary_area",
                     index=ALL_AREAS.index(st.session_state["primary_area"])
                     if st.session_state["primary_area"] in ALL_AREAS else 0)
        st.selectbox("Secondary Research Area", ALL_AREAS, key="secondary_area",
                     index=ALL_AREAS.index(st.session_state["secondary_area"])
                     if st.session_state["secondary_area"] in ALL_AREAS else 0)

    # ── Projects ──
    with st.expander("💡 Projects"):
        num_projects = st.number_input("Number of projects", min_value=0, max_value=6,
                                        step=1, key="num_projects")
        for i in range(int(num_projects)):
            st.markdown(f"**Project {i+1}**")
            if f"proj_name_{i}" not in st.session_state:
                st.session_state[f"proj_name_{i}"] = ""
            if f"proj_desc_{i}" not in st.session_state:
                st.session_state[f"proj_desc_{i}"] = ""
            if f"proj_year_{i}" not in st.session_state:
                st.session_state[f"proj_year_{i}"] = 2024
            st.text_input(f"Name", key=f"proj_name_{i}")
            st.text_area(f"Description", key=f"proj_desc_{i}", height=80)
            st.number_input(f"Year", min_value=2015, max_value=2025, step=1,
                            key=f"proj_year_{i}")

    # ── Publications ──
    with st.expander("📝 Publications"):
        num_pubs = st.number_input("Number of publications", min_value=0, max_value=10,
                                    step=1, key="num_pubs")
        for i in range(int(num_pubs)):
            st.markdown(f"**Publication {i+1}**")
            for k, d in [(f"pub_title_{i}", ""), (f"pub_venue_{i}", ""), (f"pub_doi_{i}", "")]:
                if k not in st.session_state:
                    st.session_state[k] = d
            if f"pub_year_{i}" not in st.session_state:
                st.session_state[f"pub_year_{i}"] = 2024
            st.text_input("Title", key=f"pub_title_{i}")
            st.selectbox("Venue", ALL_VENUES, key=f"pub_venue_{i}",
                         index=ALL_VENUES.index(st.session_state[f"pub_venue_{i}"])
                         if st.session_state[f"pub_venue_{i}"] in ALL_VENUES else len(ALL_VENUES)-1)
            st.number_input("Year", min_value=2015, max_value=2025, step=1,
                            key=f"pub_year_{i}")
            st.text_input("DOI (optional)", key=f"pub_doi_{i}")

    # ── Coursework & Scores ──
    with st.expander("📚 Coursework & Test Scores"):
        st.multiselect("Relevant Coursework", ALL_COURSEWORK, key="coursework")
        st.markdown("**GRE (optional)**")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.number_input("Verbal", 130, 170, step=1, key="gre_verbal")
        with c2:
            st.number_input("Quant", 130, 170, step=1, key="gre_quant")
        with c3:
            st.number_input("AWA", 0.0, 6.0, step=0.5, key="gre_awa")
        st.number_input("IELTS (optional)", 0.0, 9.0, step=0.5, key="ielts")

    st.divider()
    run_button = st.button("🚀 Generate Shortlist", use_container_width=True, type="primary")

# ─── MAIN AREA ──────────────────────────────────────────────────────────────────

if run_button:
    profile = profile_from_state(st.session_state)
    if not profile["personal"]["name"] or not profile["research"]["thesis_topic"]:
        st.error("Please fill in at least the Name and Thesis Topic before running.")
    elif not profile["target"]["countries"]:
        st.error("Please select at least one target country.")
    else:
        with st.spinner("Pipeline running... ~2 minutes. Watch your terminal for live logs."):
            try:
                output = run_pipeline(profile_dict=profile)
                st.session_state["output"] = output
                st.success("✅ Shortlist generated!")
            except Exception as e:
                st.error(f"❌ Pipeline failed: {e}")
                st.info("Check the terminal logs for more details. If you hit an OpenRouter rate limit, please wait a minute and try again.")

if "output" in st.session_state:
    output = st.session_state["output"]
    candidates = output.get("candidates", [])
    summary = output.get("summary", {})

    # ── Summary metrics ──
    st.subheader("📊 Summary")
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total", summary.get("total_candidates", 0))
    m2.metric("🔴 Reach", summary.get("reach", 0))
    m3.metric("🟢 Target", summary.get("target", 0))
    m4.metric("🔵 Safety", summary.get("safety", 0))
    m5.metric("📧 With Email", summary.get("with_email", 0))

    st.divider()

    # ── Filters ──
    fc1, fc2 = st.columns([1, 2])
    with fc1:
        top_n = st.slider(
            "Show Top N", min_value=1,
            max_value=max(1, len(candidates)),
            value=min(20, len(candidates)),
        )
    with fc2:
        tiers = st.multiselect(
            "Filter by Tier",
            ["reach", "target", "safety"],
            default=["reach", "target", "safety"],
        )

    filtered = [c for c in candidates if c.get("tier") in tiers][:top_n]
    st.subheader(f"Showing {len(filtered)} candidates")

    # ── Candidate cards ──
    TIER_COLORS = {
        "reach":  ("#4a154b", "#f0abfc"),
        "target": ("#064e3b", "#86efac"),
        "safety": ("#1e3a5f", "#93c5fd"),
    }

    for cand in filtered:
        tier = cand.get("tier", "safety")
        bg, fg = TIER_COLORS.get(tier, ("#333", "#fff"))
        rank = cand.get("rank", "?")
        name_val = cand.get("name", "Unknown")
        institution = cand.get("institution", "Unknown")
        country = cand.get("country", "Unknown")
        focus = cand.get("research_focus", "")
        why = cand.get("why_match", "")
        score = cand.get("score", "N/A")
        email = cand.get("email", "")
        homepage = cand.get("homepage", "")
        open_pos = cand.get("open_position", False)
        qs = cand.get("qs_rank")
        evidence = cand.get("evidence", [])

        # Badge row
        badge_parts = [f'<span style="background:{bg};color:{fg};padding:2px 10px;border-radius:12px;font-size:0.8rem;font-weight:bold;border:1px solid {fg};">{tier.upper()}</span>']
        if open_pos:
            badge_parts.append('<span style="background:#7c2d12;color:#fde68a;padding:2px 8px;border-radius:12px;font-size:0.75rem;border:1px solid #fde68a;">🔥 OPEN POSITION</span>')
        if qs:
            badge_parts.append(f'<span style="background:#1c1917;color:#d4d4d4;padding:2px 8px;border-radius:12px;font-size:0.75rem;">QS #{qs}</span>')
        badges_html = " ".join(badge_parts)

        with st.container(border=True):
            # Header
            st.markdown(
                f"### #{rank} &nbsp; {name_val} &nbsp;&nbsp; {badges_html}",
                unsafe_allow_html=True,
            )
            col_info, col_score = st.columns([4, 1])
            with col_info:
                st.markdown(f"🏢 **{institution}** &nbsp;•&nbsp; 🌍 {country}")
                if focus:
                    st.markdown(f"🔬 *{focus}*")
            with col_score:
                st.metric("Score", f"{score:.3f}" if isinstance(score, float) else score)

            # Why-match blurb
            if why:
                st.info(why)
            else:
                st.caption("No why-match blurb generated.")

            # Evidence expander
            links, papers = [], []
            for ev in evidence:
                if ev.get("type") == "paper":
                    papers.append(ev)

            extra_cols = st.columns(3)
            if email:
                extra_cols[0].markdown(f"📧 `{email}`")
            if homepage:
                extra_cols[1].markdown(f"[🔗 Homepage]({homepage})")
            if open_pos and extra_cols[2]:
                extra_cols[2].markdown("✅ Open position advertised")

            if papers:
                with st.expander(f"📄 {len(papers)} paper(s)"):
                    for p in papers:
                        url = p.get("url", "#")
                        title = p.get("title", "Untitled")
                        year = p.get("year", "")
                        st.markdown(f"- [{title}]({url}) ({year})")

        st.write("")  # spacing
