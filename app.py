# -*- coding: utf-8 -*-
"""
باترون — مساعد الكروشيه الذكي (Flet 1.1.2 — أندرويد / سطح المكتب)

- تسجيل دخول بحساب Google (بدون أي API Key على الهاتف).
- رفع صورة قطعة كاملة أو تفصيلة موتيف ← تحليل بـ Gemini Vision عبر خادم باترون.
- النتيجة بترتيب ثابت: ① رسمة المخطط التخطيطي ② الشرح التفصيلي خطوة بخطوة.

التشغيل على الكمبيوتر:   python app.py
بناء APK:               flet build apk
"""

import asyncio
import base64
import io
import re
import secrets

import flet as ft
import httpx
from PIL import Image, ImageOps

# ═══════════════════════════════════════════════════════════════════
#  الإعداد الوحيد المطلوب: رابط خادم باترون (مجلد server/)
#  - تجربة على الكمبيوتر:  "http://localhost:8000"
#  - على الهاتف:          رابط HTTPS العام بعد النشر (Cloud Run مثلاً)
# ═══════════════════════════════════════════════════════════════════
BACKEND_URL = "https://YOUR-PATRON-SERVER.run.app"

APP_NAME = "باترون"
TOKEN_KEY = "patron.session_token"
USER_KEY = "patron.user_name"
LOGIN_POLL_SECONDS = 2
LOGIN_TIMEOUT_SECONDS = 600
ANALYZE_TIMEOUT_SECONDS = 260

MAX_IMAGE_SIDE = 1600
PREVIEW_SIDE = 900

SCHEMATIC_HEADING = "📐 أولاً: رسمة وباترون المخطط التخطيطي (Schematic Diagram / Chart Layout)"
STEPS_HEADING = "📋 ثانياً: الشرح التفصيلي وكيفية العمل (Step-by-Step Instructions)"

# ─────────── الألوان ───────────
ACCENT = "#B04A78"
ACCENT_TINT = "#F7E6EE"
BG = "#FBF7F4"
CARD = "#FFFFFF"
BORDER = "#EADDE3"
TEXT_DARK = "#3F2A35"
TEXT_MUTED = "#86707C"
CHART_BG = "#FCFAF7"
ERR_BG = "#FDECEA"
ERR_FG = "#9B1C1C"
MONO_FONT = "monospace"
MONO_FALLBACK = ["Consolas", "Menlo", "Courier New", "Roboto Mono"]


# ═══════════════════════════════════════════════════════════════════
#  أدوات مساعدة
# ═══════════════════════════════════════════════════════════════════
class UserFacingError(Exception):
    pass


class SessionExpired(Exception):
    pass


def load_image(data: bytes) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as ex:
        raise UserFacingError(
            "تعذر فتح الصورة. استخدم JPG أو PNG أو WEBP (صور HEIC تحتاج تحويل أولاً)."
        ) from ex
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[-1])
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")
    if min(img.size) < 250:
        raise UserFacingError("الصورة صغيرة جداً. استخدم صورة أوضح (300 بكسل على الأقل).")
    img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.LANCZOS)
    return img


def image_to_jpeg_bytes(img: Image.Image, side: int, quality: int = 88) -> bytes:
    copy = img.copy()
    copy.thumbnail((side, side), Image.LANCZOS)
    buf = io.BytesIO()
    copy.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def split_code_segments(md: str) -> list:
    """يفصل كتل الكود (الرسمة التخطيطية) عن النص لعرضها بخط ثابت العرض ومن اليسار لليمين."""
    segments = []
    for part in re.split(r"(```[^\n]*\n[\s\S]*?```)", md):
        if not part.strip():
            continue
        if part.startswith("```"):
            body = part.split("\n", 1)[1] if "\n" in part else ""
            segments.append(("code", re.sub(r"\n?```\s*$", "", body).rstrip()))
        else:
            segments.append(("md", part.strip()))
    return segments


def force_ltr(text: str) -> str:
    """يجبر كل سطر من الرسمة على الاتجاه من اليسار لليمين داخل الصفحة العربية.

    rtl=False على الحاوية لا يُرسَل لأنه القيمة الافتراضية، فترث الرسمة اتجاه الصفحة (RTL)
    وتنقلب الأسطر المكونة من رموز فقط مثل «/\\  /\\». علامتا LRO/PDF غير مرئيتين.
    """
    return "\n".join(f"‭{line}‬" for line in text.split("\n"))


def compose_markdown(result: dict) -> str:
    """الترتيب الصارم: الرسمة أولاً ثم الشرح — يُبنى هنا وليس متروكاً للنموذج."""
    return (
        f"# {result.get('title', 'الباترون')}\n\n"
        f"## {SCHEMATIC_HEADING}\n\n{result.get('schematic_markdown', '').strip()}\n\n"
        f"---\n\n"
        f"## {STEPS_HEADING}\n\n{result.get('instructions_markdown', '').strip()}\n"
    )


def error_detail(resp: httpx.Response) -> str:
    try:
        detail = resp.json().get("detail")
        if isinstance(detail, str):
            return detail
    except ValueError:
        pass
    return f"حدث خطأ في الخادم (رمز {resp.status_code}). حاول مرة أخرى."


# ═══════════════════════════════════════════════════════════════════
#  الاتصال بالخادم
# ═══════════════════════════════════════════════════════════════════
class Api:
    def __init__(self, base_url: str):
        self.base = base_url.rstrip("/")
        self.token: str | None = None

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    async def login_status(self, sid: str) -> dict | None:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{self.base}/auth/status", params={"sid": sid})
        if r.status_code == 404:
            raise UserFacingError("انتهت مهلة تسجيل الدخول. اضغط الزر مرة أخرى.")
        r.raise_for_status()
        data = r.json()
        return data if data.get("status") == "ok" else None

    async def me(self) -> dict:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{self.base}/me", headers=self._headers())
        if r.status_code == 401:
            raise SessionExpired()
        r.raise_for_status()
        return r.json()

    async def analyze(self, jpeg: bytes, mode: str, notes: str) -> dict:
        payload = {
            "image_b64": base64.b64encode(jpeg).decode(),
            "mime_type": "image/jpeg",
            "mode": mode,
            "notes": notes,
        }
        try:
            async with httpx.AsyncClient(timeout=ANALYZE_TIMEOUT_SECONDS) as c:
                r = await c.post(f"{self.base}/analyze", json=payload, headers=self._headers())
        except httpx.TimeoutException as ex:
            raise UserFacingError("استغرق التحليل وقتاً أطول من المعتاد. حاول مرة أخرى.") from ex
        except httpx.HTTPError as ex:
            raise UserFacingError("تعذر الاتصال بالخادم. تأكد من اتصال الإنترنت.") from ex
        if r.status_code == 401:
            raise SessionExpired()
        if r.status_code != 200:
            raise UserFacingError(error_detail(r))
        return r.json()


# ═══════════════════════════════════════════════════════════════════
#  الواجهة
# ═══════════════════════════════════════════════════════════════════
async def main(page: ft.Page):
    page.title = APP_NAME
    page.rtl = True
    page.theme_mode = ft.ThemeMode.LIGHT
    page.theme = ft.Theme(color_scheme_seed=ACCENT, use_material3=True)
    page.bgcolor = BG
    page.padding = 0
    page.scroll = ft.ScrollMode.AUTO

    api = Api(BACKEND_URL)
    prefs = ft.SharedPreferences()
    file_picker = ft.FilePicker()
    clipboard = ft.Clipboard()
    url_launcher = ft.UrlLauncher()
    state = {"image": None, "busy": False, "markdown": "", "login_sid": None, "user": ""}

    root = ft.Column(horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=0)

    # ─────────── أدوات عامة ───────────
    async def prefs_get(key):
        try:
            return await prefs.get(key)
        except Exception:
            return None

    async def prefs_set(key, value):
        try:
            await prefs.set(key, value)
        except Exception as ex:
            print(f"[patron] prefs error: {ex!r}")

    async def prefs_remove(key):
        try:
            await prefs.remove(key)
        except Exception:
            pass

    async def open_url(url: str):
        # على أندرويد: Chrome Custom Tab (يسمح به Google ويعرض حسابات الهاتف مباشرة).
        # ممنوع WebView داخلي — Google يرفض تسجيل الدخول منه (disallowed_useragent).
        mobile = page.platform in (ft.PagePlatform.ANDROID, ft.PagePlatform.IOS)
        mode = ft.LaunchMode.IN_APP_BROWSER_VIEW if mobile else ft.LaunchMode.PLATFORM_DEFAULT
        try:
            await url_launcher.launch_url(url, mode=mode)
        except Exception:
            await url_launcher.launch_url(url, mode=ft.LaunchMode.EXTERNAL_APPLICATION)

    def toast(text: str):
        page.show_dialog(ft.SnackBar(content=ft.Text(text, color=ft.Colors.WHITE), bgcolor=TEXT_DARK))

    def card(content, visible=True):
        return ft.Container(
            content=content,
            bgcolor=CARD,
            border=ft.Border.all(1, BORDER),
            border_radius=ft.BorderRadius.all(20),
            padding=20,
            visible=visible,
        )

    def header(subtitle: str, trailing=None):
        title_row = [
            ft.Icon(ft.Icons.AUTO_AWESOME_ROUNDED, color=ft.Colors.WHITE, size=30),
            ft.Text(APP_NAME + " — مساعد الكروشيه الذكي", size=22, weight=ft.FontWeight.BOLD,
                    color=ft.Colors.WHITE, expand=True),
        ]
        if trailing:
            title_row.append(trailing)
        return ft.Container(
            content=ft.Column(
                controls=[ft.Row(controls=title_row, spacing=10),
                          ft.Text(subtitle, size=14, color="#FBEFF4")],
                spacing=6,
            ),
            gradient=ft.LinearGradient(
                begin=ft.Alignment.TOP_RIGHT,
                end=ft.Alignment.BOTTOM_LEFT,
                colors=["#B04A78", "#D98BAA"],
            ),
            padding=ft.Padding.symmetric(horizontal=24, vertical=26),
            border_radius=ft.BorderRadius.only(bottom_left=28, bottom_right=28),
        )

    def body(*controls):
        return ft.Container(
            content=ft.Column(controls=list(controls), spacing=16),
            padding=ft.Padding.symmetric(horizontal=16, vertical=16),
            width=920,
        )

    # ═══════════════════════════════════════════════════════════════
    #  شاشة تسجيل الدخول
    # ═══════════════════════════════════════════════════════════════
    login_status_text = ft.Text("", size=13, color=TEXT_MUTED, text_align=ft.TextAlign.CENTER)
    login_progress = ft.ProgressRing(width=22, height=22, stroke_width=3, color=ACCENT, visible=False)
    google_button = ft.FilledButton(content="تسجيل الدخول بحساب Google", icon=ft.Icons.LOGIN_ROUNDED,
                                    height=50)
    login_error = ft.Text("", size=13, color=ERR_FG, visible=False, text_align=ft.TextAlign.CENTER)

    login_view = ft.Column(
        controls=[
            header("ارفع صورة أي قطعة أو موتيف كروشيه، واحصل على الباترون التخطيطي والشرح خطوة بخطوة."),
            body(
                card(
                    ft.Column(
                        controls=[
                            ft.Icon(ft.Icons.ACCOUNT_CIRCLE_ROUNDED, size=64, color="#D9A9BF"),
                            ft.Text("مرحباً بك 👋", size=20, weight=ft.FontWeight.BOLD, color=TEXT_DARK),
                            ft.Text("سجّل الدخول بحساب Google الموجود على هاتفك لتبدأ — بنقرة واحدة "
                                    "وبدون أي إعدادات.", size=14, color=TEXT_MUTED,
                                    text_align=ft.TextAlign.CENTER),
                            google_button,
                            ft.Row([login_progress, login_status_text],
                                   alignment=ft.MainAxisAlignment.CENTER, spacing=10),
                            login_error,
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=14,
                    )
                )
            ),
        ],
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        spacing=0,
    )

    def set_login_waiting(waiting: bool, message: str = ""):
        google_button.disabled = waiting
        login_progress.visible = waiting
        login_status_text.value = message

    async def on_google_login(e):
        login_error.visible = False
        sid = secrets.token_urlsafe(32)
        state["login_sid"] = sid
        set_login_waiting(True, "اختر حسابك في المتصفح، ثم ارجع إلى التطبيق…")
        page.update()
        await open_url(f"{api.base}/auth/start?sid={sid}")
        page.run_task(poll_login, sid)

    async def poll_login(sid: str):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + LOGIN_TIMEOUT_SECONDS
        try:
            while state["login_sid"] == sid and loop.time() < deadline:
                await asyncio.sleep(LOGIN_POLL_SECONDS)
                try:
                    result = await api.login_status(sid)
                except httpx.HTTPError:
                    continue  # انقطاع مؤقت أثناء التبديل بين المتصفح والتطبيق
                if result:
                    api.token = result["token"]
                    state["user"] = result["user"].get("name", "")
                    await prefs_set(TOKEN_KEY, api.token)
                    await prefs_set(USER_KEY, state["user"])
                    state["login_sid"] = None
                    set_login_waiting(False)
                    show_home()
                    toast(f"أهلاً {state['user']} 🌸")
                    return
            if state["login_sid"] == sid:
                raise UserFacingError("انتهت مهلة تسجيل الدخول. اضغط الزر مرة أخرى.")
        except UserFacingError as ex:
            state["login_sid"] = None
            set_login_waiting(False)
            login_error.value = str(ex)
            login_error.visible = True
            page.update()

    google_button.on_click = on_google_login

    # ═══════════════════════════════════════════════════════════════
    #  الشاشة الرئيسية
    # ═══════════════════════════════════════════════════════════════
    user_label = ft.Text("", size=12, color=TEXT_MUTED)  # تحت الترويسة على خلفية فاتحة
    logout_button = ft.IconButton(icon=ft.Icons.LOGOUT_ROUNDED, icon_color=ft.Colors.WHITE,
                                  tooltip="تسجيل الخروج")

    placeholder = ft.Column(
        controls=[
            ft.Icon(ft.Icons.ADD_PHOTO_ALTERNATE_OUTLINED, size=56, color="#D9A9BF"),
            ft.Text("لم يتم اختيار صورة بعد", size=15, color=TEXT_MUTED),
            ft.Text("قطعة كاملة: صوّرها مفرودة على خلفية سادة.\nموتيف: صوّره عن قرب وبإضاءة طبيعية.",
                    size=12, color=TEXT_MUTED, text_align=ft.TextAlign.CENTER),
        ],
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        alignment=ft.MainAxisAlignment.CENTER,
        spacing=6,
    )
    image_box = ft.Container(
        content=placeholder, height=300, bgcolor=ACCENT_TINT,
        border_radius=ft.BorderRadius.all(16), alignment=ft.Alignment.CENTER, padding=6,
    )
    file_info = ft.Text("", size=12, color=TEXT_MUTED, visible=False)

    mode_group = ft.RadioGroup(
        value="auto",
        content=ft.Row(
            controls=[
                ft.Radio(value="auto", label="تلقائي"),
                ft.Radio(value="garment", label="قطعة كاملة"),
                ft.Radio(value="motif", label="موتيف / تفصيلة"),
            ],
            wrap=True,
        ),
    )
    notes_field = ft.TextField(
        label="ملاحظات اختيارية (المقاس، نوع الخيط…)", multiline=True, min_lines=1, max_lines=3,
        max_length=300, border_radius=12,
    )

    pick_button = ft.OutlinedButton(content="اختيار صورة", icon=ft.Icons.IMAGE_SEARCH_ROUNDED)
    analyze_button = ft.FilledButton(content="تحليل واستخراج الباترون",
                                     icon=ft.Icons.AUTO_FIX_HIGH_ROUNDED, disabled=True)

    progress_text = ft.Text("", size=14, color=TEXT_DARK, weight=ft.FontWeight.W_500, expand=True)
    progress_box = ft.Container(
        content=ft.Row(
            controls=[ft.ProgressRing(width=26, height=26, stroke_width=3, color=ACCENT), progress_text],
            spacing=12,
        ),
        bgcolor=ACCENT_TINT, border_radius=ft.BorderRadius.all(14), padding=14, visible=False,
    )

    upload_card = card(
        ft.Column(
            controls=[
                image_box,
                file_info,
                ft.Text("نوع الصورة:", size=13, color=TEXT_DARK, weight=ft.FontWeight.W_600),
                mode_group,
                notes_field,
                ft.Row([pick_button, analyze_button], wrap=True, spacing=10, run_spacing=10),
                progress_box,
            ],
            spacing=12,
        )
    )

    error_text = ft.Text("", size=14, color=ERR_FG, selectable=True, expand=True)
    error_card = ft.Container(
        content=ft.Row(
            controls=[ft.Icon(ft.Icons.ERROR_OUTLINE_ROUNDED, color=ERR_FG), error_text],
            vertical_alignment=ft.CrossAxisAlignment.START, spacing=10,
        ),
        bgcolor=ERR_BG, border_radius=ft.BorderRadius.all(14), padding=16, visible=False,
    )

    result_title = ft.Text("", size=20, weight=ft.FontWeight.BOLD, color=TEXT_DARK, expand=True)
    meta_text = ft.Text("", size=12, color=ACCENT, weight=ft.FontWeight.W_600)
    result_body = ft.Column(spacing=10)
    copy_button = ft.TextButton(content="نسخ الباترون", icon=ft.Icons.COPY_ROUNDED)

    result_card = card(
        ft.Column(
            controls=[
                ft.Row([ft.Icon(ft.Icons.MENU_BOOK_ROUNDED, color=ACCENT), result_title, copy_button],
                       spacing=8),
                meta_text,
                ft.Divider(color=BORDER),
                result_body,
            ],
            spacing=10,
        ),
        visible=False,
    )

    def section_heading(text: str):
        return ft.Container(
            content=ft.Text(text, size=17, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
            bgcolor=ACCENT, border_radius=ft.BorderRadius.all(12),
            padding=ft.Padding.symmetric(horizontal=14, vertical=10),
            margin=ft.Margin.only(top=8),
        )

    def render_section(md: str) -> list:
        controls = []
        for kind, content in split_code_segments(md):
            if kind == "code":
                controls.append(
                    ft.Container(
                        content=ft.Row(
                            controls=[ft.Text(force_ltr(content), font_family=MONO_FONT,
                                              font_family_fallback=MONO_FALLBACK, size=12,
                                              color=TEXT_DARK, selectable=True, no_wrap=True,
                                              text_align=ft.TextAlign.LEFT)],
                            scroll=ft.ScrollMode.AUTO,
                        ),
                        bgcolor=CHART_BG, border=ft.Border.all(1, BORDER),
                        border_radius=ft.BorderRadius.all(14), padding=14,
                    )
                )
            else:
                controls.append(ft.Markdown(value=content, selectable=True,
                                            extension_set=ft.MarkdownExtensionSet.GITHUB_WEB))
        return controls

    def render_result(result: dict):
        result_title.value = result.get("title") or "الباترون"
        kind = {"garment": "قطعة كاملة", "motif": "موتيف / تفصيلة"}.get(result.get("piece_kind"), "")
        meta = [m for m in (kind and f"🧶 {kind}", f"المتبقي اليوم: {result.get('remaining', '—')}") if m]
        meta_text.value = "  •  ".join(meta)
        result_body.controls = (
            [section_heading(SCHEMATIC_HEADING)]
            + render_section(result.get("schematic_markdown", ""))
            + [ft.Divider(color=BORDER), section_heading(STEPS_HEADING)]
            + render_section(result.get("instructions_markdown", ""))
        )

    def set_busy(busy: bool, message: str = ""):
        state["busy"] = busy
        pick_button.disabled = busy
        analyze_button.disabled = busy or state["image"] is None
        progress_box.visible = busy
        progress_text.value = message

    def show_error(message: str):
        error_text.value = message
        error_card.visible = True
        result_card.visible = False

    async def on_pick(e):
        if state["busy"]:
            return
        files = await file_picker.pick_files(
            dialog_title="اختر صورة قطعة الكروشيه",
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

        result_card.visible = False
        error_card.visible = False
        try:
            if not data:
                raise UserFacingError("تعذر قراءة الصورة. جرّب اختيارها مرة أخرى.")
            img = load_image(data)
        except UserFacingError as ex:
            state["image"] = None
            image_box.content = placeholder
            file_info.visible = False
            show_error(str(ex))
            set_busy(False)
            page.update()
            return

        state["image"] = img
        image_box.content = ft.Image(
            src=image_to_jpeg_bytes(img, PREVIEW_SIDE), fit=ft.BoxFit.CONTAIN,
            border_radius=ft.BorderRadius.all(14), height=288,
        )
        file_info.value = f"📎 {picked.name}  •  {img.width}×{img.height}px"
        file_info.visible = True
        set_busy(False)
        page.update()

    async def on_analyze(e):
        if state["busy"] or state["image"] is None:
            return
        error_card.visible = False
        result_card.visible = False
        set_busy(True, "جاري تحليل الصورة واستخراج الباترون… (قد يستغرق حتى دقيقة)")
        page.update()
        try:
            jpeg = image_to_jpeg_bytes(state["image"], MAX_IMAGE_SIDE, quality=90)
            result = await api.analyze(jpeg, mode_group.value or "auto", notes_field.value or "")
            state["markdown"] = compose_markdown(result)
            render_result(result)
            result_card.visible = True
        except SessionExpired:
            set_busy(False)
            await logout("انتهت الجلسة، سجّل الدخول مرة أخرى.")
            return
        except UserFacingError as ex:
            show_error(str(ex))
        except Exception as ex:  # noqa: BLE001
            print(f"[patron] error: {ex!r}")
            show_error(f"حدث خطأ غير متوقع:\n{str(ex)[:300]}")
        finally:
            if state["busy"]:
                set_busy(False)
        page.update()

    async def on_copy(e):
        if state["markdown"]:
            await clipboard.set(state["markdown"])
            toast("تم نسخ الباترون ✅")

    async def logout(message: str = ""):
        api.token = None
        state.update(image=None, markdown="", user="")
        await prefs_remove(TOKEN_KEY)
        await prefs_remove(USER_KEY)
        image_box.content = placeholder
        file_info.visible = False
        result_card.visible = False
        error_card.visible = False
        show_login()
        if message:
            toast(message)

    async def on_logout(e):
        await logout("تم تسجيل الخروج.")

    pick_button.on_click = on_pick
    analyze_button.on_click = on_analyze
    copy_button.on_click = on_copy
    logout_button.on_click = on_logout

    def home_view():
        user_label.value = f"👤 {state['user']}" if state["user"] else ""
        return ft.Column(
            controls=[
                header("ارفع صورة قطعة كاملة أو موتيف، وسنستخرج لك المخطط التخطيطي وخطوات العمل بالتفصيل.",
                       trailing=logout_button),
                ft.Container(content=user_label, padding=ft.Padding.only(top=6)),
                body(upload_card, error_card, result_card),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=0,
        )

    def show_home():
        root.controls = [home_view()]
        page.update()

    def show_login():
        set_login_waiting(False)
        root.controls = [login_view]
        page.update()

    # ─────────── بدء التشغيل: استعادة الجلسة المحفوظة ───────────
    page.add(ft.SafeArea(content=root))
    saved = await prefs_get(TOKEN_KEY)
    if saved:
        api.token = saved
        state["user"] = await prefs_get(USER_KEY) or ""
        try:
            info = await api.me()
            state["user"] = info["user"].get("name", state["user"])
            show_home()
        except SessionExpired:
            await logout()
        except httpx.HTTPError:
            show_home()  # بدون إنترنت الآن — نعرض الشاشة ونتحقق عند التحليل
    else:
        show_login()


if __name__ == "__main__":
    ft.run(main)
