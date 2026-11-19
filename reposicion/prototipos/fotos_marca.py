"""Fotos institucionales de Pret a Home ("¿Quiénes somos?" y "Preguntas frecuentes") que NUNCA se copian a la publicación de
un revendedor (pedido del usuario, 18/11/2026).

En las publicaciones de Pret cada color las lleva subidas aparte (cada copia tiene otro id de foto), así que se reconocen por la
imagen: huella visual (dHash 16x16 = 256 bits) comparada con la de las dos imágenes originales. Medido el 18/11/2026 sobre 13
publicaciones de Pret (~240 fotos): las de marca quedan a 1–6 bits, cualquier otra foto a 90 o más. Umbral: 30.
Para sumar otra imagen a excluir: calcular `dhash()` de la imagen original y agregarla a HUELLAS."""
import io
import urllib.request

from PIL import Image

HUELLAS = {
    "quienes_somos": 0x394b2f453e4d465d5b753b757b554e7573351b311b711b9d32072201ccc146c1,
    "preguntas_frecuentes": 0x6069176d077d167d17e513ed07c54ae102e506ed264d6655326fa24f4c4d4263,
}
UMBRAL = 30


def dhash(img, n=16):
    g = img.convert("L").resize((n + 1, n), Image.LANCZOS)
    px = list(g.getdata())
    bits = 0
    for y in range(n):
        for x in range(n):
            bits = (bits << 1) | (px[y * (n + 1) + x] > px[y * (n + 1) + x + 1])
    return bits


def es_foto_de_marca(picture_id):
    """True si la foto de ML `picture_id` es una de las institucionales. Ante un error de descarga devuelve None (quien llama decide)."""
    try:
        raw = urllib.request.urlopen(f"https://http2.mlstatic.com/D_{picture_id}-I.jpg", timeout=30).read()
        h = dhash(Image.open(io.BytesIO(raw)))
    except Exception:
        return None
    return min(bin(h ^ v).count("1") for v in HUELLAS.values()) <= UMBRAL


def sin_fotos_de_marca(picture_ids):
    """Devuelve (fotos a copiar, fotos descartadas). Si una foto no se pudo revisar, NO se copia (mejor una foto menos que
    publicar una de marca)."""
    quedan, fuera = [], []
    for pid in picture_ids:
        (quedan if es_foto_de_marca(pid) is False else fuera).append(pid)
    return quedan, fuera
