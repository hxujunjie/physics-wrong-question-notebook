const $ = id => document.getElementById(id);
const token = window.REVIEW_TOKEN;
const pathKeys = ["clean_pdf", "photo_root", "output_root", "recognition_json"];
const state = {
  paths: {}, students: [], filter: "all", manifest: null, page: 0, mode: "low", selected: null, undo: null,
  scale: 1, x: 0, y: 0, fit: true, dragging: null,
  crop: {evidence: null, segments: [], page: 0, box: null, image: null},
  provider: null, preflight: null, workMode: "online", budgetModalShown: false, presets: [],
  // Prevent the 1.5s health poll from overwriting fields the teacher is editing.
  settingsFormDirty: false, settingsHydrated: false
};
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));

async function api(path, method = "GET", body) {
  let response;
  try {
    response = await fetch(path, {
      method,
      headers: {"Content-Type": "application/json", "X-Review-Token": token},
      body: body === undefined ? undefined : JSON.stringify(body)
    });
  } catch {
    throw Error("本地服务连接失败，请重新点击桌面启动器");
  }
  let data;
  try { data = await response.json(); } catch { throw Error("本地服务返回无效响应"); }
  if (!response.ok) throw Error(data.error || "请求失败");
  return data;
}

function notice(message) {
  const n = $("notice");
  n.textContent = message;
  n.classList.add("show");
  clearTimeout(notice.timer);
  notice.timer = setTimeout(() => n.classList.remove("show"), 3000);
}

function fail(error, retry) {
  $("error-title").textContent = "操作失败";
  $("error-text").textContent = error.message || String(error);
  $("error-retry").hidden = !retry;
  $("error-card").hidden = false;
  state.retry = retry || null;
}

function pathLabel(id, value) {
  const empty = {
    clean_pdf: "请选择 PDF",
    photo_root: "请选择“学生姓名/照片”总目录",
    output_root: "默认创建在照片目录旁",
    recognition_json: "请选择 recognition_result.json"
  };
  $(id).textContent = value || empty[id];
  $(id).title = value || "";
}

function loadPaths() {
  try { state.paths = JSON.parse(localStorage.getItem("teacher-ai-paths")) || {}; } catch { state.paths = {}; }
  pathKeys.forEach(k => pathLabel(k, state.paths[k]));
}

function savePaths() {
  localStorage.setItem("teacher-ai-paths", JSON.stringify(state.paths));
}

// Built-in endpoints so URL still fills even before /api/health returns presets.
const PROVIDER_BASE_URLS = {
  zhipu: "https://open.bigmodel.cn/api/paas/v4",
  dashscope: "https://dashscope.aliyuncs.com/compatible-mode/v1",
  openai: "https://api.openai.com/v1",
  google: "https://generativelanguage.googleapis.com/v1beta/openai/",
  xai: "https://api.x.ai/v1",
  volcengine: "https://ark.cn-beijing.volces.com/api/v3",
  deepseek: "https://api.deepseek.com",
};
const CUSTOM_MODEL_VALUE = "__custom__";

function currentPreset() {
  const id = $("provider-select")?.value || state.provider?.provider || "zhipu";
  return (state.presets || []).find(item => item.id === id) || null;
}

function resolveBaseUrl(provider, settings, preset) {
  const id = provider || "zhipu";
  if (id === "custom") {
    return String(settings?.base_url || settings?.effective_base_url || "").trim();
  }
  // Prefer live/effective URL, then preset, then built-in map. Never leave empty for known vendors.
  return String(
    settings?.effective_base_url ||
    preset?.base_url ||
    settings?.base_url ||
    PROVIDER_BASE_URLS[id] ||
    ""
  ).trim();
}

/** Mirror backend ai_settings.infer_from_api_key so paste fills fields before save. */
function looksLikeZhipuKey(key) {
  const k = String(key || "").trim();
  const low = k.toLowerCase();
  if (!k || low.startsWith("sk-") || low.startsWith("xai-") || low.startsWith("ark-")) return false;
  if ((k.match(/\./g) || []).length === 1) {
    const [left, right] = k.split(".");
    if (left.length < 8 || right.length < 8) return false;
    return /^[a-zA-Z0-9_-]+$/.test(left) && /^[a-zA-Z0-9_-]+$/.test(right);
  }
  const body = k.replace(/[-_]/g, "");
  return k.length >= 32 && /^[a-zA-Z0-9]+$/.test(body);
}

function inferFromApiKey(apiKey) {
  const key = String(apiKey || "").trim();
  const low = key.toLowerCase();
  const byId = id => (state.presets || []).find(item => item.id === id);
  if (key.length < 8) return null;
  if (low.startsWith("xai-")) {
    const preset = byId("xai") || {};
    return {
      provider: "xai",
      base_url: preset.base_url || PROVIDER_BASE_URLS.xai,
      model: preset.default_model || "grok-2-vision-1212",
      guess_label: "xAI Grok",
    };
  }
  if (low.startsWith("ark-")) {
    const preset = byId("volcengine") || {};
    return {
      provider: "volcengine",
      base_url: preset.base_url || PROVIDER_BASE_URLS.volcengine,
      model: preset.default_model || "",
      guess_label: "豆包 / 火山方舟",
    };
  }
  if (key.startsWith("AIza") || low.startsWith("aiza")) {
    const preset = byId("google") || {};
    return {
      provider: "google",
      base_url: preset.base_url || PROVIDER_BASE_URLS.google,
      model: preset.default_model || "gemini-3.5-flash",
      guess_label: "Google Gemini",
    };
  }
  // 智谱密钥多为 id.secret（含点），必须在 sk- 规则之前判断。
  if (looksLikeZhipuKey(key)) {
    const preset = byId("zhipu") || {};
    return {
      provider: "zhipu",
      base_url: preset.base_url || PROVIDER_BASE_URLS.zhipu,
      model: preset.default_model || "glm-4.6v-flash",
      guess_label: "智谱 GLM（免费视觉 glm-4.6v-flash）",
    };
  }
  if (low.startsWith("sk-proj-") || low.startsWith("sk-or-")) {
    const preset = byId("openai") || {};
    return {
      provider: "openai",
      base_url: preset.base_url || PROVIDER_BASE_URLS.openai,
      model: preset.default_model || "gpt-4o-mini",
      guess_label: "OpenAI / GPT",
    };
  }
  if (low.startsWith("sk-")) {
    const preset = byId("dashscope") || {};
    return {
      provider: "dashscope",
      base_url: preset.base_url || PROVIDER_BASE_URLS.dashscope,
      model: preset.default_model || "qwen2.5-vl-3b-instruct",
      guess_label: "通义千问（sk- 默认；可改选 OpenAI/GPT）",
    };
  }
  const preset = byId("zhipu") || state.presets?.[0] || {};
  return {
    provider: preset.id || "zhipu",
    base_url: preset.base_url || PROVIDER_BASE_URLS.zhipu,
    model: preset.default_model || "glm-4.6v-flash",
    guess_label: (preset.name || "智谱 GLM") + "（默认免费模型）",
  };
}

function fillModelSelect(provider, selectedModel) {
  const preset = (state.presets || []).find(item => item.id === provider) || {};
  const models = Array.isArray(preset.models) ? preset.models.filter(m => m && m.id) : [];
  const allowCustom = Boolean(
    preset.allow_custom_model ||
    provider === "custom" ||
    provider === "volcengine" ||
    provider === "google" ||
    !models.length
  );
  const select = $("model-select");
  const customInput = $("model-name");
  let options = models.map(m => {
    const tag = m.free ? " · 免费/低价" : "";
    return `<option value="${esc(m.id)}">${esc(m.label || m.id)}${tag}</option>`;
  });
  if (allowCustom) {
    options.push(`<option value="${CUSTOM_MODEL_VALUE}">其他 / 自定义型号…</option>`);
  }
  if (!options.length) {
    options = [`<option value="${CUSTOM_MODEL_VALUE}">请填写模型名</option>`];
  }
  select.innerHTML = options.join("");
  const wanted = String(selectedModel || preset.default_model || "").trim();
  const known = models.some(m => m.id === wanted);
  if (wanted && known) {
    select.value = wanted;
    customInput.hidden = true;
    customInput.value = wanted;
  } else if (wanted && allowCustom) {
    select.value = CUSTOM_MODEL_VALUE;
    customInput.hidden = false;
    customInput.value = wanted;
  } else if (models[0]?.id) {
    select.value = models[0].id;
    customInput.hidden = true;
    customInput.value = models[0].id;
  } else {
    select.value = CUSTOM_MODEL_VALUE;
    customInput.hidden = false;
    if (!customInput.value) customInput.value = "";
  }
  select.hidden = false;
}

function currentModelValue() {
  const select = $("model-select");
  if (!select || select.hidden) return ($("model-name").value || "").trim();
  if (select.value === CUSTOM_MODEL_VALUE) return ($("model-name").value || "").trim();
  return (select.value || $("model-name").value || "").trim();
}

function applyProviderFields(settings) {
  const provider = settings?.provider || $("provider-select").value || "zhipu";
  if ($("provider-select").value !== provider && [...$("provider-select").options].some(o => o.value === provider)) {
    $("provider-select").value = provider;
  }
  const preset = (state.presets || []).find(item => item.id === provider);
  const custom = provider === "custom";
  // Always show URL so teachers can see auto-filled endpoint; only custom is editable.
  $("base-url").hidden = false;
  $("base-url-label").hidden = false;
  $("base-url").readOnly = !custom;
  $("base-url").classList.toggle("readonly-field", !custom);
  const url = resolveBaseUrl(provider, settings, preset);
  $("base-url").value = url;
  $("base-url").placeholder = custom ? "请填写 OpenAI 兼容接口，例如 https://..." : "自动填入的服务商接口地址";
  const model = settings?.model || preset?.default_model || "";
  fillModelSelect(provider, model);
  $("model-hint").textContent = settings?.guess_label
    ? `已识别：${settings.guess_label}。${preset?.model_hint || ""}`
    : (preset?.model_hint || (url ? `接口：${url}` : ""));
}

function applyKeyInference(apiKey) {
  const guessed = inferFromApiKey(apiKey);
  if (!guessed) return;
  // Ensure select has the option before writing fields.
  if (guessed.provider && $("provider-select").options.length &&
      [...$("provider-select").options].some(o => o.value === guessed.provider)) {
    $("provider-select").value = guessed.provider;
  }
  applyProviderFields({
    provider: guessed.provider,
    model: guessed.model,
    base_url: guessed.base_url,
    effective_base_url: guessed.base_url || PROVIDER_BASE_URLS[guessed.provider] || "",
    guess_label: guessed.guess_label,
  });
}

function fillProviderSelect(presets, selected) {
  state.presets = presets || [];
  const select = $("provider-select");
  const current = select.value;
  select.innerHTML = state.presets.map(item => `<option value="${esc(item.id)}">${esc(item.name)}</option>`).join("");
  if (selected && state.presets.some(item => item.id === selected)) select.value = selected;
  else if (current && state.presets.some(item => item.id === current)) select.value = current;
  else if (state.presets[0]) select.value = state.presets[0].id;
}

function markSettingsDirty() {
  state.settingsFormDirty = true;
}

function applyProviderStatus(settings) {
  state.provider = settings || {};
  $("provider-status").textContent = settings?.configured
    ? `已配置 ${settings.provider_name || ""} ${settings.key_hint || ""}`
    : "未配置";
  ready();
}

function applyProvider(settings, clearInput = false, forceFields = false) {
  state.provider = settings || {};
  if (!state.settingsFormDirty || forceFields || !state.settingsHydrated) {
    fillProviderSelect(settings?.presets || state.presets, settings?.provider);
    applyProviderFields(settings);
    state.settingsHydrated = true;
    state.settingsFormDirty = false;
  } else if (settings?.presets?.length && !$("provider-select").options.length) {
    fillProviderSelect(settings.presets, settings.provider);
  }
  applyProviderStatus(settings);
  if (clearInput) $("api-key").value = "";
}

function setWorkMode(mode) {
  state.workMode = mode;
  const online = mode === "online";
  $("mode-online").classList.toggle("active", online);
  $("mode-import").classList.toggle("active", !online);
  $("online-settings").hidden = !online;
  $("import-settings").hidden = online;
  $("batch").hidden = !online;
  $("import-json").hidden = online;
  $("photo-root-label").hidden = !online;
  $("photo-root-btn").hidden = !online;
  ready();
}

function ready() {
  const missing = [];
  if (state.workMode === "online") {
    if (!state.provider?.configured) missing.push("识别服务 API Key");
    if (!state.paths.clean_pdf) missing.push("干净 PDF");
    if (!state.paths.photo_root) missing.push("学生照片总目录");
    $("path-hint").textContent = missing.length
      ? `还缺少：${missing.join("、")}`
      : "路径已选择；开始后会先扫描学生并显示调用预算。";
    $("batch").disabled = missing.length > 0;
    $("import-json").disabled = true;
  } else {
    if (!state.paths.recognition_json) missing.push("识别结果 JSON");
    if (!state.paths.clean_pdf) missing.push("干净 PDF");
    $("path-hint").textContent = missing.length
      ? `还缺少：${missing.join("、")}`
      : "可直接导入 JSON，无需 API Key。";
    $("import-json").disabled = missing.length > 0;
    $("batch").disabled = true;
  }
}

function applyJob(job) {
  const running = job.status === "running";
  const result = job.result || {};
  const usage = job.usage || result.usage || {};
  $("job-title").textContent = ({
    idle: "等待任务",
    running: "正在智能识别",
    success: "识别完成",
    partial: "识别完成（有异常）",
    cancelled: "任务已取消",
    budget_paused: "调用预算已暂停",
    failed: "识别失败"
  })[job.status] || job.status;
  $("job-detail").textContent = [job.current_text, job.error].filter(Boolean).join(" · ") || "识别后确认不确定项，再生成错题集。";
  $("job-stats").textContent = `${job.total_count ? `照片 ${job.completed_count || 0}/${job.total_count}` : ""}${usage.api_calls !== undefined ? ` · API ${usage.api_calls}/${job.call_budget || "—"} · Tokens ${usage.total_tokens || 0}` : ""}`;
  $("progress-bar").style.width = `${job.progress || 0}%`;
  ready();
  if (running) {
    $("batch").disabled = true;
    $("import-json").disabled = true;
  }
  document.querySelectorAll("[data-pick]").forEach(b => { b.disabled = running; });
  $("cancel-job").hidden = !running;
  if ((job.status === "success" || job.status === "partial" || job.status === "idle") && state.paths.output_dir) {
    updateExportActions();
  }
  if (job.status === "budget_paused" && !state.budgetModalShown) {
    state.budgetModalShown = true;
    showModal(
      "调用预算已到达",
      `已使用 ${usage.api_calls || 0} 次 API 调用。可以追加调用预算后继续，不会重新识别已完成照片。`,
      [
        {label: "暂不继续", action: closeModal},
        {label: "追加 50 次", className: "primary", action: () => api("/api/continue-budget", "POST", {extra_calls: 50}).then(refresh)}
      ]
    );
  }
  if (job.status !== "budget_paused") state.budgetModalShown = false;
}

async function refresh() {
  const h = await api("/api/health");
  $("health").textContent = `本地服务正常 · v${h.app_version || "?"} · 127.0.0.1:${h.port}`;
  // Never stomp model/provider inputs while the teacher is editing them.
  applyProvider(h.provider || h.grok, false, false);
  applyJob(h.job_state);
  if (!$("review").hidden) return;
  await refreshStudents();
}

function updateExportActions() {
  // Keep export available whenever a task output dir is known.
  // Do not require a fresh job success event — teachers often return from review later.
  const hasOutput = Boolean(state.paths.output_dir);
  if ($("finalize")) $("finalize").hidden = !hasOutput;
  if ($("open")) $("open").hidden = !hasOutput;
}

async function refreshStudents() {
  if (!state.paths.output_dir) {
    state.students = [];
    updateExportActions();
    return;
  }
  try {
    state.students = (await api(`/api/students?output_dir=${encodeURIComponent(state.paths.output_dir)}`)).students;
    renderStudents();
    updateExportActions();
  } catch {
    /* ignore empty/transient */
    updateExportActions();
  }
}

function renderStats() {
  const rows = state.students;
  document.querySelectorAll("#stats strong").forEach((n, i) => {
    n.textContent = [
      rows.length,
      rows.reduce((s, r) => s + r.photo_count, 0),
      rows.reduce((s, r) => s + Math.max(0, r.total_review_page_count - r.reviewed_page_count), 0),
      rows.filter(r => r.status === "reviewed").length
    ][i] ?? 0;
  });
}

function renderStudents() {
  const q = $("search").value.trim().toLowerCase();
  const counts = {all: state.students.length, review_required: 0, reviewed: 0, failed: 0};
  state.students.forEach(r => { counts[r.status] = (counts[r.status] || 0) + 1; });
  document.querySelectorAll("[data-filter]").forEach(b => {
    b.classList.toggle("active", b.dataset.filter === state.filter);
    b.querySelector("b").textContent = counts[b.dataset.filter] || 0;
  });
  let rows = state.students.filter(r => (state.filter === "all" || r.status === state.filter) && r.student.toLowerCase().includes(q));
  rows.sort((a, b) => $("sort").value === "name"
    ? a.student.localeCompare(b.student, "zh")
    : Number(a.status !== "review_required") - Number(b.status !== "review_required"));
  $("students").innerHTML = rows.map(r =>
    `<tr data-student="${esc(r.student)}"><td>${esc(r.student)}</td><td>${r.photo_count}</td><td>${r.wrong_question_count}</td><td>${r.low_confidence_count}</td><td>${r.reviewed_page_count}/${r.total_review_page_count}</td><td><span class="status ${r.status}">${r.status === "reviewed" ? "✓ 已完成" : r.status === "failed" ? "× 失败" : "? 待复核"}</span></td></tr>`
  ).join("") || '<tr><td colspan="6" class="empty">没有符合条件的学生。</td></tr>';
  $("students").querySelectorAll("[data-student]").forEach(row => { row.onclick = () => openReview(row.dataset.student); });
  renderStats();
}

async function openReview(student) {
  try {
    state.manifest = await api(`/api/manifest?output_dir=${encodeURIComponent(state.paths.output_dir)}&student=${encodeURIComponent(student)}`);
    const pending = state.manifest.photo_tasks.findIndex(p => !p.review_completed);
    state.page = pending < 0 ? 0 : pending;
    state.selected = null;
    state.mode = "low";
    $("list-view").hidden = true;
    $("review").hidden = false;
    $("back").hidden = false;
    $("view-title").textContent = "确认题目";
    $("view-subtitle").textContent = "先处理待确认题；需要时可切换整页查看，最后再生成错题集。";
    $("student-name").textContent = student;
    renderReview();
  } catch (e) { fail(e); }
}

function currentPage() { return state.manifest?.photo_tasks[state.page]; }
function label(q) {
  return q.decision === "wrong" ? ["错题", "wrong"]
    : q.decision === "correct" ? ["正确", "correct"]
    : ["待确认", "pending"];
}
function visibleQuestions(p = currentPage()) {
  const all = p?.page_review_questions || [];
  return (state.mode === "low"
    ? all.filter(q => q.requires_review || q.decision === null || !q.content_complete)
    : all
  ).slice().sort((a, b) => (a.reading_order || 0) - (b.reading_order || 0));
}

function renderReview() {
  const p = currentPage();
  if (!p) return;
  const all = p.page_review_questions || [];
  const pending = all.filter(q => q.requires_review && q.decision === null);
  if (state.mode === "low" && !visibleQuestions(p).length) state.mode = "page";
  const qs = visibleQuestions(p);
  if (!qs.some(q => q.evidence_id === state.selected)) {
    state.selected = qs.find(q => q.decision === null)?.evidence_id || qs[0]?.evidence_id || null;
  }
  $("low-count").textContent = pending.length;
  $("page-count").textContent = all.length;
  $("low-tab").classList.toggle("active", state.mode === "low");
  $("page-tab").classList.toggle("active", state.mode === "page");
  $("page-info").textContent = `第 ${state.page + 1}/${state.manifest.photo_tasks.length} 张`;
  const pdfPage = p.matched_pdf_page ?? "";
  $("match-info").innerHTML = `PDF 第 <input id="pdf-page-edit" class="pdf-page-edit" type="number" min="1" step="1" value="${esc(pdfPage)}" title="可直接修改本页对应的干净 PDF 页码"> 页`;
  const pageEdit = $("pdf-page-edit");
  if (pageEdit) {
    pageEdit.onclick = e => e.stopPropagation();
    pageEdit.onchange = () => updatePhotoPdfPage(pageEdit.value);
    pageEdit.onkeydown = e => {
      if (e.key === "Enter") {
        e.preventDefault();
        pageEdit.blur();
      }
    };
  }
  $("review-progress").textContent = p.review_completed ? "本页已完成" : (pending.length ? `待确认 ${pending.length} 题` : "可完成本页");
  $("complete").disabled = false;
  $("prev-photo").disabled = state.page === 0;
  $("next-photo").disabled = state.page === state.manifest.photo_tasks.length - 1;
  $("questions").innerHTML = qs.map(questionCard).join("") || '<article class="question"><strong>当前没有待确认题目</strong><p>可切换到「整页查看」通览本页，或直接完成本页。</p></article>';
  $("questions").querySelectorAll("[data-evidence]").forEach(card => {
    card.onclick = e => {
      if (e.target.tagName !== "BUTTON" && e.target.tagName !== "INPUT") selectQuestion(card.dataset.evidence);
    };
    card.querySelectorAll("button[data-decision]").forEach(b => {
      b.onclick = e => { e.stopPropagation(); decide(card.dataset.evidence, b.dataset.decision); };
    });
    card.querySelector(".qnum-edit")?.addEventListener("change", e => updateQnum(card.dataset.evidence, e.target.value));
    card.querySelector(".crop-button")?.addEventListener("click", e => { e.stopPropagation(); openCrop(card.dataset.evidence); });
  });
  loadPhoto(p);
}

function cropStatus(q, page = currentPage()) {
  const spec = q.crop_spec || {};
  const manual = Array.isArray(spec.manual_segments) && spec.manual_segments.length > 0;
  const pageIndex = Number.isInteger(spec.pdf_page_index_0based)
    ? spec.pdf_page_index_0based
    : (Number.isInteger(q.page_index) ? q.page_index : null);
  const qnum = String(q.qnum || spec.question_no || "").trim();
  const pageOk = pageIndex != null && page?.registration_reliable !== false;

  if (manual || q.crop_method === "教师手动框选") {
    return { level: "ok", needCrop: false, optionalCrop: true, text: "已手动框选", action: "manual_done" };
  }
  if (pageOk && qnum && (q.crop_source === "原始PDF" || ["pdf_index", "pdf_bbox", "pdf"].includes(spec.source))) {
    return { level: "ok", needCrop: false, optionalCrop: true, text: "可自动裁切（页码+题号）", action: "auto" };
  }
  if (pageOk && qnum) {
    return { level: "ok", needCrop: false, optionalCrop: true, text: "可尝试自动裁切（页码+题号）", action: "auto" };
  }
  if (pageOk && !qnum) {
    return { level: "warn", needCrop: true, optionalCrop: true, text: "请先填写题号；若仍失败再手动框选", action: "fix_qnum" };
  }
  if (!pageOk && qnum) {
    return { level: "bad", needCrop: true, optionalCrop: false, text: "PDF 页不可靠：请核对页码或手动框选", action: "manual_or_page" };
  }
  return { level: "bad", needCrop: true, optionalCrop: false, text: "未能自动定位：请手动框选", action: "manual" };
}

function questionCard(q) {
  const [text, kind] = label(q);
  const selected = state.selected === q.evidence_id;
  const crop = cropStatus(q);
  const needs = crop.needCrop || q.content_complete === false;
  const decisionHint = kind === "pending" ? "<p class=\"q-hint\">请确认本题是正确还是错题。</p>" : "";
  const cropHint = `<p class="q-hint crop-${crop.level}">裁切：${esc(crop.text)}</p>`;
  const cropBtn = needs
    ? `<button class="crop-button" type="button">在干净 PDF 上框选</button>`
    : (crop.optionalCrop ? `<button class="crop-button quiet" type="button">改框选</button>` : "");
  return `<article class="question ${kind} ${selected ? "selected" : ""} ${needs ? "incomplete" : ""}" data-evidence="${esc(q.evidence_id)}"><div class="q-head"><strong>第 <input class="qnum-edit" value="${esc(q.qnum)}"> 题</strong><span class="state ${kind}">${text}</span>${q.teacher_modified ? "<i class=\"modified-dot\" title=\"已修改\"></i>" : ""}</div>${decisionHint}${cropHint}<div class="q-actions"><button class="correct-button" data-decision="correct">✓ 正确</button><button class="wrong-button" data-decision="wrong">× 错题</button>${cropBtn}</div></article>`;
}

function pageRotation(p = currentPage()) {
  const deg = Number(p?.display_rotation_deg || 0) % 360;
  return [0, 90, 180, 270].includes(deg) ? deg : 0;
}

function rotateNormPoint(x, y, degrees) {
  const xx = Number(x), yy = Number(y);
  if (degrees === 90) return [1 - yy, xx];
  if (degrees === 180) return [1 - xx, 1 - yy];
  if (degrees === 270) return [yy, 1 - xx];
  return [xx, yy];
}

function rotatedNaturalSize(img, degrees = pageRotation()) {
  const w = img.naturalWidth || 0;
  const h = img.naturalHeight || 0;
  if (degrees === 90 || degrees === 270) return {width: h, height: w};
  return {width: w, height: h};
}

function loadPhoto(p) {
  const img = $("photo");
  const url = `/api/photo?output_dir=${encodeURIComponent(state.paths.output_dir)}&student=${encodeURIComponent(state.manifest.student)}&photo_sha256=${encodeURIComponent(p.photo_sha256)}`;
  $("position-warning").hidden = p.registration_reliable !== false;
  $("position-warning").textContent = p.registration_reliable === false ? "页面匹配置信度不足，请人工核对 PDF 页码与题目。可用 ↺/↻ 先把照片转正。" : "";
  if (img.dataset.url === url) { fit(); draw(p); return; }
  img.dataset.url = url;
  img.onload = () => { fit(); draw(p); };
  img.onerror = () => fail(Error("学生照片无法读取"));
  img.src = url;
}

function draw(p) {
  const svg = $("overlay");
  const img = $("photo");
  const degrees = pageRotation(p);
  // Markers stay in the photo's native pixel space. CSS rotates #media-stage
  // (image + overlay together), so do NOT pre-rotate marker coordinates again.
  const width = img.naturalWidth || 0;
  const height = img.naturalHeight || 0;
  svg.innerHTML = "";
  if (width > 0 && height > 0) {
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.style.width = `${width}px`;
    svg.style.height = `${height}px`;
    svg.style.left = "0";
    svg.style.top = "0";
    svg.style.position = "absolute";
  }
  // Keep marker visual size close to the old 1000-viewBox era (~1.7% of short side).
  const shortSide = Math.max(1, Math.min(width || 1, height || 1));
  const markerR = Math.max(16, Math.min(40, shortSide * 0.017));
  const fontSize = Math.max(15, Math.min(34, markerR * 1.0));
  const textDy = fontSize * 0.35;
  for (const q of p.page_review_questions || []) {
    if (!q.photo_anchor_norm) continue;
    const x = Number(q.photo_anchor_norm[0]) * width;
    const y = Number(q.photo_anchor_norm[1]) * height;
    const [, kind] = label(q);
    const fill = kind === "wrong" ? "#d70015" : kind === "correct" ? "#248a3d" : "#b25000";
    const symbol = kind === "wrong" ? "×" : kind === "correct" ? "✓" : "?";
    const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
    g.setAttribute("class", "marker");
    // Counter-rotate only the glyph so ✓/×/? stay upright after CSS photo rotation.
    g.setAttribute("transform", `translate(${x}, ${y}) rotate(${-degrees})`);
    g.innerHTML = `<circle cx="0" cy="0" r="${markerR}" fill="${fill}"/><text x="0" y="${textDy}" text-anchor="middle" fill="white" font-size="${fontSize}" font-weight="700">${symbol}</text>`;
    g.onclick = () => selectQuestion(q.evidence_id);
    svg.append(g);
  }
  if ($("rotation-level")) $("rotation-level").textContent = `${degrees}°`;
}

function selectQuestion(id) {
  state.selected = id;
  renderReview();
  document.querySelector(`[data-evidence="${CSS.escape(id)}"]`)?.scrollIntoView({block: "nearest"});
  revealSelected();
}

async function decide(id, decision) {
  const q = currentPage().page_review_questions.find(x => x.evidence_id === id);
  const previous = q.decision;
  try {
    const r = await api("/api/decision", "POST", {
      output_dir: state.paths.output_dir,
      student: state.manifest.student,
      evidence_id: id,
      decision
    });
    Object.assign(q, r.question);
    state.undo = {id, previous};
    $("undo").disabled = false;
    $("save-state").textContent = "已保存";
    renderReview();
  } catch (e) { fail(e); }
}

async function updateQnum(id, qnum) {
  try {
    const r = await api("/api/question", "POST", {
      output_dir: state.paths.output_dir,
      student: state.manifest.student,
      evidence_id: id,
      qnum
    });
    Object.assign(currentPage().page_review_questions.find(q => q.evidence_id === id), r.question);
    $("save-state").textContent = "题号已保存";
    renderReview();
  } catch (e) { fail(e); }
}

async function completePage(allow = false) {
  const p = currentPage();
  const pending = (p.page_review_questions || []).filter(q => q.requires_review && q.decision === null);
  if (pending.length && !allow) {
    showModal("仍有待确认题目", `当前页还有 ${pending.length} 道待确认题。可返回处理，或跳过并继续（之后仍可再改）。`, [
      {label: "返回处理", action: closeModal},
      {label: "跳过并继续", className: "primary", action: () => completePage(true)}
    ]);
    return;
  }
  try {
    const r = await api("/api/complete-page", "POST", {
      output_dir: state.paths.output_dir,
      student: state.manifest.student,
      photo_sha256: p.photo_sha256,
      allow_unresolved: allow
    });
    Object.assign(p, r.page);
    const next = state.page + 1;
    if (next < state.manifest.photo_tasks.length) {
      state.page = next;
      state.selected = null;
      state.mode = "low";
      renderReview();
      notice("本页已保存，已前往下一张");
      return;
    }
    await refreshStudents();
    showModal("本学生已处理完", "修改已记录。请返回列表，确认其他学生后点击「生成错题集」。", [
      {label: "整页再看一遍", action: restartReview},
      {label: "返回列表", className: "primary", action: backToList}
    ]);
  } catch (e) { fail(e); }
}

function restartReview() {
  state.page = 0;
  state.selected = null;
  state.mode = "page";
  renderReview();
  notice("已切换到整页查看");
}

function showModal(title, text, actions) {
  $("modal-title").textContent = title;
  $("modal-text").textContent = text;
  const a = $("modal-actions");
  a.innerHTML = "";
  actions.forEach(x => {
    const b = document.createElement("button");
    b.textContent = x.label;
    b.className = x.className || "";
    b.onclick = async () => {
      if (x.action === closeModal) closeModal();
      else { closeModal(); await x.action?.(); }
    };
    a.append(b);
  });
  $("modal").hidden = false;
}

function closeModal() { $("modal").hidden = true; }

function backToList() {
  $("review").hidden = true;
  $("list-view").hidden = false;
  $("back").hidden = true;
  $("view-title").textContent = "任务首页";
  $("view-subtitle").textContent = "识别后确认不确定项，再生成错题集 PDF。";
  updateExportActions();
  refresh();
}

function fit() {
  const img = $("photo"), box = $("viewer");
  if (!img.naturalWidth) return;
  const size = rotatedNaturalSize(img);
  state.scale = Math.min((box.clientWidth - 20) / size.width, (box.clientHeight - 20) / size.height, 1);
  state.x = Math.max(10, (box.clientWidth - size.width * state.scale) / 2);
  state.y = Math.max(10, (box.clientHeight - size.height * state.scale) / 2);
  state.fit = true;
  transform();
}

function transform() {
  const img = $("photo");
  const degrees = pageRotation();
  const size = rotatedNaturalSize(img, degrees);
  // Rotate around image center, then place the rotated bounding box with translate/scale.
  $("media-stage").style.transformOrigin = "0 0";
  $("media-stage").style.transform =
    `translate(${state.x}px, ${state.y}px) scale(${state.scale}) ` +
    `translate(${size.width / 2}px, ${size.height / 2}px) rotate(${degrees}deg) ` +
    `translate(${-(img.naturalWidth || 0) / 2}px, ${-(img.naturalHeight || 0) / 2}px)`;
  $("zoom-level").textContent = `${Math.round(state.scale * 100)}%`;
  if ($("rotation-level")) $("rotation-level").textContent = `${degrees}°`;
}

function zoom(factor, clientX, clientY) {
  const box = $("viewer");
  const rect = box.getBoundingClientRect();
  const localX = (clientX ?? rect.left + box.clientWidth / 2) - rect.left;
  const localY = (clientY ?? rect.top + box.clientHeight / 2) - rect.top;
  const contentX = (localX - state.x) / state.scale;
  const contentY = (localY - state.y) / state.scale;
  const next = Math.max(0.25, Math.min(3, state.scale * factor));
  state.scale = next;
  state.x = localX - contentX * next;
  state.y = localY - contentY * next;
  state.fit = false;
  transform();
}

function revealSelected() {
  const q = (currentPage()?.page_review_questions || []).find(item => item.evidence_id === state.selected);
  const img = $("photo"), box = $("viewer");
  if (!q?.photo_anchor_norm || !img.naturalWidth) return;
  const degrees = pageRotation();
  const size = rotatedNaturalSize(img, degrees);
  // Convert native photo point into the rotated bounding-box space used by fit/transform.
  const [nx, ny] = rotateNormPoint(q.photo_anchor_norm[0], q.photo_anchor_norm[1], degrees);
  state.x = box.clientWidth / 2 - nx * size.width * state.scale;
  state.y = box.clientHeight / 2 - ny * size.height * state.scale;
  state.fit = false;
  transform();
}

async function rotatePage(delta) {
  const p = currentPage();
  if (!p) return;
  const next = (pageRotation(p) + delta + 360) % 360;
  try {
    const r = await api("/api/photo-rotation", "POST", {
      output_dir: state.paths.output_dir,
      student: state.manifest.student,
      photo_sha256: p.photo_sha256,
      degrees: next
    });
    Object.assign(p, r.page);
    $("save-state").textContent = `已旋转至 ${pageRotation(p)}°`;
    fit();
    draw(p);
    notice(`照片已转正到 ${pageRotation(p)}°，可继续复核`);
  } catch (e) { fail(e); }
}

async function updatePhotoPdfPage(value) {
  const p = currentPage();
  if (!p) return;
  const pageNumber = Number(value);
  if (!Number.isInteger(pageNumber) || pageNumber < 1) {
    notice("请输入从 1 开始的 PDF 页码");
    renderReview();
    return;
  }
  if (Number(p.matched_pdf_page) === pageNumber) return;
  try {
    const r = await api("/api/photo-pdf-page", "POST", {
      output_dir: state.paths.output_dir,
      student: state.manifest.student,
      photo_sha256: p.photo_sha256,
      pdf_page: pageNumber
    });
    if (r.page) Object.assign(p, r.page);
    $("save-state").textContent = `PDF 页码已改为第 ${pageNumber} 页`;
    renderReview();
    if (r.page_changed) {
      const extra = r.synced_count ? `，已同步 ${r.synced_count} 题` : "";
      notice(`本页 PDF 页码已改为第 ${pageNumber} 页${extra}`);
    } else {
      notice(`PDF 页码仍是第 ${pageNumber} 页`);
    }
  } catch (e) {
    fail(e, () => updatePhotoPdfPage(value));
    renderReview();
  }
}

function openCrop(evidence) {
  const matched = currentPage().matched_pdf_page;
  state.crop = {evidence, segments: [], page: Number.isInteger(matched) ? Math.max(0, matched - 1) : 0, box: null};
  $("crop-modal").hidden = false;
  loadCropPage();
}

async function loadCropPage() {
  const c = state.crop;
  $("crop-page").value = c.page + 1;
  const url = `/api/pdf-page?output_dir=${encodeURIComponent(state.paths.output_dir)}&student=${encodeURIComponent(state.manifest.student)}&page_index=${c.page}`;
  const img = $("pdf-page-image");
  img.onload = () => {
    c.image = img;
    const canvas = $("crop-canvas");
    canvas.width = img.clientWidth;
    canvas.height = img.clientHeight;
    canvas.style.width = `${img.clientWidth}px`;
    canvas.style.height = `${img.clientHeight}px`;
    drawCropBox();
  };
  img.src = url;
  renderCropSegments();
}

function drawCropBox() {
  const canvas = $("crop-canvas"), ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (!state.crop.box) return;
  const b = state.crop.box;
  ctx.strokeStyle = "#d70015";
  ctx.lineWidth = 3;
  ctx.strokeRect(b.x * canvas.width, b.y * canvas.height, b.w * canvas.width, b.h * canvas.height);
}

function renderCropSegments() {
  $("crop-segments").textContent = state.crop.segments.map((s, i) =>
    `片段 ${i + 1}：第 ${s.page_index + 1} 页 [${s.bbox_norm.map(x => x.toFixed(3)).join(", ")}]`
  ).join("；");
}

function addCropSegment() {
  const b = state.crop.box;
  if (!b || b.w <= 0 || b.h <= 0) { notice("请先在页面上拖出题目框"); return; }
  state.crop.segments.push({
    page_index: state.crop.page,
    bbox_norm: [b.x, b.y, b.x + b.w, b.y + b.h],
    is_continuation: state.crop.segments.length > 0
  });
  state.crop.box = null;
  drawCropBox();
  renderCropSegments();
}

async function saveCrop() {
  if (state.crop.box) addCropSegment();
  if (!state.crop.segments.length) { notice("请至少添加一个 PDF 片段"); return; }
  try {
    const r = await api("/api/manual-crop", "POST", {
      output_dir: state.paths.output_dir,
      student: state.manifest.student,
      evidence_id: state.crop.evidence,
      manual_segments: state.crop.segments,
      sync_page_siblings: true
    });
    const page = currentPage();
    if (r.page && page) {
      // Replace whole photo task so sibling page-index sync is visible immediately.
      Object.assign(page, r.page);
    } else if (r.question) {
      Object.assign(page.page_review_questions.find(q => q.evidence_id === state.crop.evidence), r.question);
    }
    $("crop-modal").hidden = true;
    renderReview();
    if (r.page_changed && r.synced_count > 0) {
      notice(`已保存框选，并同步本页其他 ${r.synced_count} 题到 PDF 第 ${r.pdf_page} 页`);
    } else if (r.page_changed) {
      notice(`已保存框选，本页 PDF 页码已更正为第 ${r.pdf_page} 页`);
    } else {
      notice("已保存干净 PDF 框选");
    }
  } catch (e) { fail(e); }
}

function selectedStudents() {
  return [...document.querySelectorAll("#student-select input:checked")].map(item => item.value);
}

function renderStudentSelect(pre) {
  const box = $("student-select");
  state.preflight = pre;
  box.hidden = false;
  const indexPart = pre.pdf_index_cached
    ? `PDF索引已缓存（0 次）`
    : `PDF索引约 ${pre.pdf_index_calls ?? "?"} 次`;
  const photoPart = `照片识别 ${pre.photo_calls ?? pre.photo_count} 次`;
  box.innerHTML = `<b>本次处理学生（${pre.photo_count} 张照片 · ${indexPart} · ${photoPart} · 预计最多 ${pre.call_budget} 次 · ${esc(pre.provider || "")}/${esc(pre.model || "")}）</b>` +
    pre.students.map(item =>
      `<label><input type="checkbox" value="${esc(item.student)}" ${item.selected ? "checked" : ""}>${esc(item.student)}（${item.photo_count} 张）</label>`
    ).join("");
}

document.querySelectorAll("[data-pick]").forEach(b => {
  b.onclick = async () => {
    const id = b.dataset.pick;
    try {
      const kind = id === "clean_pdf" ? "pdf" : id === "recognition_json" ? "json" : "directory";
      const r = await api("/api/pick-path", "POST", {kind, purpose: id});
      if (r.path) {
        state.paths[id] = r.path;
        state.preflight = null;
        $("student-select").hidden = true;
        pathLabel(id, r.path);
        savePaths();
        ready();
      }
    } catch (e) { fail(e); }
  };
});

$("provider-select").onchange = () => {
  markSettingsDirty();
  const provider = $("provider-select").value;
  const preset = currentPreset();
  applyProviderFields({
    provider,
    model: preset?.default_model || "",
    base_url: provider === "custom" ? ($("base-url").value || "") : (preset?.base_url || PROVIDER_BASE_URLS[provider] || ""),
    effective_base_url: preset?.base_url || PROVIDER_BASE_URLS[provider] || "",
    guess_label: "",
  });
  if (provider === "custom" && !$("base-url").value) {
    $("model-hint").textContent = preset?.model_hint || "自行填写 Base URL 与模型名";
  }
};

$("model-select").onchange = () => {
  markSettingsDirty();
  const custom = $("model-select").value === CUSTOM_MODEL_VALUE;
  $("model-name").hidden = !custom;
  if (!custom) $("model-name").value = $("model-select").value;
  else if (!$("model-name").value) $("model-name").focus();
};

$("batch").onclick = async () => {
  try {
    const body = {...state.paths, selected_students: state.preflight ? selectedStudents() : undefined};
    if (!state.preflight) {
      const pre = await api("/api/preflight", "POST", body);
      renderStudentSelect(pre);
      notice(
        pre.pdf_index_cached
          ? `已扫描完成：同一 PDF 将复用本机索引（0 次索引调用），仅识别照片约 ${pre.photo_calls ?? pre.photo_count} 次`
          : `已扫描完成：首次需建 PDF 索引约 ${pre.pdf_index_calls ?? "?"} 次 + 照片 ${pre.photo_calls ?? pre.photo_count} 次；完成后会缓存，下次同 PDF 不再全量索引`
      );
      return;
    }
    if (!body.selected_students.length) { notice("请至少选择一名学生"); return; }
    const started = await api("/api/batch", "POST", {...body, call_budget: state.preflight.call_budget});
    state.paths.output_dir = started.output_dir;
    savePaths();
    updateExportActions();
    await refresh();
  } catch (e) { fail(e); }
};

$("import-json").onclick = async () => {
  try {
    const started = await api("/api/import-recognition", "POST", {
      recognition_json: state.paths.recognition_json,
      clean_pdf: state.paths.clean_pdf,
      output_root: state.paths.output_root || undefined
    });
    state.paths.output_dir = started.output_dir;
    updateExportActions();
    savePaths();
    $("open").hidden = false;
    $("finalize").hidden = false;
    notice("JSON 已导入，可开始复核");
    await refresh();
  } catch (e) { fail(e); }
};

$("save-provider").onclick = async () => {
  try {
    const key = $("api-key").value.trim();
    // 老师主流程：只填 Key 保存 → 后端按密钥自动识别服务商/模型/地址。
    // 绝不把表单里残留的 grok-4.5 / api.x.ai 一并提交，避免旧值覆盖自动识别。
    // 已配置后仅改模型/服务商时，可不重复粘贴 Key。
    // 粘贴 Key 时前端已自动填服务商/模型；保存时以表单为准，这样模型下拉框的选择一定生效。
    // 仅当还没选出服务商时才让后端 auto_detect。
    const provider = $("provider-select").value;
    const model = currentModelValue();
    const body = {
      api_key: key,
      auto_detect: Boolean(key) && !provider,
      provider: provider || null,
      model: model || null,
      base_url: provider === "custom" ? $("base-url").value : null,
    };
    const result = await api("/api/settings/provider", "POST", body);
    state.settingsFormDirty = false;
    applyProvider(result, Boolean(key), true);
    if (result.auto_detected || result.guess_label) {
      const url = result.effective_base_url || result.base_url || "";
      notice(`已保存。自动识别为：${result.guess_label || result.provider_name || result.provider} / ${result.model || "（请补模型名）"}${url ? ` · ${url}` : ""}`);
    } else {
      notice("识别服务已加密保存到本机");
    }
  } catch (e) { fail(e); }
};

$("test-provider").onclick = async () => {
  try {
    await api("/api/settings/provider/test", "POST", {});
    notice("识别服务连接正常");
  } catch (e) { fail(e); }
};

$("clear-provider").onclick = async () => {
  try {
    state.settingsFormDirty = false;
    applyProvider(await api("/api/settings/provider", "POST", {clear: true}), true, true);
    $("model-name").value = "";
    if ($("model-select")) $("model-select").innerHTML = "";
    $("base-url").value = "";
    notice("已清除本机 API Key");
  } catch (e) { fail(e); }
};

["api-key", "model-name", "base-url", "model-select"].forEach(id => {
  const el = $(id);
  if (!el) return;
  el.addEventListener("input", markSettingsDirty);
  el.addEventListener("change", markSettingsDirty);
});

// Paste / type API key → immediately fill provider, model, and URL (do not keep old Grok values).
$("api-key").addEventListener("input", () => {
  const key = $("api-key").value.trim();
  if (key.length >= 8) applyKeyInference(key);
});
$("api-key").addEventListener("paste", () => {
  setTimeout(() => {
    const key = $("api-key").value.trim();
    if (key.length >= 8) applyKeyInference(key);
  }, 0);
});

$("mode-online").onclick = () => setWorkMode("online");
$("mode-import").onclick = () => setWorkMode("import");

$("cancel-job").onclick = () => api("/api/cancel", "POST", {}).then(() => notice("将停止新的识别请求，已完成结果会保留")).catch(fail);
$("open").onclick = $("result-open").onclick = () => api("/api/open-output", "POST", {output_dir: state.paths.output_dir}).catch(fail);

$("finalize").onclick = async () => {
  try {
    const r = await api("/api/finalize", "POST", {output_dir: state.paths.output_dir, allow_incomplete: false});
    if (r.status === "review_required") {
      showModal("仍有页面未完成", `还有 ${r.unreviewed_page_count} 页未点「完成本页」。可先处理，或按当前结果直接生成错题集。`, [
        {label: "去处理", className: "primary", action: () => {
          const s = state.students.find(x => x.status === "review_required");
          if (s) openReview(s.student);
        }},
        {label: "仍按当前结果生成", action: async () => showResult(await api("/api/finalize", "POST", {output_dir: state.paths.output_dir, allow_incomplete: true}))}
      ]);
    } else showResult(r);
  } catch (e) { fail(e); }
};

function showResult(r) {
  const rebuilt = r.rebuilt || [];
  const existing = r.existing_pdfs || [];
  const pdfCount = rebuilt.length || existing.length;
  const pending = rebuilt.reduce((n, item) => n + (item.content_pending?.length || 0), 0);
  const parts = [`已生成/已有 ${pdfCount} 份错题集 PDF`];
  if (r.unreviewed_page_count) parts.push(`未完成页面 ${r.unreviewed_page_count}`);
  if (pending) parts.push(`未能从干净 PDF 定位的题目 ${pending} 道（未写入）`);
  $("result-text").textContent = parts.join("；") + "。";
  $("result-card").hidden = false;
  notice(pdfCount ? "错题集已生成" : "导出完成（当前没有可写入的错题 PDF）");
}

$("shutdown").onclick = () => showModal("退出本地服务", "确认停止教师端服务吗？", [
  {label: "取消", action: closeModal},
  {label: "退出服务", className: "danger", action: () => api("/api/shutdown", "POST", {}).catch(fail)}
]);

$("back").onclick = backToList;
$("low-tab").onclick = () => { state.mode = "low"; renderReview(); };
$("page-tab").onclick = () => { state.mode = "page"; renderReview(); };
$("prev-photo").onclick = () => { state.page--; state.selected = null; renderReview(); };
$("next-photo").onclick = () => {
  if (state.page < state.manifest.photo_tasks.length - 1) {
    state.page++;
    state.selected = null;
    renderReview();
  }
};
$("rotate-left").onclick = () => rotatePage(-90);
$("rotate-right").onclick = () => rotatePage(90);
$("complete").onclick = () => completePage(false);
$("undo").onclick = async () => {
  if (!state.undo) return;
  try {
    const decision = state.undo.previous == null ? "pending" : state.undo.previous;
    await decide(state.undo.id, decision);
    state.undo = null;
    $("undo").disabled = true;
    $("save-state").textContent = "已撤销";
  } catch (e) { fail(e); }
};
$("crop-cancel").onclick = () => { $("crop-modal").hidden = true; };
$("crop-add").onclick = addCropSegment;
$("crop-save").onclick = saveCrop;
$("crop-page").onchange = () => {
  state.crop.page = Math.max(0, Number($("crop-page").value || 1) - 1);
  state.crop.box = null;
  loadCropPage();
};
$("fit").onclick = fit;
$("zoom-in").onclick = () => zoom(1.2);
$("zoom-out").onclick = () => zoom(0.8);
$("reset").onclick = () => { state.scale = 1; state.x = state.y = 0; state.fit = false; transform(); };
$("error-close").onclick = () => { $("error-card").hidden = true; };
$("error-retry").onclick = () => { const r = state.retry; $("error-card").hidden = true; r?.(); };
$("search").oninput = renderStudents;
$("sort").onchange = renderStudents;
document.querySelectorAll("[data-filter]").forEach(b => {
  b.onclick = () => { state.filter = b.dataset.filter; renderStudents(); };
});

$("viewer").addEventListener("wheel", event => {
  event.preventDefault();
  zoom(event.deltaY < 0 ? 1.1 : 0.9, event.clientX, event.clientY);
}, {passive: false});
$("viewer").addEventListener("pointerdown", event => {
  state.dragging = {x: event.clientX, y: event.clientY, px: state.x, py: state.y};
  $("viewer").setPointerCapture(event.pointerId);
  $("viewer").classList.add("dragging");
});
$("viewer").addEventListener("pointermove", event => {
  if (!state.dragging) return;
  state.x = state.dragging.px + event.clientX - state.dragging.x;
  state.y = state.dragging.py + event.clientY - state.dragging.y;
  state.fit = false;
  transform();
});
$("viewer").addEventListener("pointerup", () => { state.dragging = null; $("viewer").classList.remove("dragging"); });
$("viewer").addEventListener("pointercancel", () => { state.dragging = null; $("viewer").classList.remove("dragging"); });
window.addEventListener("resize", () => { if (state.fit) fit(); });

const canvas = $("crop-canvas");
let drag = null;
canvas.addEventListener("pointerdown", e => {
  const r = canvas.getBoundingClientRect();
  drag = {x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height};
  canvas.setPointerCapture(e.pointerId);
});
canvas.addEventListener("pointermove", e => {
  if (!drag) return;
  const r = canvas.getBoundingClientRect();
  const x = (e.clientX - r.left) / r.width;
  const y = (e.clientY - r.top) / r.height;
  state.crop.box = {
    x: Math.max(0, Math.min(drag.x, x)),
    y: Math.max(0, Math.min(drag.y, y)),
    w: Math.min(1, Math.abs(x - drag.x)),
    h: Math.min(1, Math.abs(y - drag.y))
  };
  drawCropBox();
});
canvas.addEventListener("pointerup", () => { drag = null; });

loadPaths();
setWorkMode("online");
ready();
updateExportActions();
refresh().catch(e => { $("health").textContent = "本地服务连接失败"; fail(e, refresh); });
setInterval(() => refresh().catch(() => {}), 1500);
