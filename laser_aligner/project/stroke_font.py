"""Original geometric single-stroke alphabet, independent of installed fonts/Qt.

Coordinates use a 4 x 6 cap-height grid with Y down. Spaces separate pen-down
strokes, pairs encode vertices. Lowercase has a four-unit x-height and optional
descenders. No contour tracing or implicit closing segment is involved.
"""

STROKE_FONT_NAME = "E3 Single Stroke"

_GLYPHS = {
    "A": "062046 1333", "B": "0006 003041423303 033344453606",
    "C": "4130100105163645", "D": "00063645413000",
    "E": "40000646 0333", "F": "060040 0333",
    "G": "41301001051636454323", "H": "0006 4046 0343",
    "I": "0040 2026 0646", "J": "0040 4045351605",
    "K": "0006 400346", "L": "000646",
    "M": "0600234046",
    "N": "06004640", "O": "103041453616050110",
    "P": "06003041423303", "Q": "103041453616050110 2447",
    "R": "06003041423303 2346", "S": "4130100112233445361605",
    "T": "0040 2026", "U": "000516364540", "V": "002640",
    "W": "0006234640", "X": "0046 4006", "Y": "002340 2326",
    "Z": "00400646",
    "a": "4332120305163645 4246", "b": "0006 03324345361605",
    "c": "4332120305163645", "d": "4046 4332120305163645",
    "e": "04444332120305163645", "f": "16112030 0333",
    "g": "4536160503123243 4247371707", "h": "0006 03324346",
    "i": "2021 2226", "j": "3031 32371707",
    "k": "0006 420446", "l": "10152636", "m": "0602132326 23334346",
    "n": "0602 03324346", "o": "123243453616050312",
    "p": "0208 03324345361605", "q": "4248 4332120305163645",
    "r": "0206 033243", "s": "4332120314243546361605",
    "t": "20253646 0242", "u": "0205163645 4246",
    "v": "022642", "w": "020614264642", "x": "0246 4206",
    "y": "022642 2608", "z": "02420646",
    "0": "103041453616050110 0541", "1": "112026 0646",
    "2": "0110304142230646", "3": "0040233445361605",
    "4": "300444 3036", "5": "400003334445361605",
    "6": "4030100105163645443303", "7": "004016",
    "8": "1302011030414233 1304051636454433 1333",
    "9": "103041423313020110 4245361606",
    ".": "2526", ",": "252617", ":": "2223 2526", ";": "2223 252617",
    "!": "2024 2526", "?": "01103041422324 2526",
    "'": "2022", '"': "1012 3032", "`": "1021",
    "-": "0343", "_": "0646", "+": "0343 2125", "=": "0242 0444",
    "/": "0640", "\\": "0046", "|": "2026",
    "(": "3011121536", ")": "1031323516", "[": "30202636",
    "]": "10202616", "{": "30212213242536", "}": "10212233242516",
    "<": "410345", ">": "014305", "^": "022042", "~": "0312233243",
    "*": "2125 1234 3214", "#": "1016 3036 0242 0444",
    "%": "0640 0010110100 3545463635", "$": "4130100112233445361605 2026",
    "&": "462112011022450616052334", "@": "443424232232444130100105163646",
}


def stroke_text_lines(text: str) -> list[list[tuple[float, float]]]:
    """Return centered-line layout in font units, rejecting unsupported glyphs."""
    unsupported = sorted(set(text) - set(_GLYPHS) - {" ", "\n", "\r", "\t"})
    if unsupported:
        raise ValueError(
            "Single-line text supports English letters, digits and ASCII punctuation. "
            f"Unsupported characters: {''.join(unsupported)!r}"
        )
    lines = text.expandtabs(4).splitlines()
    widest = max((len(line) for line in lines), default=0)
    result = []
    for row, line in enumerate(lines):
        offset = (widest - len(line)) * 3.0
        for column, character in enumerate(line):
            if character == " ":
                continue
            for stroke in _GLYPHS[character].split():
                result.append([
                    (offset + column * 6 + int(stroke[i]), row * 10 + int(stroke[i + 1]))
                    for i in range(0, len(stroke), 2)
                ])
    if not result:
        raise ValueError("Text cannot be empty")
    return result
