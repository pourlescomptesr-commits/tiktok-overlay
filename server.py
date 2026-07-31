import os, time, json, subprocess, sys, re, urllib.request
from flask import Flask, request, jsonify, Response, send_from_directory, redirect, render_template_string

try:
    from gevent.pywsgi import WSGIServer
    HAS_GEVENT = True
except Exception:
    HAS_GEVENT = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=os.path.join(BASE_DIR, 'public'), static_url_path='')

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Grenouille123")
KEYS_FILE = os.path.join(BASE_DIR, 'keys.json')

def load_keys():
    if os.path.exists(KEYS_FILE):
        try:
            with open(KEYS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "DEMO1234": {"label": "Compte Démo Gratuit", "created": "2026-07-30"},
        "KEY-5792224B": {"label": "Client #1", "created": "2026-07-30"},
        "KEY-AA860585": {"label": "Client #2", "created": "2026-07-30"},
        "KEY-718019E8": {"label": "Client #3", "created": "2026-07-30"}
    }

def save_keys(keys):
    try:
        with open(KEYS_FILE, 'w', encoding='utf-8') as f:
            json.dump(keys, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[Save Keys Error] {e}", flush=True)

VALID_KEYS = load_keys()

def get_key_strict():
    data = request.get_json(silent=True) or {}
    k = request.args.get('key') or data.get('key') or request.headers.get('X-Streamer-Key')
    if k:
        k = k.strip().upper()
        if k in VALID_KEYS:
            return k
        if k.startswith("KEY-"):
            VALID_KEYS[k] = {"label": f"Client {k[-4:]}", "created": time.strftime("%Y-%m-%d")}
            save_keys(VALID_KEYS)
            return k
    return None

STORES = {}

def get_store(key):
    if not key or key not in VALID_KEYS:
        return None
    if key not in STORES:
        STORES[key] = {
            "key": key,
            "timer_dur": 60,
            "snipe_dur": 10,
            "timer_rem": 60,
            "snipe_rem": 10,
            "timer_on": False,
            "snipe_on": False,
            "last_t": time.time(),
            "min_bid_enabled": False,
            "min_bid_val": 1,
            "vouches_val": "100 VOUCHES",
            "color": "#ffd700",
            "theme": "hypercode",
            "tiktok": "off",
            "tiktok_user": "",
            "cf_url": "",
            "proc": None,
            "players": [],
            "subs": []
        }
    return STORES[key]

def fetch_tiktok_avatar(u):
    if not u: return None
    u = u.strip().lstrip('@')
    url = f"https://www.tiktok.com/@{u}"
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9'
    })
    try:
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
            m1 = re.search(r'avatarLarger":"([^"]+)"', html)
            m2 = re.search(r'og:image" content="([^"]+)"', html)
            m3 = re.search(r'avatarMedium":"([^"]+)"', html)
            m = m1 or m2 or m3
            if m:
                return m.group(1).replace(r'\u002F', '/').replace(r'\/', '/')
    except Exception as e:
        print(f"[Fetch Avatar Error @{u}] {e}", flush=True)
    return f"https://ui-avatars.com/api/?name={u}&background=ffd700&color=000&font-size=0.55&bold=true"

def update_timer(s):
    now = time.time()
    elapsed = now - s["last_t"]
    s["last_t"] = now

    if s["snipe_on"]:
        s["snipe_rem"] -= elapsed
        if s["snipe_rem"] <= 0:
            s["snipe_rem"] = 0
            # Auto-relaunch Snipe Delay if TIE occurs at timer end!
            top = sorted(s["players"], key=lambda x: x["coins"], reverse=True)[:2]
            if len(top) >= 2 and top[0]["coins"] == top[1]["coins"] and top[0]["coins"] > 0:
                s["snipe_on"] = True
                s["snipe_rem"] = s["snipe_dur"] if s["snipe_dur"] > 0 else 10
            else:
                s["snipe_on"] = False
                s["timer_on"] = False
    elif s["timer_on"]:
        s["timer_rem"] -= elapsed
        if s["timer_rem"] <= 0:
            s["timer_rem"] = 0
            top = sorted(s["players"], key=lambda x: x["coins"], reverse=True)[:2]
            if s["snipe_dur"] > 0 or (len(top) >= 2 and top[0]["coins"] == top[1]["coins"] and top[0]["coins"] > 0):
                s["snipe_on"] = True
                s["snipe_rem"] = s["snipe_dur"] if s["snipe_dur"] > 0 else 10
            else:
                s["timer_on"] = False

def notify(s):
    payload = json.dumps(get_public_state(s))
    msg = f"data: {payload}\n\n"
    dead = []
    for q in s["subs"]:
        try:
            q.append(msg)
        except Exception:
            dead.append(q)
    for d in dead:
        if d in s["subs"]:
            s["subs"].remove(d)

def get_public_state(s):
    update_timer(s)
    return {
        "key": s["key"],
        "timer_dur": s["timer_dur"],
        "snipe_dur": s["snipe_dur"],
        "timer_rem": max(0, round(s["timer_rem"], 1)),
        "snipe_rem": max(0, round(s["snipe_rem"], 1)),
        "timer_on": s["timer_on"],
        "snipe_on": s["snipe_on"],
        "min_bid_enabled": s["min_bid_enabled"],
        "min_bid_val": s["min_bid_val"],
        "vouches_val": s.get("vouches_val", "100 VOUCHES"),
        "color": s["color"],
        "theme": s.get("theme", "hypercode"),
        "tiktok": s["tiktok"],
        "tiktok_user": s["tiktok_user"],
        "cf_url": s["cf_url"],
        "players": sorted(s["players"], key=lambda x: x["coins"], reverse=True)
    }

@app.after_request
def add_no_cache_headers(response):
    if request.path in ['/', '/panel', '/overlay', '/admin']:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

@app.route('/')
def route_root():
    k = get_key_strict()
    if k:
        return redirect(f'/panel?key={k}')
    return send_from_directory(app.static_folder, 'login.html')

@app.route('/panel')
def route_panel():
    k = get_key_strict()
    if not k:
        return redirect('/')
    return send_from_directory(app.static_folder, 'panel.html')

@app.route('/overlay')
def route_overlay():
    k = get_key_strict()
    if not k:
        return "Overlay invalide: Clé manquante", 403
    return send_from_directory(app.static_folder, 'overlay.html')

@app.route('/admin')
def route_admin():
    return send_from_directory(app.static_folder, 'admin.html')

@app.route('/api/key/verify', methods=['POST'])
def api_key_verify():
    data = request.get_json(silent=True) or {}
    k = (data.get('key') or '').strip().upper()
    if k in VALID_KEYS or k.startswith("KEY-"):
        if k not in VALID_KEYS:
            VALID_KEYS[k] = {"label": f"Client {k[-4:]}", "created": time.strftime("%Y-%m-%d")}
            save_keys(VALID_KEYS)
        return jsonify(ok=True, valid=True, label=VALID_KEYS[k].get('label', 'Streamer'))
    return jsonify(ok=False, valid=False, error="Clé invalide ou expirée"), 403

@app.route('/api/state')
def api_state():
    k = get_key_strict()
    if not k:
        return jsonify(error="Clé invalide ou manquante"), 403
    s = get_store(k)
    return jsonify(get_public_state(s))

@app.route('/events')
def route_events():
    k = get_key_strict()
    if not k:
        return "Clé manquante ou invalide", 403

    s = get_store(k)
    q = []
    s["subs"].append(q)

    def gen():
        try:
            q.append(f"data: {json.dumps(get_public_state(s))}\n\n")
            while True:
                if q:
                    msg = q.pop(0)
                    yield msg
                else:
                    time.sleep(0.15)
                    update_timer(s)
                    q.append(f"data: {json.dumps(get_public_state(s))}\n\n")
        except GeneratorExit:
            if q in s["subs"]:
                s["subs"].remove(q)

    return Response(gen(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',
        'Connection': 'keep-alive'
    })

@app.route('/api/timer', methods=['POST'])
def api_timer():
    k = get_key_strict()
    if not k: return jsonify(error="Clé invalide"), 403
    s = get_store(k)

    data = request.get_json(silent=True) or {}
    a = data.get('action')
    dur = data.get('duration')
    snipe = data.get('snipe_delay')

    if dur is not None:
        try:
            d = max(5, int(dur))
            s["timer_dur"] = d
            if not s["timer_on"] and not s["snipe_on"]:
                s["timer_rem"] = d
        except Exception: pass

    if snipe is not None:
        try:
            s["snipe_dur"] = max(0, int(snipe))
            if not s["snipe_on"]:
                s["snipe_rem"] = s["snipe_dur"]
        except Exception: pass

    s["last_t"] = time.time()

    if a == 'start':
        s["timer_on"] = True
        s["snipe_on"] = False
        if s["timer_rem"] <= 0: s["timer_rem"] = s["timer_dur"]
    elif a == 'pause':
        s["timer_on"] = False
        s["snipe_on"] = False
    elif a == 'reset':
        s["timer_on"] = False
        s["snipe_on"] = False
        s["timer_rem"] = s["timer_dur"]
        s["snipe_rem"] = s["snipe_dur"]
    elif a == 'set':
        if not s["timer_on"] and not s["snipe_on"]:
            s["timer_rem"] = s["timer_dur"]
    elif a == 'add_time':
        if s["snipe_on"]:
            s["snipe_rem"] = s["snipe_dur"]

    notify(s)
    return jsonify(ok=True, state=get_public_state(s))

@app.route('/api/config', methods=['POST'])
@app.route('/api/color', methods=['POST'])
def api_config():
    k = get_key_strict()
    if not k: return jsonify(error="Clé invalide"), 403
    s = get_store(k)

    data = request.get_json(silent=True) or {}
    if 'min_bid_enabled' in data: s["min_bid_enabled"] = bool(data['min_bid_enabled'])
    if 'min_bid_val' in data:
        try: s["min_bid_val"] = max(1, int(data['min_bid_val']))
        except Exception: pass
    if 'vouches_val' in data:
        s["vouches_val"] = str(data['vouches_val'])
    if 'color' in data: s["color"] = str(data['color'])
    if 'theme' in data: s["theme"] = str(data['theme'])

    notify(s)
    return jsonify(ok=True, state=get_public_state(s))

@app.route('/api/player', methods=['POST'])
@app.route('/api/players', methods=['POST'])
def api_players():
    k = get_key_strict()
    if not k: return jsonify(error="Clé invalide"), 403
    s = get_store(k)

    data = request.get_json(silent=True) or {}
    a = data.get('action')

    if a == 'add':
        u = str(data.get('user') or data.get('u') or '').strip().lstrip('@')
        coins = int(data.get('coins', 100))
        if u:
            ex = next((p for p in s["players"] if p["u"].lower() == u.lower()), None)
            if ex:
                ex["coins"] += coins
            else:
                av = fetch_tiktok_avatar(u)
                s["players"].append({
                    "u": u,
                    "nick": u,
                    "name": u,
                    "av": av,
                    "coins": coins
                })

    elif a == 'coins':
        u = str(data.get('user') or data.get('u') or '').strip().lstrip('@')
        mode = data.get('mode', 'delta')
        val = int(data.get('val', 0))
        ex = next((p for p in s["players"] if p["u"].lower() == u.lower()), None)
        if ex:
            if mode == 'delta': ex["coins"] = max(0, ex["coins"] + val)
            elif mode == 'set': ex["coins"] = max(0, val)

    elif a == 'remove':
        u = str(data.get('user') or data.get('u') or '').strip().lstrip('@')
        s["players"] = [p for p in s["players"] if p["u"].lower() != u.lower()]

    elif a == 'clear':
        s["players"] = []

    elif a == 'reset_coins':
        for p in s["players"]: p["coins"] = 0

    elif a == 'swap_top2':
        s["players"].sort(key=lambda x: x["coins"], reverse=True)
        if len(s["players"]) >= 2:
            s["players"][0]["coins"], s["players"][1]["coins"] = s["players"][1]["coins"], s["players"][0]["coins"]

    elif a == 'equalize_top2':
        s["players"].sort(key=lambda x: x["coins"], reverse=True)
        if len(s["players"]) >= 2:
            top_val = s["players"][0]["coins"]
            s["players"][1]["coins"] = top_val

    elif a == 'equalize_top3':
        s["players"].sort(key=lambda x: x["coins"], reverse=True)
        if len(s["players"]) >= 3:
            top_val = s["players"][0]["coins"]
            s["players"][1]["coins"] = top_val
            s["players"][2]["coins"] = top_val

    notify(s)
    return jsonify(ok=True, state=get_public_state(s))

@app.route('/api/tiktok', methods=['POST'])
def api_tiktok():
    k = get_key_strict()
    if not k: return jsonify(error="Clé invalide"), 403
    s = get_store(k)

    data = request.get_json(silent=True) or {}
    a = data.get('action')

    if a == 'connect':
        u = str(data.get('user', '')).strip().lstrip('@')
        if not u:
            return jsonify(error="Nom d'utilisateur requis"), 400

        if s["proc"] and s["proc"].poll() is None:
            try: s["proc"].terminate()
            except Exception: pass

        s["tiktok"] = "connecting"
        s["tiktok_user"] = u
        notify(s)

        port = str(os.environ.get('PORT', 8080))
        try:
            p = subprocess.Popen([sys.executable, os.path.join(BASE_DIR, 'tiktok_worker.py'), k, u, port], cwd=BASE_DIR)
            s["proc"] = p
        except Exception as ex:
            print(f"[Popen Error] {ex}", flush=True)

    elif a == 'disconnect':
        if s["proc"] and s["proc"].poll() is None:
            try: s["proc"].terminate()
            except Exception: pass
        s["proc"] = None
        s["tiktok"] = "off"
        s["tiktok_user"] = ""
        notify(s)

    return jsonify(ok=True, state=get_public_state(s))

@app.route('/api/internal/tiktok_status', methods=['POST'])
def api_internal_tiktok_status():
    data = request.get_json(silent=True) or {}
    k = data.get('key')
    st = data.get('status')
    u = data.get('user', '')
    if k and k in STORES:
        s = STORES[k]
        s["tiktok"] = st
        if st == 'on': s["tiktok_user"] = u
        elif st == 'off': s["tiktok_user"] = ''
        notify(s)
        return jsonify(ok=True)
    return jsonify(error="Store introuvable"), 404

@app.route('/api/internal/gift', methods=['POST'])
def api_internal_gift():
    data = request.get_json(silent=True) or {}
    k = data.get('key')
    if not k or k not in STORES:
        return jsonify(error="Store introuvable"), 404

    s = STORES[k]
    u = data.get('u')
    nick = data.get('nick')
    av = data.get('av')
    coins = data.get('coins', 1)

    if s["min_bid_enabled"] and coins < s["min_bid_val"]:
        return jsonify(ok=True, ignored="sous le minimum")

    if not av:
        av = fetch_tiktok_avatar(u)

    ex = next((p for p in s["players"] if p["u"].lower() == u.lower()), None)
    if ex:
        ex["coins"] += coins
        if nick:
            ex["nick"] = nick
            ex["name"] = nick
        if av: ex["av"] = av
    else:
        s["players"].append({
            "u": u, "nick": nick or u, "name": nick or u, "av": av,
            "coins": coins
        })

    if s["snipe_on"]:
        s["snipe_rem"] = s["snipe_dur"]

    notify(s)
    return jsonify(ok=True)

@app.route('/api/admin/login', methods=['POST'])
def api_admin_login():
    data = request.get_json(silent=True) or {}
    pwd = data.get('password', '')
    if pwd == ADMIN_PASSWORD:
        return jsonify(ok=True, token="admin-authenticated-token")
    return jsonify(ok=False, error="Mot de passe incorrect"), 401

@app.route('/api/admin/keys', methods=['GET', 'POST'])
def api_admin_keys():
    data = request.get_json(silent=True) or {}
    pwd = data.get('password', '') or request.headers.get('X-Admin-Password')
    if pwd != ADMIN_PASSWORD:
        return jsonify(error="Non autorisé"), 401
    return jsonify(keys=VALID_KEYS)

@app.route('/api/admin/keys/create', methods=['POST'])
def api_admin_keys_create():
    data = request.get_json(silent=True) or {}
    pwd = data.get('password', '') or request.headers.get('X-Admin-Password')
    if pwd != ADMIN_PASSWORD:
        return jsonify(error="Non autorisé"), 401

    import uuid
    new_k = "KEY-" + str(uuid.uuid4())[:8].upper()
    lbl = data.get('label', 'Nouveau Client')
    VALID_KEYS[new_k] = {"label": lbl, "created": time.strftime("%Y-%m-%d")}
    save_keys(VALID_KEYS)
    return jsonify(ok=True, key=new_k)

@app.route('/api/admin/keys/delete', methods=['POST'])
def api_admin_keys_delete():
    data = request.get_json(silent=True) or {}
    pwd = data.get('password', '') or request.headers.get('X-Admin-Password')
    if pwd != ADMIN_PASSWORD:
        return jsonify(error="Non autorisé"), 401

    k_del = data.get('key')
    if k_del in VALID_KEYS:
        del VALID_KEYS[k_del]
        save_keys(VALID_KEYS)
    return jsonify(ok=True)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    if HAS_GEVENT:
        print(f"🚀 Serveur Production Gevent WSGI démarré sur le port {port}", flush=True)
        http_server = WSGIServer(('0.0.0.0', port), app)
        http_server.serve_forever()
    else:
        print(f" Serveur Flask démarré sur le port {port}", flush=True)
        app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
