/* Máscara de CPF e CEP nos formulários e consulta automática de endereço pelo CEP (BrasilAPI).
   O CPF é enviado só com dígitos; a máscara é apenas visual. */
(function () {
  function digitos(v) { return (v || '').replace(/\D/g, ''); }
  function mascaraCpf(v) {
    var d = digitos(v).slice(0, 11), r = d.slice(0, 3);
    if (d.length > 3) r += '.' + d.slice(3, 6);
    if (d.length > 6) r += '.' + d.slice(6, 9);
    if (d.length > 9) r += '-' + d.slice(9, 11);
    return r;
  }
  function mascaraCep(v) {
    var d = digitos(v).slice(0, 8);
    return d.length > 5 ? d.slice(0, 5) + '-' + d.slice(5) : d;
  }
  document.querySelectorAll('main form input[name="cpf"]').forEach(function (el) {
    el.value = mascaraCpf(el.value); el.setAttribute('inputmode', 'numeric'); el.setAttribute('maxlength', '14');
    el.addEventListener('input', function () { el.value = mascaraCpf(el.value); });
    if (el.form) el.form.addEventListener('submit', function () { el.value = digitos(el.value); });
  });
  var cep = document.querySelector('main form input[name="cep"]');
  var url = document.body.getAttribute('data-consulta-cep');
  if (!cep || !url) return;
  cep.setAttribute('inputmode', 'numeric'); cep.setAttribute('maxlength', '9');
  var msg = document.createElement('div'); msg.className = 'form-text';
  cep.insertAdjacentElement('afterend', msg);
  cep.addEventListener('input', function () { cep.value = mascaraCep(cep.value); });
  function limparErro(el) {   // tira a mensagem de erro antiga do campo que acabou de ser preenchido
    el.classList.remove('is-invalid');
    var caixa = el.parentElement;
    if (caixa) caixa.querySelectorAll('.invalid-feedback, .text-danger').forEach(function (m) {
      if (m !== msg) m.remove();
    });
  }
  function set(nome, v) {
    var el = cep.form.elements[nome];
    if (!el || !v) return;
    el.value = v; limparErro(el); el.dispatchEvent(new Event('change', {bubbles: true}));
  }
  cep.addEventListener('blur', function () {
    if (digitos(cep.value).length !== 8) { msg.textContent = ''; return; }
    msg.textContent = 'Buscando endereço…'; msg.className = 'form-text';
    fetch(url + '?cep=' + digitos(cep.value), {credentials: 'same-origin'}).then(function (r) { return r.json(); })
      .then(function (j) {
        if (j.erro) { msg.textContent = j.erro; msg.className = 'form-text text-danger'; return; }
        limparErro(cep);
        ['logradouro', 'bairro', 'cidade', 'uf'].forEach(function (n) { set(n, j[n]); });
        msg.textContent = 'Endereço preenchido. Informe o número.'; msg.className = 'form-text text-success';
        var num = cep.form.elements['numero']; if (num && !num.value) num.focus();
      }).catch(function () { msg.textContent = 'Falha na consulta do CEP.'; msg.className = 'form-text text-danger'; });
  });
})();
