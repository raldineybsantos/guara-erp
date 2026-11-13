"""Gera um PDF simples de texto (uma página por bloco de ~45 linhas), sem dependências externas.

Usado nos dados de demonstração e nos testes de assinatura digital.
"""


def _escapar(texto):
    texto = texto.encode("cp1252", "replace").decode("latin-1")
    return texto.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def gerar_pdf(titulo, linhas):
    paginas = [linhas[i:i + 45] for i in range(0, max(len(linhas), 1), 45)] or [[]]
    objetos = []  # conteúdo de cada objeto (índice + 1 = número)

    def add(conteudo):
        objetos.append(conteudo)
        return len(objetos)

    catalogo = add(None)
    raiz_paginas = add(None)
    fonte = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    fonte_b = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    ids_paginas = []
    for n, bloco in enumerate(paginas):
        fluxo = ["BT", "/F2 15 Tf", "56 790 Td", f"({_escapar(titulo)}) Tj", "/F1 10.5 Tf", "0 -26 Td", "14 TL"]
        for linha in bloco:
            fluxo.append(f"({_escapar(linha)}) Tj T*")
        fluxo += ["ET", "BT", "/F1 8 Tf", "56 30 Td", f"(Pagina {n + 1} de {len(paginas)}) Tj", "ET"]
        dados = "\n".join(fluxo).encode("latin-1")
        conteudo = add(b"<< /Length %d >>\nstream\n" % len(dados) + dados + b"\nendstream")
        ids_paginas.append(add(
            b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 595 842] /Contents %d 0 R "
            b"/Resources << /Font << /F1 %d 0 R /F2 %d 0 R >> >> >>" % (raiz_paginas, conteudo, fonte, fonte_b)))
    objetos[catalogo - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % raiz_paginas
    kids = b" ".join(b"%d 0 R" % i for i in ids_paginas)
    objetos[raiz_paginas - 1] = b"<< /Type /Pages /Kids [" + kids + b"] /Count %d >>" % len(ids_paginas)

    saida = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    posicoes = []
    for i, obj in enumerate(objetos, start=1):
        posicoes.append(len(saida))
        saida += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(saida)
    saida += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objetos) + 1)
    for p in posicoes:
        saida += b"%010d 00000 n \n" % p
    saida += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objetos) + 1, catalogo, xref)
    return bytes(saida)
