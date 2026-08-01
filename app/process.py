"""
Pipeline "Opzione A": riusa boccetta + unghia REALI di ogni foto originale,
le isola (cutout, senza IA generativa) e le ricompone in un layout uniforme
su sfondo bianco. Nessuna alterazione di forma/testo/colore: solo riposizionamento.

A differenza di un taglio "a meta' fissa" (che rischia di tagliare la boccetta
in due), qui il cutout viene fatto sull'immagine INTERA, poi i soggetti isolati
vengono raggruppati per posizione (sinistra = boccetta, destra = unghia/e) in
base al vuoto piu' ampio tra un soggetto e l'altro. Ogni gruppo viene poi
scalato rispettando sia altezza che larghezza della zona (contain-fit), cosi'
non viene mai tagliato.

Input:  backup/manifest/manifest.json + backup/images/*
Output: processed/<product_id>_<sku>.png  (layout uniforme)
        processed/preview/<product_id>_<sku>_compare.png (originale vs risultato, per revisione)
"""
import json
from pathlib import Path

import numpy as np
from PIL import Image
from rembg import remove, new_session
from scipy import ndimage

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "backup" / "manifest" / "manifest.json"
PROCESSED_DIR = ROOT / "processed"
PREVIEW_DIR = PROCESSED_DIR / "preview"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

# Canvas di output uniforme per tutti i prodotti
CANVAS_W, CANVAS_H = 1600, 1000
BG_COLOR = (255, 255, 255, 255)

LEFT_ZONE = (40, 40, CANVAS_W // 2 - 20, CANVAS_H - 40)
RIGHT_ZONE = (CANVAS_W // 2 + 20, 40, CANVAS_W - 40, CANVAS_H - 40)

# Margine di sicurezza: il soggetto occupa al massimo questa frazione della zona
CONTAIN_RATIO = 0.92

# Componenti piu' piccole di questa frazione dell'area totale del soggetto principale
# sono considerate rumore del cutout e scartate
MIN_COMPONENT_AREA_RATIO = 0.01

_session = new_session("isnet-general-use")


def cutout_full(image: Image.Image) -> Image.Image:
    """Rimuove lo sfondo dall'immagine intera, ritorna RGBA con soggetti isolati."""
    return remove(image, session=_session).convert("RGBA")


def find_components(rgba: Image.Image, alpha_threshold: int = 20):
    """Trova le componenti connesse (soggetti) nel canale alpha e i loro bbox/centroidi."""
    alpha = np.array(rgba.split()[-1])
    mask = alpha > alpha_threshold
    labeled, n = ndimage.label(mask)
    if n == 0:
        return []

    components = []
    areas = ndimage.sum(mask, labeled, index=range(1, n + 1))
    max_area = areas.max()
    for i in range(1, n + 1):
        area = areas[i - 1]
        if area < max_area * MIN_COMPONENT_AREA_RATIO:
            continue
        ys, xs = np.where(labeled == i)
        components.append({
            "bbox": (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1),
            "centroid_x": float(xs.mean()),
            "area": float(area),
        })
    return components


def split_into_two_groups(components):
    """Divide le componenti in due gruppi (sinistra/destra) trovando il vuoto
    orizzontale piu' ampio tra i centroidi ordinati."""
    if len(components) <= 1:
        return components, []

    sorted_comps = sorted(components, key=lambda c: c["centroid_x"])
    gaps = [
        (sorted_comps[i + 1]["centroid_x"] - sorted_comps[i]["centroid_x"], i)
        for i in range(len(sorted_comps) - 1)
    ]
    _, split_idx = max(gaps)
    left_group = sorted_comps[: split_idx + 1]
    right_group = sorted_comps[split_idx + 1:]
    return left_group, right_group


def union_bbox(components):
    x0 = min(c["bbox"][0] for c in components)
    y0 = min(c["bbox"][1] for c in components)
    x1 = max(c["bbox"][2] for c in components)
    y1 = max(c["bbox"][3] for c in components)
    return x0, y0, x1, y1


def paste_contain_fit(canvas: Image.Image, subject_rgba: Image.Image, bbox, zone):
    zx0, zy0, zx1, zy1 = zone
    zone_w = zx1 - zx0
    zone_h = zy1 - zy0

    cropped = subject_rgba.crop(bbox)
    max_w = int(zone_w * CONTAIN_RATIO)
    max_h = int(zone_h * CONTAIN_RATIO)

    scale = min(max_w / cropped.width, max_h / cropped.height)
    target_w = max(1, int(cropped.width * scale))
    target_h = max(1, int(cropped.height * scale))
    resized = cropped.resize((target_w, target_h), Image.LANCZOS)

    paste_x = zx0 + (zone_w - target_w) // 2
    paste_y = zy0 + (zone_h - target_h) // 2
    canvas.alpha_composite(resized, (paste_x, paste_y))


def process_one(entry: dict) -> Path:
    src_path = ROOT / entry["backup_local_path"]
    img = Image.open(src_path).convert("RGB")

    cut = cutout_full(img)
    components = find_components(cut)

    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), BG_COLOR)

    if not components:
        raise ValueError("nessun soggetto rilevato dopo il cutout")

    left_group, right_group = split_into_two_groups(components)

    if left_group:
        paste_contain_fit(canvas, cut, union_bbox(left_group), LEFT_ZONE)
    if right_group:
        paste_contain_fit(canvas, cut, union_bbox(right_group), RIGHT_ZONE)

    out_name = Path(entry["backup_local_path"]).stem + ".png"
    out_path = PROCESSED_DIR / out_name
    canvas.convert("RGB").save(out_path, quality=95)

    # Preview affiancata originale/risultato per revisione rapida
    preview = Image.new("RGB", (img.width + CANVAS_W, max(img.height, CANVAS_H)), (240, 240, 240))
    preview.paste(img, (0, 0))
    preview.paste(canvas.convert("RGB"), (img.width, 0))
    preview.save(PREVIEW_DIR / out_name)

    return out_path


def main():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    results = []
    for entry in manifest:
        if entry["status"] != "ok":
            continue
        try:
            out_path = process_one(entry)
            print(f"[ok] {entry['product_id']} - {entry['name']} -> {out_path.name}")
            results.append({**entry, "processed_path": str(out_path.relative_to(ROOT)), "process_status": "ok"})
        except Exception as e:
            print(f"[ERRORE] {entry['product_id']} - {entry['name']}: {e}")
            results.append({**entry, "process_status": f"errore: {e}"})

    out_manifest = ROOT / "processed" / "process_manifest.json"
    out_manifest.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nFatto. Manifest elaborazione: {out_manifest}")


if __name__ == "__main__":
    main()
