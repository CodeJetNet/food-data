"""GTIN-13 normalization. Every barcode in the database is a 13-digit string."""

def valid(d: str) -> bool:
    if len(d) != 13 or not d.isdigit():
        return False
    s = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(d[:12]))
    return (10 - s % 10) % 10 == int(d[12])

def expand_upc_e(d: str) -> str:
    """8-digit UPC-E (number system + 6 digits + check) to 12-digit UPC-A."""
    n, x, c = d[0], d[1:7], d[7]
    a, b, cc, dd, e, f = x
    if f in "012":
        body = f"{a}{b}{f}0000{cc}{dd}{e}"
    elif f == "3":
        body = f"{a}{b}{cc}00000{dd}{e}"
    elif f == "4":
        body = f"{a}{b}{cc}{dd}00000{e}"
    else:
        body = f"{a}{b}{cc}{dd}{e}0000{f}"
    return n + body + c

def normalize(code: str | None, symbology: str | None = None) -> str | None:
    if not code:
        return None
    d = "".join(ch for ch in code if ch.isdigit())
    if symbology == "upc_e" and len(d) == 8:
        d = expand_upc_e(d)
    if len(d) == 14 and d[0] == "0":
        d = d[1:]
    if len(d) in (8, 12):
        d = d.rjust(13, "0")
    return d if valid(d) else None
