#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
alert_hook.py - ส่ง Alert ตอนเงื่อนไขดัง (เจอ setup ใหม่ / ราคาแตะ entry / breakout)
  D1) Telegram   (ถ้ามี TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)
  D2) Discord     (ถ้ามี DISCORD_WEBHOOK env) - แนะนำแทน LINE (LINE Notify ปิด 2026)
  D3) เสียงในเครื่อง (Windows: winsound)

รัน CLI:
  python alert_hook.py "ข้อความ alert" [--sound] [--silent]
import: from alert_hook import alert
  alert("ข้อความ", sound=True)
"""
import os, sys, argparse, subprocess, urllib.request, urllib.parse, json

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")
DISCORD_WH = os.environ.get("DISCORD_WEBHOOK") or os.environ.get("DISCORD_WEBHOOK_URL")


def _tg(text):
    if not (TG_TOKEN and TG_CHAT):
        return False, "no telegram creds"
    try:
        data = urllib.parse.urlencode({"chat_id": TG_CHAT, "text": text,
                                       "parse_mode": "Markdown"}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=30) as r:
            j = json.loads(r.read().decode())
        return j.get("ok", False), "sent" if j.get("ok") else str(j)
    except Exception as e:
        return False, f"tg err: {e}"


def _discord(text):
    if not DISCORD_WH:
        return False, "no discord webhook"
    try:
        # Discord รับ JSON {content:...}
        payload = json.dumps({"content": text}).encode()
        req = urllib.request.Request(
            DISCORD_WH, data=payload,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status in (200, 204), str(r.status)
    except Exception as e:
        return False, f"discord err: {e}"


def _sound():
    """เสียงเตือนในเครื่อง (ไม่พึ่ง network)"""
    try:
        if sys.platform.startswith("win"):
            import winsound
            # เสียงสามจังหวะเตือน
            winsound.Beep(880, 150); winsound.Beep(1108, 150); winsound.Beep(880, 200)
            return True, "beep"
        else:
            subprocess.run(["afplay", "/System/Library/Sounds/Glass.aiff"],
                           capture_output=True, timeout=5)
            return True, "beep"
    except Exception as e:
        return False, f"sound err: {e}"


def alert(text, sound=True, silent=False):
    """คืน dict สรุปการส่งแต่ละช่อง"""
    res = {}
    if not silent:
        ok, msg = _tg(text)
        res["telegram"] = f"{ok}:{msg}"
        ok, msg = _discord(text)
        res["discord"] = f"{ok}:{msg}"
    if sound:
        ok, msg = _sound()
        res["sound"] = f"{ok}:{msg}"
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("text", help="ข้อความ alert")
    ap.add_argument("--sound", action="store_true", default=True)
    ap.add_argument("--silent", action="store_true", help="ไม่ส่ง Telegram/Discord")
    a = ap.parse_args()
    r = alert(a.text, sound=a.sound, silent=a.silent)
    print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
