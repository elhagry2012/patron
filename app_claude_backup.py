"""
مساعد الكروشيه الذكي - تطبيق Flet متعدد المنصات (Windows / macOS / Linux / Android / iOS / Web)

التشغيل:
    pip install "flet==1.1.2" anthropic pillow
    flet run app.py

المفتاح:
    - إما متغير البيئة ANTHROPIC_API_KEY
    - أو من زر الإعدادات ⚙️ داخل التطبيق (بيتحفظ على الجهاز) - مناسب للموبايل.

التطبيق مفيهوش أي بيانات وهمية: كل نتيجة جاية من تحليل حقيقي للصورة بـ Claude Vision،
والرد بيرجع بصيغة JSON مقيّدة بـ Schema عشان العرض يكون منظم ومضمون.
"""

from __future__ import annotations

import base64
import io
import json
import os
from dataclasses import dataclass

import flet as ft

try:
    import anthropic
except ImportError:
    anthropic = None

try:
    from PIL import Image, ImageOps
except ImportError:  # Pillow اختياري: بيستخدم لتصغير وتصحيح اتجاه الصور الكبيرة
    Image = None
    ImageOps = None


# ═════════════════════════════ الإعدادات ═════════════════════════════
MODEL = "claude-opus-5-5"
MAX_OUTPUT_TOKENS = 32000
API_IMAGE_LIMIT = 5 * 1024 * 1024        # حد الـ API للصورة الواحدة (بعد الضغط)
MAX_UPLOAD_BYTES = 25 * 1024 * 1024      # أقصى حجم نقبله من المستخدم قبل المعالجة
MAX_IMAGE_SIDE = 2000                    # أبعاد كافية لقراءة تفاصيل الغرزة
MIN_IMAGE_SIDE = 350                     # أقل من كده التفاصيل بتضيع
PREFS_KEY = "crochet.anthropic_api_key"

ROSE = "#C2185B"
ROSE_SOFT = "#F8BBD0"
ROSE_TINT = "#FCE4EC"
CREAM = "#FFF8F3"
CARD = "#FFFFFF"
TEXT_DARK = "#4A2C3A"
TEXT_MUTED = "#8C6B7A"
BORDER = "#F1D9E2"
WARN_BG = "#FFF4E5"
WARN_FG = "#8A4B00"
ERR_BG = "#FDECEA"
ERR_FG = "#9B1C1C"
CHART_BG = "#FBF7F9"

MONO_FONT = "Consolas"
MONO_FALLBACK = ["Courier New", "Menlo", "monospace"]


# ═════════════════════════════ تعليمات النموذج ═════════════════════════════
SYSTEM_PROMPT = """أنتِ مصممة باترونات كروشيه محترفة بخبرة طويلة في ملابس الأطفال والكبار، وبتكتبي بالعامية المصرية البسيطة.
هيوصلك صورة حقيقية لقطعة. شغلك يتم على مرحلتين بالترتيب:

المرحلة 1 - الفحص الدقيق قبل أي استنتاج:
- افحصي الصورة كلها بعناية: الشكل العام، الحواف، فتحة الرقبة، الأكمام، الخصر، الأطراف.
- حددي نوع القطعة بدقة (جاكيت / كارديجان مفتوح من قدام، بلوفر مقفول، شال، بونشو، فستان، طاقية، ...).
  الفرق المهم: الجاكيت/الكارديجان مفتوح من الأمام (له أماميين)، البلوفر قطعة أمامية واحدة.
- عدّي الأزرار الظاهرة فعلاً. لو مش ظاهرة، closure = "none" وعددها 0. ما تفترضيش أزرار مش موجودة.
- هل فيه هودي/كابيشون فعلاً؟ هل فيه جيوب ونوعها؟ ما تخترعيش عناصر مش باينة.
- حددي الغرزة البارزة من شكل النسيج نفسه (حشو، نص عمود، عمود، غرزة الفشار Popcorn، الفول Bobble، الصدفة Shell، Granny، V-stitch، ريب أمامي/خلفي FPdc/BPdc، ...).
- تأكدي إنها كروشيه مش تريكو (التريكو غرزه شكل V متراصة رأسياً في أعمدة منتظمة، الكروشيه غرزه أكثر عُقداً وارتفاعاً).
- أي حاجة مش واضحة سجّليها في uncertain_points وخفّضي confidence. الصراحة أهم من الثقة الزايفة.

حالات الرفض (status):
- "unclear_image": الصورة مهزوزة/مظلمة/بعيدة/مقصوصة لدرجة إن نوع القطعة أو الغرزة مش ممكن تحديده.
- "not_crochet": القطعة تريكو أو قماش منسوج أو مش شغل يدوي.
- "not_garment": الصورة مفيهاش قطعة كروشيه أصلاً.
في الحالات دي اكتبي السبب ونصيحة تصوير محددة في status_message، وسيبي باقي الحقول نصوص فاضية وقوائم فاضية وأرقام صفر.
لو status = "ok" سيبي status_message فاضي أو ملاحظة قصيرة.

المرحلة 2 - الباترون:
أ) diagram.schematic: رسمة تخطيطية نصية (ASCII) للقطع المنفصلة جنب بعض أو تحت بعض بالترتيب المنطقي للتجميع.
   - استخدمي فقط الحروف: + - | / \\ _ . : والمسافات والأرقام والحروف الإنجليزية الكبيرة.
   - ممنوع أي حرف عربي جوه الرسمة (بيبوّظ المحاذاة). اكتبي داخل كل قطعة كود قصير بالإنجليزي (BACK, L-FRONT, R-FRONT, SLEEVE x2, HOOD, POCKET, BAND ...) والمقاس بالسم.
   - وضّحي مكان الأزرار بـ o وفتحة الرقبة والإبط بشكل تقريبي، وبيّني اتجاه الشغل بسهم (^ أو v) لو مهم.
   - عرض الرسمة لا يزيد عن 70 حرف في السطر.
ب) diagram.pieces: لكل قطعة: الكود المستخدم في الرسمة، الاسم بالعربي، العدد، المقاسات التقريبية، وعدد السلاسل/الغرز التقريبي.
ج) instructions: الشرح خطوة بخطوة:
   - الخامات: نوع الخيط وسُمكه والكمية التقريبية بالجرام، مقاس الإبرة، والعيّنة (gauge) على 10×10 سم.
   - عقدة البداية وطريقة مسك الخيط.
   - السلاسل المبدئية لكل قطعة، ومضاعفات النقشة لو فيه.
   - sections: قسم لكل قطعة بنفس ترتيب الرسمة، وكل خطوة سطر بسطر (السطر 1، السطر 2 ...، أو "كرري من سطر 3 لـ 4 لحد 30 سم") مع عدد الغرز بين قوسين في آخر السطر.
   - التجميع (الخياطة، الحواف، تركيب الأزرار أو الهودي) ثم التشطيب.
   - نصائح للخيط والإبرة وأخطاء شائعة.
المقاسات تقديرية من الصورة: وضّحي المقاس المفترض في target_size، ولو المستخدم كتب ملاحظات (مقاس أو خيط معين) التزمي بيها.
اكتبي أسماء الغرز بالمصري + الإنجليزي + الاختصار (مثلاً: عمود - Double Crochet - dc).
"""

USER_PROMPT = "حلّلي القطعة اللي في الصورة دي بدقة، وطلّعي الرسمة التخطيطية للباترون ثم الشرح التفصيلي."

ANALYSIS_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "status_message", "identification", "diagram", "instructions"],
    "properties": {
        "status": {"type": "string", "enum": ["ok", "unclear_image", "not_crochet", "not_garment"]},
        "status_message": {"type": "string"},
        "identification": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "garment_type_ar", "garment_type_en", "target_size", "construction",
                "main_stitches", "closure", "button_count", "has_hood", "pockets",
                "neckline_collar", "sleeves", "colors", "confidence", "uncertain_points",
            ],
            "properties": {
                "garment_type_ar": {"type": "string"},
                "garment_type_en": {"type": "string"},
                "target_size": {"type": "string"},
                "construction": {"type": "string"},
                "main_stitches": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["name_ar", "name_en", "abbr", "where_used"],
                        "properties": {
                            "name_ar": {"type": "string"},
                            "name_en": {"type": "string"},
                            "abbr": {"type": "string"},
                            "where_used": {"type": "string"},
                        },
                    },
                },
                "closure": {"type": "string", "enum": ["none", "buttons", "zipper", "ties", "toggle", "other"]},
                "button_count": {"type": "integer"},
                "has_hood": {"type": "boolean"},
                "pockets": {"type": "string"},
                "neckline_collar": {"type": "string"},
                "sleeves": {"type": "string"},
                "colors": {"type": "string"},
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "uncertain_points": {"type": "array", "items": {"type": "string"}},
            },
        },
        "diagram": {
            "type": "object",
            "additionalProperties": False,
            "required": ["schematic", "pieces"],
            "properties": {
                "schematic": {"type": "string"},
                "pieces": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["code", "name_ar", "quantity", "dimensions", "stitch_count"],
                        "properties": {
                            "code": {"type": "string"},
                            "name_ar": {"type": "string"},
                            "quantity": {"type": "integer"},
                            "dimensions": {"type": "string"},
                            "stitch_count": {"type": "string"},
                        },
                    },
                },
            },
        },
        "instructions": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "difficulty", "yarn", "hook", "gauge", "materials", "starting_knot",
                "foundation_chain", "sections", "assembly", "finishing", "tips",
            ],
            "properties": {
                "difficulty": {"type": "string"},
                "yarn": {"type": "string"},
                "hook": {"type": "string"},
                "gauge": {"type": "string"},
                "materials": {"type": "array", "items": {"type": "string"}},
                "starting_knot": {"type": "string"},
                "foundation_chain": {"type": "string"},
                "sections": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["title", "steps"],
                        "properties": {
                            "title": {"type": "string"},
                            "steps": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
                "assembly": {"type": "array", "items": {"type": "string"}},
                "finishing": {"type": "array", "items": {"type": "string"}},
                "tips": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
}


# ═════════════════════════════ الأخطاء ═════════════════════════════
class UserFacingError(Exception):
    """خطأ برسالة جاهزة للعرض للمستخدم."""

    def __init__(self, title: str, message: str, tips: list[str] | None = None):
        super().__init__(message)
        self.title = title
        self.message = message
        self.tips = tips or []


PHOTO_TIPS = [
    "صوّري في ضوء نهار طبيعي من غير فلاش.",
    "افردي القطعة على سطح سادة بلون مختلف عن الخيط.",
    "خلي القطعة كاملة باينة في الكادر، ولو تقدري صورة تانية قريبة للغرزة.",
    "ثبّتي الموبايل كويس عشان الصورة ما تطلعش مهزوزة.",
]


# ═════════════════════════════ معالجة الصورة ═════════════════════════════
def sniff_media_type(data: bytes) -> str | None:
    """تحديد نوع الصورة من محتواها الفعلي مش من الامتداد."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


@dataclass
class PreparedImage:
    data: bytes
    media_type: str
    width: int | None
    height: int | None


def prepare_image(raw: bytes) -> PreparedImage:
    """يتحقق من الصورة ويصحح اتجاهها ويصغّرها لحجم مناسب للـ API."""
    if not raw:
        raise UserFacingError("الصورة فاضية", "الملف اللي اخترتيه مفيهوش بيانات. جرّبي صورة تانية.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise UserFacingError("الصورة كبيرة جداً", "حجم الصورة أكبر من 25 ميجا. صغّريها أو صوّري بجودة أقل.")

    if Image is None:
        media_type = sniff_media_type(raw)
        if media_type is None:
            raise UserFacingError(
                "صيغة غير مدعومة",
                "الصيغ المدعومة: JPG / PNG / WEBP / GIF. ثبّتي Pillow لدعم صيغ أكتر.",
            )
        if len(raw) > API_IMAGE_LIMIT:
            raise UserFacingError("الصورة كبيرة", "الصورة أكبر من 5 ميجا. ثبّتي Pillow للتصغير التلقائي أو صغّريها يدوياً.")
        return PreparedImage(raw, media_type, None, None)

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as ex:  # noqa: BLE001 - أي ملف تالف أو مش صورة
        raise UserFacingError("مقدرتش أفتح الصورة", "الملف تالف أو مش صورة. جرّبي صورة تانية.") from ex

    img = ImageOps.exif_transpose(img)  # صور الموبايل بتيجي متلفّة أحياناً
    width, height = img.size
    if min(width, height) < MIN_IMAGE_SIDE:
        raise UserFacingError(
            "الصورة صغيرة جداً",
            f"أبعاد الصورة {width}×{height} بكسل، والتفاصيل مش هتبان. استخدمي صورة أوضح.",
            PHOTO_TIPS,
        )

    if img.mode not in ("RGB", "L"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        rgba = img.convert("RGBA")
        background.paste(rgba, mask=rgba.split()[-1])
        img = background
    elif img.mode == "L":
        img = img.convert("RGB")

    img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.LANCZOS)

    for quality in (92, 85, 75, 65):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        if buf.tell() <= API_IMAGE_LIMIT:
            return PreparedImage(buf.getvalue(), "image/jpeg", img.width, img.height)

    raise UserFacingError("الصورة تقيلة", "مقدرتش أضغط الصورة لأقل من 5 ميجا. جرّبي صورة تانية.")


# ═════════════════════════════ التحليل بـ Claude ═════════════════════════════
async def analyze_image(image: PreparedImage, notes: str, api_key: str | None) -> dict:
    if anthropic is None:
        raise UserFacingError("مكتبة ناقصة", "مكتبة anthropic مش متثبتة. نفّذي: pip install anthropic")

    user_text = USER_PROMPT
    if notes:
        user_text += f"\n\nملاحظات المستخدمة (التزمي بيها):\n{notes}"

    client = anthropic.AsyncAnthropic(api_key=api_key) if api_key else anthropic.AsyncAnthropic()

    try:
        async with client.beta.messages.stream(
            model=MODEL,
            max_tokens=MAX_OUTPUT_TOKENS,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={
                "effort": "high",
                "format": {"type": "json_schema", "schema": ANALYSIS_SCHEMA},
            },
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": image.media_type,
                                "data": base64.standard_b64encode(image.data).decode("ascii"),
                            },
                        },
                        {"type": "text", "text": user_text},
                    ],
                }
            ],
        ) as stream:
            response = await stream.get_final_message()
    except anthropic.AuthenticationError as ex:
        raise UserFacingError("المفتاح غير صحيح", "مفتاح الـ API مرفوض. افتحي ⚙️ الإعدادات واتأكدي منه.") from ex
    except anthropic.PermissionDeniedError as ex:
        raise UserFacingError("مفيش صلاحية", "المفتاح ده مالوش صلاحية على النموذج المطلوب.") from ex
    except anthropic.RateLimitError as ex:
        raise UserFacingError("ضغط على الخدمة", "في طلبات كتير دلوقتي. استني دقيقة وجرّبي تاني.") from ex
    except anthropic.BadRequestError as ex:
        raise UserFacingError("الطلب مرفوض", f"الخدمة رفضت الصورة أو الطلب: {ex.message}") from ex
    except anthropic.APITimeoutError as ex:
        raise UserFacingError("التحليل أخد وقت طويل", "انتهت مهلة الاتصال. جرّبي تاني.") from ex
    except anthropic.APIConnectionError as ex:
        raise UserFacingError("مفيش اتصال", "اتأكدي من الإنترنت وجرّبي تاني.") from ex
    except anthropic.APIStatusError as ex:
        raise UserFacingError("خطأ من الخدمة", f"الخدمة رجّعت خطأ ({ex.status_code}). جرّبي كمان شوية.") from ex

    if response.stop_reason == "refusal":
        raise UserFacingError("مقدرتش أحلل الصورة دي", "الخدمة اعتذرت عن تحليل الصورة. جرّبي صورة تانية للقطعة.")
    if response.stop_reason == "max_tokens":
        raise UserFacingError(
            "الباترون طويل جداً",
            "الرد اتقطع قبل ما يكمل. جرّبي تاني، أو صوّري القطعة بشكل أبسط، أو اكتبي في الملاحظات أي جزء يهمك.",
        )

    text = "".join(block.text for block in response.content if block.type == "text").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as ex:
        raise UserFacingError("رد غير مكتمل", "الرد وصل بشكل غير سليم. جرّبي التحليل تاني.") from ex


# ═════════════════════════════ تنسيق النتيجة ═════════════════════════════
CLOSURE_AR = {
    "none": "بدون قفل",
    "buttons": "أزرار",
    "zipper": "سوستة",
    "ties": "أربطة",
    "toggle": "زراير خشب/تجل",
    "other": "قفل آخر",
}
CONFIDENCE_AR = {"high": "ثقة عالية", "medium": "ثقة متوسطة", "low": "ثقة منخفضة"}
STATUS_TITLES = {
    "unclear_image": "الصورة مش واضحة كفاية",
    "not_crochet": "القطعة دي مش كروشيه",
    "not_garment": "مش لاقية قطعة كروشيه في الصورة",
}


def bullet_list(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items if item.strip())


def numbered_list(items: list[str]) -> str:
    return "\n".join(f"{i}. {item}" for i, item in enumerate((s for s in items if s.strip()), start=1))


def build_instructions_markdown(ident: dict, ins: dict) -> str:
    parts: list[str] = []

    stitches = ident.get("main_stitches") or []
    if stitches:
        rows = ["| الغرزة | بالإنجليزي | الاختصار | مكانها |", "|---|---|---|---|"]
        for s in stitches:
            rows.append(f"| {s['name_ar']} | {s['name_en']} | `{s['abbr']}` | {s['where_used']} |")
        parts.append("### 🔤 الغرز المستخدمة\n" + "\n".join(rows))

    materials = [
        f"**مستوى الصعوبة:** {ins['difficulty']}" if ins.get("difficulty") else "",
        f"**الخيط:** {ins['yarn']}" if ins.get("yarn") else "",
        f"**الإبرة:** {ins['hook']}" if ins.get("hook") else "",
        f"**العيّنة (Gauge):** {ins['gauge']}" if ins.get("gauge") else "",
        *ins.get("materials", []),
    ]
    if any(materials):
        parts.append("### 🧺 الخامات والأدوات\n" + bullet_list(materials))

    if ins.get("starting_knot"):
        parts.append("### 🪢 عقدة البداية ومسك الخيط\n" + ins["starting_knot"])
    if ins.get("foundation_chain"):
        parts.append("### 🔢 السلاسل المبدئية\n" + ins["foundation_chain"])

    for section in ins.get("sections", []):
        steps = numbered_list(section.get("steps", []))
        if steps:
            parts.append(f"### 📋 {section['title']}\n{steps}")

    if ins.get("assembly"):
        parts.append("### 🧵 التجميع\n" + numbered_list(ins["assembly"]))
    if ins.get("finishing"):
        parts.append("### ✂️ التشطيب\n" + bullet_list(ins["finishing"]))
    if ins.get("tips"):
        parts.append("### 🪡 نصائح الخيط والإبرة\n" + bullet_list(ins["tips"]))

    return "\n\n".join(parts)


def build_plain_text_export(result: dict) -> str:
    """نسخة نصية كاملة للنسخ والمشاركة."""
    ident, diagram, ins = result["identification"], result["diagram"], result["instructions"]
    lines = [
        f"🧶 {ident['garment_type_ar']} ({ident['garment_type_en']}) - {ident['target_size']}",
        "",
        "═══ الرسمة التخطيطية ═══",
        diagram["schematic"],
        "",
        *[f"• {p['code']} = {p['name_ar']} ×{p['quantity']} | {p['dimensions']} | {p['stitch_count']}" for p in diagram["pieces"]],
        "",
        "═══ الشرح التفصيلي ═══",
        build_instructions_markdown(ident, ins),
    ]
    return "\n".join(lines)


# ═════════════════════════════ الواجهة ═════════════════════════════
def main(page: ft.Page):
    page.title = "مساعد الكروشيه الذكي"
    page.rtl = True
    page.theme_mode = ft.ThemeMode.LIGHT
    page.theme = ft.Theme(color_scheme_seed=ROSE, use_material3=True)
    page.bgcolor = CREAM
    page.padding = 0

    state: dict = {"raw": None, "name": None, "busy": False, "result": None, "api_key": None}

    file_picker = ft.FilePicker()
    prefs = ft.SharedPreferences()
    clipboard = ft.Clipboard()

    # ─────────── عناصر مساعدة ───────────
    def card(content: ft.Control, visible: bool = True, padding: int = 20) -> ft.Container:
        return ft.Container(
            content=content,
            bgcolor=CARD,
            border=ft.Border.all(1, BORDER),
            border_radius=ft.BorderRadius.all(20),
            padding=padding,
            visible=visible,
        )

    def section_header(number: str, title: str, icon) -> ft.Row:
        return ft.Row(
            controls=[
                ft.Container(
                    content=ft.Text(number, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=13),
                    bgcolor=ROSE,
                    width=28,
                    height=28,
                    border_radius=ft.BorderRadius.all(14),
                    alignment=ft.Alignment.CENTER,
                ),
                ft.Icon(icon, color=ROSE, size=22),
                ft.Text(title, size=18, weight=ft.FontWeight.BOLD, color=TEXT_DARK, expand=True),
            ],
            spacing=8,
        )

    def chip(text: str, bg: str = ROSE_TINT, fg: str = ROSE) -> ft.Container:
        return ft.Container(
            content=ft.Text(text, size=12, color=fg, weight=ft.FontWeight.W_600),
            bgcolor=bg,
            border_radius=ft.BorderRadius.all(20),
            padding=ft.Padding.symmetric(horizontal=12, vertical=6),
        )

    def toast(message: str, error: bool = False):
        page.show_dialog(
            ft.SnackBar(
                content=ft.Text(message, color=ft.Colors.WHITE),
                bgcolor="#B71C1C" if error else TEXT_DARK,
            )
        )

    # ─────────── بطاقة الرفع ───────────
    placeholder = ft.Column(
        controls=[
            ft.Icon(ft.Icons.IMAGE_OUTLINED, size=56, color=ROSE_SOFT),
            ft.Text("لسه ما اخترتيش صورة", size=15, color=TEXT_MUTED),
            ft.Text(
                "صورة واضحة للقطعة كاملة في ضوء طبيعي = باترون أدق",
                size=12,
                color=TEXT_MUTED,
                text_align=ft.TextAlign.CENTER,
            ),
        ],
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        alignment=ft.MainAxisAlignment.CENTER,
        spacing=6,
    )

    image_box = ft.Container(
        content=placeholder,
        alignment=ft.Alignment.CENTER,
        bgcolor="#FDF0F5",
        border=ft.Border.all(2, BORDER),
        border_radius=ft.BorderRadius.all(20),
        padding=12,
        height=320,
    )

    file_info = ft.Text("", size=12, color=TEXT_MUTED, visible=False)

    notes_field = ft.TextField(
        label="ملاحظات إضافية (اختياري)",
        hint_text="مثلاً: المقاس المطلوب سنتين، أو عندي خيط قطن 4 مم...",
        multiline=True,
        min_lines=1,
        max_lines=4,
        max_length=500,
        border_radius=14,
        border_color=BORDER,
        focused_border_color=ROSE,
    )

    pick_button = ft.OutlinedButton(
        content="اختاري صورة القطعة",
        icon=ft.Icons.ADD_PHOTO_ALTERNATE_ROUNDED,
        height=50,
        expand=True,
        style=ft.ButtonStyle(
            color=ROSE,
            shape=ft.RoundedRectangleBorder(radius=14),
            side=ft.BorderSide(1.5, ROSE),
        ),
    )

    analyze_button = ft.FilledButton(
        content="حلّلي الصورة وطلّعي الباترون",
        icon=ft.Icons.AUTO_AWESOME_ROUNDED,
        height=50,
        expand=True,
        disabled=True,
        style=ft.ButtonStyle(
            bgcolor=ROSE,
            color=ft.Colors.WHITE,
            shape=ft.RoundedRectangleBorder(radius=14),
        ),
    )

    loading = ft.Container(
        content=ft.Column(
            controls=[
                ft.ProgressBar(color=ROSE, bgcolor=ROSE_TINT, border_radius=ft.BorderRadius.all(4)),
                ft.Text(
                    "بفحص القطعة والغرزة وبرسم الباترون... ده ممكن ياخد دقيقة أو اتنين",
                    size=13,
                    color=TEXT_MUTED,
                    text_align=ft.TextAlign.CENTER,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=10,
        ),
        visible=False,
    )

    # ─────────── بطاقة التنبيه / الخطأ ───────────
    alert_icon = ft.Icon(ft.Icons.ERROR_OUTLINE_ROUNDED, color=ERR_FG)
    alert_title = ft.Text("", size=16, weight=ft.FontWeight.BOLD, color=ERR_FG, expand=True)
    alert_body = ft.Text("", size=14, color=TEXT_DARK, selectable=True)
    alert_tips = ft.Column(spacing=4)
    alert_card = ft.Container(
        content=ft.Column(
            controls=[ft.Row([alert_icon, alert_title], spacing=8), alert_body, alert_tips],
            spacing=8,
        ),
        bgcolor=ERR_BG,
        border_radius=ft.BorderRadius.all(16),
        padding=16,
        visible=False,
    )

    def show_alert(title: str, message: str, tips: list[str] | None = None, warning: bool = False):
        fg = WARN_FG if warning else ERR_FG
        alert_card.bgcolor = WARN_BG if warning else ERR_BG
        alert_icon.icon = ft.Icons.WARNING_AMBER_ROUNDED if warning else ft.Icons.ERROR_OUTLINE_ROUNDED
        alert_icon.color = fg
        alert_title.value = title
        alert_title.color = fg
        alert_body.value = message
        alert_tips.controls = [ft.Text(f"• {t}", size=13, color=TEXT_DARK) for t in (tips or [])]
        alert_card.visible = True

    # ─────────── القسم الأول: الرسمة التخطيطية ───────────
    diagram_subtitle = ft.Text("", size=14, color=TEXT_MUTED)
    diagram_chips = ft.Row(wrap=True, spacing=6, run_spacing=6)
    schematic_text = ft.Text(
        "",
        font_family=MONO_FONT,
        font_family_fallback=MONO_FALLBACK,
        size=12.5,
        color=TEXT_DARK,
        selectable=True,
        no_wrap=True,
    )
    schematic_box = ft.Container(
        content=ft.Row(controls=[schematic_text], scroll=ft.ScrollMode.AUTO),
        rtl=False,  # الرسمة لازم تتعرض من الشمال لليمين عشان المحاذاة
        bgcolor=CHART_BG,
        border=ft.Border.all(1, BORDER),
        border_radius=ft.BorderRadius.all(14),
        padding=14,
    )
    pieces_list = ft.Column(spacing=8)

    diagram_card = card(
        ft.Column(
            controls=[
                section_header("1", "رسمة الباترون التخطيطية", ft.Icons.GRID_ON_ROUNDED),
                diagram_subtitle,
                diagram_chips,
                ft.Divider(color=BORDER),
                schematic_box,
                ft.Text("مفتاح القطع", size=15, weight=ft.FontWeight.BOLD, color=TEXT_DARK),
                pieces_list,
            ],
            spacing=10,
        ),
        visible=False,
    )

    def piece_tile(piece: dict) -> ft.Container:
        return ft.Container(
            content=ft.Row(
                controls=[
                    ft.Container(
                        content=ft.Text(
                            piece["code"],
                            font_family=MONO_FONT,
                            font_family_fallback=MONO_FALLBACK,
                            size=12,
                            weight=ft.FontWeight.BOLD,
                            color=ROSE,
                        ),
                        bgcolor=ROSE_TINT,
                        border_radius=ft.BorderRadius.all(8),
                        padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                        rtl=False,
                    ),
                    ft.Column(
                        controls=[
                            ft.Text(
                                f"{piece['name_ar']}  ×{piece['quantity']}",
                                size=14,
                                weight=ft.FontWeight.W_600,
                                color=TEXT_DARK,
                            ),
                            ft.Text(f"📐 {piece['dimensions']}", size=12.5, color=TEXT_MUTED),
                            ft.Text(f"🔢 {piece['stitch_count']}", size=12.5, color=TEXT_MUTED),
                        ],
                        spacing=2,
                        expand=True,
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.START,
                spacing=10,
            ),
            border=ft.Border.all(1, BORDER),
            border_radius=ft.BorderRadius.all(12),
            padding=10,
        )

    # ─────────── القسم الثاني: الشرح التفصيلي ───────────
    instructions_md = ft.Markdown(
        value="",
        selectable=True,
        extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
    )
    copy_button = ft.TextButton(content="نسخ الباترون كامل", icon=ft.Icons.COPY_ROUNDED)

    instructions_card = card(
        ft.Column(
            controls=[
                section_header("2", "الشرح التفصيلي وطريقة العمل", ft.Icons.MENU_BOOK_ROUNDED),
                ft.Divider(color=BORDER),
                instructions_md,
                ft.Row([copy_button], alignment=ft.MainAxisAlignment.END),
            ],
            spacing=8,
        ),
        visible=False,
    )

    # ─────────── الإعدادات (المفتاح) ───────────
    key_field = ft.TextField(
        label="Anthropic API Key",
        password=True,
        can_reveal_password=True,
        border_radius=12,
        rtl=False,
    )

    async def save_key(e):
        value = (key_field.value or "").strip()
        if value:
            await prefs.set(PREFS_KEY, value)
            state["api_key"] = value
            toast("تم حفظ المفتاح على الجهاز ✅")
        else:
            await prefs.remove(PREFS_KEY)
            state["api_key"] = None
            toast("تم مسح المفتاح المحفوظ")
        page.pop_dialog()
        refresh_buttons()

    settings_dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("الإعدادات"),
        content=ft.Column(
            controls=[
                ft.Text(
                    "المفتاح بيتحفظ على الجهاز ده بس. لو متغير البيئة ANTHROPIC_API_KEY موجود هيتستخدم تلقائياً.",
                    size=13,
                    color=TEXT_MUTED,
                ),
                key_field,
            ],
            tight=True,
            spacing=12,
            width=420,
        ),
        actions=[
            ft.TextButton(content="إلغاء", on_click=lambda e: page.pop_dialog()),
            ft.FilledButton(content="حفظ", on_click=save_key),
        ],
    )

    def open_settings(e):
        key_field.value = state["api_key"] or ""
        page.show_dialog(settings_dialog)

    def has_credentials() -> bool:
        return bool(state["api_key"] or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))

    # ─────────── المنطق ───────────
    def refresh_buttons():
        busy = state["busy"]
        loading.visible = busy
        pick_button.disabled = busy
        analyze_button.disabled = busy or state["raw"] is None
        page.update()

    def clear_results():
        alert_card.visible = False
        diagram_card.visible = False
        instructions_card.visible = False
        state["result"] = None

    async def on_pick(e):
        files = await file_picker.pick_files(
            dialog_title="اختاري صورة قطعة الكروشيه",
            file_type=ft.FilePickerFileType.IMAGE,
            allow_multiple=False,
            with_data=True,
        )
        if not files:
            return

        picked = files[0]
        data = picked.bytes
        if data is None and picked.path:
            try:
                with open(picked.path, "rb") as f:
                    data = f.read()
            except OSError:
                data = None

        clear_results()
        if not data:
            state["raw"] = None
            show_alert("مقدرتش أقرا الصورة", "جرّبي تختاري الصورة تاني أو صورة مختلفة.")
            refresh_buttons()
            return

        state["raw"] = data
        state["name"] = picked.name
        image_box.content = ft.Image(
            src=data,
            fit=ft.BoxFit.CONTAIN,
            border_radius=ft.BorderRadius.all(16),
            height=296,
        )
        file_info.value = f"📎 {picked.name}  •  {len(data) / 1024:.0f} KB"
        file_info.visible = True
        refresh_buttons()

    def render_result(result: dict):
        status = result.get("status")
        ident = result["identification"]

        if status != "ok":
            show_alert(
                STATUS_TITLES.get(status, "مقدرتش أحلل القطعة"),
                result.get("status_message") or "جرّبي صورة أوضح للقطعة.",
                PHOTO_TIPS if status == "unclear_image" else None,
                warning=True,
            )
            return False

        # تنبيه لو الثقة مش عالية أو فيه نقط مش واضحة
        if ident["confidence"] != "high" or ident["uncertain_points"]:
            show_alert(
                f"ملاحظات على التحليل ({CONFIDENCE_AR[ident['confidence']]})",
                "بعض التفاصيل مقدّرة من الصورة، راجعيها قبل ما تبدئي:",
                ident["uncertain_points"],
                warning=True,
            )

        diagram_subtitle.value = (
            f"🧶 {ident['garment_type_ar']} ({ident['garment_type_en']})"
            f"  •  {ident['target_size']}  •  {ident['construction']}"
        )

        closure = CLOSURE_AR.get(ident["closure"], ident["closure"])
        if ident["closure"] == "buttons" and ident["button_count"] > 0:
            closure = f"{ident['button_count']} أزرار"
        chips = [
            chip(CONFIDENCE_AR[ident["confidence"]]),
            chip(f"🔘 {closure}"),
            chip("🧢 بهودي" if ident["has_hood"] else "بدون هودي"),
            chip(f"👝 {ident['pockets']}") if ident["pockets"] else None,
            chip(f"👕 {ident['neckline_collar']}") if ident["neckline_collar"] else None,
            chip(f"💪 {ident['sleeves']}") if ident["sleeves"] else None,
            chip(f"🎨 {ident['colors']}") if ident["colors"] else None,
        ]
        diagram_chips.controls = [c for c in chips if c is not None]

        schematic_text.value = result["diagram"]["schematic"].rstrip()
        pieces_list.controls = [piece_tile(p) for p in result["diagram"]["pieces"]]
        diagram_card.visible = True

        instructions_md.value = build_instructions_markdown(ident, result["instructions"])
        instructions_card.visible = True
        return True

    async def on_analyze(e):
        if state["raw"] is None or state["busy"]:
            return
        if anthropic is None:
            clear_results()
            show_alert("مكتبة ناقصة", "ثبّتي مكتبة anthropic الأول: pip install anthropic")
            page.update()
            return
        if not has_credentials():
            clear_results()
            show_alert("محتاجين مفتاح الـ API", "افتحي ⚙️ الإعدادات فوق وحطّي مفتاح Anthropic عشان نبدأ التحليل.")
            page.update()
            return

        clear_results()
        state["busy"] = True
        refresh_buttons()
        try:
            image = prepare_image(state["raw"])
            result = await analyze_image(image, (notes_field.value or "").strip(), state["api_key"])
            if render_result(result):
                state["result"] = result
                toast("الباترون جاهز! بالتوفيق في الشغل 💕")
        except UserFacingError as ex:
            show_alert(ex.title, ex.message, ex.tips)
        except (KeyError, TypeError) as ex:
            show_alert("رد غير متوقع", "النتيجة وصلت ناقصة. جرّبي التحليل تاني.")
            print(f"[crochet] malformed result: {ex!r}")
        except Exception as ex:  # noqa: BLE001 - آخر خط دفاع عشان الواجهة ما تقعش
            show_alert("حصل خطأ غير متوقع", "جرّبي تاني، ولو المشكلة استمرت أعيدي فتح التطبيق.")
            print(f"[crochet] unexpected error: {ex!r}")
        finally:
            state["busy"] = False
            refresh_buttons()

    async def on_copy(e):
        if state["result"]:
            await clipboard.set(build_plain_text_export(state["result"]))
            toast("اتنسخ الباترون ✅")

    pick_button.on_click = on_pick
    analyze_button.on_click = on_analyze
    copy_button.on_click = on_copy

    # ─────────── التجميع ───────────
    header = ft.Container(
        content=ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        ft.IconButton(
                            icon=ft.Icons.SETTINGS_ROUNDED,
                            icon_color=TEXT_MUTED,
                            tooltip="الإعدادات",
                            on_click=open_settings,
                        )
                    ],
                    alignment=ft.MainAxisAlignment.END,
                ),
                ft.Text("🧶", size=44),
                ft.Text(
                    "مساعد الكروشيه الذكي",
                    size=26,
                    weight=ft.FontWeight.BOLD,
                    color=TEXT_DARK,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Text(
                    "ارفعي صورة أي قطعة، وهنطلّعلك رسمة الباترون وخطوات الشغل بالتفصيل",
                    size=14,
                    color=TEXT_MUTED,
                    text_align=ft.TextAlign.CENTER,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=6,
        ),
    )

    upload_card = ft.Container(
        content=ft.Column(
            controls=[
                image_box,
                file_info,
                notes_field,
                ft.Row(controls=[pick_button]),
                ft.Row(controls=[analyze_button]),
                loading,
            ],
            spacing=14,
        ),
        bgcolor=CARD,
        border=ft.Border.all(1, BORDER),
        border_radius=ft.BorderRadius.all(24),
        padding=20,
        shadow=ft.BoxShadow(blur_radius=24, spread_radius=0, color="#14C2185B", offset=ft.Offset(0, 6)),
    )

    content = ft.Container(
        content=ft.Column(
            controls=[
                header,
                upload_card,
                alert_card,
                diagram_card,       # أولاً: الرسمة التخطيطية
                instructions_card,  # ثانياً: الشرح التفصيلي
                ft.Container(height=24),
            ],
            spacing=18,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
        max_width=760,
        padding=ft.Padding.symmetric(horizontal=16, vertical=8),
    )

    page.add(
        ft.SafeArea(
            content=ft.Column(
                controls=[content],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                scroll=ft.ScrollMode.AUTO,
                expand=True,
            ),
            expand=True,
        )
    )

    async def load_saved_key():
        try:
            saved = await prefs.get(PREFS_KEY)
        except Exception:  # noqa: BLE001 - التخزين مش متاح على كل المنصات
            saved = None
        if isinstance(saved, str) and saved.strip():
            state["api_key"] = saved.strip()

    page.run_task(load_saved_key)


if __name__ == "__main__":
    ft.run(main)
