"""Risk module — HONEST SIGNALS ONLY (spec section 9).

We NEVER output "this is a deepfake" or a definitive fake/real verdict.
We output signals — each with severity (low/medium/high) and detail —
that a human can use as one input among many.

Allowed signals:
  - "EXIF metadata absent"          (forwarded screenshots strip EXIF)
  - "EXIF software tag present"     (edited with Photoshop/Snapseed etc.)
  - "C2PA manifest present"         (positive signal, cite it)
  - "Reverse-image lookup not attempted"  (be honest if not implemented)
  - "Source domain not in official allowlist"
  - "Deepfake-probability model output: 0.xx (low-confidence signal —
     models are unreliable, treat as one input among many)"

NEVER:
  - Output "this is a deepfake"
  - Claim a probability is definitive
  - Show a signal without a plain-language caveat
"""
from __future__ import annotations

import base64
import io
from typing import List, Optional

from backend.schemas import RiskSignal
from backend.utils.audit import AuditTrail
from backend.utils.logging import get_logger
from backend.config import OFFICIAL_DOMAINS
import re

log = get_logger("risk.metadata")


async def audit_image_metadata(
    file_bytes: Optional[bytes],
    claim_text: str,
    audit: AuditTrail,
) -> List[RiskSignal]:
    """Run all available risk signals on the input. Returns a list
    (possibly empty). Each signal has severity + plain-language detail."""
    signals: List[RiskSignal] = []

    # --- Signal 1: EXIF metadata absent (forwarded screenshots strip EXIF) ---
    exif_info = _extract_exif(file_bytes) if file_bytes else None
    if file_bytes and not exif_info:
        signals.append(RiskSignal(
            signal="EXIF metadata absent",
            severity="low",
            detail="Forwarded screenshots on WhatsApp/social media typically strip EXIF data. This is common and not by itself evidence of manipulation.",
        ))
        audit.add("risk.exif_absent", "low")
    elif exif_info:
        # --- Signal 2: EXIF software tag present ---
        software = exif_info.get("software") or exif_info.get("Software")
        if software:
            signals.append(RiskSignal(
                signal="EXIF software tag present",
                severity="medium",
                detail=f"Image metadata references editing software: {software}. This MAY indicate manipulation — but legitimate photo editors (e.g. Snapseed) also write this tag.",
            ))
            audit.add("risk.exif_software", f"medium | {software}")
        else:
            audit.add("risk.exif_clean", "EXIF present, no software tag")

    # --- Signal 3: C2PA manifest presence (best-effort) ---
    if file_bytes and _detect_c2pa(file_bytes):
        signals.append(RiskSignal(
            signal="C2PA manifest present",
            severity="low",
            detail="Image contains a C2PA (Content Provenance & Authenticity) manifest. This is a positive provenance signal — it shows the image's edit history is recorded.",
        ))
        audit.add("risk.c2pa_present", "low")

    # --- Signal 4: Domain reputation for any URL inside the claim ---
    urls = re.findall(r'https?://([a-z0-9.-]+)', claim_text, re.I)
    suspicious = []
    for domain in urls:
        d = domain.lower()
        if not any(official in d for official in OFFICIAL_DOMAINS):
            suspicious.append(d)
    if suspicious:
        signals.append(RiskSignal(
            signal="Source domain not in official allowlist",
            severity="medium",
            detail=f"The claim references domains {suspicious} that are not in the official .gov.in / .nic.in allowlist. Verify the source before sharing.",
        ))
        audit.add("risk.domain", f"medium | {suspicious}")

    # --- Signal 5: Reverse-image lookup not attempted (honesty signal) ---
    if file_bytes:
        signals.append(RiskSignal(
            signal="Reverse-image lookup not attempted",
            severity="low",
            detail="We did not perform a reverse-image search (Google/Bing image search). We recommend checking whether the image has appeared elsewhere online before the current date.",
        ))
        audit.add("risk.reverse_image_skipped", "low | honest disclosure")

    # --- Signal 6: HF deepfake-probability model (optional, off by default) ---
    # Skipping HF model in v1 — it adds latency + the model itself is unreliable.
    # Spec section 9 explicitly allows labeling model output as "low-confidence signal".

    return signals


def _extract_exif(file_bytes: bytes) -> Optional[dict]:
    """Pull EXIF tags from an image using Pillow. Returns None if no
    EXIF, or a dict of tag values."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(file_bytes))
        exif = img.getexif()
        if not exif:
            return None
        # Convert EXIF tags to a readable dict
        out = {}
        for tag_id, value in exif.items():
            tag_name = str(tag_id)
            try:
                from PIL.ExifTags import TAGS
                tag_name = TAGS.get(tag_id, tag_id)
            except ImportError:
                pass
            out[tag_name] = str(value)
        return out
    except Exception as e:
        log.info(f"exif extraction failed: {e}")
        return None


def _detect_c2pa(file_bytes: bytes) -> bool:
    """Best-effort C2PA manifest detection. C2PA manifests are stored
    as 'c2pa' XMP packets or in a special APP17 marker."""
    # Quick byte scan for the c2pa XMP namespace
    return b"c2pa" in file_bytes[:8192] or b"contentauthenticity" in file_bytes[:8192]
