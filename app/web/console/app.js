const state = {
  conversations: [], activeId: null, filter: "", search: "",
  connector: "stopped", globalAutomation: false, detailRequest: 0,
  conversationSignature: ""
};
const $ = (id) => document.getElementById(id);
const els = {
  list: $("conversationList"), count: $("conversationCount"), header: $("chatHeader"),
  messages: $("messageList"), form: $("replyForm"), input: $("replyInput"), send: $("sendButton"),
  hint: $("sendHint"), charCount: $("charCount"), connectorPill: $("connectorPill"),
  connectorButton: $("connectorButton"), globalAuto: $("globalAutomation"), convAuto: $("conversationAutomation"),
  goods: $("goodsContext"), goodsTag: $("goodsTag"), decision: $("decisionContent"),
  decisionRoute: $("decisionRoute"), useSuggestion: $("useSuggestion"), toast: $("toast")
};

async function api(path, options = {}) {
  const response = await fetch(`/api/v1${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options
  });
  let body;
  try { body = await response.json(); } catch { throw new Error(`服务返回异常 (${response.status})`); }
  if (!response.ok || body.code !== 0) throw new Error(body.message || "请求失败");
  return body.data;
}

function toast(message, error = false) {
  els.toast.textContent = message;
  els.toast.className = `toast show${error ? " error" : ""}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { els.toast.className = "toast"; }, 2800);
}

function fmtTime(value) {
  if (!value) return "";
  const normalized = /(?:Z|[+-]\d{2}:\d{2})$/.test(value) ? value : `${value}Z`;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return "";
  const today = new Date();
  return date.toDateString() === today.toDateString()
    ? date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })
    : date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

function node(tag, cls, text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
}

function avatarNode(conversation, className = "avatar") {
  const fallback = node("span", "avatar-fallback", (conversation?.display_name || "顾").slice(0, 1));
  const avatar = node("span", className);
  avatar.append(fallback);
  if (conversation?.avatar_url) {
    const image = document.createElement("img");
    image.src = conversation.avatar_url;
    image.alt = "";
    image.loading = "lazy";
    image.addEventListener("load", () => avatar.classList.add("has-image"));
    image.addEventListener("error", () => image.remove());
    avatar.prepend(image);
  }
  return avatar;
}

function renderConversations() {
  els.list.replaceChildren();
  els.count.textContent = `${state.conversations.length} 个会话`;
  if (!state.conversations.length) {
    els.list.append(node("div", "empty compact", "暂无符合条件的会话"));
    return;
  }
  for (const item of state.conversations) {
    const button = node("button", `conversation${item.id === state.activeId ? " active" : ""}`);
    button.type = "button";
    const avatar = avatarNode(item);
    const main = node("span", "conversation-main");
    const title = node("span", "conversation-name");
    title.append(node("strong", "", item.display_name || "顾客"), node("time", "", fmtTime(item.last_message_at)));
    const outboundState = ({ failed: "发送失败", uncertain: "发送结果待核实", sending: "正在发送", queued: "等待发送" })[item.last_outbound_status];
    const preview = node("span", "conversation-preview", outboundState || item.goods_name || receptionLabel(item));
    main.append(title, preview);
    button.append(avatar, main);
    if (item.unread_count) button.append(node("span", "badge", String(item.unread_count)));
    else button.append(node("span"));
    button.addEventListener("click", () => openConversation(item.id));
    els.list.append(button);
  }
}

function statusLabel(value) {
  return ({ pending: "等待处理", manual: "人工接管", auto_replied: "已自动回复" })[value] || "会话";
}

function receptionLabel(conversation) {
  if (!conversation.auto_reply_enabled) return "人工接待";
  if (!state.globalAutomation || state.connector !== "ready") return "自动接待已暂停";
  return conversation.state === "auto_replied" ? "已自动回复" : "自动接待";
}

function syncConversationPermission(conversation) {
  const canEnable = state.globalAutomation && state.connector === "ready";
  els.convAuto.checked = Boolean(conversation?.auto_reply_enabled);
  // 自动模式无权限时仍允许关闭，从而保证人工接管始终可用。
  els.convAuto.disabled = !conversation || (!conversation.auto_reply_enabled && !canEnable);
  const note = document.getElementById("conversationModeNote");
  if (!conversation) note.textContent = "选择会话后设置接待方式";
  else if (!conversation.auto_reply_enabled) note.textContent = canEnable ? "当前由人工接待" : "需先连接并开启全局自动接待";
  else if (!canEnable) note.textContent = "自动接待已暂停，可切换为人工接待";
  else note.textContent = "当前允许知识库自动回复";
}

function syncGlobalPermission() {
  const canEnable = state.connector === "ready";
  els.globalAuto.disabled = !state.globalAutomation && !canEnable;
  els.globalAuto.title = canEnable || state.globalAutomation
    ? ""
    : "请先连接拼多多客服页面";
}

async function loadConversations() {
  const params = new URLSearchParams({ limit: "100" });
  if (state.filter) params.set("state", state.filter);
  if (state.search) params.set("search", state.search);
  try {
    const data = await api(`/conversations?${params}`);
    const signature = JSON.stringify(data.items);
    if (signature !== state.conversationSignature) {
      state.conversationSignature = signature;
      state.conversations = data.items;
      renderConversations();
    }
    if (state.activeId && !state.conversations.some((x) => x.id === state.activeId)) state.activeId = null;
  } catch (error) {
    els.list.replaceChildren(node("div", "empty compact", error.message));
  }
}

function renderMessage(message) {
  const row = node("div", `message-row ${message.direction}${message.kind === "text" ? "" : " rich-message"}`);
  const conversation = state.conversations.find((item) => item.id === state.activeId);
  const avatar = message.direction === "outbound"
    ? node("span", "message-avatar", "我")
    : avatarNode(conversation, "message-avatar customer-avatar");
  const wrap = node("div", "bubble-wrap");
  const bubble = node("div", `bubble ${message.kind}`);
  if (message.kind === "text") bubble.textContent = message.content || "";
  else if (message.content) bubble.append(node("div", "rich-caption", message.kind === "goods_card" ? "商品卡片" : (message.content || "")));
  for (const asset of (message.assets || [])) {
    if (!asset.id) continue;
    const image = document.createElement("img");
    image.className = `message-asset ${asset.asset_type}`;
    image.src = `/api/v1/message-assets/${encodeURIComponent(asset.id)}`;
    image.alt = asset.metadata?.alt || "聊天图片";
    image.loading = "lazy";
    image.addEventListener("error", () => image.remove());
    bubble.append(image);
  }
  if (message.kind === "unsupported" && !message.assets?.length) bubble.append(node("div", "rich-caption muted", "暂不支持的消息"));
  wrap.append(bubble, node("div", "message-meta", `${fmtTime(message.occurred_at)}${message.is_backfill ? " · 历史同步" : ""}`));
  row.append(avatar, wrap);
  return row;
}

function renderDetail(data) {
  const c = data.conversation;
  els.header.className = "chat-head";
  els.header.replaceChildren();
  const info = node("div", "chat-identity");
  info.append(avatarNode(c, "header-avatar"));
  const identityText = node("div");
  identityText.append(node("h2", "", c.display_name), node("p", "", c.goods_name || "暂未关联商品卡片"));
  info.append(identityText);
  els.header.append(info, node("span", "state", receptionLabel(c)));
  els.messages.replaceChildren(...data.messages.map(renderMessage));
  els.messages.scrollTop = els.messages.scrollHeight;
  els.form.classList.remove("hidden");
  syncConversationPermission(c);
  if (c.goods_id || c.goods_name) {
    els.goods.className = "goods-context";
    els.goods.replaceChildren(node("strong", "", c.goods_name || "商品卡片"), node("span", "", `商品 ID：${c.goods_id || "未识别"}${c.goods_price ? ` · 价格：${c.goods_price}` : ""}`));
    if (c.goods_url) {
      const link = document.createElement("a"); link.href = c.goods_url; link.target = "_blank"; link.rel = "noreferrer"; link.textContent = "打开公开商品链接"; els.goods.append(link);
    }
    els.goodsTag.textContent = "已关联";
  } else {
    els.goods.className = "goods-empty";
    els.goods.textContent = "当前会话尚未识别到商品卡片";
    els.goodsTag.textContent = "无上下文";
  }
  renderDecision(data.decision);
}

function renderDecision(decision) {
  els.decision.replaceChildren();
  els.useSuggestion.classList.add("hidden");
  if (!decision) {
    els.decision.className = "decision-empty";
    els.decision.textContent = "尚无回复决策，收到顾客文本后会生成建议。";
    els.decisionRoute.textContent = "等待消息";
    return;
  }
  els.decision.className = "";
  els.decisionRoute.textContent = decision.action === "auto_send" ? "自动发送" : "人工建议";
  if (decision.suggested_answer) {
    els.decision.append(node("div", "decision-answer", decision.suggested_answer));
    els.useSuggestion.classList.remove("hidden");
    els.useSuggestion.onclick = () => {
      els.input.value = decision.suggested_answer.slice(0, 400);
      els.input.dispatchEvent(new Event("input"));
      els.input.focus();
    };
  }
  if (decision.risk_reason) els.decision.append(node("div", "decision-reason", `转人工原因：${decision.risk_reason}`));
  const metrics = [decision.qa_code && `QA ${decision.qa_code}`, decision.top_score != null && `分数 ${decision.top_score.toFixed(3)}`, decision.score_margin != null && `差值 ${decision.score_margin.toFixed(3)}`].filter(Boolean).join(" · ");
  if (metrics) els.decision.append(node("div", "decision-metrics", metrics));
}

async function openConversation(id) {
  state.activeId = id;
  const request = ++state.detailRequest;
  renderConversations();
  try {
    const detail = await api(`/conversations/${id}/messages?limit=200`);
    if (state.activeId !== id || request !== state.detailRequest) return;
    renderDetail(detail);
    if (detail.conversation.unread_count > 0) {
      const updated = await api(`/conversations/${id}/read`, { method: "POST" });
      const current = state.conversations.find((item) => item.id === id);
      if (current) Object.assign(current, updated);
      renderConversations();
    }
  } catch (error) { toast(error.message, true); }
}

function renderConnector(data) {
  state.connector = data.status;
  els.connectorPill.className = `status-pill ${data.status}`;
  const label = ({ stopped: "连接器已停止", starting: "正在启动", login_required: "等待扫码登录", ready: "消息监听正常", degraded: "页面结构异常", error: "连接器错误" })[data.status];
  els.connectorPill.querySelector("span").textContent = data.detail || label;
  els.connectorButton.textContent = data.status === "degraded" ? "重新连接" : (["ready", "starting", "login_required"].includes(data.status) ? "停止连接" : "打开登录窗口");
  els.send.disabled = data.status !== "ready";
  els.hint.textContent = data.status === "ready" ? "" : "连接器就绪后才能发送";
  const active = state.conversations.find((item) => item.id === state.activeId);
  syncConversationPermission(active);
  syncGlobalPermission();
  renderConversations();
}

async function loadStatus() {
  try { renderConnector(await api("/connector/status")); }
  catch (error) { toast(error.message, true); }
  try {
    state.globalAutomation = (await api("/automation")).enabled;
    els.globalAuto.checked = state.globalAutomation;
  } catch {
    state.globalAutomation = false;
    els.globalAuto.checked = false;
  }
  syncGlobalPermission();
  renderConversations();
}

els.connectorButton.addEventListener("click", async () => {
  els.connectorButton.disabled = true;
  try {
    const action = ["ready", "starting", "login_required"].includes(state.connector) ? "stop" : "start";
    renderConnector(await api(`/connector/${action}`, { method: "POST" }));
  } catch (error) { toast(error.message, true); }
  finally { els.connectorButton.disabled = false; }
});

els.globalAuto.addEventListener("change", async () => {
  const enabled = els.globalAuto.checked;
  try {
    const result = await api("/automation", { method: "PUT", body: JSON.stringify({ enabled }) });
    state.globalAutomation = result.enabled;
    els.globalAuto.checked = result.enabled;
    syncGlobalPermission();
    renderConversations();
    const active = state.conversations.find((item) => item.id === state.activeId);
    syncConversationPermission(active);
    toast(result.enabled ? "全局自动接待已开启" : "全局自动接待已暂停");
  } catch (error) { els.globalAuto.checked = !enabled; toast(error.message, true); }
});

els.convAuto.addEventListener("change", async () => {
  if (!state.activeId) return;
  const enabled = els.convAuto.checked;
  try {
    await api(`/conversations/${state.activeId}/automation`, { method: "PUT", body: JSON.stringify({ enabled }) });
    toast(enabled ? "该会话已恢复自动接待" : "该会话已由人工接待");
    await loadConversations(); await openConversation(state.activeId);
  } catch (error) { els.convAuto.checked = !enabled; toast(error.message, true); }
});

els.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const content = els.input.value.trim();
  if (!state.activeId || !content) return;
  els.send.disabled = true; els.hint.textContent = "正在提交发送任务…";
  try {
    const requestId = `manual:${crypto.randomUUID()}`;
    const job = await api(`/conversations/${state.activeId}/reply`, { method: "POST", body: JSON.stringify({ content, client_request_id: requestId }) });
    els.input.value = ""; els.input.dispatchEvent(new Event("input"));
    toast(job.status === "sent" ? "回复已发送" : "回复已进入发送队列");
    await loadConversations(); await openConversation(state.activeId);
  } catch (error) { toast(error.message, true); }
  finally { els.send.disabled = state.connector !== "ready"; els.hint.textContent = ""; }
});
els.input.addEventListener("input", () => { els.charCount.textContent = `${els.input.value.length} / 400`; });
els.input.addEventListener("keydown", (event) => { if (event.ctrlKey && event.key === "Enter") els.form.requestSubmit(); });
$("refreshButton").addEventListener("click", loadConversations);

let searchTimer;
$("searchInput").addEventListener("input", (event) => {
  clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.search = event.target.value.trim(); loadConversations(); }, 250);
});
document.querySelectorAll(".filters button").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll(".filters button").forEach((item) => item.classList.remove("active"));
  button.classList.add("active"); state.filter = button.dataset.state; loadConversations();
}));

const eventSource = new EventSource("/api/v1/events");
for (const type of ["conversation.upserted", "message.created", "outbound.updated", "reply.decision"]) {
  eventSource.addEventListener(type, async () => { await loadConversations(); if (state.activeId) await openConversation(state.activeId); });
}
eventSource.addEventListener("connector.status", (event) => { try { renderConnector(JSON.parse(event.data).data); } catch {} });
eventSource.addEventListener("automation.updated", (event) => {
  try {
    state.globalAutomation = JSON.parse(event.data).data.enabled;
    els.globalAuto.checked = state.globalAutomation;
    syncGlobalPermission();
    renderConversations();
    syncConversationPermission(state.conversations.find((item) => item.id === state.activeId));
  } catch {}
});
eventSource.onerror = () => { els.connectorPill.querySelector("span").textContent = "实时通道重连中"; };

// SSE 断线重连期间仍定时校准，避免消息已经入库但当前聊天没有刷新。
setInterval(async () => {
  if (document.hidden) return;
  await loadConversations();
  if (state.activeId) await openConversation(state.activeId);
}, 3000);

await Promise.all([loadConversations(), loadStatus()]);
