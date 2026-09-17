const embedMode = new URLSearchParams(window.location.search).get("embed");
if (["shops", "console"].includes(embedMode)) {
  document.body.classList.add(`embed-${embedMode}`);
}

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
  decisionRoute: $("decisionRoute"), useSuggestion: $("useSuggestion"), toast: $("toast"),
  shopWorkspace: $("shopWorkspace"), shopName: $("shopName"), shopStatus: $("shopStatus"),
  shopPendingCount: $("shopPendingCount"), shopAvatar: $("shopAvatar"),
  greetingForm: $("greetingForm"), greetingEnabled: $("greetingEnabled"),
  greetingGroups: $("greetingGroups"), greetingUpdatedAt: $("greetingUpdatedAt"),
  greetingReset: $("greetingReset"), greetingSave: $("greetingSave")
};

const GREETING_META = {
  salutation: { label: "问候" },
  availability: { label: "询问在线" },
  thanks: { label: "致谢" },
  goodbye: { label: "告别" }
};
const DEFAULT_GREETING = {
  enabled: true,
  trigger_groups: {
    salutation: ["你好", "您好", "哈喽", "嗨"],
    availability: ["在吗", "有人吗", "客服在吗"],
    thanks: ["谢谢", "辛苦了", "感谢"],
    goodbye: ["再见", "拜拜", "先这样"]
  },
  reply_templates: {
    salutation: [
      "您好，亲，请问有什么可以帮您？",
      "您好呀，亲，有什么问题都可以告诉我。",
      "亲，您好，我在这里，请问需要了解什么呢？",
      "您好，欢迎咨询，请问有什么可以为您解答？",
      "您好呀，很高兴为您服务，请问您想咨询什么？"
    ],
    availability: [
      "您好，亲，我在的，请问有什么可以帮您？",
      "在的，亲，您有什么问题可以直接告诉我。",
      "您好，我在线的，请问您想咨询什么呢？",
      "亲，在呢，有什么需要我帮您看看的吗？",
      "我在的，您请说，我马上帮您处理。"
    ],
    thanks: [
      "不客气，亲，很高兴能帮到您。",
      "不用客气，这是我们应该做的。",
      "亲，不客气，能帮到您就好。",
      "感谢您的认可，有需要随时联系我们。",
      "不客气呀，祝您购物愉快！"
    ],
    goodbye: [
      "好的，亲，感谢您的咨询，祝您生活愉快！",
      "好的，有需要随时联系我们，祝您生活愉快！",
      "感谢您的咨询，祝您每天都有好心情！",
      "好的，亲，那就先不打扰您啦，祝您一切顺利！",
      "很高兴为您服务，期待下次再见！"
    ]
  }
};

if (embedMode === "shops") els.shopWorkspace.classList.remove("hidden");

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

function timestamp(value) {
  if (!value) return null;
  const normalized = /(?:Z|[+-]\d{2}:\d{2})$/.test(value) ? value : `${value}Z`;
  const parsed = new Date(normalized).getTime();
  return Number.isNaN(parsed) ? null : parsed;
}

function compareConversations(left, right) {
  const leftDeadline = timestamp(left.response_deadline_at);
  const rightDeadline = timestamp(right.response_deadline_at);
  if (leftDeadline != null && rightDeadline != null && leftDeadline !== rightDeadline) {
    return leftDeadline - rightDeadline;
  }
  if (leftDeadline != null) return -1;
  if (rightDeadline != null) return 1;
  const lastMessageDifference = (timestamp(right.last_message_at) || 0) - (timestamp(left.last_message_at) || 0);
  return lastMessageDifference || String(right.id).localeCompare(String(left.id));
}

function formatDuration(totalSeconds) {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  const parts = hours > 0 ? [hours, minutes, remainder] : [minutes, remainder];
  return parts.map((part) => String(part).padStart(2, "0")).join(":");
}

function responseTimerState(conversation, now = Date.now()) {
  const deadline = timestamp(conversation?.response_deadline_at);
  if (deadline == null) return null;
  const milliseconds = deadline - now;
  if (milliseconds > 0) {
    const seconds = Math.ceil(milliseconds / 1000);
    const level = seconds > 60 ? "normal" : (seconds > 30 ? "warning" : "danger");
    return {
      level,
      text: `剩余 ${formatDuration(seconds)}`,
      label: `回复剩余时间 ${formatDuration(seconds)}`,
    };
  }
  const seconds = Math.floor(Math.abs(milliseconds) / 1000);
  return {
    level: "overdue",
    text: `已超时 ${formatDuration(seconds)}`,
    label: `回复已超时 ${formatDuration(seconds)}`,
  };
}

function updateTimerNode(element, conversation, now = Date.now()) {
  const timer = responseTimerState(conversation, now);
  if (!timer) {
    element.remove();
    return;
  }
  element.className = `response-timer ${timer.level}${element.dataset.variant === "header" ? " header-timer" : ""}`;
  element.textContent = element.dataset.variant === "header" ? `回复倒计时 ${timer.text.replace("剩余 ", "")}` : timer.text;
  element.setAttribute("aria-label", timer.label);
}

function responseTimerNode(conversation, variant = "list") {
  if (!conversation?.response_deadline_at) return null;
  const element = node("span", "response-timer");
  element.dataset.responseTimer = conversation.id;
  element.dataset.variant = variant;
  element.setAttribute("role", "timer");
  updateTimerNode(element, conversation);
  return element;
}

function updateResponseTimers() {
  const now = Date.now();
  const byId = new Map(state.conversations.map((item) => [item.id, item]));
  document.querySelectorAll("[data-response-timer]").forEach((element) => {
    const conversation = byId.get(element.dataset.responseTimer);
    if (conversation) updateTimerNode(element, conversation, now);
  });

  state.conversations.sort(compareConversations);
  const buttons = new Map(
    [...els.list.querySelectorAll(".conversation[data-conversation-id]")]
      .map((element) => [element.dataset.conversationId, element])
  );
  for (const conversation of state.conversations) {
    const button = buttons.get(conversation.id);
    if (button) els.list.append(button);
  }
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
  state.conversations.sort(compareConversations);
  els.list.replaceChildren();
  els.count.textContent = `${state.conversations.length} 个会话`;
  if (!state.conversations.length) {
    els.list.append(node("div", "empty compact", "暂无符合条件的会话"));
    return;
  }
  for (const item of state.conversations) {
    const button = node("button", `conversation${item.id === state.activeId ? " active" : ""}`);
    button.type = "button";
    button.dataset.conversationId = item.id;
    const avatar = avatarNode(item);
    const main = node("span", "conversation-main");
    const title = node("span", "conversation-name");
    title.append(node("strong", "", item.display_name || "顾客"), node("time", "", fmtTime(item.last_message_at)));
    const outboundState = ({ failed: "发送失败", uncertain: "发送结果待核实", sending: "正在发送", queued: "等待发送" })[item.last_outbound_status];
    const preview = node("span", "conversation-preview", outboundState || item.goods_name || receptionLabel(item));
    main.append(title, preview);
    const meta = node("span", "conversation-meta");
    const timer = responseTimerNode(item);
    if (timer) meta.append(timer);
    if (item.unread_count) meta.append(node("span", "badge", String(item.unread_count)));
    button.append(avatar, main, meta);
    button.addEventListener("click", () => openConversation(item.id));
    els.list.append(button);
  }
  renderShopWorkspace();
}

function renderShopWorkspace() {
  if (!els.shopWorkspace) return;
  const waiting = state.conversations.filter((item) => item.response_deadline_at).length;
  els.shopPendingCount.textContent = String(waiting);
  els.shopPendingCount.classList.toggle("hidden", waiting === 0);
  const labels = {
    ready: "消息监听正常",
    starting: "正在启动",
    login_required: "等待登录",
    degraded: "监听异常",
    error: "连接失败",
    stopped: "未连接",
  };
  els.shopStatus.textContent = labels[state.connector] || "状态未知";
}

async function loadShop() {
  if (embedMode !== "shops") return;
  try {
    const shop = await api("/shop");
    els.shopName.textContent = shop.name;
    els.shopAvatar.textContent = (shop.name || "拼").slice(0, 1);
  } catch (error) {
    els.shopStatus.textContent = error.message;
  }
}

function renderGreetingSettings(config) {
  const current = config || DEFAULT_GREETING;
  els.greetingEnabled.checked = Boolean(current.enabled);
  els.greetingGroups.replaceChildren();
  for (const [type, meta] of Object.entries(GREETING_META)) {
    const field = node("div", "greeting-field");
    const heading = node("div", "greeting-field-head");
    heading.append(
      node("strong", "", meta.label),
      node("small", "", "每行一个触发语，最多 50 条")
    );
    const triggers = document.createElement("textarea");
    triggers.className = "greeting-triggers";
    triggers.dataset.type = type;
    triggers.maxLength = 2100;
    triggers.value = (current.trigger_groups?.[type] || []).join("\n");
    const tags = node("div", "greeting-tags");
    const label = node("label", "greeting-reply-line");
    label.append(node("span", "", "回复话术（每行一个，最多 10 条；系统按顾客随机分配）"));
    const reply = document.createElement("textarea");
    reply.className = "greeting-reply";
    reply.dataset.type = type;
    reply.maxLength = 4000;
    const rawReplies = current.reply_templates?.[type] || [];
    reply.value = (Array.isArray(rawReplies) ? rawReplies : [rawReplies]).join("\n");
    label.append(reply);
    field.append(heading, triggers, tags, label);
    els.greetingGroups.append(field);
    const renderTags = () => {
      tags.replaceChildren(
        ...triggers.value
          .split(/\r?\n/)
          .map((item) => item.trim())
          .filter(Boolean)
          .slice(0, 50)
          .map((item) => node("span", "greeting-tag", item))
      );
    };
    triggers.addEventListener("input", renderTags);
    renderTags();
  }
  els.greetingUpdatedAt.textContent = current.updated_at
    ? `已更新 ${fmtTime(current.updated_at)}`
    : "默认配置";
}

function readGreetingSettings() {
  const trigger_groups = {};
  const reply_templates = {};
  for (const type of Object.keys(GREETING_META)) {
    const triggerNode = els.greetingGroups.querySelector(`.greeting-triggers[data-type="${type}"]`);
    const replyNode = els.greetingGroups.querySelector(`.greeting-reply[data-type="${type}"]`);
    trigger_groups[type] = (triggerNode?.value || "")
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean);
    reply_templates[type] = (replyNode?.value || "")
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean);
  }
  return { enabled: els.greetingEnabled.checked, trigger_groups, reply_templates };
}

async function loadGreetingSettings() {
  try {
    renderGreetingSettings(await api("/automation/greeting"));
  } catch (error) {
    renderGreetingSettings(DEFAULT_GREETING);
    toast(error.message, true);
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
  const current = state.conversations.find((item) => item.id === c.id);
  if (current) Object.assign(current, c);
  els.header.className = "chat-head";
  els.header.replaceChildren();
  const info = node("div", "chat-identity");
  const back = node("button", "embed-back", "‹");
  back.type = "button";
  back.setAttribute("aria-label", "返回会话列表");
  back.addEventListener("click", closeEmbeddedConversation);
  info.append(back);
  info.append(avatarNode(c, "header-avatar"));
  const identityText = node("div");
  identityText.append(node("h2", "", c.display_name), node("p", "", c.goods_name || "暂未关联商品卡片"));
  info.append(identityText);
  const status = node("div", "chat-head-status");
  const timer = responseTimerNode(c, "header");
  if (timer) status.append(timer);
  status.append(node("span", "state", receptionLabel(c)));
  els.header.append(info, status);
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

function closeEmbeddedConversation() {
  state.activeId = null;
  state.detailRequest += 1;
  els.header.className = "chat-head muted";
  const copy = node("div");
  copy.append(
    node("h2", "", "请选择一个会话"),
    node("p", "", "从会话列表进入中台辅助对话框")
  );
  els.header.replaceChildren(copy);
  els.messages.replaceChildren(
    node("div", "empty compact", "选择会话后查看聊天记录")
  );
  els.form.classList.add("hidden");
  renderConversations();
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
  els.decisionRoute.textContent = decision.route === "greeting"
    ? (decision.action === "auto_send" ? "问候 · 自动发送" : "问候 · 人工建议")
    : (decision.action === "auto_send" ? "自动发送" : "人工建议");
  if (decision.suggested_answer) {
    els.decision.append(node("div", "decision-answer", decision.suggested_answer));
    els.useSuggestion.classList.remove("hidden");
    els.useSuggestion.onclick = () => {
      els.input.value = decision.suggested_answer.slice(0, 400);
      els.input.dispatchEvent(new Event("input"));
      els.input.focus();
    };
  }
  if (decision.risk_reason) {
    const reasonLabel = decision.route === "greeting" ? "识别与发送说明" : "转人工原因";
    els.decision.append(node("div", "decision-reason", `${reasonLabel}：${decision.risk_reason}`));
  }
  const greetingLabel = GREETING_META[decision.greeting_type]?.label;
  const recognitionLabel = decision.recognition_source === "rule"
    ? "规则识别"
    : (decision.recognition_source === "llm" ? "模型兜底" : "");
  const metrics = [
    decision.qa_code && `QA ${decision.qa_code}`,
    greetingLabel && `类型 ${greetingLabel}`,
    recognitionLabel,
    decision.top_score != null && `分数 ${decision.top_score.toFixed(3)}`,
    decision.score_margin != null && `差值 ${decision.score_margin.toFixed(3)}`
  ].filter(Boolean).join(" · ");
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
  renderShopWorkspace();
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

els.greetingForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = readGreetingSettings();
  els.greetingSave.disabled = true;
  try {
    const result = await api("/automation/greeting", { method: "PUT", body: JSON.stringify(payload) });
    renderGreetingSettings(result);
    toast("问候话术已保存，下一条消息立即生效");
  } catch (error) {
    toast(error.message, true);
  } finally {
    els.greetingSave.disabled = false;
  }
});

els.greetingReset.addEventListener("click", () => {
  if (!window.confirm("恢复默认问候配置会覆盖当前触发语和回复话术，继续吗？")) return;
  renderGreetingSettings(DEFAULT_GREETING);
  toast("已填入默认配置，请点击保存后生效");
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

setInterval(updateResponseTimers, 1000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) updateResponseTimers();
});

await Promise.all([loadConversations(), loadStatus(), loadShop(), loadGreetingSettings()]);
