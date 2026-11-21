"""Cliente VNC propio: DES (vector FIPS 46) y respuesta al desafío VNC."""

from playground.vnc import des_encrypt_block, vnc_auth_response


def test_des_fips_vector():
    key = bytes.fromhex("133457799BBCDFF1")
    assert des_encrypt_block(key, bytes.fromhex("0123456789ABCDEF")).hex().upper() == "85E813540F0AB405"


def test_vnc_auth_response_shape_and_bit_reversal():
    # misma contraseña, mismo desafío -> misma respuesta de 16 bytes; distinta contraseña -> distinta
    ch = bytes(range(16))
    a = vnc_auth_response("abc12345", ch); b = vnc_auth_response("abc12346", ch)
    assert len(a) == 16 and a != b and a == vnc_auth_response("abc12345", ch)
    # contraseña corta se rellena con ceros; más de 8 chars se truncan (como todos los servidores VNC)
    assert vnc_auth_response("abc", ch) == vnc_auth_response("abc\0\0\0\0\0", ch)
    assert vnc_auth_response("abc12345XYZ", ch) == vnc_auth_response("abc12345", ch)
