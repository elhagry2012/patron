# -*- coding: utf-8 -*-
"""
خادم باترون (Patron Backend) — FastAPI

لماذا خادم؟
  - مفتاح Gemini يبقى هنا فقط (متغير بيئة)، ولا يدخل ملف الـ APK أبداً.
  - المستخدم لا يرى أي API Key: يسجّل الدخول بحساب Google ثم يرفع الصورة.

المسارات:
  GET  /auth/start?sid=...   يفتحه التطبيق في المتصفح → شاشة اختيار حساب Google
  GET  /auth/callback        يعود إليه Google بعد الموافقة
  GET  /auth/status?sid=...  يسأله التطبيق كل ثانيتين حتى يستلم رمز الجلسة
  GET  /me                   بيانات المستخدم الحالي (للتحقق من الجلسة)
  POST /analyze              تحليل صورة الكروشيه بـ Gemini Vision

متغيرات البيئة:
  GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET   من Google Cloud Console (OAuth client: Web application)
  GEMINI_API_KEY                           من Google AI Studio
  PUBLIC_URL                               رابط الخادم العام، مثل https://patron-xxxx.run.app
  SESSION_SECRET                           نص عشوائي طويل لتوقيع الجلسات
  GEMINI_MODELS   (اختياري)                 النماذج بالترتيب، مفصولة بفواصل
  DAILY_LIMIT     (اختياري)                 عدد التحليلات اليومية لكل مستخدم (افتراضي 30)

التشغيل المحلي:
  pip install -r requirements.txt
  uvicorn main:app --reload --port 8000
"""

import asyncio
import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import time
from datetime import date
from urllib.parse import urlencode

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from google import genai
from google.auth.transport import requests as google_auth_requests
from google.genai import errors as genai_errors
from google.genai import types
from google.oauth2 import id_token as google_id_token
from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════
#  الإعدادات
# ═══════════════════════════════════════════════════════════════════
def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing environment variable: {name}")
    return value


GOOGLE_CLIENT_ID = require_env("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = require_env("GOOGLE_CLIENT_SECRET")
GEMINI_API_KEY = require_env("GEMINI_API_KEY")
PUBLIC_URL = require_env("PUBLIC_URL").rstrip("/")
REDIRECT_URI = f"{PUBLIC_URL}/auth/callback"

_secret = os.environ.get("SESSION_SECRET", "").strip()
if not _secret:
    print("[patron] WARNING: SESSION_SECRET not set — sessions will reset on every restart.")
    _secret = secrets.token_urlsafe(48)
SESSION_SECRET = _secret.encode()

# gemini-1.5-flash أُوقف من Google (يرجع 404)، لذلك نستخدم النماذج الحالية مع بديل احتياطي.
MODELS = [
    m.strip()
    for m in os.environ.get("GEMINI_MODELS", "gemini-3.5-flash,gemini-3.5-flash-lite").split(",")
    if m.strip()
]
DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", "30"))
GEMINI_TIMEOUT = 120          # ثانية لكل نموذج
SESSION_DAYS = 30
LOGIN_TTL = 600               # صلاحية محاولة تسجيل الدخول: 10 دقائق
MAX_IMAGE_BYTES = 7 * 1024 * 1024
ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
SID_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")

gemini = genai.Client(api_key=GEMINI_API_KEY)
app = FastAPI(title="Patron Backend", version="1.0.0")

# تخزين في الذاكرة — مناسب للتجربة بنسخة خادم واحدة (Cloud Run: --max-instances=1).
# للإنتاج الفعلي استبدلها بـ Firestore أو Redis.
PENDING_LOGINS: dict[str, dict] = {}
USAGE: dict[tuple[str, str], int] = {}


# ═══════════════════════════════════════════════════════════════════
#  رموز الجلسة (موقّعة بـ HMAC — بدون مكتبات إضافية)
# ═══════════════════════════════════════════════════════════════════
def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(body: str) -> str:
    return _b64e(hmac.new(SESSION_SECRET, body.encode(), hashlib.sha256).digest())


def make_token(claims: dict) -> str:
    body = _b64e(json.dumps(claims, separators=(",", ":"), ensure_ascii=False).encode())
    return f"{body}.{_sign(body)}"


def read_token(token: str) -> dict | None:
    try:
        body, sig = token.split(".", 1)
        if not hmac.compare_digest(sig, _sign(body)):
            return None
        claims = json.loads(_b64d(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if claims.get("exp", 0) < time.time():
        return None
    return claims


def current_user(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "يلزم تسجيل الدخول.")
    claims = read_token(authorization[7:].strip())
    if not claims:
        raise HTTPException(401, "انتهت الجلسة. سجّل الدخول مرة أخرى.")
    return claims


def _cleanup_logins() -> None:
    now = time.time()
    for sid in [s for s, e in PENDING_LOGINS.items() if now - e["created"] > LOGIN_TTL]:
        PENDING_LOGINS.pop(sid, None)


def _html_page(title: str, message: str, ok: bool) -> HTMLResponse:
    color = "#B04A78" if ok else "#9B1C1C"
    page = f"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<style>body{{font-family:system-ui,sans-serif;background:#FBF7F4;margin:0;display:flex;
min-height:100vh;align-items:center;justify-content:center;padding:16px}}
.c{{background:#fff;border:1px solid #EADDE3;border-radius:20px;padding:28px;max-width:420px;
text-align:center}}h1{{color:{color};font-size:22px}}p{{color:#3F2A35;line-height:1.8}}</style>
</head><body><div class="c"><h1>{html.escape(title)}</h1><p>{message}</p></div></body></html>"""
    return HTMLResponse(page, status_code=200 if ok else 400)


# ═══════════════════════════════════════════════════════════════════
#  تسجيل الدخول بحساب Google
# ═══════════════════════════════════════════════════════════════════
@app.get("/")
def health():
    return {"ok": True, "service": "patron", "models": MODELS}


@app.get("/auth/start")
def auth_start(sid: str = Query(...)):
    if not SID_RE.match(sid):
        raise HTTPException(400, "Invalid session id.")
    _cleanup_logins()
    PENDING_LOGINS.setdefault(sid, {"created": time.time(), "result": None})
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": sid,
        "prompt": "select_account",   # يعرض حسابات Google الموجودة على الهاتف للاختيار بنقرة
        "access_type": "online",
    }
    return RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params))


@app.get("/auth/callback")
def auth_callback(code: str | None = None, state: str | None = None, error: str | None = None):
    entry = PENDING_LOGINS.get(state or "")
    if error or not code or entry is None:
        return _html_page("تعذر تسجيل الدخول", "انتهت المحاولة أو تم إلغاؤها. ارجع للتطبيق وحاول مرة أخرى.", False)

    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "redirect_uri": REDIRECT_URI,
                    "grant_type": "authorization_code",
                },
            )
        resp.raise_for_status()
        info = google_id_token.verify_oauth2_token(
            resp.json()["id_token"], google_auth_requests.Request(), GOOGLE_CLIENT_ID
        )
    except Exception as ex:  # noqa: BLE001 — أي فشل هنا يعني رفض الدخول
        print(f"[patron] oauth error: {ex!r}")
        return _html_page("تعذر تسجيل الدخول", "تعذر التحقق من حساب Google. حاول مرة أخرى.", False)

    if not info.get("email_verified"):
        return _html_page("تعذر تسجيل الدخول", "البريد الإلكتروني لهذا الحساب غير مؤكد.", False)

    user = {
        "sub": info["sub"],
        "email": info.get("email", ""),
        "name": info.get("name") or info.get("email", ""),
    }
    token = make_token({**user, "exp": int(time.time()) + SESSION_DAYS * 86400})
    entry["result"] = {"token": token, "user": user}
    return _html_page(
        "تم تسجيل الدخول ✅",
        f"أهلاً <b>{html.escape(user['name'])}</b><br>({html.escape(user['email'])})<br><br>"
        "ارجع الآن إلى تطبيق باترون — سيفتح تلقائياً على شاشتك الرئيسية.",
        True,
    )


@app.get("/auth/status")
def auth_status(sid: str = Query(...)):
    entry = PENDING_LOGINS.get(sid)
    if entry is None:
        raise HTTPException(404, "expired")
    if entry["result"] is None:
        return {"status": "pending"}
    PENDING_LOGINS.pop(sid, None)   # الرمز يُسلَّم مرة واحدة فقط
    return {"status": "ok", **entry["result"]}


@app.get("/me")
def me(user: dict = Depends(current_user)):
    used = USAGE.get((user["sub"], date.today().isoformat()), 0)
    return {"user": {k: user[k] for k in ("sub", "email", "name")}, "remaining": max(DAILY_LIMIT - used, 0)}


# ═══════════════════════════════════════════════════════════════════
#  التحليل البصري بـ Gemini
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

# يحذف أي عنوان رئيسي كتبه النموذج رغم التعليمات حتى لا يتكرر العنوان أو يختل الترتيب
_LEADING_HEADING = re.compile(r"^\s*#{1,2}\s[^\n]*\n+")


class AnalyzeRequest(BaseModel):
    image_b64: str
    mime_type: str = "image/jpeg"
    mode: str = "auto"
    notes: str = Field(default="", max_length=500)


async def _generate(image: bytes, mime: str, user_prompt: str) -> tuple[CrochetAnalysis, str]:
    last_error = "unknown"
    for model in MODELS:
        try:
            resp = await asyncio.wait_for(
                gemini.aio.models.generate_content(
                    model=model,
                    contents=[types.Part.from_bytes(data=image, mime_type=mime), user_prompt],
                    config=GEN_CONFIG,
                ),
                timeout=GEMINI_TIMEOUT,
            )
        except asyncio.TimeoutError:
            last_error = f"{model}: timeout"
            continue
        except genai_errors.APIError as ex:
            last_error = f"{model}: {ex.code} {ex.message}"
            if ex.code in (404, 429, 500, 503, 504):
                continue        # النموذج غير متاح أو مشغول → جرّب التالي
            raise HTTPException(502, "رفضت خدمة Gemini الطلب.") from ex

        parsed = resp.parsed
        if parsed is None and resp.text:
            try:
                parsed = CrochetAnalysis.model_validate_json(resp.text)
            except ValueError:
                parsed = None
        if parsed is not None:
            return parsed, model
        last_error = f"{model}: empty/invalid response"

    print(f"[patron] all models failed: {last_error}")
    raise HTTPException(503, "خدمة التحليل مشغولة حالياً. حاول مرة أخرى بعد دقيقة.")


@app.post("/analyze")
async def analyze(req: AnalyzeRequest, user: dict = Depends(current_user)):
    if req.mime_type not in ALLOWED_MIME:
        raise HTTPException(400, "صيغة الصورة غير مدعومة. استخدم JPG أو PNG أو WEBP.")
    try:
        image = base64.b64decode(req.image_b64, validate=True)
    except ValueError:
        raise HTTPException(400, "بيانات الصورة تالفة.")
    if not image or len(image) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "حجم الصورة كبير جداً.")

    usage_key = (user["sub"], date.today().isoformat())
    if USAGE.get(usage_key, 0) >= DAILY_LIMIT:
        raise HTTPException(429, f"وصلت للحد اليومي ({DAILY_LIMIT} تحليل). عُد غداً.")

    user_prompt = MODE_HINTS.get(req.mode, MODE_HINTS["auto"])
    if req.notes.strip():
        user_prompt += f"\nUser notes (size, yarn, wishes): {req.notes.strip()}"

    result, model = await _generate(image, req.mime_type, user_prompt)
    if not result.is_crochet:
        raise HTTPException(
            422, "الصورة لا تبدو لقطعة كروشيه أو تريكو. صوّر القطعة بوضوح على خلفية سادة وفي ضوء جيد."
        )

    USAGE[usage_key] = USAGE.get(usage_key, 0) + 1
    return {
        "title": result.title.strip(),
        "piece_kind": result.piece_kind.strip(),
        "schematic_markdown": _LEADING_HEADING.sub("", result.schematic_markdown.strip(), count=1),
        "instructions_markdown": _LEADING_HEADING.sub("", result.instructions_markdown.strip(), count=1),
        "model": model,
        "remaining": max(DAILY_LIMIT - USAGE[usage_key], 0),
    }
