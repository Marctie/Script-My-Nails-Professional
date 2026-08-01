"""
Pipeline "Opzione A": riusa boccetta + unghia REALI di ogni foto originale,
le isola (cutout, senza IA generativa) e le ricompone in un layout uniforme
su sfondo bianco. Nessuna alterazione di forma/testo/colore: solo riposizionamento.

Input:  backup/manifest/manifest.json + backup/images/*
Output: processed/<product_id>_<sku>.png  (layout uniforme)
        processed/preview/<product_id>_<sku>_compare.png (originale vs risultato, per revisione)
"""
import json
from pathlib import Path

from PIL import Image
from rembg import remove, new_session

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "backup" / "manifest" / "manifest.json"
PROCESSED_DIR = ROOT / "processed"
PREVIEW_DIR = PROCESSED_DIR / "preview"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

# Canvas di output uniforme per tutti i prodotti
CANVAS_W, CANVAS_H = 1600, 1000
BG_COLOR = (255, 255, 255, 255)

# Divisione orizzontale: boccetta a sinistra, unghia a destra (come nelle foto attuali)
LEFT_ZONE = (0, 0, CANVAS_W // 2, CANVAS_H)
RIGHT_ZONE = (CANVAS_W // 2, 0, CANVAS_W, CANVAS_H)

# Margini e altezza target di ciascun soggetto rispetto alla propria zona
SUBJECT_HEIGHT_RATIO = 0.85

_session = new_session("isnet-general-use")


def cutout(image: Image.Image) -> Image.Image:
    """Rimuove lo sfondo, ritorna immagine RGBA con soggetto isolato."""
    return remove(image, session=_session)


def split_left_right(img: Image.Image):
    """Divide l'immagine originale in meta' sinistra (boccetta) e destra (unghia)."""
    w, h = img.size
    left = img.crop((0, 0, w // 2, h))
    right = img.crop((w // 2, 0, w, h))
    return left, right


def bbox_of_subject(rgba: Image.Image):
    """Restituisce il bounding box del soggetto (pixel non trasparenti)."""
    alpha = rgba.split()[-1]
    bbox = alpha.getbbox()
    return bbox


def paste_centered_in_zone(canvas: Image.Image, subject_rgba: Image.Image, zone):
    zx0, zy0, zx1, zy1 = zone
    zone_w = zx1 - zx0
    zone_h = zy1 - zy0

    bbox = bbox_of_subject(subject_rgba)
    if bbox is None:
        return
    cropped = subject_rgba.crop(bbox)

    target_h = int(zone_h * SUBJECT_HEIGHT_RATIO)
    scale = target_h / cropped.height
    target_w = int(cropped.width * scale)
    resized = cropped.resize((target_w, target_h), Image.LANCZOS)

    paste_x = zx0 + (zone_w - target_w) // 2
    paste_y = zy0 + (zone_h - target_h) // 2
    canvas.alpha_composite(resized, (paste_x, paste_y))


def process_one(entry: dict) -> Path:
    src_path = ROOT / entry["backup_local_path"]
    img = Image.open(src_path).convert("RGB")

    left_half, right_half = split_left_right(img)
    left_cut = cutout(left_half).convert("RGBA")
    right_cut = cutout(right_half).convert("RGBA")

    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), BG_COLOR)
    paste_centered_in_zone(canvas, left_cut, LEFT_ZONE)
    paste_centered_in_zone(canvas, right_cut, RIGHT_ZONE)

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
