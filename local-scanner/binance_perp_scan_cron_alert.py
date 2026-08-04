#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
binance_perp_scan_cron_alert.py
  Cron wrapper: รัน scanner_v2 -> หา setup ใหม่ vs checkpoint ก่อนหน้า
  -> ถ้ามีใหม่ เรียก alert_hook (Telegram + Discord + เสียงในเครื่อง)
  ปรับปรุงจาก binance_perp_scan_cron.py เดิม โดยเพิ่ม alert (Offer 3)
"""
import os, subprocess, sys, json

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.normpath(os.path.join(HERE, "..", "AppData", "Local", "hermes", "scripts"))
SCANNER = os.path.join(SCRIPTS, "scanner_v2.py")
CKPT = os.path.join(SCRIPTS, "scanner_v2_checkpoint.json")
CKPT_PREV = os.path.join(SCRIPTS, "scanner_v2_checkpoint_prev.json")
ALERT = os.path.join(SCRIPTS, "alert_hook.py")
UV = os.path.join(os.environ["LOCALAPPDATA"], "hermes", "bin", "uv.exe")


def run_scan():
    env = os.environ.copy()
    env["FULL"] = "0"
    env["CRON"] = "1"
    r = subprocess.run([UV, "run", "python", SCANNER],
                       capture_output=True, text=True, env=env, cwd=SCRIPTS)
    return r.returncode, r.stdout, r.stderr


def load_ckpt(p):
    try:
        return json.load(open(p))
    except Exception:
        return {"base": {}, "dip": {}}


def new_symbols(cur, prev):
    out = []
    for kind in ("base", "dip"):
        cb = cur.get(kind, {})
        pb = prev.get(kind, {})
        for s in cb:
            if s not in pb:
                d = cb[s]
                out.append((kind, s, d))
    return out


def fmt(nk):
    L = [f"🔔 สแกนเจอ setup ใหม่ {len(nk)} ตัว:"]
    for kind, s, d in nk:
        pct = round((d.get("tp1", 0) / d.get("entry", 1) - 1) * 100, 1)
        L.append(f"  [{kind.upper()}] {s} entry={d.get('entry')} SL={d.get('sl')} "
                 f"TP1={d.get('tp1')} (+{pct}%) R:R={d.get('rr1')}")
    return "\n".join(L)


def main():
    rc, out, err = run_scan()
    if rc != 0:
        sys.stderr.write(err)
        raise SystemExit(rc)
    cur = load_ckpt(CKPT)
    prev = load_ckpt(CKPT_PREV)
    nk = new_symbols(cur, prev)
    # rotate checkpoint: เก็บของเก่าเป็น prev
    try:
        import shutil
        shutil.copy(CKPT, CKPT_PREV)
    except Exception:
        pass
    if nk:
        msg = fmt(nk)
        # ส่ง alert
        ar = subprocess.run([sys.executable, ALERT, msg],
                            capture_output=True, text=True, cwd=SCRIPTS)
        sys.stdout.write(msg + "\n--alert: " + ar.stdout.strip() + "\n")
    else:
        sys.stdout.write("ไม่มี setup ใหม่รอบนี้\n")


if __name__ == "__main__":
    main()
