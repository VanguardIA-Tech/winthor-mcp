"""A cifra do PCEMPR.SENHABD, com vetores independentes.

Os vetores são calculados à mão a partir da definição da cifra, não pela
própria função — senão o teste só provaria que ela é consistente consigo mesma.
Nada de credencial real aqui: `AB`/`XY` bastam para fixar o formato.
"""

from winthor_mcp.identidade import senhabd


def test_cifra_um_caractere_por_byte_apos_o_bom():
    # X^A = 88^65 = 25 (0x19); Y^B = 89^66 = 27 (0x1B). BOM FF FE na frente.
    esperado = b"\xff\xfe\x19\x1b"
    assert senhabd.cifrar("XY", "AB") == esperado


def test_chave_entra_sempre_em_maiusculas():
    # Login minúsculo dá o mesmo resultado: o WinThor guarda USUARIOBD upado.
    assert senhabd.cifrar("XY", "ab") == senhabd.cifrar("XY", "AB")


def test_byte_de_espaco_e_escapado_para_fe():
    # 'a'^'A' = 97^65 = 32 (0x20). Espaço no meio some no TRIM do Oracle, então
    # a cifra o grava como 0xFE.
    assert senhabd.cifrar("a", "A") == b"\xff\xfe\xfe"


def test_verificar_aceita_a_senha_certa():
    guardado = b"\xff\xfe\x19\x1b".decode("cp1252")
    assert senhabd.verificar("XY", "AB", guardado) is True


def test_verificar_e_sensivel_a_maiusculas():
    guardado = senhabd.cifrar("Senha1", "USUARIO.X").decode("cp1252")
    assert senhabd.verificar("Senha1", "USUARIO.X", guardado) is True
    assert senhabd.verificar("senha1", "USUARIO.X", guardado) is False


def test_verificar_com_escape_de_espaco():
    guardado = b"\xff\xfe\xfe".decode("cp1252")
    assert senhabd.verificar("a", "A", guardado) is True
    assert senhabd.verificar("b", "A", guardado) is False


def test_verificar_fecha_em_falso_sem_dados():
    assert senhabd.verificar("x", None, "qualquer") is False
    assert senhabd.verificar("x", "USER", None) is False
    assert senhabd.verificar("", "USER", "\xff\xfe") is False


def test_verificar_recusa_sem_o_bom():
    # Um campo que não começa com FF FE não é SENHABD deste formato.
    assert senhabd.verificar("XY", "AB", "\x19\x1b") is False


def test_login_curto_demais_nao_arrisca():
    # Chave menor que a senha: em vez de adivinhar (wrap? corte?), recusa.
    assert senhabd.cifrar("SENHALONGA", "AB") is None
    assert senhabd.verificar("SENHALONGA", "AB", "\xff\xfe") is False
