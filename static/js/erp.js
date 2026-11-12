// Tema claro/escuro persistente
(function () {
  const html = document.documentElement;
  let salvo = null;
  try { salvo = localStorage.getItem("erp-tema"); } catch (e) {}
  if (salvo) html.setAttribute("data-bs-theme", salvo);
  const btn = document.getElementById("tema");
  if (btn) btn.addEventListener("click", () => {
    const novo = html.getAttribute("data-bs-theme") === "dark" ? "light" : "dark";
    html.setAttribute("data-bs-theme", novo);
    try { localStorage.setItem("erp-tema", novo); } catch (e) {}
  });
})();

// Formset: adicionar nova linha de itens
document.addEventListener("click", function (ev) {
  const btn = ev.target.closest("[data-add-form]");
  if (!btn) return;
  const prefixo = btn.dataset.addForm;
  const total = document.getElementById("id_" + prefixo + "-TOTAL_FORMS");
  const modelo = document.getElementById("modelo-" + prefixo);
  const corpo = document.getElementById("corpo-" + prefixo);
  const n = parseInt(total.value, 10);
  const html = modelo.innerHTML.replace(/__prefix__/g, n);
  corpo.insertAdjacentHTML("beforeend", html);
  total.value = n + 1;
});

// Copiar texto (PIX copia-e-cola)
document.addEventListener("click", function (ev) {
  const btn = ev.target.closest("[data-copiar]");
  if (!btn) return;
  const alvo = document.querySelector(btn.dataset.copiar);
  navigator.clipboard.writeText(alvo.textContent.trim()).then(() => {
    btn.innerHTML = '<i class="bi bi-check2"></i> Copiado';
  });
});

// Busca de CEP (ViaCEP) nos formulários com campo cep
document.addEventListener("change", function (ev) {
  if (ev.target.name !== "cep") return;
  const cep = ev.target.value.replace(/\D/g, "");
  if (cep.length !== 8) return;
  fetch("https://viacep.com.br/ws/" + cep + "/json/").then(r => r.json()).then(d => {
    if (d.erro) return;
    const f = ev.target.form;
    const set = (n, v) => { if (f.elements[n] && !f.elements[n].value) f.elements[n].value = v; };
    set("logradouro", d.logradouro); set("bairro", d.bairro); set("cidade", d.localidade); set("uf", d.uf);
  }).catch(() => {});
});
