# -*- coding: utf-8 -*-
"""
باترون — مساعد الكروشيه الذكي (نسخة Streamlit للويب)

- بدون تسجيل دخول: أي شخص يفتح الرابط يدخل مباشرة على مولّد الباترون.
- التحليل بـ Gemini Vision مباشرة من التطبيق (نفس منطق server/main.py).
- مفتاح GEMINI_API_KEY يُقرأ من st.secrets (Streamlit Cloud) أو من متغيرات البيئة فقط،
  ولا يظهر أبداً في الواجهة أو في رسائل الخطأ.

التشغيل محلياً:
    pip install -r streamlit_requirements.txt
    set GEMINI_API_KEY=...        (أو ملف .streamlit/secrets.toml)
    streamlit run streamlit_app.py
"""

import os
import re

import streamlit as st
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, Field

MAX_IMAGE_BYTES = 7 * 1024 * 1024
ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
GEMINI_TIMEOUT_MS = 120_000

SCHEMATIC_HEADING = "📐 أولاً: رسمة وباترون المخطط التخطيطي (Schematic Diagram / Chart Layout)"
STEPS_HEADING = "📋 ثانياً: الشرح التفصيلي وكيفية العمل (Step-by-Step Instructions)"


# ═══════════════════════════════════════════════════════════════════
#  الإعدادات السرية — st.secrets أولاً ثم متغيرات البيئة
# ═══════════════════════════════════════════════════════════════════
def get_setting(name: str, default: str = "") -> str:
    try:
        if name in st.secrets:
            return str(st.secrets[name]).strip()
    except Exception:  # لا يوجد ملف secrets.toml — طبيعي محلياً
        pass
    return os.environ.get(name, default).strip()


MODELS = [
    m.strip()
    for m in get_setting("GEMINI_MODELS", "gemini-3.5-flash,gemini-3.5-flash-lite").split(",")
    if m.strip()
]


@st.cache_resource(show_spinner=False)
def get_client(api_key: str) -> genai.Client:
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS),
    )


# ═══════════════════════════════════════════════════════════════════
#  التعليمات وشكل الرد (مطابقة لـ server/main.py)
# ═══════════════════════════════════════════════════════════════════
SYSTEM_PROMPT = """You are a master crochet designer and technical pattern writer with 30 years of
experience, specialised in reading BOTH finished garments (dresses, jackets, cardigans, tops, bags,
blankets) AND close-up motif / chart details (leaves, flowers, petals, granny squares, Irish crochet
elements, lace repeats). Study the photo with extreme care before writing.

SILENT ANALYSIS (never print it):
1. Is this crochet (or knitting)? If the image contains no crochet/knitted item, set is_crochet=false,
   fill the other fields with empty strings, and stop.
2. piece_kind: "garment" (a whole wearable/usable item), "motif" (a single motif, leaf, flower,
   unit or close-up of a stitch pattern), or "other".
3. Classify by STRUCTURE, never by styling: a jacket/cardigan needs a full front opening; a pullover
   has none; a scarf has no armholes. Never invent buttons, hoods, pockets or sleeves that are not visible.
4. For motifs: count petals/lobes/points exactly, count rounds/rows from the centre outward, identify
   the stitch heights along each petal or leaf side (sl st, sc, hdc, dc, tr, dtr), picots, chain
   spaces, spine/vein chains, and how units connect to their neighbours.

OUTPUT — Modern Standard Arabic Markdown, US crochet terms with the English abbreviation in
parentheses the first time, e.g. «عمود (dc)». Do NOT write the two main section headings yourself
(the app adds them); start sub-headings at ### level.

FIELD schematic_markdown (shown FIRST to the user):
  - One opening line: what the item is + assumed size (e.g. adult M, bust 92 cm; or motif 8 cm
    diameter) + the visible evidence for this identification.
  - ONE fenced code block that starts with ```text — the schematic, drawn like a printed pattern:
      * plain ASCII and box-drawing characters only (┌ ┐ └ ┘ ─ │ ├ ┤ ┬ ┴ ┼ ^ v < > / \\ o * + T),
        NO Arabic letters inside the block, short UPPERCASE English labels, max 60 chars per line,
        perfectly aligned;
      * garment: every real piece as its own outlined shape, roughly proportional, numbered
        ([1] FRONT, [2] BACK, [3] SLEEVE x2, [4] SKIRT ...), widths/lengths in cm, ribbing as =====,
        motif-based garments as a grid of units showing how many units per row and the join points;
      * motif: a top-view chart — centre ring, each round labelled R1, R2 ..., the exact number of
        petals/lobes placed around the centre, stitch heights per petal; for a leaf: the spine chain
        with the stitch sequence mirrored on both sides (sc-hdc-dc-tr-dc-hdc-sc) and the tip.
  - A Markdown table directly after the block:
      garment → | الرقم | الجزء | العدد | العرض (سم) | الطول (سم) | ملاحظات |
      motif   → | الدورة/السطر | الغرز المستخدمة | عدد الغرز | ملاحظات |
  - A small legend table for every symbol used in the block: | الرمز | الغرزة | الاختصار |

FIELD instructions_markdown (shown SECOND):
  ### 🧵 الخامات والأدوات — yarn weight & fiber, grams/meters per colour, hook size in mm, notions.
  ### 📏 العينة (Gauge) — stitches and rows per 10×10 cm (or motif size after R2) and how to adjust.
  ### 🔤 الغرز والاختصارات — table | الغرزة | الاختصار | طريقة العمل باختصار |
  ### 🪡 خطوات العمل — one #### per piece/unit in the table order: foundation (magic ring or exact
      starting chain count, with the gauge × width calculation for garments), then EVERY row/round
      written out with the stitch count in parentheses at the end, increases/decreases, turning
      chains, colour changes.
  ### 🧩 التجميع وتشبيك الوحدات — joining method (join-as-you-go, sl st, whipstitch, mattress),
      order of joining, where each connection happens, seaming of pieces.
  ### ✨ التشطيب — edging, blocking, weaving ends.
  ### 💡 نصائح احترافية — yarn & hook tips, common mistakes, how to resize, and anything that was
      not clearly visible in the photo (state your assumption honestly).

All numbers MUST agree between the diagram, the tables and the steps. Respect stitch multiples."""


class CrochetAnalysis(BaseModel):
    is_crochet: bool = Field(description="False if the image shows no crochet or knitted item.")
    piece_kind: str = Field(description='One of "garment", "motif", "other".')
    title: str = Field(description="Short Arabic name of the item, e.g. «فستان كروشيه بموتيفات الزهور».")
    schematic_markdown: str = Field(description="Schematic / chart section, Arabic Markdown.")
    instructions_markdown: str = Field(description="Step-by-step instructions section, Arabic Markdown.")


GEN_CONFIG = types.GenerateContentConfig(
    system_instruction=SYSTEM_PROMPT,
    temperature=0.3,
    response_mime_type="application/json",
    response_schema=CrochetAnalysis,
)

MODE_HINTS = {
    "auto": "Decide yourself whether this is a whole piece or a motif close-up.",
    "garment": "The user says this photo shows a WHOLE piece (garment/item). Draw the full pieces schematic.",
    "motif": "The user says this photo is a CLOSE-UP of a motif/pattern detail. Draw the motif chart "
             "round by round with exact petal and stitch counts.",
}
MODE_LABELS = {"auto": "تحديد تلقائي", "garment": "قطعة كاملة", "motif": "تفصيلة / موتيف"}

_LEADING_HEADING = re.compile(r"^\s*#{1,2}\s[^\n]*\n+")


class AnalysisError(Exception):
    """خطأ برسالة آمنة للعرض على المستخدم (بدون أي تفاصيل حساسة)."""


def analyze(client: genai.Client, image: bytes, mime: str, user_prompt: str) -> CrochetAnalysis:
    last_error = "unknown"
    for model in MODELS:
        try:
            resp = client.models.generate_content(
                model=model,
                contents=[types.Part.from_bytes(data=image, mime_type=mime), user_prompt],
                config=GEN_CONFIG,
            )
        except genai_errors.APIError as ex:
            last_error = f"{model}: {ex.code}"
            if ex.code in (400, 401, 403) and "API key" in str(ex.message or ""):
                raise AnalysisError("مفتاح Gemini غير صالح. راجع GEMINI_API_KEY في إعدادات التطبيق.") from ex
            if ex.code in (404, 408, 429, 500, 503, 504):
                continue  # النموذج غير متاح أو مشغول → جرّب التالي
            raise AnalysisError("رفضت خدمة Gemini الطلب. جرّب صورة أخرى.") from ex
        except Exception as ex:  # مهلة أو مشكلة اتصال
            last_error = f"{model}: {type(ex).__name__}"
            continue

        parsed = resp.parsed
        if parsed is None and resp.text:
            try:
                parsed = CrochetAnalysis.model_validate_json(resp.text)
            except ValueError:
                parsed = None
        if parsed is not None:
            return parsed
        last_error = f"{model}: empty/invalid response"

    print(f"[patron] all models failed: {last_error}")
    raise AnalysisError("خدمة التحليل مشغولة حالياً. حاول مرة أخرى بعد دقيقة.")


# ═══════════════════════════════════════════════════════════════════
#  الواجهة
# ═══════════════════════════════════════════════════════════════════
st.set_page_config(page_title="باترون — مساعد الكروشيه", page_icon="🧶", layout="centered")

st.markdown(
    """
    <style>
    .stApp, .stMarkdown, .stTextArea, .stAlert, .stRadio { direction: rtl; text-align: right; }
    .stMarkdown pre, .stMarkdown code { direction: ltr; text-align: left; }
    .stMarkdown table { direction: rtl; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🧶 باترون — مساعد الكروشيه الذكي")
st.write("ارفع صورة قطعة كروشيه كاملة أو تفصيلة موتيف، وهنطلع لك رسمة الباترون وخطوات العمل بالتفصيل.")

api_key = get_setting("GEMINI_API_KEY")
if not api_key:
    st.error(
        "التطبيق غير مُعد بعد: أضف GEMINI_API_KEY في إعدادات Secrets على Streamlit Cloud "
        "(أو كمتغير بيئة عند التشغيل محلياً)."
    )
    st.stop()

uploaded_file = st.file_uploader(
    "اختر صورة القطعة (صورة واضحة على خلفية سادة)", type=["jpg", "jpeg", "png", "webp"]
)
mode = st.radio(
    "نوع الصورة", options=list(MODE_LABELS), format_func=MODE_LABELS.get, horizontal=True
)
notes = st.text_area(
    "ملاحظات إضافية (اختياري)",
    max_chars=500,
    placeholder="مثلاً: عايزة مقاس L، أو عندي خيط قطن 4 مم...",
)

if uploaded_file is not None:
    st.image(uploaded_file, caption="الصورة المطلوبة", use_container_width=True)

    if st.button("حلّل الصورة واكتب الباترون 🪄", type="primary"):
        image_bytes = uploaded_file.getvalue()
        mime = "image/jpeg" if uploaded_file.type == "image/jpg" else uploaded_file.type

        if mime not in ALLOWED_MIME:
            st.error("صيغة الصورة غير مدعومة. استخدم JPG أو PNG أو WEBP.")
            st.stop()
        if len(image_bytes) > MAX_IMAGE_BYTES:
            st.error("الصورة أكبر من 7 ميجا. صغّرها شوية وجرّب تاني 🙏")
            st.stop()

        user_prompt = MODE_HINTS[mode]
        if notes.strip():
            user_prompt += f"\nUser notes (size, yarn, wishes): {notes.strip()}"

        try:
            with st.spinner("جاري دراسة الغرز ورسم الباترون... (قد يستغرق دقيقة)"):
                result = analyze(get_client(api_key), image_bytes, mime, user_prompt)
        except AnalysisError as ex:
            st.error(str(ex))
            st.stop()

        if not result.is_crochet:
            st.warning("الصورة لا تبدو لقطعة كروشيه أو تريكو. صوّر القطعة بوضوح على خلفية سادة وفي ضوء جيد.")
            st.stop()

        schematic = _LEADING_HEADING.sub("", result.schematic_markdown.strip(), count=1)
        steps = _LEADING_HEADING.sub("", result.instructions_markdown.strip(), count=1)

        st.markdown("---")
        if result.title.strip():
            st.subheader(result.title.strip())
        st.markdown(f"## {SCHEMATIC_HEADING}")
        st.markdown(schematic)
        st.markdown(f"## {STEPS_HEADING}")
        st.markdown(steps)

        st.download_button(
            "⬇️ تحميل الباترون (Markdown)",
            data=f"# {result.title.strip()}\n\n## {SCHEMATIC_HEADING}\n\n{schematic}\n\n## {STEPS_HEADING}\n\n{steps}\n",
            file_name="patron.md",
            mime="text/markdown",
        )
        st.success("تم تحليل القطعة! بالتوفيق في الشغل 💕")
