const state = {
  token: localStorage.getItem("token"),
  clients: [],
  client: null,
  workers: [],
  events: [],
  selectedEvent: null,
  assignments: [],
};

const $ = (id) => document.getElementById(id);
const money = (value) => Number(value || 0).toLocaleString("es-MX", { style: "currency", currency: "MXN" });
const shiftLabels = { before: "Antes", during: "Durante", after: "Despues" };

function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(el.timer);
  el.timer = setTimeout(() => el.classList.remove("show"), 2600);
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const res = await fetch(path, { ...options, headers });
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || "Error de servidor");
  return data;
}

function showApp(loggedIn) {
  $("login").classList.toggle("hidden", loggedIn);
  $("app").classList.toggle("hidden", !loggedIn);
}

async function login(event) {
  event.preventDefault();
  $("login-error").textContent = "";
  try {
    const data = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email: $("email").value, password: $("password").value }),
    });
    state.token = data.access_token;
    localStorage.setItem("token", state.token);
    showApp(true);
    await bootstrap();
  } catch (err) {
    $("login-error").textContent = err.message;
  }
}

async function bootstrap() {
  state.clients = await api("/api/clients");
  $("client-select").innerHTML = state.clients.map((c) => `<option value="${c.slug}">${c.name}</option>`).join("");
  state.client = state.clients[0];
  if (state.client) $("client-select").value = state.client.slug;
  await loadClientData();
}

async function loadClientData() {
  if (!state.client) return;
  $("client-title").textContent = state.client.name;
  const slug = state.client.slug;
  const [workers, events] = await Promise.all([api(`/api/clients/${slug}/workers`), api(`/api/clients/${slug}/events`)]);
  state.workers = workers;
  state.events = events;
  state.selectedEvent = null;
  state.assignments = [];
  renderWorkers();
  renderEvents();
  await renderSummary();
}

function renderEvents() {
  const grid = $("events-grid");
  if (!state.events.length) {
    grid.innerHTML = `<p class="muted">Sin eventos registrados.</p>`;
    $("assignment-panel").classList.add("hidden");
    return;
  }
  grid.innerHTML = state.events.map((event) => {
    const active = state.selectedEvent?.id === event.id ? " active" : "";
    return `<article class="event-card${active}" data-event="${event.id}">
      <span class="pill">${event.event_type}</span>
      <h3>${event.name}</h3>
      <p class="muted">${event.event_date}</p>
      <p>${money(event.salary_before)} / ${money(event.salary_during)} / ${money(event.salary_after)}</p>
    </article>`;
  }).join("");
  document.querySelectorAll("[data-event]").forEach((el) => el.addEventListener("click", () => selectEvent(Number(el.dataset.event))));
}

async function selectEvent(id) {
  state.selectedEvent = state.events.find((event) => event.id === id);
  state.assignments = await api(`/api/clients/${state.client.slug}/events/${id}/assignments`);
  $("assignment-panel").classList.remove("hidden");
  $("assignment-title").textContent = `Turnos: ${state.selectedEvent.name}`;
  renderEvents();
  renderShifts();
}

function renderShifts() {
  $("shift-grid").innerHTML = Object.entries(shiftLabels).map(([shift, label]) => {
    const assigned = state.assignments.filter((item) => item.shift === shift);
    const assignedIds = new Set(assigned.map((item) => item.worker_id));
    const options = state.workers
      .filter((worker) => !assignedIds.has(worker.id))
      .map((worker) => `<option value="${worker.id}">${worker.full_name} - ${worker.area}</option>`)
      .join("");
    const rows = assigned.map((item) => `<div class="assignment">
      <span>${item.worker.full_name}<br><small class="muted">${item.worker.area}</small></span>
      <strong>${money(item.pay_amount)}</strong>
      <button class="ghost" data-remove-assignment="${item.id}" type="button">Quitar</button>
    </div>`).join("") || `<p class="muted">Sin jornales asignados</p>`;
    return `<article class="shift-card">
      <h3>${label}</h3>
      <div class="add-row">
        <select data-shift-select="${shift}"><option value="">Seleccionar jornal</option>${options}</select>
        <button class="primary" data-add-shift="${shift}" type="button">Agregar</button>
      </div>
      <div>${rows}</div>
    </article>`;
  }).join("");
  document.querySelectorAll("[data-add-shift]").forEach((btn) => btn.addEventListener("click", () => addAssignment(btn.dataset.addShift)));
  document.querySelectorAll("[data-remove-assignment]").forEach((btn) => btn.addEventListener("click", () => removeAssignment(Number(btn.dataset.removeAssignment))));
}

async function saveEvent(event) {
  event.preventDefault();
  await api(`/api/clients/${state.client.slug}/events`, {
    method: "POST",
    body: JSON.stringify({
      name: $("event-name").value,
      event_date: $("event-date").value,
      event_type: $("event-type").value,
      description: $("event-description").value,
      salary_before: $("salary-before").value,
      salary_during: $("salary-during").value,
      salary_after: $("salary-after").value,
      operator_positions: Number($("operator-positions").value || 0),
      supervisor_positions: Number($("supervisor-positions").value || 0),
    }),
  });
  $("event-form").reset();
  $("event-form").classList.add("hidden");
  toast("Evento creado");
  await loadClientData();
}

async function deleteSelectedEvent() {
  if (!state.selectedEvent || !confirm("Eliminar este evento y sus asignaciones?")) return;
  await api(`/api/clients/${state.client.slug}/events/${state.selectedEvent.id}`, { method: "DELETE" });
  toast("Evento eliminado");
  await loadClientData();
}

async function addAssignment(shift) {
  const select = document.querySelector(`[data-shift-select="${shift}"]`);
  if (!select.value) return toast("Selecciona un jornal");
  await api(`/api/clients/${state.client.slug}/events/${state.selectedEvent.id}/assignments`, {
    method: "POST",
    body: JSON.stringify({ worker_id: Number(select.value), shift }),
  });
  await selectEvent(state.selectedEvent.id);
  await renderSummary();
}

async function removeAssignment(id) {
  await api(`/api/clients/${state.client.slug}/assignments/${id}`, { method: "DELETE" });
  await selectEvent(state.selectedEvent.id);
  await renderSummary();
}

function renderWorkers() {
  $("workers-body").innerHTML = state.workers.map((worker) => `<tr>
    <td>${worker.employee_number}</td>
    <td><strong>${worker.full_name}</strong></td>
    <td>${worker.area}</td>
    <td>${worker.phone || ""}<br>${worker.mobile || ""}<br>${worker.social || ""}</td>
    <td>${worker.bank || ""}<br>${worker.account_number || ""}<br>${worker.clabe || ""}</td>
    <td>${worker.ine_filename || "Sin INE"}</td>
    <td>
      <button class="ghost" data-edit-worker="${worker.id}" type="button">Editar</button>
      <button class="danger" data-delete-worker="${worker.id}" type="button">Eliminar</button>
    </td>
  </tr>`).join("") || `<tr><td colspan="7">Sin jornales registrados.</td></tr>`;
  document.querySelectorAll("[data-edit-worker]").forEach((btn) => btn.addEventListener("click", () => editWorker(Number(btn.dataset.editWorker))));
  document.querySelectorAll("[data-delete-worker]").forEach((btn) => btn.addEventListener("click", () => deleteWorker(Number(btn.dataset.deleteWorker))));
}

function editWorker(id) {
  const worker = state.workers.find((item) => item.id === id);
  $("worker-id").value = worker.id;
  $("worker-name").value = worker.full_name;
  $("worker-number").value = worker.employee_number;
  $("worker-area").value = worker.area;
  $("worker-phone").value = worker.phone || "";
  $("worker-mobile").value = worker.mobile || "";
  $("worker-social").value = worker.social || "";
  $("worker-bank").value = worker.bank || "";
  $("worker-account").value = worker.account_number || "";
  $("worker-clabe").value = worker.clabe || "";
  $("worker-ine").value = worker.ine_filename || "";
  $("worker-form").classList.remove("hidden");
}

async function saveWorker(event) {
  event.preventDefault();
  const id = $("worker-id").value;
  const payload = {
    employee_number: $("worker-number").value || null,
    full_name: $("worker-name").value,
    area: $("worker-area").value,
    phone: $("worker-phone").value,
    mobile: $("worker-mobile").value,
    social: $("worker-social").value,
    bank: $("worker-bank").value,
    account_number: $("worker-account").value,
    clabe: $("worker-clabe").value,
    ine_filename: $("worker-ine").value,
  };
  await api(`/api/clients/${state.client.slug}/workers${id ? `/${id}` : ""}`, {
    method: id ? "PUT" : "POST",
    body: JSON.stringify(payload),
  });
  $("worker-form").reset();
  $("worker-id").value = "";
  $("worker-form").classList.add("hidden");
  toast("Jornal guardado");
  await loadClientData();
}

async function deleteWorker(id) {
  if (!confirm("Eliminar este jornal?")) return;
  await api(`/api/clients/${state.client.slug}/workers/${id}`, { method: "DELETE" });
  toast("Jornal eliminado");
  await loadClientData();
}

async function renderSummary() {
  if (!state.client) return;
  const data = await api(`/api/clients/${state.client.slug}/summary`);
  $("summary-cards").innerHTML = [
    ["Eventos", data.events],
    ["Jornales activos", data.active_workers],
    ["Turnos", data.total_shifts],
    ["Total a pagar", money(data.total_pay)],
  ].map(([label, value]) => `<article class="summary-card"><span class="muted">${label}</span><strong>${value}</strong></article>`).join("");
  $("summary-body").innerHTML = data.rows.map((row) => `<tr>
    <td>${row.full_name}</td>
    <td>${row.area}</td>
    <td>${row.before_count}</td>
    <td>${row.during_count}</td>
    <td>${row.after_count}</td>
    <td>${row.shift_count}</td>
    <td><strong>${money(row.total_pay)}</strong></td>
  </tr>`).join("") || `<tr><td colspan="7">Sin asignaciones.</td></tr>`;
}

function bindUi() {
  $("login-form").addEventListener("submit", login);
  $("logout").addEventListener("click", () => {
    localStorage.removeItem("token");
    state.token = null;
    showApp(false);
  });
  $("client-select").addEventListener("change", async (event) => {
    state.client = state.clients.find((client) => client.slug === event.target.value);
    await loadClientData();
  });
  document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((el) => el.classList.remove("active"));
    document.querySelectorAll(".view").forEach((el) => el.classList.remove("active"));
    tab.classList.add("active");
    $(tab.dataset.view).classList.add("active");
    if (tab.dataset.view === "summary") renderSummary();
  }));
  $("new-event").addEventListener("click", () => $("event-form").classList.toggle("hidden"));
  $("new-worker").addEventListener("click", () => {
    $("worker-form").reset();
    $("worker-id").value = "";
    $("worker-form").classList.toggle("hidden");
  });
  document.querySelectorAll("[data-close]").forEach((btn) => btn.addEventListener("click", () => $(btn.dataset.close).classList.add("hidden")));
  $("event-form").addEventListener("submit", saveEvent);
  $("worker-form").addEventListener("submit", saveWorker);
  $("delete-event").addEventListener("click", deleteSelectedEvent);
}

bindUi();
if (state.token) {
  showApp(true);
  bootstrap().catch(() => {
    localStorage.removeItem("token");
    state.token = null;
    showApp(false);
  });
}
