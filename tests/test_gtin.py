from gtin import normalize, valid, expand_upc_e

def test_ean13_passthrough():
    assert normalize("3017620422003") == "3017620422003"

def test_upc_a_gets_leading_zero():
    assert normalize("012345678905") == "0012345678905"

def test_ean8_padded():
    assert normalize("96385074") == "0000096385074"

def test_gtin14_with_leading_zero_trimmed():
    assert normalize("03017620422003") == "3017620422003"

def test_bad_checksum_rejected():
    assert normalize("3017620422004") is None

def test_garbage_rejected():
    assert normalize("abc") is None
    assert normalize("") is None

def test_upc_e_expands():
    # the textbook example: UPC-E 04252614 is UPC-A 042100005264
    assert expand_upc_e("04252614") == "042100005264"
    assert normalize("04252614", symbology="upc_e") == "0042100005264"

def test_valid():
    assert valid("3017620422003")
    assert not valid("3017620422000")
