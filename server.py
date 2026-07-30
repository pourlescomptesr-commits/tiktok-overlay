import os, sys, time, json, queue, threading, urllib.request, re, asyncio, subprocess
from flask import Flask, request, jsonify, Response, send_from_directory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, static_folder='public', static_url_path='')

LOCK = threading.Lock()
STATES = {}
SSE_QUEUES = {}
KEYS_FILE = os.path.join(BASE_DIR, 'keys.json')

def load_keys():
    try:
        if os.path.exists(KEYS_FILE):
            with open(KEYS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"Erreur chargement keys.json: {e}")
    return {
        "DEMO1234": {"label": "Demo", "created": "2026-01-01"},
        "KEY-5792224B": {"label": "Client 1", "created": "2026-07-30"},
        "KEY-AA860585": {"label": "Client 2", "created": "2026-07-30"}
    }

def save_keys(keys_dict):
    try:
        with open(KEYS_FILE, 'w', encoding='utf-8') as f:
            json.dump(keys_dict, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Erreur sauvegarde keys.json: {e}")

VALID_KEYS = load_keys()

def default_state():
    return {
        "timer_dur": 60,
        "timer_rem": 60,
        "timer_on": False,
        "min_bid_enabled": False,
        "min_bid_val": 1,
        "snipe_dur": 10,
        "snipe_rem": 10,
        "snipe_on": False,
        "color": "#ffd700",
        "tiktok": "off",
        "tiktok_user": "",
        "players": []
    }

def get_or_create_state(key):
    with LOCK:
        if key not in STATES:
            STATES[key] = default_state()
            SSE_QUEUES[key] = []
        return STATES[key]

def push_key(key):
    with LOCK:
        if key in STATES and key in SSE_QUEUES:
            data = json.dumps(STATES[key])
            for q in list(SSE_QUEUES[key]):
                try: q.put_nowait(data)
                except: pass

def global_timer_loop():
    last_time = time.time()
    while True:
        time.sleep(0.1)
        now = time.time()
        dt = now - last_time
        last_time = now

        with LOCK:
            keys_to_push = set()
            for key, s in STATES.items():
                changed = False

                if s["timer_on"]:
                    s["timer_rem"] -= dt
                    if s["timer_rem"] <= 0:
                        s["timer_rem"] = 0
                        s["timer_on"] = False
                        s["snipe_on"] = False
                        s["snipe_rem"] = s["snipe_dur"]
                    changed = True

                if s["snipe_on"]:
                    s["snipe_rem"] -= dt
                    if s["snipe_rem"] <= 0:
                        s["snipe_rem"] = 0
                        s["snipe_on"] = False
                    changed = True

                if changed:
                    keys_to_push.add(key)

        for k in keys_to_push:
            push_key(k)

threading.Thread(target=global_timer_loop, daemon=True).start()

def trigger_snipe_key(s):
    if s["timer_on"] and s["timer_rem"] <= s["snipe_dur"]:
        s["snipe_on"] = True
        s["snipe_rem"] = s["snipe_dur"]
        s["timer_rem"] = s["snipe_dur"]

def get_key_from_req():
    k = request.args.get('key') or (request.json or {}).get('key') or request.headers.get('X-Streamer-Key')
    if k:
        k = k.strip().upper().replace(' ', '')
        if k in VALID_KEYS or k.startswith("KEY-") or k.startswith("DEMO"):
            if k not in VALID_KEYS:
                VALID_KEYS[k] = {"label": "Auto-registered", "created": time.strftime("%Y-%m-%d")}
            return k
    return None

@app.after_request
def add_no_cache_headers(response):
    if request.path in ['/', '/panel', '/overlay', '/admin']:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

@app.route('/')
def route_home():
    return send_from_directory('public', 'panel.html')

@app.route('/panel')
def route_panel():
    return send_from_directory('public', 'panel.html')

@app.route('/overlay')
def route_overlay():
    return send_from_directory('public', 'overlay.html')

@app.route('/admin')
def route_admin():
    return send_from_directory('public', 'admin.html')

@app.route('/events')
def route_events():
    key = get_key_from_req()
    if not key:
        return "Clé non autorisée", 401
    s = get_or_create_state(key)

    def gen():
        q = queue.Queue(maxsize=50)
        with LOCK:
            if key in SSE_QUEUES:
                SSE_QUEUES[key].append(q)
        try:
            with LOCK:
                init_data = json.dumps(STATES[key])
            yield f"data: {init_data}\n\n"

            while True:
                try:
                    msg = q.get(timeout=20)
                    yield f"data: {msg}\n\n"
                except queue.Empty:
                    yield ": keep-alive\n\n"
        finally:
            with LOCK:
                if key in SSE_QUEUES and q in SSE_QUEUES[key]:
                    SSE_QUEUES[key].remove(q)

    return Response(gen(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',
        'Connection': 'keep-alive'
    })

@app.route('/api/state')
def api_state():
    key = get_key_from_req()
    if not key: return jsonify(ok=False, error="Clé non autorisée"), 401
    s = get_or_create_state(key)
    with LOCK:
        return jsonify(ok=True, state=s)

@app.route('/api/timer', methods=['POST'])
def api_timer():
    key = get_key_from_req()
    if not key: return jsonify(ok=False, error="Clé non autorisée"), 401
    s = get_or_create_state(key)
    a = (request.json or {}).get('action')

    with LOCK:
        if a == 'start':
            s["timer_on"] = True
        elif a == 'pause':
            s["timer_on"] = False
            s["snipe_on"] = False
        elif a == 'reset':
            s["timer_on"] = False
            s["snipe_on"] = False
            s["timer_rem"] = s["timer_dur"]
            s["snipe_rem"] = s["snipe_dur"]
        elif a == 'set_dur':
            v = max(5, int((request.json or {}).get('val', 60)))
            s["timer_dur"] = v
            s["timer_rem"] = v
        elif a == 'add_time':
            sec = int((request.json or {}).get('sec', 30))
            s["timer_rem"] += sec

    push_key(key)
    return jsonify(ok=True)

@app.route('/api/config', methods=['POST'])
def api_config():
    key = get_key_from_req()
    if not key: return jsonify(ok=False, error="Clé non autorisée"), 401
    s = get_or_create_state(key)
    d = request.json or {}
    with LOCK:
        if 'min_bid_enabled' in d: s['min_bid_enabled'] = bool(d['min_bid_enabled'])
        if 'min_bid_val' in d: s['min_bid_val'] = max(0, int(d['min_bid_val']))
    push_key(key)
    return jsonify(ok=True)

@app.route('/api/color', methods=['POST'])
def api_color():
    key = get_key_from_req()
    if not key: return jsonify(ok=False, error="Clé non autorisée"), 401
    s = get_or_create_state(key)
    with LOCK: s["color"] = (request.json or {}).get('color','#ffd700')
    push_key(key)
    return jsonify(ok=True)

TIKTOK_WORKERS = {}

def stop_tiktok_worker_for_key(key):
    with LOCK:
        p = TIKTOK_WORKERS.pop(key, None)
    if p:
        try: p.kill()
        except: pass

@app.route('/api/tiktok', methods=['POST'])
def api_tiktok():
    key = get_key_from_req()
    if not key: return jsonify(ok=False, error="Clé non autorisée"), 401
    s = get_or_create_state(key)
    a = (request.json or {}).get('action')
    if a == 'connect':
        u = (request.json or {}).get('user','').strip().lstrip('@')
        if not u: return jsonify(ok=False)
        stop_tiktok_worker_for_key(key)
        with LOCK:
            s["tiktok"] = "connecting"
            s["tiktok_user"] = u
        push_key(key)
        port = str(os.environ.get('PORT', 3000))
        p = subprocess.Popen([sys.executable, os.path.join(BASE_DIR, 'tiktok_worker.py'), key, u, port], cwd=BASE_DIR)
        with LOCK:
            TIKTOK_WORKERS[key] = p
    elif a == 'disconnect':
        stop_tiktok_worker_for_key(key)
        with LOCK:
            s["tiktok"] = "off"
            s["tiktok_user"] = ""
        push_key(key)
    return jsonify(ok=True)

@app.route('/api/internal/tiktok_status', methods=['POST'])
def api_internal_tiktok_status():
    d = request.json or {}
    key = d.get('key')
    if not key: return jsonify(ok=False)
    with LOCK:
        s = get_or_create_state(key)
        s["tiktok"] = d.get('status', 'off')
        s["tiktok_user"] = d.get('user', '')
    push_key(key)
    return jsonify(ok=True)

@app.route('/api/internal/gift', methods=['POST'])
def api_internal_gift():
    d = request.json or {}
    key = d.get('key')
    if not key: return jsonify(ok=False)
    u = d.get('u','')
    if not u: return jsonify(ok=False)
    nick = d.get('nick', u)
    av = d.get('av', '')
    total_coins = int(d.get('coins', 1))
    with LOCK:
        s = get_or_create_state(key)
        found = False
        for p in s["players"]:
            if p["u"].lower() == u.lower():
                p["coins"] += total_coins
                p["name"] = nick
                found = True
                break
        if not found:
            s["players"].append({"u": u, "name": nick, "av": av, "coins": total_coins})
        s["players"].sort(key=lambda x: x["coins"], reverse=True)
        trigger_snipe_key(s)
    push_key(key)
    return jsonify(ok=True)

@app.route('/api/player', methods=['POST'])
def api_player():
    key = get_key_from_req()
    if not key: return jsonify(ok=False, error="Clé non autorisée"), 401
    s = get_or_create_state(key)
    d = request.json or {}
    a = d.get('action')

    with LOCK:
        if a == 'add':
            u = (d.get('user') or '').strip().lstrip('@')
            c = max(1, int(d.get('coins', 10)))
            if u:
                found = False
                for p in s["players"]:
                    if p["u"].lower() == u.lower():
                        p["coins"] += c
                        found = True
                        break
                if not found:
                    s["players"].append({
                        "u": u,
                        "name": u,
                        "av": f"https://api.dicebear.com/7.x/initials/svg?seed={u}",
                        "coins": c
                    })
                s["players"].sort(key=lambda x: x["coins"], reverse=True)
                trigger_snipe_key(s)
        elif a == 'remove':
            idx = d.get('idx')
            u_del = (d.get('user') or '').strip().lower()
            if idx is not None and isinstance(idx, int) and 0 <= idx < len(s["players"]):
                s["players"].pop(idx)
            elif u_del:
                s["players"] = [p for p in s["players"] if p["u"].lower() != u_del]
        elif a == 'clear':
            s["players"] = []

    push_key(key)
    return jsonify(ok=True)

@app.route('/api/admin/keys', methods=['GET', 'POST'])
def api_admin_keys():
    pwd = request.headers.get('X-Admin-Password') or request.args.get('pwd') or (request.json or {}).get('pwd')
    if pwd != "Grenouille123":
        return jsonify(ok=False, error="Mot de passe admin incorrect"), 403

    if request.method == 'GET':
        return jsonify(ok=True, keys=VALID_KEYS)

    a = (request.json or {}).get('action')
    if a == 'create':
        import uuid
        new_k = "KEY-" + str(uuid.uuid4())[:8].upper()
        lbl = (request.json or {}).get('label', 'Nouveau Client')
        VALID_KEYS[new_k] = {"label": lbl, "created": time.strftime("%Y-%m-%d")}
        save_keys(VALID_KEYS)
        return jsonify(ok=True, key=new_k)
    elif a == 'delete':
        k_del = (request.json or {}).get('key')
        if k_del in VALID_KEYS:
            del VALID_KEYS[k_del]
            save_keys(VALID_KEYS)
        return jsonify(ok=True)

    return jsonify(ok=False, error="Action invalide"), 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 3000))
    print(f" Serveur démarré sur le port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
