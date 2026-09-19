const endpoints = {
  snapshot: "/api/v1/ops/snapshot",
  services: "/api/v1/ops/services",
  application: "/api/v1/ops/application",
};

const dom = {
  connectionDot: document.querySelector("#connectionDot"),
  connectionText: document.querySelector("#connectionText"),
  refreshButton: document.querySelector("#refreshButton"),
  linkGrid: document.querySelector("#linkGrid"),
  appHealth: document.querySelector("#appHealth"),
  appPid: document.querySelector("#appPid"),
  appAddress: document.querySelector("#appAddress"),
  appLatency: document.querySelector("#appLatency"),
  lastRefresh: document.querySelector("#lastRefresh"),
  startAppButton: document.querySelector("#startAppButton"),
  restartAppButton: document.querySelector("#restartAppButton"),
  stopAppButton: document.querySelector("#stopAppButton"),
  commodityHealth: document.querySelector("#commodityHealth"),
  endpointList: document.querySelector("#endpointList"),
  composeStatus: document.querySelector("#composeStatus"),
  serviceGrid: document.querySelector("#serviceGrid"),
  operationLog: document.querySelector("#operationLog"),
  clearLogButton: document.querySelector("#clearLogButton"),
};

const serviceNames = {
  mysql: "MySQL",
  "product-postgres": "商品 PostgreSQL",
  etcd: "Etcd",
  minio: "MinIO",
  milvus: "Milvus",
  "commodity-management": "商品资料管理",
  attu: "Attu / Milvus UI",
};

let refreshTimer = null;

function formatTime(value) {
  return new Intl.DateTimeFormat("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(value);
}

function setPill(element, className, text) {
  element.className = `pill ${className}`;
  element.textContent = text;
}

function appendLog(kind, title, detail) {
  const item = document.createElement("li");
  item.className = kind;
  const heading = document.createElement("strong");
  heading.textContent = `${formatTime(new Date())} · ${title}`;
  const body = document.createElement("span");
  body.textContent = detail;
  item.append(heading, body);
  dom.operationLog.prepend(item);
  while (dom.operationLog.children.length > 50) dom.operationLog.lastElementChild.remove();
}

async function fetchSnapshot() {
  dom.connectionText.textContent = "刷新中…";
  try {
    const response = await fetch(endpoints.snapshot, { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    if (payload.code !== 0) throw new Error(payload.message || "API returned an error");
    renderSnapshot(payload.data);
  } catch (error) {
    dom.connectionDot.className = "status-dot offline";
    dom.connectionText.textContent = "主服务离线";
    setPill(dom.appHealth, "error", "离线");
    dom.appPid.textContent = "-";
    dom.appLatency.textContent = "-";
    appendLog("error", "状态刷新失败", `${error.message}；如果刚点击“停止主服务”，这是预期状态。`);
  }
}

function renderLinks(links) {
  dom.linkGrid.replaceChildren();
  for (const link of links) {
    const card = document.createElement("a");
    card.className = "link-card";
    card.href = link.link_url;
    card.target = "_blank";
    card.rel = "noopener noreferrer";
    const title = document.createElement("h2");
    title.textContent = link.link_title;
    const description = document.createElement("p");
    description.textContent = link.link_description;
    const footer = document.createElement("footer");
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = link.link_badge;
    const url = document.createElement("span");
    url.className = "link-url";
    url.textContent = link.link_url;
    footer.append(badge, url);
    card.append(title, description, footer);
    dom.linkGrid.append(card);
  }
}

function renderEndpoint(endpoint) {
  const item = document.createElement("div");
  item.className = "endpoint-item";
  const dot = document.createElement("span");
  dot.className = `status-dot ${endpoint.is_ok ? "online" : "offline"}`;
  const content = document.createElement("div");
  const title = document.createElement("strong");
  title.textContent = endpoint.display_name;
  const url = document.createElement("small");
  url.textContent = endpoint.endpoint_url;
  content.append(title, url);
  const state = document.createElement("span");
  state.className = "endpoint-state";
  state.textContent = endpoint.is_ok
    ? `${endpoint.http_status} · ${endpoint.latency_ms ?? "-"}ms`
    : `${endpoint.error_name || `HTTP ${endpoint.http_status ?? "-"}`} · ${endpoint.latency_ms ?? "-"}ms`;
  item.append(dot, content, state);
  return item;
}

function renderService(service) {
  const item = document.createElement("article");
  item.className = "service-item";
  const header = document.createElement("header");
  const name = document.createElement("strong");
  name.textContent = serviceNames[service.service_name] || service.service_name;
  const dot = document.createElement("span");
  const state = service.service_state.toLowerCase();
  dot.className = `status-dot ${state === "running" ? "online" : state === "not_created" ? "" : "offline"}`;
  header.append(name, dot);
  const detail = document.createElement("small");
  const health = service.service_health && service.service_health !== "unknown" ? service.service_health : state;
  detail.textContent = `${state}${service.container_name ? ` · ${service.container_name}` : ""} · ${health}`;
  item.append(header, detail);
  if (service.published_ports.length) {
    const ports = document.createElement("p");
    ports.className = "service-ports";
    ports.textContent = service.published_ports.join("  ");
    item.append(ports);
  }
  const controls = document.createElement("div");
  controls.className = "service-actions";
  for (const action of ["start", "restart", "stop"]) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `service-action mini-button ${action === "start" ? "secondary-button" : action === "restart" ? "ghost-button" : "danger-button"}`;
    button.dataset.target = service.service_name;
    button.dataset.action = action;
    button.textContent = action === "start" ? "启动" : action === "restart" ? "重启" : "停止";
    controls.append(button);
  }
  item.append(controls);
  return item;
}

function renderSnapshot(snapshot) {
  dom.connectionDot.className = "status-dot online";
  dom.connectionText.textContent = "已连接";
  dom.appPid.textContent = String(snapshot.application_pid);
  dom.appAddress.textContent = `127.0.0.1:${new URL(snapshot.page_links.find((link) => link.link_id === "health").link_url).port}`;
  dom.lastRefresh.textContent = formatTime(new Date());
  renderLinks(snapshot.page_links);

  const application = snapshot.http_endpoints.find((endpoint) => endpoint.endpoint_id === "application");
  if (application?.is_ok) {
    setPill(dom.appHealth, "ok", `运行中 · ${application.latency_ms ?? "-"}ms`);
    dom.appLatency.textContent = `${application.latency_ms ?? "-"}ms`;
  } else {
    setPill(dom.appHealth, "error", "异常");
    dom.appLatency.textContent = "-";
  }

  const commodity = snapshot.http_endpoints.find((endpoint) => endpoint.endpoint_id === "commodity");
  setPill(dom.commodityHealth, commodity?.is_ok ? "ok" : "error", commodity?.is_ok ? "健康" : "不可用");

  dom.endpointList.replaceChildren(...snapshot.http_endpoints.map(renderEndpoint));
  if (snapshot.compose_available) {
    setPill(dom.composeStatus, "ok", "Docker 可用");
    dom.serviceGrid.replaceChildren(...snapshot.compose_services.map(renderService));
  } else {
    setPill(dom.composeStatus, "error", "Docker 不可用");
    dom.serviceGrid.replaceChildren();
  }
}

async function postAction(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Service-Hub": "local" },
    body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({ message: `HTTP ${response.status}` }));
  if (!response.ok || payload.code !== 0) {
    throw new Error(payload.detail || payload.message || `HTTP ${response.status}`);
  }
  return payload.data;
}

async function runApplicationAction(action) {
  const needsConfirm = action !== "start";
  if (needsConfirm && !window.confirm(action === "restart" ? "确认重启主服务进程？" : "确认停止主服务进程？")) return;
  const button = action === "start" ? dom.startAppButton : action === "restart" ? dom.restartAppButton : dom.stopAppButton;
  button.disabled = true;
  appendLog("pending", `主服务 ${action}`, "请求已提交，等待后端响应…");
  try {
    const result = await postAction(endpoints.application, { action, confirm: needsConfirm });
    appendLog(result.success ? "ok" : "error", `主服务 ${action}`, result.result_detail);
    if (action !== "start") dom.connectionText.textContent = action === "stop" ? "停止中…" : "重启中…";
  } catch (error) {
    appendLog("error", `主服务 ${action}`, error.message);
  } finally {
    button.disabled = false;
    await fetchSnapshot();
  }
}

async function runServiceAction(target, action) {
  const button = document.querySelector(`.service-action[data-target="${target}"][data-action="${action}"]`);
  if (button) button.disabled = true;
  appendLog("pending", `Docker ${action}`, `目标：${target}；Docker Compose 操作可能需要几十秒。`);
  try {
    const result = await postAction(endpoints.services, { target, action });
    appendLog(result.success ? "ok" : "error", `Docker ${action}`, result.result_detail);
  } catch (error) {
    appendLog("error", `Docker ${action}`, error.message);
  } finally {
    if (button) button.disabled = false;
    await fetchSnapshot();
  }
}

dom.refreshButton.addEventListener("click", fetchSnapshot);
dom.startAppButton.addEventListener("click", () => runApplicationAction("start"));
dom.restartAppButton.addEventListener("click", () => runApplicationAction("restart"));
dom.stopAppButton.addEventListener("click", () => runApplicationAction("stop"));
dom.clearLogButton.addEventListener("click", () => dom.operationLog.replaceChildren());
document.addEventListener("click", (event) => {
  const button = event.target.closest(".service-action");
  if (button) void runServiceAction(button.dataset.target, button.dataset.action);
});

async function initialize() {
  appendLog("ok", "服务导航台已初始化", "每 5 秒自动探测主服务、商品服务和 Docker 依赖。");
  await fetchSnapshot();
  refreshTimer = setInterval(fetchSnapshot, 5000);
  window.addEventListener("beforeunload", () => clearInterval(refreshTimer));
}

void initialize();
