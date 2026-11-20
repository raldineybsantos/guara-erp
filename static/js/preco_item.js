/* Pedido de venda: ao escolher o produto de um item com valor unitário zerado, mostra e preenche o preço sugerido
   (tabela de preço do cliente ou cadastro do produto). O servidor repete a regra ao salvar. */
(function () {
  var url = document.body.getAttribute('data-preco-item');
  var form = document.querySelector('main form');
  if (!url || !form || !form.querySelector('[name$="-produto"][name^="itens-"]')) return;
  form.addEventListener('change', function (ev) {
    var el = ev.target;
    var m = el.name && el.name.match(/^(itens-\d+)-(produto|quantidade)$/);
    if (!m) return;
    var pref = m[1];
    var prod = form.elements[pref + '-produto'], vu = form.elements[pref + '-valor_unitario'];
    if (!prod || !prod.value || !vu) return;
    if (el.name.endsWith('-quantidade') && vu.dataset.sugerido !== '1') return;      // só reajusta o que foi sugerido
    if (el.name.endsWith('-produto') && parseFloat(vu.value) > 0 && vu.dataset.sugerido !== '1') return;
    var q = form.elements[pref + '-quantidade'], c = form.elements.cliente, d = form.elements.data_pedido;
    var qs = '?produto=' + prod.value + '&quantidade=' + encodeURIComponent(q ? q.value : 1) +
             (c && c.value ? '&cliente=' + c.value : '') + (d && d.value ? '&data=' + d.value : '');
    fetch(url + qs, {credentials: 'same-origin'}).then(function (r) { return r.json(); }).then(function (j) {
      if (j.erro) return;
      vu.value = j.preco; vu.dataset.sugerido = '1'; vu.title = 'Preço sugerido: ' + j.origem;
    });
  });
  form.addEventListener('input', function (ev) {
    if (ev.target.name && ev.target.name.endsWith('-valor_unitario')) ev.target.dataset.sugerido = '0';
  });
})();
