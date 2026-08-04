#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tunnel.py - จัดการ Cloudflare Tunnel สำหรับ Local Dashboard
  - quick tunnel:  cloudflared tunnel --url http://localhost:PORT  (ไม่ต้อง login)
  - named tunnel:  ติดตั้งครั้งแรกต้อง login ครั้งเดียว (cloudflared tunnel login)
                    แล้ว create + route DNS ฟรี -> ได้ลิงก์คงที่ (static)

รัน CLI:
  python tunnel.py start [--port 8765] [--named mytunnel]
  python tunnel.py stop
  python tunnel.py status
  python tunnel.py url
"""
import argparse, json, os, re, shutil, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8765
STATE = os.path.join(HERE, "tunnel_state.json")
CF_BIN = shutil.which("cloudflared") or os.path.join(HERE, "cloudflared.exe")
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
_tunnel_proc = None


def _find_cf():
    return shutil.which("cloudflared")


def install_cf():
    """พยายามติดตั้ง cloudflared (Windows) ผ่าน winget ก่อน ถ้าไม่ได้ให้ดาวน์โหลดตรง"""
    if _find_cf():
        return True, "มีอยู่แล้ว"
    # 1) winget
    try:
        r = subprocess.run(["winget", "install", "--id", "Cloudflare.cloudflared",
                            "-e", "--accept-package-agreements", "--accept-source-agreements"],
                           capture_output=True, text=True, timeout=180)
        if r.returncode == 0 and _find_cf():
            return True, "ติดตั้งผ่าน winget สำเร็จ"
    except Exception as e:
        pass
    # 2) ดาวน์โหลดตรง
    url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    dst = os.path.join(HERE, "cloudflared.exe")
    try:
        import urllib.request
        print("ดาวน์โหลด cloudflared...", file=sys.stderr)
        urllib.request.urlretrieve(url, dst)
        return True, f"ดาวน์โหลดมาแล้วที่ {dst}"
    except Exception as e:
        return False, f"ติดตั้งไม่สำเร็จ: {e}\nขอให้ติดตั้งเอง: winget install Cloudflare.cloudflared"


def _parse_url(proc):
    """รอจนได้ URL จาก stdout ของ cloudflared"""
    deadline = time.time() + 25
    buf = ""
    while time.time() < deadline:
        line = proc.stderr.readline() if proc.stderr else ""
        if not line:
            # try stdout
            line = proc.stdout.readline() if proc.stdout else ""
        if line:
            buf += line
            m = URL_RE.search(buf)
            if m:
                return m.group(0)
        if proc.poll() is not None:
            return None
    return None


def start_quick(port=PORT):
    global _tunnel_proc
    cf = _find_cf() or (install_cf() and _find_cf())
    if not cf:
        return None, "ไม่มี cloudflared"
    _tunnel_proc = subprocess.Popen(
        [cf, "tunnel", "--url", f"http://localhost:{port}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    url = _parse_url(_tunnel_proc)
    if url:
        json.dump({"url": url, "port": port, "mode": "quick",
                   "pid": _tunnel_proc.pid}, open(STATE, "w"))
        return url, "ok"
    return None, "ไม่ได้ URL (อาจไม่มีเน็ต หรือ cloudflared ค้าง)"


def start_named(name="hermesdash", port=PORT):
    """ต้อง login ครั้งเดียวก่อน: cloudflared tunnel login
    แล้วสคริปต์จะ create + run named tunnel -> ลิงก์คงที่ (static)
    ถ้าไม่มีโดเมนตัวเอง ให้ใช้ subdomain ฟรีของ cloudflare:
      cloudflared tunnel route dns <name> <name>.trycloudflare.com  (ได้ subdomain ฟรี)
    """
    cf = _find_cf() or (install_cf() and _find_cf())
    if not cf:
        return None, "ไม่มี cloudflared"
    cert = os.path.expanduser("~/.cloudflared/cert.pem")
    if not os.path.exists(cert):
        return None, ("ยังไม่ได้ login: รันคำสั่ง `cloudflared tunnel login` ใน terminal ครั้งเดียว "
                      "(เปิดเบราว์เซอร์เลือกโดเมน หรือข้ามได้สำหรับ subdomain ฟรี) แล้วมาสั่ง start named อีกที")
    # create ถ้ายังไม่มี
    try:
        lst = subprocess.run([cf, "tunnel", "list"], capture_output=True, text=True, timeout=30)
        if name not in lst.stdout:
            cr = subprocess.run([cf, "tunnel", "create", name], capture_output=True, text=True, timeout=60)
            # พยายาม route DNS subdomain ฟรี (trycloudflare) ถ้ามีโดเมนของตัวเองค่อยเปลี่ยน
            subprocess.run([cf, "tunnel", "route", "dns", name, f"{name}.trycloudflare.com"],
                           capture_output=True, text=True, timeout=60)
    except Exception as e:
        return None, f"tunnel list/create ผิดพลาด: {e}"
    _tunnel_proc = subprocess.Popen(
        [cf, "tunnel", "run", name],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    time.sleep(3)
    url = f"https://{name}.trycloudflare.com"
    json.dump({"url": url, "port": port, "mode": "named", "pid": _tunnel_proc.pid}, open(STATE, "w"))
    return url, "ok(named, static)"


def stop():
    global _tunnel_proc
    stopped = []
    # จาก state
    try:
        st = json.load(open(STATE))
        pid = st.get("pid")
        if pid:
            try:
                import signal
                os.kill(pid, signal.SIGTERM)
                stopped.append(str(pid))
            except Exception:
                pass
    except Exception:
        pass
    # ฆาประบวนการ cloudflared ทั้งหมดที่เหลือ
    try:
        subprocess.run(["taskkill", "/F", "/IM", "cloudflared.exe"],
                       capture_output=True, text=True, timeout=20)
        stopped.append("taskkill")
    except Exception:
        pass
    if _tunnel_proc and _tunnel_proc.poll() is None:
        _tunnel_proc.terminate()
    if os.path.exists(STATE):
        os.remove(STATE)
    return "stopped: " + ", ".join(stopped) if stopped else "ไม่มี tunnel ทำงาน"


def status():
    try:
        st = json.load(open(STATE))
        return st
    except Exception:
        return {"url": None, "running": False}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["start", "stop", "status", "url"])
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--named", default=None, help="ชื่อ named tunnel (ต้อง login แล้ว)")
    a = ap.parse_args()
    if a.action == "start":
        if a.named:
            url, msg = start_named(a.named, a.port)
        else:
            url, msg = start_quick(a.port)
        print(url or "", msg)
    elif a.action == "stop":
        print(stop())
    elif a.action == "status":
        print(json.dumps(status()))
    elif a.action == "url":
        print(status().get("url") or "")


if __name__ == "__main__":
    main()
