const state = {
  selectedClusterId: null,
  sources: [],
  clusters: [],
  timelineTotal: 0,
  timelineHasMore: false,
  timelineLoadingMore: false,
  system: null,
  aiConfig: null,
  editingSourceId: null,
  settingsOpen: false,
};

const AUTO_REFRESH_INTERVAL_MS = 60000;
const TIMELINE_PAGE_SIZE = 20;
let timelineLoadObserver = null;

const elements = {
  timelineOverview: document.getElementById("timeline-overview"),
  timelineList: document.getElementById("timeline-list"),
  timelineLoadMore: document.getElementById("timeline-load-more"),
  timelineLoadMoreText: document.getElementById("timeline-load-more-text"),
  detailPanel: document.getElementById("detail-panel"),
  categoryFilter: document.getElementById("timeline-category-filter"),
  sortFilter: document.getElementById("timeline-sort-filter"),
  importanceFilter: document.getElementById("timeline-importance-filter"),
  socketIndicator: document.getElementById("socket-indicator"),
  settingsButton: document.getElementById("settings-button"),
  settingsPanel: document.getElementById("settings-panel"),
  closeSettingsButton: document.getElementById("close-settings-button"),
  sourceTable: document.getElementById("source-table"),
  statusGrid: document.getElementById("status-grid"),
  sourceModal: document.getElementById("source-modal"),
  sourceModalTitle: document.getElementById("source-modal-title"),
  sourceForm: document.getElementById("source-form"),
  sourceFeedback: document.getElementById("source-feedback"),
  bulkXModal: document.getElementById("bulk-x-modal"),
  bulkXForm: document.getElementById("bulk-x-form"),
  bulkXFeedback: document.getElementById("bulk-x-feedback"),
  manualSyncButton: document.getElementById("manual-sync-button"),
  syncFeedback: document.getElementById("sync-feedback"),
  bulkXImportButton: document.getElementById("bulk-x-import-button"),
  newSourceButton: document.getElementById("new-source-button"),
  closeModalButton: document.getElementById("close-modal-button"),
  closeBulkXModalButton: document.getElementById("close-bulk-x-modal-button"),
  testSourceButton: document.getElementById("test-source-button"),
  aiConfigForm: document.getElementById("ai-config-form"),
  aiConfigFeedback: document.getElementById("ai-config-feedback"),
  testAIButton: document.getElementById("test-ai-button"),
};

const categoryLabels = {
  AI: "AI",
  科技: "科技",
  宏观: "宏观",
  时政: "时政",
  数字货币: "数字货币",
};

const sourceTypeLabels = {
  api: "API Source",
  feed: "Feed Source",
  x: "X Source",
};

function formatRelativeTime(isoString) {
  const date = new Date(isoString);
  const diffMinutes = Math.max(1, Math.round((Date.now() - date.getTime()) / 60000));
  if (diffMinutes < 60) {
    return `${diffMinutes} 分钟前`;
  }
  const diffHours = Math.round(diffMinutes / 60);
  if (diffHours < 24) {
    return `${diffHours} 小时前`;
  }
  return `${Math.round(diffHours / 24)} 天前`;
}

function formatClockTime(isoString) {
  const date = new Date(isoString);
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function isChineseContent(language, originalTitle, translatedTitle) {
  const normalizedOriginal = stripInlineUrls(originalTitle || "");
  const normalizedTranslated = stripInlineUrls(translatedTitle || "");
  return (
    language === "zh" ||
    !normalizedOriginal ||
    normalizedOriginal === normalizedTranslated
  );
}

function getBilingualLines(language, originalText, translatedText) {
  const normalizedOriginal = stripInlineUrls(originalText || "");
  const normalizedTranslated = stripInlineUrls(translatedText || "");
  if (isChineseContent(language, normalizedOriginal, normalizedTranslated)) {
    return {
      original: "",
      translated: normalizedOriginal || normalizedTranslated,
      bilingual: false,
    };
  }
  if (!normalizedTranslated) {
    return {
      original: "",
      translated: normalizedOriginal,
      bilingual: false,
    };
  }
  return {
    original: normalizedOriginal,
    translated: normalizedTranslated,
    bilingual: true,
  };
}

function renderBilingualLines({
  language,
  originalText,
  translatedText,
  originalClassName,
  translatedClassName,
}) {
  const lines = getBilingualLines(language, originalText, translatedText);
  if (!lines.bilingual) {
    return `<span class="${translatedClassName}">${escapeHtml(lines.translated)}</span>`;
  }
  return `
    <span class="${originalClassName}">${escapeHtml(lines.original)}</span>
    <span class="${translatedClassName}">${escapeHtml(lines.translated)}</span>
  `;
}

function renderDetailBody(message) {
  const originalContent = stripInlineUrls(message.original_content || "");
  const summary = stripInlineUrls(message.summary_zh || "");
  const showSummary =
    summary &&
    summary !== originalContent &&
    !(message.language === "zh" && summary === stripInlineUrls(message.original_title || ""));
  return `
    ${showSummary ? `<p class="detail-message-summary">${escapeHtml(summary)}</p>` : ""}
    <div class="detail-message-body">${escapeHtml(originalContent || summary || message.original_title || "")}</div>
  `;
}

function formatTimelineDate(isoString) {
  const date = new Date(isoString);
  const weekday = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"][date.getDay()];
  return `${date.getMonth() + 1} 月 ${date.getDate()} 日 ${weekday}`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function stripInlineUrls(value) {
  return String(value || "")
    .replace(/https?:\/\/\S+|www\.\S+/g, " ")
    .replace(/\(\s*\)/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function renderTimelineOverview() {
  if (state.clusters.length === 0) {
    elements.timelineOverview.innerHTML = "";
    return;
  }

  const latestCluster = state.clusters[0];
  elements.timelineOverview.innerHTML = `
    <div class="timeline-overview-date">${formatTimelineDate(latestCluster.latest_published_at)}</div>
    <div class="timeline-overview-meta">
      <span>已加载 ${state.clusters.length} / ${state.timelineTotal || state.clusters.length} 条聚合</span>
      <span>更新至 ${formatClockTime(latestCluster.latest_published_at)}</span>
    </div>
  `;
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const payload = await response.text();
    throw new Error(payload || "请求失败");
  }
  if (response.status === 204) {
    return null;
  }
  return response.json();
}

function setSettingsOpen(open) {
  state.settingsOpen = open;
  elements.settingsPanel.hidden = !open;
}

function renderTimelineLoadMore() {
  if (state.clusters.length === 0) {
    elements.timelineLoadMoreText.textContent = "";
    return;
  }
  if (state.timelineLoadingMore) {
    elements.timelineLoadMoreText.textContent = "正在加载更多…";
    return;
  }
  if (state.timelineHasMore) {
    elements.timelineLoadMoreText.textContent =
      `已加载 ${state.clusters.length} / ${state.timelineTotal} 条，继续下拉加载更多`;
    return;
  }
  elements.timelineLoadMoreText.textContent =
    `已显示全部 ${state.timelineTotal || state.clusters.length} 条聚合`;
}

function renderTimeline() {
  elements.timelineList.innerHTML = "";
  if (state.clusters.length === 0) {
    renderTimelineOverview();
    renderTimelineLoadMore();
    elements.timelineList.innerHTML = '<div class="empty-state">当前筛选条件下没有聚合卡片。</div>';
    elements.detailPanel.innerHTML = `
      <div class="detail-placeholder">
        <p class="eyebrow">Detail</p>
        <h3>暂无可展示内容</h3>
        <p>调整筛选条件，或在右上角设置里点击手动同步。</p>
      </div>
    `;
    return;
  }

  renderTimelineOverview();
  renderTimelineLoadMore();
  const template = document.getElementById("timeline-card-template");
  for (const cluster of state.clusters) {
    const fragment = template.content.cloneNode(true);
    fragment.querySelector(".category-label").textContent =
      categoryLabels[cluster.primary_category] || cluster.primary_category;
    fragment.querySelector(".cluster-time").textContent = formatClockTime(cluster.latest_published_at);
    fragment.querySelector(".score-importance").textContent = `重要性 ${Math.round(cluster.importance_score)}`;
    fragment.querySelector(".score-heat").textContent = `热度 ${Math.round(cluster.heat_score)}`;
    fragment.querySelector(".cluster-title").innerHTML = renderBilingualLines({
      language: cluster.canonical_language,
      originalText: cluster.canonical_original_title,
      translatedText: cluster.canonical_title,
      originalClassName: "cluster-title-original",
      translatedClassName: "cluster-title-translated",
    });
    fragment.querySelector(".cluster-summary").remove();
    fragment.querySelector(".cluster-counts").textContent =
      `来源 ${cluster.source_count} · 消息 ${cluster.message_count}`;
    const card = fragment.querySelector(".cluster-card");
    card.dataset.category = cluster.primary_category;
    card.setAttribute("aria-pressed", cluster.cluster_id === state.selectedClusterId ? "true" : "false");
    if (cluster.cluster_id === state.selectedClusterId) {
      card.classList.add("is-selected");
    }
    card.addEventListener("click", () => openCluster(cluster.cluster_id));
    elements.timelineList.appendChild(fragment);
  }

  if (!state.selectedClusterId && state.clusters[0]) {
    openCluster(state.clusters[0].cluster_id);
  }
}

async function openCluster(clusterId) {
  state.selectedClusterId = clusterId;
  renderTimeline();
  elements.detailPanel.innerHTML =
    '<div class="detail-placeholder"><p class="eyebrow">Detail</p><h3>正在加载详情</h3></div>';
  try {
    const messages = await fetchJson(`/api/clusters/${clusterId}/messages`);
    const currentCluster = state.clusters.find((item) => item.cluster_id === clusterId);
    if (!currentCluster) {
      return;
    }
    elements.detailPanel.dataset.category = currentCluster.primary_category;
    elements.detailPanel.innerHTML = `
      <div class="detail-content">
        <div class="detail-content-header">
          <p class="eyebrow">Detail</p>
          <div class="detail-cluster-title">
            ${renderBilingualLines({
              language: currentCluster.canonical_language,
              originalText: currentCluster.canonical_original_title,
              translatedText: currentCluster.canonical_title,
              originalClassName: "detail-cluster-original",
              translatedClassName: "detail-cluster-translated",
            })}
          </div>
        </div>
      </div>
      ${messages
        .map(
          (message) => `
            <article class="detail-message">
              <div class="detail-message-header">
                <strong>${escapeHtml(message.source_name)}</strong>
                <span class="muted-text">${formatClockTime(message.published_at)}</span>
              </div>
              <div class="detail-message-lines">
                ${renderBilingualLines({
                  language: message.language,
                  originalText: message.original_title,
                  translatedText: message.translated_title,
                  originalClassName: "detail-message-original",
                  translatedClassName: "detail-message-title",
                })}
              </div>
              ${renderDetailBody(message)}
              <div class="source-actions">
                <span class="muted-text">${message.language.toUpperCase()}</span>
                <a class="link-button" target="_blank" rel="noreferrer" href="${escapeHtml(message.original_url)}">打开原文</a>
              </div>
            </article>
          `
        )
        .join("")}
    `;
  } catch (error) {
    delete elements.detailPanel.dataset.category;
    elements.detailPanel.innerHTML = `
      <div class="detail-placeholder">
        <p class="eyebrow">Detail</p>
        <h3>详情加载失败</h3>
        <p>${error.message}</p>
      </div>
    `;
  }
}

function renderSources() {
  if (state.sources.length === 0) {
    elements.sourceTable.innerHTML = '<div class="empty-state">尚未添加来源。</div>';
    return;
  }

  const standardSources = state.sources.filter((source) => source.source_type !== "x");
  const xSources = state.sources.filter((source) => source.source_type === "x");

  elements.sourceTable.innerHTML = [
    ...standardSources.map((source) => renderSourceRow(source)),
    xSources.length ? renderXSourceGroup(xSources) : "",
  ].join("");

  for (const button of elements.sourceTable.querySelectorAll("button")) {
    const { action, id } = button.dataset;
    button.addEventListener("click", () => handleSourceAction(action, id));
  }
}

function renderSourceRow(source) {
  const errorMessage = source.last_error_message
    ? `<p class="muted-text">${escapeHtml(source.last_error_message)}</p>`
    : "";
  return `
    <article class="source-row">
      <div class="source-row-header">
        <div>
          <p class="eyebrow">${sourceTypeLabels[source.source_type] || `${source.source_type.toUpperCase()} Source`}</p>
          <h3 class="source-title">${escapeHtml(source.name)}</h3>
          <p class="source-subtitle">${escapeHtml(source.endpoint)}</p>
          ${errorMessage}
        </div>
        <div class="source-status-group">
          <span class="status-tag" data-state="${source.health_status}">${escapeHtml(source.health_status)}</span>
          <button class="toggle-button ${source.enabled ? "is-active" : ""}" type="button" data-action="toggle" data-id="${source.source_id}" aria-label="${source.enabled ? "停用" : "启用"}">
            <span class="toggle-slider"></span>
          </button>
        </div>
      </div>
      <div class="source-actions">
        <button class="ghost-button compact-button" type="button" data-action="edit" data-id="${source.source_id}">编辑</button>
        <button class="ghost-button compact-button" type="button" data-action="delete" data-id="${source.source_id}">删除</button>
      </div>
    </article>
  `;
}

function renderXSourceGroup(sources) {
  const enabledCount = sources.filter((source) => source.enabled).length;
  return `
    <article class="source-row source-group-card">
      <div class="source-row-header source-group-header">
        <div>
          <p class="eyebrow">X Source</p>
          <h3 class="source-title">X 订阅</h3>
          <p class="source-subtitle">共 ${sources.length} 个账号，已启用 ${enabledCount} 个</p>
        </div>
        <div class="source-actions">
          <button class="ghost-button compact-button" type="button" data-action="open-bulk-x">批量管理</button>
        </div>
      </div>
      <div class="x-source-list">
        ${sources
          .map(
            (source) => `
              <div class="x-source-item">
                <div class="x-source-item-main">
                  <div class="x-source-item-copy">
                    <div class="x-source-item-title-row">
                      <h4 class="x-source-item-title">${escapeHtml(source.name)}</h4>
                      <span class="status-tag" data-state="${source.health_status}">${escapeHtml(source.health_status)}</span>
                    </div>
                    <p class="source-subtitle">${escapeHtml(source.endpoint)}</p>
                    <div class="x-source-item-meta muted-text">
                      <span>分类 ${escapeHtml(source.category)}</span>
                      <span>轮询 ${source.poll_interval_minutes} 分钟</span>
                    </div>
                    ${source.last_error_message ? `<p class="muted-text">${escapeHtml(source.last_error_message)}</p>` : ""}
                  </div>
                  <div class="x-source-item-controls">
                    <button class="toggle-button ${source.enabled ? "is-active" : ""}" type="button" data-action="toggle" data-id="${source.source_id}" aria-label="${source.enabled ? "停用" : "启用"}">
                      <span class="toggle-slider"></span>
                    </button>
                  </div>
                </div>
                <div class="source-actions">
                  <button class="ghost-button compact-button" type="button" data-action="edit" data-id="${source.source_id}">编辑</button>
                  <button class="ghost-button compact-button" type="button" data-action="delete" data-id="${source.source_id}">删除</button>
                </div>
              </div>
            `
          )
          .join("")}
      </div>
    </article>
  `;
}

function renderSystemStatus() {
  if (!state.system) {
    elements.statusGrid.innerHTML = '<div class="empty-state">系统状态尚未加载。</div>';
    return;
  }

  elements.statusGrid.innerHTML = `
    <div class="status-row">
      <span class="status-label">网络状态</span>
      <span class="status-value">${state.system.network_status}</span>
    </div>
    <div class="status-row">
      <span class="status-label">LM Studio</span>
      <span class="status-value">${state.system.ai_status}</span>
    </div>
    <div class="status-row">
      <span class="status-label">AI Base URL</span>
      <span class="status-value">${state.system.ai_base_url || "未配置"}</span>
    </div>
    <div class="status-row">
      <span class="status-label">AI 模型</span>
      <span class="status-value">${state.system.ai_model || "未配置"}</span>
    </div>
    <div class="status-row">
      <span class="status-label">翻译 / 分类</span>
      <span class="status-value">${state.system.ai_translation_enabled ? "开" : "关"} / ${state.system.ai_classification_enabled ? "开" : "关"}</span>
    </div>
    <div class="status-row">
      <span class="status-label">AI 最近失败</span>
      <span class="status-value">${escapeHtml(state.system.ai_last_error_message || "暂无")}</span>
    </div>
    <div class="status-row">
      <span class="status-label">最近同步</span>
      <span class="status-value">${state.system.last_sync_at ? formatRelativeTime(state.system.last_sync_at) : "暂无"}</span>
    </div>
    <div class="status-row">
      <span class="status-label">最近清理</span>
      <span class="status-value">${state.system.last_cleanup_at ? formatRelativeTime(state.system.last_cleanup_at) : "暂无"}</span>
    </div>
    <div class="status-row">
      <span class="status-label">已启用来源</span>
      <span class="status-value">${state.system.enabled_sources}</span>
    </div>
    <div class="status-row">
      <span class="status-label">消息 / 聚合组</span>
      <span class="status-value">${state.system.stored_messages} / ${state.system.stored_clusters}</span>
    </div>
  `;
}

function renderAIConfig() {
  if (!state.aiConfig) {
    elements.aiConfigFeedback.textContent = "LM Studio 配置尚未加载。";
    return;
  }
  document.getElementById("ai-base-url").value = state.aiConfig.base_url || "http://127.0.0.1:1234";
  document.getElementById("ai-model").value = state.aiConfig.model || "";
  document.getElementById("ai-api-key").value = "";
  document.getElementById("ai-api-key").placeholder = state.aiConfig.has_api_key
    ? "已保存，留空表示继续使用当前值"
    : "留空表示不使用 API Key";
  document.getElementById("ai-clear-api-key").checked = false;
  document.getElementById("ai-translation-enabled").checked = state.aiConfig.translation_enabled;
  document.getElementById("ai-classification-enabled").checked = state.aiConfig.classification_enabled;
  document.getElementById("ai-categories").value = state.aiConfig.categories.join("\n");
  elements.aiConfigFeedback.textContent = state.aiConfig.has_api_key
    ? "已加载配置，可直接测试或保存。"
    : "当前未保存 API Key，可直接连接无鉴权的 LM Studio。";
}

function syncSourceFormState() {
  const sourceType = document.getElementById("source-type").value;
  const endpointInput = document.getElementById("source-endpoint");
  const methodSelect = document.getElementById("source-method");
  const categoryInput = document.getElementById("source-category");

  if (sourceType === "x") {
    endpointInput.placeholder = "https://x.com/OpenAI";
    methodSelect.value = "GET";
    methodSelect.disabled = true;
    if (!categoryInput.value || categoryInput.value === "其他") {
      categoryInput.value = "科技";
    }
    return;
  }

  endpointInput.placeholder = "https://example.com/feed.xml";
  methodSelect.disabled = false;
}

function populateForm(source) {
  const headers = source?.headers && Object.keys(source.headers).length
    ? JSON.stringify(source.headers, null, 2)
    : "";
  document.getElementById("source-id").value = source?.source_id || "";
  document.getElementById("source-name").value = source?.name || "";
  document.getElementById("source-type").value = source?.source_type || "api";
  document.getElementById("source-endpoint").value = source?.endpoint || "";
  document.getElementById("source-method").value = source?.method || "GET";
  document.getElementById("source-category").value = source?.category || "其他";
  document.getElementById("source-poll").value = source?.poll_interval_minutes || 15;
  document.getElementById("source-enabled").checked = source?.enabled ?? true;
  document.getElementById("source-headers").value = headers;
  elements.sourceModalTitle.textContent = source ? "编辑来源" : "新增来源";
  elements.sourceFeedback.textContent = "";
  syncSourceFormState();
}

function collectFormPayload() {
  const rawHeaders = document.getElementById("source-headers").value.trim();
  return {
    name: document.getElementById("source-name").value.trim(),
    source_type: document.getElementById("source-type").value,
    endpoint: document.getElementById("source-endpoint").value.trim(),
    method: document.getElementById("source-method").value.trim().toUpperCase(),
    category: document.getElementById("source-category").value.trim(),
    poll_interval_minutes: Number(document.getElementById("source-poll").value || 15),
    enabled: document.getElementById("source-enabled").checked,
    headers: JSON.parse(rawHeaders || "{}"),
  };
}

function populateBulkXForm() {
  document.getElementById("bulk-x-accounts").value = "";
  document.getElementById("bulk-x-category").value = "科技";
  document.getElementById("bulk-x-poll").value = 15;
  document.getElementById("bulk-x-enabled").checked = true;
  document.getElementById("bulk-x-sync-after-import").checked = true;
  elements.bulkXFeedback.textContent = "";
}

function collectBulkXPayload() {
  return {
    accounts_text: document.getElementById("bulk-x-accounts").value.trim(),
    category: document.getElementById("bulk-x-category").value.trim(),
    poll_interval_minutes: Number(document.getElementById("bulk-x-poll").value || 15),
    enabled: document.getElementById("bulk-x-enabled").checked,
    sync_after_import: document.getElementById("bulk-x-sync-after-import").checked,
  };
}

function parseAICategories(value) {
  return Array.from(
    new Set(
      value
        .split(/[\n,，]/)
        .map((item) => item.trim())
        .filter(Boolean)
    )
  );
}

function collectAIConfigPayload() {
  const apiKeyInput = document.getElementById("ai-api-key").value.trim();
  return {
    base_url: document.getElementById("ai-base-url").value.trim(),
    model: document.getElementById("ai-model").value.trim(),
    api_key: apiKeyInput || null,
    clear_api_key: document.getElementById("ai-clear-api-key").checked,
    translation_enabled: document.getElementById("ai-translation-enabled").checked,
    classification_enabled: document.getElementById("ai-classification-enabled").checked,
    categories: parseAICategories(document.getElementById("ai-categories").value),
  };
}

async function handleSourceAction(action, sourceId) {
  if (action === "open-bulk-x") {
    populateBulkXForm();
    elements.bulkXModal.showModal();
    return;
  }

  const source = state.sources.find((item) => item.source_id === sourceId);
  if (!source) {
    return;
  }

  if (action === "edit") {
    state.editingSourceId = sourceId;
    populateForm(source);
    elements.sourceModal.showModal();
    return;
  }

  if (action === "toggle") {
    await fetchJson(`/api/sources/${sourceId}/status`, {
      method: "PATCH",
      body: JSON.stringify({ enabled: !source.enabled }),
    });
    await refreshAll();
    return;
  }

  if (action === "delete") {
    await fetchJson(`/api/sources/${sourceId}`, { method: "DELETE" });
    await refreshAll();
  }
}

function buildTimelineSearchParams(offset = 0) {
  const search = new URLSearchParams();
  if (elements.categoryFilter.value) {
    search.set("category", elements.categoryFilter.value);
  }
  if (elements.importanceFilter.checked) {
    search.set("high_importance_only", "true");
  }
  search.set("sort_by", elements.sortFilter.value);
  search.set("limit", String(TIMELINE_PAGE_SIZE));
  search.set("offset", String(offset));
  return search;
}

async function loadTimeline({ append = false } = {}) {
  if (append && (!state.timelineHasMore || state.timelineLoadingMore)) {
    return;
  }
  const nextOffset = append ? state.clusters.length : 0;
  state.timelineLoadingMore = append;
  renderTimelineLoadMore();
  try {
    const payload = await fetchJson(
      `/api/timeline/clusters?${buildTimelineSearchParams(nextOffset).toString()}`
    );
    const incomingClusters = payload.clusters || [];
    if (append) {
      const existingIds = new Set(state.clusters.map((item) => item.cluster_id));
      state.clusters = state.clusters.concat(
        incomingClusters.filter((item) => !existingIds.has(item.cluster_id))
      );
    } else {
      state.clusters = incomingClusters;
    }
    state.timelineTotal = payload.total || state.clusters.length;
    state.timelineHasMore = Boolean(payload.has_more);
    if (!state.clusters.find((item) => item.cluster_id === state.selectedClusterId)) {
      state.selectedClusterId = null;
      delete elements.detailPanel.dataset.category;
    }
    renderTimeline();
  } finally {
    state.timelineLoadingMore = false;
    renderTimelineLoadMore();
  }
}

function observeTimelineLoadMore() {
  if (!elements.timelineLoadMore || !("IntersectionObserver" in window)) {
    return;
  }
  timelineLoadObserver = new IntersectionObserver(
    (entries) => {
      const [entry] = entries;
      if (!entry?.isIntersecting || !state.timelineHasMore || state.timelineLoadingMore) {
        return;
      }
      loadTimeline({ append: true }).catch(() => {
        state.timelineLoadingMore = false;
        renderTimelineLoadMore();
      });
    },
    {
      root: null,
      rootMargin: "280px 0px",
      threshold: 0,
    }
  );
  timelineLoadObserver.observe(elements.timelineLoadMore);
}

async function loadSources() {
  state.sources = await fetchJson("/api/sources");
  renderSources();
}

async function loadSystem() {
  state.system = await fetchJson("/api/system/status");
  renderSystemStatus();
}

async function loadAIConfig() {
  state.aiConfig = await fetchJson("/api/ai/config");
  renderAIConfig();
}

async function refreshAll() {
  await Promise.all([loadTimeline(), loadSources(), loadSystem()]);
}

function connectSocket() {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${window.location.host}/ws/stream`);
  socket.addEventListener("open", () => {
    elements.socketIndicator.dataset.state = "connected";
  });
  socket.addEventListener("message", async (event) => {
    const payload = JSON.parse(event.data);
    if (payload.type === "heartbeat") {
      return;
    }
    await refreshAll();
  });
  socket.addEventListener("close", () => {
    elements.socketIndicator.dataset.state = "disconnected";
    setTimeout(connectSocket, 2000);
  });
}

function bindEvents() {
  elements.categoryFilter.addEventListener("change", () => loadTimeline());
  elements.sortFilter.addEventListener("change", () => loadTimeline());
  elements.importanceFilter.addEventListener("change", () => loadTimeline());

  elements.settingsButton.addEventListener("click", async () => {
    const nextState = !state.settingsOpen;
    setSettingsOpen(nextState);
    if (nextState) {
      try {
        await loadAIConfig();
      } catch (error) {
        elements.aiConfigFeedback.textContent = error.message;
      }
    }
  });

  elements.closeSettingsButton.addEventListener("click", () => {
    setSettingsOpen(false);
  });

  document.addEventListener("click", (event) => {
    if (elements.settingsPanel.hidden) {
      return;
    }
    if (
      elements.settingsPanel.contains(event.target) ||
      elements.settingsButton.contains(event.target)
    ) {
      return;
    }
    setSettingsOpen(false);
  });

  elements.manualSyncButton.addEventListener("click", async () => {
    try {
      elements.syncFeedback.textContent = "正在同步并执行抓取后的 AI 处理…";
      const result = await fetchJson("/api/system/sync", { method: "POST" });
      await refreshAll();
      const messages = [
        `已同步 ${result.successful_sources}/${result.processed_sources} 个来源`,
      ];
      if (result.failed_sources) {
        messages.push(`抓取失败 ${result.failed_sources} 个来源`);
      }
      if (result.warning_sources) {
        messages.push(`AI 处理失败 ${result.warning_sources} 个来源`);
      }
      const aiError = result.results.find((item) => item.ai_error_message)?.ai_error_message;
      if (aiError) {
        messages.push(`原因：${aiError}`);
      }
      elements.syncFeedback.textContent = messages.join("；");
    } catch (error) {
      elements.syncFeedback.textContent = error.message;
    }
  });

  elements.bulkXImportButton.addEventListener("click", () => {
    populateBulkXForm();
    elements.bulkXModal.showModal();
  });

  elements.newSourceButton.addEventListener("click", () => {
    state.editingSourceId = null;
    populateForm(null);
    elements.sourceModal.showModal();
  });

  elements.closeModalButton.addEventListener("click", () => {
    elements.sourceModal.close();
  });

  elements.closeBulkXModalButton.addEventListener("click", () => {
    elements.bulkXModal.close();
  });

  document.getElementById("source-type").addEventListener("change", () => {
    syncSourceFormState();
  });

  elements.testSourceButton.addEventListener("click", async () => {
    try {
      const payload = collectFormPayload();
      const result = await fetchJson("/api/sources/test", {
        method: "POST",
        body: JSON.stringify({
          endpoint: payload.endpoint,
          method: payload.method,
          source_type: payload.source_type,
          category: payload.category,
          headers: payload.headers,
        }),
      });
      elements.sourceFeedback.textContent = result.message;
    } catch (error) {
      elements.sourceFeedback.textContent = error.message;
    }
  });

  elements.sourceForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const payload = collectFormPayload();
      if (state.editingSourceId) {
        await fetchJson(`/api/sources/${state.editingSourceId}`, {
          method: "PUT",
          body: JSON.stringify(payload),
        });
      } else {
        await fetchJson("/api/sources", {
          method: "POST",
          body: JSON.stringify(payload),
        });
      }
      elements.sourceModal.close();
      await refreshAll();
    } catch (error) {
      elements.sourceFeedback.textContent = error.message;
    }
  });

  elements.bulkXForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const payload = collectBulkXPayload();
      elements.bulkXFeedback.textContent = "正在导入账号…";
      const result = await fetchJson("/api/sources/bulk-x", {
        method: "POST",
        body: JSON.stringify({
          accounts_text: payload.accounts_text,
          category: payload.category,
          poll_interval_minutes: payload.poll_interval_minutes,
          enabled: payload.enabled,
        }),
      });
      let syncedSources = 0;
      if (payload.sync_after_import && result.created.length) {
        elements.bulkXFeedback.textContent = "导入完成，正在抓取最新消息…";
        for (const source of result.created) {
          await fetchJson("/api/system/sync", {
            method: "POST",
            body: JSON.stringify({ source_id: source.source_id }),
          });
          syncedSources += 1;
        }
      }
      elements.sortFilter.value = "latest";
      await refreshAll();
      const messages = [`已导入 ${result.created.length} 个账号`];
      if (payload.sync_after_import && syncedSources) {
        messages.push(`已同步 ${syncedSources} 个来源的最新消息`);
      }
      if (result.skipped_handles.length) {
        messages.push(`已跳过重复账号：${result.skipped_handles.join("、")}`);
      }
      elements.bulkXFeedback.textContent = messages.join("；");
    } catch (error) {
      elements.bulkXFeedback.textContent = error.message;
    }
  });

  elements.testAIButton.addEventListener("click", async () => {
    try {
      const payload = collectAIConfigPayload();
      elements.aiConfigFeedback.textContent = "正在测试 LM Studio…";
      const result = await fetchJson("/api/ai/test", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      const modelText = result.available_models?.length
        ? `；可用模型：${result.available_models.join("、")}`
        : "";
      elements.aiConfigFeedback.textContent = `${result.message}${modelText}`;
      await loadSystem();
    } catch (error) {
      elements.aiConfigFeedback.textContent = error.message;
    }
  });

  elements.aiConfigForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const payload = collectAIConfigPayload();
      elements.aiConfigFeedback.textContent = "正在保存 AI 配置…";
      state.aiConfig = await fetchJson("/api/ai/config", {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      renderAIConfig();
      await loadSystem();
      elements.aiConfigFeedback.textContent = "AI 配置已保存。";
    } catch (error) {
      elements.aiConfigFeedback.textContent = error.message;
    }
  });
}

async function bootstrap() {
  bindEvents();
  observeTimelineLoadMore();
  await refreshAll();
  connectSocket();
  window.setInterval(() => {
    refreshAll().catch(() => {
      elements.socketIndicator.dataset.state = "disconnected";
    });
  }, AUTO_REFRESH_INTERVAL_MS);
}

bootstrap();
