"""拼多多客服页 DOM 契约集中定义。页面改版时只需调整本模块。"""

CONVERSATION_ITEM_SELECTORS = (
    ".all-chat-list .chat-item-box[data-random]",
    ".chat-item-box[data-random]",
    '[data-random$="-reply"]',
    "[data-uid].chat-item",
    '.chat-item[data-random]',
)
REPLY_TEXTAREA_SELECTORS = ("textarea#replyTextarea", "textarea[placeholder*='回复']")
SEND_BUTTON_SELECTORS = (
    ".reply-box button:visible:has-text('发送')",
    ".reply-box .send-btn:visible:not([disabled])",
    "div.send-btn:visible:not([disabled])",
    "button.send-btn:visible:not([disabled])",
)


DOM_CONTRACT_SCRIPT = r"""
() => ({
  conversation_root: Boolean(document.querySelector('.chat-list-box, .chat-list')),
  message_root: Boolean(document.querySelector('.history-box, .msg-list, .content-box')),
  reply_root: Boolean(document.querySelector('.reply-box, .reply-input')),
  conversation_count: document.querySelectorAll('.chat-item-box[data-random], [data-random$="-reply"], [data-uid].chat-item').length,
  message_count: document.querySelectorAll('.msg-list > .onemsg, .onemsg').length
})
"""


DOM_DIAGNOSTICS_SCRIPT = r"""
() => {
  const classes = new Map();
  const dataAttrs = new Map();
  for (const element of document.querySelectorAll('*')) {
    for (const token of element.classList || []) {
      if (token.length <= 100) classes.set(token, (classes.get(token) || 0) + 1);
    }
    for (const attr of element.attributes || []) {
      if (attr.name.startsWith('data-')) dataAttrs.set(attr.name, (dataAttrs.get(attr.name) || 0) + 1);
    }
  }
  const top = map => Array.from(map.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, 120)
    .map(([name, count]) => ({name, count}));
  const safeUrl = `${location.origin}${location.pathname}`;
  const describe = element => {
    const ancestors = [];
    let parent = element.parentElement;
    for (let depth = 0; parent && depth < 4; depth += 1, parent = parent.parentElement) {
      ancestors.push({tag: parent.tagName.toLowerCase(), classes: Array.from(parent.classList || []).slice(0, 12)});
    }
    const children = Array.from(element.children || []).slice(0, 12).map(child => ({
      tag: child.tagName.toLowerCase(),
      classes: Array.from(child.classList || []).slice(0, 12),
      attributes: Array.from(child.attributes || []).map(attr => attr.name).filter(name => name !== 'style')
    }));
    const random = element.getAttribute('data-random') || '';
    return {
      tag: element.tagName.toLowerCase(),
      classes: Array.from(element.classList || []).slice(0, 16),
      attributes: Array.from(element.attributes || []).map(attr => attr.name).filter(name => name !== 'style'),
      data_random_length: random.length,
      data_random_ends_reply: random.endsWith('-reply'),
      ancestors,
      children
    };
  };
  const samples = selector => Array.from(document.querySelectorAll(selector)).slice(0, 8).map(describe);
  const inputDiagnostics = Array.from(document.querySelectorAll('textarea, [contenteditable="true"]')).slice(0, 12).map(element => {
    const rect = element.getBoundingClientRect();
    const centerX = rect.left + rect.width / 2;
    const centerY = rect.top + rect.height / 2;
    const hit = rect.width > 0 && rect.height > 0 ? document.elementFromPoint(centerX, centerY) : null;
    const style = getComputedStyle(element);
    return {
      tag: element.tagName.toLowerCase(),
      id: element.id || null,
      classes: Array.from(element.classList || []).slice(0, 12),
      visible: style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0,
      disabled: element.disabled === true,
      readonly: element.readOnly === true,
      editable: element.isContentEditable,
      focused: document.activeElement === element,
      rect: {x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height)},
      hit_is_self: hit === element || Boolean(hit?.contains(element)),
      hit_tag: hit?.tagName.toLowerCase() || null,
      hit_classes: hit ? Array.from(hit.classList || []).slice(0, 12) : []
    };
  });
  return {
    url: safeUrl,
    element_count: document.querySelectorAll('*').length,
    iframe_count: document.querySelectorAll('iframe').length,
    textarea_count: document.querySelectorAll('textarea').length,
    editable_count: document.querySelectorAll('[contenteditable="true"]').length,
    button_count: document.querySelectorAll('button').length,
    visible_dialog_count: Array.from(document.querySelectorAll('.el-dialog__wrapper')).filter(element => element.offsetParent !== null).length,
    visible_dialog_close_count: Array.from(document.querySelectorAll('.el-dialog__wrapper .el-dialog__headerbtn')).filter(element => element.offsetParent !== null).length,
    list_count: document.querySelectorAll('[role="list"], ul, ol').length,
    listitem_count: document.querySelectorAll('[role="listitem"], li').length,
    current_conversation_matches: document.querySelectorAll('[data-random$="-reply"], [data-uid].chat-item, .chat-item[data-random]').length,
    current_message_matches: document.querySelectorAll('[data-message-id], [data-msg-id], .message-item, [class*="message-row"]').length,
    current_reply_matches: document.querySelectorAll('textarea#replyTextarea, textarea[placeholder*="回复"]').length,
    input_diagnostics: inputDiagnostics,
    structural_samples: {
      conversation: samples('.chat-item, .chat-item-box, [data-random]'),
      message: samples('.onemsg, .msg-content-box, .chat-message-content, .merchantMessage, .buyer-item'),
      input: samples('textarea, [contenteditable="true"]'),
      send: samples('.reply-box button, .reply-box [class*="send"]')
    },
    top_classes: top(classes),
    data_attributes: top(dataAttrs)
  };
}
"""


MUTATION_OBSERVER_SCRIPT = r"""
() => {
  const install = () => {
    if (!document.body || window.__codexPddObserver) return;
    let timer = null;
    window.__codexPddObserver = new MutationObserver(() => {
      clearTimeout(timer);
      timer = setTimeout(() => window.__codexPddWake?.(), 80);
    });
    window.__codexPddObserver.observe(document.body, {childList: true, subtree: true});
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install);
  else install();
}
"""


SCAN_CONVERSATIONS_SCRIPT = r"""
() => {
  const itemSelector = '.chat-item-box[data-random], [data-random$="-reply"], [data-uid].chat-item, .chat-item[data-random]';
  const allItems = Array.from(document.querySelectorAll(itemSelector));
  const visibleItems = allItems.filter(item => item.offsetParent !== null);
  const items = visibleItems.length ? visibleItems : allItems;
  const found = new Map();
  const hash = value => {
    let result = 2166136261;
    for (let index = 0; index < value.length; index += 1) {
      result ^= value.charCodeAt(index);
      result = Math.imul(result, 16777619);
    }
    return (result >>> 0).toString(16);
  };
  const customerId = item => {
    const raw = item.getAttribute('data-uid') || item.getAttribute('data-random') || '';
    const match = raw.match(/^(\d+)(?:-|$)/);
    return match ? match[1] : raw.replace(/-(?:all|reply)$/, '');
  };
  items.forEach((item, index) => {
    const random = item.getAttribute('data-random') || '';
    const id = customerId(item);
    if (!id) return;
    const nameNode = item.querySelector('.chat-nickname, .nickname-span, .chat-detail [class*="nickname"], [class*="nick"], [class*="name"], .username');
    const displayName = (nameNode?.textContent || '顾客').trim();
    const avatarNode = item.querySelector('.chat-portrait img, img');
    const avatarValue = avatarNode?.getAttribute('src') || '';
    let avatarPath = avatarValue.split(/[?#]/, 1)[0];
    if (avatarValue) {
      try {
        const avatarUrl = new URL(avatarValue, location.origin);
        avatarPath = `${avatarUrl.origin}${avatarUrl.pathname}`;
      } catch (_error) {}
    }
    const identityMaterial = avatarPath ? `${displayName}|${avatarPath}` : `${displayName}|${id}`;
    const unreadNode = item.querySelector('[class*="unread"], [class*="badge"], .chat-status-icon');
    const unreadMatch = (unreadNode?.textContent || '').match(/\d+/);
    const activityText = Array.from(item.querySelectorAll('.bottom-message, .chat-message-content, .chat-time'))
      .map(node => (node.textContent || '').trim()).join('|');
    const candidate = {
      conversation_id: id,
      platform_customer_id: id,
      dom_conversation_id: item.getAttribute('data-uid') || random,
      identity_material: identityMaterial,
      display_name: displayName,
      avatar_url: avatarValue || null,
      unread: unreadMatch ? Number(unreadMatch[0]) : (unreadNode ? 1 : 0),
      index,
      active: item.matches('.active, [class*="active"], [aria-selected="true"]'),
      activity_key: hash(activityText)
    };
    const previous = found.get(id);
    if (!previous || candidate.active || candidate.unread > previous.unread) found.set(id, candidate);
  });
  return Array.from(found.values());
}
"""


SCAN_CURRENT_MESSAGES_SCRIPT = r"""
(conversationId) => {
  const root = document.querySelector('.history-box .msg-list, .msg-list, .history-box, [class*="message-list"]') || document;
  const nodes = Array.from(root.querySelectorAll('.onemsg, [data-message-id], [data-msg-id], .message-item, [class*="message-row"]'));
  const panelCustomerIds = Array.from(root.querySelectorAll('[currentuid]'))
    .map(item => item.getAttribute('currentuid')).filter(Boolean);
  const panelCustomerId = Array.from(new Set(panelCustomerIds)).length === 1
    ? panelCustomerIds[0]
    : null;
  return nodes.map((node, index) => {
    const cls = String(node.className || '').toLowerCase();
    const role = String(node.getAttribute('data-role') || node.getAttribute('data-from') || '').toLowerCase();
    const outbound = Boolean(node.querySelector('.cs-item')) || /seller|self|outgoing|right/.test(`${cls} ${role}`);
    const inbound = Boolean(node.querySelector('.buyer-item')) || (!outbound && !node.matches('.onemsg'));
    if (!outbound && !inbound) return null;
    const currentNode = node.querySelector('[currentuid]');
    const messageCustomerId = currentNode?.getAttribute('currentuid') || panelCustomerId;
    const textNode = node.querySelector('.msg-content-box, .message-text, .text-content, [class*="text-content"]');
    const allText = (node.textContent || '').trim();
    const goodsNode = node.querySelector('[data-goods-id], .goodsName, [class*="goods-card"], [class*="goods-item"], [class*="product-card"]');
    const goodsNameNode = goodsNode?.querySelector?.('.goodsName, [class*="goods-name"], [class*="product-name"], [class*="title"]') || goodsNode;
    const goodsText = (goodsNameNode?.textContent || goodsNode?.textContent || '').trim();
    const goodsIdMatch = `${goodsNode?.getAttribute('data-goods-id') || ''} ${goodsNode?.getAttribute('data-id') || ''} ${goodsNode?.textContent || ''}`.match(/\d{8,}/);
    const priceNode = goodsNode?.querySelector?.('[class*="price"], [data-price]');
    const priceMatch = `${priceNode?.getAttribute('data-price') || ''} ${priceNode?.textContent || ''} ${goodsNode?.textContent || ''}`.match(/[¥￥]?\s*\d+(?:\.\d{1,2})?/);
    const goodsLinkNode = goodsNode?.closest?.('a[href]') || goodsNode?.querySelector?.('a[href]');
    let goodsUrl = goodsLinkNode?.getAttribute('href') || goodsNode?.getAttribute('data-href') || goodsNode?.getAttribute('data-url') || '';
    try { if (goodsUrl) goodsUrl = new URL(goodsUrl, location.href).href; } catch (_error) { goodsUrl = ''; }
    const mediaRoot = node.querySelector('.msg-content, [class*="message-content"], .message-text, .text-content') || node;
    const normalizeUrl = value => {
      if (!value || value.startsWith('blob:')) return '';
      try { return new URL(value, location.href).href; } catch (_error) { return ''; }
    };
    const candidates = [];
    for (const image of mediaRoot.querySelectorAll('img')) {
      if (image.closest('.avatar, [class*="avatar"], .portrait, [class*="portrait"]')) continue;
      const source = normalizeUrl(image.currentSrc || image.getAttribute('src') || image.getAttribute('data-src') || '');
      if (!source) continue;
      const rect = image.getBoundingClientRect();
      const markers = `${image.className || ''} ${image.getAttribute('alt') || ''} ${image.getAttribute('title') || ''}`;
      candidates.push({
        source,
        element: image,
        emoji: /emoji|emotion|emoticon|face|表情/i.test(markers) || (!goodsNode && rect.width > 0 && rect.width <= 96 && rect.height > 0 && rect.height <= 96),
        alt: (image.getAttribute('alt') || image.getAttribute('title') || '').trim(),
        width: Math.round(rect.width || image.naturalWidth || 0) || null,
        height: Math.round(rect.height || image.naturalHeight || 0) || null
      });
    }
    const backgroundNodes = [mediaRoot, ...mediaRoot.querySelectorAll('[style*="background"], [class*="emoji"], [class*="emotion"]')];
    for (const element of backgroundNodes) {
      const match = getComputedStyle(element).backgroundImage.match(/^url\(["']?(.*?)["']?\)$/);
      const source = normalizeUrl(match?.[1] || '');
      if (!source || candidates.some(item => item.source === source)) continue;
      const rect = element.getBoundingClientRect();
      candidates.push({
        source,
        element,
        emoji: /emoji|emotion|emoticon|face|表情/i.test(String(element.className || '')) || (rect.width > 0 && rect.width <= 96 && rect.height > 0 && rect.height <= 96),
        alt: (element.getAttribute('aria-label') || element.getAttribute('title') || '').trim(),
        width: Math.round(rect.width) || null,
        height: Math.round(rect.height) || null
      });
    }
    const assets = candidates.slice(0, goodsNode ? 1 : 4).map(candidate => ({
      asset_type: goodsNode ? 'goods_image' : (candidate.emoji ? 'emoji' : 'image'),
      source_url: candidate.source,
      width: candidate.width,
      height: candidate.height,
      metadata: {
        ...(candidate.alt ? {alt: candidate.alt} : {}),
        ...(goodsNode ? {
          goods_id: goodsIdMatch ? goodsIdMatch[0] : null,
          goods_name: goodsText || null,
          goods_price: priceMatch ? priceMatch[0].replace(/\s+/g, '') : null,
          goods_url: goodsUrl || null
        } : {})
      }
    }));
    const hasEmoji = assets.length > 0 && assets.every(item => item.asset_type === 'emoji');
    let kind = goodsNode ? 'goods_card' : (assets.length ? (hasEmoji ? 'emoji' : 'image') : (textNode ? 'text' : 'unsupported'));
    const messageId = node.getAttribute('data-message-id') || node.getAttribute('data-msg-id') || node.id || node.getAttribute('data-random');
    const timeNode = node.querySelector('time, .message-time');
    const rawSenderName = (node.querySelector('.cs-item .nickname, .seller-item .nickname')?.textContent || '').trim();
    const automatic = outbound && Boolean(node.querySelector('.robot-text, [class*="robot"]'));
    const automaticName = rawSenderName.match(/([^\s]+)\s*-\s*自动回复/);
    const senderName = automatic ? (automaticName?.[1] || '自动客服') : rawSenderName;
    return {
      conversation_id: conversationId,
      platform_customer_id: messageCustomerId || null,
      direction: outbound ? 'outbound' : 'inbound',
      sender_type: outbound ? (automatic ? 'automation' : 'agent') : 'customer',
      sender_name: senderName || null,
      kind,
      content: kind === 'text' ? (textNode?.textContent || allText).trim() : (goodsText || assets[0]?.metadata?.alt || allText || null),
      message_id: messageId || null,
      timestamp: node.getAttribute('data-timestamp') || timeNode?.getAttribute('datetime') || (timeNode?.textContent || '').trim() || null,
      goods_id: goodsIdMatch ? goodsIdMatch[0] : null,
      goods_name: goodsText || null,
      goods_price: priceMatch ? priceMatch[0].replace(/\s+/g, '') : null,
      goods_url: goodsUrl || null,
      assets,
      dom_index: index,
      dom_key: `${index}:${messageId || ''}`
    };
  }).filter(Boolean);
}
"""


MESSAGE_HISTORY_STATE_SCRIPT = r"""
() => {
  const container = document.querySelector('.history-box.custom-scroll, .history-box .custom-scroll, .history-box');
  const nodes = Array.from(document.querySelectorAll('.history-box .onemsg, .msg-list .onemsg'));
  const key = node => node?.getAttribute('data-message-id') || node?.getAttribute('data-msg-id') || node?.id || '';
  const customer_ids = Array.from(document.querySelectorAll('.history-box [currentuid]'))
    .map(node => node.getAttribute('currentuid')).filter(Boolean);
  return {
    available: Boolean(container),
    scroll_top: container?.scrollTop || 0,
    scroll_height: container?.scrollHeight || 0,
    client_height: container?.clientHeight || 0,
    message_count: nodes.length,
    first_key: key(nodes[0]),
    last_key: key(nodes.at(-1)),
    customer_ids: Array.from(new Set(customer_ids))
  };
}
"""


SCROLL_MESSAGE_HISTORY_SCRIPT = r"""
(position) => {
  const container = document.querySelector('.history-box.custom-scroll, .history-box .custom-scroll, .history-box');
  if (!container) return false;
  container.scrollTop = position === 'top' ? 0 : container.scrollHeight;
  container.dispatchEvent(new Event('scroll', {bubbles: true}));
  return true;
}
"""


COUNT_OUTBOUND_TEXT_SCRIPT = r"""
(content) => Array.from(document.querySelectorAll('.onemsg, [data-message-id], [data-msg-id], .message-item, [class*="message-row"]'))
  .filter(node => {
    const cls = String(node.className || '').toLowerCase();
    const role = String(node.getAttribute('data-role') || node.getAttribute('data-from') || '').toLowerCase();
    const outbound = Boolean(node.querySelector('.cs-item')) || /seller|self|outgoing|right/.test(`${cls} ${role}`);
    return outbound && (node.textContent || '').trim().includes(content);
  }).length
"""


LATEST_OUTBOUND_TEXT_SCRIPT = r"""
(content) => {
  const nodes = Array.from(document.querySelectorAll('.onemsg, [data-message-id], [data-msg-id], .message-item, [class*="message-row"]'));
  const matches = nodes.filter(node => {
    const cls = String(node.className || '').toLowerCase();
    const role = String(node.getAttribute('data-role') || node.getAttribute('data-from') || '').toLowerCase();
    return (Boolean(node.querySelector('.cs-item')) || /seller|self|outgoing|right/.test(`${cls} ${role}`))
      && (node.textContent || '').trim().includes(content);
  });
  const node = matches.at(-1);
  if (!node) return null;
  return {
    content,
    message_id: node.getAttribute('data-message-id') || node.getAttribute('data-msg-id') || node.id || node.getAttribute('data-random'),
    timestamp: node.getAttribute('data-timestamp') || node.querySelector('time')?.getAttribute('datetime') || (node.querySelector('.message-time')?.textContent || '').trim() || null,
    dom_key: String(nodes.indexOf(node)),
    platform_customer_id: node.querySelector('[currentuid]')?.getAttribute('currentuid') || null,
    sender_type: Boolean(node.querySelector('.robot-text, [class*="robot"]')) ? 'automation' : 'agent',
    sender_name: (() => {
      const raw = (node.querySelector('.cs-item .nickname, .seller-item .nickname')?.textContent || '').trim();
      if (!Boolean(node.querySelector('.robot-text, [class*="robot"]'))) return raw || null;
      return raw.match(/([^\s]+)\s*-\s*自动回复/)?.[1] || '自动客服';
    })()
  };
}
"""


SHOP_IDENTITY_SCRIPT = r"""
() => {
  const visible = node => {
    if (!(node instanceof HTMLElement)) return false;
    const style = window.getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
  };
  const roots = Array.from(document.querySelectorAll(
    '[data-shop-id], [data-mall-id], [data-merchant-id], [data-store-id], '
    + '[class*="merchant"], [class*="shop"], [class*="mall"], [class*="store"]'
  )).filter(visible);
  const idPattern = /(?:shop|mall|merchant|store)[-_ ]?(?:id)?[:：\s#]*([A-Za-z0-9_-]{4,191})/i;
  for (const node of roots) {
    const id = node.getAttribute('data-shop-id')
      || node.getAttribute('data-mall-id')
      || node.getAttribute('data-merchant-id')
      || node.getAttribute('data-store-id')
      || (node.textContent || '').match(idPattern)?.[1];
    if (!id) continue;
    const nameNode = node.querySelector('[class*="name"], [title]');
    const name = (nameNode?.getAttribute('title') || nameNode?.textContent || node.getAttribute('title') || '').trim();
    if (name && name.length <= 255) return {platform_shop_id: String(id), name};
  }
  const bodyText = (document.body?.innerText || '').slice(0, 20000);
  const id = bodyText.match(idPattern)?.[1];
  const title = (document.querySelector('header [title], [class*="shop-name"]')?.getAttribute('title')
    || document.querySelector('header [class*="name"], [class*="shop-name"]')?.textContent || '').trim();
  return id && title ? {platform_shop_id: id, name: title} : null;
}
"""
