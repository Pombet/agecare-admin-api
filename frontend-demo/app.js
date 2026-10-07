"use strict";

const API_BASE = ["localhost", "127.0.0.1"].includes(location.hostname)
  ? "https://agecare-admin-pruebas.vercel.app/api/v1/admin"
  : location.origin + "/api/v1/admin";
const API_ORIGIN = new URL(API_BASE).origin;
const $ = (id) => document.getElementById(id);
const labels = {
  admin: "Administrador", support: "Soporte", analyst: "Analista", editor: "Editora",
  moderator: "Moderación", draft: "Borrador", published: "Publicado", archived: "Archivado",
  investigating: "En investigación", observing: "En observación", resolved: "Resuelto",
  completed: "Completado", operational: "Operativo", degraded: "Degradado", outage: "Interrupción",
  open: "Abierto", in_progress: "En curso", waiting_user: "Esperando usuario", closed: "Cerrado",
  low: "Baja", medium: "Media", high: "Alta", critical: "Crítica",
  family: "Familiares", caregiver: "Cuidadoras", elder: "Adultos mayores", doctor: "Médicos",
  account_access: "Acceso y cuenta", wearable_sync: "Sincronización", alerts_push: "Alertas",
  billing_plans: "Pagos y planes", medications: "Medicamentos", other: "Otros",
  database: "Base de datos", api_core: "API central", auth: "Autenticación",
};
const headings = {
  overview: ["Resumen", "Una vista de los datos y funcionalidades disponibles."],
  products: ["Productos", "Crea, publica y archiva artículos del marketplace."],
  incidents: ["Incidentes", "Registra situaciones de prueba y su resolución."],
  support: ["Soporte", "Consulta los tickets y sus indicadores."],
  staff: ["Personal", "Cuentas de administración y sus roles."],
  activity: ["Actividad", "Acciones que quedaron registradas en la auditoría."],
};
const accounts = {
  admin: ["admin@wellq.co.uk", "Admin123!"],
  soporte: ["soporte@wellq.co.uk", "Soporte123!"],
  analista: ["analista@wellq.co.uk", "Analista123!"],
  editora: ["editora@wellq.co.uk", "Editora123!"],
};
const pages = { products: 1, incidents: 1, support: 1, staff: 1, activity: 1 };
let session = null;
let profile = null;
let currentView = "overview";
let refreshPromise = null;
let sessionGeneration = 0;
let incidentToResolve = null;

function el(tag, attributes = {}, children = []) {
  const item = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (name.startsWith("on") && typeof value === "function") item.addEventListener(name.slice(2), value);
    else if (typeof value === "boolean") { if (value) item.setAttribute(name, ""); }
    else if (value != null) item.setAttribute(name, String(value));
  }
  for (const child of Array.isArray(children) ? children : [children]) {
    if (child != null) item.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return item;
}

const text = (value) => value == null || value === "" ? "—" : String(value);
const number = (value) => value == null ? "—" : new Intl.NumberFormat("es-CL").format(value);
const money = (value) => value == null ? "Sin precio" : new Intl.NumberFormat("es-CL", { style: "currency", currency: "CLP", maximumFractionDigits: 0 }).format(value);
function date(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "—" : new Intl.DateTimeFormat("es-CL", { dateStyle: "short", timeStyle: "short" }).format(parsed);
}
function pill(value) {
  return el("span", { class: "pill " + String(value).replace(/[^a-z_]/g, "") }, labels[value] || text(value));
}
function description(primary, secondary) {
  return el("div", {}, [el("strong", {}, text(primary)), el("span", { class: "secondary" }, text(secondary))]);
}
function action(label, handler, disabled = false) {
  return el("button", { type: "button", class: "button small-button", onclick: handler, disabled }, label);
}
function notice(message, error = false) {
  $("notice").textContent = message;
  $("notice").className = "notice" + (error ? " error" : "");
  $("notice").hidden = !message;
}
function errorMessage(error) {
  let message = error.message || "No se pudo completar la operación.";
  if (error.details?.length) message += "\n" + error.details.map((detail) => text(detail.field) + ": " + text(detail.issue)).join("\n");
  if (error.requestId) message += "\nReferencia: " + error.requestId;
  return message;
}
function has(module) { return !!profile?.permissions?.includes(module); }
function canWrite(module) {
  const writers = { marketplace: ["admin", "editor"], ops: ["admin", "support"] };
  return !!writers[module]?.includes(profile?.role);
}

async function send(path, options = {}, token = null) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 45000);
  try {
    const headers = { Accept: "application/json", ...options.headers };
    if (options.body != null) headers["Content-Type"] = "application/json";
    if (token) headers.Authorization = "Bearer " + token;
    const response = await fetch(API_BASE + path, { ...options, headers, credentials: "omit", signal: controller.signal });
    const data = response.status === 204 ? null : await response.json().catch(() => null);
    return { response, data };
  } catch (error) {
    throw new Error(error.name === "AbortError"
      ? "La API tardó demasiado. Intenta de nuevo."
      : "No se pudo conectar con la API. Revisa la conexión y vuelve a intentar.");
  } finally { clearTimeout(timer); }
}

function apiError(response, data) {
  const error = new Error(data?.error?.message || "No se pudo completar la solicitud (HTTP " + response.status + ").");
  error.details = data?.error?.details;
  error.requestId = data?.error?.request_id;
  error.status = response.status;
  return error;
}

async function renewSession() {
  if (!session) throw new Error("Inicia sesión para continuar.");
  if (!refreshPromise) {
    const generation = sessionGeneration;
    const token = session.refresh_token;
    refreshPromise = (async () => {
      const { response, data } = await send("/auth/refresh", { method: "POST", body: JSON.stringify({ refresh_token: token }) });
      if (!response.ok || generation !== sessionGeneration) {
        if (generation === sessionGeneration) clearSession("La sesión expiró. Inicia sesión de nuevo.");
        throw new Error("La sesión expiró. Inicia sesión de nuevo.");
      }
      session = { ...session, ...data };
    })().finally(() => { refreshPromise = null; });
  }
  await refreshPromise;
}

async function request(path, options = {}) {
  if (!session) throw new Error("Inicia sesión para continuar.");
  let result = await send(path, options, session.access_token);
  if (result.response.status === 401) {
    await renewSession();
    result = await send(path, options, session.access_token);
  }
  if (!result.response.ok) throw apiError(result.response, result.data);
  return result.data;
}

function clearSession(message = "") {
  sessionGeneration++;
  session = null;
  profile = null;
  incidentToResolve = null;
  $("application").hidden = true;
  $("login-screen").hidden = false;
  $("logout").hidden = true;
  for (const dialog of document.querySelectorAll("dialog[open]")) dialog.close();
  for (const area of document.querySelectorAll(".table-wrap")) area.replaceChildren();
  $("metrics").replaceChildren();
  $("support-metrics").replaceChildren();
  $("record-content").replaceChildren();
  $("record-sql").textContent = "";
  $("login-error").textContent = message;
  $("login-error").hidden = !message;
}

async function login(event) {
  event.preventDefault();
  const button = event.currentTarget.querySelector('[type="submit"]');
  button.disabled = true;
  button.textContent = "Conectando…";
  $("login-error").hidden = true;
  try {
    const body = { email: $("email").value.trim(), password: $("password").value };
    if ($("otp").value.trim()) body.otp_code = $("otp").value.trim();
    const { response, data } = await send("/auth/login", { method: "POST", body: JSON.stringify(body) });
    if (!response.ok) throw apiError(response, data);
    sessionGeneration++;
    session = data;
    profile = await request("/auth/me");
    $("account-name").textContent = profile.full_name;
    $("account-role").textContent = labels[profile.role] || profile.role;
    $("avatar").textContent = profile.full_name.split(" ").map((part) => part[0]).slice(0, 2).join("").toUpperCase();
    for (const button of document.querySelectorAll("[data-permission]")) button.hidden = !has(button.dataset.permission);
    $("new-product").hidden = !canWrite("marketplace");
    $("new-incident").hidden = !canWrite("ops");
    $("login-screen").hidden = true;
    $("application").hidden = false;
    $("logout").hidden = false;
    window.scrollTo(0, 0);
    for (const page of Object.keys(pages)) pages[page] = 1;
    notice("");
    await go("overview");
  } catch (error) {
    clearSession(errorMessage(error));
  } finally {
    button.disabled = false;
    button.textContent = "Entrar a la consola";
  }
}

function table(target, headers, rows, empty = "No hay registros para estos filtros.") {
  const destination = $(target);
  if (!rows.length) { destination.replaceChildren(el("p", { class: "empty" }, empty)); return; }
  const head = el("thead", {}, el("tr", {}, headers.map((heading) => el("th", { scope: "col" }, heading))));
  const body = el("tbody", {}, rows.map((row) => el("tr", {}, row.map((cell) => el("td", {}, cell ?? "—")))));
  destination.replaceChildren(el("table", {}, [head, body]));
}
function loading(target) { $(target).replaceChildren(el("p", { class: "empty" }, "Cargando datos…")); }
function metrics(target, values) {
  $(target).replaceChildren(...values.map(([label, value, note]) => el("div", { class: "metric" }, [
    el("p", { class: "metric-label" }, label), el("p", { class: "metric-value" }, value),
    el("p", { class: "metric-note" }, note),
  ])));
}
function pager(target, view, data) {
  const total = data.total;
  const page = data.page;
  const size = data.page_size;
  const pageCount = Math.max(1, Math.ceil(total / size));
  $(target).replaceChildren(
    el("span", {}, number(total) + " registros · Página " + page + " de " + pageCount),
    el("div", { class: "pager-buttons" }, [
      action("Anterior", () => { pages[view] = page - 1; go(view); }, page <= 1),
      action("Siguiente", () => { pages[view] = page + 1; go(view); }, page * size >= total),
    ]),
  );
}
function query(values) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) if (value !== "" && value != null) params.set(key, String(value));
  return "?" + params.toString();
}

async function overview() {
  const endpoints = [];
  if (has("metrics")) endpoints.push(["commercial", "/metrics/commercial/summary?period=current_month"], ["adoption", "/metrics/features/adoption?days=30"]);
  if (has("ops")) endpoints.push(["ops", "/ops/status"]);
  if (has("support")) endpoints.push(["support", "/support/summary"]);
  $("metrics").replaceChildren(el("p", { class: "muted" }, "Cargando resumen…"));
  const results = await Promise.allSettled(endpoints.map(async ([key, path]) => [key, await request(path)]));
  if (!session) return;
  const data = Object.fromEntries(results.filter((result) => result.status === "fulfilled").map((result) => result.value));
  const failures = results.filter((result) => result.status === "rejected");
  const cards = [];
  if (data.commercial) {
    cards.push(["Usuarios activos", number(data.commercial.active_users), "Período: mes actual"]);
    cards.push(["Usuarios de pago", number(data.commercial.paying_users), "Datos de demostración"]);
    cards.push(["Ingreso mensual", money(data.commercial.mrr_clp), "MRR registrado en la API"]);
  }
  if (data.support) cards.push(["Tickets abiertos", number(data.support.open), "Pendientes de atención"]);
  if (cards.length) metrics("metrics", cards);
  else $("metrics").replaceChildren(el("p", { class: "muted" }, failures.length ? "No se pudo cargar el resumen." : "Tu rol puede trabajar en los módulos habilitados del menú."));
  if (data.ops) {
    $("overall-status").replaceChildren(pill(data.ops.overall));
    table("components", ["Componente", "Estado"], data.ops.components.map((component) => [component.name, pill(component.status)]));
  } else {
    $("overall-status").replaceChildren();
    $("components").replaceChildren(el("p", { class: "empty" }, has("ops") ? "No se pudo consultar el estado." : "Este rol no tiene acceso al estado operativo."));
  }
  $("adoption-card").hidden = !has("metrics");
  if (data.adoption) table("adoption", ["Funcionalidad", ...data.adoption.roles.map((role) => labels[role] || role)],
    data.adoption.features.map((feature) => [feature.name, ...feature.adoption.map((value) => value == null ? "No aplica" : number(Math.round(value * 100)) + "%")]));
  else if (has("metrics")) $("adoption").replaceChildren(el("p", { class: "empty" }, "No se pudo consultar la adopción."));
  if (failures.length) notice(errorMessage(failures[0].reason), true);
}

function showRecord(title, record, tableName, extra = {}) {
  $("record-title").textContent = title;
  const fieldLabels = {
    id: "Identificador", number: "Número", name: "Nombre", title: "Título", subject: "Asunto",
    status: "Estado", category: "Categoría", vendor: "Proveedor", price_clp: "Precio",
    external_url: "Enlace externo", image_url: "Imagen", updated_at: "Actualizado",
    created_at: "Creado", started_at: "Inicio", resolved_at: "Resuelto", description: "Descripción",
    resolution: "Resolución", component_key: "Componente", severity: "Severidad", is_maintenance: "Mantenimiento",
    full_name: "Nombre", email: "Correo", role: "Rol", is_active: "Cuenta activa",
    mfa_enabled: "MFA activo", last_login_at: "Último acceso", action: "Acción", actor_name: "Cuenta",
    entity_type: "Entidad", entity_id: "Registro afectado", ...extra,
  };
  const fields = el("dl", { class: "record-fields" });
  for (const [key, label] of Object.entries(fieldLabels)) {
    if (!(key in record)) continue;
    let value = record[key];
    if (key.endsWith("_at")) value = date(value);
    else if (typeof value === "boolean") value = value ? "Sí" : "No";
    else if (key === "price_clp") value = money(value);
    else if (["status", "role", "category", "severity", "component_key"].includes(key)) value = labels[value] || text(value);
    fields.append(el("dt", {}, label), el("dd", {}, text(value)));
  }
  $("record-content").replaceChildren(fields);
  const allowedTables = ["marketplace_products", "ops_incidents", "support_tickets", "admin_users", "audit_log"];
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  $("sql-section").hidden = !allowedTables.includes(tableName) || !uuid.test(record.id);
  $("record-sql").textContent = $("sql-section").hidden ? "" : "SELECT *\nFROM admin." + tableName + "\nWHERE id = '" + record.id + "';";
  $("record-dialog").showModal();
}

async function productState(product, state, event) {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    await request("/marketplace/products/" + product.id, { method: "PATCH", body: JSON.stringify({ status: state }) });
    await products();
    notice("Producto " + (state === "published" ? "publicado" : "archivado") + ". El cambio quedó guardado.");
  } catch (error) { notice(errorMessage(error), true); }
  finally { button.disabled = false; }
}

async function products() {
  loading("products-table");
  const data = await request("/marketplace/products" + query({ page: pages.products, page_size: 10, q: $("product-search").value.trim(), status: $("product-status").value }));
  if (!session) return;
  table("products-table", ["Artículo", "Categoría", "Precio", "Estado", "Actualizado", "Acciones"], data.items.map((product) => {
    const buttons = [action("Ver registro", () => showRecord("Producto guardado", product, "marketplace_products"))];
    if (canWrite("marketplace") && product.status === "draft") buttons.push(action("Publicar", (event) => productState(product, "published", event)));
    if (canWrite("marketplace") && product.status !== "archived") buttons.push(action("Archivar", (event) => productState(product, "archived", event)));
    return [description(product.name, product.vendor), product.category, money(product.price_clp), pill(product.status), date(product.updated_at), el("div", { class: "row-actions" }, buttons)];
  }));
  pager("products-pager", "products", data);
}

function resolveIncident(incident) {
  incidentToResolve = incident;
  $("resolution-subject").textContent = incident.title;
  $("resolution-form").reset();
  $("resolution-form").querySelector(".inline-error").hidden = true;
  $("resolution-dialog").showModal();
}

async function incidents() {
  loading("incidents-table");
  const data = await request("/ops/incidents" + query({ days: 365, page: pages.incidents, page_size: 10, status: $("incident-status").value }));
  if (!session) return;
  table("incidents-table", ["Incidente", "Severidad", "Estado", "Inicio", "Acciones"], data.items.map((incident) => {
    const buttons = [action("Ver registro", () => showRecord("Incidente guardado", incident, "ops_incidents"))];
    if (canWrite("ops") && ["investigating", "observing"].includes(incident.status)) buttons.push(action("Resolver", () => resolveIncident(incident)));
    return [description(incident.title, labels[incident.component_key] || incident.component_key || "General"), pill(incident.severity), pill(incident.status), date(incident.started_at), el("div", { class: "row-actions" }, buttons)];
  }));
  pager("incidents-pager", "incidents", data);
}

async function ticketDetail(ticket, event) {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const detail = await request("/support/tickets/" + ticket.id);
    showRecord("Ticket #" + detail.number, detail, "support_tickets");
  } catch (error) { notice(errorMessage(error), true); }
  finally { button.disabled = false; }
}

async function support() {
  loading("tickets-table");
  const [summary, data] = await Promise.all([
    request("/support/summary"),
    request("/support/tickets" + query({ page: pages.support, page_size: 10, q: $("ticket-search").value.trim(), status: $("ticket-status").value })),
  ]);
  if (!session) return;
  metrics("support-metrics", [
    ["Abiertos", number(summary.open), "Pendientes de atención"],
    ["En curso", number(summary.in_progress), "Trabajo en progreso"],
    ["Esperando usuario", number(summary.waiting_user), "Pendientes de respuesta"],
    ["Resueltos", number(summary.resolved_30d), "Últimos 30 días"],
  ]);
  table("tickets-table", ["Ticket", "Solicitante", "Prioridad", "Estado", "Creado", ""], data.items.map((ticket) => [
    description("#" + ticket.number, ticket.subject), description(ticket.requester.name, ticket.requester.email),
    pill(ticket.priority), pill(ticket.status), date(ticket.created_at), action("Ver detalle", (event) => ticketDetail(ticket, event)),
  ]));
  pager("tickets-pager", "support", data);
}

async function staff() {
  loading("staff-table");
  const data = await request("/users" + query({ page: pages.staff, page_size: 10 }));
  if (!session) return;
  table("staff-table", ["Cuenta", "Rol", "Estado", "MFA", "Último acceso", ""], data.items.map((user) => [
    description(user.full_name, user.email), labels[user.role] || user.role,
    el("span", { class: "badge " + (user.is_active ? "connected" : "") }, user.is_active ? "Activa" : "Inactiva"),
    user.mfa_enabled ? "Sí" : "No", date(user.last_login_at),
    action("Ver registro", () => showRecord("Cuenta de administración", user, "admin_users")),
  ]));
  pager("staff-pager", "staff", data);
}

const actionLabels = {
  "auth.login": "Inicio de sesión", "auth.logout": "Cierre de sesión", "auth.login_failed": "Acceso rechazado",
  "marketplace.product_create": "Producto creado", "marketplace.product_update": "Producto modificado",
  "ops.incident_create": "Incidente creado", "ops.incident_update": "Incidente modificado",
  "ticket.create": "Ticket creado", "ticket.update": "Ticket modificado", "ticket.reply": "Respuesta a ticket",
  "staff.update": "Cuenta modificada", "staff.create": "Cuenta creada", "settings.update": "Configuración modificada",
};
async function activity() {
  loading("activity-table");
  const data = await request("/audit-log" + query({ page: pages.activity, page_size: 15 }));
  if (!session) return;
  table("activity-table", ["Fecha", "Cuenta", "Acción", "Entidad", ""], data.items.map((entry) => [
    date(entry.created_at), text(entry.actor?.name), actionLabels[entry.action] || entry.action,
    text(entry.entity_type), action("Ver registro", () => showRecord("Registro de auditoría", { ...entry, actor_name: entry.actor?.name }, "audit_log")),
  ]));
  pager("activity-pager", "activity", data);
}

async function go(view) {
  if (!session) return;
  const permission = { products: "marketplace", incidents: "ops", support: "support", staff: "staff", activity: "audit" }[view];
  if (permission && !has(permission)) return;
  const changedView = currentView !== view;
  currentView = view;
  if (changedView) window.scrollTo(0, 0);
  const generation = sessionGeneration;
  for (const panel of document.querySelectorAll(".view")) panel.hidden = panel.id !== "view-" + view;
  for (const button of document.querySelectorAll("[data-view]")) {
    button.classList.toggle("active", button.dataset.view === view);
    if (button.dataset.view === view) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  }
  $("page-title").textContent = headings[view][0];
  $("page-description").textContent = headings[view][1];
  $("refresh").disabled = true;
  $("view-" + view).setAttribute("aria-busy", "true");
  try {
    await ({ overview, products, incidents, support, staff, activity })[view]();
    if (session && generation === sessionGeneration) $("last-updated").textContent = "Última consulta: " + date(new Date());
  } catch (error) {
    if (session && generation === sessionGeneration) notice(errorMessage(error), true);
  } finally {
    $("view-" + view).removeAttribute("aria-busy");
    $("refresh").disabled = false;
  }
}

async function submitForm(form, operation) {
  const button = form.querySelector('[type="submit"]');
  const errorArea = form.querySelector(".inline-error");
  button.disabled = true;
  errorArea.hidden = true;
  try { await operation(new FormData(form)); }
  catch (error) { errorArea.textContent = errorMessage(error); errorArea.hidden = false; }
  finally { button.disabled = false; }
}

$("login-form").addEventListener("submit", login);
$("demo-account").addEventListener("change", (event) => {
  const [email, password] = accounts[event.target.value];
  $("email").value = email;
  $("password").value = password;
  $("otp").value = "";
});
$("logout").addEventListener("click", async () => {
  const activeSession = session;
  $("logout").disabled = true;
  let message = "";
  try {
    if (activeSession) {
      const result = await send("/auth/logout", { method: "POST", body: JSON.stringify({ refresh_token: activeSession.refresh_token }) }, activeSession.access_token);
      if (!result.response.ok) message = "Se cerró la vista local. La API no pudo confirmar la revocación de la sesión.";
    }
  } catch { message = "Se cerró la vista local. No hubo conexión para confirmar la revocación de la sesión."; }
  finally { clearSession(message); $("logout").disabled = false; }
});
$("refresh").addEventListener("click", () => { notice(""); go(currentView); });
for (const button of document.querySelectorAll("[data-view]")) button.addEventListener("click", () => { notice(""); go(button.dataset.view); });
for (const button of document.querySelectorAll("[data-close]")) button.addEventListener("click", () => {
  const dialog = $(button.dataset.close);
  if (!dialog.querySelector('[type="submit"]:disabled')) dialog.close();
});
for (const dialog of document.querySelectorAll("dialog")) dialog.addEventListener("cancel", (event) => {
  if (dialog.querySelector('[type="submit"]:disabled')) event.preventDefault();
});
function bindFilter(button, input, view) {
  $(button).addEventListener("click", () => { pages[view] = 1; notice(""); go(view); });
  $(input).addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); pages[view] = 1; notice(""); go(view); }
  });
}
bindFilter("product-filter", "product-search", "products");
bindFilter("ticket-filter", "ticket-search", "support");
$("product-status").addEventListener("change", () => { pages.products = 1; go("products"); });
$("ticket-status").addEventListener("change", () => { pages.support = 1; go("support"); });
$("incident-status").addEventListener("change", () => { pages.incidents = 1; go("incidents"); });
$("new-product").addEventListener("click", () => {
  $("product-form").reset();
  $("product-form").querySelector(".inline-error").hidden = true;
  $("product-dialog").showModal();
});
$("new-incident").addEventListener("click", () => {
  const form = $("incident-form");
  form.reset();
  form.querySelector(".inline-error").hidden = true;
  const now = new Date(Date.now() - 5 * 60000);
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  form.elements.started_at.value = local;
  $("incident-dialog").showModal();
});
$("product-form").addEventListener("submit", (event) => {
  event.preventDefault();
  submitForm(event.currentTarget, async (values) => {
    const body = Object.fromEntries(values);
    if (!body.external_url.startsWith("https://")) throw new Error("El enlace externo debe comenzar con https://.");
    if (body.image_url && !body.image_url.startsWith("https://")) throw new Error("El enlace de imagen debe comenzar con https://.");
    body.price_clp = body.price_clp === "" ? null : Number(body.price_clp);
    body.image_url = body.image_url.trim() || null;
    const product = await request("/marketplace/products", { method: "POST", body: JSON.stringify(body) });
    $("product-dialog").close();
    $("product-search").value = "";
    $("product-status").value = "";
    pages.products = 1;
    await go("products");
    notice("Producto guardado. Su ID se generó automáticamente; puedes verlo en «Ver registro».");
    showRecord("Producto creado", product, "marketplace_products");
  });
});
$("incident-form").addEventListener("submit", (event) => {
  event.preventDefault();
  submitForm(event.currentTarget, async (values) => {
    const body = Object.fromEntries(values);
    body.is_maintenance = values.get("is_maintenance") === "on";
    body.started_at = new Date(body.started_at).toISOString();
    const incident = await request("/ops/incidents", { method: "POST", body: JSON.stringify(body) });
    $("incident-dialog").close();
    $("incident-status").value = "";
    pages.incidents = 1;
    await go("incidents");
    notice("Incidente guardado. Puedes comprobarlo en Neon con la consulta de «Ver registro».");
    showRecord("Incidente creado", incident, "ops_incidents");
  });
});
$("resolution-form").addEventListener("submit", (event) => {
  event.preventDefault();
  submitForm(event.currentTarget, async (values) => {
    await request("/ops/incidents/" + incidentToResolve.id, { method: "PATCH", body: JSON.stringify({ status: "resolved", resolution: values.get("resolution").trim() }) });
    $("resolution-dialog").close();
    incidentToResolve = null;
    await go("incidents");
    notice("Incidente resuelto. La nota de resolución quedó guardada.");
  });
});
$("copy-sql").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("record-sql").textContent);
    $("copy-sql").textContent = "Consulta copiada";
    setTimeout(() => { $("copy-sql").textContent = "Copiar consulta"; }, 2000);
  } catch {
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents($("record-sql"));
    selection.removeAllRanges();
    selection.addRange(range);
    $("copy-sql").textContent = "Seleccionada: usa Ctrl+C";
  }
});
$("swagger-link").href = API_BASE + "/docs";
fetch(API_ORIGIN + "/health", { credentials: "omit", signal: AbortSignal.timeout(45000) })
  .then((response) => {
    $("connection").textContent = response.ok ? "API disponible" : "API no disponible";
    $("connection").className = "badge " + (response.ok ? "connected" : "disconnected");
  }).catch(() => {
    $("connection").textContent = "Sin conexión";
    $("connection").className = "badge disconnected";
  });
