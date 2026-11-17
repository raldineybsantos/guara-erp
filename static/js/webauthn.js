// Guará ERP — chaves de segurança (WebAuthn). Sem dependências.
(function () {
  function b64uParaBuf(s) {
    s = s.replace(/-/g, "+").replace(/_/g, "/"); while (s.length % 4) s += "=";
    var b = atob(s), u = new Uint8Array(b.length);
    for (var i = 0; i < b.length; i++) u[i] = b.charCodeAt(i);
    return u.buffer;
  }
  function bufParaB64u(buf) {
    var u = new Uint8Array(buf), s = "";
    for (var i = 0; i < u.length; i++) s += String.fromCharCode(u[i]);
    return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }
  function csrf() { var m = document.cookie.match(/csrftoken=([^;]+)/); return m ? m[1] : (document.querySelector("[name=csrfmiddlewaretoken]") || {}).value; }
  function post(url, corpo) {
    return fetch(url, {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json", "X-CSRFToken": csrf()},
      body: JSON.stringify(corpo || {})}).then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.erro || "Falha"); return j; }); });
  }
  function aviso(el, msg) { if (el) { el.textContent = msg; el.classList.remove("d-none"); } }

  window.guaraChaves = {
    suportado: function () { return !!(window.PublicKeyCredential && navigator.credentials); },
    cadastrar: function (urls, nome, el) {
      return post(urls.opcoes).then(function (o) {
        o.challenge = b64uParaBuf(o.challenge); o.user.id = b64uParaBuf(o.user.id);
        (o.excludeCredentials || []).forEach(function (c) { c.id = b64uParaBuf(c.id); });
        return navigator.credentials.create({publicKey: o});
      }).then(function (c) {
        var r = c.response;
        return post(urls.confirmar, {nome: nome, credencial: {id: c.id, rawId: bufParaB64u(c.rawId), type: c.type,
          response: {clientDataJSON: bufParaB64u(r.clientDataJSON), attestationObject: bufParaB64u(r.attestationObject),
                     transports: r.getTransports ? r.getTransports() : []}, clientExtensionResults: {}}});
      }).then(function (j) { location.href = j.destino; }).catch(function (e) { aviso(el, e.name === "NotAllowedError" ? "Operação cancelada ou expirada." : e.message); });
    },
    entrar: function (urls, proximo, el) {
      return post(urls.opcoes).then(function (o) {
        o.challenge = b64uParaBuf(o.challenge);
        (o.allowCredentials || []).forEach(function (c) { c.id = b64uParaBuf(c.id); });
        return navigator.credentials.get({publicKey: o});
      }).then(function (c) {
        var r = c.response;
        return post(urls.verificar, {credencial: {id: c.id, rawId: bufParaB64u(c.rawId), type: c.type,
          response: {clientDataJSON: bufParaB64u(r.clientDataJSON), authenticatorData: bufParaB64u(r.authenticatorData),
                     signature: bufParaB64u(r.signature), userHandle: r.userHandle ? bufParaB64u(r.userHandle) : null},
          clientExtensionResults: {}}});
      }).then(function () { location.href = proximo || "/"; }).catch(function (e) { aviso(el, e.name === "NotAllowedError" ? "Operação cancelada ou expirada." : e.message); });
    }
  };
})();
