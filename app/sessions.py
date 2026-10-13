"""
Utenti, codici e sessioni dell'editor (login semplice ma efficace) — lato telefono (live_server.py).

Idea:
  * Ogni utente ha un CODICE personale. Dal codice si ricava una chiave con PBKDF2 (600.000 giri, sale fisso per utente):
    la stessa derivazione la fa il browser (docs/sessione.js). Il codice non viaggia mai: viaggiano solo firme HMAC
    fatte con la chiave derivata, che il telefono ricalcola e confronta.
  * Il login e' un evento firmato ("login") che il browser manda nella coda GitHub: il telefono lo verifica, apre la sessione
    e avvisa su Telegram. Le modifiche al sito sono accettate SOLO se l'utente ha una sessione attiva.
  * La sessione si chiude: a mano ("logout"), dopo N modifiche fatte (SESSION_MAX_ACTIONS) o dopo troppa inattivita'
    (SESSION_IDLE_MIN). In ogni caso parte un riepilogo su Telegram con tutto quello che l'utente ha fatto.

Solo libreria standard: si testa offline (test_sessions.py).
"""
import hashlib
import hmac
import json
import threading
import time
from pathlib import Path

ITERATIONS = 600_000
DEFAULT_MAX_ACTIONS = 30      # deve coincidere con maxActions in docs/utenti.js (lo scrive gen_utenti.py)
DEFAULT_IDLE_MIN = 30


# ------------------------------------------------------------------ chiavi
def derive_key(code: str, user_id: str, iterations: int = ITERATIONS) -> str:
    """Chiave (hex) ricavata dal codice. Deve coincidere con deriveKey() in docs/sessione.js."""
    return hashlib.pbkdf2_hmac("sha256", code.encode("utf-8"), f"mnp-editor|{user_id}".encode("utf-8"), iterations).hex()


def verifier(key_hex: str) -> str:
    """Impronta pubblica della chiave: nel browser serve solo a dire 'codice giusto/sbagliato' prima di mandare il login."""
    return hashlib.sha256(("verify|" + key_hex).encode("utf-8")).hexdigest()


def parse_users(spec: str, iterations: int = ITERATIONS) -> dict:
    """spec = 'miriam=CODICE;marco=CODICE'  ->  {id: {"name", "key"}}  (le chiavi si calcolano qui, una volta sola)."""
    users = {}
    for part in (spec or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        uid, code = part.split("=", 1)
        uid = uid.strip().lower()
        if uid and code:
            users[uid] = {"name": uid.capitalize(), "key": derive_key(code, uid, iterations)}
    return users


# ------------------------------------------------------------------ firme
def canon(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def signable(obj: dict) -> dict:
    """L'oggetto firmato senza i dati delle foto (troppo grandi): contano testi, prezzi, id, azioni e URL delle foto vecchie."""
    out = dict(obj)
    out.pop("image_base64", None)
    out.pop("image_mime", None)
    if isinstance(out.get("images"), list):
        out["images"] = [{k: v for k, v in im.items() if k not in ("image_base64", "image_mime")} for im in out["images"]]
    return out


def sign(key_hex: str, user: str, ts: int, obj: dict) -> str:
    return hmac.new(key_hex.encode("utf-8"), f"{user}|{ts}|{canon(signable(obj))}".encode("utf-8"), hashlib.sha256).hexdigest()


def verify(users: dict, payload: dict, obj: dict, max_age=900, seen: set | None = None, now=None) -> str | None:
    """Ritorna l'id dell'utente se la firma e' valida (utente noto, richiesta recente, mai vista prima), altrimenti None."""
    try:
        user = str(payload["user"]).lower()
        ts = int(payload["ts"])
        sig = str(payload["sig"])
    except Exception:
        return None
    u = users.get(user)
    if not u:
        return None
    if abs((now or time.time()) - ts) > max_age:
        return None
    if seen is not None and sig in seen:
        return None
    if not hmac.compare_digest(sign(u["key"], user, ts, obj), sig):
        return None
    if seen is not None:
        seen.add(sig)
    return user


# ------------------------------------------------------------------ sessioni
class Sessions:
    """Sessioni attive per utente. Tutto in memoria + file JSON (sopravvive a un riavvio del telefono)."""

    def __init__(self, path: Path | None = None, max_actions=DEFAULT_MAX_ACTIONS, idle_min=DEFAULT_IDLE_MIN, clock=time.time):
        self.path = Path(path) if path else None
        self.max_actions = int(max_actions)
        self.idle_s = int(idle_min) * 60
        self.clock = clock
        self.lock = threading.RLock()
        self.data = {}
        if self.path and self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self.data = {}

    def _save(self):
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")

    def _summary(self, s: dict, reason: str) -> dict:
        end = self.clock()
        return {"user": s["user"], "started": s["started"], "ended": end, "duration_s": int(end - s["started"]),
                "reason": reason, "actions": list(s["actions"]), "device": s.get("device", ""), "relogin": s.get("relogin", False)}

    def login(self, user: str, device: str = "") -> dict:
        """Apre la sessione. Se ce n'era gia' una la sostituisce e ne restituisce il riepilogo in 'previous'."""
        with self.lock:
            prev = self._summary(self.data[user], "nuovo login") if user in self.data else None
            now = self.clock()
            self.data[user] = {"user": user, "started": now, "last": now, "actions": [], "device": (device or "")[:120]}
            self._save()
            return {"session": dict(self.data[user]), "previous": prev}

    def active(self, user: str) -> dict | None:
        """La sessione se e' attiva e non scaduta, altrimenti None (la scadenza vera la chiude sweep())."""
        with self.lock:
            s = self.data.get(user)
            if not s or self.clock() - s["last"] > self.idle_s:
                return None
            return s

    def touch(self, user: str):
        with self.lock:
            if user in self.data:
                self.data[user]["last"] = self.clock()
                self._save()

    def record_action(self, user: str, text: str) -> dict:
        """Registra una modifica riuscita. Ritorna {'count','max','closed','summary'}: se raggiunto il limite la sessione si chiude."""
        with self.lock:
            s = self.data[user]
            s["actions"].append({"t": self.clock(), "text": text})
            s["last"] = self.clock()
            n = len(s["actions"])
            if n >= self.max_actions:
                summary = self._summary(s, f"limite di {self.max_actions} modifiche raggiunto")
                del self.data[user]
                self._save()
                return {"count": n, "max": self.max_actions, "closed": True, "summary": summary}
            self._save()
            return {"count": n, "max": self.max_actions, "closed": False, "summary": None}

    def logout(self, user: str, reason: str = "uscita volontaria") -> dict | None:
        with self.lock:
            if user not in self.data:
                return None
            summary = self._summary(self.data[user], reason)
            del self.data[user]
            self._save()
            return summary

    def sweep(self) -> list:
        """Chiude le sessioni inattive da troppo tempo e ne ritorna i riepiloghi (per l'avviso su Telegram)."""
        with self.lock:
            out = []
            for user in list(self.data):
                if self.clock() - self.data[user]["last"] > self.idle_s:
                    out.append(self._summary(self.data[user], f"inattivita' (oltre {self.idle_s // 60} minuti senza attivita')"))
                    del self.data[user]
            if out:
                self._save()
            return out
