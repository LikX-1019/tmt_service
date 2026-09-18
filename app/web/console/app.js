const state = {
  conversations: [], activeId: null, filter: "", search: "",
  connector: "stopped", globalAutomation: false, detailRequest: 0,
  conversationSignature: "", contextMenuId: null, shops: [], shopId: null,
  eventSource: null, requestController: new AbortController(), noteCustomerId: null
};
const $ = (id) => document.getElementById(id);
const els = {
  list: $("conversationList"), count: $("conversationCount"), header: $("chatHeader"),
  messages: $("messageList"), form: $("replyForm"), input: $("replyInput"), send: $("sendButton"),
  hint: $("sendHint"), charCount: $("charCount"), connectorPill: $("connectorPill"),
  connectorButton: $("connectorButton"), globalAuto: $("globalAutomation"), convAuto: $("conversationAutomation"),
  goods: $("goodsContext"), goodsTag: $("goodsTag"), decision: $("decisionContent"),
  decisionRoute: $("decisionRoute"), useSuggestion: $("useSuggestion"), toast: $("toast"),
  greetingForm: $("greetingForm"), greetingEnabled: $("greetingEnabled"),
  greetingGroups: $("greetingGroups"), greetingUpdatedAt: $("greetingUpdatedAt"),
    greetingReset: $("greetingReset"), greetingSave: $("greetingSave"),
    handoffForm: $("handoffForm"), handoffReply: $("handoffReply"),
    handoffUpdatedAt: $("handoffUpdatedAt"), handoffSave: $("handoffSave"),
    contextMenu: $("conversationContextMenu"), contextMenuName: $("contextMenuName"),
    contextMenuTime: $("contextMenuTime"), contextMenuStatus: $("contextMenuStatus"),
    contextMenuManual: $("contextMenuManual"), contextMenuAuto: $("contextMenuAuto"),
    contextMenuClearTimer: $("contextMenuClearTimer"),
    overview: $("shopOverview"), workspace: $("shopWorkspace"),
    workspaceActions: $("workspaceActions"), shopList: $("shopList"),
    overviewMetrics: $("overviewMetrics"), addShop: $("addShopButton"),
    overviewButton: $("overviewButton"), shopSwitcher: $("shopSwitcher"),
    focusButton: $("focusButton"), receptionMode: $("receptionMode"),
    brandContext: $("brandContext"), customerNoteForm: $("customerNoteForm"),
    customerNote: $("customerNote"), customerTags: $("customerTags"),
    customerNoteSave: $("customerNoteSave"), noteUpdatedAt: $("noteUpdatedAt"),
    knowledgeGapList: $("knowledgeGapList"), gapCount: $("gapCount")
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
const DEFAULT_HANDOFF = {
  reply_template: "您好，您反馈的售后问题已为您转接人工客服处理，请您稍候。"
};

async function api(path, options = {}) {
  const scoped = options.scope !== false;
  const requestOptions = { ...options };
  delete requestOptions.scope;
  const scopedPath = scoped && state.shopId && !path.startsWith("/shops")
    ? `/shops/${encodeURIComponent(state.shopId)}${path}`
    : path;
  const response = await fetch(`/api/v1${scopedPath}`, {
    headers: { "Content-Type": "application/json", ...(requestOptions.headers || {}) },
    signal: requestOptions.signal || state.requestController.signal,
    ...requestOptions
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

function currentTimeText() {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false
  }).format(new Date());
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
    button.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      showConversationContextMenu(item, event.clientX, event.clientY);
    });
    els.list.append(button);
  }
}

function hideConversationContextMenu() {
  state.contextMenuId = null;
  els.contextMenu.classList.add("hidden");
}

function showConversationContextMenu(conversation, clientX, clientY) {
  state.contextMenuId = conversation.id;
  els.contextMenuName.textContent = conversation.display_name || "顾客";
  els.contextMenuTime.textContent = `当前时间 ${currentTimeText()}`;
  els.contextMenuStatus.textContent = `${statusLabel(conversation.state)} · ${receptionLabel(conversation)}`;
  const canEnable = state.globalAutomation && state.connector === "ready";
  els.contextMenuManual.disabled = !conversation.auto_reply_enabled;
  els.contextMenuAuto.disabled = conversation.auto_reply_enabled || !canEnable;
  els.contextMenuAuto.title = canEnable ? "" : "请先连接并开启全局自动接待";
  els.contextMenuClearTimer.disabled = !conversation.response_deadline_at;
  els.contextMenuClearTimer.title = conversation.response_deadline_at ? "" : "当前会话没有响应计时";
  els.contextMenu.classList.remove("hidden");
  const rect = els.contextMenu.getBoundingClientRect();
  const left = Math.min(clientX, window.innerWidth - rect.width - 8);
  const top = Math.min(clientY, window.innerHeight - rect.height - 8);
  els.contextMenu.style.left = `${Math.max(8, left)}px`;
  els.contextMenu.style.top = `${Math.max(8, top)}px`;
}

async function updateConversationReception(conversationId, enabled) {
  try {
    await api(`/conversations/${conversationId}/automation`, {
      method: "PUT", body: JSON.stringify({ enabled })
    });
    toast(enabled ? "该会话已恢复自动接待" : "该会话已转人工接待");
    await loadConversations();
    if (state.activeId === conversationId) await openConversation(conversationId);
  } catch (error) {
    toast(error.message, true);
  } finally {
    hideConversationContextMenu();
  }
}

async function clearConversationResponseTimer(conversationId) {
  try {
    await api(`/conversations/${conversationId}/response-timer`, { method: "DELETE" });
    toast("该会话的响应计时已清除");
    await loadConversations();
    if (state.activeId === conversationId) await openConversation(conversationId);
  } catch (error) {
    toast(error.message, true);
  } finally {
    hideConversationContextMenu();
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

function renderHandoffSettings(config) {
  const current = config || DEFAULT_HANDOFF;
  els.handoffReply.value = current.reply_template || DEFAULT_HANDOFF.reply_template;
  els.handoffUpdatedAt.textContent = current.updated_at
    ? `已更新 ${fmtTime(current.updated_at)}`
    : "默认话术";
}

async function loadHandoffSettings() {
  try {
    renderHandoffSettings(await api("/automation/handoff"));
  } catch (error) {
    renderHandoffSettings(DEFAULT_HANDOFF);
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
    image.src = `/api/v1/shops/${encodeURIComponent(state.shopId)}/message-assets/${encodeURIComponent(asset.id)}`;
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
  if (state.noteCustomerId !== c.customer_id) loadCustomerNote(c.customer_id);
}

async function loadCustomerNote(customerId) {
  state.noteCustomerId = customerId || null;
  els.customerNoteForm.dataset.customerId = customerId || "";
  els.customerNote.disabled = !customerId;
  els.customerTags.disabled = !customerId;
  els.customerNoteSave.disabled = !customerId;
  if (!customerId) {
    els.customerNote.value = ""; els.customerTags.value = "";
    els.noteUpdatedAt.textContent = "未识别顾客";
    return;
  }
  try {
    const note = await api(`/customers/${encodeURIComponent(customerId)}/note`);
    if (els.customerNoteForm.dataset.customerId !== customerId) return;
    els.customerNote.value = note?.content || "";
    els.customerTags.value = (note?.tags || []).join("，");
    els.noteUpdatedAt.textContent = note?.updated_at ? `已更新 ${fmtTime(note.updated_at)}` : "暂无备注";
  } catch (error) { toast(error.message, true); }
}

async function loadKnowledgeGaps() {
  if (!state.shopId) return;
  try {
    const data = await api("/knowledge-gaps?status=open&limit=20");
    els.gapCount.textContent = `${data.items.length} 条`;
    els.knowledgeGapList.replaceChildren();
    if (!data.items.length) {
      els.knowledgeGapList.append(node("div", "decision-empty", "当前店铺暂无待处理缺口"));
      return;
    }
    for (const gap of data.items) {
      const item = node("div", "knowledge-gap-item");
      item.append(
        node("strong", "", gap.example_question),
        node("span", "", `${gap.reason_code} · 出现 ${gap.occurrences} 次`)
      );
      const answer = document.createElement("textarea");
      answer.className = "gap-answer";
      answer.maxLength = 4000;
      answer.placeholder = "填写审核用标准答案";
      const actions = node("div", "gap-actions");
      const createDraft = node("button", "gap-draft", "转为 QA 草稿");
      createDraft.type = "button";
      createDraft.addEventListener("click", async () => {
        const standardAnswer = answer.value.trim();
        if (!standardAnswer) {
          toast("请先填写标准答案", true);
          answer.focus();
          return;
        }
        createDraft.disabled = true;
        try {
          await api(`/knowledge-gaps/${encodeURIComponent(gap.id)}/qa-draft`, {
            method: "POST", body: JSON.stringify({ standard_answer: standardAnswer })
          });
          toast("QA 草稿已创建，需审核后才能启用");
          await loadKnowledgeGaps();
        } catch (error) {
          toast(error.message, true);
          createDraft.disabled = false;
        }
      });
      const dismiss = node("button", "", "标记已处理");
      dismiss.type = "button";
      dismiss.addEventListener("click", async () => {
        await api(`/knowledge-gaps/${encodeURIComponent(gap.id)}`, {
          method: "PATCH", body: JSON.stringify({ status: "resolved" })
        });
        await loadKnowledgeGaps();
      });
      actions.append(createDraft, dismiss);
      item.append(answer, actions);
      els.knowledgeGapList.append(item);
    }
  } catch (error) { toast(error.message, true); }
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
  els.decisionRoute.textContent = decision.route === "handoff"
    ? "已转人工"
    : decision.route === "product"
      ? (decision.action === "auto_send" ? "商品咨询 · 自动发送" : "商品咨询 · 人工建议")
    : decision.route === "greeting"
    ? (decision.action === "auto_send" ? "问候 · 自动发送" : "问候 · 人工建议")
    : (decision.action === "auto_send" ? "自动发送" : "人工建议");
  if (decision.suggested_answer) {
    els.decision.append(node("div", "decision-answer", decision.suggested_answer));
    if (decision.action === "suggest") els.useSuggestion.classList.remove("hidden");
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
    decision.product_id && `商品 ${decision.product_name || decision.product_id}`,
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

function maskedShopId(value) {
  if (!value) return "等待登录后识别";
  if (value.length <= 6) return `${value.slice(0, 2)}***`;
  return `${value.slice(0, 3)}***${value.slice(-3)}`;
}

function connectorLabel(status) {
  return ({
    stopped: "离线", starting: "正在启动", login_required: "等待登录",
    ready: "监听中", degraded: "监听异常", error: "连接错误"
  })[status] || "未知";
}

function renderOverview() {
  const enabled = state.shops.filter((shop) => shop.lifecycle_status !== "disabled");
  const totals = enabled.reduce((result, shop) => {
    result.pending += shop.counts?.pending || 0;
    result.overdue += shop.counts?.overdue || 0;
    result.gaps += shop.counts?.knowledge_gaps || 0;
    if (shop.connector?.status === "ready") result.online += 1;
    return result;
  }, { online: 0, pending: 0, overdue: 0, gaps: 0 });
  els.overviewMetrics.replaceChildren(
    ...[["在线店铺", totals.online], ["待处理", totals.pending], ["已超时", totals.overdue], ["知识缺口", totals.gaps]]
      .map(([label, value]) => {
        const metric = node("div", "metric");
        metric.append(node("span", "", label), node("strong", "", String(value)));
        return metric;
      })
  );
  els.shopList.replaceChildren();
  if (!state.shops.length) {
    els.shopList.append(node("div", "empty compact", "尚未添加店铺"));
    return;
  }
  for (const shop of state.shops) {
    const row = node("div", "shop-row");
    const identity = node("div", "shop-identity");
    identity.append(
      node("strong", "", shop.name),
      node("small", "", `拼多多 ID ${maskedShopId(shop.platform_shop_id)} · ${shop.reception_mode === "guarded_auto" ? "受控自动" : "人机协同"}`)
    );
    const status = node("div", `shop-status ${shop.connector?.status || "stopped"}`);
    status.append(node("i"), node("span", "", shop.lifecycle_status === "disabled" ? "已停用" : connectorLabel(shop.connector?.status)));
    const counts = node("div", "shop-counts");
    for (const [label, value] of [["待处理", shop.counts?.pending], ["即将超时", shop.counts?.due_soon], ["已超时", shop.counts?.overdue], ["知识缺口", shop.counts?.knowledge_gaps]]) {
      const item = node("div", "shop-count");
      item.append(node("b", "", String(value || 0)), node("span", "", label));
      counts.append(item);
    }
    const actions = node("div", "shop-actions");
    if (shop.lifecycle_status !== "disabled") {
      const enter = node("button", "button primary", "进入店铺");
      enter.addEventListener("click", () => enterShop(shop.id));
      const focus = node("button", "button secondary", "显示窗口");
      focus.disabled = shop.connector?.status === "stopped";
      focus.addEventListener("click", () => shopAction(shop.id, "connector/focus"));
      const online = shop.connector?.status !== "stopped";
      const toggle = node("button", "button secondary", online ? "下线" : "上线");
      toggle.addEventListener("click", () => shopAction(shop.id, `connector/${online ? "stop" : "start"}`));
      const disable = node("button", "button danger", "停用");
      disable.addEventListener("click", () => disableShop(shop));
      actions.append(enter, focus, toggle, disable);
    }
    row.append(identity, status, counts, actions);
    els.shopList.append(row);
  }
}

async function loadShops() {
  try {
    const data = await api("/shops", { scope: false, signal: undefined });
    state.shops = data.items;
    renderOverview();
    els.shopSwitcher.replaceChildren(...state.shops
      .filter((shop) => shop.lifecycle_status !== "disabled")
      .map((shop) => {
        const option = document.createElement("option");
        option.value = shop.id; option.textContent = shop.name;
        return option;
      }));
    if (state.shopId) els.shopSwitcher.value = state.shopId;
  } catch (error) {
    if (error.name !== "AbortError") els.shopList.replaceChildren(node("div", "empty compact", error.message));
  }
}

async function shopAction(shopId, action) {
  try {
    await api(`/shops/${encodeURIComponent(shopId)}/${action}`, { method: "POST", scope: false });
    await loadShops();
  } catch (error) { toast(error.message, true); }
}

async function disableShop(shop) {
  if (!window.confirm(`停用“${shop.name}”？历史数据和登录目录会保留。`)) return;
  try {
    await api(`/shops/${encodeURIComponent(shop.id)}`, {
      method: "PATCH", body: JSON.stringify({ enabled: false }), scope: false
    });
    if (state.shopId === shop.id) showOverview();
    await loadShops();
  } catch (error) { toast(error.message, true); }
}

function resetWorkspaceState() {
  state.requestController.abort();
  state.requestController = new AbortController();
  state.detailRequest += 1;
  state.activeId = null;
  state.noteCustomerId = null;
  state.conversations = [];
  state.conversationSignature = "";
  els.form.classList.add("hidden");
  els.header.className = "chat-head muted";
  els.header.innerHTML = "<div><h2>请选择一个会话</h2><p>顾客消息会通过独立浏览器同步到这里</p></div>";
  els.messages.replaceChildren(node("div", "empty", "还没有打开会话"));
  state.eventSource?.close();
  state.eventSource = null;
}

async function enterShop(shopId) {
  resetWorkspaceState();
  state.shopId = shopId;
  const shop = state.shops.find((item) => item.id === shopId);
  els.overview.classList.add("hidden");
  els.workspace.classList.remove("hidden");
  els.workspaceActions.classList.remove("hidden");
  els.brandContext.textContent = shop?.name || "店铺工作区";
  els.shopSwitcher.value = shopId;
  els.receptionMode.querySelectorAll("button").forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === shop?.reception_mode);
  });
  connectEvents();
  await Promise.all([loadConversations(), loadStatus(), loadGreetingSettings(), loadHandoffSettings(), loadKnowledgeGaps()]);
}

function showOverview() {
  resetWorkspaceState();
  state.shopId = null;
  els.workspace.classList.add("hidden");
  els.workspaceActions.classList.add("hidden");
  els.overview.classList.remove("hidden");
  els.brandContext.textContent = "多店铺本地版";
  loadShops();
}

function connectEvents() {
  state.eventSource?.close();
  if (!state.shopId) return;
  const source = new EventSource(`/api/v1/shops/${encodeURIComponent(state.shopId)}/events`);
  state.eventSource = source;
  for (const type of ["conversation.upserted", "message.created", "outbound.updated", "reply.decision"]) {
    source.addEventListener(type, async () => {
      await loadConversations();
      if (state.activeId) await openConversation(state.activeId);
    });
  }
  source.addEventListener("connector.status", (event) => { try { renderConnector(JSON.parse(event.data).data); } catch {} });
  source.addEventListener("knowledge_gap.updated", loadKnowledgeGaps);
  source.addEventListener("automation.updated", (event) => {
    try {
      state.globalAutomation = JSON.parse(event.data).data.enabled;
      els.globalAuto.checked = state.globalAutomation;
      syncGlobalPermission(); renderConversations();
    } catch {}
  });
  source.onerror = () => { els.connectorPill.querySelector("span").textContent = "实时通道重连中"; };
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
    await updateConversationReception(state.activeId, enabled);
  } finally {
    const active = state.conversations.find((item) => item.id === state.activeId);
    if (active) els.convAuto.checked = Boolean(active.auto_reply_enabled);
  }
});

els.contextMenuManual.addEventListener("click", () => {
  if (state.contextMenuId) updateConversationReception(state.contextMenuId, false);
});
els.contextMenuAuto.addEventListener("click", () => {
  if (state.contextMenuId) updateConversationReception(state.contextMenuId, true);
});
els.contextMenuClearTimer.addEventListener("click", () => {
  if (state.contextMenuId) clearConversationResponseTimer(state.contextMenuId);
});
document.addEventListener("click", (event) => {
  if (!els.contextMenu.contains(event.target)) hideConversationContextMenu();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") hideConversationContextMenu();
});
window.addEventListener("resize", hideConversationContextMenu);
els.list.addEventListener("scroll", hideConversationContextMenu, { passive: true });

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

els.handoffForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const reply_template = els.handoffReply.value.trim();
  if (!reply_template) { toast("转人工话术不能为空", true); return; }
  els.handoffSave.disabled = true;
  try {
    renderHandoffSettings(await api("/automation/handoff", {
      method: "PUT", body: JSON.stringify({ reply_template })
    }));
    toast("转人工话术已保存，下一条售后消息立即生效");
  } catch (error) {
    toast(error.message, true);
  } finally {
    els.handoffSave.disabled = false;
  }
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
els.customerNoteForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const customerId = els.customerNoteForm.dataset.customerId;
  if (!customerId) return;
  els.customerNoteSave.disabled = true;
  try {
    const note = await api(`/customers/${encodeURIComponent(customerId)}/note`, {
      method: "PUT",
      body: JSON.stringify({
        content: els.customerNote.value.trim(),
        tags: els.customerTags.value.split(/[,，]/).map((tag) => tag.trim()).filter(Boolean),
        pinned: false
      })
    });
    els.noteUpdatedAt.textContent = `已更新 ${fmtTime(note.updated_at)}`;
    toast("顾客备注已保存");
  } catch (error) { toast(error.message, true); }
  finally { els.customerNoteSave.disabled = false; }
});
els.input.addEventListener("input", () => { els.charCount.textContent = `${els.input.value.length} / 400`; });
els.input.addEventListener("keydown", (event) => { if (event.ctrlKey && event.key === "Enter") els.form.requestSubmit(); });
$("refreshButton").addEventListener("click", loadConversations);
els.addShop.addEventListener("click", async () => {
  els.addShop.disabled = true;
  try {
    const shop = await api("/shops/provision", { method: "POST", scope: false });
    await loadShops();
    toast("已打开独立拼多多窗口，请完成登录");
    await enterShop(shop.id);
  } catch (error) { toast(error.message, true); }
  finally { els.addShop.disabled = false; }
});
els.overviewButton.addEventListener("click", showOverview);
els.shopSwitcher.addEventListener("change", () => enterShop(els.shopSwitcher.value));
els.focusButton.addEventListener("click", async () => {
  if (state.shopId) await shopAction(state.shopId, "connector/focus");
});
els.receptionMode.querySelectorAll("button").forEach((button) => {
  button.addEventListener("click", async () => {
    if (!state.shopId) return;
    try {
      await api(`/shops/${encodeURIComponent(state.shopId)}`, {
        method: "PATCH", body: JSON.stringify({ reception_mode: button.dataset.mode }), scope: false
      });
      await loadShops();
      els.receptionMode.querySelectorAll("button").forEach((item) => item.classList.toggle("active", item === button));
      toast(button.dataset.mode === "guarded_auto" ? "已切换为受控自动" : "已切换为人机协同");
    } catch (error) { toast(error.message, true); }
  });
});

let searchTimer;
$("searchInput").addEventListener("input", (event) => {
  clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.search = event.target.value.trim(); loadConversations(); }, 250);
});
document.querySelectorAll(".filters button").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll(".filters button").forEach((item) => item.classList.remove("active"));
  button.classList.add("active"); state.filter = button.dataset.state; loadConversations();
}));

// SSE 断线重连期间仍定时校准，避免消息已经入库但当前聊天没有刷新。
setInterval(async () => {
  if (document.hidden || !state.shopId) return;
  await loadConversations();
  if (state.activeId) await openConversation(state.activeId);
}, 3000);

setInterval(updateResponseTimers, 1000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) updateResponseTimers();
});

await loadShops();
