"""Pramaan — Streamlit frontend. Two screens only (per spec section 1):
  1. Verify screen  — submit a claim, screenshot, voice note, or PDF
  2. Explain screen — submit a gov notification PDF, get plain-language
                     summary + rights + eligibility + impact + Hindi audio

Audit trail is shown inline for every request so judges can see the
agent's reasoning steps.
"""
from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

import streamlit as st
import httpx

# Allow `import backend...` when running from /pramaan
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# --- Config ---
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
DEFAULT_LANG = os.getenv("DEFAULT_OUTPUT_LANG", "en")

# --- Page setup ---
st.set_page_config(
    page_title="Pramaan — Civic Verification & Explainer",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Inject custom CSS
_css_path = Path(__file__).parent / "styles.css"
if _css_path.exists():
    st.markdown(f"<style>{_css_path.read_text()}</style>", unsafe_allow_html=True)

# --- Header ---
st.markdown(
    "<div class='pr-header'>"
    "<h1>🛡️ Pramaan</h1>"
    "<p>Civic verification + explainer agent. Every verdict carries a real source URL with timestamp.</p>"
    "</div>",
    unsafe_allow_html=True,
)

# --- Mode toggle ---
mode = st.radio(
    "Choose a screen",
    ["Verify a claim", "Explain a document"],
    horizontal=True,
    label_visibility="collapsed",
)



# --- Helpers ---
def _call_backend(path: str, payload: dict, files: dict | None = None):
    """POST to the FastAPI backend. Returns the JSON response, or raises."""
    r = httpx.post(f"{BACKEND_URL}{path}", json=payload, timeout=650.0)
    r.raise_for_status()
    return r.json()


def _file_to_b64(uploaded) -> str:
    """Convert a Streamlit UploadedFile to base64 for the API."""
    return base64.b64encode(uploaded.read()).decode("ascii")


def _render_citations(citations):
    """Render the citation list as clickable cards with snippets."""
    if not citations:
        st.warning("No citations found. The verdict is Insufficient Evidence by the citation-or-refuse rule.")
        return
    st.subheader(f"📚 Citations ({len(citations)})")
    for i, c in enumerate(citations, 1):
        st.markdown(
            f"<div class='citation-card'>"
            f"<div class='citation-source'>{c['source_name']}</div>"
            f"<div class='citation-title'>{c['title']}</div>"
            f"<div class='citation-snippet'>{c['snippet']}</div>"
            f"<a class='citation-url' href='{c['url']}' target='_blank'>{c['url']}</a>"
            f"<div class='citation-ts'>Fetched: {c['fetched_at']}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )


def _render_audit_trail(trail):
    """Render the audit trail as a numbered timeline."""
    if not trail:
        return
    with st.expander(f"🔍 Audit trail ({len(trail)} steps)", expanded=False):
        for i, entry in enumerate(trail, 1):
            step = entry.get("step", "?")
            detail = entry.get("detail", "")
            ts = entry.get("ts", "")
            st.markdown(
                f"<div class='audit-step'>"
                f"<span class='audit-num'>{i:02d}</span>"
                f"<span class='audit-step-name'>{step}</span>"
                f"<span class='audit-detail'>{detail}</span>"
                f"<span class='audit-ts'>{ts[11:19]}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )


def _render_risk_signals(signals):
    """Render the risk-signals panel — honest signals only, no fake/real."""
    if not signals:
        return
    st.subheader(f"⚠️ Provenance & Consistency Signals ({len(signals)})")
    st.caption(
        "These are signals, not verdicts. Each one is a single input. "
        "We never claim an image is real or fake."
    )
    for sig in signals:
        sev = sig["severity"]
        cls = f"signal-card signal-{sev}"
        st.markdown(
            f"<div class='{cls}'>"
            f"<span class='signal-sev signal-sev-{sev}'>{sev.upper()}</span>"
            f"<div class='signal-name'>{sig['signal']}</div>"
            f"<div class='signal-detail'>{sig['detail']}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )


def _render_verify_screen():
    """Screen 1: verify a claim from text, image, audio, or PDF."""
    st.markdown("### Verify a claim")

    input_type = st.selectbox(
        "Input type",
        ["text", "image", "audio", "pdf"],
        format_func=lambda x: {
            "text": "📝 Text",
            "image": "🖼️ Screenshot / image",
            "audio": "🎤 Voice note",
            "pdf": "📄 PDF document",
        }[x],
    )

    raw_input = None
    if input_type == "text":
        raw_input = st.text_area(
            "Paste the claim",
            placeholder="e.g. 'PM Modi announced ₹10 lakh per farmer under PM-Kisan'",
            height=100,
        )
    else:
        accepted = {
            "image": ["png", "jpg", "jpeg", "webp"],
            "audio": ["mp3", "m4a", "wav"],
            "pdf": ["pdf"],
        }[input_type]
        uploaded = st.file_uploader(
            f"Upload {input_type}",
            type=accepted,
        )
        if uploaded:
            raw_input = uploaded

    col1, col2 = st.columns([1, 3])
    with col1:
        language = st.selectbox(
            "Output language",
            ["en", "hi", "ta", "bn"],
            format_func=lambda x: {"en": "English", "hi": "Hindi", "ta": "Tamil", "bn": "Bengali"}[x],
            index=0,
        )
    with col2:
        include_risk = st.checkbox("Include provenance signals", value=True)

    if st.button("Verify", type="primary", disabled=not raw_input):
        payload = {"input_type": input_type, "language": language, "include_risk": include_risk}
        if input_type == "text":
            payload["text"] = raw_input
        elif raw_input:
            payload["file_b64"] = _file_to_b64(raw_input)
        with st.spinner("Running live sources + RAG + reasoning…"):
            try:
                resp = _call_backend("/verify", payload)
            except httpx.HTTPError as e:
                st.error(f"Backend call failed: {e}")
                return
            except Exception as e:
                st.error(f"Unexpected error: {e}")
                return

        # Verdict banner
        verdict = resp["verdict"]
        conf = resp["confidence"]
        vcolor = {
            "Supported": "verdict-supported",
            "Contradicted": "verdict-contradicted",
            "Insufficient Evidence": "verdict-insufficient",
        }[verdict]
        st.markdown(
            f"<div class='verdict-banner {vcolor}'>"
            f"<div class='verdict-label'>{verdict}</div>"
            f"<div class='verdict-conf'>Confidence: {conf}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

        # Extracted claim
        st.caption(f"Extracted claim: {resp['extracted_claim']}")

        # Reasoning summary
        st.markdown(f"**Summary:** {resp['reasoning_summary']}")

        # Citations
        _render_citations(resp["citations"])

        # Risk signals
        if include_risk:
            _render_risk_signals(resp.get("risk_signals", []))

        # Audit trail
        _render_audit_trail(resp.get("audit_trail", []))


def _render_explain_screen():
    """Screen 2: explain a government notification PDF or text."""
    st.markdown("### Explain a document")

    input_type = st.selectbox(
        "Input type",
        ["text", "pdf"],
        format_func=lambda x: {"text": "📝 Text", "pdf": "📄 PDF document"}[x],
    )

    raw_input = None
    if input_type == "text":
        raw_input = st.text_area(
            "Paste the document text",
            placeholder="e.g. 'MINISTRY OF AGRICULTURE AND FARMERS WELFARE NOTIFICATION…'",
            height=150,
        )
    else:
        uploaded = st.file_uploader("Upload PDF", type=["pdf"])
        if uploaded:
            raw_input = uploaded

    col1, col2 = st.columns([1, 3])
    with col1:
        language = st.selectbox(
            "Output language",
            ["en", "hi", "ta", "bn"],
            format_func=lambda x: {"en": "English", "hi": "Hindi", "ta": "Tamil", "bn": "Bengali"}[x],
            index=1 if DEFAULT_LANG == "hi" else 0,
        )
    with col2:
        generate_audio = st.checkbox("Generate audio (TTS)", value=True)

    if st.button("Explain", type="primary", disabled=not raw_input):
        payload = {"input_type": input_type, "language": language, "generate_audio": generate_audio}
        if input_type == "text":
            payload["text"] = raw_input
        elif raw_input:
            payload["file_b64"] = _file_to_b64(raw_input)
        with st.spinner("Reading the document + extracting rights/eligibility/impact…"):
            try:
                resp = _call_backend("/explain", payload)
            except httpx.HTTPError as e:
                st.error(f"Backend call failed: {e}")
                return
            except Exception as e:
                st.error(f"Unexpected error: {e}")
                return

        # Document title
        st.markdown(f"### 📄 {resp['document_title']}")

        # Plain summary
        st.markdown("#### Summary")
        st.markdown(resp["plain_summary"])

        # Three columns: rights, eligibility, impact
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("#### ✅ Rights")
            for r in resp["rights"]:
                st.markdown(f"- {r}")
        with c2:
            st.markdown("#### 👥 Eligibility")
            for e in resp["eligibility"]:
                st.markdown(f"- {e}")
        with c3:
            st.markdown("#### 🌍 Impact")
            for i in resp["impact"]:
                st.markdown(f"- {i}")

        # Citations (the doc itself)
        _render_citations(resp["citations"])

        # Audio
        if generate_audio and resp.get("audio_b64"):
            audio_bytes = base64.b64decode(resp["audio_b64"])
            st.audio(audio_bytes, format="audio/mp3")
            st.caption("🔊 Voice explainer generated via edge-tts (free)")

        # Audit trail
        _render_audit_trail(resp.get("audit_trail", []))
        


# --- Dispatch (must come AFTER all _render_* defs above) ---
if mode == "Verify a claim":
    _render_verify_screen()
else:
    _render_explain_screen()