import imagehash
from PIL import Image, ImageOps


HASH_SIZE = 16  # 256-bit phash


def compute_phash(image: Image.Image) -> imagehash.ImageHash:
    """
    Compute a perceptual hash of a PIL Image.
    Normalises contrast first to reduce sensitivity to lighting variation.
    """
    normalised = ImageOps.autocontrast(image.convert("L"))
    return imagehash.phash(normalised, hash_size=HASH_SIZE)


def hash_to_str(h: imagehash.ImageHash) -> str:
    return str(h)


def str_to_hash(s: str) -> imagehash.ImageHash:
    return imagehash.hex_to_hash(s)


def hamming(a: imagehash.ImageHash, b: imagehash.ImageHash) -> int:
    return a - b
