import sys, time, json, urllib.request, httpx

if len(sys.argv) < 3:
    sys.exit(1)

key = sys.argv[1]
username = sys.argv[2].strip().lstrip('@')
port = sys.argv[3] if len(sys.argv) > 3 else "3000"

PROXY_STR = "http://lresmlvg:nn73ir9gv9zs@31.59.20.176:6754"
httpx_proxy = httpx.Proxy(PROXY_STR)

print(f"[TikTok Worker] Démarrage pour key={key}, user=@{username} (proxy={PROXY_STR})", flush=True)

def notify_flask(endpoint, data):
    for host in ["127.0.0.1", "localhost", "0.0.0.0"]:
        try:
            url = f"http://{host}:{port}{endpoint}"
            payload = json.dumps({"key": key, **data}).encode('utf-8')
            req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status == 200:
                    return
        except Exception:
            pass

from TikTokLive import TikTokLiveClient
from TikTokLive.events import GiftEvent, ConnectEvent, DisconnectEvent

for attempt in range(1, 3):
    try:
        print(f"[TikTok Worker] Connexion à @{username} (Essai {attempt})...", flush=True)
        notify_flask('/api/internal/tiktok_status', {"status": "connecting", "user": username})

        client = TikTokLiveClient(
            unique_id=username,
            web_proxy=httpx_proxy,
            ws_proxy=httpx_proxy
        )

        @client.on(ConnectEvent)
        def on_connect(e):
            print(f"[TikTok Worker] Connecté à @{username} !", flush=True)
            notify_flask('/api/internal/tiktok_status', {"status": "on", "user": username})

        @client.on(DisconnectEvent)
        def on_disconnect(e):
            print(f"[TikTok Worker] Déconnecté de @{username}", flush=True)
            notify_flask('/api/internal/tiktok_status', {"status": "off", "user": ""})

        @client.on(GiftEvent)
        def on_gift(e):
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

                av = None
                try:
                    if hasattr(e.user, 'avatar') and e.user.avatar:
                        urls = getattr(e.user.avatar, 'urls', [])
                        if urls:
                            av = urls[-1] or urls[0]
                except Exception:
                    pass

                if not av:
                    av = f"https://api.dicebear.com/7.x/initials/svg?seed={u}"

                print(f"[TikTok Gift] @{u} ({nick}) -> +{total_coins} 🪙", flush=True)

                notify_flask('/api/internal/gift', {
                    "u": u, "nick": nick, "av": av, "coins": total_coins
                })
            except Exception as gift_err:
                print(f"[TikTok Worker Gift Err] {gift_err}", flush=True)

        client.run()
        break
    except Exception as ex:
        print(f"[TikTok Worker Error @{username}] {ex}", flush=True)
        time.sleep(1)

notify_flask('/api/internal/tiktok_status', {"status": "off", "user": ""})
