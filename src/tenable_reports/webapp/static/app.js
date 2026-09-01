const { filterClients, selectionForVisibleClients, resolveResponsibleAnalystValue } = window.TenableClientSelection;
const state = { data: null, selectedClient: null, runClientIds: [], runScope: "single", filter: "", analystFilter: "all", runSelection: [], runSelectionQuery: "", runSelectionAnalystFilter: "all", runSelectionFilterSnapshot: null, responsibleAnalystDraft: undefined, connectionChecks: {}, editingClientId: null, currentReports: [], backfillPlan: null, availableTags: [], tagSearch: "", selectedBatchId: null };
const CLOUD_PROGRESS_EVENT = "TENABLE_CLOUD_PROGRESS";
const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value = "") => String(value).replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));
const slugify = (value = "") => value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^[-._]+|[-._]+$/g, "").replace(/[-_.]{2,}/g, "-").slice(0, 80) || "cliente";

async function api(path, options = {}) {
  const init = { ...options, headers: { ...(options.headers || {}) } };
  if (options.body && typeof options.body !== "string") {
    init.body = JSON.stringify(options.body);
    init.headers["Content-Type"] = "application/json";
  }
  if (options.method && options.method !== "GET") init.headers["X-Tenable-UI"] = "1";
  const response = await fetch(path, init);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Falha HTTP ${response.status}`);
  return payload;
}

function formatDate(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" }).format(new Date(value));
}
function formatBytes(value) {
  if (!value) return "0 KB";
  const units = ["B", "KB", "MB", "GB"]; let size = value; let unit = 0;
  while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit++; }
  return `${size.toFixed(unit ? 1 : 0)} ${units[unit]}`;
}
function statusFor(client) {
  const job = client.job;
  if (!client.enabled) return { key: "disabled", label: "Desabilitado" };
  if (job?.status === "RUNNING") return { key: "running", label: "Gerando" };
  if (job?.status === "QUEUED") return { key: "queued", label: `Fila ${job.queue_position || ""}` };
  if (job?.status === "WAITING_WAS_DECISION") return { key: "failed", label: "Decisão WEB" };
  if (job?.status === "FAILED") return { key: "failed", label: "Falha recente" };
  if (!client.credentials_ready || client.profile_error) return { key: "failed", label: "Configurar" };
  return { key: "ready", label: "Pronto" };
}

function tagProgressCopy(job) {
  if (!job?.tag_progress) return null;
  const tag = job.tag_progress;
  return `TAG ${tag.current}/${tag.total} · ${tag.label || tag.tag_uuid}`;
}

function sourceProgressCopy(progress, label) {
  if (!progress) return null;
  const current = Number(progress.completed_chunks || 0);
  const total = Number(progress.total_chunks || 0);
  const chunks = total > 0 ? current + "/" + total + " chunks" : current + " chunks";
  const details = [
    chunks,
    progress.export_uuid ? "UUID " + progress.export_uuid : "",
    progress.origin ? "origem " + progress.origin : "",
    progress.date_field ? "filtro " + progress.date_field : "",
  ].filter(Boolean).join(" · ");
  if (progress.stalled) {
    const minutes = Math.max(1, Math.floor(Number(progress.idle_seconds || 0) / 60));
    const noProgressLimit = Number(progress.no_progress_timeout_seconds || 0);
    const limit = noProgressLimit > 0
      ? " · limite " + Math.max(1, Math.floor(noProgressLimit / 60)) + " min"
      : "";
    return label + " sem novos chunks há " + minutes + " min" + limit + " · " + details;
  }
  return label + " · " + details;
}

function exportProgressCopy(job) {
  return sourceProgressCopy(job?.export_progress, "Export VM");
}

function wasExportProgressCopy(job) {
  return sourceProgressCopy(job?.was_export_progress, "Export WEB");
}

function cloudProgressCopy(job) {
  const progress = job?.cloud_progress;
  if (!progress) return null;
  const stages = {
    CONTRACT_PROBE: "validando contrato",
    COLLECTION: "coletando fontes",
    DATASET: "montando dados",
    RENDERING: "gerando documentos",
    PUBLICATION: "publicando",
    RECENT_COLLECTION_GUARD: "coleta recente protegida",
  };
  const stage = stages[progress.stage] || String(progress.stage || "processando").toLowerCase();
  const count = Number(progress.total || 0) > 0
    ? ` · ${Number(progress.current || 0)}/${Number(progress.total)}`
    : "";
  const source = progress.source ? ` · ${progress.source}` : "";
  return `Cloud Security · ${stage}${source}${count}`;
}

function collectionOutcomeCopy(job) {
  if (job?.force_live_collection) {
    const detail = job.reconstruction_status === "HISTORICAL_RECONSTRUCTION" ? " · HISTÓRICO RECONSTRUÍDO" : "";
    return `NOVA COLETA PELA API${detail}`;
  }

  if (job?.reconstruction_status === "HISTORICAL_RECONSTRUCTION") {
    return "HISTÓRICO RECONSTRUÍDO · Inventory Findings";
  }
  if (job?.collection_route === "snapshot_replay") {
    return "SNAPSHOT HISTÓRICO REUTILIZADO";
  }
  return null;
}

function documentKind(document) {
  if (document.document_kind) return document.document_kind;
  return /inteligência|customiza/i.test(document.name || "") ? "custom" : "base";
}

function renderDocumentGroups(documents) {
  const groups = [
    ["base", "Geral"],
    ["custom", "Customizado"],
    ["tag", "Por TAG"],
    ["cloud", "Cloud Security"],
  ];
  return groups.map(([kind, label]) => {
    const items = documents.filter(document => documentKind(document) === kind);
    if (!items.length) return "";
    const links = items.map(document => {
      const title = kind === "tag"
        ? `${document.tag_category || "TAG"}: ${document.tag_value || document.name}`
        : document.name || "documento";
      return `<div class="report-document"><span>${escapeHtml(title)}</span><div><a class="download" target="_blank" rel="noopener" href="/api/reports/${document.document_id}/download?inline=true">Abrir</a><a class="download" href="/api/reports/${document.document_id}/download">Baixar</a></div></div>`;
    }).join("");
    return `<section class="document-group"><strong>${label}</strong>${links}</section>`;
  }).join("");
}

const ACTIVE_BATCH_STATES = new Set(["QUEUED", "RUNNING", "PAUSE_REQUESTED", "PAUSED", "STOP_REQUESTED"]);
const TERMINAL_BATCH_STATES = new Set(["STOPPED", "COMPLETE", "COMPLETE_WITH_FAILURES", "COMPLETE_WITH_WARNINGS"]);
const BATCH_ACTION_ROUTES = {
  pause: "/pause",
  resume: "/resume",
  stop: "/stop",
  "retry-incomplete": "/retry-incomplete",
  "rerun-all": "/rerun-all",
};

function actionKey(prefix, batchId) {
  const random = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}:${batchId}:${random}`;
}

function batchStatusLabel(status) {
  return ({
    QUEUED: "Na fila",
    RUNNING: "Em execução",
    PAUSE_REQUESTED: "Pausa solicitada",
    PAUSED: "Pausado",
    STOP_REQUESTED: "Parada solicitada",
    STOPPED: "Parado",
    COMPLETE: "Concluído",
    COMPLETE_WITH_FAILURES: "Concluído com falhas",
    COMPLETE_WITH_WARNINGS: "Concluído com avisos",
  })[status] || status;
}

async function runBatchAction(button, batch, action) {
  if (!batch || button.disabled) return;
  const body = {
    idempotency_key: actionKey(action, batch.id),
    actor: "interface-local",
    reason: "Ação solicitada pela interface local.",
  };
  if (action === "stop") {
    const message = `O cliente atual será interrompido com checkpoint. O export remoto será preservado para uma tentativa futura.\n\nLote: ${batch.id}\n\nDeseja parar este lote?`;
    if (!window.confirm(message)) return;
    body.confirmation = `PARAR ${batch.id.slice(0, 8)}`;
  } else if (action === "retry-incomplete") {
    if (!window.confirm(`Somente ${batch.retryable_count} cliente(s) com falha, interrupção ou cancelamento entrarão em um novo lote. Deseja continuar?`)) return;
  } else if (action === "rerun-all") {
    if (!window.confirm(`Todos os ${batch.total_count} cliente(s) serão gerados novamente em um novo lote. Deseja continuar?`)) return;
    body.confirmation = `GERAR NOVAMENTE ${batch.id.slice(0, 8)}`;
  }
  button.disabled = true;
  try {
    await api(`/api/batches/${encodeURIComponent(batch.id)}${BATCH_ACTION_ROUTES[action]}`, {
      method: "POST",
      body,
    });
    await refresh();
    toast(action === "pause" ? "Pausa solicitada após o cliente atual." : action === "resume" ? "Lote retomado." : action === "stop" ? "Parada cooperativa solicitada." : "Novo lote adicionado à fila.");
  } catch (error) {
    toast(error.message, "error");
    button.disabled = false;
  }
}

function renderBatches() {
  const batches = state.data?.batches || [];
  const batch = batches[0];
  const panel = $("#batch-panel");
  panel.classList.toggle("hidden", !batch);
  if (!batch) return;
  const finished = Number(batch.completed_count || 0) + Number(batch.failed_count || 0) + Number(batch.interrupted_count || 0) + Number(batch.cancelled_count || 0);
  const recoveredPaused = batch.kind === "RECOVERED" && batch.status === "PAUSED";
  $("#batch-title").textContent = batch.kind === "GENERATE_ALL" ? "Geração da carteira" : batch.kind === "RECOVERED" ? "Lote recuperado" : batch.kind === "RETRY_INCOMPLETE" ? "Retentativa de incompletos" : batch.kind === "RERUN_ALL" ? "Nova geração integral" : "Geração individual";
  $("#batch-state").textContent = batchStatusLabel(batch.status);
  $("#batch-state").dataset.status = batch.status;
  $("#batch-progress-copy").textContent = `${finished} de ${batch.total_count} finalizados`;
  $("#batch-progress-percent").textContent = `${batch.progress_percent}%`;
  $("#batch-progress-bar").value = Number(batch.progress_percent || 0);
  $("#batch-current-copy").textContent = batch.current_client_id ? `Cliente atual: ${batch.current_client_id}` : recoveredPaused ? `${Number(batch.retryable_count || 0)} falha(s) disponível(is) para retentativa controlada.` : batch.status === "PAUSED" ? "O lote aguarda retomada manual." : "Nenhum cliente em execução neste instante.";
  $("#batch-counters").innerHTML = [
    ["Concluídos", batch.completed_count],
    ["Falhas", batch.failed_count],
    ["Interrompidos", batch.interrupted_count],
    ["Pendentes", Number(batch.queued_count || 0) + Number(batch.cancelled_count || 0)],
  ].map(([label, value]) => `<div><strong>${Number(value || 0)}</strong><span>${label}</span></div>`).join("");

  const actions = [];
  if (["QUEUED", "RUNNING"].includes(batch.status)) actions.push(["pause", "Pausar após o atual", "ghost"]);
  if (!recoveredPaused && ["QUEUED", "RUNNING", "PAUSE_REQUESTED", "PAUSED"].includes(batch.status)) actions.push(["stop", "Parar lote", "danger"]);
  if (!recoveredPaused && batch.status === "PAUSED") actions.push(["resume", "Retomar lote", "primary"]);
  if (recoveredPaused && Number(batch.retryable_count || 0) > 0) actions.push(["retry-incomplete", "Tentar falhas/interrompidos", "primary"]);
  if (TERMINAL_BATCH_STATES.has(batch.status) && Number(batch.retryable_count || 0) > 0) actions.push(["retry-incomplete", "Tentar somente falhas e interrompidos", "ghost"]);
  if (TERMINAL_BATCH_STATES.has(batch.status) && batch.kind === "GENERATE_ALL") actions.push(["rerun-all", "Gerar novamente para todos", "ghost"]);
  $("#batch-actions").innerHTML = actions.map(([action, label, tone]) => `<button class="button ${tone}" data-batch-action="${action}" type="button">${label}</button>`).join("");
  document.querySelectorAll("[data-batch-action]").forEach(button => button.addEventListener("click", () => runBatchAction(button, batch, button.dataset.batchAction)));
}

function analystOptions(records) {
  return records.map(analyst => `<option value="${escapeHtml(analyst.analyst_id)}">${escapeHtml(analyst.display_name)}</option>`).join("");
}

function populateAnalystControls() {
  const analysts = state.data?.analysts || [];
  const active = analysts.filter(analyst => analyst.active);
  const filterOptions = `<option value="all">Todos os responsáveis</option><option value="unassigned">Sem responsável</option>${analystOptions(active)}`;
  const dashboardFilter = $("#dashboard-analyst-filter");
  dashboardFilter.innerHTML = filterOptions;
  dashboardFilter.value = active.some(item => item.analyst_id === state.analystFilter) || ["all", "unassigned"].includes(state.analystFilter) ? state.analystFilter : "all";
  state.analystFilter = dashboardFilter.value;
  const runFilter = $("#run-selection-analyst-filter");
  runFilter.innerHTML = filterOptions;
  runFilter.value = active.some(item => item.analyst_id === state.runSelectionAnalystFilter) || ["all", "unassigned"].includes(state.runSelectionAnalystFilter) ? state.runSelectionAnalystFilter : "all";
  state.runSelectionAnalystFilter = runFilter.value;

  const responsibleSelect = $("#client-form [name='responsible_analyst_id']");
  const editingClient = state.data?.clients.find(client => client.client_id === state.editingClientId);
  const currentId = resolveResponsibleAnalystValue(
    state.responsibleAnalystDraft,
    editingClient?.responsible_analyst_id,
  );
  const assignable = analysts.filter(analyst => analyst.active || analyst.analyst_id === currentId);
  responsibleSelect.innerHTML = `<option value="">Sem responsável</option>${analystOptions(assignable)}`;
  responsibleSelect.value = currentId;
}

function renderAnalystManager() {
  const analysts = state.data?.analysts || [];
  $("#analyst-list").innerHTML = analysts.length ? analysts.map(analyst => `<div class="analyst-row" data-analyst-id="${escapeHtml(analyst.analyst_id)}"><div><strong>${escapeHtml(analyst.display_name)}</strong><small>${analyst.active ? "Ativo" : "Inativo"}</small></div><div class="analyst-actions"><button class="mini-button" type="button" data-analyst-action="rename">Renomear</button><button class="mini-button" type="button" data-analyst-action="toggle">${analyst.active ? "Desativar" : "Ativar"}</button><button class="mini-button danger" type="button" data-analyst-action="delete">Excluir</button></div></div>`).join("") : '<div class="loading">Nenhum analista cadastrado.</div>';
}

function eligibleRunClients() {
  return (state.data?.clients || []).filter(client => client.enabled && client.credentials_ready);
}

function visibleRunSelectionClients() {
  return filterClients(eligibleRunClients(), {
    query: state.runSelectionQuery,
    analystId: state.runSelectionAnalystFilter,
  });
}

function renderRunSelection() {
  const visible = visibleRunSelectionClients();
  const selected = new Set(state.runSelection);
  $("#run-selection-list").innerHTML = visible.length ? visible.map(client => `<label class="selection-row"><input type="checkbox" data-run-selection-id="${escapeHtml(client.client_id)}" ${selected.has(client.client_id) ? "checked" : ""}><span><strong>${escapeHtml(client.display_name)}</strong><small>${escapeHtml(client.client_id)} · ${escapeHtml(client.responsible_analyst_name || "Sem responsável")}</small></span></label>`).join("") : '<div class="loading">Nenhum cliente corresponde aos filtros.</div>';
  document.querySelectorAll("[data-run-selection-id]").forEach(input => input.addEventListener("change", () => {
    state.runSelection = selectionForVisibleClients(state.runSelection, [input.dataset.runSelectionId], input.checked);
    renderRunSelection();
  }));
  const count = state.runSelection.length;
  $("#run-selection-count").textContent = `${count} cliente(s) selecionado(s)`;
  const confirmButton = $("#confirm-run-selection");
  confirmButton.disabled = count === 0;
  confirmButton.textContent = `Gerar ${count} cliente(s)`;
}

function render() {
  if (!state.data) return;
  const clients = state.data.clients || [];
  renderBatches();
  populateAnalystControls();
  renderAnalystManager();
  const activeJobs = (state.data.jobs || []).filter(j => ["QUEUED", "RUNNING"].includes(j.status));
  $("#metric-clients").textContent = clients.filter(c => c.enabled).length;
  $("#metric-running").textContent = activeJobs.length;
  const jobWarningCount = (state.data.jobs || []).reduce((total, job) => total + (job.warnings || []).length, 0);
  const stalledExportCount = (state.data.jobs || []).filter(
    job => job.export_progress?.stalled || job.was_export_progress?.stalled
  ).length;
  const wasRecoveryCount = (state.data.was_recoveries || []).length;
  $("#metric-alerts").textContent = (state.data.alerts || []).length + jobWarningCount + stalledExportCount + wasRecoveryCount + (state.data.database_error ? 1 : 0);
  $("#connection-label").textContent = state.data.database_error ? "banco indisponível" : "PostgreSQL online";
  $(".connection").classList.toggle("online", !state.data.database_error);
  const firstJobWarning = (state.data.jobs || []).find(job => job.warnings?.length)?.warnings?.[0];
  const stalledExport = (state.data.jobs || []).find(
    job => job.was_export_progress?.stalled || job.export_progress?.stalled
  );
  const stalledExportAlert = stalledExport
    ? stalledExport.client_id + ": " + (
        wasExportProgressCopy(stalledExport) || exportProgressCopy(stalledExport)
      )
    : null;
  const wasRecoveryAlert = state.data.was_recoveries?.[0]
    ? `${state.data.was_recoveries[0].client_id}: decisão necessária sobre a coleta WEB.`
    : null;
  const alert = state.data.database_error || state.data.alerts?.[0]?.message || firstJobWarning?.message || stalledExportAlert || wasRecoveryAlert;
  $("#global-alert").classList.toggle("hidden", !alert);
  $("#global-alert-text").textContent = alert || "";
  $("#run-all-button").disabled = !clients.some(c => c.enabled && c.credentials_ready);
  $("#check-all-button").disabled = !clients.some(c => c.enabled);
  $("#archive-all-button").disabled = Boolean(state.data.database_error);
  const storage = state.data.storage || {};
  $("#storage-free").textContent = formatBytes(storage.available_bytes);
  $("#storage-temporary").textContent = formatBytes(storage.temporary_bytes);
  $("#storage-pending").textContent = String(storage.pending_cleanup_runs || 0);
  $("#storage-reserved").textContent = formatBytes(storage.queue_reserved_bytes);

  const filtered = filterClients(clients, { query: state.filter, analystId: state.analystFilter });
  $("#empty-state").classList.toggle("hidden", clients.length > 0);
  $("#client-grid").innerHTML = filtered.map(client => {
    const status = statusFor(client); const job = client.job;
    const progress = job?.progress ?? (client.latest_report ? 100 : 0);
    const report = client.latest_report;
    const connectionCheck = state.connectionChecks[client.client_id];
    const warning = client.alert || job?.error || job?.warnings?.length || job?.status === "WAITING_WAS_DECISION" || client.was_recoveries?.length || job?.export_progress?.stalled || job?.was_export_progress?.stalled || !client.credentials_ready || client.profile_error || connectionCheck?.ok === false || connectionCheck?.cloud?.ok === false || (client.cloud_enabled && !client.cloud_token_saved);
    const runningCopy = job?.vm_selective_mode === "validation"
      ? "Validando export completo x otimizado"
      : cloudProgressCopy(job) || tagProgressCopy(job) || wasExportProgressCopy(job) || exportProgressCopy(job) || "Coletando e gerando documentos";
    const displayRunningCopy = job?.force_live_collection ? `API ao vivo · ${runningCopy}` : runningCopy;
    const queuedCopy = job?.force_live_collection ? "Aguardando execução · API ao vivo" : "Aguardando execução";
    const componentProgress = [
      exportProgressCopy(job),
      wasExportProgressCopy(job),
      cloudProgressCopy(job),
    ].filter(Boolean);
    const validation = job?.vm_export_validation;
    const completedCopy = validation
      ? `Validação do export: ${validation.outcome === "PASSED" ? "aprovada" : "revisar"}`
      : collectionOutcomeCopy(job) || (report ? `${report.document_count} documento(s)` : "Aguardando primeira execução");
    return `<article class="client-card" data-client="${escapeHtml(client.client_id)}" tabindex="0">
      <div class="card-top"><span class="status-pill ${status.key}"><i></i>${escapeHtml(status.label)}</span>${warning ? '<span class="warning-badge" title="Há um alerta">!</span>' : ""}</div>
      <h3>${escapeHtml(client.display_name)}</h3><span class="analyst-chip">${escapeHtml(client.responsible_analyst_name || "Sem responsável")}</span><p class="client-meta">${escapeHtml(client.client_id)}<br>${escapeHtml(client.tenant_id || "tenant não informado")}</p>
      <div class="card-report"><span>Último relatório</span><strong>${report ? escapeHtml(report.period_id || formatDate(report.ended_at)) : "Ainda não gerado"}</strong></div>
      <div class="progress-wrap"><div class="progress-copy"><span>${job?.status === "RUNNING" ? escapeHtml(displayRunningCopy) : job?.status === "QUEUED" ? queuedCopy : job?.status === "FAILED" ? "Execução interrompida" : job?.status === "WAITING_WAS_DECISION" ? "VM concluído · escolha como tratar o WEB" : completedCopy}</span><span>${progress}%</span></div><div class="progress-track"><progress class="progress-bar" max="100" value="${progress}" aria-label="Progresso: ${progress}%"></progress></div>${componentProgress.length ? `<div class="component-progress">${componentProgress.map(item => `<span>${escapeHtml(item)}</span>`).join("")}</div>` : ""}</div>
    </article>`;
  }).join("");
  document.querySelectorAll(".client-card").forEach(card => {
    const open = () => openClient(card.dataset.client);
    card.addEventListener("click", open); card.addEventListener("keydown", e => { if (["Enter", " "].includes(e.key)) open(); });
  });
  renderManageList(); renderAlerts();
}

async function refresh(silent = true) {
  try { state.data = await api("/api/state"); render(); }
  catch (error) { if (!silent) toast(error.message, "error"); $("#connection-label").textContent = "servidor indisponível"; }
}

function startBrowserDownload(url) {
  const link = document.createElement("a");
  link.href = url;
  link.download = "";
  link.hidden = true;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

async function prepareArchive(payload) {
  const prepared = await api("/api/report-archives/prepare", {
    method: "POST",
    body: payload,
  });
  if (!/^\/api\/report-archives\/download\/[a-f0-9]{32}$/.test(prepared.download_url || "")) {
    throw new Error("O servidor devolveu um endereço de download inválido.");
  }
  startBrowserDownload(prepared.download_url);
  return prepared;
}

function monthlyPeriodLabel(periodId) {
  const [year, month] = periodId.split("-").map(Number);
  if (!year || !month) return periodId;
  return new Date(Date.UTC(year, month - 1, 1)).toLocaleDateString("pt-BR", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
}

async function openMonthlyArchiveDialog(button) {
  button.disabled = true;
  try {
    const payload = await api("/api/report-archives/months");
    const periods = payload.periods || [];
    if (!periods.length) {
      toast("Nenhum período mensal com relatório MAIN está disponível.", "error");
      return;
    }
    $("#archive-month-select").innerHTML = periods.map(period =>
      `<option value="${escapeHtml(period)}">${escapeHtml(monthlyPeriodLabel(period))}</option>`
    ).join("");
    $("#archive-dialog").showModal();
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function openClient(clientId) {
  const client = state.data.clients.find(c => c.client_id === clientId); if (!client) return;
  state.selectedClient = clientId;
  $("#detail-title").textContent = client.display_name; $("#detail-subtitle").textContent = `${client.client_id} · ${client.tenant_id || "sem tenant"}`;
  const status = statusFor(client); const warning = client.job?.error || client.job?.warnings?.[0]?.message || client.alert?.message || client.profile_error || (!client.credentials_ready ? "As credenciais Tenable ainda não foram preenchidas." : client.cloud_enabled && !client.cloud_token_saved ? "Cloud Security está ativo, mas o token Cloud ainda não foi salvo. Os relatórios VM continuam disponíveis." : "");
  $("#detail-status").innerHTML = `<span class="status-pill ${status.key}"><i></i>${escapeHtml(status.label)}</span>${warning ? `<p>${escapeHtml(warning)}</p>` : "<p>Cliente configurado e pronto para uma nova execução.</p>"}`;
  $("#detail-run-button").disabled = !client.enabled || !client.credentials_ready || ["QUEUED","RUNNING"].includes(client.job?.status);
  $("#detail-check-button").disabled = !client.credentials_ready;
  $("#report-list").innerHTML = '<div class="loading">Carregando relatórios…</div>'; $("#client-dialog").showModal();
  try {
    const payload = await api(`/api/clients/${encodeURIComponent(clientId)}/reports?include_deleted=true`); const reports = payload.reports || [];
    state.currentReports = reports;
    $("#reports-count").textContent = `${reports.length} execução(ões)`;
    $("#report-list").innerHTML = reports.length ? reports.map(report => {
      const documents = renderDocumentGroups(report.documents || []);
      const omitted = (report.omitted_modules || []).length ? ` · omitidos: ${escapeHtml(report.omitted_modules.join(", "))}` : "";
      const reference = report.reference_run_id ? ` · referência: ${escapeHtml(report.reference_run_id)}` : "";
      const cloudBadge = report.cloud_status && report.cloud_status !== "NOT_REQUESTED"
        ? `<span class="report-badge">CLOUD ${escapeHtml(report.cloud_status)}</span>`
        : "";
      const cloudRetryAction = report.cloud_retry_available
        ? '<button class="mini-button" data-report-action="retry-cloud" type="button">Tentar Cloud novamente</button>'
        : "";
      const reportArchiveAction = !report.deleted_at
        ? '<button class="mini-button archive-download" data-report-action="archive" type="button">Baixar conjunto ZIP</button>'
        : "";
      return `<div class="report-row ${report.deleted_at ? "deleted" : ""}" data-report-run="${escapeHtml(report.run_id)}"><div><div class="report-badges">${report.is_main ? '<span class="report-badge main">MAIN</span>' : ""}<span class="report-badge">${escapeHtml(report.origin || "MANUAL")}</span><span class="report-badge">${escapeHtml(report.status || "")}</span>${cloudBadge}${report.deleted_at ? '<span class="report-badge">EXCLUÍDO</span>' : ""}</div><strong>${escapeHtml(report.period_id || report.run_id)}</strong><small>${escapeHtml(report.run_id)} · ${formatBytes(report.size_bytes)}${reference}${omitted}</small><div class="report-documents">${documents || '<small>Documentos não localizados no disco.</small>'}</div></div><div class="report-actions">${reportArchiveAction}${cloudRetryAction}${!report.deleted_at && !report.is_main ? '<button class="mini-button" data-report-action="main" type="button">Definir MAIN</button>' : ""}${report.deleted_at ? '<button class="mini-button" data-report-action="restore" type="button">Restaurar</button>' : '<button class="mini-button danger" data-report-action="delete" type="button">Excluir conjunto</button>'}</div></div>`;
    }).join("") : '<div class="loading">Nenhum relatório gerado para este cliente.</div>';
    bindReportActions();
  } catch (error) { $("#report-list").innerHTML = `<div class="loading">${escapeHtml(error.message)}</div>`; }
}

function bindReportActions() {
  document.querySelectorAll("[data-report-action]").forEach(button => button.addEventListener("click", async () => {
    const runId = button.closest("[data-report-run]").dataset.reportRun;
    const report = state.currentReports.find(item => item.run_id === runId);
    if (!report) return;
    try {
      let successMessage = "Registro de relatório atualizado.";
      if (button.dataset.reportAction === "archive") {
        button.disabled = true;
        const prepared = await prepareArchive({ run_id: runId });
        toast(`Download preparado: ${prepared.download_name}`);
        return;
      } else if (button.dataset.reportAction === "retry-cloud") {
        const message = `Somente o componente Cloud Security será executado novamente. A coleta VM e os demais documentos não serão repetidos.\n\nExecução: ${runId}\n\nDeseja continuar?`;
        if (!window.confirm(message)) return;
        await api(`/api/reports/${encodeURIComponent(runId)}/retry-cloud`, {
          method: "POST",
          body: { confirmation: `RETENTAR CLOUD ${runId}` },
        });
        successMessage = "Retentativa Cloud adicionada à fila.";
      } else if (button.dataset.reportAction === "main") {
        const reason = prompt("Motivo para definir esta geração como MAIN:"); if (!reason?.trim()) return;
        await api(`/api/reports/${encodeURIComponent(runId)}/main`, { method: "POST", body: { actor: "analista-web", reason } });
      } else if (button.dataset.reportAction === "restore") {
        const reason = prompt("Motivo da restauração:"); if (!reason?.trim()) return;
        await api(`/api/reports/${encodeURIComponent(runId)}/restore`, { method: "POST", body: { actor: "analista-web", reason } });
      } else {
        const preview = await api(`/api/reports/${encodeURIComponent(runId)}/purge-preview`);
        const replacementIds = preview.compatible_replacement_run_ids || [];
        const requiresMainGapConfirmation = Boolean(
          preview.requires_main_gap_confirmation ?? (preview.is_main && !replacementIds.length)
        );
        const mainGapWarning = requiresMainGapConfirmation
          ? `ATENÇÃO: este é o único relatório MAIN do período ${preview.period_id}. A exclusão deixará esse período sem referência para comparações futuras.\n\n`
          : "";
        const summary = `${mainGapWarning}Esta exclusão é permanente e não poderá ser desfeita.\n\nPeríodo: ${preview.period_id}\nDocumentos: ${preview.document_count}\nArquivos no disco: ${preview.file_count}\nEspaço: ${formatBytes(preview.total_bytes)}\n\nDeseja continuar?`;
        if (!window.confirm(summary)) return;
        const reason = prompt("Motivo da exclusão permanente:"); if (!reason?.trim()) return;
        const confirmation = prompt('Digite EXCLUIR para remover o conjunto do banco e do disco:');
        if (confirmation !== "EXCLUIR") {
          toast('Confirmação cancelada. Digite exatamente "EXCLUIR".', "error");
          return;
        }
        const body = { actor: "analista-web", reason, confirmation };
        if (preview.is_main) {
          if (replacementIds.length) {
            const replacementLines = replacementIds.map(id => {
              const candidate = state.currentReports.find(item => item.run_id === id);
              return `${id}${candidate?.period_id ? ` · ${candidate.period_id}` : ""}`;
            });
            const selected = prompt(`Esta geração é MAIN. Informe o ID da substituta:\n${replacementLines.join("\n")}`);
            if (!selected || !replacementIds.includes(selected.trim())) {
              toast("Selecione exatamente um dos IDs de geração apresentados.", "error");
              return;
            }
            body.replacement_run_id = selected.trim();
          } else if (requiresMainGapConfirmation) {
            body.allow_main_gap = true;
          }
        }
        const deleted = await api(`/api/reports/${encodeURIComponent(runId)}`, { method: "DELETE", body });
        successMessage = `Conjunto excluído: ${deleted.deleted_files} arquivo(s), ${formatBytes(deleted.deleted_bytes)}.`;
      }
      toast(successMessage);
      await openClient(state.selectedClient);
      await refresh();
    } catch (error) {
      toast(error.message, "error");
    } finally {
      if (button.isConnected) button.disabled = false;
    }
  }));
}

function renderManageList() {
  if (!state.data) return;
  $("#manage-client-list").innerHTML = state.data.clients.length ? state.data.clients.map(c => {
    const check = state.connectionChecks[c.client_id];
    const checks = check ? [
      `VM ${check.ok ? "OK" : "falhou"}`,
      check.cloud ? `Cloud ${check.cloud.ok ? "OK" : "falhou"}` : "",
    ].filter(Boolean).join(" · ") : "";
    const result = check ? `<span class="api-result ${check.ok && (!check.cloud || check.cloud.ok) ? "ok" : "failed"}">${escapeHtml(checks)}</span>` : "";
    return `<div class="manage-item ${state.editingClientId === c.client_id ? "selected" : ""}" data-edit-client="${escapeHtml(c.client_id)}" role="button" tabindex="0" title="Clique para editar"><div><strong>${escapeHtml(c.display_name)}</strong><small>${escapeHtml(c.client_id)} · ${c.credentials_ready ? "credenciais prontas" : "sem credenciais"}</small></div><div class="manage-actions">${result}<button class="mini-button" type="button" data-check-client="${escapeHtml(c.client_id)}">Testar API</button><label class="switch" title="Ativar ou desativar"><input type="checkbox" data-toggle-client="${escapeHtml(c.client_id)}" ${c.enabled ? "checked" : ""}><span></span></label></div></div>`;
  }).join("") : '<div class="loading">Nenhum cliente cadastrado.</div>';
  document.querySelectorAll("[data-edit-client]").forEach(item => {
    const edit = () => editClient(item.dataset.editClient);
    item.addEventListener("click", edit);
    item.addEventListener("keydown", event => { if (["Enter", " "].includes(event.key)) { event.preventDefault(); edit(); } });
  });
  document.querySelectorAll(".manage-actions").forEach(actions => actions.addEventListener("click", event => event.stopPropagation()));
  document.querySelectorAll("[data-toggle-client]").forEach(input => input.addEventListener("change", async event => {
    event.stopPropagation();
    try { await api(`/api/clients/${encodeURIComponent(input.dataset.toggleClient)}`, { method: "PATCH", body: { enabled: input.checked } }); await refresh(); }
    catch (error) { input.checked = !input.checked; toast(error.message, "error"); }
  }));
  document.querySelectorAll("[data-check-client]").forEach(button => button.addEventListener("click", event => { event.stopPropagation(); testConnections([button.dataset.checkClient], button); }));
}

async function testConnections(clientIds, button) {
  const original = button?.textContent; if (button) { button.disabled = true; button.textContent = "Testando…"; }
  try {
    const payload = await api("/api/connections/check", { method: "POST", body: { client_ids: clientIds } });
    for (const result of payload.results || []) state.connectionChecks[result.client_id] = result;
    render();
    const vmOk = payload.results.filter(item => item.ok).length;
    const vmFailed = payload.results.length - vmOk;
    const cloudResults = payload.results.map(item => item.cloud).filter(Boolean);
    const cloudOk = cloudResults.filter(item => item.ok).length;
    const cloudFailed = cloudResults.length - cloudOk;
    const cloudCopy = cloudResults.length ? ` · Cloud ${cloudOk} OK${cloudFailed ? ` / ${cloudFailed} falha(s)` : ""}` : "";
    toast(`VM ${vmOk} OK${vmFailed ? ` / ${vmFailed} falha(s)` : ""}${cloudCopy}`, vmFailed || cloudFailed ? "error" : "success");
    if (payload.results.length === 1 && vmFailed) toast(payload.results[0].message, "error");
    if (payload.results.length === 1 && cloudFailed) toast(payload.results[0].cloud.message, "error");
    if (payload.results.length > 1 && !$("#manage-dialog").open) $("#manage-dialog").showModal();
  } catch (error) { toast(error.message, "error"); }
  finally { if (button?.isConnected) { button.disabled = false; button.textContent = original; } }
}

function renderAlerts() {
  if (!state.data) return; const alerts = [...(state.data.alerts || [])];
  const failedJobs = (state.data.jobs || []).filter(j => j.status === "FAILED").map(j => ({client_id:j.client_id, message:j.error, at:j.ended_at, run_id:j.run_id, job_id:j.job_id, export:j.export_progress}));
  const waitingWasJobs = (state.data.jobs || []).filter(j => j.status === "WAITING_WAS_DECISION").map(j => ({
    client_id: j.client_id,
    message: j.was_recovery?.failure?.message || "A coleta VM foi preservada, mas a coleta WEB precisa de uma decisão.",
    at: j.ended_at,
    run_id: j.was_recovery?.run_id || j.run_id,
    was_recovery: j.was_recovery,
  }));
  const waitingRunIds = new Set(waitingWasJobs.map(item => item.run_id));
  const durableWasRecoveries = (state.data.was_recoveries || [])
    .filter(item => !waitingRunIds.has(item.run_id))
    .map(item => ({
      client_id: item.client_id,
      message: item.failure?.message || "A coleta WEB precisa de uma decisão.",
      at: item.updated_at,
      run_id: item.run_id,
      was_recovery: { ...item, failure: item.failure || {} },
    }));
  const componentWarnings = (state.data.jobs || []).flatMap(job => (job.warnings || []).map(warning => {
    const cloud = String(warning.code || "").startsWith("CLOUD_");
    return {
      client_id: job.client_id,
      message: cloud
        ? `Cloud Security: ${warning.message || "Falha no componente Cloud."}`
        : `${warning.tag_label || warning.tag_uuid || "TAG"}: ${warning.message || "Falha no relatório por TAG."}`,
      at: job.ended_at,
      run_id: job.run_id,
      cloud_retry: cloud && Boolean(warning.retryable) && Boolean(job.run_id),
    };
  }));
  if (state.data.database_error) alerts.unshift({client_id:"Sistema", message:state.data.database_error, at:state.data.server_time});
  const items = [...waitingWasJobs, ...durableWasRecoveries, ...failedJobs, ...componentWarnings, ...alerts];
  $("#alerts-list").innerHTML = items.length ? items.map(a => {
    const stuck = a.export?.status === "TIMED_OUT" && !a.export?.auto_cancelled;
    const segmentCopy = a.export?.segment ? ` · segmento ${a.export.segment === "fixed" ? "corrigidas / last_fixed" : "ativas e reabertas / last_found"}` : "";
    const idleCopy = a.export?.idle_seconds ? ` · sem progresso há ${Math.max(1, Math.floor(Number(a.export.idle_seconds) / 60))} min` : "";
    const limitCopy = a.export?.no_progress_timeout_seconds ? ` · limite ${Math.max(1, Math.floor(Number(a.export.no_progress_timeout_seconds) / 60))} min` : "";
    const routeCopy = a.export?.date_field ? ` · filtro ${escapeHtml(a.export.date_field)}` : "";
    const exportCopy = a.export?.export_uuid ? `<p><small>Export VM: ${escapeHtml(a.export.export_uuid)} · ${Number(a.export.completed_chunks || 0)}/${Number(a.export.total_chunks || 0)} chunks · origem ${escapeHtml(a.export.origin || "desconhecida")}${segmentCopy}${routeCopy}${idleCopy}${limitCopy}</small></p>` : "";
    const wasFailure = a.was_recovery?.failure || {};
    const wasCopy = a.was_recovery
      ? `<p><small>Export WEB: ${escapeHtml(wasFailure.export_uuid || "UUID não informado")} · ${Number(wasFailure.completed_chunks || 0)}/${Number(wasFailure.total_chunks || 0)} chunks · origem ${escapeHtml(wasFailure.origin || "desconhecida")}</small></p>`
      : "";
    const automaticWasRetry = a.was_recovery?.status === "RETRY_AVAILABLE";
    const action = a.was_recovery
      ? automaticWasRetry
        ? `<p><button class="mini-button" data-was-retry-run="${escapeHtml(a.run_id)}" type="button">Tentar WEB novamente</button></p>`
        : `<p><button class="mini-button" data-was-continue-run="${escapeHtml(a.run_id)}" type="button">Continuar sem WEB</button> <button class="mini-button" data-was-retry-run="${escapeHtml(a.run_id)}" type="button">Tentar WEB novamente</button></p>`
      : a.cloud_retry
      ? `<p><button class="mini-button" data-retry-cloud-run="${escapeHtml(a.run_id)}" type="button">Tentar Cloud novamente</button></p>`
      : !a.job_id ? "" : stuck
        ? `<p><button class="mini-button danger" data-cancel-export-job="${escapeHtml(a.job_id)}" data-export-uuid="${escapeHtml(a.export.export_uuid)}" type="button">Cancelar export e tentar novamente</button></p>`
        : `<p><button class="mini-button" data-retry-job="${escapeHtml(a.job_id)}" type="button">Tentar novamente</button></p>`;
    return `<div class="alert-row"><span class="alert-sign">!</span><div><strong>${escapeHtml(a.client_id || "Sistema")}</strong><p>${escapeHtml(a.message || "Falha sem detalhes.")}</p>${exportCopy}${wasCopy}<small>${formatDate(a.at)}${a.run_id ? ` · ${escapeHtml(a.run_id)}` : ""}</small>${action}</div></div>`;
  }).join("") : '<div class="loading">Nenhum alerta registrado.</div>';
  document.querySelectorAll("[data-retry-job]").forEach(button => button.addEventListener("click", async () => {
    try { await api(`/api/jobs/${encodeURIComponent(button.dataset.retryJob)}/retry`, { method: "POST", body: {} }); await refresh(); toast("Nova tentativa adicionada à fila."); }
    catch (error) { toast(error.message, "error"); }
  }));
  document.querySelectorAll("[data-retry-cloud-run]").forEach(button => button.addEventListener("click", async () => {
    const runId = button.dataset.retryCloudRun;
    const message = `Somente o componente Cloud Security será executado novamente.\n\nExecução: ${runId}\n\nDeseja continuar?`;
    if (!window.confirm(message)) return;
    button.disabled = true;
    try {
      await api(`/api/reports/${encodeURIComponent(runId)}/retry-cloud`, {
        method: "POST", body: { confirmation: `RETENTAR CLOUD ${runId}` },
      });
      await refresh(); toast("Retentativa Cloud adicionada à fila.");
    } catch (error) { toast(error.message, "error"); button.disabled = false; }
  }));
  document.querySelectorAll("[data-was-continue-run]").forEach(button => button.addEventListener("click", async () => {
    const runId = button.dataset.wasContinueRun;
    if (!window.confirm(`Os dados VM, assets, TAG e Cloud já coletados serão preservados. O relatório será concluído sem a seção WEB.\n\nExecução: ${runId}\n\nDeseja continuar?`)) return;
    button.disabled = true;
    try {
      await api(`/api/was-recoveries/${encodeURIComponent(runId)}/continue`, {
        method: "POST", body: { confirmation: `CONTINUAR SEM WAS ${runId}` },
      });
      await refresh(); toast("Conclusão sem WEB adicionada à fila.");
    } catch (error) { toast(error.message, "error"); button.disabled = false; }
  }));
  document.querySelectorAll("[data-was-retry-run]").forEach(button => button.addEventListener("click", async () => {
    const runId = button.dataset.wasRetryRun;
    if (!window.confirm(`Somente a coleta WEB será tentada novamente. VM, assets, TAG e Cloud não serão repetidos.\n\nExecução: ${runId}\n\nDeseja continuar?`)) return;
    button.disabled = true;
    try {
      await api(`/api/was-recoveries/${encodeURIComponent(runId)}/retry`, {
        method: "POST", body: { confirmation: `RETENTAR WAS ${runId}` },
      });
      await refresh(); toast("Retentativa WEB adicionada à fila.");
    } catch (error) { toast(error.message, "error"); button.disabled = false; }
  }));
  document.querySelectorAll("[data-cancel-export-job]").forEach(button => button.addEventListener("click", async () => {
    const exportUuid = button.dataset.exportUuid;
    const message = `O export VM será cancelado na Tenable e uma nova tentativa entrará na fila.\n\nUUID: ${exportUuid}\n\nDeseja continuar?`;
    if (!window.confirm(message)) return;
    button.disabled = true;
    try {
      await api(`/api/jobs/${encodeURIComponent(button.dataset.cancelExportJob)}/cancel-export-and-retry`, {
        method: "POST", body: { export_uuid: exportUuid, confirmation: `CANCELAR ${exportUuid}` },
      });
      await refresh(); toast("Export cancelado e nova tentativa adicionada à fila.");
    } catch (error) { toast(error.message, "error"); button.disabled = false; }
  }));
}

function renderBackfillPlan(plan) {
  state.backfillPlan = plan;
  const promotions = plan.promotions || [];
  const alerts = plan.alerts || [];
  const invalid = plan.invalid || [];
  const selected = plan.already_selected_run_ids || [];
  const reasonLabels = {
    DOCUMENTS_NOT_VALID: "Documentos não validados",
    PUBLICATION_NOT_READY: "Publicação ainda não concluída",
    REPORT_DELETED: "Relatório excluído",
    REFERENCE_METADATA_INVALID: "Dados do período incompletos",
  };
  const readableReasons = reasons => reasons.map(reason => reasonLabels[reason] || reason).join(" · ");
  const section = (title, tone, items, emptyText, renderItem) => `<section class="backfill-section ${tone}"><div class="backfill-section-head"><h3>${title}</h3><span>${items.length}</span></div>${items.length ? `<div class="backfill-list">${items.map(renderItem).join("")}</div>` : `<p class="backfill-empty">${emptyText}</p>`}</section>`;
  $("#backfill-results").innerHTML = `<div class="backfill-summary"><div><strong>${promotions.length}</strong><span>promoções seguras</span></div><div><strong>${alerts.length}</strong><span>decisões manuais</span></div><div><strong>${invalid.length}</strong><span>ignorados</span></div><div><strong>${selected.length}</strong><span>já definidos</span></div></div>
    ${section("Promoções seguras", "safe", promotions, "Nenhuma promoção segura pendente.", item => `<div class="backfill-row"><div><strong>${escapeHtml(item.run_id)}</strong><small>${escapeHtml(item.period_key || item.reference_key)}</small></div><span>pronto</span></div>`)}
    ${section("Seleção manual necessária", "attention", alerts, "Nenhuma decisão manual pendente.", item => `<div class="backfill-row"><div><strong>${escapeHtml(item.run_ids.join(" · "))}</strong><small>${escapeHtml(item.message)}</small></div><span>revisar</span></div>`)}
    ${section("Registros ignorados", "muted", invalid, "Nenhum registro inválido ou excluído.", item => `<div class="backfill-row"><div><strong>${escapeHtml(item.run_id)}</strong><small>${escapeHtml(readableReasons(item.reasons))}</small></div><span>ignorado</span></div>`)}`;
  $("#apply-backfill-button").disabled = promotions.length === 0;
  $("#backfill-action-note").textContent = promotions.length ? `${promotions.length} promoção(ões) segura(s) pronta(s) para aplicação.` : "Nenhuma alteração é necessária agora.";
}

async function analyzeBackfill() {
  const button = $("#analyze-backfill-button");
  button.disabled = true; button.textContent = "Analisando…";
  $("#backfill-results").innerHTML = '<div class="loading">Verificando registros e referências históricas…</div>';
  try {
    renderBackfillPlan(await api("/api/admin/backfill"));
  } catch (error) {
    state.backfillPlan = null;
    $("#apply-backfill-button").disabled = true;
    $("#backfill-results").innerHTML = `<div class="backfill-error"><strong>Não foi possível analisar.</strong><p>${escapeHtml(error.message)}</p></div>`;
    $("#backfill-action-note").textContent = "O banco precisa estar disponível para esta operação.";
  } finally {
    button.disabled = false; button.textContent = "Analisar novamente";
  }
}

function openRunSelection() {
  const active = (state.data?.batches || []).find(batch => ACTIVE_BATCH_STATES.has(batch.status));
  if (active) {
    toast(`O lote ${active.id.slice(0, 8)} ainda está ${batchStatusLabel(active.status).toLowerCase()}. Use os controles do lote antes de iniciar outro.`, "error");
    return;
  }
  state.runSelection = eligibleRunClients().map(client => client.client_id);
  state.runSelectionQuery = "";
  state.runSelectionAnalystFilter = "all";
  state.runSelectionFilterSnapshot = null;
  $("#run-selection-search").value = "";
  populateAnalystControls();
  $("#run-selection-analyst-filter").value = "all";
  renderRunSelection();
  $("#run-selection-dialog").showModal();
}

function openRun(clientIds, runScope = "single") {
  state.runClientIds = clientIds;
  state.runScope = runScope;
  if (runScope === "single") state.runSelectionFilterSnapshot = null;
  else if (!state.runSelectionFilterSnapshot) state.runSelectionFilterSnapshot = { analyst_id: null, query: "", unassigned: false };
  const all = runScope === "all"; $("#run-title").textContent = all ? "Gerar todos os clientes" : "Gerar relatório";
  $("#run-subtitle").textContent = all ? `${clientIds.length} clientes serão adicionados à fila.` : state.data.clients.find(c => c.client_id === clientIds[0])?.display_name || "";
  $("#run-dialog").showModal();
}

function toast(message, type = "success") {
  const node = document.createElement("div"); node.className = `toast ${type}`; node.textContent = message; $("#toast-stack").append(node); setTimeout(() => node.remove(), 4500);
}

document.querySelectorAll(".close-dialog").forEach(button => button.addEventListener("click", () => button.closest("dialog").close()));
$("#manage-button").addEventListener("click", () => $("#manage-dialog").showModal());
$("#analyst-form").addEventListener("submit", async event => {
  event.preventDefault();
  const input = event.currentTarget.elements.display_name;
  const button = event.currentTarget.querySelector('button[type="submit"]');
  button.disabled = true;
  try {
    await api("/api/analysts", { method: "POST", body: { display_name: input.value.trim() } });
    event.currentTarget.reset();
    await refresh();
    toast("Analista adicionado.");
  } catch (error) { toast(error.message, "error"); }
  finally { button.disabled = false; }
});
$("#analyst-list").addEventListener("click", async event => {
  const button = event.target.closest("[data-analyst-action]");
  if (!button) return;
  const row = button.closest("[data-analyst-id]");
  const analyst = (state.data?.analysts || []).find(item => item.analyst_id === row?.dataset.analystId);
  if (!analyst) return;
  const action = button.dataset.analystAction;
  let path = `/api/analysts/${encodeURIComponent(analyst.analyst_id)}`;
  let method = "PATCH";
  let body;
  if (action === "rename") {
    const displayName = window.prompt("Novo nome do analista:", analyst.display_name);
    if (displayName === null) return;
    body = { display_name: displayName.trim() };
  } else if (action === "toggle") {
    body = { active: !analyst.active };
  } else if (action === "delete") {
    if (!window.confirm(`Excluir o analista ${analyst.display_name}?`)) return;
    method = "DELETE";
    body = { confirmation: "EXCLUIR" };
  } else return;
  button.disabled = true;
  try {
    await api(path, { method, body });
    await refresh();
    toast(action === "delete" ? "Analista excluído." : "Analista atualizado.");
  } catch (error) { toast(error.message, "error"); }
  finally { button.disabled = false; }
});
$("#admin-button").addEventListener("click", () => { $("#admin-dialog").showModal(); analyzeBackfill(); });
$("#analyze-backfill-button").addEventListener("click", analyzeBackfill);
$("#apply-backfill-button").addEventListener("click", async event => {
  const count = state.backfillPlan?.promotions?.length || 0;
  if (!count) return;
  const confirmation = prompt(`Esta ação definirá ${count} relatório(s) como MAIN.\nDigite APLICAR BACKFILL para confirmar:`);
  if (confirmation === null) return;
  event.currentTarget.disabled = true;
  try {
    const result = await api("/api/admin/backfill/apply", { method: "POST", body: { confirmation } });
    toast(`${result.applied_promotions.length} relatório(s) definido(s) como MAIN.`);
    await analyzeBackfill();
    await refresh();
  } catch (error) { toast(error.message, "error"); event.currentTarget.disabled = false; }
});
$("#check-all-button").addEventListener("click", event => testConnections(state.data.clients.filter(c => c.enabled).map(c => c.client_id), event.currentTarget));
$("#archive-all-button").addEventListener("click", event => openMonthlyArchiveDialog(event.currentTarget));
$("#archive-form").addEventListener("submit", async event => {
  event.preventDefault();
  const periodId = $("#archive-month-select").value;
  if (!periodId) return;
  const button = $("#archive-download-button");
  button.disabled = true;
  try {
    const prepared = await prepareArchive({ period_id: periodId });
    $("#archive-dialog").close();
    toast(`Download mensal preparado: ${prepared.download_name}`);
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
});
$("#empty-add-button").addEventListener("click", () => { resetClientForm(); $("#manage-dialog").showModal(); });
$("#open-alerts-button").addEventListener("click", () => $("#alerts-dialog").showModal());
$("#cleanup-button").addEventListener("click", async event => {
  event.currentTarget.disabled = true;
  try {
    const preview = await api("/api/storage/cleanup/preview", { method: "POST", body: {} });
    if (!preview.candidate_count) { toast("Nenhum dado temporário elegível para limpeza."); return; }
    const message = `${preview.candidate_count} conjunto(s) temporário(s), totalizando ${formatBytes(preview.candidate_bytes)}, serão removidos. Os DOCX e o histórico compacto serão mantidos. Continuar?`;
    if (!confirm(message)) return;
    const result = await api("/api/storage/cleanup/apply", { method: "POST", body: {} });
    await refresh(); toast(`${result.removed.length} resíduo(s) removido(s) · ${formatBytes(result.removed_bytes)} liberados.`);
  } catch (error) { toast(error.message, "error"); }
  finally { event.currentTarget.disabled = false; }
});
$("#run-all-button").addEventListener("click", () => {
  const batches = state.data?.batches || [];
  const active = batches.find(batch => ACTIVE_BATCH_STATES.has(batch.status));
  if (active) {
    toast(`O lote ${active.id.slice(0, 8)} ainda está ${batchStatusLabel(active.status).toLowerCase()}. Use os controles do lote antes de iniciar outro.`, "error");
    return;
  }
  const comparable = batches.find(batch => batch.kind === "GENERATE_ALL" && TERMINAL_BATCH_STATES.has(batch.status));
  if (comparable && Number(comparable.retryable_count || 0) > 0) {
    state.selectedBatchId = comparable.id;
    $("#batch-choice-copy").textContent = `O lote ${comparable.id.slice(0, 8)} tem ${comparable.retryable_count} cliente(s) incompleto(s).`;
    $("#retry-incomplete-copy").textContent = `Reexecuta somente ${comparable.retryable_count} cliente(s); concluídos e avisos ficam de fora.`;
    $("#batch-choice-dialog").showModal();
    return;
  }
  openRunSelection();
});
$("#retry-incomplete-button").addEventListener("click", async event => {
  const batch = (state.data?.batches || []).find(item => item.id === state.selectedBatchId);
  if (!batch) return;
  await runBatchAction(event.currentTarget, batch, "retry-incomplete");
  $("#batch-choice-dialog").close();
});
$("#rerun-all-button").addEventListener("click", () => {
  $("#batch-choice-dialog").close();
  openRunSelection();
});
$("#detail-run-button").addEventListener("click", () => { $("#client-dialog").close(); openRun([state.selectedClient]); });
$("#detail-check-button").addEventListener("click", event => testConnections([state.selectedClient], event.currentTarget));
$("#detail-edit-button").addEventListener("click", () => { const clientId = state.selectedClient; $("#client-dialog").close(); $("#manage-dialog").showModal(); editClient(clientId); });
$("#search-input").addEventListener("input", event => { state.filter = event.target.value.trim().toLowerCase(); render(); });
$("#dashboard-analyst-filter").addEventListener("change", event => { state.analystFilter = event.target.value; render(); });
$("#run-selection-search").addEventListener("input", event => { state.runSelectionQuery = event.target.value; renderRunSelection(); });
$("#run-selection-analyst-filter").addEventListener("change", event => { state.runSelectionAnalystFilter = event.target.value; renderRunSelection(); });
$("#select-visible-clients").addEventListener("click", () => {
  state.runSelection = selectionForVisibleClients(state.runSelection, visibleRunSelectionClients().map(client => client.client_id), true);
  renderRunSelection();
});
$("#clear-visible-clients").addEventListener("click", () => {
  state.runSelection = selectionForVisibleClients(state.runSelection, visibleRunSelectionClients().map(client => client.client_id), false);
  renderRunSelection();
});
$("#confirm-run-selection").addEventListener("click", () => {
  const selected = [...state.runSelection];
  const analystFilter = state.runSelectionAnalystFilter;
  state.runSelectionFilterSnapshot = {
    analyst_id: ["all", "unassigned"].includes(analystFilter) ? null : analystFilter,
    query: state.runSelectionQuery.trim().slice(0, 200),
    unassigned: analystFilter === "unassigned",
  };
  $("#run-selection-dialog").close();
  openRun(selected, "all");
});

document.querySelectorAll('input[name="period_type"]').forEach(input => input.addEventListener("change", () => {
  const type = document.querySelector('input[name="period_type"]:checked').value;
  $("#days-fields").classList.toggle("hidden", type !== "days"); $("#range-fields").classList.toggle("hidden", type !== "range");
}));

const nameField = $("#client-form [name='display_name']");
const idField = $("#client-form [name='client_id']");
const tenantField = $("#client-form [name='tenant_id']");
const clientForm = $("#client-form");

function renderTagSelector() {
  const root = $("#tag-selector");
  const query = state.tagSearch.toLowerCase();
  const visible = state.availableTags.filter(tag =>
    `${tag.category_name} ${tag.value} ${tag.tag_uuid}`.toLowerCase().includes(query)
  );
  if (!visible.length) {
    root.innerHTML = `<div class="tag-empty">${state.availableTags.length ? "Nenhuma TAG corresponde à busca." : state.editingClientId ? "Clique em “Buscar TAGs da Tenable”." : "Salve o cliente antes de buscar as TAGs."}</div>`;
    return;
  }
  const grouped = visible.reduce((result, tag) => {
    (result[tag.category_name] ||= []).push(tag); return result;
  }, {});
  root.innerHTML = Object.entries(grouped).map(([category, tags]) => `<section class="tag-category"><div class="tag-category-title"><strong>${escapeHtml(category)}</strong><span>${tags.length}</span></div>${tags.map(tag => `<div class="tag-row ${tag.available === false ? "unavailable" : ""}" data-tag-row="${escapeHtml(tag.tag_uuid)}"><div><strong>${escapeHtml(tag.value)}</strong><small>${tag.available === false ? "Não retornada pela API nesta consulta" : escapeHtml(tag.tag_uuid)}</small></div><label><input type="checkbox" data-tag-generate ${tag.generate_report ? "checked" : ""}><span>Gerar relatório</span></label><label><input type="checkbox" data-tag-compare ${tag.include_temporal_comparison ? "checked" : ""} ${tag.generate_report ? "" : "disabled"}><span>Comparativo temporal</span></label></div>`).join("")}</section>`).join("");
  root.querySelectorAll("[data-tag-row]").forEach(row => {
    const tag = state.availableTags.find(item => item.tag_uuid === row.dataset.tagRow);
    const generate = row.querySelector("[data-tag-generate]");
    const compare = row.querySelector("[data-tag-compare]");
    generate.addEventListener("change", () => {
      tag.generate_report = generate.checked;
      if (!generate.checked) { tag.include_temporal_comparison = false; compare.checked = false; }
      compare.disabled = !generate.checked;
    });
    compare.addEventListener("change", () => { tag.include_temporal_comparison = compare.checked; });
  });
}

async function fetchClientTags() {
  if (!state.editingClientId) return;
  const button = $("#fetch-tags-button");
  button.disabled = true; button.textContent = "Buscando…";
  try {
    const payload = await api(`/api/clients/${encodeURIComponent(state.editingClientId)}/tags`);
    state.availableTags = payload.tags || [];
    clientForm.elements.tag_reports_enabled.checked = Boolean(payload.tag_reports_enabled);
    renderTagSelector();
    toast(`${state.availableTags.filter(tag => tag.available).length} TAG(s) disponíveis.`);
  } catch (error) { toast(error.message, "error"); }
  finally { button.disabled = false; button.textContent = "Buscar TAGs da Tenable"; }
}

function syncCloudConfig() {
  const enabled = clientForm.elements.cloud_enabled.checked;
  const section = $("#cloud-config");
  section.classList.toggle("disabled", !enabled);
  const client = state.data?.clients.find(item => item.client_id === state.editingClientId);
  $("#test-cloud-button").disabled = !enabled || !state.editingClientId || !client?.cloud_token_saved || !client?.enabled;
  $("#cloud-config-note").textContent = !enabled
    ? "Ative Cloud Security acima para gerar o relatório junto com os documentos VM."
    : client?.cloud_token_saved
      ? "Token Cloud salvo. Deixe o campo vazio para mantê-lo; o conteúdo nunca retorna à tela."
      : "Informe o token Cloud e salve o cliente antes de testar a API.";
}

function resetClientForm() {
  state.editingClientId = null;
  state.responsibleAnalystDraft = "";
  clientForm.reset();
  clientForm.elements.responsible_analyst_id.value = "";
  clientForm.classList.remove("editing");
  idField.readOnly = false;
  idField.dataset.manual = "false";
  tenantField.dataset.manual = "false";
  clientForm.elements.access_key.placeholder = "Opcional agora";
  clientForm.elements.secret_key.placeholder = "Opcional agora";
  clientForm.elements.intelligence_enabled.checked = true;
  clientForm.elements.was_enabled.checked = true;
  clientForm.elements.cloud_enabled.checked = false;
  clientForm.elements.cloud_api_secret.value = "";
  clientForm.elements.cloud_api_secret.placeholder = "Preencha para salvar";
  clientForm.elements.cloud_environment.value = "global";
  clientForm.elements.tag_reports_enabled.checked = false;
  clientForm.elements.vm_export_strategy.value = "combined";
  clientForm.elements.vm_num_assets_per_chunk.value = "1000";
  clientForm.elements.vm_selective_properties.value = "disabled";
  clientForm.elements.historical_source.value = "legacy";
  $("#validate-vm-export-button").disabled = true;
  state.availableTags = [];
  state.tagSearch = "";
  $("#tag-search-input").value = "";
  $("#fetch-tags-button").disabled = true;
  renderTagSelector();
  syncCloudConfig();
  $("#client-form-mode").textContent = "NOVO";
  $("#client-form-title").textContent = "Adicionar cliente";
  $("#client-form-note").textContent = "As chaves ficam apenas no arquivo local ignorado pelo Git e nunca retornam à tela.";
  $("#save-client-button").textContent = "Salvar cliente";
  $("#cancel-edit-button").classList.add("hidden");
  renderManageList();
}

function editClient(clientId) {
  const client = state.data?.clients.find(item => item.client_id === clientId);
  if (!client) return;
  state.editingClientId = clientId;
  state.responsibleAnalystDraft = client.responsible_analyst_id || "";
  populateAnalystControls();
  clientForm.classList.add("editing");
  clientForm.elements.display_name.value = client.display_name || "";
  idField.value = client.client_id;
  idField.readOnly = true;
  idField.dataset.manual = "true";
  tenantField.value = client.tenant_id || client.client_id;
  tenantField.dataset.manual = "true";
  clientForm.elements.responsible_analyst_id.value = client.responsible_analyst_id || "";
  clientForm.elements.access_key.value = "";
  clientForm.elements.secret_key.value = "";
  clientForm.elements.access_key.placeholder = "Preencha somente para trocar";
  clientForm.elements.secret_key.placeholder = "Preencha somente para trocar";
  clientForm.elements.tag_reports_enabled.checked = Boolean(client.tag_reports_enabled);
  state.availableTags = (client.tag_reports || []).map(tag => ({ ...tag, available: false }));
  state.tagSearch = "";
  $("#tag-search-input").value = "";
  $("#fetch-tags-button").disabled = !client.credentials_ready;
  renderTagSelector();
  clientForm.elements.intelligence_enabled.checked = Boolean(client.intelligence_enabled);
  clientForm.elements.was_enabled.checked = Boolean(client.was_enabled);
  clientForm.elements.cloud_enabled.checked = Boolean(client.cloud_enabled);
  clientForm.elements.cloud_api_secret.value = "";
  clientForm.elements.cloud_api_secret.placeholder = client.cloud_token_saved ? "Token Cloud salvo · preencha somente para trocar" : "Token Cloud ainda não salvo";
  clientForm.elements.cloud_environment.value = client.cloud_environment || "global";
  clientForm.elements.include_output.checked = Boolean(client.include_output);
  clientForm.elements.show_source_filters.checked = Boolean(client.show_source_filters);
  clientForm.elements.vm_export_strategy.value = client.vm_export_strategy || "combined";
  clientForm.elements.vm_num_assets_per_chunk.value = String(client.vm_num_assets_per_chunk || 1000);
  clientForm.elements.vm_selective_properties.value = client.vm_selective_properties || "disabled";
  clientForm.elements.historical_source.value = client.historical_source || "legacy";
  $("#validate-vm-export-button").disabled = !client.credentials_ready || !client.enabled;
  syncCloudConfig();
  $("#client-form-mode").textContent = "EDITANDO";
  $("#client-form-title").textContent = client.display_name;
  $("#client-form-note").textContent = "Deixe as chaves vazias para manter as credenciais atuais. O ID interno não pode ser alterado.";
  $("#save-client-button").textContent = "Salvar alterações";
  $("#cancel-edit-button").classList.remove("hidden");
  renderManageList();
  clientForm.scrollIntoView({ behavior: "smooth", block: "start" });
}
nameField.addEventListener("input", () => {
  const generated = slugify(nameField.value);
  if (idField.dataset.manual !== "true") idField.value = generated;
  if (tenantField.dataset.manual !== "true") tenantField.value = generated;
});
idField.addEventListener("input", event => { event.target.dataset.manual = event.isTrusted ? "true" : "false"; });
tenantField.addEventListener("input", event => { event.target.dataset.manual = event.isTrusted ? "true" : "false"; });
clientForm.elements.responsible_analyst_id.addEventListener("change", event => {
  state.responsibleAnalystDraft = event.target.value;
});

$("#cancel-edit-button").addEventListener("click", resetClientForm);
$("#fetch-tags-button").addEventListener("click", fetchClientTags);
clientForm.elements.cloud_enabled.addEventListener("change", syncCloudConfig);
$("#test-cloud-button").addEventListener("click", async event => {
  if (!state.editingClientId) return;
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "Testando…";
  try {
    const payload = await api(`/api/clients/${encodeURIComponent(state.editingClientId)}/cloud/check`, {
      method: "POST", body: {},
    });
    const current = state.connectionChecks[state.editingClientId] || {};
    state.connectionChecks[state.editingClientId] = { ...current, cloud: payload.result };
    render();
    toast(payload.result.ok ? "API Cloud funcionando." : payload.result.message, payload.result.ok ? "success" : "error");
  } catch (error) { toast(error.message, "error"); }
  finally {
    button.textContent = "Testar API Cloud";
    syncCloudConfig();
  }
});
$("#validate-vm-export-button").addEventListener("click", async event => {
  if (!state.editingClientId) return;
  const message = "A validação inicia duas exportações reais na Tenable: uma completa e uma otimizada. O relatório continuará usando a coleta completa. Deseja continuar?";
  if (!window.confirm(message)) return;
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "Validando…";
  try {
    await api(`/api/clients/${encodeURIComponent(state.editingClientId)}/vm-export/validate`, {
      method: "POST", body: {},
    });
    await refresh();
    toast("Validação A/B adicionada à fila. Acompanhe o progresso no card do cliente.");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    const client = state.data?.clients.find(item => item.client_id === state.editingClientId);
    button.disabled = !client?.credentials_ready || !client?.enabled;
    button.textContent = "Validar export otimizado";
  }
});
$("#tag-search-input").addEventListener("input", event => {
  state.tagSearch = event.target.value.trim(); renderTagSelector();
});

clientForm.addEventListener("submit", async event => {
  event.preventDefault(); const form = new FormData(event.currentTarget); const button = event.currentTarget.querySelector('button[type="submit"]'); button.disabled = true;
  const payload = Object.fromEntries(form.entries()); ["intelligence_enabled","was_enabled","cloud_enabled","include_output","show_source_filters","tag_reports_enabled"].forEach(name => payload[name] = form.has(name));
  payload.responsible_analyst_id = String(payload.responsible_analyst_id || "").trim() || null;
  payload.tag_reports = state.availableTags.filter(tag => tag.generate_report).map(tag => ({
    tag_uuid: tag.tag_uuid,
    category_uuid: tag.category_uuid || "",
    category_name: tag.category_name,
    value: tag.value,
    generate_report: true,
    include_temporal_comparison: Boolean(tag.include_temporal_comparison),
  }));
  const editing = state.editingClientId;
  const path = editing ? `/api/clients/${encodeURIComponent(editing)}` : "/api/clients";
  const method = editing ? "PATCH" : "POST";
  try { await api(path, { method, body: payload }); resetClientForm(); await refresh(); toast(editing ? "Cliente atualizado." : "Cliente adicionado."); }
  catch (error) { toast(error.message, "error"); } finally { button.disabled = false; }
});

$("#run-form").addEventListener("submit", async event => {
  event.preventDefault(); const form = new FormData(event.currentTarget); const type = form.get("period_type"); const payload = { client_ids: state.runClientIds, mode: "manual", run_scope: state.runScope };
  if (state.runScope === "all") payload.selection_filter_snapshot = state.runSelectionFilterSnapshot || { analyst_id: null, query: "", unassigned: false };
  if (type === "days") payload.days = Number(form.get("days"));
  payload.force_live_collection = form.has("force_live_collection");
  if (type === "range") {
    if (!form.get("start_date") || !form.get("end_date")) { toast("Informe a data inicial e a data final.", "error"); return; }
    payload.start_date = String(form.get("start_date"));
    payload.end_date = String(form.get("end_date"));

    const inventoryClients = state.runClientIds
      .map(clientId => state.data?.clients.find(client => client.client_id === clientId))
      .filter(client => client?.historical_source === "inventory_beta");
    if (inventoryClients.length) {
      const names = inventoryClients.map(client => client.display_name).join(", ");
      const message = payload.force_live_collection
        ? `O snapshot será ignorado e o período será reconstruído pela Inventory Findings API para: ${names}. Deseja continuar?`
        : `Se não houver snapshot compacto exato, o período será reconstruído pela Inventory Findings API para: ${names}. O resultado será identificado como histórico reconstruído. Deseja continuar?`;
      if (!window.confirm(message)) return;
      payload.confirm_historical_reconstruction = true;
    }
  }
  if (payload.force_live_collection && !payload.confirm_historical_reconstruction) {
    const message = "Esta execução ignorará o snapshot existente e iniciará novos exports na Tenable. O snapshot atual não será apagado. Deseja continuar?";
    if (!window.confirm(message)) return;
  }

  const button = event.currentTarget.querySelector('button[type="submit"]'); button.disabled = true;
  try { const result = await api("/api/jobs", { method: "POST", body: payload }); $("#run-dialog").close(); await refresh(); toast(`${result.jobs.length} execução(ões) adicionada(s) à fila.`); }
  catch (error) { toast(error.message, "error"); } finally { button.disabled = false; }
});

refresh(false); setInterval(() => refresh(true), 3000);
