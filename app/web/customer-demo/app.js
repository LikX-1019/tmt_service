"use strict";

const WELCOME_MESSAGE = "您好，我是小满客服，请问有什么可以帮助您？";
const SERVICE_STAGE_LABELS = {
  pre_sale: "售前",
  post_sale: "售后",
  general: "通用",
};

const dom = {
  sessionId: document.querySelector("#sessionId"),
  customerId: document.querySelector("#customerId"),
  newSessionButton: document.querySelector("#newSessionButton"),
  resetSessionButton: document.querySelector("#resetSessionButton"),
  sessionList: document.querySelector("#sessionList"),
  sessionCount: document.querySelector("#sessionCount"),
  productForm: document.querySelector("#productForm"),
  productId: document.querySelector("#productId"),
  legacyProductCode: document.querySelector("#legacyProductCode"),
  stageInputs: Array.from(document.querySelectorAll('input[name="serviceStage"]')),
  transportMode: document.querySelector("#transportMode"),
  currentProductChip: document.querySelector("#currentProductChip"),
  currentStageChip: document.querySelector("#currentStageChip"),
  messageForm: document.querySelector("#messageForm"),
  messageInput: document.querySelector("#messageInput"),
  messageList: document.querySelector("#messageList"),
  sendButton: document.querySelector("#sendButton"),
  sendState: document.querySelector("#sendState"),
  debugSessionId: document.querySelector("#debugSessionId"),
  debugCustomerId: document.querySelector("#debugCustomerId"),
  debugInputMessageId: document.querySelector("#debugInputMessageId"),
  debugOutputMessageId: document.querySelector("#debugOutputMessageId"),
  debugProductId: document.querySelector("#debugProductId"),
  debugProductResolution: document.querySelector("#debugProductResolution"),
  debugServiceStage: document.querySelector("#debugServiceStage"),
  debugTransportMode: document.querySelector("#debugTransportMode"),
  debugRoute: document.querySelector("#debugRoute"),
  debugSource: document.querySelector("#debugSource"),
  debugRule: document.querySelector("#debugRule"),
  debugConfidence: document.querySelector("#debugConfidence"),
  debugRequestId: document.querySelector("#debugRequestId"),
  debugLatency: document.querySelector("#debugLatency"),
  sourceCount: document.querySelector("#sourceCount"),
  sourceList: document.querySelector("#sourceList"),
  panelScrim: document.querySelector("#panelScrim"),
  panelButtons: Array.from(document.querySelectorAll("[data-panel]")),
};

const state = {
  sessionId: "",
  customerId: "",
  messages: [],
  isSending: false,
  transportMode: "chat",
  transport: null,
  boundProductId: null,
  debug: {},
  sessions: new Map(),
};

class TransportError extends Error {
  constructor(message, options = {}) {
    super(message);
    this.name = "TransportError";
    this.status = options.status ?? null;
    this.requestId = options.requestId ?? null;
    this.latencyMs = options.latencyMs ?? null;
  }
}

class ChatTransport {
  get mode() {
    return "abstract";
  }

  async send() {
    throw new Error("ChatTransport must be implemented by a concrete adapter.");
  }

  async postJson(endpoint, payload) {
    const startedAt = performance.now();
    let response;
    try {
      response = await fetch(endpoint, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify(payload),
      });
    } catch (error) {
      const latencyMs = Math.round(performance.now() - startedAt);
      throw new TransportError("网络请求失败，请检查本地服务。", { latencyMs });
    }

    let body = null;
    try {
      body = await response.json();
    } catch (_) {
      body = null;
    }

    const latencyMs = Math.round(performance.now() - startedAt);
    const requestId = response.headers.get("X-Request-Id");

    if (!response.ok) {
      const detail = body?.detail ?? body?.message ?? body?.data?.message;
      const message = typeof detail === "string" && detail.trim()
        ? detail
        : `请求失败（HTTP ${response.status}）`;
      throw new TransportError(message, { status: response.status, requestId, latencyMs });
    }

    return { body, requestId, latencyMs };
  }
}

class CustomerChatTransport extends ChatTransport {
  constructor() {
    super();
    this.endpoint = "/api/v1/chat";
  }

  get mode() {
    return "chat";
  }

  async send(message) {
    const payload = {
      conversation_id: message.session_id,
      customer_id: message.customer_id,
      message: message.content,
      product_id: message.product_id || null,
      service_stage: message.service_stage,
    };
    const { body, requestId, latencyMs } = await this.postJson(this.endpoint, payload);
    if (body && typeof body === "object" && body.code !== undefined && body.code !== 0) {
      const messageText = typeof body.message === "string" ? body.message : "Chat 服务返回业务错误。";
      throw new TransportError(messageText, { requestId, latencyMs });
    }

    const data = body?.data ?? body;
    if (!data || typeof data.answer !== "string" || data.answer.trim() === "") {
      throw new TransportError("Chat 服务没有返回有效答案。", { requestId, latencyMs });
    }

    return {
      answer: data.answer,
      source: typeof data.source === "string" ? data.source : null,
      route: typeof data.route === "string" ? data.route : null,
      product: data.product && typeof data.product.id === "string" ? data.product : null,
      productResolution: typeof data.product_resolution === "string" ? data.product_resolution : null,
      ruleName: typeof data.rule_name === "string" ? data.rule_name : null,
      confidence: normalizeNumber(data.confidence),
      sources: normalizeSources(data.sources),
      requestId: requestId ?? (typeof data.request_id === "string" ? data.request_id : null),
      latencyMs,
    };
  }
}

class QACompatibilityTransport extends ChatTransport {
  constructor(options = {}) {
    super();
    this.endpoint = "/api/v1/qa";
    this.getLegacyProductCode = options.getLegacyProductCode ?? (() => "");
  }

  get mode() {
    return "qa-compatibility";
  }

  async send(message) {
    const payload = {
      query: message.content,
    };
    if (message.service_stage) payload.service_stage = message.service_stage;

    const legacyProductCode = this.getLegacyProductCode();
    if (legacyProductCode) payload.product_code = legacyProductCode;

    const { body, requestId, latencyMs } = await this.postJson(this.endpoint, payload);
    if (body && typeof body === "object" && body.code !== undefined && body.code !== 0) {
      const messageText = typeof body.message === "string" ? body.message : "QA 服务返回业务错误。";
      throw new TransportError(messageText, { requestId, latencyMs });
    }

    const data = body?.data ?? body;
    if (!data || typeof data.answer !== "string" || data.answer.trim() === "") {
      throw new TransportError("QA 服务没有返回有效答案。", { requestId, latencyMs });
    }

    return {
      answer: data.answer,
      source: "qa",
      route: typeof data.route === "string" ? data.route : null,
      product: null,
      productResolution: message.product_id ? "request" : "none",
      ruleName: null,
      confidence: normalizeNumber(data.confidence),
      sources: normalizeSources(data.sources),
      requestId: requestId ?? (typeof data.request_id === "string" ? data.request_id : null),
      latencyMs,
    };
  }
}

class RulePreflightTransport extends ChatTransport {
  constructor(innerTransport) {
    super();
    this.innerTransport = innerTransport;
    this.endpoint = "/api/v1/rules/evaluate";
  }

  get mode() {
    return `${this.innerTransport.mode}+rule-preflight`;
  }

  async send(message) {
    const startedAt = performance.now();
    const payload = {
      message: message.content,
      session_id: message.session_id,
      customer_id: message.customer_id,
      current_product_id: message.product_id || null,
      service_stage: message.service_stage,
      channel: "customer_demo",
    };
    const { body, requestId, latencyMs } = await this.postJson(this.endpoint, payload);
    const data = body?.data ?? body;
    const decision = data?.terminal_decision;

    if (decision && typeof decision.fixed_reply === "string" && decision.fixed_reply) {
      return {
        answer: decision.fixed_reply,
        source: "rule",
        route: typeof decision.route === "string" ? decision.route : null,
        product: null,
        productResolution: message.product_id ? "request" : "none",
        ruleName: typeof decision.rule_name === "string" ? decision.rule_name : null,
        confidence: normalizeNumber(decision.confidence),
        sources: [
          {
            chunkId: null,
            title: `Rule: ${decision.rule_name ?? "unknown"}`,
            source: decision.reason_code ?? null,
            score: normalizeNumber(decision.confidence),
          },
        ],
        requestId,
        latencyMs,
      };
    }

    const result = await this.innerTransport.send(message);
    return {
      ...result,
      latencyMs: Math.round(performance.now() - startedAt),
    };
  }
}

class FutureRagChatTransport extends ChatTransport {
  constructor() {
    super();
    this.endpoint = "/api/v1/rag-chat/messages";
  }

  get mode() {
    return "rag-chat";
  }

  async send(message) {
    const payload = {
      session_id: message.session_id,
      message_id: message.id,
      customer_id: message.customer_id,
      message: message.content,
      product_id: message.product_id || null,
      service_stage: message.service_stage,
    };
    const { body, requestId, latencyMs } = await this.postJson(this.endpoint, payload);
    const data = body?.data ?? body;
    if (!data || typeof data.answer !== "string" || data.answer.trim() === "") {
      throw new TransportError("RagChat 尚未接入或返回格式无效。", { requestId, latencyMs });
    }
    return {
      answer: data.answer,
      source: typeof data.source === "string" ? data.source : null,
      route: typeof data.route === "string" ? data.route : null,
      product: data.product && typeof data.product.id === "string" ? data.product : null,
      productResolution: typeof data.product_resolution === "string" ? data.product_resolution : null,
      ruleName: typeof data.rule_name === "string" ? data.rule_name : null,
      confidence: normalizeNumber(data.confidence),
      sources: normalizeSources(data.sources),
      requestId: requestId ?? (typeof data.request_id === "string" ? data.request_id : null),
      latencyMs,
    };
  }
}

function normalizeNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function normalizeSources(value) {
  if (!Array.isArray(value)) return [];
  return value.map((source) => ({
    chunkId: typeof source?.chunk_id === "string" ? source.chunk_id : null,
    title: typeof source?.title === "string" && source.title ? source.title : null,
    source: typeof source?.source === "string" && source.source ? source.source : null,
    score: normalizeNumber(source?.score),
  }));
}

function uuid() {
  if ("randomUUID" in globalThis.crypto) return globalThis.crypto.randomUUID();
  const bytes = new Uint8Array(16);
  if ("getRandomValues" in globalThis.crypto) {
    globalThis.crypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function createMessage(role, content, status = "sent") {
  return {
    id: `msg_${uuid()}`,
    session_id: state.sessionId,
    customer_id: state.customerId,
    role,
    content,
    created_at: new Date().toISOString(),
    status,
  };
}

function currentServiceStage() {
  const selected = dom.stageInputs.find((input) => input.checked);
  return selected ? selected.value : "general";
}

function currentProductId() {
  return dom.productId.value.trim();
}

function updateContextView() {
  const explicitProductId = currentProductId();
  const productId = state.boundProductId || explicitProductId;
  const serviceStage = currentServiceStage();
  dom.currentProductChip.textContent = productId || "未指定商品";
  dom.currentStageChip.textContent = SERVICE_STAGE_LABELS[serviceStage] ?? serviceStage;
  updateDebug({
    current_product_id: productId || null,
    service_stage: serviceStage,
  });
  persistCurrentSession();
}

function persistCurrentSession() {
  if (!state.sessionId) return;
  state.sessions.set(state.sessionId, {
    sessionId: state.sessionId,
    customerId: state.customerId,
    messages: state.messages,
    explicitProductId: currentProductId(),
    boundProductId: state.boundProductId,
    legacyProductCode: dom.legacyProductCode.value.trim(),
    serviceStage: currentServiceStage(),
    debug: state.debug,
  });
  while (state.sessions.size > 12) {
    const oldestSessionId = state.sessions.keys().next().value;
    if (oldestSessionId === state.sessionId) break;
    state.sessions.delete(oldestSessionId);
  }
}

function renderSessionHistory() {
  const sessions = Array.from(state.sessions.values()).reverse();
  dom.sessionCount.textContent = `${sessions.length} 个`;
  dom.sessionList.replaceChildren();

  sessions.forEach((session) => {
    const item = document.createElement("button");
    item.type = "button";
    item.className = `session-item${session.sessionId === state.sessionId ? " active" : ""}`;
    item.dataset.sessionId = session.sessionId;
    item.setAttribute("aria-current", session.sessionId === state.sessionId ? "true" : "false");

    const title = document.createElement("strong");
    title.textContent = `会话 ${session.sessionId.replace("sess_", "").slice(0, 8)}`;
    const customer = document.createElement("span");
    customer.className = "session-customer";
    customer.textContent = session.customerId;
    const meta = document.createElement("em");
    const product = session.boundProductId || session.explicitProductId || "未指定商品";
    const stage = SERVICE_STAGE_LABELS[session.serviceStage] ?? session.serviceStage ?? "通用";
    meta.textContent = `${product} · ${stage} · ${session.messages.length} 条消息`;
    item.append(title, customer, meta);
    item.addEventListener("click", () => selectSession(session.sessionId));
    dom.sessionList.append(item);
  });
}

function selectSession(sessionId) {
  if (state.isSending || sessionId === state.sessionId) return;
  const session = state.sessions.get(sessionId);
  if (!session) return;

  persistCurrentSession();
  state.sessionId = session.sessionId;
  state.customerId = session.customerId;
  state.messages = session.messages;
  state.boundProductId = session.boundProductId ?? null;
  state.debug = { ...session.debug };
  dom.productId.value = session.explicitProductId ?? "";
  dom.legacyProductCode.value = session.legacyProductCode ?? "";
  dom.stageInputs.forEach((input) => {
    input.checked = input.value === (session.serviceStage ?? "general");
  });
  dom.sessionId.textContent = state.sessionId;
  dom.customerId.textContent = state.customerId;
  updateContextView();
  renderMessages();
  renderSessionHistory();
  if (window.matchMedia("(max-width: 1000px)").matches) closePanels();
}

function defaultDebug() {
  return {
    session_id: state.sessionId || null,
    customer_id: state.customerId || null,
    input_message_id: null,
    output_message_id: null,
    current_product_id: state.boundProductId || currentProductId() || null,
    service_stage: currentServiceStage(),
    transport_mode: state.transport?.mode ?? state.transportMode,
    source: null,
    route: null,
    product_resolution: null,
    rule: null,
    confidence: null,
    request_id: null,
    latency: null,
    sources: [],
  };
}

function debugValue(key, value) {
  if (value === null || value === undefined || value === "") return "-";
  if (key === "confidence" && typeof value === "number") return value.toFixed(3);
  if (key === "latency" && typeof value === "number") return `${value} ms`;
  return String(value);
}

function updateDebug(changes = {}) {
  state.debug = { ...defaultDebug(), ...state.debug, ...changes };
  const mappings = {
    session_id: dom.debugSessionId,
    conversation_id: dom.debugConversationId,
    customer_id: dom.debugCustomerId,
    input_message_id: dom.debugInputMessageId,
    output_message_id: dom.debugOutputMessageId,
    current_product_id: dom.debugProductId,
    service_stage: dom.debugServiceStage,
    transport_mode: dom.debugTransportMode,
    source: dom.debugSource,
    route: dom.debugRoute,
    product_resolution: dom.debugProductResolution,
    rule: dom.debugRule,
    confidence: dom.debugConfidence,
    request_id: dom.debugRequestId,
    latency: dom.debugLatency,
  };
  for (const [key, element] of Object.entries(mappings)) {
    element.textContent = debugValue(key, state.debug[key]);
  }
  renderSources(state.debug.sources);
}

function renderSources(sources) {
  dom.sourceList.replaceChildren();
  if (!Array.isArray(sources) || sources.length === 0) {
    dom.sourceCount.textContent = "-";
    const empty = document.createElement("li");
    empty.className = "empty-value";
    empty.textContent = "-";
    dom.sourceList.append(empty);
    return;
  }

  dom.sourceCount.textContent = String(sources.length);
  sources.forEach((source, index) => {
    const item = document.createElement("li");
    item.className = "source-item";
    const title = document.createElement("strong");
    title.textContent = source.title ?? source.source ?? `Source ${index + 1}`;
    const detail = document.createElement("span");
    const parts = [];
    if (source.chunkId) parts.push(source.chunkId);
    if (source.score !== null && source.score !== undefined) parts.push(source.score.toFixed(3));
    detail.textContent = parts.length ? parts.join(" · ") : "-";
    item.append(title, detail);
    dom.sourceList.append(item);
  });
}

function formatTime(isoString) {
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleTimeString("zh-CN", { hour12: false });
}

function scrollToEnd() {
  requestAnimationFrame(() => {
    dom.messageList.scrollTop = dom.messageList.scrollHeight;
  });
}

function renderMessages() {
  dom.messageList.replaceChildren();
  state.messages.forEach((message) => {
    dom.messageList.append(createMessageElement(message));
  });
  scrollToEnd();
}

function createMessageElement(message) {
  const wrapper = document.createElement("article");
  wrapper.className = `message ${message.role} ${message.status}`;
  wrapper.dataset.messageId = message.id;

  if (message.role !== "system") {
    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.setAttribute("aria-hidden", "true");
    avatar.textContent = message.role === "customer" ? "我" : "客服";
    wrapper.append(avatar);
  }

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  const content = document.createElement("p");
  content.className = "message-content";
  content.textContent = message.content;
  bubble.append(content);

  if (message.role === "system" && message.status === "failed" && message.retryFor) {
    const retryButton = document.createElement("button");
    retryButton.type = "button";
    retryButton.className = "retry-button";
    retryButton.textContent = "重试";
    retryButton.addEventListener("click", () => retryMessage(message.retryFor));
    bubble.append(retryButton);
  }

  const meta = document.createElement("div");
  meta.className = "message-meta";
  const time = document.createElement("time");
  time.dateTime = message.created_at;
  time.textContent = formatTime(message.created_at);
  const status = document.createElement("span");
  status.className = `status ${message.status}`;
  status.textContent = getStatusLabel(message.status);
  meta.append(time, status);

  const details = document.createElement("details");
  details.className = "message-details";
  const summary = document.createElement("summary");
  summary.textContent = "消息 ID";
  const code = document.createElement("code");
  code.textContent = message.id;
  details.append(summary, code);

  wrapper.append(bubble, meta, details);
  return wrapper;
}

function getStatusLabel(status) {
  if (status === "sending") return "sending";
  if (status === "failed") return "failed";
  return "sent";
}

function showTypingIndicator() {
  const wrapper = document.createElement("article");
  wrapper.className = "message assistant typing";
  wrapper.id = "typingIndicator";
  wrapper.setAttribute("aria-label", "客服正在输入");

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.setAttribute("aria-hidden", "true");
  avatar.textContent = "客服";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  for (let index = 0; index < 3; index += 1) {
    const dot = document.createElement("span");
    dot.className = "typing-dot";
    bubble.append(dot);
  }
  wrapper.append(avatar, bubble);
  dom.messageList.append(wrapper);
  scrollToEnd();
}

function removeTypingIndicator() {
  document.querySelector("#typingIndicator")?.remove();
}

function resetSession() {
  if (state.isSending) return;
  persistCurrentSession();
  state.sessionId = `sess_${uuid()}`;
  state.customerId = `demo_customer_${uuid()}`;
  state.messages = [];
  state.boundProductId = null;
  state.debug = {};
  const welcome = createMessage("assistant", WELCOME_MESSAGE, "sent");
  state.messages.push(welcome);
  dom.sessionId.textContent = state.sessionId;
  dom.customerId.textContent = state.customerId;
  updateDebug({
    output_message_id: welcome.id,
  });
  persistCurrentSession();
  renderMessages();
  renderSessionHistory();
}

function setSending(isSending) {
  state.isSending = isSending;
  dom.sendButton.disabled = isSending;
  dom.sendButton.textContent = isSending ? "发送中" : "发送";
  dom.sendState.textContent = isSending ? "请求处理中，请稍候…" : "Enter 发送 · Shift + Enter 换行";
}

function createTransport() {
  if (state.transportMode === "chat") return new CustomerChatTransport();
  if (state.transportMode === "rag-chat") return new FutureRagChatTransport();
  return new RulePreflightTransport(new QACompatibilityTransport({
    getLegacyProductCode: () => dom.legacyProductCode.value.trim(),
  }));
}

async function dispatchCustomerMessage(message) {
  if (state.isSending) return;
  setSending(true);
  message.status = "sending";
  message.session_id = state.sessionId;
  message.customer_id = state.customerId;
  message.product_id = currentProductId();
  message.service_stage = currentServiceStage();
  message.legacy_product_code = dom.legacyProductCode.value.trim();
  renderMessages();
  showTypingIndicator();
  updateDebug({
    input_message_id: message.id,
    output_message_id: null,
    current_product_id: state.boundProductId || message.product_id || null,
    service_stage: message.service_stage,
    source: null,
    route: null,
    product_resolution: message.product_id ? "request" : "none",
    rule: null,
    confidence: null,
    request_id: null,
    latency: null,
    sources: [],
  });

  try {
    const result = await state.transport.send(message);
    message.status = "sent";
    if (result.product?.id) {
      state.boundProductId = result.product.id;
    } else if (result.source === "product" && result.route === "product_not_found") {
      state.boundProductId = null;
    }
    const assistantMessage = createMessage("assistant", result.answer, "sent");
    state.messages.push(assistantMessage);
    updateDebug({
      output_message_id: assistantMessage.id,
      current_product_id: state.boundProductId || message.product_id || null,
      source: result.source,
      route: result.route,
      product_resolution: result.productResolution,
      rule: result.ruleName,
      confidence: result.confidence,
      request_id: result.requestId,
      latency: result.latencyMs,
      sources: result.sources,
    });
  } catch (error) {
    message.status = "failed";
    const errorMessage = createMessage(
      "system",
      `请求失败：${error instanceof Error ? error.message : "未知错误"}`,
      "failed",
    );
    errorMessage.retryFor = message.id;
    state.messages.push(errorMessage);
    updateDebug({
      output_message_id: null,
      source: null,
      route: null,
      product_resolution: message.product_id ? "request" : "none",
      rule: null,
      confidence: null,
      request_id: error instanceof TransportError ? error.requestId : null,
      latency: error instanceof TransportError ? error.latencyMs : null,
      sources: [],
    });
  } finally {
    removeTypingIndicator();
    setSending(false);
    persistCurrentSession();
    renderMessages();
    renderSessionHistory();
  }
}

function retryMessage(messageId) {
  if (state.isSending) return;
  const customerMessage = state.messages.find(
    (item) => item.id === messageId && item.role === "customer",
  );
  if (!customerMessage) return;
  state.messages = state.messages.filter(
    (item) => !(item.role === "system" && item.retryFor === messageId),
  );
  dispatchCustomerMessage(customerMessage);
}

function autoResizeTextarea() {
  dom.messageInput.style.height = "auto";
  dom.messageInput.style.height = `${Math.min(dom.messageInput.scrollHeight, 150)}px`;
}

function closePanels() {
  document.body.classList.remove("session-open", "debug-open");
  dom.panelScrim.hidden = true;
  dom.panelButtons.forEach((button) => button.setAttribute("aria-expanded", "false"));
}

function bindEvents() {
  dom.newSessionButton.addEventListener("click", resetSession);
  dom.resetSessionButton.addEventListener("click", resetSession);

  dom.productForm.addEventListener("submit", (event) => event.preventDefault());
  dom.productId.addEventListener("input", updateContextView);
  dom.legacyProductCode.addEventListener("input", updateContextView);
  dom.stageInputs.forEach((input) => input.addEventListener("change", updateContextView));

  dom.transportMode.addEventListener("change", () => {
    state.transportMode = dom.transportMode.value;
    state.transport = createTransport();
    updateDebug({
      transport_mode: state.transport?.mode ?? state.transportMode,
      source: null,
      route: null,
      product_resolution: state.boundProductId ? "conversation" : "none",
      rule: null,
      confidence: null,
      request_id: null,
      latency: null,
      sources: [],
    });
  });

  dom.messageForm.addEventListener("submit", (event) => {
    event.preventDefault();
    if (state.isSending) return;
    const content = dom.messageInput.value.trim();
    if (!content) {
      dom.messageInput.focus();
      return;
    }
    const message = createMessage("customer", content, "sending");
    state.messages.push(message);
    dom.messageInput.value = "";
    autoResizeTextarea();
    dispatchCustomerMessage(message);
  });

  dom.messageInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      dom.messageForm.requestSubmit();
    }
  });
  dom.messageInput.addEventListener("input", autoResizeTextarea);

  dom.panelButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const panel = button.dataset.panel;
      const isOpen = document.body.classList.contains(`${panel}-open`);
      closePanels();
      if (!isOpen) {
        document.body.classList.add(`${panel}-open`);
        button.setAttribute("aria-expanded", "true");
        dom.panelScrim.hidden = false;
      }
    });
  });
  dom.panelScrim.addEventListener("click", closePanels);
}

function initialize() {
  state.transportMode = dom.transportMode.value;
  state.transport = createTransport();
  bindEvents();
  updateContextView();
  resetSession();
  renderSessionHistory();
}

initialize();
