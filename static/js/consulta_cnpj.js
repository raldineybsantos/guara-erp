/* Botão "Consultar CNPJ": preenche o cadastro com os dados públicos da Receita (BrasilAPI). */
(function () {
  var campo = document.querySelector('main form input[name="cnpj"], main form input[name="documento"]');
  var url = document.body.getAttribute('data-consulta-cnpj');
  if (!campo || !url) return;
  var form = campo.form;
  var btn = document.createElement('button');
  btn.type = 'button'; btn.className = 'btn btn-sm btn-outline-secondary mt-1';
  btn.innerHTML = '<i class="bi bi-search"></i> Consultar CNPJ';
  var msg = document.createElement('div'); msg.className = 'form-text';
  campo.insertAdjacentElement('afterend', btn); btn.insertAdjacentElement('afterend', msg);
  function set(nome, v, so_vazio) {
    var el = form.elements[nome];
    if (!el || !v) return;
    if (so_vazio && el.value) return;
    el.value = v; el.dispatchEvent(new Event('change', {bubbles: true}));
  }
  btn.addEventListener('click', function () {
    var d = campo.value.replace(/\D/g, '');
    if (d.length !== 14) { msg.textContent = 'Informe os 14 dígitos do CNPJ.'; msg.className = 'form-text text-danger'; return; }
    msg.textContent = 'Consultando…'; msg.className = 'form-text';
    fetch(url + '?cnpj=' + d, {credentials: 'same-origin'}).then(function (r) { return r.json(); }).then(function (j) {
      if (j.erro) { msg.textContent = j.erro; msg.className = 'form-text text-danger'; return; }
      set('razao_social', j.razao_social); set('nome', j.razao_social, true);
      set('nome_fantasia', j.nome_fantasia, true); set('email', j.email, true); set('telefone', j.telefone, true);
      ['cep', 'logradouro', 'numero', 'complemento', 'bairro', 'cidade', 'uf', 'codigo_municipio', 'cnae_principal']
        .forEach(function (n) { set(n, j[n], true); });
      msg.textContent = 'Situação cadastral: ' + (j.situacao || 'não informada') + '. Confira os dados antes de salvar.';
      msg.className = 'form-text ' + (j.inativa ? 'text-danger fw-bold' : 'text-success');
    }).catch(function () { msg.textContent = 'Falha na consulta.'; msg.className = 'form-text text-danger'; });
  });
})();
