"""Planlı paylaşım · Instagram (S17). Bu dosya GitHub'da, zamanlayıcının açtığı geçici bir bilgisayarda çalışır; stüdyo onu depoya kopyalar.

Kanal sahibi (12.10): "b olsun… İkisi de tam otomatik olsun." Instagram "şu saatte yayınla" demeyi kabul etmiyor; saati gelince her zaman açık
bir yerin paylaşması gerekiyor. O yer burası.

İki adım (iş akışı sırayla çağırır):
  hazirla  kuyruğu açar (şifreli), saati gelen gönderileri bulur, dosyalarını indirip açar ve `site/` klasörüne koyar. Instagram dosyayı
           internetten çektiği için bu klasör birkaç dakikalığına herkese açık bir adreste yayınlanır (GitHub Pages; rastgele adlı klasör).
  paylas   adresler açılınca Instagram'a gönderir (tek fotoğraf · kaydırmalı · Reels), sonucu durum dosyasına (şifreli) yazar.

Güvenlik: paylaşım anahtarları ve şifre yalnız ortam değişkeninden okunur, hiçbir yere yazılmaz ve basılmaz. Günlük herkese açık olduğu için
yalnız rastgele kimlikler ve durum sözcükleri basılır; açıklama, kanal adı, adres basılmaz. Çift paylaşımı önlemek için "yayınla" çağrısından
hemen önce durum `yayinlaniyor` diye kaydedilir: o noktadan sonra yarıda kalan gönderi yeniden DENENMEZ, stüdyoda "kontrol et" diye görünür.
"""
import base64
import datetime as dt
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

GH = "https://api.github.com"
IG = "https://graph.instagram.com"
BUFFER = "https://api.buffer.com"      # TikTok (29.10): Buffer'ın GraphQL bağlantısı; anahtar "Authorization: Bearer …" başlığıyla (developers.buffer.com/guides/authentication)
BUFFER_BEKLE_SN = 720                  # Buffer'ın videoyu çekip TikTok'a vermesi beklenir; bitmezse durum "yayinlaniyor" kalır, stüdyo Buffer'dan okur
KUYRUK, DURUM, SURUM_ETIKETI = "kuyruk.enc", "durum.enc", "kuyruk"
ERKEN_SN = 90                          # zamanlayıcı birkaç saniye erken tetiklerse de paylaş
GEC_SAAT = 6                           # bu kadar geciken gönderi artık paylaşılmaz ("kaçtı"; stüdyo yeniden planlatır)
DENEME_EN_COK = 3
TEKRAR_DK = 4                          # olmayan deneme bu kadar dakika geçmeden yinelenmez (geciken tetiklemeler art arda gelince üç hak yarım dakikada bitmesin; 13.10)
BEKLE_SN = {"REELS": 420, "KAYDIRMALI": 180, "TEK": 120}
IS = Path("_is.json")                  # iki adım arasında, yalnız bu geçici bilgisayarın diskinde


# ---------------------------------------------------------------- şifre
def _anahtar() -> bytes:
    k = base64.b64decode(os.environ["KILIT"])
    if len(k) != 32:
        raise SystemExit("şifre anahtarı geçersiz")
    return k


def sifrele(veri: bytes, anahtar: bytes | None = None) -> bytes:
    n = secrets.token_bytes(12)
    return n + AESGCM(anahtar or _anahtar()).encrypt(n, veri, None)


def coz(veri: bytes, anahtar: bytes | None = None) -> bytes:
    return AESGCM(anahtar or _anahtar()).decrypt(veri[:12], veri[12:], None)


def paketle(veri: bytes, anahtar: bytes | None = None) -> bytes:
    """Depodaki küçük dosyalar (kuyruk, durum) için: şifreli veri düz ASCII metin (base64) olarak yazılır. Neden: GitHub'ın dosya okuma ucu, metne
    benzeyen küçük ikili dosyayı başka bir karakter kümesinden UTF-8'e ÇEVİREREK döndürüyor (12.10, ilk canlı çalışma: 30 baytlık durum dosyası
    40 bayt geldi, açılamadı, paylaşım durdu). ASCII metin çevrilmez."""
    return base64.b64encode(sifrele(veri, anahtar))


def ac(ham: bytes, anahtar: bytes | None = None) -> bytes:
    """paketle'nin tersi; eski (ham ikili) dosyayı da açar."""
    try:
        return coz(base64.b64decode(ham.strip(), validate=True), anahtar)
    except Exception:                                              # noqa: BLE001 — base64 değil ya da eski biçim: ham dene (o da açılmazsa hata yükselir)
        return coz(ham, anahtar)


# ---------------------------------------------------------------- GitHub
def _gh(yontem: str, yol: str, govde: dict | None = None, ham: bool = False, kabul: str | None = None):
    r = urllib.request.Request(yol if yol.startswith("http") else GH + yol, method=yontem,
                               data=None if govde is None else json.dumps(govde).encode("utf-8"),
                               headers={"Accept": kabul or "application/vnd.github+json", "User-Agent": "studyo-paylasim", "X-GitHub-Api-Version": "2022-11-28"})
    r.add_unredirected_header("Authorization", f"Bearer {os.environ['GITHUB_TOKEN']}")      # dosya indirme başka sunucuya yönlenir: anahtar oraya gitmez
    try:
        with urllib.request.urlopen(r, timeout=300) as y:
            b = y.read()
    except urllib.error.HTTPError as h:
        if h.code == 404:
            return None
        raise SystemExit(f"GitHub {yontem} {h.code}")
    return b if ham else (json.loads(b.decode("utf-8")) if b.strip() else {})


def _depo() -> str:
    return os.environ["GITHUB_REPOSITORY"]


def dosya_oku(ad: str) -> tuple[bytes | None, str | None]:
    v = _gh("GET", f"/repos/{_depo()}/contents/{ad}")
    if not v:
        return None, None
    ham = base64.b64decode(v["content"]) if v.get("content") else _gh("GET", v["download_url"], ham=True)
    return ham, v["sha"]


def dosya_yaz(ad: str, veri: bytes, mesaj: str) -> None:
    for _ in range(4):                                             # stüdyo aynı anda başka dosya yazmış olabilir: sürümü yeniden oku
        _, sha = dosya_oku(ad)
        govde = {"message": mesaj, "content": base64.b64encode(veri).decode("ascii"), **({"sha": sha} if sha else {})}
        r = urllib.request.Request(f"{GH}/repos/{_depo()}/contents/{ad}", method="PUT", data=json.dumps(govde).encode("utf-8"),
                                   headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json", "User-Agent": "studyo-paylasim"})
        try:
            urllib.request.urlopen(r, timeout=120).read()
            return
        except urllib.error.HTTPError as h:
            if h.code not in (409, 422):
                raise SystemExit(f"GitHub yazma {h.code}")
            time.sleep(2)
    raise SystemExit("durum dosyası yazılamadı")


def varliklar() -> dict:
    s = _gh("GET", f"/repos/{_depo()}/releases/tags/{SURUM_ETIKETI}")
    return {a["name"]: a for a in (s or {}).get("assets", [])}


# ---------------------------------------------------------------- durum
BOS_DURUM_BAYT = 80                    # bundan kısa bir durum dosyasında kayıt olamaz (tek kayıt bile daha uzundur)


def durum_coz(ham: bytes | None, anahtar: bytes | None = None) -> dict:
    """Durum dosyası → sözlük. Açılamayan dosya hata verir (paylaşılmış bir gönderiyi unutup yeniden paylaşmamak için); tek istisna, içinde kayıt
    olamayacak kadar kısa dosyadır (12.10'da bozuk yazılan boş durum): boş sayılır."""
    if not ham:
        return {}
    try:
        return json.loads(ac(ham, anahtar).decode("utf-8"))
    except Exception:                                              # noqa: BLE001
        if len(ham) < BOS_DURUM_BAYT:
            return {}
        raise SystemExit("durum dosyası açılamadı")


def durum_oku() -> dict:
    return durum_coz(dosya_oku(DURUM)[0])


def durum_yaz(d: dict) -> None:
    dosya_yaz(DURUM, paketle(json.dumps(d, ensure_ascii=False).encode("utf-8")), "durum")


def _simdi() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _zaman(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def sirasi_gelenler(kuyruk: dict, durum: dict, simdi: dt.datetime | None = None) -> tuple[list[dict], dict]:
    """(paylaşılacaklar, güncellenen durum). Paylaşılmış, yayınlanırken yarıda kalmış, üç kez denenmiş ya da çok gecikmiş gönderi alınmaz."""
    simdi = simdi or _simdi()
    out = []
    for o in kuyruk.get("ogeler", []):
        d = durum.get(o["id"]) or {}
        if d.get("d") in ("paylasildi", "yayinlaniyor", "kacti", "kuru-tamam") or int(d.get("deneme") or 0) >= DENEME_EN_COK:
            continue
        z = _zaman(o["zaman"])
        if z > simdi + dt.timedelta(seconds=ERKEN_SN):
            continue
        if simdi - z > dt.timedelta(hours=GEC_SAAT):
            durum[o["id"]] = {**d, "d": "kacti", "zaman": simdi.isoformat(timespec="seconds"), "mesaj": f"{GEC_SAAT} saatten fazla gecikti; paylaşılmadı"}
            continue
        if d.get("d") == "hata" and d.get("zaman"):                # az önce denendi ve olmadı: hemen yinelenmez
            try:
                if dt.timedelta(0) <= simdi - _zaman(d["zaman"]) < dt.timedelta(minutes=TEKRAR_DK):
                    continue
            except (ValueError, TypeError):
                pass
        out.append(o)
    return out, durum


# ---------------------------------------------------------------- 1 · hazırla
def hazirla() -> None:
    cikti = os.environ.get("GITHUB_OUTPUT")

    def bildir(var: bool) -> None:
        if cikti:
            with open(cikti, "a", encoding="utf-8") as f:
                f.write(f"var={'evet' if var else 'hayir'}\n")
    ham = Path(KUYRUK).read_bytes() if Path(KUYRUK).is_file() else None
    if not ham:
        print("kuyruk yok")
        return bildir(False)
    kuyruk = json.loads(ac(ham).decode("utf-8"))
    durum = durum_oku()
    once = json.dumps(durum, sort_keys=True)
    isler, durum = sirasi_gelenler(kuyruk, durum)
    if json.dumps(durum, sort_keys=True) != once:
        durum_yaz(durum)
    if not isler:
        print(f"sırası gelen yok ({len(kuyruk.get('ogeler', []))} gönderi kuyrukta)")
        return bildir(False)
    V, site, klasor = varliklar(), Path("site"), secrets.token_urlsafe(18)
    (site / klasor).mkdir(parents=True, exist_ok=True)
    (site / "index.html").write_text("<!doctype html><title>.</title>", encoding="utf-8")
    (site / ".nojekyll").write_text("", encoding="utf-8")
    hazir = []
    for o in isler:
        eksik = [f["ad"] for f in o["dosyalar"] if f["ad"] not in V]
        if eksik:
            d = durum.get(o["id"]) or {}
            durum[o["id"]] = {**d, "d": "hata", "deneme": int(d.get("deneme") or 0) + 1, "zaman": _simdi().isoformat(timespec="seconds"), "mesaj": "dosya depoda yok"}
            print(f"{o['id']}: dosya yok")
            continue
        for n, f in enumerate(o["dosyalar"]):
            sifreli = _gh("GET", f"/repos/{_depo()}/releases/assets/{V[f['ad']]['id']}", ham=True, kabul="application/octet-stream")
            f["yol"] = f"{klasor}/{o['id']}-{n}.{f['uz']}"
            (site / f["yol"]).write_bytes(coz(sifreli))
        hazir.append(o)
        print(f"{o['id']}: {len(o['dosyalar'])} dosya hazır")
    durum_yaz(durum)
    IS.write_text(json.dumps(hazir, ensure_ascii=False), encoding="utf-8")
    bildir(bool(hazir))


# ---------------------------------------------------------------- Instagram
class IGHata(Exception):
    pass


# Paylaşımın adımları: olmayan denemenin NEREDE kaldığı sonuca yazılır (13.10: üç Reels "API access blocked" ile döndü; hangi çağrıda olduğu
# kayıtta yoktu). Günlüğe de yalnız bu sabit sözler basılır.
ADIM_HESAP, ADIM_ADRES, ADIM_VER, ADIM_ISLE, ADIM_YAYIN = ("hesap okunurken", "dosyanın adresi beklenirken", "Instagram'a verilirken",
                                                           "Instagram dosyayı işlerken", "yayınlanırken")


def _ig(yontem: str, yol: str, alanlar: dict, anahtar: str) -> dict:
    veri = urllib.parse.urlencode({**{a: d for a, d in alanlar.items() if d is not None}, "access_token": anahtar}).encode("utf-8")
    r = urllib.request.Request(f"{IG}{yol}" + ("?" + veri.decode("ascii") if yontem == "GET" else ""), method=yontem, data=veri if yontem == "POST" else None,
                               headers={"User-Agent": "studyo-paylasim"})
    try:
        with urllib.request.urlopen(r, timeout=120) as y:
            return json.loads(y.read().decode("utf-8"))
    except urllib.error.HTTPError as h:
        try:
            e = (json.loads(h.read().decode("utf-8")).get("error") or {})
            mesaj = f"{e.get('code')}/{e.get('error_subcode')}: {str(e.get('error_user_msg') or e.get('message') or '')[:240]}"
        except ValueError:
            mesaj = f"HTTP {h.code}"
        raise IGHata(mesaj.replace(anahtar, "•"))
    except urllib.error.URLError:
        raise IGHata("Instagram'a ulaşılamadı")


def _bekle(kap: str, anahtar: str, sure: int) -> None:
    son = time.time() + sure
    while time.time() < son:
        d = _ig("GET", f"/{kap}", {"fields": "status_code,status"}, anahtar)
        if d.get("status_code") == "FINISHED":
            return
        if d.get("status_code") in ("ERROR", "EXPIRED"):
            raise IGHata(f"Instagram dosyayı işleyemedi: {str(d.get('status') or d.get('status_code'))[:200]}")
        time.sleep(6)
    raise IGHata("Instagram dosyayı zamanında işlemedi")


def adres_bekle(adres: str, sure: int = 240) -> None:
    son = time.time() + sure
    while time.time() < son:
        try:
            r = urllib.request.Request(adres, method="HEAD", headers={"User-Agent": "studyo-paylasim"})
            with urllib.request.urlopen(r, timeout=30) as y:
                if y.status == 200:
                    return
        except (urllib.error.HTTPError, urllib.error.URLError):
            pass
        time.sleep(5)
    raise IGHata("dosyanın adresi açılmadı")


def kapsayici(o: dict, taban: str, anahtar: str, kim: str, adim: dict | None = None) -> str:
    """Gönderinin Instagram kapsayıcısını kurar ve işlenmesini bekler → yayınlanmaya hazır kapsayıcı kimliği. Bu noktaya kadar yeniden denemek güvenli.
    adim["ad"]: o an yapılan iş (hata olursa nerede olduğu sonuca yazılır)."""
    adim = adim if adim is not None else {}
    adres = lambda f: taban.rstrip("/") + "/" + f["yol"]              # noqa: E731
    ortak = json.dumps(o.get("ortak")) if o.get("ortak") else None
    adim["ad"] = ADIM_ADRES
    for f in o["dosyalar"]:
        adres_bekle(adres(f))
    adim["ad"] = ADIM_VER
    if o["tur"] == "REELS":
        video = next(f for f in o["dosyalar"] if f["rol"] == "video")
        kapak = next((f for f in o["dosyalar"] if f["rol"] == "kapak"), None)
        k = _ig("POST", f"/{kim}/media", {"media_type": "REELS", "video_url": adres(video), "caption": o.get("aciklama") or "", "cover_url": adres(kapak) if kapak else None,
                                          "share_to_feed": "true" if o.get("akista", True) else "false", "collaborators": ortak}, anahtar)["id"]
    elif o["tur"] == "TEK":
        k = _ig("POST", f"/{kim}/media", {"image_url": adres(o["dosyalar"][0]), "caption": o.get("aciklama") or "", "alt_text": o.get("alt") or None, "collaborators": ortak}, anahtar)["id"]
    else:
        cocuk = []
        for f in o["dosyalar"]:
            c = _ig("POST", f"/{kim}/media", {"image_url": adres(f), "is_carousel_item": "true"}, anahtar)["id"]
            _bekle(c, anahtar, 120)
            cocuk.append(c)
        k = _ig("POST", f"/{kim}/media", {"media_type": "CAROUSEL", "children": ",".join(cocuk), "caption": o.get("aciklama") or "", "collaborators": ortak}, anahtar)["id"]
    adim["ad"] = ADIM_ISLE
    _bekle(k, anahtar, BEKLE_SN.get(o["tur"], 180))
    return k


# ---------------------------------------------------------------- TikTok (Buffer üzerinden; 29.10)
# Kanal sahibi (29.10): "tamam önerdiğin yolu yapalım. haftada 3 tane shorts yükleyeceğiz." TikTok kendi bağlantısıyla denetimsiz uygulamanın herkese
# açık paylaşmasına izin vermiyor; Buffer TikTok'un onaylı ortağı. Buffer dosya almıyor, videoyu herkese açık bir adresten ÇEKİYOR ve belgesine göre
# bunu PAYLAŞIM ANINDA yapıyor (developers.buffer.com/guides/hosting-media: "Buffer fetches the media when the post publishes"); aynı belge açılamayan
# adresin gönderi kurulurken de hata verdiğini söylüyor. Video günlerce açık adreste durmasın diye Buffer'a önceden planlı gönderi KURULMAZ: saati
# gelince bu yayıncı (Instagram'daki gibi) videoyu birkaç dakikalığına açar, Buffer'a "şimdi paylaş" (mode: shareNow) der, Buffer'ın işi bitirmesini
# bekler, sonra adres kapanır. Zamanlama Buffer'da değil GitHub'da (bilgisayar kapalı olabilir).
BF_OLUSTUR = """mutation Olustur($input: CreatePostInput!) {
  createPost(input: $input) {
    ... on PostActionSuccess { post { id status } }
    ... on MutationError { message }
  }
}"""
BF_GONDERI = """query Gonderi($input: PostInput!) {
  post(input: $input) { id status dueAt sentAt externalLink error { message } }
}"""
BF_SIL = """mutation Sil($input: DeletePostInput!) {
  deletePost(input: $input) {
    ... on DeletePostSuccess { id }
    ... on MutationError { message }
  }
}"""
ADIM_BF_VER, ADIM_BF_ISLE = "Buffer'a verilirken", "Buffer TikTok'a gönderirken"


class BFHata(Exception):
    """kesin: Buffer'dan yanıt geldi ve gönderi KURULMADI (yeniden denemek güvenli). kesin değil: yanıt alınamadı (kurulmuş olabilir)."""
    def __init__(self, mesaj: str, kesin: bool = True):
        super().__init__(mesaj)
        self.kesin = kesin


def bf(sorgu: str, degisken: dict, anahtar: str) -> dict:
    """Buffer GraphQL çağrısı → data. GraphQL her zaman HTTP 200 döner; hata gövdedeki "errors" dizisindedir (guides/error-handling)."""
    r = urllib.request.Request(BUFFER, method="POST", data=json.dumps({"query": sorgu, "variables": degisken}).encode("utf-8"),
                               headers={"Content-Type": "application/json", "User-Agent": "studyo-paylasim"})
    r.add_unredirected_header("Authorization", f"Bearer {anahtar}")
    try:
        with urllib.request.urlopen(r, timeout=120) as y:
            v = json.loads(y.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as h:
        raise BFHata(f"Buffer HTTP {h.code}", kesin=h.code in (400, 401, 403, 404, 429))
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise BFHata("Buffer'a ulaşılamadı", kesin=False)
    if v.get("errors"):
        e = v["errors"][0] if isinstance(v["errors"], list) and v["errors"] else {}
        kod = ((e.get("extensions") or {}).get("code")) or ""
        raise BFHata(f"{kod + ': ' if kod else ''}{str(e.get('message') or '')[:200]}".replace(anahtar, "•"))
    return v.get("data") or {}


def buffer_girdi(o: dict, video_url: str) -> dict:
    """createPost'un girdisi (reference.html → CreatePostInput, VideoAssetInput, TikTokPostMetadataInput). needsApproval belgede zorunlu (Boolean!)
    görünüyor, örnekler yazmıyor: açıkça false gönderilir."""
    video: dict = {"url": video_url}
    if o.get("kapak_ms"):
        video["metadata"] = {"thumbnailOffset": int(o["kapak_ms"])}       # TikTok'ta kapak karesi (ms); verilmezse Buffer'ın varsayılanı
    girdi = {"channelId": o["bf_kanal"], "text": o.get("aciklama") or "", "schedulingType": "automatic", "mode": "shareNow",
             "needsApproval": False, "assets": [{"video": video}], "source": "studyo"}
    if o.get("yz") is not None:
        girdi["metadata"] = {"tiktok": {"isAiGenerated": bool(o["yz"])}}  # TikTok'un yapay zekâ etiketi (yalnız video)
    return girdi


def tiktok_paylas(o: dict, taban: str, durum: dict, kayit: dict) -> bool:
    """Bir TikTok gönderisi. Dönen: olmadı mı. Durum her adımda yazılır; Buffer'a "şimdi paylaş" denmeden hemen önce `yayinlaniyor`: o andan sonra
    yarıda kalan gönderi yeniden DENENMEZ (TikTok'ta iki kez çıkmasın), stüdyo sonucu Buffer'dan kendisi okur."""
    adim = ADIM_ADRES
    try:
        anahtar = os.environ.get("BUFFER_" + o["kanal"].upper().replace("-", "_")) or ""
        if not anahtar:
            raise BFHata("bu kanalın Buffer anahtarı yok")
        video = next(f for f in o["dosyalar"] if f["rol"] == "video")
        adres = taban.rstrip("/") + "/" + video["yol"]
        adres_bekle(adres)
        adim = ADIM_BF_VER
        durum[o["id"]] = {**kayit, "d": "yayinlaniyor", "mesaj": ""}
        durum_yaz(durum)
        v = (bf(BF_OLUSTUR, {"input": buffer_girdi(o, adres)}, anahtar).get("createPost")) or {}
        if not (v.get("post") or {}).get("id"):
            raise BFHata(str(v.get("message") or "Buffer gönderiyi kurmadı")[:240])
        pid = v["post"]["id"]
        durum[o["id"]] = {**kayit, "d": "yayinlaniyor", "buffer": pid, "mesaj": ""}
        durum_yaz(durum)
        adim, son, p = ADIM_BF_ISLE, time.time() + BUFFER_BEKLE_SN, {}
        while time.time() < son:
            time.sleep(15)
            try:
                p = bf(BF_GONDERI, {"input": {"id": pid}}, anahtar).get("post") or {}
            except BFHata:
                continue
            if p.get("status") in ("sent", "error", "draft", "needs_approval"):
                break
        if p.get("status") == "sent":
            durum[o["id"]] = {**kayit, "d": "paylasildi", "buffer": pid, "baglanti": p.get("externalLink") or "", "mesaj": ""}
            print(f"{o['id']}: paylaşıldı", flush=True)
            return False
        if p.get("status") == "error":                                 # TikTok ya da Buffer geri çevirdi: kendiliğinden yinelenmez (yeni saat verilince yeniden)
            durum[o["id"]] = {**kayit, "d": "hata", "deneme": DENEME_EN_COK, "buffer": pid, "adim": adim,
                              "mesaj": f"{adim} · {str((p.get('error') or {}).get('message') or 'Buffer hata verdi')[:240]}"}
        elif p.get("status") in ("draft", "needs_approval"):
            durum[o["id"]] = {**kayit, "d": "hata", "deneme": DENEME_EN_COK, "buffer": pid, "adim": adim,
                              "mesaj": f"{adim} · Buffer gönderiyi taslak / onay bekleyen olarak aldı; Buffer'da bak"}
        else:                                                          # süre doldu: Buffer hâlâ çalışıyor olabilir; stüdyo sonucu Buffer'dan okur
            durum[o["id"]] = {**kayit, "d": "yayinlaniyor", "buffer": pid, "adim": adim, "mesaj": f"{adim} · Buffer {BUFFER_BEKLE_SN // 60} dakikada bitirmedi"}
        print(f"{o['id']}: olmadı ({adim})", flush=True)
        return True
    except BFHata as h:
        kesin = h.kesin or adim == ADIM_ADRES
        durum[o["id"]] = {**kayit, "d": "hata" if kesin else "yayinlaniyor", "adim": adim, "mesaj": f"{adim} · {h}"[:300]}
        print(f"{o['id']}: olmadı ({adim})", flush=True)
        return True
    except IGHata as h:                                                # adres_bekle: dosyanın adresi açılmadı
        durum[o["id"]] = {**kayit, "d": "hata", "adim": adim, "mesaj": f"{adim} · {h}"[:300]}
        print(f"{o['id']}: olmadı ({adim})", flush=True)
        return True


# ---------------------------------------------------------------- 2 · paylaş
def paylas() -> None:
    isler = json.loads(IS.read_text(encoding="utf-8")) if IS.is_file() else []
    taban = os.environ["SITE"]
    durum = durum_oku()
    hata = 0
    for o in isler:
        d = durum.get(o["id"]) or {}
        kayit = {**d, "deneme": int(d.get("deneme") or 0) + 1, "zaman": _simdi().isoformat(timespec="seconds")}
        if o.get("tur") == "TIKTOK" and not o.get("kuru"):
            hata += tiktok_paylas(o, taban, durum, kayit)
            durum_yaz(durum)
            continue
        adim = {"ad": ADIM_ADRES if o.get("kuru") else ADIM_HESAP}
        try:
            if o.get("kuru"):                                      # kuru deneme: Instagram'a gidilmez; yalnız dosyaların adresi açılıyor mu
                for f in o["dosyalar"]:
                    adres_bekle(taban.rstrip("/") + "/" + f["yol"])
                durum[o["id"]] = {**kayit, "d": "kuru-tamam", "mesaj": ""}
                print(f"{o['id']}: kuru deneme tamam", flush=True)
                durum_yaz(durum)
                continue
            anahtar = os.environ.get("IG_" + o["kanal"].upper().replace("-", "_")) or ""
            if not anahtar:
                raise IGHata("bu kanalın Instagram anahtarı yok")
            ben = _ig("GET", "/me", {"fields": "user_id,username"}, anahtar)
            kim = str(ben.get("user_id") or ben.get("id"))
            durum[o["id"]] = {**kayit, "d": "hazirlaniyor", "mesaj": ""}
            k = kapsayici(o, taban, anahtar, kim, adim)
            adim["ad"] = ADIM_YAYIN
            durum[o["id"]] = {**kayit, "d": "yayinlaniyor", "mesaj": ""}
            durum_yaz(durum)                                       # bu satırdan sonra yarıda kalan gönderi yeniden denenmez (çift paylaşım olmasın)
            m = _ig("POST", f"/{kim}/media_publish", {"creation_id": k}, anahtar)["id"]
            bag = ""
            try:
                bag = _ig("GET", f"/{m}", {"fields": "permalink"}, anahtar).get("permalink") or ""
            except IGHata:
                pass
            durum[o["id"]] = {**kayit, "d": "paylasildi", "medya": m, "baglanti": bag, "mesaj": ""}
            print(f"{o['id']}: paylaşıldı", flush=True)
        except IGHata as h:
            hata += 1
            onceki = (durum.get(o["id"]) or {}).get("d")
            durum[o["id"]] = {**kayit, "d": "yayinlaniyor" if onceki == "yayinlaniyor" else "hata", "adim": adim["ad"], "mesaj": f"{adim['ad']} · {h}"[:300]}
            print(f"{o['id']}: olmadı ({adim['ad']})", flush=True)  # sabit söz: içerik, açıklama, adres basılmaz
        durum_yaz(durum)
    V = varliklar()                                                # paylaşılan gönderinin şifreli dosyaları depodan silinir
    for o in isler:
        if (durum.get(o["id"]) or {}).get("d") in ("paylasildi", "kuru-tamam"):
            for f in o["dosyalar"]:
                if f["ad"] in V:
                    _gh("DELETE", f"/repos/{_depo()}/releases/assets/{V[f['ad']]['id']}")
    if hata:
        sys.exit(1)


if __name__ == "__main__":
    {"hazirla": hazirla, "paylas": paylas}[sys.argv[1]]()
