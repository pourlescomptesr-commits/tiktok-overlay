import sys, time, json, asyncio, urllib.request, re

if len(sys.argv) < 3:
    sys.exit(1)

key = sys.argv[1]
username = sys.argv[2].strip().lstrip('@')
port = sys.argv[3] if len(sys.argv) > 3 else "3000"

PROXY_URL = "http://lresmlvg:nn73ir9gv9zs@p.webshare.io:80"

print(f"[TikTok Worker] Démarrage pour key={key}, user=@{username} (proxy={PROXY_URL})", flush=True)

def notify_flask(endpoint, data):
    for host in ["127.0.0.1", "localhost", "0.0.0.0"]:
        try:
            url = f"http://{host}:{port}{endpoint}"
            payload = json.dumps({"key": key, **data}).encode('utf-8')
            req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    return
        except Exception:
            pass
    print(f"[TikTok Worker Notify Failed] {endpoint}", flush=True)

def fetch_room_id(user):
    try:
        url = f"https://www.tiktok.com/@{user}/live"
        proxy_handler = urllib.request.ProxyHandler({'http': PROXY_URL, 'https': PROXY_URL})
        opener = urllib.request.build_opener(proxy_handler)
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
            'Accept-Language': 'fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7',
            'Referer': 'https://www.tiktok.com/'
        })
        html = opener.open(req, timeout=8).read().decode('utf-8', errors='ignore')
        m = re.search(r'"roomId"\s*:\s*"(\d+)"', html) or re.search(r'room_id=(\d+)', html) or re.search(r'"roomId":(\d+)', html)
        if m:
            print(f"[TikTok Worker] RoomID extrait avec succès: {m.group(1)}", flush=True)
            return m.group(1)
    except Exception as e:
        print(f"[TikTok Worker RoomID Scrape Error] {e}", flush=True)
    return None

from TikTokLive import TikTokLiveClient
from TikTokLive.events import GiftEvent, ConnectEvent, DisconnectEvent
from TikTokLive.client.web.routes.fetch_signed_websocket import WebcastPlatform

platforms = [WebcastPlatform.WEB, WebcastPlatform.MOBILE]

for attempt in range(1, 6):
    for platform in platforms:
        try:
            print(f"[TikTok Worker] Essai plate-forme {platform.name} pour @{username}...", flush=True)
            notify_flask('/api/internal/tiktok_status', {"status": "connecting", "user": username})

            room_id = fetch_room_id(username)
            target = room_id if room_id else username

            client = TikTokLiveClient(unique_id=target, platform=platform, web_proxy=PROXY_URL, ws_proxy=None)
            client.web.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"

            @client.on(ConnectEvent)
            async def on_connect(e):
                print(f"[TikTok Worker] Connecté avec succès à @{username} via {platform.name} !", flush=True)
                notify_flask('/api/internal/tiktok_status', {"status": "on", "user": username})

            @client.on(DisconnectEvent)
            async def on_disconnect(e):
                print(f"[TikTok Worker] Déconnecté de @{username}", flush=True)
                notify_flask('/api/internal/tiktok_status', {"status": "off", "user": ""})

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

                    notify_flask('/api/internal/gift', {
                        "u": u, "nick": nick, "av": av, "coins": total_coins
                    })
                except Exception as gift_err:
                    print(f"[TikTok Worker Gift Err] {gift_err}", flush=True)

            asyncio.run(client.start())
            break
        except Exception as ex:
            print(f"[TikTok Worker Platform {platform.name} Error @{username}] {ex}", flush=True)
            time.sleep(2)

notify_flask('/api/internal/tiktok_status', {"status": "off", "user": ""})
