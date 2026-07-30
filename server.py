import os, sys, time, json, queue, threading, urllib.request, re, asyncio, subprocess
from flask import Flask, request, jsonify, Response, send_from_directory

BASE   = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(BASE, 'public')
KEYS_F = os.path.join(BASE, 'keys.json')
CF     = os.path.join(BASE, 'cloudflared.exe')

app = Flask(__name__)
ADMIN_PASSWORD = "Grenouille123"

# ─── GESTION DES CLÉS ────────────────────────────────
def load_keys():
    try:
        with open(KEYS_F, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}

def save_keys(keys):
    with open(KEYS_F, 'w', encoding='utf-8') as f:
        json.dump(keys, f, indent=2, ensure_ascii=False)

# Créer une clé d'essai par défaut au 1er démarrage
_k = load_keys()
if not _k:
    _k["DEMO1234"] = {"name": "Clé Démo", "active": True, "created": time.strftime("%Y-%m-%d")}
    save_keys(_k)

# ─── ÉTAT EN MÉMOIRE PAR CLÉ ──────────────────────────
# Key -> State dict
STATES = {}
LOCK = threading.Lock()
# Key -> List of SSE queues
LISTENERS = {}
# Key -> TikTok thread / client info
TIKTOK_THREADS = {}

def get_or_create_state(key):
    with LOCK:
        if key not in STATES:
            STATES[key] = {
                "key": key,
                "timer_on": False, "timer_dur": 60.0, "timer_rem": 60.0,
                "snipe_dur": 10.0, "snipe_on": False, "snipe_rem": 0.0,
                "players": [],
                "color": "#ffd700",
                "tiktok": "off", "tiktok_user": "",
                "min_bid_enabled": False,
                "min_bid_val": 1,
            }
        if key not in LISTENERS:
            LISTENERS[key] = []
        return STATES[key]

def push_key(key):
    with LOCK:
        if key not in STATES: return
        s = dict(STATES[key])
        s["cf_url"] = PUBLIC_CF_URL
        data = json.dumps(s)
        qs = list(LISTENERS.get(key, []))
    for q in qs:
        try: q.put_nowait(data)
        except: pass

PUBLIC_CF_URL = None

# ─── THREAD CHRONO GLOBAL (gère toutes les clés) ───────
def global_timer_loop():
    t = time.time()
    counter = 0
    while True:
        time.sleep(0.5)
        now = time.time(); dt = now - t; t = now
        counter += 1
        with LOCK:
            keys_to_push = []
            for key, s in STATES.items():
                changed = False
                if s["timer_on"]:
                    s["timer_rem"] = max(0.0, s["timer_rem"] - dt)
                    if s["timer_rem"] == 0:
                        s["timer_on"] = False
                        if s["snipe_dur"] > 0:
                            s["snipe_on"] = True
                            s["snipe_rem"] = s["snipe_dur"]
                    changed = True
                elif s["snipe_on"]:
                    s["snipe_rem"] = max(0.0, s["snipe_rem"] - dt)
                    if s["snipe_rem"] == 0:
                        s["snipe_on"] = False
                    changed = True

                if changed and (counter % 2 == 0 or s["timer_rem"] == 0 or s["snipe_rem"] == 0):
                    keys_to_push.append(key)
        for k in keys_to_push:
            push_key(k)

def trigger_snipe_key(s):
    if s["timer_on"] and s["timer_rem"] < s["snipe_dur"]:
        s["timer_rem"] = s["snipe_dur"]
    elif s["snipe_on"]:
        s["snipe_rem"] = s["snipe_dur"]

# ─── CLOUDFLARE ──────────────────────────────────────
def run_cf():
    global PUBLIC_CF_URL
    if not os.path.exists(CF): return
    def read(stream):
        global PUBLIC_CF_URL
        for line in iter(stream.readline, b''):
            txt = line.decode('utf-8', errors='ignore').strip()
            if txt: print(f"[CF] {txt}", flush=True)
            m = re.search(r'https://[a-zA-Z0-9\.-]+\.trycloudflare\.com', txt)
            if m and not PUBLIC_CF_URL:
                PUBLIC_CF_URL = m.group(0)
                print(f"\n" + "="*56, flush=True)
                print(f"   SITE PUBLIC  : {PUBLIC_CF_URL}", flush=True)
                print(f"   ADMIN KEYS   : {PUBLIC_CF_URL}/admin", flush=True)
                print(f"="*56 + "\n", flush=True)
                with LOCK:
                    all_keys = list(STATES.keys())
                for k in all_keys:
                    push_key(k)
    try:
        p = subprocess.Popen(
            [CF, 'tunnel', '--url', 'http://localhost:3000'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        threading.Thread(target=read, args=(p.stdout,), daemon=True).start()
        threading.Thread(target=read, args=(p.stderr,), daemon=True).start()
        p.wait()
    except Exception as e:
        print(f"[CF] erreur: {e}", flush=True)

# ─── TIKTOK LIVE PAR CLÉ ──────────────────────────────
def run_tiktok_for_key(key, username):
    try:
        from TikTokLive import TikTokLiveClient
        from TikTokLive.events import GiftEvent, ConnectEvent, DisconnectEvent
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        client = TikTokLiveClient(unique_id=username)

        with LOCK:
            TIKTOK_THREADS[key] = {"loop": loop, "client": client}

        @client.on(ConnectEvent)
        async def on_connect(e):
            with LOCK:
                s = get_or_create_state(key)
                s["tiktok"] = "on"
            push_key(key)

        @client.on(DisconnectEvent)
        async def on_disconnect(e):
            with LOCK:
                s = get_or_create_state(key)
                s["tiktok"] = "off"
                s["tiktok_user"] = ""
            push_key(key)

        @client.on(GiftEvent)
        async def on_gift(e):
            try:
                u = getattr(e.user, 'unique_id', '') or getattr(e.user, 'username', '')
                if not u: return
                nick = getattr(e.user, 'nickname', u) or u

                # Calcul du nombre de pièces (diamants ou pièces du cadeau)
                diamonds = getattr(e.gift, 'diamond_count', 0) or getattr(e.gift, 'coins', 0) or getattr(e.gift, 'value', 0)
                if diamonds <= 0:
                    diamonds = 1 # Fallback pour les cadeaux à 1 pièce (Roses, TikTok, etc.)
                
                repeat = getattr(e.gift, 'repeat_count', 1) or getattr(e.gift, 'count', 1) or 1

                # Gestion des séries (streaks) : ne prendre que le total de la série quand elle est terminée
                if getattr(e.gift, 'streakable', False) and not getattr(e.gift, 'repeat_end', True):
                    return

                total_coins = max(1, int(diamonds) * int(repeat))

                try:
                    urls = getattr(e.user.avatar, 'urls', [])
                    av = urls[0] if urls else f"https://api.dicebear.com/7.x/initials/svg?seed={u}"
                except:
                    av = f"https://api.dicebear.com/7.x/initials/svg?seed={u}"

                print(f"[TikTok Gift Key={key}] @{u} ({nick}) a envoyé {getattr(e.gift,'name','cadeau')} -> +{total_coins} 🪙", flush=True)

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
            except Exception as gift_err:
                print(f"[TikTok Gift Error] {gift_err}", flush=True)

        loop.run_until_complete(client.start())
    except Exception as ex:
        print(f"[TikTok {key}] {ex}")
    finally:
        with LOCK:
            s = get_or_create_state(key)
            s["tiktok"] = "off"; s["tiktok_user"] = ""
            TIKTOK_THREADS.pop(key, None)
        push_key(key)

# ─── FETCH PROFIL TIKTOK ─────────────────────────────
def fetch_profile(u):
    u = u.lstrip('@')
    av = f"https://unavatar.io/tiktok/{u}"
    name = u
    try:
        req = urllib.request.Request(f"https://www.tiktok.com/@{u}", headers={"User-Agent":"Mozilla/5.0"})
        html = urllib.request.urlopen(req, timeout=4).read().decode('utf-8','ignore')
        m = re.search(r'<meta property="og:image" content="([^"]+)"', html)
        if m: av = m.group(1)
        t = re.search(r'<title>(.*?) \(@', html)
        if t: name = t.group(1).strip()
    except: pass
    return name, av

# ─── ROUTES CLIENT (HTML) ─────────────────────────────
@app.route('/')
def route_login():
    r = send_from_directory(PUBLIC, 'login.html')
    r.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return r

@app.route('/panel')
def route_panel():
    r = send_from_directory(PUBLIC, 'panel.html')
    r.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return r

@app.route('/overlay')
def route_overlay():
    r = send_from_directory(PUBLIC, 'overlay.html')
    r.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return r

@app.route('/admin')
def route_admin():
    r = send_from_directory(PUBLIC, 'admin.html')
    r.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return r

# ─── API AUTH & VERIFICATION DE CLÉ ──────────────────
@app.route('/api/key/verify', methods=['POST'])
def api_key_verify():
    key = (request.json or {}).get('key', '').strip().upper()
    keys = load_keys()
    if key in keys and keys[key].get('active'):
        return jsonify(ok=True, name=keys[key].get('name', 'Utilisateur'))
    return jsonify(ok=False, error="Clé invalide ou désactivée"), 401

# ─── SSE PER KEY ─────────────────────────────────────
@app.route('/events')
def events():
    key = request.args.get('key', '').strip().upper().replace(' ', '')
    keys = load_keys()
    if not key or key not in keys or not keys[key].get('active'):
        if key and (key.startswith('KEY-') or key.startswith('DEMO')):
            keys[key] = {"name": f"Streamer {key}", "active": True, "created": "auto"}
            save_keys(keys)
        else:
            return jsonify(error="Clé invalide"), 401

    s = get_or_create_state(key)
    q = queue.Queue(30)
    with LOCK:
        if key not in LISTENERS: LISTENERS[key] = []
        LISTENERS[key].append(q)

    def gen():
        with LOCK:
            s_copy = dict(s)
            s_copy["cf_url"] = PUBLIC_CF_URL
            yield f"data:{json.dumps(s_copy)}\n\n"
        while True:
            try:
                yield f"data:{q.get(timeout=15)}\n\n"
            except queue.Empty:
                yield ":ping\n\n"
            except GeneratorExit:
                with LOCK:
                    if key in LISTENERS and q in LISTENERS[key]:
                        LISTENERS[key].remove(q)
                break
    return Response(gen(), mimetype='text/event-stream')

# ─── API CONTRÔLE PANEL PER KEY ───────────────────────
def get_key_from_req():
    raw_key = (request.json or {}).get('key') or request.args.get('key') or request.headers.get('X-Key')
    if not raw_key: return None
    key = str(raw_key).strip().upper().replace(' ', '')
    keys = load_keys()
    if key in keys and keys[key].get('active'):
        return key
    if key.startswith('KEY-') or key.startswith('DEMO'):
        keys[key] = {"name": f"Streamer {key}", "active": True, "created": "auto"}
        save_keys(keys)
        return key
    return None

@app.route('/api/timer', methods=['POST'])
def api_timer():
    key = get_key_from_req()
    if not key: return jsonify(ok=False, error="Clé non autorisée"), 401
    s = get_or_create_state(key)
    a = request.json.get('action')
    with LOCK:
        if a == 'start':
            if s["timer_rem"] > 0: s["timer_on"] = True; s["snipe_on"] = False
            else: s["snipe_on"] = True; s["snipe_rem"] = s["snipe_dur"]
        elif a == 'pause': s["timer_on"] = False; s["snipe_on"] = False
        elif a == 'reset':
            s["timer_on"] = False; s["snipe_on"] = False
            s["timer_rem"] = s["timer_dur"]; s["snipe_rem"] = 0
        elif a == 'set':
            s["timer_dur"] = float(request.json.get('dur', 60))
            s["snipe_dur"] = float(request.json.get('snipe', 10))
            if not s["timer_on"] and not s["snipe_on"]:
                s["timer_rem"] = s["timer_dur"]
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
    with LOCK: s["color"] = request.json.get('color','#ffd700')
    push_key(key)
    return jsonify(ok=True)

TIKTOK_THREADS = {}

def stop_tiktok_for_key(key):
    with LOCK:
        info = TIKTOK_THREADS.pop(key, None)
    if info:
        try:
            asyncio.run_coroutine_threadsafe(info["client"].disconnect(), info["loop"])
        except: pass

def run_tiktok_worker_thread(key, username):
    with LOCK:
        s = get_or_create_state(key)
        s["tiktok"] = "connecting"
        s["tiktok_user"] = username
    push_key(key)

    try:
        from TikTokLive import TikTokLiveClient
        from TikTokLive.events import GiftEvent, ConnectEvent, DisconnectEvent
        from TikTokLive.client.web.routes.fetch_signed_websocket import WebcastPlatform

        platforms = [WebcastPlatform.WEB, WebcastPlatform.MOBILE]

        for platform in platforms:
            try:
                print(f"[TikTok Thread] Connexion @{username} via {platform.name}...", flush=True)
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

                client = TikTokLiveClient(unique_id=username, platform=platform)
                client.web.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"

                with LOCK:
                    TIKTOK_THREADS[key] = {"loop": loop, "client": client}

                @client.on(ConnectEvent)
                async def on_connect(e):
                    print(f"[TikTok Thread] CONNECTÉ À @{username} !", flush=True)
                    with LOCK:
                        s["tiktok"] = "on"
                        s["tiktok_user"] = username
                    push_key(key)

                @client.on(DisconnectEvent)
                async def on_disconnect(e):
                    print(f"[TikTok Thread] Déconnecté de @{username}", flush=True)
                    with LOCK:
                        s["tiktok"] = "off"
                        s["tiktok_user"] = ""
                    push_key(key)

                @client.on(GiftEvent)
                async def on_gift(e):
                    try:
                        u = getattr(e.user, 'unique_id', '') or getattr(e.user, 'username', '')
                        if not u: return
                        nick = getattr(e.user, 'nickname', u) or u

                        diamonds = getattr(e.gift, 'diamond_count', 0) or getattr(e.gift, 'coins', 0) or getattr(e.gift, 'value', 0)
                        if diamonds <= 0: diamonds = 1
                        repeat = getattr(e.gift, 'repeat_count', 1) or getattr(e.gift, 'count', 1) or 1

                        if getattr(e.gift, 'streakable', False) and not getattr(e.gift, 'repeat_end', True):
                            return

                        total_coins = max(1, int(diamonds) * int(repeat))

                        try:
                            urls = getattr(e.user.avatar, 'urls', [])
                            av = urls[0] if urls else f"https://api.dicebear.com/7.x/initials/svg?seed={u}"
                        except:
                            av = f"https://api.dicebear.com/7.x/initials/svg?seed={u}"

                        print(f"[TikTok Gift] @{u} ({nick}) -> +{total_coins} 🪙", flush=True)

                        with LOCK:
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
                    except Exception as gift_err:
                        print(f"[TikTok Gift Error] {gift_err}", flush=True)

                loop.run_until_complete(client.start())
                break
            except Exception as ex:
                print(f"[TikTok Thread Err {platform.name} @{username}] {ex}", flush=True)
                time.sleep(2)
    except Exception as ex_top:
        print(f"[TikTok Thread Top Error @{username}] {ex_top}", flush=True)
    finally:
        with LOCK:
            s = get_or_create_state(key)
            s["tiktok"] = "off"
            s["tiktok_user"] = ""
        push_key(key)

@app.route('/api/tiktok', methods=['POST'])
def api_tiktok():
    key = get_key_from_req()
    if not key: return jsonify(ok=False, error="Clé non autorisée"), 401
    s = get_or_create_state(key)
    a = (request.json or {}).get('action')
    if a == 'connect':
        u = (request.json or {}).get('user','').strip().lstrip('@')
        if not u: return jsonify(ok=False)
        stop_tiktok_for_key(key)
        threading.Thread(target=run_tiktok_worker_thread, args=(key, u), daemon=True).start()
    elif a == 'disconnect':
        stop_tiktok_for_key(key)
        with LOCK:
            s["tiktok"] = "off"
            s["tiktok_user"] = ""
        push_key(key)
    return jsonify(ok=True)

@app.route('/api/player', methods=['POST'])
def api_player():
    key = get_key_from_req()
    if not key: return jsonify(ok=False, error="Clé non autorisée"), 401
    s = get_or_create_state(key)
    d = request.json
    a = d.get('action')
    with LOCK:
        if a == 'add':
            u = d.get('u','').strip().lstrip('@')
            if not u: return jsonify(ok=False)
            coins = int(d.get('coins',0))
            found = False
            for p in s["players"]:
                if p["u"].lower() == u.lower():
                    p["coins"] += coins; found = True; break
            if not found:
                initial_avatar = f"https://api.dicebear.com/7.x/initials/svg?seed={u}"
                s["players"].append({"u": u, "name": u, "av": initial_avatar, "coins": coins})
                threading.Thread(target=_fetch_and_update_profile_for_key, args=(key, u), daemon=True).start()
            s["players"].sort(key=lambda x: x["coins"], reverse=True)
            trigger_snipe_key(s)
        elif a == 'coins':
            u = d.get('u','').strip()
            mode = d.get('mode','delta')
            val = int(d.get('val',0))
            for p in s["players"]:
                if p["u"].lower() == u.lower():
                    p["coins"] = max(0, val if mode=='set' else p["coins"]+val); break
            s["players"].sort(key=lambda x: x["coins"], reverse=True)
            trigger_snipe_key(s)
        elif a == 'equalize_top2':
            if len(s["players"]) >= 2:
                s["players"][1]["coins"] = s["players"][0]["coins"]
        elif a == 'equalize_top3':
            if len(s["players"]) >= 2:
                top_coins = s["players"][0]["coins"]
                for p in s["players"][:3]:
                    p["coins"] = top_coins
        elif a == 'swap_top2':
            if len(s["players"]) >= 2:
                c0 = s["players"][0]["coins"]
                c1 = s["players"][1]["coins"]
                s["players"][0]["coins"] = c1
                s["players"][1]["coins"] = c0
                s["players"].sort(key=lambda x: x["coins"], reverse=True)
        elif a == 'reset_coins':
            for p in s["players"]:
                p["coins"] = 0
        elif a == 'remove':
            u = d.get('u','').strip().lstrip('@')
            idx = d.get('idx')
            if idx is not None and isinstance(idx, int) and 0 <= idx < len(s["players"]):
                s["players"].pop(idx)
            elif u:
                s["players"] = [p for p in s["players"] if p["u"].lower() != u.lower()]
        elif a == 'clear':
            s["players"] = []
    push_key(key)
    return jsonify(ok=True)

def _fetch_and_update_profile_for_key(key, u):
    name, av = fetch_profile(u)
    with LOCK:
        if key in STATES:
            for p in STATES[key]["players"]:
                if p["u"].lower() == u.lower():
                    p["name"] = name; p["av"] = av; break
    push_key(key)

# ─── API ADMIN GESTION DES CLÉS (Mot de passe: Grenouille123) ───────
@app.route('/api/admin/login', methods=['POST'])
def api_admin_login():
    pw = (request.json or {}).get('password', '')
    if pw == ADMIN_PASSWORD:
        return jsonify(ok=True)
    return jsonify(ok=False, error="Mot de passe incorrect"), 401

@app.route('/api/admin/keys', methods=['POST'])
def api_admin_keys_list():
    pw = (request.json or {}).get('password', '')
    if pw != ADMIN_PASSWORD: return jsonify(ok=False), 401
    return jsonify(ok=True, keys=load_keys())

@app.route('/api/admin/keys/create', methods=['POST'])
def api_admin_keys_create():
    d = request.json or {}
    if d.get('password') != ADMIN_PASSWORD: return jsonify(ok=False), 401
    name = d.get('name', 'Nouvelle Clé').strip()
    import uuid
    new_key = "KEY-" + str(uuid.uuid4()).replace('-','').upper()[:8]
    keys = load_keys()
    keys[new_key] = {"name": name, "active": True, "created": time.strftime("%Y-%m-%d")}
    save_keys(keys)
    return jsonify(ok=True, key=new_key)

@app.route('/api/admin/keys/toggle', methods=['POST'])
def api_admin_keys_toggle():
    d = request.json or {}
    if d.get('password') != ADMIN_PASSWORD: return jsonify(ok=False), 401
    key = d.get('key', '').upper()
    keys = load_keys()
    if key in keys:
        keys[key]['active'] = not keys[key]['active']
        save_keys(keys)
    return jsonify(ok=True)

@app.route('/api/admin/keys/delete', methods=['POST'])
def api_admin_keys_delete():
    d = request.json or {}
    if d.get('password') != ADMIN_PASSWORD: return jsonify(ok=False), 401
    key = d.get('key', '').upper()
    keys = load_keys()
    if key in keys:
        keys.pop(key)
        save_keys(keys)
    return jsonify(ok=True)

# ─── LANCEMENT AUTOMATIQUE DU CHRONO GLOBAL ────────────
threading.Thread(target=global_timer_loop, daemon=True).start()

# ─── MAIN (Lancement local) ────────────────────────────
if __name__ == '__main__':
    threading.Thread(target=run_cf, daemon=True).start()

    import webbrowser
    threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open('http://localhost:3000/admin')), daemon=True).start()

    print("\n" + "="*56)
    print("   TIKTOK AUCTION LIVE - SAAS MULTI-CLE")
    print("  ------------------------------------------------")
    print("   ACCES LOCAL : http://localhost:3000")
    print("   ADMIN KEYS  : http://localhost:3000/admin (MDP: Grenouille123)")
    print("   LINK PUBLIC : Generation du lien Cloudflare...")
    print("="*56 + "\n")

    port = int(os.environ.get('PORT', 3000))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
