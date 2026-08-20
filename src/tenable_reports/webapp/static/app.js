const state = { data: null, selectedClient: null, runClientIds: [], filter: "", connectionChecks: {}, editingClientId: null, currentReports: [], backfillPlan: null };
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
  if (job?.status === "FAILED") return { key: "failed", label: "Falha recente" };
  if (!client.credentials_ready || client.profile_error) return { key: "failed", label: "Configurar" };
  return { key: "ready", label: "Pronto" };
}

function render() {
  if (!state.data) return;
  const clients = state.data.clients || [];
  const activeJobs = (state.data.jobs || []).filter(j => ["QUEUED", "RUNNING"].includes(j.status));
  $("#metric-clients").textContent = clients.filter(c => c.enabled).length;
  $("#metric-running").textContent = activeJobs.length;
  $("#metric-alerts").textContent = (state.data.alerts || []).length + (state.data.database_error ? 1 : 0);
  $("#connection-label").textContent = state.data.database_error ? "banco indisponível" : "PostgreSQL online";
  $(".connection").classList.toggle("online", !state.data.database_error);
  const alert = state.data.database_error || state.data.alerts?.[0]?.message;
  $("#global-alert").classList.toggle("hidden", !alert);
  $("#global-alert-text").textContent = alert || "";
  $("#run-all-button").disabled = !clients.some(c => c.enabled && c.credentials_ready);
  $("#check-all-button").disabled = !clients.some(c => c.enabled);
  const storage = state.data.storage || {};
  $("#storage-free").textContent = formatBytes(storage.available_bytes);
  $("#storage-temporary").textContent = formatBytes(storage.temporary_bytes);
  $("#storage-pending").textContent = String(storage.pending_cleanup_runs || 0);
  $("#storage-reserved").textContent = formatBytes(storage.queue_reserved_bytes);

  const filtered = clients.filter(c => `${c.display_name} ${c.client_id} ${c.tenant_id}`.toLowerCase().includes(state.filter));
  $("#empty-state").classList.toggle("hidden", clients.length > 0);
  $("#client-grid").innerHTML = filtered.map((client, index) => {
    const status = statusFor(client); const job = client.job;
    const progress = job?.progress ?? (client.latest_report ? 100 : 0);
    const report = client.latest_report;
    const connectionCheck = state.connectionChecks[client.client_id];
    const warning = client.alert || job?.error || !client.credentials_ready || client.profile_error || connectionCheck?.ok === false;
    return `<article class="client-card" data-client="${escapeHtml(client.client_id)}" style="animation-delay:${Math.min(index * 45, 300)}ms" tabindex="0">
      <div class="card-top"><span class="status-pill ${status.key}"><i></i>${escapeHtml(status.label)}</span>${warning ? '<span class="warning-badge" title="Há um alerta">!</span>' : ""}</div>
      <h3>${escapeHtml(client.display_name)}</h3><p class="client-meta">${escapeHtml(client.client_id)}<br>${escapeHtml(client.tenant_id || "tenant não informado")}</p>
      <div class="card-report"><span>Último relatório</span><strong>${report ? escapeHtml(report.period_id || formatDate(report.ended_at)) : "Ainda não gerado"}</strong></div>
      <div class="progress-wrap"><div class="progress-copy"><span>${job?.status === "RUNNING" ? "Coletando e gerando documentos" : job?.status === "QUEUED" ? "Aguardando execução" : job?.status === "FAILED" ? "Execução interrompida" : report ? `${report.document_count} documento(s)` : "Aguardando primeira execução"}</span><span>${progress}%</span></div><div class="progress-track"><div class="progress-bar ${job?.status === "RUNNING" ? "running" : ""}" style="width:${progress}%"></div></div></div>
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

async function openClient(clientId) {
  const client = state.data.clients.find(c => c.client_id === clientId); if (!client) return;
  state.selectedClient = clientId;
  $("#detail-title").textContent = client.display_name; $("#detail-subtitle").textContent = `${client.client_id} · ${client.tenant_id || "sem tenant"}`;
  const status = statusFor(client); const warning = client.job?.error || client.alert?.message || client.profile_error || (!client.credentials_ready ? "As credenciais Tenable ainda não foram preenchidas." : "");
  $("#detail-status").innerHTML = `<span class="status-pill ${status.key}"><i></i>${escapeHtml(status.label)}</span>${warning ? `<p>${escapeHtml(warning)}</p>` : "<p>Cliente configurado e pronto para uma nova execução.</p>"}`;
  $("#detail-run-button").disabled = !client.enabled || !client.credentials_ready || ["QUEUED","RUNNING"].includes(client.job?.status);
  $("#detail-check-button").disabled = !client.credentials_ready;
  $("#report-list").innerHTML = '<div class="loading">Carregando relatórios…</div>'; $("#client-dialog").showModal();
  try {
    const payload = await api(`/api/clients/${encodeURIComponent(clientId)}/reports?include_deleted=true`); const reports = payload.reports || [];
    state.currentReports = reports;
    $("#reports-count").textContent = `${reports.length} execução(ões)`;
    $("#report-list").innerHTML = reports.length ? reports.map(report => {
      const documents = (report.documents || []).map(doc => `<a class="download" target="_blank" rel="noopener" href="/api/reports/${doc.document_id}/download?inline=true">Abrir ${escapeHtml(doc.name || "documento")}</a><a class="download" href="/api/reports/${doc.document_id}/download">Baixar</a>`).join("");
      const omitted = (report.omitted_modules || []).length ? ` · omitidos: ${escapeHtml(report.omitted_modules.join(", "))}` : "";
      const reference = report.reference_run_id ? ` · referência: ${escapeHtml(report.reference_run_id)}` : "";
      return `<div class="report-row ${report.deleted_at ? "deleted" : ""}" data-report-run="${escapeHtml(report.run_id)}"><div><div class="report-badges">${report.is_main ? '<span class="report-badge main">MAIN</span>' : ""}<span class="report-badge">${escapeHtml(report.origin || "MANUAL")}</span><span class="report-badge">${escapeHtml(report.status || "")}</span>${report.deleted_at ? '<span class="report-badge">EXCLUÍDO</span>' : ""}</div><strong>${escapeHtml(report.period_id || report.run_id)}</strong><small>${escapeHtml(report.run_id)} · ${formatBytes(report.size_bytes)}${reference}${omitted}</small><div class="report-documents">${documents || '<small>Documentos não localizados no disco.</small>'}</div></div><div class="report-actions">${!report.deleted_at && !report.is_main ? '<button class="mini-button" data-report-action="main" type="button">Definir MAIN</button>' : ""}${report.deleted_at ? '<button class="mini-button" data-report-action="restore" type="button">Restaurar</button>' : '<button class="mini-button danger" data-report-action="delete" type="button">Excluir</button>'}</div></div>`;
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
      if (button.dataset.reportAction === "main") {
        const reason = prompt("Motivo para definir esta geração como MAIN:"); if (!reason?.trim()) return;
        await api(`/api/reports/${encodeURIComponent(runId)}/main`, { method: "POST", body: { actor: "analista-web", reason } });
      } else if (button.dataset.reportAction === "restore") {
        const reason = prompt("Motivo da restauração:"); if (!reason?.trim()) return;
        await api(`/api/reports/${encodeURIComponent(runId)}/restore`, { method: "POST", body: { actor: "analista-web", reason } });
      } else {
        const reason = prompt("Motivo da exclusão:"); if (!reason?.trim()) return;
        const body = { actor: "analista-web", reason };
        if (report.is_main) {
          const replacements = state.currentReports.filter(item => item.run_id !== runId && !item.deleted_at && item.period_id === report.period_id);
          if (replacements.length) {
            const selected = prompt(`Esta geração é MAIN. Informe a substituta:\n${replacements.map(item => item.run_id).join("\n")}`);
            if (!selected || !replacements.some(item => item.run_id === selected.trim())) return;
            body.replacement_run_id = selected.trim();
          } else {
            if (!confirm("Não há substituta compatível. Deseja deixar uma lacuna histórica?")) return;
            if (!confirm("Confirme novamente: o próximo relatório ficará sem esta referência histórica.")) return;
            body.allow_gap = true;
          }
        } else if (!confirm("Excluir logicamente esta geração?")) return;
        await api(`/api/reports/${encodeURIComponent(runId)}`, { method: "DELETE", body });
      }
      toast("Registro de relatório atualizado.");
      await openClient(state.selectedClient);
      await refresh();
    } catch (error) { toast(error.message, "error"); }
  }));
}

function renderManageList() {
  if (!state.data) return;
  $("#manage-client-list").innerHTML = state.data.clients.length ? state.data.clients.map(c => {
    const check = state.connectionChecks[c.client_id];
    const result = check ? `<span class="api-result ${check.ok ? "ok" : "failed"}">${check.ok ? `OK · ${check.latency_ms} ms` : "Falhou"}</span>` : "";
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
    const ok = payload.results.filter(item => item.ok).length; const failed = payload.results.length - ok;
    toast(`${ok} conexão(ões) OK${failed ? ` · ${failed} falha(s)` : ""}`, failed ? "error" : "success");
    if (payload.results.length === 1 && failed) toast(payload.results[0].message, "error");
    if (payload.results.length > 1 && !$("#manage-dialog").open) $("#manage-dialog").showModal();
  } catch (error) { toast(error.message, "error"); }
  finally { if (button?.isConnected) { button.disabled = false; button.textContent = original; } }
}

function renderAlerts() {
  if (!state.data) return; const alerts = [...(state.data.alerts || [])];
  const failedJobs = (state.data.jobs || []).filter(j => j.status === "FAILED").map(j => ({client_id:j.client_id, message:j.error, at:j.ended_at, run_id:j.run_id, job_id:j.job_id}));
  if (state.data.database_error) alerts.unshift({client_id:"Sistema", message:state.data.database_error, at:state.data.server_time});
  $("#alerts-list").innerHTML = [...failedJobs, ...alerts].length ? [...failedJobs, ...alerts].map(a => `<div class="alert-row"><span class="alert-sign">!</span><div><strong>${escapeHtml(a.client_id || "Sistema")}</strong><p>${escapeHtml(a.message || "Falha sem detalhes.")}</p><small>${formatDate(a.at)}${a.run_id ? ` · ${escapeHtml(a.run_id)}` : ""}</small>${a.job_id ? `<p><button class="mini-button" data-retry-job="${escapeHtml(a.job_id)}" type="button">Tentar novamente</button></p>` : ""}</div></div>`).join("") : '<div class="loading">Nenhum alerta registrado.</div>';
  document.querySelectorAll("[data-retry-job]").forEach(button => button.addEventListener("click", async () => {
    try { await api(`/api/jobs/${encodeURIComponent(button.dataset.retryJob)}/retry`, { method: "POST", body: {} }); await refresh(); toast("Nova tentativa adicionada à fila."); }
    catch (error) { toast(error.message, "error"); }
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

function openRun(clientIds) {
  state.runClientIds = clientIds;
  const all = clientIds.length !== 1; $("#run-title").textContent = all ? "Gerar todos os clientes" : "Gerar relatório";
  $("#run-subtitle").textContent = all ? `${clientIds.length} clientes serão adicionados à fila.` : state.data.clients.find(c => c.client_id === clientIds[0])?.display_name || "";
  $("#run-dialog").showModal();
}

function toast(message, type = "success") {
  const node = document.createElement("div"); node.className = `toast ${type}`; node.textContent = message; $("#toast-stack").append(node); setTimeout(() => node.remove(), 4500);
}

document.querySelectorAll(".close-dialog").forEach(button => button.addEventListener("click", () => button.closest("dialog").close()));
$("#manage-button").addEventListener("click", () => $("#manage-dialog").showModal());
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
$("#run-all-button").addEventListener("click", () => openRun(state.data.clients.filter(c => c.enabled && c.credentials_ready).map(c => c.client_id)));
$("#detail-run-button").addEventListener("click", () => { $("#client-dialog").close(); openRun([state.selectedClient]); });
$("#detail-check-button").addEventListener("click", event => testConnections([state.selectedClient], event.currentTarget));
$("#detail-edit-button").addEventListener("click", () => { const clientId = state.selectedClient; $("#client-dialog").close(); $("#manage-dialog").showModal(); editClient(clientId); });
$("#search-input").addEventListener("input", event => { state.filter = event.target.value.trim().toLowerCase(); render(); });

document.querySelectorAll('input[name="period_type"]').forEach(input => input.addEventListener("change", () => {
  const type = document.querySelector('input[name="period_type"]:checked').value;
  $("#days-fields").classList.toggle("hidden", type !== "days"); $("#range-fields").classList.toggle("hidden", type !== "range");
}));

const nameField = $("#client-form [name='display_name']");
const idField = $("#client-form [name='client_id']");
const tenantField = $("#client-form [name='tenant_id']");
const clientForm = $("#client-form");

function resetClientForm() {
  state.editingClientId = null;
  clientForm.reset();
  clientForm.classList.remove("editing");
  idField.readOnly = false;
  idField.dataset.manual = "false";
  tenantField.dataset.manual = "false";
  clientForm.elements.access_key.placeholder = "Opcional agora";
  clientForm.elements.secret_key.placeholder = "Opcional agora";
  clientForm.elements.intelligence_enabled.checked = true;
  clientForm.elements.was_enabled.checked = true;
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
  clientForm.classList.add("editing");
  clientForm.elements.display_name.value = client.display_name || "";
  idField.value = client.client_id;
  idField.readOnly = true;
  idField.dataset.manual = "true";
  tenantField.value = client.tenant_id || client.client_id;
  tenantField.dataset.manual = "true";
  clientForm.elements.access_key.value = "";
  clientForm.elements.secret_key.value = "";
  clientForm.elements.access_key.placeholder = "Preencha somente para trocar";
  clientForm.elements.secret_key.placeholder = "Preencha somente para trocar";
  clientForm.elements.tags.value = (client.tags || []).join(", ");
  clientForm.elements.intelligence_enabled.checked = Boolean(client.intelligence_enabled);
  clientForm.elements.was_enabled.checked = Boolean(client.was_enabled);
  clientForm.elements.cloud_enabled.checked = Boolean(client.cloud_enabled);
  clientForm.elements.include_output.checked = Boolean(client.include_output);
  clientForm.elements.show_source_filters.checked = Boolean(client.show_source_filters);
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

$("#cancel-edit-button").addEventListener("click", resetClientForm);

clientForm.addEventListener("submit", async event => {
  event.preventDefault(); const form = new FormData(event.currentTarget); const button = event.currentTarget.querySelector('button[type="submit"]'); button.disabled = true;
  const payload = Object.fromEntries(form.entries()); ["intelligence_enabled","was_enabled","cloud_enabled","include_output","show_source_filters"].forEach(name => payload[name] = form.has(name));
  const editing = state.editingClientId;
  const path = editing ? `/api/clients/${encodeURIComponent(editing)}` : "/api/clients";
  const method = editing ? "PATCH" : "POST";
  try { await api(path, { method, body: payload }); resetClientForm(); await refresh(); toast(editing ? "Cliente atualizado." : "Cliente adicionado."); }
  catch (error) { toast(error.message, "error"); } finally { button.disabled = false; }
});

$("#run-form").addEventListener("submit", async event => {
  event.preventDefault(); const form = new FormData(event.currentTarget); const type = form.get("period_type"); const payload = { client_ids: state.runClientIds, mode: "manual" };
  if (type === "days") payload.days = Number(form.get("days"));
  if (type === "range") {
    if (!form.get("start_at") || !form.get("end_at")) { toast("Informe o início e o fim do período.", "error"); return; }
    payload.start_at = new Date(form.get("start_at")).toISOString(); payload.end_at = new Date(form.get("end_at")).toISOString();
  }
  const button = event.currentTarget.querySelector('button[type="submit"]'); button.disabled = true;
  try { const result = await api("/api/jobs", { method: "POST", body: payload }); $("#run-dialog").close(); await refresh(); toast(`${result.jobs.length} execução(ões) adicionada(s) à fila.`); }
  catch (error) { toast(error.message, "error"); } finally { button.disabled = false; }
});

refresh(false); setInterval(() => refresh(true), 3000);
