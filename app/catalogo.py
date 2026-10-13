"""
Catalogo prodotti per la pagina di Miriam (docs/data/catalogo.json).

Perche': l'API pubblica dei prodotti (WooCommerce Store API) non si puo' leggere dal browser di una pagina su
GitHub Pages (manca l'intestazione CORS), quindi prezzi, tipo e galleria arrivano da questo file, generato qui
dalla stessa API pubblica (nessuna credenziale necessaria, sola lettura).

Uso da riga di comando:   python catalogo.py <file_di_uscita.json> [https://mynailsprofessional.it]
Dal telefono (live_server.py): build_catalog(site) + push_catalog(...) dopo ogni modifica ai prodotti.
"""
import base64
import html
import json
import sys
import time

import requests

UA = {"User-Agent": "Mozilla/5.0"}


def _minor(value, prices):
    if value in ("", None):
        return ""
    return f"{int(value) / 10 ** int(prices.get('currency_minor_unit', 2)):.2f}"


def build_catalog(site: str, timeout=60) -> dict:
    site = site.rstrip("/")
    products, page = [], 1
    while True:
        r = requests.get(f"{site}/wp-json/wc/store/v1/products", params={"per_page": 100, "page": page}, headers=UA, timeout=timeout)
        r.raise_for_status()
        for p in r.json():
            pr = p.get("prices") or {}
            reg = _minor(pr.get("regular_price"), pr)
            sale = _minor(pr.get("sale_price"), pr) if p.get("on_sale") else ""
            products.append({
                "id": p["id"],
                "name": html.unescape(p.get("name", "")),
                "type": p.get("type", "simple"),
                "reg": reg,
                "sale": sale,
                "link": p.get("permalink", ""),
                "cats": [html.unescape(c.get("name", "")) for c in p.get("categories", [])],
                "imgs": [{"src": i["src"], "thumb": i.get("thumbnail") or i["src"]} for i in p.get("images", [])],
            })
        if page >= int(r.headers.get("X-WP-TotalPages", 1)):
            break
        page += 1
    products.sort(key=lambda x: x["name"].lower())
    return {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"), "count": len(products), "products": products}


def catalog_bytes(cat: dict) -> bytes:
    return json.dumps(cat, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def push_catalog(cat: dict, repo: str, branch: str, headers: dict, path="docs/data/catalogo.json", timeout=30) -> bool:
    """Scrive il catalogo nel repository GitHub (se e' cambiato) con l'API contents. True se ha scritto."""
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    r = requests.get(url, headers=headers, params={"ref": branch}, timeout=timeout)
    sha = None
    if r.status_code == 200:
        sha = r.json()["sha"]
        old = requests.get(f"https://api.github.com/repos/{repo}/git/blobs/{sha}", headers=headers, timeout=timeout)
        try:
            prev = json.loads(base64.b64decode(old.json()["content"]))
            prev.pop("generated", None)
            now = dict(cat)
            now.pop("generated", None)
            if prev == now:
                return False
        except Exception:
            pass
    elif r.status_code != 404:
        r.raise_for_status()
    body = {"message": "catalogo: aggiornamento automatico", "branch": branch,
            "content": base64.b64encode(catalog_bytes(cat)).decode()}
    if sha:
        body["sha"] = sha
    w = requests.put(url, headers=headers, json=body, timeout=timeout)
    w.raise_for_status()
    return True


if __name__ == "__main__":
    out = sys.argv[1]
    site = sys.argv[2] if len(sys.argv) > 2 else "https://mynailsprofessional.it"
    c = build_catalog(site)
    open(out, "wb").write(catalog_bytes(c))
    print(f"catalogo: {c['count']} prodotti -> {out}")
