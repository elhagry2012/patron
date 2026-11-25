# -*- coding: utf-8 -*-
"""
إصلاح SSL: CERTIFICATE_VERIFY_FAILED على ويندوز — بدون تعطيل التحقق.

يبني ملف شهادات = شهادات certifi + الشهادات الموثوقة في مخزن ويندوز (ROOT و CA)،
وهذا يغطي شهادات برامج الحماية/البروكسي التي تفحص HTTPS. ثم يضبط متغيرات البيئة
SSL_CERT_FILE و REQUESTS_CA_BUNDLE للمستخدم الحالي (setx) لتستخدمها Python و flet build.

التشغيل:   python tools\\fix_ssl_certs.py
للتراجع:   python tools\\fix_ssl_certs.py --undo
"""

import os
import ssl
import subprocess
import sys
from pathlib import Path

import certifi

OUT = Path.home() / ".patron-certs" / "ca-bundle.pem"
VARS = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")


def windows_certs() -> list[str]:
    pems, seen = [], set()
    for store in ("ROOT", "CA"):
        for der, encoding, trust in ssl.enum_certificates(store):
            # x509_asn = شهادة؛ trust=True أو يحتوي serverAuth = صالحة لمواقع HTTPS
            if encoding != "x509_asn" or der in seen:
                continue
            if trust is not True and "1.3.6.1.5.5.7.3.1" not in trust:
                continue
            seen.add(der)
            pems.append(ssl.DER_cert_to_PEM_cert(der))
    return pems


def main() -> None:
    if sys.platform != "win32":
        sys.exit("This script is for Windows only.")

    if "--undo" in sys.argv:
        for var in VARS:
            subprocess.run(["reg", "delete", r"HKCU\Environment", "/v", var, "/f"], capture_output=True)
        print("Removed SSL_CERT_FILE / REQUESTS_CA_BUNDLE. Open a new terminal.")
        return

    extra = windows_certs()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(Path(certifi.where()).read_text(encoding="ascii") + "\n" + "".join(extra), encoding="ascii")

    # تحقق أن الملف صالح قبل اعتماده
    ctx = ssl.create_default_context(cafile=str(OUT))
    print(f"Bundle: {OUT}  (certifi + {len(extra)} Windows certs, {len(ctx.get_ca_certs())} loaded)")

    for var in VARS:
        subprocess.run(["setx", var, str(OUT)], check=True, capture_output=True)
    print("Set SSL_CERT_FILE and REQUESTS_CA_BUNDLE for your user. Open a NEW terminal to use them.")


if __name__ == "__main__":
    main()
