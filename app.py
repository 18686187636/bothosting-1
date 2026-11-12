#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, sys, time, json, random, requests, subprocess, ctypes
import urllib.request, urllib.parse, urllib.error
from datetime import datetime
from seleniumbase import SB

# ========== 环境变量 ==========
EMAIL         = os.environ.get("EMAIL") or ""
SESSION_TOKEN = os.environ.get("SESSION_TOKEN") or ""
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN") or ""
GH_TOKEN      = os.environ.get("GH_TOKEN") or ""
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN  = os.environ.get("TG_BOT_TOKEN") or ""

DC_TOKEN = ""
if DISCORD_TOKEN:
    _parts = DISCORD_TOKEN.split(",", 1)
    DC_TOKEN = _parts[-1].strip()

if not SESSION_TOKEN and not DC_TOKEN:
    print("ℹ️ 未配置 SESSION_TOKEN 和 DISCORD_TOKEN,脚本终止。")
    sys.exit(1)

COOKIES = {
    "session_token": SESSION_TOKEN,
    "login": "true",
    "theme": "system",
}
_LOGIN_METHOD = "SESSION_TOKEN"

# 站点路由（已从 /a/billings 迁移到 /dash/billings）
BILLINGS_URL = "https://bot-hosting.net/dash/billings"
HOME_URL     = "https://bot-hosting.net/"
LOGIN_URL    = "https://bot-hosting.net/login"

# ========== Cookie / Secret ==========
def get_cookie_info(sb, name):
    cookies = sb.get_cookies()
    for c in cookies:
        if c.get('name') == name:
            value = c.get('value')
            expiry_ts = c.get('expiry')
            expiry_dt = datetime.fromtimestamp(expiry_ts) if expiry_ts else None
            return value, expiry_dt
    return None, None

def should_update_cookie(new_value, old_value, expiry_dt, days_threshold=3):
    if new_value is None:
        return False
    if new_value != old_value:
        return True
    if expiry_dt:
        remaining = (expiry_dt - datetime.now()).total_seconds()
        if remaining < days_threshold * 24 * 3600:
            return True
    return False

def update_github_secret(secret_name, new_value):
    if not new_value:
        print(f"⚠️ 跳过更新 {secret_name}：新值为空")
        return False
    masked = new_value[:4] + "..." + new_value[-4:] if len(new_value) > 8 else "***"
    print(f"🔄 更新 Secret: {secret_name} (新值: {masked})")
    try:
        env = os.environ.copy()
        if GH_TOKEN:
            env["GH_TOKEN"] = GH_TOKEN
        proc = subprocess.run(
            ["gh", "secret", "set", secret_name, "--body", new_value],
            capture_output=True, text=True, timeout=30, check=False, env=env
        )
        if proc.returncode == 0:
            return True
        print(f"❌ 更新失败: {proc.stderr.strip()}")
        return False
    except Exception as e:
        print(f"❌ 异常: {e}")
        return False

# ========== Telegram 通知 ==========
def send_telegram_message(message: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️ Telegram 未配置，跳过通知")
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": message}, timeout=10)
        print("✅ Telegram 通知已发送")
    except Exception as e:
        print(f"❌ Telegram 发送失败: {e}")

def format_notification(status: str, extra: str = "", error: str = "", expiry_date: str = "") -> str:
    local_time = time.gmtime(time.time() + 8 * 3600)
    now = time.strftime("%Y-%m-%d %H:%M:%S", local_time)
    if '@' in EMAIL:
        name, domain = EMAIL.split('@', 1)
        if len(name) > 4:
            masked_email = f"{name[:2]}****{name[-2:]}@{domain}"
        else:
            masked_email = f"{name}@{domain}"
    else:
        masked_email = EMAIL[:2] + '****' if EMAIL else "未配置"

    lines = [
        "🇫🇮 Bot-hosting 续期通知",
        "",
        f"{status}",
        f"👤 登录账户: {masked_email}",
    ]
    if _LOGIN_METHOD != "SESSION_TOKEN":
        lines.append(f"🔐 登录方式: {_LOGIN_METHOD}")
    if expiry_date:
        lines.append(f"📅 到期时间: {expiry_date}")
    if extra:
        lines.append(extra)
    if error:
        lines.append(f"⚠️ 错误信息: {error}")
    lines.append(f"⏱️ 登录时间: {now}")
    return "\n".join(lines)

# ========== JS 执行工具 ==========
def js(sb, script, default=None, errors=None):
    try:
        result = sb.execute_script(script)
        return default if result is None else result
    except Exception as e:
        if errors is not None:
            errors.append(f"execute_script: {type(e).__name__}: {e}")
        try:
            result = sb.cdp.evaluate("(function(){ " + script + " })()")
            return default if result is None else result
        except Exception as e2:
            if errors is not None:
                errors.append(f"cdp.evaluate: {type(e2).__name__}: {e2}")
            return default

def diag_turnstile_dom(sb, errors=None):
    snap = js(sb, """
        var out = [];
        var ifr = document.querySelectorAll('iframe');
        out.push('top-doc iframes: ' + ifr.length);
        for (var i = 0; i < ifr.length; i++) {
            var r = ifr[i].getBoundingClientRect();
            out.push('iframe w=' + Math.round(r.width) + ' h=' + Math.round(r.height) + ' src=' + (ifr[i].src || '').slice(0, 90));
        }
        var ts = document.querySelectorAll('[class*="turnstile"], [id*="turnstile"], input[name="cf-turnstile-response"]');
        out.push('turnstile-ish elements: ' + ts.length);
        for (var j = 0; j < ts.length; j++) {
            var rr = ts[j].getBoundingClientRect();
            var cls = (typeof ts[j].className === 'string') ? ts[j].className : (ts[j].getAttribute('class') || '');
            out.push(' - ' + ts[j].tagName + ' class=' + cls + ' w=' + Math.round(rr.width) + ' h=' + Math.round(rr.height));
        }
        out.push('window.turnstile=' + (typeof window.turnstile) + ' readyState=' + document.readyState);
        return out.join('\\n');
    """, "")
    for line in str(snap).splitlines():
        print("   [diag] " + line)
    if errors:
        for err in errors:
            print("   [diag][exec-error] " + str(err)[:200])

TURNSTILE_TOKEN_JS = """
try { if (window.turnstile && window.turnstile.getResponse) {
        var t = window.turnstile.getResponse(); if (t) return t; } } catch (e) {}
var el = document.querySelector('input[name="cf-turnstile-response"]');
return el ? (el.value || '') : '';
"""

TURNSTILE_EXISTS_JS = """
return document.querySelector(
    'input[name="cf-turnstile-response"], iframe[src*="challenges.cloudflare.com"], .cf-turnstile'
) !== null;
"""

TS_COORDS_JS = """
function findWidget(root) {
    var els = root.querySelectorAll(
        'iframe[src*="challenges.cloudflare.com"], .cf-turnstile, [id*="cf-chl"]');
    for (var i = 0; i < els.length; i++) {
        var r = els[i].getBoundingClientRect();
        if (r.width > 50 && r.height > 20) return els[i];
    }
    var all = root.querySelectorAll('*');
    for (var j = 0; j < all.length; j++) {
        if (all[j].shadowRoot) {
            var f = findWidget(all[j].shadowRoot);
            if (f) return f;
        }
    }
    return null;
}
function boxFromTokenInput() {
    var el = document.querySelector('input[name="cf-turnstile-response"]');
    var cur = el ? el.parentElement : null;
    for (var k = 0; k < 6 && cur; k++) {
        var r = cur.getBoundingClientRect();
        if (r.width > 100 && r.width < 600 && r.height > 30 && r.height < 140) return cur;
        cur = cur.parentElement;
    }
    return null;
}
var hit = findWidget(document) || boxFromTokenInput();
if (!hit) return null;
var hr = hit.getBoundingClientRect();
return {cx: Math.round(hr.x + 30), cy: Math.round(hr.y + hr.height / 2)};
"""

def wait_turnstile_present(sb, timeout=12):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if js(sb, TURNSTILE_EXISTS_JS, False):
            return True
        time.sleep(1)
    return False

def _activate_window():
    if os.name == "nt":
        return
    for cls in ["chrome", "chromium", "Chromium", "Chrome", "google-chrome"]:
        try:
            r = subprocess.run(["xdotool", "search", "--onlyvisible", "--class", cls],
                               capture_output=True, text=True, timeout=3)
            wids = [w for w in r.stdout.strip().split("\n") if w.strip()]
            if wids:
                subprocess.run(["xdotool", "windowactivate", "--sync", wids[0]],
                               timeout=3, stderr=subprocess.DEVNULL)
                time.sleep(0.2)
                return
        except Exception:
            pass

def _windows_mouse_click(x: int, y: int):
    try:
        user32 = ctypes.windll.user32
        user32.SetCursorPos(int(x), int(y))
        time.sleep(0.08)
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(0.03)
        user32.mouse_event(0x0004, 0, 0, 0, 0)
    except Exception:
        pass

def _xdotool_click(x: int, y: int):
    if os.name == "nt":
        _windows_mouse_click(x, y)
        return
    _activate_window()
    try:
        subprocess.run(["xdotool", "mousemove", "--sync", str(x), str(y)],
                       timeout=3, stderr=subprocess.DEVNULL)
        time.sleep(0.15)
        subprocess.run(["xdotool", "click", "1"], timeout=2, stderr=subprocess.DEVNULL)
    except Exception:
        os.system(f"xdotool mousemove {x} {y} click 1 2>/dev/null")

def turnstile_token(sb) -> str:
    token = js(sb, TURNSTILE_TOKEN_JS, "")
    return token if isinstance(token, str) else ""

def _click_turnstile(sb, locate_timeout=10) -> bool:
    coords = None
    errors = []
    deadline = time.time() + locate_timeout
    while time.time() < deadline:
        coords = js(sb, TS_COORDS_JS, None, errors)
        if coords:
            break
        time.sleep(1)
    if not coords:
        print("⚠️ 无法定位 Turnstile 坐标, 打印现场诊断:")
        diag_turnstile_dom(sb, errors)
        return False
    try:
        wi = js(sb, "return '' + (window.screenX || 0) + '|' + (window.screenY || 0) + '|' +"
                    " window.outerHeight + '|' + window.innerHeight;", "")
        parts = str(wi).split("|")
        sx, sy = int(parts[0] or 0), int(parts[1] or 0)
        bar = int(parts[2] or 800) - int(parts[3] or 768)
        ax = int(coords["cx"]) + sx
        ay = int(coords["cy"]) + sy + bar
        print(f"🖱️ 底层点击 Turnstile ({ax}, {ay})")
        _xdotool_click(ax, ay)
        time.sleep(3)
        return True
    except Exception as e:
        print(f"⚠️ 底层点击失败: {e}")
        return False

def click_turnstile(sb) -> bool:
    if _click_turnstile(sb):
        return True
    coords = js(sb, TS_COORDS_JS)
    if coords:
        try:
            if hasattr(sb, "cdp") and hasattr(sb.cdp, "gui_click_x_y"):
                print(f"🖱️ 坐标点击 Turnstile ({coords['cx']},{coords['cy']})")
                sb.cdp.gui_click_x_y(int(coords["cx"]), int(coords["cy"]))
                return True
        except Exception as e:
            print(f"⚠️ 坐标点击失败: {e}")
    try:
        if hasattr(sb, "cdp") and hasattr(sb.cdp, "click_captcha"):
            print("🖱️ 兜底调用 cdp.click_captcha ...")
            sb.cdp.click_captcha()
            return True
        print("⚠️ 当前 SeleniumBase 版本无 sb.cdp.click_captcha")
    except Exception as e:
        print(f"⚠️ click_captcha 出错: {e}")
    return False

def solve_turnstile(sb, timeout=90, tag="Turnstile", first_wait=8) -> bool:
    if turnstile_token(sb):
        return True
    deadline = time.time() + timeout
    next_click = time.time() + first_wait
    clicks = 0
    while time.time() < deadline:
        if turnstile_token(sb):
            print(f"✅ {tag}已通过(已获取 token)")
            return True
        if time.time() >= next_click:
            clicks += 1
            print(f"🖱️ 第 {clicks} 次点击 Turnstile 验证框...")
            click_turnstile(sb)
            next_click = time.time() + 10 + random.uniform(0, 3)
        time.sleep(2)
    ok = bool(turnstile_token(sb))
    print(f"{'✅' if ok else '❌'} {tag}{'已通过' if ok else '未通过'}")
    return ok

def handle_turnstile(sb) -> bool:
    print("🛡️ 处理 Turnstile 验证...")
    return solve_turnstile(sb, timeout=90, tag="Turnstile", first_wait=8)

# ========== 网络 / 时间 ==========
def get_current_ip(proxy_server: str = "") -> str:
    proxies = None
    if proxy_server:
        proxies = {"http": proxy_server, "https": proxy_server}
    response = requests.get("https://api.ip.sb/ip", proxies=proxies, timeout=15)
    response.raise_for_status()
    return response.text.strip()

def format_countdown(countdown_str: str) -> str:
    try:
        h, m, _ = countdown_str.split(':')
        h, m = int(h), int(m)
        if h > 0:
            return f"{h}h{m}min"
        return f"{m}min"
    except Exception:
        return countdown_str

# ========== 日期提取 ==========
DATE_RE = r"(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}|\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})"

def normalize_date(date_str: str) -> str:
    parts = date_str.split('/')
    if len(parts) == 3:
        if len(parts[2]) == 4:   # MM/DD/YYYY
            return f"{parts[2]}/{int(parts[0]):02d}/{int(parts[1]):02d}"
        if len(parts[0]) == 4:   # YYYY/MM/DD
            return f"{parts[0]}/{int(parts[1]):02d}/{int(parts[2]):02d}"
    return date_str

def extract_expiry_date(page_source: str) -> str:
    patterns = [
        r"[Ee]xpires\s*[:\-]?\s*" + DATE_RE,
        DATE_RE,
    ]
    for pattern in patterns:
        match = re.search(pattern, page_source)
        if match:
            return normalize_date(match.group(1))
    return None

def get_expiry_from_dom(sb) -> str:
    for selector in ['span.st-meta', '.st-meta', '[class*="st-meta"]']:
        try:
            if sb.is_element_present(selector):
                match = re.search(DATE_RE, sb.get_text(selector))
                if match:
                    return normalize_date(match.group(1))
        except Exception:
            pass
    return None

# ========== Discord OAuth ==========
DISCORD_CLIENT_ID   = "884382422530158623"
OAUTH_REDIRECT_URI  = "https://bot-hosting.net/login"
OAUTH_SCOPE         = "identify email guilds"
DISCORD_API         = "https://discord.com/api/v9/oauth2/authorize"
DISCORD_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)
STATE_RE = re.compile(r"[?&]state=([^&]+)")

def capture_discord_state(sb) -> str:
    print("🔎 获取 Discord OAuth state...")
    sb.uc_open_with_reconnect("https://bot-hosting.net/login/discord", reconnect_time=4)
    time.sleep(2)
    url = sb.get_current_url()
    if "discord.com" not in url:
        print(f"⚠️ 未跳转到 Discord 相关页面，当前 URL：{url}")
        return ""
    m = STATE_RE.search(url)
    if not m:
        print(f"❌ 未能从 URL 中解析出 state，当前 URL：{url}")
        return ""
    state = urllib.parse.unquote(m.group(1))
    print(f"✅ 已捕获 state（当前落地页：{urllib.parse.urlparse(url).path}）")
    return state

def discord_authorize(state: str) -> str:
    query = urllib.parse.urlencode({
        "client_id": DISCORD_CLIENT_ID, "response_type": "code",
        "redirect_uri": OAUTH_REDIRECT_URI, "scope": OAUTH_SCOPE, "state": state,
    })
    authorize_url = f"{DISCORD_API}?{query}"
    referer = (
        "https://discord.com/oauth2/authorize?" +
        urllib.parse.urlencode({
            "client_id": DISCORD_CLIENT_ID, "redirect_uri": OAUTH_REDIRECT_URI,
            "response_type": "code", "scope": OAUTH_SCOPE, "state": state,
        })
    )
    headers = {
        "accept": "*/*", "authorization": DC_TOKEN, "content-type": "application/json",
        "origin": "https://discord.com", "referer": referer,
        "user-agent": DISCORD_UA, "x-discord-locale": "zh-CN",
    }
    body = json.dumps({
        "permissions": "0", "authorize": True, "integration_type": 0,
        "location_context": {"guild_id": "10000", "channel_id": "10000", "channel_type": 10000},
    })
    proxies = None
    if os.environ.get("IS_PROXY", "false").lower() == "true":
        _p = os.environ.get("PROXY_SERVER", "").strip() or "http://127.0.0.1:1080"
        proxies = {"http": _p, "https": _p}
    try:
        resp = requests.post(authorize_url, headers=headers, data=body, proxies=proxies, timeout=20)
        if resp.status_code != 200:
            print(f"❌ Discord OAuth2 授权失败: HTTP {resp.status_code} - {resp.text[:300]}")
            return ""
        resp_data = resp.json()
    except Exception as e:
        print(f"❌ Discord OAuth2 授权异常: {e}")
        return ""
    location = resp_data.get("location", "")
    if not location:
        print(f"❌ 授权响应中未找到 location 字段: {resp_data}")
        return ""
    print(f"✅ 拿到回调 URL: {re.sub(r'code=[^&]+', 'code=***', location)}")
    return location

def do_discord_login(sb) -> bool:
    print("\n🔑 通过 Discord Token 登录...")
    state = capture_discord_state(sb)
    if not state:
        sb.save_screenshot("login_no_state.png")
        return False
    location = discord_authorize(state)
    if not location:
        return False
    print("↩️ 携带授权码打开回调链接...")
    sb.uc_open_with_reconnect(location, reconnect_time=4)
    time.sleep(3)
    url = sb.get_current_url()
    if "/error/banned" in url:
        print("🚫 账号已被封禁")
        sb.save_screenshot("login_banned.png")
        return False
    if "bot-hosting.net" not in url:
        print(f"❌ 回调后未跳转至 bot-hosting.net，当前 URL：{url}")
        sb.save_screenshot("login_no_redirect.png")
        return False
    try:
        if "fraud" in sb.get_text("body").lower():
            print("🚫 触发风控（fraud attempt），可能是 IP 被拦截")
            sb.save_screenshot("login_fraud.png")
            return False
    except Exception:
        pass
    for _ in range(30):
        url = sb.get_current_url()
        path = urllib.parse.urlparse(url).path
        if "bot-hosting.net" in url and path != "/login" and not path.startswith("/login/discord"):
            print(f"✅ Discord OAuth 登录成功！当前页面：{url}")
            return True
        time.sleep(0.5)
    print(f"❌ 登录超时或未跳转成功，最终停留在：{url}")
    sb.save_screenshot("login_timeout.png")
    return False

# ========== 主流程 ==========
def main():
    print("#" * 25)
    print("   Bot-hosting 自动续期")
    print("#" * 25)

    IS_PROXY = os.environ.get("IS_PROXY", "false").lower() == "true"
    PROXY_SERVER = os.environ.get("PROXY_SERVER", "").strip() or "http://127.0.0.1:1080"
    HEADLESS = os.environ.get("HEADLESS", "false").lower() == "true"

    if HEADLESS:
        # Turnstile 需要真实鼠标点击，无头模式下几乎一定失败，直接提示
        print("⚠️ HEADLESS=true，Turnstile 可能无法通过；建议设置 HEADLESS=false 配合 xvfb-run 使用")

    sb_kwargs = {"uc": True, "headless": HEADLESS, "locale_code": "en-US"}
    if IS_PROXY:
        print(f"🔗 挂载代理: {PROXY_SERVER}")
        sb_kwargs["proxy"] = PROXY_SERVER
    else:
        print("🍭 未使用代理，直连访问")

    global _LOGIN_METHOD

    with SB(**sb_kwargs) as sb:
        try:
            print(f"📍 当前出口IP: {get_current_ip(PROXY_SERVER if IS_PROXY else '')}")
        except Exception as e:
            print(f"⚠️ 获取出口 IP 失败: {e}")

        login_ok = False

        # ---------- 方式1: SESSION_TOKEN Cookie 登录 ----------
        if SESSION_TOKEN:
            print("🚀 启动浏览器...")
            sb.open(HOME_URL)
            sb.wait_for_ready_state_complete()
            sb.sleep(2)

            print("📝 注入 Cookie...")
            for name, value in COOKIES.items():
                if value:
                    sb.add_cookie({"name": name, "value": value, "domain": "bot-hosting.net"})

            print(f"🌐 访问 {BILLINGS_URL} ...")
            sb.open(BILLINGS_URL)
            sb.wait_for_ready_state_complete()
            sb.sleep(3)
            current_url = sb.get_current_url()
            print(f"📝 当前URL: {current_url}, Title: {sb.get_title()}")

            # 站点路由从 /a/billings 迁移到 /dash/billings，用包含 "billings" 兼容两条路径
            if "billings" in current_url and "/login" not in current_url and "error=" not in current_url:
                login_ok = True
                print("✅ SESSION_TOKEN 登录成功, 当前已到达账单页")
            else:
                print(f"❌ SESSION_TOKEN 登录失败，当前URL: {current_url}")

        # ---------- 方式2: Discord OAuth 登录（备用） ----------
        if not login_ok and DC_TOKEN:
            _LOGIN_METHOD = "Discord Token"
            print("\n🔄 SESSION_TOKEN 登录失败或未配置，尝试 Discord OAuth 登录...")
            if do_discord_login(sb):
                print(f"🌐 访问 {BILLINGS_URL} ...")
                sb.open(BILLINGS_URL)
                sb.wait_for_ready_state_complete()
                sb.sleep(3)
                current_url = sb.get_current_url()
                print(f"📝 当前URL: {current_url}, Title: {sb.get_title()}")

                if "billings" in current_url and "/login" not in current_url:
                    login_ok = True
                    print("✅ Discord OAuth 登录成功, 当前已到达账单页")
                else:
                    print(f"❌ Discord OAuth 登录后仍未到达账单页，当前URL: {current_url}")
            else:
                print("❌ Discord OAuth 登录失败")

        if not login_ok:
            error_msg = "Cookie 已失效或页面异常"
            if not SESSION_TOKEN and DC_TOKEN:
                error_msg = "Discord OAuth 登录失败"
            elif SESSION_TOKEN and DC_TOKEN:
                error_msg = "SESSION_TOKEN 和 Discord OAuth 均失败"
            send_telegram_message(format_notification("❌ 登录失败", error=error_msg))
            return

        if _LOGIN_METHOD == "Discord Token":
            print("ℹ️ 本次使用 Discord OAuth 登录，新的 SESSION_TOKEN 将自动更新到 Secrets")

        # ---------- 提取当前到期日期 ----------
        sb.sleep(3)
        try:
            page_source = sb.get_page_text() + "\n" + sb.get_page_source()
        except Exception:
            page_source = sb.get_page_source()
        current_expiry = get_expiry_from_dom(sb) or extract_expiry_date(page_source)
        if current_expiry:
            print(f"📅 当前到期日期: {current_expiry}")
        else:
            print("⚠️ 未能提取当前到期日期")

        # ---------- 寻找外部续期按钮 ----------
        outer_renew_selector = None
        countdown_text = None
        possible_selectors = [
            'type:button("Renew free plan")',
            'button:contains("Renew free plan")',
            '[class*="btn btn-primary btn-sm"]',
        ]
        for selector in possible_selectors:
            try:
                if sb.is_element_visible(selector):
                    button_text = sb.get_text(selector)
                    # "Renew in HH:MM:SS" / "Renewal cooldown HH:MM:SS" 都表示冷却中
                    match = re.search(r"(?:Renew in|Renewal cooldown)\s+(\d{1,2}:\d{2}:\d{2})",
                                      button_text, re.I)
                    if match:
                        countdown_text = match.group(1)
                        print(f"⏳ 续期冷却中, 未到续期时间: '{button_text}'")
                        break
                    elif "Renew" in button_text:
                        outer_renew_selector = selector
                        print(f"✅ 续期按钮可用: '{button_text}'")
                        break
            except Exception:
                pass

        # ---------- 点击外部续期按钮，等待弹窗 ----------
        if outer_renew_selector:
            print("🔄 点击外部续期按钮，等待验证窗口...")
            try:
                sb.sleep(2)
                sb.click(outer_renew_selector)
                sb.sleep(15)  # 等待模态框加载
            except Exception as e:
                print(f"❌ 点击外部按钮失败: {e}")
                send_telegram_message(format_notification("❌ 续期失败", error="点击外部续期按钮出错"))
                return

            # 处理弹窗中的 Turnstile
            turnstile_passed = True
            if wait_turnstile_present(sb):
                turnstile_passed = handle_turnstile(sb)
                if not turnstile_passed:
                    print("❌ Turnstile 验证失败，尝试兜底点击...")
                    try:
                        if hasattr(sb, "cdp") and hasattr(sb.cdp, "click_captcha"):
                            sb.cdp.click_captcha()
                            sb.sleep(4)
                            turnstile_passed = bool(turnstile_token(sb))
                    except Exception as e:
                        print(f"⚠️ 兜底点击失败: {e}")
            else:
                print("ℹ️ 未检测到 Turnstile")

            if not turnstile_passed:
                print("❌ Turnstile 验证最终未通过，脚本退出")
                send_telegram_message(format_notification("❌ 续期失败", error="Turnstile 验证未通过"))
                return

            # 点击弹窗内续期按钮
            print("⏳ 等待续期按钮可用并点击...")
            time.sleep(5)

            try:
                sb.click('div[role="dialog"] button.btn-primary', timeout=8)
                print("✅ 已点击续期按钮")
            except Exception as e:
                print(f"续期按钮点击失败: {e}")

            print("⏳ 等待新的过期时间...")
            sb.sleep(6)

            # 提取新的到期日期和倒计时
            try:
                new_page_text = sb.get_page_text() + "\n" + sb.get_page_source()
            except Exception:
                new_page_text = sb.get_page_source()
            new_expiry = get_expiry_from_dom(sb) or extract_expiry_date(new_page_text)
            new_match = re.search(r"(?:Renewal cooldown|Renew in)\s+(\d{1,2}:\d{2}:\d{2})",
                                  new_page_text, re.I)

            if new_match:
                new_countdown = new_match.group(1)
                print(f"✅ 续期成功！新的倒计时: {new_countdown}")
                if new_expiry:
                    print(f"📅 新的到期日期: {new_expiry}")
                send_telegram_message(format_notification(
                    "✅ 续期成功",
                    extra=f"⏱️ 可续期时间: {format_countdown(new_countdown)}后",
                    expiry_date=new_expiry or "（未获取到）"))
            elif new_expiry and new_expiry != current_expiry:
                print(f"✅ 续期成功，到期日期已更新为: {new_expiry}")
                send_telegram_message(format_notification(
                    "✅ 续期成功", extra="到期日期已更新", expiry_date=new_expiry))
            else:
                print("⚠️ 续期结果未知，到期日期未变化，请手动检查")
                send_telegram_message(format_notification(
                    "⚠️ 续期可能未成功", extra="请登录后台检查",
                    expiry_date=current_expiry or "（未获取到）"))
        else:
            if countdown_text:
                friendly = format_countdown(countdown_text)
                print(f"⏳ 未到续期时间，倒计时: {countdown_text} ({friendly})")
                send_telegram_message(format_notification(
                    "⏳ 未到续期时间",
                    extra=f"⏱️ 可续期时间: {friendly}后",
                    expiry_date=current_expiry or "（未获取到）"))
            else:
                print("ℹ️ 未找到续期按钮或倒计时，状态未知")
                send_telegram_message(format_notification(
                    "ℹ️ 无需续期", extra="当前状态未知，请手动检查",
                    expiry_date=current_expiry or "（未获取到）"))

        # ---------- 更新 SESSION_TOKEN ----------
        print("🔄 检查 SESSION_TOKEN 是否需要更新")
        new_token, token_expiry = get_cookie_info(sb, "session_token")
        old_token = SESSION_TOKEN
        if should_update_cookie(new_token, old_token, token_expiry):
            print("🔄 SESSION_TOKEN 需要更新")
            if GH_TOKEN:
                if update_github_secret("SESSION_TOKEN", new_token):
                    print("✅ SESSION_TOKEN 更新成功")
                else:
                    print("⚠️ 更新失败，请检查 GH_TOKEN 权限")
            else:
                print("⚠️ 未设置 GH_TOKEN，无法自动更新")
                print(f"📋 请手动设置 SESSION_TOKEN = {new_token[:4]}...{new_token[-4:]}")
        else:
            print("✅ SESSION_TOKEN 无需更新")

        print("🏁 脚本执行完毕")


if __name__ == "__main__":
    main()
