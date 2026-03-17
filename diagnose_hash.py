"""
Diagnostic: compare debug_crop.jpg against the database and show the top 5 closest matches.
Run: python diagnose_hash.py
"""
import sqlite3
import imagehash
from PIL import Image, ImageOps

DB_PATH = "db/cards.db"
CROP_PATH = "debug_crop.jpg"
HASH_SIZE = 16


def compute(img: Image.Image) -> imagehash.ImageHash:
    return imagehash.phash(ImageOps.autocontrast(img.convert("L")), hash_size=HASH_SIZE)


img = Image.open(CROP_PATH)
query = compute(img)
print(f"Query hash: {query}")
print(f"Crop size:  {img.size}\n")

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT id, name, set_name, number, phash FROM cards").fetchall()

results = []
for row in rows:
    try:
        db_hash = imagehash.hex_to_hash(row["phash"])
        dist = query - db_hash
        results.append((dist, row))
    except Exception:
        continue

results.sort(key=lambda x: x[0])

print(f"Top 5 closest matches (out of {len(results)} cards):")
print("-" * 60)
for dist, row in results[:5]:
    print(f"  dist={dist:3d}  {row['name']} [{row['set_name']} #{row['number']}]  id={row['id']}")

print()
best_dist, best_row = results[0]
ref_path = f"db/images/{best_row['id']}.jpg"
import os
if os.path.exists(ref_path):
    ref_img = Image.open(ref_path)
    ref_hash = compute(ref_img)
    print(f"\nBest match hash: {ref_hash}")
    print(f"Query hash:      {query}")
    print(f"Distance:        {best_dist}")
    print(f"Ref image size:  {ref_img.size}")
    ref_img.save("debug_ref.jpg")
    print("Saved reference image to debug_ref.jpg for visual comparison")
else:
    print(f"\n(Reference image not found at {ref_path})")

conn.close()
