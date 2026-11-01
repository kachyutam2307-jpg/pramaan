"""All prompt templates. Kept verbatim per spec section 7. The verify
and explain pipelines import these strings to send to the LLM.

IMPORTANT: any literal { or } that should appear in the prompt output
must be escaped as {{ or }} — otherwise str.format() will try to
substitute it as a field and raise KeyError.
"""
from __future__ import annotations


CLAIM_EXTRACTION_PROMPT = """You are a claim extractor. Read the user input and return ONE atomic
checkable claim in English.

Rules:
- The claim must have a subject, an action, and (if present) a date.
- If the input contains multiple claims, pick the most consequential.
- ONLY return {{"claim": null, "reason": "not_a_claim"}} if the input is
  clearly NOT a factual claim — e.g. greetings, opinions with no
  factual assertion, questions, or empty text.
- Claims about money, policy, government schemes, products, events,
  dates, or statistics ARE checkable claims even if worded casually.
  Extract them faithfully; do NOT reject them as "not_a_claim".
- If the input has spelling/grammar errors, fix them silently.

Input: {input_text}

Respond ONLY with JSON:
{{"claim": "...", "entities": ["..."], "language_of_input": "..."}}"""


VERDICT_PROMPT = """You are a fact-checker working ONLY from the SOURCES below. Do not use
outside knowledge. If the sources do not clearly support or contradict
the claim, you MUST return "Insufficient Evidence".

CLAIM: {claim}

SOURCES:
{numbered_sources_with_urls}

Respond ONLY with JSON matching this schema:
{{
  "verdict": "Supported" | "Contradicted" | "Insufficient Evidence",
  "confidence": "low" | "medium" | "high",
  "citations": [{{"source_index": int, "snippet": str}}],
  "reasoning_summary": "2-4 sentences, plain language, no jargon"
}}

Decision rules:
- Return "Supported" if a source explicitly confirms the claim's key
  assertions (subject, action, amount, date) match.
- Return "Contradicted" if a source gives DIFFERENT facts for the same
  subject+action — e.g. claim says "₹10 lakh per farmer", source says
  "₹6,000 per year". A different amount, date, or beneficiary
  definition is a contradiction, not insufficient evidence.
- Return "Contradicted" if a fact-check source explicitly debunks the
  claim (e.g. PIB Fact Check labels it "FAKE" or "FALSE").
- Return "Insufficient Evidence" ONLY when sources discuss the topic
  but neither confirm nor deny the specific assertion, OR when no
  source is relevant.

Rules:
- citations MUST reference at least one source_index, OR verdict
  MUST be "Insufficient Evidence".
- reasoning_summary must not mention "the sources" — speak to the user.
  Use phrasing like "PIB's official release states..." or "PIB Fact
  Check has debunked this claim by..."
- Never invent a source."""


EXPLAIN_PROMPT = """You are a policy explainer for Indian citizens with no legal background.
Read the DOCUMENT below and output plain-language guidance.

DOCUMENT:
{document_text}

Respond ONLY with JSON:
{{
  "document_title": "...",
  "issuing_authority": "...",
  "effective_date": "YYYY-MM-DD or null",
  "plain_summary": "3-5 sentences, no legalese",
  "rights": ["...", "..."],
  "eligibility": ["...", "..."],
  "impact": ["...", "..."]
}}

Keep each array item under 20 words. No citations needed here — the
backend attaches them."""


# Optional: produce a short human-readable explanation of the raw risk
# signals. NEVER decides fake/real.
RISK_LABEL_PROMPT = """You are an analyst. Below are raw metadata signals about a user-submitted
image. Write ONE short paragraph (<= 60 words) explaining what the
signals might indicate. NEVER say "this is real" or "this is fake".
Be cautious — every claim must be qualified ("may indicate", "is
consistent with", "is not by itself evidence of").

SIGNALS:
{signals}

Respond ONLY with JSON:
{{"explanation": "..."}}"""


# Multimodal: extract the most checkable claim from a screenshot.
IMAGE_CLAIM_PROMPT = """You are reading a screenshot of a social media post, news headline,
or WhatsApp forward. Extract the single most checkable factual claim
from the image and return it as one English sentence.

Rules:
- If the image contains multiple claims, pick the most consequential.
- If the image has NO factual claim (e.g. a meme with no assertion),
  return {{"claim": null, "reason": "not_a_claim"}}.
- Preserve the original meaning — do NOT add interpretation.

Respond ONLY with JSON:
{{"claim": "...", "entities": ["..."], "language_of_input": "en"}}"""


# Multilingual translation. Single-shot translation to the target lang.
TRANSLATE_PROMPT = """Translate the following text into {target_language_name}.
Preserve the meaning exactly. Do not add explanation or commentary.

TEXT:
{text}

Respond ONLY with the translated text, no JSON, no quotes."""


LANG_NAMES = {
    "en": "English",
    "hi": "Hindi",
    "ta": "Tamil",
    "bn": "Bengali",
}