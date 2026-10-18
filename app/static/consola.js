// Consola de la API de Tambor. Sin build y sin framework: fetch contra la misma API.
// La API no lista trabajos, así que el libro (IDs y su número de asiento) vive en localStorage.

const CLAVE_LIBRO = "tambor.libro.v1";
const CLAVE_REVISOR = "tambor.revisor";
const FINALES = new Set(["DONE", "FAILED"]);
const ETIQUETA = {
  PENDING: "En cola",
  RUNNING: "En curso",
  WAITING_APPROVAL: "Espera firma",
  DONE: "Resuelto",
  FAILED: "Falló",
};
const AGENTE = { supervisor: "Supervisor", investigador: "Investigador", analista: "Analista" };
const PHOENIX = "http://localhost:6006";

const $ = (sel) => document.querySelector(sel);

// Con las columnas apiladas (≤960px) el comprobante queda debajo del libro: lo traemos a la vista.
function mostrarHoja() {
  if (!matchMedia("(max-width: 960px)").matches) return;
  // Instantáneo: un re-render a mitad de un scroll suave lo cancela.
  $("#hoja").scrollIntoView({ block: "start" });
  $("#hoja").focus({ preventScroll: true });
}

// ---------- Estado ----------

function leerLibro() {
  try {
    const crudo = JSON.parse(localStorage.getItem(CLAVE_LIBRO) || "null");
    if (crudo && Array.isArray(crudo.asientos)) return crudo;
  } catch { /* localStorage bloqueado o corrupto: empezamos de cero */ }
  return { proximo: 1, asientos: [] };
}

function guardarLibro() {
  try { localStorage.setItem(CLAVE_LIBRO, JSON.stringify(libro)); } catch { /* sin persistencia */ }
}

const libro = leerLibro();               // { proximo, asientos: [{id, nro, pedido, creado}] }
const trabajos = new Map();              // id -> último Job devuelto por la API
const perdidos = new Set();              // ids que la API ya no tiene (TTL vencido o Redis limpio)
let seleccionado = location.hash.slice(1) || libro.asientos[0]?.id || null;
const estadoMostrado = new Map();        // id -> estado del último render, para estampar al cambiar

// ---------- Utilidades ----------

function h(tag, attrs = {}, ...hijos) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const hijo of hijos.flat()) {
    if (hijo == null || hijo === false) continue;
    el.append(hijo instanceof Node ? hijo : document.createTextNode(String(hijo)));
  }
  return el;
}

const nro = (n) => `Nº ${String(n).padStart(4, "0")}`;

function duracion(desde, hasta = new Date()) {
  const s = Math.max(0, Math.round((new Date(hasta) - new Date(desde)) / 1000));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  return `${m} min ${String(s % 60).padStart(2, "0")} s`;
}

const hora = (iso) =>
  new Date(iso).toLocaleTimeString("es-AR", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });

const segundos = (ms) => `${(ms / 1000).toFixed(1).replace(".", ",")} s`;

// La respuesta viene de un LLM: se pasa por DOMPurify antes de tocar el DOM.
// Si las librerías no cargaron (sin internet), se muestra como texto plano.
function prosa(markdown) {
  const div = h("div", { class: "prosa" });
  if (window.marked && window.DOMPurify) {
    div.innerHTML = window.DOMPurify.sanitize(window.marked.parse(markdown || ""));
    div.querySelectorAll("a").forEach((a) => { a.target = "_blank"; a.rel = "noopener"; });
  } else {
    div.append(h("p", { style: "white-space: pre-wrap" }, markdown || ""));
  }
  return div;
}

function sello(estado, { grande = false, estampar = false } = {}) {
  const clases = ["sello", grande && "sello--grande", estampar && "sello--estampa"].filter(Boolean).join(" ");
  return h("span", { class: clases, dataset: { estado } }, ETIQUETA[estado] || estado);
}

let temporizadorToast;
function avisar(texto) {
  let toast = $("#toast");
  if (!toast) {
    toast = h("div", { id: "toast", class: "aviso-toast", role: "status", "data-oculto": "true" });
    document.body.append(toast);
  }
  toast.textContent = texto;
  requestAnimationFrame(() => toast.setAttribute("data-oculto", "false"));
  clearTimeout(temporizadorToast);
  temporizadorToast = setTimeout(() => toast.setAttribute("data-oculto", "true"), 3200);
}

async function api(ruta, opciones = {}) {
  const r = await fetch(ruta, {
    ...opciones,
    headers: { "Content-Type": "application/json", ...(opciones.headers || {}) },
  });
  let cuerpo = null;
  try { cuerpo = await r.json(); } catch { /* sin cuerpo JSON */ }
  if (!r.ok) {
    const detalle = cuerpo?.detail;
    const texto = Array.isArray(detalle)
      ? detalle.map((d) => d.msg).join(". ")
      : detalle || `La API respondió ${r.status}.`;
    const error = new Error(texto);
    error.status = r.status;
    throw error;
  }
  return cuerpo;
}

// ---------- Libro (columna izquierda) ----------

function renderLibro(nuevoId = null) {
  const lista = $("#filas");
  lista.replaceChildren(
    ...libro.asientos.map((a) => {
      const job = trabajos.get(a.id);
      const estado = perdidos.has(a.id) ? null : job?.estado;
      const meta = perdidos.has(a.id)
        ? "La API ya no lo tiene"
        : job
          ? FINALES.has(job.estado)
            ? `${hora(job.creado)} · ${duracion(job.creado, job.actualizado)}`
            : `${hora(job.creado)} · hace ${duracion(job.creado)}`
          : "Consultando";
      return h("li", {},
        h("button", {
          type: "button",
          class: `fila${a.id === nuevoId ? " fila--nueva" : ""}`,
          "aria-current": a.id === seleccionado ? "true" : "false",
          onclick: () => { seleccionar(a.id); mostrarHoja(); },
        },
          h("span", { class: "fila__nro", "aria-label": nro(a.nro) }, String(a.nro).padStart(4, "0")),
          h("span", { class: "fila__texto" },
            h("span", { class: "fila__pedido" }, a.pedido),
            h("span", { class: "fila__meta" }, meta),
          ),
          estado ? sello(estado) : null,
        ),
      );
    }),
  );
  $("#libro-vacio").hidden = libro.asientos.length > 0;
  $("#limpiar").hidden = libro.asientos.length === 0;
  $("#proximo-nro").textContent = nro(libro.proximo);
}

function seleccionar(id) {
  seleccionado = id;
  history.replaceState(null, "", id ? `#${id}` : location.pathname);
  renderLibro();
  renderHoja({ abrir: true });
  if (id && !trabajos.has(id) && !perdidos.has(id)) consultar(id);
}

// ---------- Hoja (comprobante abierto) ----------

function renderHoja({ abrir = false } = {}) {
  const hoja = $("#hoja");
  const asiento = libro.asientos.find((a) => a.id === seleccionado);
  if (!asiento) {
    hoja.replaceChildren($("#tpl-vacio").content.cloneNode(true));
    return;
  }
  // No pisar lo que el usuario está escribiendo en la firma.
  const revisor = $("#revisor")?.value;
  const comentario = $("#comentario")?.value;
  const foco = document.activeElement?.id;

  const job = trabajos.get(asiento.id);
  const anterior = estadoMostrado.get(asiento.id);
  const cambio = job && anterior && anterior !== job.estado;
  if (job) estadoMostrado.set(asiento.id, job.estado);

  const comprobante = h("article", { class: `comprobante${abrir ? " comprobante--abriendo" : ""}` },
    cabeza(asiento, job, cambio),
    ...cuerpo(asiento, job),
  );
  hoja.replaceChildren(comprobante);

  if (revisor != null && $("#revisor")) $("#revisor").value = revisor;
  if (comentario != null && $("#comentario")) $("#comentario").value = comentario;
  if (foco && document.getElementById(foco)) document.getElementById(foco).focus();
}

function cabeza(asiento, job, cambio) {
  const datos = [["Asentado", job ? hora(job.creado) : "-"]];
  if (job) {
    datos.push([
      FINALES.has(job.estado) ? "Duración" : "Transcurrido",
      FINALES.has(job.estado) ? duracion(job.creado, job.actualizado) : duracion(job.creado),
    ]);
    datos.push(["Último cambio", hora(job.actualizado)]);
  }
  return h("header", { class: "comprobante__cabeza" },
    h("div", {},
      h("h2", { class: "comprobante__nro" }, nro(asiento.nro)),
      h("p", { class: "comprobante__pedido" }, asiento.pedido),
      h("dl", { class: "datos" },
        ...datos.map(([k, v]) => h("div", {}, h("dt", {}, k), h("dd", {}, v))),
        h("div", {},
          h("dt", {}, "Trabajo y sesión en Phoenix"),
          h("dd", {},
            asiento.id.slice(0, 12),
            h("button", {
              type: "button",
              class: "copiar",
              title: "Copiar el ID completo",
              "aria-label": "Copiar el ID completo del trabajo",
              onclick: () => navigator.clipboard?.writeText(asiento.id).then(() => avisar("ID copiado. En Phoenix, es la sesión del trabajo.")),
            }, h("i", { class: "ph ph-copy", "aria-hidden": "true" })),
          ),
        ),
      ),
    ),
    h("div", { class: "comprobante__sello-lugar" },
      job ? sello(job.estado, { grande: true, estampar: cambio }) : null,
    ),
  );
}

function cuerpo(asiento, job) {
  if (perdidos.has(asiento.id)) {
    return [h("section", { class: "apartado" },
      h("div", { class: "falla" },
        h("strong", {}, "La API ya no tiene este trabajo."),
        h("p", { style: "margin: 6px 0 0" }, "Los trabajos vencen a los 7 días, o Redis se limpió. Podés quitarlo del libro."),
        h("div", { style: "margin-top: 12px" },
          h("button", { type: "button", class: "boton boton--rojo", onclick: () => quitar(asiento.id) }, "Quitar del libro")),
      ))];
  }
  if (!job) return [h("section", { class: "apartado" }, esqueleto())];

  const partes = [];
  if (job.estado === "PENDING" || job.estado === "RUNNING") {
    partes.push(h("section", { class: "apartado" },
      h("h3", { class: "apartado__titulo" }, job.estado === "PENDING" ? "En la cola" : "Los agentes están trabajando"),
      esqueleto(),
      h("p", { class: "espera" }, job.estado === "PENDING"
        ? "Guardado en Redis. Un worker lo toma en cuanto se libera."
        : "El supervisor delega en el investigador y el analista. Suele tardar entre 15 y 60 segundos; la página se actualiza sola."),
    ));
  }
  if (job.estado === "WAITING_APPROVAL" && job.aprobacion) partes.push(firma(job));
  if (job.estado === "FAILED") {
    partes.push(h("section", { class: "apartado" },
      h("h3", { class: "apartado__titulo" }, "El trabajo falló"),
      h("div", { class: "falla" },
        h("p", { style: "margin: 0" }, "El worker capturó la excepción y dejó el trabajo en FAILED, así no queda esperando para siempre."),
        h("pre", {}, job.error || "Sin detalle."),
      ),
    ));
  }
  const r = job.resultado;
  if (r) {
    if (r.resultado_accion || r.revision) partes.push(accion(r));
    // El recorrido va antes de la respuesta, que suele ser larga: es lo que muestra cómo trabajaron los agentes.
    partes.push(recorrido(r));
    partes.push(h("section", { class: "apartado" },
      h("h3", { class: "apartado__titulo" }, "Respuesta"),
      prosa(r.respuesta_final),
    ));
  }
  return partes;
}

function esqueleto() {
  return h("div", { class: "esqueleto", "aria-hidden": "true" }, h("span"), h("span"), h("span"), h("span"));
}

function firma(job) {
  const ap = job.aprobacion;
  const enviar = async (aprobado, boton) => {
    const revisor = $("#revisor").value.trim();
    const comentario = $("#comentario").value.trim();
    const alerta = $("#firma-alerta");
    alerta.hidden = true;
    if (!revisor) {
      $("#revisor").setAttribute("aria-invalid", "true");
      alerta.textContent = "Escribí tu nombre: la firma queda registrada con el reembolso.";
      alerta.hidden = false;
      $("#revisor").focus();
      return;
    }
    $("#revisor").removeAttribute("aria-invalid");
    try { localStorage.setItem(CLAVE_REVISOR, revisor); } catch { /* sin persistencia */ }
    document.querySelectorAll(".firma .boton").forEach((b) => { b.disabled = true; });
    boton.setAttribute("aria-busy", "true");
    try {
      const actualizado = await api(`/tasks/${job.id}/approve`, {
        method: "POST",
        body: JSON.stringify({ aprobado, revisor, comentario }),
      });
      trabajos.set(job.id, actualizado);
      avisar(aprobado ? "Firmado. El grafo se reanuda y registra el reembolso." : "Rechazado. El grafo termina sin ejecutar nada.");
      renderLibro();
      renderHoja();
      programar(job.id);
    } catch (error) {
      alerta.textContent = error.status === 409
        ? "Este trabajo ya recibió una firma. Actualizo su estado."
        : `No se pudo firmar: ${error.message}`;
      alerta.hidden = false;
      document.querySelectorAll(".firma .boton").forEach((b) => { b.disabled = false; b.removeAttribute("aria-busy"); });
      if (error.status === 409) consultar(job.id);
    }
  };

  let revisorGuardado = "";
  try { revisorGuardado = localStorage.getItem(CLAVE_REVISOR) || ""; } catch { /* nada */ }

  const formulario = h("form", { class: "firma__form", novalidate: true, onsubmit: (e) => e.preventDefault() },
    h("div", { class: "campo" },
      h("label", { class: "campo__etiqueta", for: "revisor" }, "Quién firma"),
      h("input", { class: "campo__entrada", id: "revisor", name: "revisor", maxlength: "80", autocomplete: "name", value: revisorGuardado, placeholder: "Tu nombre" }),
    ),
    h("div", { class: "campo" },
      h("label", { class: "campo__etiqueta", for: "comentario" }, "Comentario (opcional)"),
      h("input", { class: "campo__entrada", id: "comentario", name: "comentario", maxlength: "500", placeholder: "Ej.: verificado con el comercio" }),
    ),
    h("div", { class: "firma__acciones" },
      h("button", { class: "boton boton--sello", type: "button", onclick: (e) => enviar(true, e.currentTarget) },
        h("i", { class: "ph ph-seal-check", "aria-hidden": "true" }), h("span", { class: "boton__texto" }, "Aprobar y firmar")),
      h("button", { class: "boton boton--rojo", type: "button", onclick: (e) => enviar(false, e.currentTarget) },
        h("span", { class: "boton__texto" }, "Rechazar")),
      h("span", { class: "firma__aviso" }, "Solo se reembolsan cobros aprobados, y nunca dos veces."),
    ),
  );

  return h("section", { class: "firma", "aria-labelledby": "firma-titulo" },
    h("h3", { class: "firma__titulo", id: "firma-titulo" }, "Firma requerida"),
    h("p", { class: "firma__pregunta" }, ap.pregunta || "¿Aprobás la acción?"),
    h("ul", { class: "firma__cobros", "aria-label": "Cobros a reembolsar" },
      ...(ap.accion?.cobro_ids || []).map((c) => h("li", {}, c))),
    // El análisis va antes del formulario: se lee lo que encontraron los agentes y después se decide.
    h("div", { class: "firma__analisis", tabindex: "0", "aria-label": "Análisis de los agentes" },
      h("h4", { class: "firma__subtitulo" }, "Lo que encontraron los agentes"),
      prosa(ap.analisis),
    ),
    h("p", { id: "firma-alerta", class: "alerta", role: "alert", hidden: true }),
    formulario,
  );
}

function accion(r) {
  const lineas = (r.resultado_accion || "").split("\n").filter(Boolean);
  const rev = r.revision;
  return h("section", { class: "apartado" },
    h("h3", { class: "apartado__titulo" }, rev && !rev.aprobado ? "Reembolso rechazado" : "Reembolso"),
    lineas.length
      ? h("ul", { class: "accion" },
        ...lineas.map((l) => {
          // "ya tenía un reembolso registrado" no es un reembolso nuevo.
          const hecho = l.includes(": reembolso de ");
          return h("li", { class: hecho ? "hecho" : "no" },
            h("i", { class: `ph ${hecho ? "ph-check-circle" : "ph-minus-circle"}`, "aria-hidden": "true" }), l);
        }))
      : h("p", { class: "apartado__nota" }, "No se ejecutó nada."),
    rev ? h("p", { class: "revision" },
      `${rev.aprobado ? "Firmó" : "Rechazó"}: ${rev.revisor}${rev.comentario ? `. "${rev.comentario}"` : ""}`) : null,
  );
}

// Supervisor, especialistas y síntesis, en el orden en que trabajaron.
function recorrido(r) {
  const aportes = [...(r.aportes || [])];
  const filas = [];
  for (const d of r.decisiones || []) {
    filas.push(h("li", { class: "paso", dataset: { agente: "supervisor" } },
      h("span", { class: "paso__quien" }, "Supervisor"),
      h("div", { class: "paso__que" },
        h("p", {}, d.siguiente === "sintesis" ? "Pasa a la síntesis." : `Delega en ${AGENTE[d.siguiente]?.toLowerCase() || d.siguiente}: ${d.instruccion}`),
        h("p", { class: "paso__detalle" }, d.motivo),
      ),
      h("span", { class: "paso__tiempo" }),
    ));
    if (d.siguiente !== "sintesis" && aportes.length) {
      const a = aportes.shift();
      filas.push(h("li", { class: `paso${a.error ? " paso--error" : ""}`, dataset: { agente: a.agente } },
        h("span", { class: "paso__quien" }, AGENTE[a.agente] || a.agente),
        h("div", { class: "paso__que" },
          a.herramientas.length
            ? h("div", { class: "paso__herr" }, ...a.herramientas.map((t) => h("code", {}, t)))
            : h("p", { class: "paso__detalle", style: "margin-top: 0" }, a.error ? "Falló antes de usar herramientas." : "Sin herramientas."),
          h("details", { class: "desplegable" }, h("summary", {}, "Ver aporte"), prosa(a.contenido)),
        ),
        h("span", { class: "paso__tiempo" }, segundos(a.ms)),
      ));
    }
  }
  filas.push(h("li", { class: "paso" },
    h("span", { class: "paso__quien" }, "Síntesis"),
    h("div", { class: "paso__que" }, h("p", {}, "Redacta la respuesta con los aportes.")),
    h("span", { class: "paso__tiempo" }),
  ));
  return h("section", { class: "apartado" },
    h("h3", { class: "apartado__titulo" }, "Recorrido"),
    h("p", { class: "apartado__nota" }, "Quién trabajó y qué decidió el supervisor en cada vuelta. Las llamadas al modelo, con tokens y costo, están en Phoenix."),
    h("ol", { class: "recorrido" }, ...filas),
  );
}

function quitar(id) {
  libro.asientos = libro.asientos.filter((a) => a.id !== id);
  trabajos.delete(id);
  perdidos.delete(id);
  guardarLibro();
  seleccionado = libro.asientos[0]?.id || null;
  seleccionar(seleccionado);
}

// ---------- Consultas en vivo ----------

const pendientes = new Map(); // id -> timeout

async function consultar(id) {
  try {
    const job = await api(`/tasks/${id}`);
    const previo = trabajos.get(id);
    trabajos.set(id, job);
    perdidos.delete(id);
    if (previo && previo.actualizado === job.actualizado && previo.estado === job.estado) {
      programar(id);
      return;
    }
    if (previo && previo.estado !== job.estado) {
      const asiento = libro.asientos.find((a) => a.id === id);
      if (asiento) avisar(`${nro(asiento.nro)}: ${ETIQUETA[job.estado].toLowerCase()}.`);
    }
  } catch (error) {
    if (error.status === 404) perdidos.add(id);
    else { programar(id, 4000); return; }
  }
  renderLibro();
  if (id === seleccionado) renderHoja();
  programar(id);
}

function programar(id, espera) {
  clearTimeout(pendientes.get(id));
  const job = trabajos.get(id);
  if (perdidos.has(id) || (job && FINALES.has(job.estado))) { pendientes.delete(id); return; }
  // En espera de firma no cambia solo: se consulta más despacio.
  const ms = espera ?? (job?.estado === "WAITING_APPROVAL" ? 8000 : 1500);
  pendientes.set(id, setTimeout(() => {
    if (document.hidden) { programar(id, 2000); return; }
    consultar(id);
  }, ms));
}

// El reloj de "transcurrido" avanza aunque no cambie el estado.
setInterval(() => {
  if (document.hidden) return;
  const vivo = libro.asientos.some((a) => {
    const j = trabajos.get(a.id);
    return j && !FINALES.has(j.estado);
  });
  if (!vivo) return;
  renderLibro();
  const job = trabajos.get(seleccionado);
  if (job && !FINALES.has(job.estado) && job.estado !== "WAITING_APPROVAL") {
    const dd = document.querySelector(".datos div:nth-child(2) dd");
    if (dd) dd.textContent = duracion(job.creado);
  }
}, 1000);

async function salud() {
  const el = $("#salud");
  try {
    await api("/health");
    el.dataset.estado = "ok";
    el.querySelector(".salud__texto").textContent = "API y Redis en línea";
  } catch {
    el.dataset.estado = "caida";
    el.querySelector(".salud__texto").textContent = "Sin conexión con la API";
  }
}

// ---------- Nueva orden ----------

function prepararBoleta() {
  const form = $("#boleta");
  const texto = $("#pedido");
  const error = $("#pedido-error");
  const contador = $("#contador");
  const actualizarContador = () => { contador.textContent = `${texto.value.length} / 2000`; };

  texto.addEventListener("input", () => {
    actualizarContador();
    if (texto.value.trim().length >= 5) { error.hidden = true; texto.removeAttribute("aria-invalid"); }
  });
  texto.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) form.requestSubmit();
  });
  document.querySelectorAll(".ejemplo").forEach((b) => b.addEventListener("click", () => {
    texto.value = b.dataset.pedido;
    actualizarContador();
    error.hidden = true;
    texto.removeAttribute("aria-invalid");
    texto.focus();
  }));

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const pedido = texto.value.trim();
    if (pedido.length < 5) {
      error.textContent = "Escribí al menos 5 caracteres, o elegí un ejemplo.";
      error.hidden = false;
      texto.setAttribute("aria-invalid", "true");
      texto.focus();
      return;
    }
    const boton = $("#encolar");
    boton.disabled = true;
    boton.setAttribute("aria-busy", "true");
    try {
      const job = await api("/tasks", { method: "POST", body: JSON.stringify({ pedido }) });
      libro.asientos.unshift({ id: job.id, nro: libro.proximo++, pedido, creado: job.creado });
      libro.asientos = libro.asientos.slice(0, 50);
      guardarLibro();
      trabajos.set(job.id, job);
      estadoMostrado.set(job.id, job.estado);
      texto.value = "";
      actualizarContador();
      seleccionado = job.id;
      history.replaceState(null, "", `#${job.id}`);
      renderLibro(job.id);
      renderHoja({ abrir: true });
      mostrarHoja();
      programar(job.id);
    } catch (err) {
      error.textContent = `No se pudo encolar: ${err.message}`;
      error.hidden = false;
    } finally {
      boton.disabled = false;
      boton.removeAttribute("aria-busy");
    }
  });
  actualizarContador();
}

$("#limpiar").addEventListener("click", () => {
  if (!confirm("¿Quitar todos los asientos del libro? Los trabajos siguen en Redis.")) return;
  libro.asientos = [];
  guardarLibro();
  trabajos.clear();
  perdidos.clear();
  seleccionar(null);
});

// ---------- Arranque ----------

prepararBoleta();
if (seleccionado && !libro.asientos.some((a) => a.id === seleccionado)) seleccionado = libro.asientos[0]?.id || null;
renderLibro();
renderHoja();
libro.asientos.forEach((a) => consultar(a.id));
salud();
setInterval(salud, 15000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) salud(); });
