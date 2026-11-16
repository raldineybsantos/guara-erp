# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
"""Backup do Guará ERP: banco (pg_dump) + arquivos enviados (GED), num único arquivo cifrado (AES-256-GCM).

Fluxo: pg_dump -Fc -> tar.gz (banco + media + manifesto) -> cifra em blocos -> grava em BACKUP_DIR ->
confere o arquivo (decifra, valida o manifesto, pg_restore --list) -> cópia externa -> retenção (diária,
semanal, mensal). Nada passa por shell: todos os comandos são listas de argumentos."""
import datetime
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import struct
import subprocess
import tarfile
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from django.conf import settings
from django.utils import timezone

MAGIA = b"GUARABK1"
BLOCO = 1024 * 1024
PADRAO_NOME = re.compile(r"^guara-(\d{8})-(\d{6})\.gbk$")


class BackupErro(Exception):
    pass


# ----------------------------------------------------------------------------------------------- cifra
def _chave(senha: str, sal: bytes) -> bytes:
    return Scrypt(salt=sal, length=32, n=2 ** 15, r=8, p=1).derive(senha.encode())


def senha_backup() -> str:
    """BACKUP_SENHA; sem ela, deriva-se da chave do cofre/SECRET_KEY (ver aviso na tela de backups)."""
    return (getattr(settings, "BACKUP_SENHA", "") or getattr(settings, "FISCAL_CHAVE_COFRE", "")
            or settings.SECRET_KEY)


def senha_propria() -> bool:
    return bool(getattr(settings, "BACKUP_SENHA", ""))


class _Cifrador:
    """Arquivo-like de escrita: cifra em blocos de 1 MiB (AES-GCM, nonce = prefixo + contador, o último bloco
    é marcado — truncar ou reordenar o arquivo é detectado na leitura)."""

    def __init__(self, destino, senha):
        self.destino = destino
        sal, self.prefixo = os.urandom(16), os.urandom(4)
        self.aes = AESGCM(_chave(senha, sal))
        destino.write(MAGIA + sal + self.prefixo)
        self.n = 0
        self.buf = bytearray()

    def write(self, dados):
        self.buf += dados
        while len(self.buf) > BLOCO:           # sempre sobra ao menos 1 byte para o bloco final
            self._bloco(bytes(self.buf[:BLOCO]), False)
            del self.buf[:BLOCO]
        return len(dados)

    def _bloco(self, texto, final):
        nonce = self.prefixo + struct.pack(">Q", self.n)
        ct = self.aes.encrypt(nonce, texto, b"\x01" if final else b"\x00")
        self.destino.write(struct.pack(">I", len(ct)) + ct)
        self.n += 1

    def close(self):
        self._bloco(bytes(self.buf), True)
        self.buf.clear()

    def flush(self):
        pass


def decifrar(origem, destino, senha):
    """Decifra para o arquivo-like destino. Levanta BackupErro se a senha estiver errada ou o arquivo alterado."""
    cab = origem.read(len(MAGIA) + 16 + 4)
    if len(cab) < len(MAGIA) + 20 or cab[:len(MAGIA)] != MAGIA:
        raise BackupErro("Arquivo não é um backup do Guará ERP.")
    sal, prefixo = cab[len(MAGIA):len(MAGIA) + 16], cab[-4:]
    aes = AESGCM(_chave(senha, sal))
    n, final = 0, False
    while not final:
        t = origem.read(4)
        if len(t) != 4:
            raise BackupErro("Backup truncado (falta o bloco final).")
        (tam,) = struct.unpack(">I", t)
        if tam > BLOCO + 16:
            raise BackupErro("Backup corrompido (bloco inválido).")
        ct = origem.read(tam)
        if len(ct) != tam:
            raise BackupErro("Backup truncado.")
        final = origem.peek(1) == b""          # o último bloco do arquivo precisa ter sido gravado como final
        try:
            destino.write(aes.decrypt(prefixo + struct.pack(">Q", n), ct, b"\x01" if final else b"\x00"))
        except Exception:
            raise BackupErro("Senha de backup incorreta, ou o arquivo foi alterado/truncado.") from None
        n += 1
    if origem.read(1):
        raise BackupErro("Backup corrompido: dados depois do bloco final.")


# ----------------------------------------------------------------------------------------------- pastas
def pasta_backup() -> Path:
    p = Path(getattr(settings, "BACKUP_DIR", "") or settings.BASE_DIR / "backups")
    p.mkdir(parents=True, exist_ok=True)
    return p


def pasta_copia():
    d = getattr(settings, "BACKUP_COPIA_DIR", "")
    return Path(d) if d else None


def _db():
    return settings.DATABASES["default"]


def _pg(comando, *args, banco=None, env_extra=None):
    exe = shutil.which(comando)
    if not exe:
        raise BackupErro(f"{comando} não encontrado. Instale o cliente do PostgreSQL (postgresql-client) no servidor.")
    db = _db()
    env = dict(os.environ, PGPASSWORD=db["PASSWORD"], **(env_extra or {}))
    return [exe, "-h", db["HOST"], "-p", str(db["PORT"]), "-U", db["USER"], *args], env


# ----------------------------------------------------------------------------------------------- gerar
def _manifesto(dump_sha, n_media):
    from django.db.migrations.recorder import MigrationRecorder

    return {"formato": 1, "sistema": "Guará ERP", "gerado_em": timezone.now().isoformat(),
            "banco": _db()["NAME"], "banco_sha256": dump_sha, "arquivos_media": n_media,
            "migracoes": MigrationRecorder.Migration.objects.count()}


def _sha256(caminho):
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for b in iter(lambda: f.read(BLOCO), b""):
            h.update(b)
    return h.hexdigest()


def gerar(destino: Path | None = None, incluir_media=None) -> dict:
    """Cria o arquivo cifrado em BACKUP_DIR. Devolve {caminho, tamanho, sha256, arquivos_media}."""
    destino = destino or pasta_backup()
    destino.mkdir(parents=True, exist_ok=True)
    incluir_media = getattr(settings, "BACKUP_INCLUIR_MEDIA", True) if incluir_media is None else incluir_media
    agora = timezone.localtime()
    final = destino / f"guara-{agora:%Y%m%d-%H%M%S}.gbk"
    parcial = final.with_suffix(".parcial")
    with tempfile.TemporaryDirectory(prefix="guara-bk-") as tmp:   # 0700; apagado ao sair
        dump = Path(tmp) / "banco.dump"
        cmd, env = _pg("pg_dump", "-Fc", "--no-owner", "-f", str(dump), _db()["NAME"])
        proc = subprocess.run(cmd, env=env, capture_output=True, timeout=3600)
        if proc.returncode != 0:
            raise BackupErro(f"pg_dump falhou: {proc.stderr.decode(errors='ignore')[:400]}")
        media = Path(settings.MEDIA_ROOT)
        n_media = 0
        try:
            with open(parcial, "wb") as saida:
                cif = _Cifrador(saida, senha_backup())
                with tarfile.open(fileobj=cif, mode="w|gz") as tar:
                    tar.add(dump, arcname="banco.dump")
                    if incluir_media and media.is_dir():
                        for arq in sorted(media.rglob("*")):
                            if arq.is_file() and not arq.is_symlink() and destino.resolve() not in arq.resolve().parents:
                                tar.add(arq, arcname="media/" + arq.relative_to(media).as_posix(), recursive=False)
                                n_media += 1
                    dados = json.dumps(_manifesto(_sha256(dump), n_media), indent=1).encode()
                    info = tarfile.TarInfo("manifesto.json")
                    info.size = len(dados)
                    info.mtime = int(agora.timestamp())
                    tar.addfile(info, io.BytesIO(dados))
                cif.close()
            os.chmod(parcial, 0o600)
            os.replace(parcial, final)
        finally:
            if parcial.exists():
                parcial.unlink()
    return {"caminho": final, "tamanho": final.stat().st_size, "sha256": _sha256(final), "arquivos_media": n_media}


# ----------------------------------------------------------------------------------------------- conferir
def abrir(arquivo: Path, pasta_saida: Path, senha=None) -> dict:
    """Decifra e extrai em pasta_saida (apenas membros seguros). Devolve o manifesto."""
    senha = senha or senha_backup()
    tmp_tar = pasta_saida / "conteudo.tar.gz"
    with open(arquivo, "rb") as origem, open(tmp_tar, "wb") as destino:
        decifrar(origem, destino, senha)
    try:
        with tarfile.open(tmp_tar, "r:gz") as tar:
            for m in tar.getmembers():
                p = Path(m.name)
                if p.is_absolute() or ".." in p.parts or not (m.isfile() or m.isdir()):
                    raise BackupErro(f"Backup com membro inseguro: {m.name}")
            tar.extractall(pasta_saida, filter="data")
    except tarfile.TarError as exc:
        raise BackupErro(f"Conteúdo do backup ilegível: {exc}") from exc
    finally:
        tmp_tar.unlink(missing_ok=True)
    try:
        manifesto = json.loads((pasta_saida / "manifesto.json").read_text())
    except (OSError, ValueError) as exc:
        raise BackupErro("Backup sem manifesto válido.") from exc
    if _sha256(pasta_saida / "banco.dump") != manifesto.get("banco_sha256"):
        raise BackupErro("A impressão digital do banco não confere com o manifesto.")
    return manifesto


def verificar(arquivo: Path, senha=None) -> dict:
    """Prova que o backup é utilizável sem restaurar: decifra, confere o manifesto e lê o dump com pg_restore --list."""
    with tempfile.TemporaryDirectory(prefix="guara-vf-") as tmp:
        manifesto = abrir(arquivo, Path(tmp), senha)
        exe = shutil.which("pg_restore")
        if not exe:
            raise BackupErro("pg_restore não encontrado. Instale o cliente do PostgreSQL.")
        proc = subprocess.run([exe, "--list", str(Path(tmp) / "banco.dump")], capture_output=True, timeout=600)
        if proc.returncode != 0 or b"TABLE DATA" not in proc.stdout:
            raise BackupErro(f"pg_restore não conseguiu ler o banco do backup: {proc.stderr.decode(errors='ignore')[:300]}")
    return manifesto


# ----------------------------------------------------------------------------------------------- retenção
def _data_do_nome(nome):
    m = PADRAO_NOME.match(nome)
    return datetime.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S") if m else None


def escolher_para_manter(nomes, diarios=7, semanais=4, mensais=12):
    """Política avô-pai-filho: guarda o backup mais recente de cada um dos últimos N dias, N semanas e N meses."""
    datados = sorted(((d, n) for n in nomes if (d := _data_do_nome(n))), reverse=True)
    manter = set()
    for chave, limite in ((lambda d: d.date(), diarios), (lambda d: d.isocalendar()[:2], semanais),
                          (lambda d: (d.year, d.month), mensais)):
        vistos = []
        for d, n in datados:
            k = chave(d)
            if k not in vistos:
                vistos.append(k)
                if len(vistos) <= limite:
                    manter.add(n)
    return manter


def aplicar_retencao(pasta: Path) -> int:
    nomes = [p.name for p in pasta.iterdir() if p.is_file() and PADRAO_NOME.match(p.name)]
    manter = escolher_para_manter(nomes, getattr(settings, "BACKUP_RETENCAO_DIARIOS", 7),
                                  getattr(settings, "BACKUP_RETENCAO_SEMANAIS", 4),
                                  getattr(settings, "BACKUP_RETENCAO_MENSAIS", 12))
    apagados = 0
    for n in nomes:
        if n not in manter:
            (pasta / n).unlink()
            apagados += 1
    return apagados


# ----------------------------------------------------------------------------------------------- cópia externa
def copiar_externo(arquivo: Path) -> str:
    """Copia para BACKUP_COPIA_DIR (disco externo, NAS montado) e/ou roda BACKUP_COMANDO_COPIA
    (ex.: 'rclone copy {arquivo} nuvem:guara'). Devolve uma descrição do que foi feito; '' se nada configurado."""
    feito = []
    pasta = pasta_copia()
    if pasta:
        pasta.mkdir(parents=True, exist_ok=True)
        tmp = pasta / (arquivo.name + ".parcial")
        shutil.copyfile(arquivo, tmp)
        os.replace(tmp, pasta / arquivo.name)
        if _sha256(pasta / arquivo.name) != _sha256(arquivo):
            raise BackupErro("A cópia externa não confere com o original.")
        aplicar_retencao(pasta)
        feito.append(f"pasta {pasta}")
    comando = getattr(settings, "BACKUP_COMANDO_COPIA", "")
    if comando:
        args = [a.replace("{arquivo}", str(arquivo)) for a in shlex.split(comando, posix=os.name != "nt")]
        proc = subprocess.run(args, capture_output=True, timeout=7200)
        if proc.returncode != 0:
            raise BackupErro(f"Comando de cópia externa falhou: {proc.stderr.decode(errors='ignore')[:300]}")
        feito.append("comando externo")
    return " + ".join(feito)


# ----------------------------------------------------------------------------------------------- orquestração
def executar(origem="agendado", usuario=None):
    """Faz o backup completo e registra o resultado. Nunca levanta: devolve o RegistroBackup."""
    from apps.core.models import LogAuditoria

    from .models import RegistroBackup

    reg = RegistroBackup.objects.create(origem=origem, usuario=usuario)
    try:
        r = gerar()
        reg.arquivo, reg.tamanho, reg.sha256, reg.arquivos_media = (r["caminho"].name, r["tamanho"], r["sha256"],
                                                                     r["arquivos_media"])
        verificar(r["caminho"])
        reg.verificado = True
        reg.save()
        reg.removidos = aplicar_retencao(r["caminho"].parent)
        try:
            reg.copia_externa = copiar_externo(r["caminho"])
        except Exception as exc:        # o backup local existe; a falha da cópia é avisada, não esconde o resultado
            reg.erro = f"Backup local OK, mas a cópia externa falhou: {exc}"
        reg.situacao = "ok"
    except Exception as exc:
        reg.situacao, reg.erro = "erro", str(exc)[:2000]
    reg.concluido_em = timezone.now()
    reg.save()
    LogAuditoria.objects.create(usuario=usuario, acao="acao", modelo="seguranca.backup", objeto_id=str(reg.pk),
                                descricao=(f"Backup {reg.get_origem_display().lower()} "
                                           f"{'concluído: ' + reg.arquivo if reg.situacao == 'ok' else 'FALHOU'}")[:300])
    if reg.situacao == "erro" or reg.erro:
        _avisar(reg)
    return reg


def _avisar(reg):
    from django.contrib.auth.models import User

    from apps.core.models import notificar

    notificar(User.objects.filter(is_superuser=True, is_active=True),
              f"Backup com problema em {reg.iniciado_em:%d/%m/%Y %H:%M}: {(reg.erro or '')[:140]}",
              "/seguranca/backups/", email=True)


def verificar_atraso(horas=36):
    """Avisa os administradores se não há backup concluído nas últimas horas (agendador parado?)."""
    from django.contrib.auth.models import User

    from apps.core.models import Notificacao, notificar

    from .models import RegistroBackup

    ultimo = RegistroBackup.objects.filter(situacao="ok").order_by("-iniciado_em").first()
    if ultimo and timezone.now() - ultimo.iniciado_em < datetime.timedelta(hours=horas):
        return False
    texto = "Nenhum backup concluído nas últimas %d horas." % horas
    hoje = timezone.localdate()
    if Notificacao.objects.filter(texto=texto, criada_em__date=hoje).exists():
        return True
    notificar(User.objects.filter(is_superuser=True, is_active=True), texto, "/seguranca/backups/", email=True)
    return True


# ----------------------------------------------------------------------------------------------- restaurar
def restaurar(arquivo: Path, senha=None, banco_destino=None, restaurar_media=True) -> dict:
    """Restaura o banco (pg_restore --clean) e os arquivos. Operação destrutiva: só pela linha de comando."""
    with tempfile.TemporaryDirectory(prefix="guara-rs-") as tmp:
        manifesto = abrir(arquivo, Path(tmp), senha)
        alvo = banco_destino or _db()["NAME"]
        cmd, env = _pg("pg_restore", "--clean", "--if-exists", "--no-owner", "--exit-on-error", "-d", alvo,
                       str(Path(tmp) / "banco.dump"))
        proc = subprocess.run(cmd, env=env, capture_output=True, timeout=7200)
        if proc.returncode != 0:
            raise BackupErro(f"pg_restore falhou: {proc.stderr.decode(errors='ignore')[:500]}")
        n = 0
        origem = Path(tmp) / "media"
        if restaurar_media and origem.is_dir():
            raiz = Path(settings.MEDIA_ROOT)
            for arq in origem.rglob("*"):
                if arq.is_file():
                    dest = raiz / arq.relative_to(origem)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(arq, dest)
                    n += 1
    manifesto["arquivos_restaurados"] = n
    return manifesto
