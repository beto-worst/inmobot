/* Widget de chat embebible.
 *
 * Instalacion en el sitio del cliente: una linea antes de </body>.
 *   <script src="https://TU-SERVIDOR/widget/inmobot.js"
 *           data-inquilino="rednorte"
 *           data-api="https://TU-SERVIDOR"></script>
 *
 * Atributos opcionales:
 *   data-launcher="false"   no dibuja la burbuja flotante. El sitio abre el
 *                           chat desde sus propios botones.
 *
 * API publica, para los sitios que ponen sus propios disparadores:
 *   window.Inmobot.open()     abre el panel
 *   window.Inmobot.close()    lo cierra
 *   window.Inmobot.toggle()   alterna
 *   window.Inmobot.isOpen()   true si esta abierto
 *   window.Inmobot.ready      promesa que resuelve cuando el widget existe
 *
 * Tambien emite eventos en document, por si se prefiere escucharlos:
 *   "inmobot:ready", "inmobot:open", "inmobot:close"
 *
 * El contrato es ese y nada mas: las clases y la estructura de adentro son
 * internas y pueden cambiar. Un sitio que dependa de ellas se rompe sin
 * aviso en la siguiente version.
 *
 * Todo vive dentro de un Shadow DOM: el CSS del sitio anfitrion no puede
 * romper el widget, y el widget no puede romper el sitio. En un producto que
 * se instala en paginas ajenas eso no es un lujo.
 */
(function () {
  "use strict";

  // currentScript viene nulo cuando el script se inyecta desde un gestor de
  // etiquetas (Google Tag Manager) o desde un callback. Sin el respaldo, el
  // widget simplemente no aparece en esos sitios y nadie sabe por que.
  var script = document.currentScript ||
               document.querySelector("script[data-inquilino]");
  if (!script) {
    console.error("[inmobot] no encuentro la etiqueta script con data-inquilino");
    return;
  }

  var INQUILINO = script.getAttribute("data-inquilino") || "demo";
  var API = (script.getAttribute("data-api") || "").replace(/\/$/, "") ||
            new URL(script.src).origin;
  // Hay sitios que prefieren abrir el chat desde su propio boton -un CTA en
  // el hero, una tarjeta del catalogo- en vez de una burbuja flotante mas.
  var CON_BURBUJA = script.getAttribute("data-launcher") !== "false";

  var sesion = null;
  var ocupado = false;
  var abierto = false;

  var host = document.createElement("div");
  host.setAttribute("data-inmobot", INQUILINO);
  var raiz = host.attachShadow({ mode: "open" });
  document.body.appendChild(host);

  raiz.innerHTML =
    '<style>' +
    ':host{all:initial}' +
    '*{box-sizing:border-box;font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}' +
    '.burbuja{position:fixed;right:20px;bottom:20px;width:60px;height:60px;border-radius:50%;' +
      'background:var(--acento);color:#fff;border:0;cursor:pointer;font-size:26px;z-index:2147483000;' +
      'box-shadow:0 6px 20px rgba(0,0,0,.28);display:flex;align-items:center;justify-content:center}' +
    '.burbuja:hover{transform:scale(1.05)}' +
    '.panel{position:fixed;right:20px;bottom:92px;width:380px;max-width:calc(100vw - 40px);' +
      'height:560px;max-height:calc(100vh - 130px);background:#fff;border-radius:14px;display:none;' +
      'flex-direction:column;overflow:hidden;z-index:2147483000;box-shadow:0 12px 40px rgba(0,0,0,.3)}' +
    '.panel.abierto{display:flex}' +
    '.cabecera{background:var(--acento);color:#fff;padding:14px 16px;font-weight:600;font-size:15px;' +
      'display:flex;justify-content:space-between;align-items:center}' +
    // Un nombre largo -"Athena, tu ayudante para buscar hogar"- cabe en dos
    // renglones en vez de empujar la cruz de cerrar fuera de la cabecera.
    '.titulo{line-height:1.25;padding-right:10px}' +
    '.cerrar{background:none;border:0;color:#fff;font-size:22px;cursor:pointer;line-height:1;flex:none}' +
    '.hilo{flex:1;overflow-y:auto;padding:14px;background:#f6f7f9;display:flex;flex-direction:column;gap:10px}' +
    '.msg{max-width:85%;padding:10px 13px;border-radius:14px;font-size:14px;line-height:1.5;white-space:pre-wrap;word-wrap:break-word}' +
    '.bot{background:#fff;color:#1a1a1a;align-self:flex-start;border-bottom-left-radius:4px;box-shadow:0 1px 3px rgba(0,0,0,.08)}' +
    '.yo{background:var(--acento);color:#fff;align-self:flex-end;border-bottom-right-radius:4px}' +
    '.error{background:#fdecea;color:#8a1c12;align-self:flex-start;font-size:13px}' +
    '.props{display:flex;flex-direction:column;gap:8px;align-self:flex-start;width:100%}' +
    '.prop{background:#fff;border:1px solid #e3e5e8;border-radius:10px;padding:10px 12px;font-size:13px;' +
      'display:block;color:inherit;text-decoration:none;transition:border-color .15s,transform .15s}' +
    // Toda la tarjeta es el enlace: nadie quiere cazar un "ver ficha" de
    // ocho pixeles en un telefono.
    'a.prop:hover{border-color:var(--acento);transform:translateY(-1px)}' +
    'a.prop:focus-visible{outline:2px solid var(--acento);outline-offset:2px}' +
    '.prop b{display:block;color:#1a1a1a;margin-bottom:3px;font-size:13.5px}' +
    '.prop span{color:#5b6068;display:block;line-height:1.5}' +
    '.prop .ver{color:var(--acento);font-weight:600;display:inline-block;margin-top:5px}' +
    '.pie{padding:10px;border-top:1px solid #e6e8ea;display:flex;gap:8px;background:#fff}' +
    '.pie input{flex:1;border:1px solid #d6d9dd;border-radius:20px;padding:10px 14px;font-size:14px;outline:none}' +
    '.pie input:focus{border-color:var(--acento)}' +
    '.pie button{background:var(--acento);color:#fff;border:0;border-radius:20px;padding:0 18px;cursor:pointer;font-size:14px;font-weight:600}' +
    '.pie button:disabled{opacity:.5;cursor:default}' +
    '.puntos{display:flex;gap:4px;padding:12px 14px;align-self:flex-start}' +
    '.puntos i{width:7px;height:7px;background:#b6bbc2;border-radius:50%;animation:s 1.2s infinite}' +
    '.puntos i:nth-child(2){animation-delay:.2s}.puntos i:nth-child(3){animation-delay:.4s}' +
    '@keyframes s{0%,60%,100%{opacity:.3}30%{opacity:1}}' +
    '.nota{font-size:11px;color:#8a9099;text-align:center;padding:0 10px 8px;background:#fff}' +
    '</style>' +
    '<button class="burbuja" part="burbuja" aria-label="Abrir chat">&#128172;</button>' +
    '<div class="panel" role="dialog" aria-label="Asistente">' +
      '<div class="cabecera"><span class="titulo">Asistente</span>' +
        '<button class="cerrar" aria-label="Cerrar">&times;</button></div>' +
      '<div class="hilo"></div>' +
      '<div class="nota">Asistente automatico. No compartas datos bancarios ni oficiales.</div>' +
      '<div class="pie">' +
        '<input type="text" placeholder="Escribe tu mensaje..." aria-label="Mensaje">' +
        '<button class="enviar">Enviar</button>' +
      '</div>' +
    '</div>';

  var burbuja = raiz.querySelector(".burbuja");
  if (!CON_BURBUJA) burbuja.remove();
  var panel = raiz.querySelector(".panel");
  var hilo = raiz.querySelector(".hilo");
  var campo = raiz.querySelector(".pie input");
  var boton = raiz.querySelector(".enviar");
  var titulo = raiz.querySelector(".titulo");

  host.style.setProperty("--acento", "#1F5D4C");

  fetch(API + "/api/config?inquilino=" + encodeURIComponent(INQUILINO))
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (cfg) {
      if (!cfg) return;
      var marca = cfg.marca || {};
      if (marca.color) host.style.setProperty("--acento", marca.color);
      // El nombre de la IA manda sobre el de la inmobiliaria: para el cliente
      // final esta hablando con Athena (o como la haya rebautizado), no con
      // "un asistente" generico.
      titulo.textContent = marca.titulo || cfg.asistente || cfg.nombre || "Asistente";
      host.dataset.saludo = marca.saludo || "";
    })
    .catch(function () { /* el widget funciona igual con los valores por defecto */ });

  function burbujaTexto(texto, clase) {
    var d = document.createElement("div");
    d.className = "msg " + clase;
    d.textContent = texto;
    hilo.appendChild(d);
    hilo.scrollTop = hilo.scrollHeight;
    return d;
  }

  function pintarPropiedades(lista) {
    if (!lista || !lista.length) return;
    var caja = document.createElement("div");
    caja.className = "props";
    lista.slice(0, 4).forEach(function (p) {
      // Si hay liga, la tarjeta ES la liga; si no, es una tarjeta normal.
      var d = document.createElement(p.url ? "a" : "div");
      d.className = "prop";
      if (p.url) {
        d.href = p.url;
        d.target = "_blank";
        d.rel = "noopener noreferrer";
      }
      var b = document.createElement("b");
      b.textContent = p.titulo || p.id;
      d.appendChild(b);

      var detalle = [p.precio, p.ubicacion].filter(Boolean).join(" · ");
      var medidas = [];
      if (p.recamaras) medidas.push(p.recamaras + " rec");
      if (p.banos) medidas.push(p.banos + " baños");
      if (p.construccion_m2) medidas.push(p.construccion_m2 + " m²");
      if (medidas.length) detalle += "\n" + medidas.join(" · ");

      var s = document.createElement("span");
      s.textContent = detalle;
      d.appendChild(s);

      if (p.url) {
        var pie = document.createElement("span");
        pie.className = "ver";
        pie.textContent = "Ver ficha →";
        d.appendChild(pie);
      }
      caja.appendChild(d);
    });
    hilo.appendChild(caja);
    hilo.scrollTop = hilo.scrollHeight;
  }

  function escribiendo() {
    var d = document.createElement("div");
    d.className = "puntos";
    d.innerHTML = "<i></i><i></i><i></i>";
    hilo.appendChild(d);
    hilo.scrollTop = hilo.scrollHeight;
    return d;
  }

  // Aplica un evento del stream a la pantalla. `vista` guarda la burbuja que
  // se va llenando y el indicador de "escribiendo".
  function aplicarEvento(d, vista) {
    if (d.tipo === "sesion") {
      sesion = d.sesion || sesion;
      return;
    }
    if (d.tipo === "texto") {
      if (vista.espera) { vista.espera.remove(); vista.espera = null; }
      if (!vista.burbuja) vista.burbuja = burbujaTexto("", "bot");
      vista.burbuja.textContent += d.texto;
      hilo.scrollTop = hilo.scrollHeight;
      return;
    }
    if (d.tipo === "herramienta") {
      // Buscar en el inventario tarda; sin esta senal el chat se queda mudo
      // justo despues de decir "déjame revisar" y parece que se trabó.
      if (!vista.espera) vista.espera = escribiendo();
      return;
    }
    if (d.tipo === "propiedades") {
      pintarPropiedades(d.propiedades);
      // Lo que Athena diga despues va en una burbuja nueva, debajo de las
      // tarjetas: si no, el texto se sigue escribiendo arriba de ellas.
      vista.burbuja = null;
      return;
    }
    if (d.tipo === "error") {
      if (vista.espera) { vista.espera.remove(); vista.espera = null; }
      console.error("[inmobot]", d.detalle || d.mensaje);
      burbujaTexto(d.mensaje || "Hubo un problema.", "error");
    }
  }

  async function enviar() {
    var texto = campo.value.trim();
    if (!texto || ocupado) return;
    campo.value = "";
    burbujaTexto(texto, "yo");

    ocupado = true;
    boton.disabled = true;
    var vista = { burbuja: null, espera: escribiendo() };

    try {
      var r = await fetch(API + "/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ inquilino: INQUILINO, mensaje: texto, sesion: sesion })
      });

      if (!r.ok) {
        // El cliente final ve un mensaje amable, pero quien instala el widget
        // necesita el motivo real: sin esto, "no pude conectarme" no dice si
        // falta la llave de API, si el inquilino no existe o si es la red.
        var d = await r.json().catch(function () { return {}; });
        throw new Error("Inmobot " + r.status + ": " + (d.detail || "sin detalle"));
      }

      var lector = r.body.getReader();
      var decodificador = new TextDecoder();
      var pendiente = "";

      while (true) {
        var paso = await lector.read();
        if (paso.done) break;
        pendiente += decodificador.decode(paso.value, { stream: true });

        // Los eventos SSE se separan con linea en blanco. Un evento puede
        // llegar partido en dos lecturas, asi que lo incompleto se queda en
        // `pendiente` hasta que llegue el resto.
        var bloques = pendiente.split("\n\n");
        pendiente = bloques.pop();

        for (var i = 0; i < bloques.length; i++) {
          var linea = bloques[i].trim();
          if (linea.indexOf("data:") !== 0) continue;
          try {
            aplicarEvento(JSON.parse(linea.slice(5).trim()), vista);
          } catch (e) {
            console.warn("[inmobot] evento ilegible", e);
          }
        }
      }
    } catch (err) {
      if (vista.espera) vista.espera.remove();
      console.error("[inmobot]", err && err.message ? err.message : err);
      burbujaTexto("No pude conectarme. Intenta de nuevo en un momento.", "error");
    } finally {
      if (vista.espera) vista.espera.remove();
      ocupado = false;
      boton.disabled = false;
      campo.focus();
    }
  }

  function alternar() {
    abierto = !abierto;
    panel.classList.toggle("abierto", abierto);
    document.dispatchEvent(new CustomEvent(
      abierto ? "inmobot:open" : "inmobot:close"));
    if (abierto) {
      if (!hilo.children.length) {
        burbujaTexto(host.dataset.saludo ||
          "Hola, ¿qué tipo de propiedad andas buscando?", "bot");
      }
      campo.focus();
    }
  }

  function abrir() { if (!abierto) alternar(); }
  function cerrar() { if (abierto) alternar(); }

  if (CON_BURBUJA) burbuja.addEventListener("click", alternar);
  raiz.querySelector(".cerrar").addEventListener("click", alternar);
  boton.addEventListener("click", enviar);
  campo.addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); enviar(); }
  });

  // Superficie publica. Se expone al final, cuando todo lo de adentro ya
  // existe: un boton del sitio que llame a open() antes de tiempo no puede
  // encontrarse con medio widget.
  window.Inmobot = {
    open: abrir,
    close: cerrar,
    toggle: alternar,
    isOpen: function () { return abierto; },
    inquilino: INQUILINO,
    // Para el sitio que carga el script con defer o lazy y necesita saber
    // cuando ya puede llamar. Si llega tarde, igual resuelve.
    ready: Promise.resolve(),
  };
  document.dispatchEvent(new CustomEvent("inmobot:ready"));
})();
