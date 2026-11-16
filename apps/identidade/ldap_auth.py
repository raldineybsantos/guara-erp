# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
"""Autenticação LDAP/AD com ldap3 (Python puro: funciona igual no Linux e no Windows).

Segurança: só TLS com certificado validado; a senha vazia é recusada (um bind simples sem senha vira acesso
anônimo e "dá certo" em muitos servidores); o login digitado entra no filtro já escapado; a conta de serviço só lê."""
import ssl

import ldap3
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.models import Group, User
from django.utils import timezone
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars

from .models import ConfiguracaoLDAP, IdentidadeExterna

UAC_DESATIVADA = 0x2


def _abrir(cfg, dn=None, senha=None):
    """Abre e autentica uma conexão. Devolve a conexão ou None se o diretório recusar as credenciais."""
    tls = ldap3.Tls(validate=ssl.CERT_REQUIRED, version=ssl.PROTOCOL_TLS_CLIENT,
                    ca_certs_data=cfg.ca_pem.strip() or None)
    servidor = ldap3.Server(cfg.servidor, port=cfg.porta, use_ssl=cfg.seguranca == "ldaps", tls=tls,
                            get_info=ldap3.NONE, connect_timeout=cfg.timeout)
    conn = ldap3.Connection(servidor, user=dn or None, password=senha or None, receive_timeout=cfg.timeout,
                            raise_exceptions=False, auto_bind=False)
    if cfg.seguranca == "starttls":
        conn.open()
        conn.start_tls()
    if not conn.bind():
        conn.unbind()
        return None
    return conn


def _valor(entrada, atributo):
    v = entrada.get("attributes", {}).get(atributo)
    if isinstance(v, (list, tuple)):
        v = v[0] if v else ""
    return str(v) if v not in (None, "") else ""


def _lista(entrada, atributo):
    v = entrada.get("attributes", {}).get(atributo) or []
    return [str(x) for x in (v if isinstance(v, (list, tuple)) else [v])]


def buscar_usuario(cfg, conn, login):
    filtro = cfg.filtro_usuario.replace("{usuario}", escape_filter_chars(login))
    atributos = {cfg.atributo_login, cfg.atributo_email, cfg.atributo_nome, cfg.atributo_grupos, "userAccountControl"}
    conn.search(cfg.base_dn, filtro, search_scope=ldap3.SUBTREE, attributes=list(atributos), size_limit=2)
    achados = [r for r in conn.response or [] if r.get("type") == "searchResEntry"]
    return achados[0] if len(achados) == 1 else None      # 0 ou ambíguo: recusa


def autenticar_no_diretorio(cfg, login, senha):
    """Devolve a entrada do diretório se login e senha conferem e a conta está habilitada; senão None."""
    if not login or not senha:
        return None
    svc = _abrir(cfg, cfg.bind_dn, cfg.obter_senha())
    if svc is None:
        raise LDAPException("a conta de serviço foi recusada pelo diretório")
    try:
        entrada = buscar_usuario(cfg, svc, login)
    finally:
        svc.unbind()
    if entrada is None:
        return None
    usuario = _abrir(cfg, entrada["dn"], senha)
    if usuario is None:
        return None
    usuario.unbind()
    uac = _valor(entrada, "userAccountControl")
    if uac.isdigit() and int(uac) & UAC_DESATIVADA:
        return None
    if cfg.grupo_obrigatorio and cfg.grupo_obrigatorio.strip().lower() not in {
            g.lower() for g in _lista(entrada, cfg.atributo_grupos)}:
        return None
    return entrada


def sincronizar(cfg, entrada, login):
    """Cria/atualiza o usuário do ERP e seus grupos. Devolve o usuário ou None (não pode entrar)."""
    origem = f"ldap:{cfg.pk}"
    subject = (_valor(entrada, cfg.atributo_login) or login).lower()
    ident = IdentidadeExterna.objects.filter(origem=origem, subject=subject).select_related("usuario").first()
    if ident:
        user = ident.usuario
    else:
        existente = User.objects.filter(username__iexact=subject).first()
        if existente:
            if not cfg.vincular_existentes:
                return None
            user = existente
        elif cfg.criar_usuarios:
            user = User(username=subject)
            user.set_unusable_password()
        else:
            return None
        if user.pk is None:
            user.save()
        ident = IdentidadeExterna.objects.create(usuario=user, origem=origem, subject=subject)
    if not user.is_active:
        return None
    email, nome = _valor(entrada, cfg.atributo_email), _valor(entrada, cfg.atributo_nome)
    mudou = False
    if email and user.email != email[:254]:
        user.email, mudou = email[:254], True
    if nome:
        primeiro, _, resto = nome.partition(" ")
        if (user.first_name, user.last_name) != (primeiro[:150], resto[:150]):
            user.first_name, user.last_name, mudou = primeiro[:150], resto[:150], True
    if mudou:
        user.save()
    # grupos: só mexe nos que o mapa controla; grupos atribuídos à mão no ERP continuam
    membro_de = {g.lower() for g in _lista(entrada, cfg.atributo_grupos)}
    desejados = {g for dn, g in cfg.mapa if dn in membro_de}
    controlados = {g for _, g in cfg.mapa}
    atuais = set(user.groups.filter(name__in=controlados).values_list("name", flat=True))
    for nome_grupo in desejados - atuais:
        user.groups.add(Group.objects.get(name=nome_grupo))
    for nome_grupo in atuais - desejados:
        user.groups.remove(Group.objects.get(name=nome_grupo))
    ident.ultimo_login = timezone.now()
    ident.save(update_fields=["ultimo_login"])
    return user


class LDAPBackend(ModelBackend):
    """Tenta cada diretório ativo. Falha de rede/configuração não derruba o login local (vai para o log de erros)."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password:
            return None
        username = username.strip()[:150]
        for cfg in ConfiguracaoLDAP.objects.filter(ativo=True):
            try:
                entrada = autenticar_no_diretorio(cfg, username, password)
                if entrada is None:
                    continue
                user = sincronizar(cfg, entrada, username)
            except (LDAPException, OSError, ssl.SSLError) as exc:
                from apps.core.logs import registrar_erro

                registrar_erro(mensagem=f"LDAP '{cfg.nome}': {exc}"[:500], origem="ldap", nivel="ERROR")
                continue
            if user is not None and self.user_can_authenticate(user):
                return user
        return None


def testar(cfg, login="", senha=""):
    """Teste para a tela de administração. Devolve lista de (ok, mensagem)."""
    passos = []
    try:
        svc = _abrir(cfg, cfg.bind_dn, cfg.obter_senha())
    except (LDAPException, OSError, ssl.SSLError) as exc:
        return [(False, f"Não foi possível conectar com TLS válido: {exc}")]
    if svc is None:
        return [(False, "Conectou, mas a conta de serviço foi recusada (DN ou senha incorretos).")]
    passos.append((True, "Conexão segura e conta de serviço autenticada."))
    try:
        if login:
            entrada = buscar_usuario(cfg, svc, login)
            if entrada is None:
                passos.append((False, f"Usuário '{login}' não encontrado (ou o filtro devolveu mais de um)."))
            else:
                passos.append((True, f"Usuário encontrado: {entrada['dn']}"))
                if senha:
                    u = _abrir(cfg, entrada["dn"], senha)
                    passos.append((u is not None, "Senha do usuário confere." if u else "Senha do usuário recusada."))
                    if u:
                        u.unbind()
                grupos = _lista(entrada, cfg.atributo_grupos)
                passos.append((True, f"Grupos do usuário: {len(grupos)}; "
                                     f"mapeados para o ERP: {sorted({g for dn, g in cfg.mapa if dn in {x.lower() for x in grupos}}) or 'nenhum'}"))
    finally:
        svc.unbind()
    return passos
