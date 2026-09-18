const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

const state = {
  page: 1,
  pageSize: 20,
  total: 0,
  editingProductId: null,
  editingSkuId: null,
  products: [],
  variants: [],
  pendingDelete: null,
  importFile: null,
};

const statusLabels = { draft: "草稿", published: "已发布", offline: "已下线" };
const stockLabels = { unknown: "未知", in_stock: "有货", low_stock: "库存紧张", out_of_stock: "缺货" };
const variantStatusLabels = { active: "启用", inactive: "停用" };

function renderIcons() {
  if (window.lucide) window.lucide.createIcons({ attrs: { "aria-hidden": "true" } });
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const response = await fetch(path, { credentials: "same-origin", ...options, headers });
  if (response.status === 401 && path !== "/api/auth/login") {
    showLogin();
    throw new Error("登录已过期，请重新登录");
  }
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const body = await response.json();
      if (Array.isArray(body.detail)) message = body.detail.map((item) => item.msg).join("；");
      else if (body.detail) message = body.detail;
    } catch (_) { /* response is not json */ }
    throw new Error(message);
  }
  if (response.status === 204) return null;
  return response.json();
}

function showLogin() {
  $("#loginView").hidden = false;
  $("#appView").hidden = true;
  closeDrawer();
}

function showApp(username) {
  $("#loginView").hidden = true;
  $("#appView").hidden = false;
  $("#currentUser").textContent = username;
}

function toast(title, message = "", type = "success") {
  const node = document.createElement("div");
  node.className = `toast ${type}`;
  const icon = document.createElement("i");
  icon.dataset.lucide = type === "error" ? "circle-alert" : "circle-check";
  const content = document.createElement("div");
  const strong = document.createElement("strong");
  strong.textContent = title;
  const span = document.createElement("span");
  span.textContent = message;
  content.append(strong, span);
  node.append(icon, content);
  $("#toastRegion").append(node);
  renderIcons();
  setTimeout(() => node.remove(), 4200);
}

function formatDate(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  }).format(new Date(value));
}

function dateTimeLocal(value) {
  if (!value) return "";
  const date = new Date(value);
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
}

function valueOrNull(selector) {
  const value = $(selector).value.trim();
  return value || null;
}

function linesToObject(text) {
  const result = {};
  text.split("\n").forEach((line) => {
    const separator = line.includes("=") ? "=" : line.includes("：") ? "：" : null;
    if (!separator) return;
    const [key, ...rest] = line.split(separator);
    if (key.trim() && rest.join(separator).trim()) result[key.trim()] = rest.join(separator).trim();
  });
  return result;
}

function objectToLines(value) {
  return Object.entries(value || {}).map(([key, item]) => `${key}=${item}`).join("\n");
}

function setTableState(kind) {
  $("#tableLoading").hidden = kind !== "loading";
  $("#emptyState").hidden = kind !== "empty";
  $("#tableWrap").hidden = kind !== "table";
  $("#pagination").hidden = kind !== "table";
}

async function loadStats() {
  const stats = await api("/api/stats");
  $("#metricTotal").textContent = stats.total;
  $("#metricPublished").textContent = stats.published;
  $("#metricDraft").textContent = stats.draft;
  $("#metricOffline").textContent = stats.offline;
  $("#metricVariants").textContent = stats.variants;
}

async function loadProducts() {
  setTableState("loading");
  const query = new URLSearchParams({
    search: $("#searchInput").value.trim(),
    status: $("#statusFilter").value,
    page: state.page,
    page_size: state.pageSize,
  });
  try {
    const data = await api(`/api/products?${query}`);
    state.products = data.items;
    state.total = data.total;
    renderProducts();
  } catch (error) {
    setTableState("empty");
    toast("读取失败", error.message, "error");
  }
}

function renderProducts() {
  const body = $("#productTableBody");
  body.replaceChildren();
  if (!state.products.length) {
    setTableState("empty");
    return;
  }
  state.products.forEach((product) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td class="product-cell"><strong></strong><span></span></td>
      <td class="code-cell"><strong></strong><span></span></td>
      <td><span class="status-badge status-${product.status}">${statusLabels[product.status]}</span></td>
      <td class="numeric-cell">${product.variant_count}</td>
      <td class="muted-cell owner-cell"></td>
      <td class="muted-cell">${formatDate(product.updated_at)}</td>
      <td class="actions-column"><div class="row-actions">
        <button class="icon-button edit-button" type="button" title="编辑商品" aria-label="编辑商品"><i data-lucide="pencil"></i></button>
        <button class="icon-button danger delete-button" type="button" title="删除商品" aria-label="删除商品"><i data-lucide="trash-2"></i></button>
      </div></td>`;
    row.querySelector(".product-cell strong").textContent = product.name;
    row.querySelector(".product-cell span").textContent = product.id;
    row.querySelector(".code-cell strong").textContent = product.platform.toUpperCase();
    row.querySelector(".code-cell span").textContent = product.internal_code || "未设置";
    row.querySelector(".owner-cell").textContent = product.owner_name || "—";
    row.querySelector(".edit-button").addEventListener("click", () => openEditProduct(product.id));
    row.querySelector(".delete-button").addEventListener("click", () => confirmProductDelete(product));
    body.append(row);
  });
  const pages = Math.max(1, Math.ceil(state.total / state.pageSize));
  $("#paginationSummary").textContent = `共 ${state.total} 件商品`;
  $("#pageNumber").textContent = `${state.page} / ${pages}`;
  $("#previousPage").disabled = state.page <= 1;
  $("#nextPage").disabled = state.page >= pages;
  setTableState("table");
  renderIcons();
}

function switchTab(tab) {
  $$(".form-tab").forEach((button) => button.classList.toggle("active", button.dataset.tab === tab));
  $$(".tab-panel").forEach((panel) => panel.classList.toggle("active", panel.dataset.panel === tab));
}

function resetProductForm() {
  $("#productForm").reset();
  $("#platform").value = "pdd";
  $("#productStatus").value = "draft";
  $("#productId").disabled = false;
  $("#specRows").replaceChildren();
  $("#summaryCount").textContent = "0";
  $("#productFormError").textContent = "";
  state.editingProductId = null;
  state.variants = [];
  renderVariants();
  $("#addVariantButton").disabled = true;
  $("#skuHint").textContent = "保存商品后可维护 SKU";
  switchTab("basic");
}

function openDrawer() {
  $("#drawerBackdrop").hidden = false;
  $("#editorGuide").hidden = false;
  $("#editorGuide").classList.remove("mobile-open");
  $("#productDrawer").classList.add("open");
  $("#productDrawer").setAttribute("aria-hidden", "false");
  document.body.style.overflow = "hidden";
}

function closeDrawer() {
  $("#drawerBackdrop").hidden = true;
  $("#editorGuide").hidden = true;
  $("#editorGuide").classList.remove("mobile-open");
  $("#productDrawer").classList.remove("open");
  $("#productDrawer").setAttribute("aria-hidden", "true");
  document.body.style.overflow = "";
}

function openCreateProduct() {
  resetProductForm();
  $("#drawerEyebrow").textContent = "NEW PRODUCT";
  $("#drawerTitle").textContent = "新建商品";
  openDrawer();
  $("#productId").focus();
}

async function openEditProduct(productId) {
  try {
    const [product, variants] = await Promise.all([
      api(`/api/products/${encodeURIComponent(productId)}`),
      api(`/api/products/${encodeURIComponent(productId)}/variants`),
    ]);
    resetProductForm();
    state.editingProductId = product.id;
    state.variants = variants;
    $("#drawerEyebrow").textContent = "EDIT PRODUCT";
    $("#drawerTitle").textContent = product.name;
    $("#productId").value = product.id;
    $("#productId").disabled = true;
    $("#internalCode").value = product.internal_code || "";
    $("#platform").value = product.platform;
    $("#productStatus").value = product.status;
    $("#productName").value = product.name;
    $("#brand").value = product.brand || "";
    $("#model").value = product.model || "";
    $("#categoryName").value = product.category_name || "";
    $("#summary").value = product.summary;
    $("#summaryCount").textContent = product.summary.length;
    $("#sellingPoints").value = (product.selling_points || []).join("\n");
    $("#usage").value = product.usage || "";
    $("#suitableFor").value = product.suitable_for || "";
    $("#warnings").value = product.warnings || "";
    $("#afterSalesLimits").value = product.after_sales_limits || "";
    $("#ownerName").value = product.owner_name || "";
    $("#reviewedBy").value = product.reviewed_by || "";
    $("#effectiveAt").value = dateTimeLocal(product.effective_at);
    $("#publishedAt").value = dateTimeLocal(product.published_at);
    $("#complianceNotes").value = product.compliance_notes || "";
    Object.entries(product.specifications || {}).forEach(([key, value]) => addSpecRow(key, value));
    $("#addVariantButton").disabled = false;
    $("#skuHint").textContent = `${variants.length} 个 SKU`;
    renderVariants();
    openDrawer();
  } catch (error) {
    toast("无法打开商品", error.message, "error");
  }
}

function addSpecRow(key = "", value = "") {
  const row = document.createElement("div");
  row.className = "key-value-row";
  row.innerHTML = `<input class="spec-key" maxlength="100" placeholder="规格名称"><input class="spec-value" maxlength="1000" placeholder="规格值"><button class="icon-button danger" type="button" title="删除规格" aria-label="删除规格"><i data-lucide="x"></i></button>`;
  row.querySelector(".spec-key").value = key;
  row.querySelector(".spec-value").value = value;
  row.querySelector("button").addEventListener("click", () => row.remove());
  $("#specRows").append(row);
  renderIcons();
}

function collectSpecifications() {
  const result = {};
  $$("#specRows .key-value-row").forEach((row) => {
    const key = row.querySelector(".spec-key").value.trim();
    const value = row.querySelector(".spec-value").value.trim();
    if (key && value) result[key] = value;
  });
  return result;
}

function collectProduct() {
  return {
    id: $("#productId").value.trim(),
    platform: $("#platform").value,
    internal_code: valueOrNull("#internalCode"),
    name: $("#productName").value.trim(),
    summary: $("#summary").value.trim(),
    brand: valueOrNull("#brand"),
    category_name: valueOrNull("#categoryName"),
    model: valueOrNull("#model"),
    selling_points: $("#sellingPoints").value.split("\n").map((item) => item.trim()).filter(Boolean),
    specifications: collectSpecifications(),
    usage: valueOrNull("#usage"),
    suitable_for: valueOrNull("#suitableFor"),
    warnings: valueOrNull("#warnings"),
    after_sales_limits: valueOrNull("#afterSalesLimits"),
    compliance_notes: valueOrNull("#complianceNotes"),
    status: $("#productStatus").value,
    owner_name: valueOrNull("#ownerName"),
    reviewed_by: valueOrNull("#reviewedBy"),
    effective_at: $("#effectiveAt").value ? new Date($("#effectiveAt").value).toISOString() : null,
    published_at: $("#publishedAt").value ? new Date($("#publishedAt").value).toISOString() : null,
  };
}

async function saveProduct(event) {
  event.preventDefault();
  const payload = collectProduct();
  const button = $("#saveProductButton");
  $("#productFormError").textContent = "";
  button.disabled = true;
  try {
    if (state.editingProductId) {
      delete payload.id;
      await api(`/api/products/${encodeURIComponent(state.editingProductId)}`, { method: "PUT", body: JSON.stringify(payload) });
      toast("商品已更新", "最新资料已保存到商品库");
    } else {
      await api("/api/products", { method: "POST", body: JSON.stringify(payload) });
      toast("商品已创建", "你可以继续编辑并添加 SKU");
    }
    closeDrawer();
    await Promise.all([loadProducts(), loadStats()]);
  } catch (error) {
    $("#productFormError").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

function confirmProductDelete(product) {
  state.pendingDelete = { type: "product", id: product.id };
  $("#confirmTitle").textContent = "删除商品";
  $("#confirmMessage").textContent = `确定删除“${product.name}”吗？其下所有 SKU 也会一并删除，此操作不可撤销。`;
  $("#confirmDialog").showModal();
}

function renderVariants() {
  $("#variantEmpty").hidden = state.variants.length > 0;
  $("#variantTableWrap").hidden = state.variants.length === 0;
  const body = $("#variantTableBody");
  body.replaceChildren();
  state.variants.forEach((variant) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td><strong></strong><br><span class="muted-cell"></span></td>
      <td class="attributes-cell"></td>
      <td class="numeric-cell"></td>
      <td><span class="status-badge stock-${variant.stock_status}">${stockLabels[variant.stock_status]}</span></td>
      <td><span class="status-badge status-${variant.status}">${variantStatusLabels[variant.status]}</span></td>
      <td><div class="row-actions"><button class="icon-button edit-variant" type="button" aria-label="编辑 SKU"><i data-lucide="pencil"></i></button><button class="icon-button danger delete-variant" type="button" aria-label="删除 SKU"><i data-lucide="trash-2"></i></button></div></td>`;
    row.querySelector("td strong").textContent = variant.name;
    row.querySelector("td span.muted-cell").textContent = variant.sku_id;
    row.querySelector(".attributes-cell").textContent = Object.entries(variant.attributes || {}).map(([key, value]) => `${key}: ${value}`).join(" · ") || "—";
    row.querySelector(".numeric-cell").textContent = variant.price === null ? "—" : `¥${Number(variant.price).toFixed(2)}`;
    row.querySelector(".edit-variant").addEventListener("click", () => openVariantDialog(variant));
    row.querySelector(".delete-variant").addEventListener("click", () => confirmVariantDelete(variant));
    body.append(row);
  });
  renderIcons();
}

function resetVariantForm() {
  $("#variantForm").reset();
  $("#stockStatus").value = "unknown";
  $("#variantStatus").value = "active";
  $("#variantFormError").textContent = "";
  $("#skuId").disabled = false;
  state.editingSkuId = null;
}

function openVariantDialog(variant = null) {
  resetVariantForm();
  if (variant) {
    state.editingSkuId = variant.sku_id;
    $("#variantDialogTitle").textContent = "编辑 SKU";
    $("#skuId").value = variant.sku_id;
    $("#skuId").disabled = true;
    $("#skuName").value = variant.name;
    $("#skuPrice").value = variant.price ?? "";
    $("#stockQuantity").value = variant.stock_quantity ?? "";
    $("#stockStatus").value = variant.stock_status;
    $("#variantStatus").value = variant.status;
    $("#variantAttributes").value = objectToLines(variant.attributes);
  } else {
    $("#variantDialogTitle").textContent = "添加 SKU";
  }
  $("#variantDialog").showModal();
}

async function saveVariant(event) {
  event.preventDefault();
  const payload = {
    sku_id: $("#skuId").value.trim(),
    name: $("#skuName").value.trim(),
    attributes: linesToObject($("#variantAttributes").value),
    currency: "CNY",
    price: $("#skuPrice").value === "" ? null : Number($("#skuPrice").value),
    stock_status: $("#stockStatus").value,
    stock_quantity: $("#stockQuantity").value === "" ? null : Number($("#stockQuantity").value),
    status: $("#variantStatus").value,
  };
  try {
    if (state.editingSkuId) {
      delete payload.sku_id;
      await api(`/api/variants/${encodeURIComponent(state.editingSkuId)}`, { method: "PUT", body: JSON.stringify(payload) });
      toast("SKU 已更新");
    } else {
      await api(`/api/products/${encodeURIComponent(state.editingProductId)}/variants`, { method: "POST", body: JSON.stringify(payload) });
      toast("SKU 已添加");
    }
    state.variants = await api(`/api/products/${encodeURIComponent(state.editingProductId)}/variants`);
    $("#skuHint").textContent = `${state.variants.length} 个 SKU`;
    renderVariants();
    $("#variantDialog").close();
    await loadStats();
  } catch (error) {
    $("#variantFormError").textContent = error.message;
  }
}

function confirmVariantDelete(variant) {
  state.pendingDelete = { type: "variant", id: variant.sku_id };
  $("#confirmTitle").textContent = "删除 SKU";
  $("#confirmMessage").textContent = `确定删除“${variant.name}”（${variant.sku_id}）吗？此操作不可撤销。`;
  $("#confirmDialog").showModal();
}

async function executeDelete() {
  if (!state.pendingDelete) return;
  const pending = state.pendingDelete;
  $("#confirmDeleteButton").disabled = true;
  try {
    if (pending.type === "product") {
      await api(`/api/products/${encodeURIComponent(pending.id)}`, { method: "DELETE" });
      toast("商品已删除");
      await Promise.all([loadProducts(), loadStats()]);
    } else {
      await api(`/api/variants/${encodeURIComponent(pending.id)}`, { method: "DELETE" });
      state.variants = await api(`/api/products/${encodeURIComponent(state.editingProductId)}/variants`);
      $("#skuHint").textContent = `${state.variants.length} 个 SKU`;
      renderVariants();
      toast("SKU 已删除");
      await loadStats();
    }
    $("#confirmDialog").close();
  } catch (error) {
    toast("删除失败", error.message, "error");
  } finally {
    $("#confirmDeleteButton").disabled = false;
    state.pendingDelete = null;
  }
}

function debounce(fn, delay) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}

function resetImportDialog() {
  $("#importForm").reset();
  $("#importFileName").textContent = "选择或拖入 .xlsx 文件";
  $("#importError").textContent = "";
  $("#importResult").hidden = true;
  $("#importResult").replaceChildren();
  $("#importResult").classList.remove("error");
  $("#submitImportButton").disabled = true;
  state.importFile = null;
}

function openImportDialog() {
  resetImportDialog();
  $("#importDialog").showModal();
}

function closeImportDialog() {
  $("#importDialog").close();
  resetImportDialog();
}

function selectImportFile(file) {
  $("#importError").textContent = "";
  $("#importResult").hidden = true;
  if (!file) {
    state.importFile = null;
    $("#importFileName").textContent = "选择或拖入 .xlsx 文件";
    $("#submitImportButton").disabled = true;
    return;
  }
  if (!file.name.toLowerCase().endsWith(".xlsx")) {
    $("#importError").textContent = "请选择 .xlsx 格式的 Excel 文件";
    return;
  }
  if (file.size > 5 * 1024 * 1024) {
    $("#importError").textContent = "文件不能超过 5 MB";
    return;
  }
  state.importFile = file;
  $("#importFileName").textContent = `${file.name} · ${(file.size / 1024).toFixed(1)} KB`;
  $("#submitImportButton").disabled = false;
}

function renderImportResult(result) {
  const container = $("#importResult");
  container.replaceChildren();
  container.hidden = false;
  container.classList.toggle("error", !result.imported);
  const heading = document.createElement("strong");
  if (result.imported) {
    heading.textContent = "导入完成";
    const summary = document.createElement("span");
    summary.textContent = `商品新增 ${result.products_created}、更新 ${result.products_updated}；SKU 新增 ${result.variants_created}、更新 ${result.variants_updated}。`;
    container.append(heading, summary);
    return;
  }
  heading.textContent = `发现 ${result.errors.length} 个问题，本次未写入数据`;
  const summary = document.createElement("span");
  summary.textContent = `已读取 ${result.products_found} 件商品、${result.variants_found} 个 SKU。请修改 Excel 后重新上传。`;
  const list = document.createElement("ul");
  result.errors.forEach((error) => {
    const item = document.createElement("li");
    item.textContent = `${error.sheet}${error.row ? ` 第 ${error.row} 行` : ""}：${error.message}`;
    list.append(item);
  });
  container.append(heading, summary, list);
}

async function submitImport(event) {
  event.preventDefault();
  if (!state.importFile) return;
  const button = $("#submitImportButton");
  const form = new FormData();
  form.append("file", state.importFile);
  button.disabled = true;
  $("#importError").textContent = "";
  try {
    const result = await api("/api/import/excel", { method: "POST", body: form });
    renderImportResult(result);
    if (result.imported) {
      toast("批量导入完成", `已处理 ${result.products_created + result.products_updated} 件商品`);
      state.page = 1;
      await Promise.all([loadProducts(), loadStats()]);
    }
  } catch (error) {
    $("#importError").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

function bindEvents() {
  $("#loginForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    $("#loginError").textContent = "";
    try {
      const result = await api("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ username: $("#loginUsername").value, password: $("#loginPassword").value }),
      });
      showApp(result.username);
      await Promise.all([loadProducts(), loadStats()]);
    } catch (error) {
      $("#loginError").textContent = error.message;
    }
  });
  $("#togglePassword").addEventListener("click", () => {
    const input = $("#loginPassword");
    input.type = input.type === "password" ? "text" : "password";
  });
  $("#logoutButton").addEventListener("click", async () => { await api("/api/auth/logout", { method: "POST" }); showLogin(); });
  $("#createProductButton").addEventListener("click", openCreateProduct);
  $("#batchImportButton").addEventListener("click", openImportDialog);
  $("#closeDrawerButton").addEventListener("click", closeDrawer);
  $("#toggleGuideButton").addEventListener("click", () => $("#editorGuide").classList.add("mobile-open"));
  $("#closeGuideButton").addEventListener("click", () => $("#editorGuide").classList.remove("mobile-open"));
  $("#cancelProductButton").addEventListener("click", closeDrawer);
  $("#drawerBackdrop").addEventListener("click", closeDrawer);
  $("#productForm").addEventListener("submit", saveProduct);
  $$(".form-tab").forEach((button) => button.addEventListener("click", () => switchTab(button.dataset.tab)));
  $("#addSpecButton").addEventListener("click", () => addSpecRow());
  $("#summary").addEventListener("input", (event) => { $("#summaryCount").textContent = event.target.value.length; });
  $("#searchInput").addEventListener("input", debounce(() => { state.page = 1; loadProducts(); }, 280));
  $("#statusFilter").addEventListener("change", () => { state.page = 1; loadProducts(); });
  $("#refreshButton").addEventListener("click", () => Promise.all([loadProducts(), loadStats()]));
  $("#previousPage").addEventListener("click", () => { state.page -= 1; loadProducts(); });
  $("#nextPage").addEventListener("click", () => { state.page += 1; loadProducts(); });
  $("#addVariantButton").addEventListener("click", () => openVariantDialog());
  $("#variantForm").addEventListener("submit", saveVariant);
  $("#closeVariantButton").addEventListener("click", () => $("#variantDialog").close());
  $("#cancelVariantButton").addEventListener("click", () => $("#variantDialog").close());
  $("#cancelConfirmButton").addEventListener("click", () => $("#confirmDialog").close());
  $("#confirmDeleteButton").addEventListener("click", executeDelete);
  $("#importForm").addEventListener("submit", submitImport);
  $("#closeImportButton").addEventListener("click", closeImportDialog);
  $("#cancelImportButton").addEventListener("click", closeImportDialog);
  $("#importFile").addEventListener("change", (event) => selectImportFile(event.target.files[0]));
  const dropzone = $("#importDropzone");
  ["dragenter", "dragover"].forEach((name) => dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    dropzone.classList.add("dragover");
  }));
  ["dragleave", "drop"].forEach((name) => dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    dropzone.classList.remove("dragover");
  }));
  dropzone.addEventListener("drop", (event) => selectImportFile(event.dataTransfer.files[0]));
  document.addEventListener("keydown", (event) => { if (event.key === "Escape" && $("#productDrawer").classList.contains("open")) closeDrawer(); });
}

async function start() {
  renderIcons();
  bindEvents();
  try {
    const me = await api("/api/auth/me");
    showApp(me.username);
    await Promise.all([loadProducts(), loadStats()]);
  } catch (_) {
    showLogin();
  }
}

start();
