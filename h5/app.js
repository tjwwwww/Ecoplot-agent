const API_BASE = "";
const DEFAULT_SUBPLOT_ID = "0101";
const SPECIES_COLOR_MAP = {
  白桦: "#2f7fd1",
  红桦: "#c2410c",
  祁连圆柏: "#0f9f95",
  青海云杉: "#147d44",
  山杨: "#f47b20",
  乌柳: "#8b5cf6",
};
const SPECIES_COLORS = [
  "#147d44",
  "#f47b20",
  "#2f7fd1",
  "#8b5cf6",
  "#0f9f95",
  "#c2410c",
  "#4f46e5",
  "#be185d",
  "#0e7490",
  "#65a30d",
];

const CHAT_HISTORY_KEY = "forest-assistant-chat-history";
const CHAT_STATE_KEY = "forest-assistant-current-chat";
const CLIENT_ID_KEY = "forest-assistant-client-id";
const DEFAULT_USER_TEXT = "请输入问题开始对话";
const DEFAULT_ASSISTANT_TEXT = "我可以回答样方巡检、异常原因、树种结构和复核建议。";
const DEMO_CHAT_HISTORY = [
  {
    id: 101,
    title: "0316 样方倒木情况",
    userText: "0316 样方有哪些倒木？",
    assistantText: "0316 样方返回数据中有 3 株倒木和 1 株断头木。建议在样方图中查看对应点位，并进入样木清单核对具体编号。",
    time: "2026-07-16T09:00:00.000Z",
  },
];

const sampleStore = new Map();
let activeSampleId = DEFAULT_SUBPLOT_ID;
let selectedTreeId = "";
let enabledSpecies = new Set();
let currentClientId = getOrCreateClientId();
let currentSessionId = getSavedChatState().sessionId || createSessionId();
let currentConversationId = getSavedChatState().conversationId || Date.now();
let chatMessages = [];
let activeAssistantMessageId = "";
let isChatStreaming = false;
let speechRecognition = null;
let isVoiceListening = false;

const dom = {
  segments: document.querySelectorAll(".segment"),
  bottomTabs: document.querySelectorAll(".bottom-tab[data-view]"),
  chatView: document.querySelector("#chatView"),
  chatMessages: document.querySelector("#chatMessages"),
  mapView: document.querySelector("#mapView"),
  thinkingPanel: null,
  thinkingToggle: null,
  thinkingList: null,
  historyButton: document.querySelector("#historyButton"),
  moreButton: document.querySelector("#moreButton"),
  moreMenu: document.querySelector("#moreMenu"),
  newChatButton: document.querySelector("#newChatButton"),
  openHistoryButton: document.querySelector("#openHistoryButton"),
  historyPanel: document.querySelector("#historyPanel"),
  closeHistoryButton: document.querySelector("#closeHistoryButton"),
  historyList: document.querySelector("#historyList"),
  plotPanel: document.querySelector("#plotPanel"),
  insightPanel: document.querySelector("#insightPanel"),
  treeListPanel: document.querySelector("#treeListPanel"),
  treeDetailPanel: document.querySelector("#treeDetailPanel"),
  treeDetailTitle: document.querySelector("#treeDetailTitle"),
  treeDetailStatus: document.querySelector("#treeDetailStatus"),
  treeDetailGrid: document.querySelector("#treeDetailGrid"),
  plotTitle: document.querySelector("#plotTitle"),
  metricGrid: document.querySelector("#metricGrid"),
  plotCanvas: document.querySelector("#plotCanvas"),
  largePlotCanvas: document.querySelector("#largePlotCanvas"),
  selectedTreeSummary: document.querySelector("#selectedTreeSummary"),
  speciesLegend: document.querySelector("#speciesLegend"),
  dialogLegend: document.querySelector("#dialogLegend"),
  insightList: document.querySelector("#insightList"),
  sampleSearch: document.querySelector("#sampleSearch"),
  sampleIdInput: document.querySelector("#sampleIdInput"),
  sampleStatus: document.querySelector("#sampleStatus"),
  speciesFilter: document.querySelector("#speciesFilter"),
  treeList: document.querySelector("#treeList"),
  treeListTitle: document.querySelector("#treeListTitle"),
  treeCount: document.querySelector("#treeCount"),
  composer: document.querySelector("#composer"),
  queryInput: document.querySelector("#queryInput"),
  sendButton: document.querySelector(".send-button"),
  voiceButton: document.querySelector("#voiceButton"),
  listButton: document.querySelector("#listButton"),
  backPlotButton: document.querySelector("#backPlotButton"),
  backListButton: document.querySelector("#backListButton"),
  locateTreeButton: document.querySelector("#locateTreeButton"),
  zoomButton: document.querySelector("#zoomButton"),
  plotDialog: document.querySelector("#plotDialog"),
  closeDialog: document.querySelector("#closeDialog"),
  dialogTitle: document.querySelector("#dialogTitle"),
  surveyView: document.querySelector("#surveyView"),
  surveyHome: document.querySelector("#surveyHome"),
  surveyDetail: document.querySelector("#surveyDetail"),
  surveyList: document.querySelector("#surveyList"),
  newSurveyBtn: document.querySelector("#newSurveyBtn"),
  surveyBackBtn: document.querySelector("#surveyBackBtn"),
  surveyDetailTitle: document.querySelector("#surveyDetailTitle"),
  surveyDetailStatus: document.querySelector("#surveyDetailStatus"),
  surveyProgressBar: document.querySelector("#surveyProgressBar"),
  surveyProgressFill: document.querySelector("#surveyProgressFill"),
  surveyProgressText: document.querySelector("#surveyProgressText"),
  surveyAiAnalysis: document.querySelector("#surveyAiAnalysis"),
  surveyAiAnalysisText: document.querySelector("#surveyAiAnalysisText"),
  surveyRecList: document.querySelector("#surveyRecList"),
  surveyReportBtn: document.querySelector("#surveyReportBtn"),
  surveyObsDialog: document.querySelector("#surveyObsDialog"),
  surveyObsDialogTitle: document.querySelector("#surveyObsDialogTitle"),
  surveyObsDialogClose: document.querySelector("#surveyObsDialogClose"),
  surveyObsInfo: document.querySelector("#surveyObsInfo"),
  surveyObsNotes: document.querySelector("#surveyObsNotes"),
  surveyObsHealth: document.querySelector("#surveyObsHealth"),
  surveyObsPest: document.querySelector("#surveyObsPest"),
  surveyObsPheno: document.querySelector("#surveyObsPheno"),
  surveyObsSkip: document.querySelector("#surveyObsSkip"),
  surveyObsSave: document.querySelector("#surveyObsSave"),
  surveyNewDialog: document.querySelector("#surveyNewDialog"),
  surveyNewDialogClose: document.querySelector("#surveyNewDialogClose"),
  surveyNewRequest: document.querySelector("#surveyNewRequest"),
  surveyNewLoading: document.querySelector("#surveyNewLoading"),
  surveyNewCancel: document.querySelector("#surveyNewCancel"),
  surveyNewSubmit: document.querySelector("#surveyNewSubmit"),
};

function getOrCreateClientId() {
  const storageKey = "forestry_agent_client_id";
  let clientId = localStorage.getItem(storageKey);

  if (!clientId) {
    if (window.crypto && crypto.randomUUID) {
      clientId = crypto.randomUUID();
    } else {
      clientId = `client_${Date.now()}_${Math.random().toString(16).slice(2)}`;
    }
    localStorage.setItem(storageKey, clientId);
  }

  return clientId;
}

function apiUrl(subplotId) {
  const params = new URLSearchParams({
    sort_by: "tree_id",
    order: "asc",
    offset: "0",
    limit: "5000",
    include_unverified_volume: "false",
  });
  return `${API_BASE}/api/subplots/${encodeURIComponent(subplotId)}/trees?${params}`;
}

async function fetchSample(subplotId) {
  const response = await fetch(apiUrl(subplotId), {
    headers: { accept: "application/json" },
  });

  if (!response.ok) {
    throw new Error(`接口返回 ${response.status}`);
  }

  const payload = await response.json();
  const rows = Array.isArray(payload) ? payload : payload.items || payload.data || [];
  return normalizeSample(subplotId, rows);
}

function normalizeSample(subplotId, rows) {
  const trees = rows.map((row, index) => {
    const x = toNumber(row.tree_x_m);
    const y = toNumber(row.tree_y_m);
    const species = row.species || "未知树种";
    const healthStatus = row.health_status || "未知";

    return {
      id: row.tree_id || `${subplotId}-${String(index + 1).padStart(4, "0")}`,
      subplotId: row.subplot_id || subplotId,
      species,
      healthStatus,
      healthClass: healthClass(healthStatus),
      dbh: toNumber(row.tree_dbh_cm),
      height: toNumber(row.tree_height_m),
      x,
      y,
      crownWidth: toNumber(row.crown_width_mean_m),
      crownBaseHeight: toNumber(row.crown_base_height_m),
      remarks: row.remarks || "",
      databaseVolume: toNumber(row.database_volume_m3),
      volumeStatus: row.volume_status || "",
      volumeRecommended: Boolean(row.volume_display_recommended),
      raw: row,
    };
  });

  const xs = trees.map((tree) => tree.x).filter(Number.isFinite);
  const ys = trees.map((tree) => tree.y).filter(Number.isFinite);
  const minX = xs.length ? Math.min(...xs) : 0;
  const maxX = xs.length ? Math.max(...xs) : 1;
  const minY = ys.length ? Math.min(...ys) : 0;
  const maxY = ys.length ? Math.max(...ys) : 1;
  const xRange = Math.max(maxX - minX, 1);
  const yRange = Math.max(maxY - minY, 1);

  trees.forEach((tree) => {
    tree.localX = Number.isFinite(tree.x) ? round1(tree.x - minX) : null;
    tree.localY = Number.isFinite(tree.y) ? round1(tree.y - minY) : null;
  });

  const species = uniqueValues(trees.map((tree) => tree.species));
  const abnormalCount = trees.filter((tree) => tree.healthClass !== "healthy").length;

  return {
    id: subplotId,
    trees,
    species,
    extent: { minX, maxX, minY, maxY, xRange, yRange },
    insights: buildInsights(subplotId, trees, species.length, abnormalCount),
  };
}

function buildInsights(subplotId, trees, speciesCount, abnormalCount) {
  if (!trees.length) {
    return [`样方 ${subplotId} 暂无样木记录。`];
  }

  const dominantSpecies = mostCommon(trees.map((tree) => tree.species));
  return [
    `样方 ${subplotId} 共记录 ${trees.length} 株单木，包含 ${speciesCount} 种树种。`,
    `主要树种为 ${dominantSpecies}，图中点位颜色按树种区分。`,
    abnormalCount ? `非健康状态样木 ${abnormalCount} 株，建议优先查看清单中的健康状态。` : "当前返回样木健康状态均为健康。",
  ];
}

function mostCommon(values) {
  const counts = new Map();
  values.forEach((value) => counts.set(value, (counts.get(value) || 0) + 1));
  return [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] || "未知";
}

function toNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function round1(value) {
  return Math.round(value * 10) / 10;
}

function uniqueValues(values) {
  return [...new Set(values.filter(Boolean))];
}

function getSample() {
  return sampleStore.get(activeSampleId);
}

function getVisibleTrees(sample = getSample()) {
  if (!sample) return [];
  return sample.trees.filter((tree) => enabledSpecies.has(tree.species));
}

function uniqueSpecies(sample = getSample()) {
  return sample ? sample.species : [];
}

function speciesMeta(speciesName) {
  const species = speciesName || "未知树种";
  if (SPECIES_COLOR_MAP[species]) {
    return { name: species, color: SPECIES_COLOR_MAP[species] };
  }

  const index = Math.abs(hashCode(species)) % SPECIES_COLORS.length;
  return { name: species, color: SPECIES_COLORS[index] };
}

function hashCode(value) {
  return [...value].reduce((hash, char) => ((hash << 5) - hash + char.charCodeAt(0)) | 0, 0);
}

function healthClass(status) {
  if (!status) return "warning";
  if (/枯|死|死亡/.test(status)) return "dead";
  if (/异常|病|虫|差|衰弱|倒|断|折|伤|倾/.test(status)) return "danger";
  if (/关注|亚健康|一般/.test(status)) return "warning";
  return "healthy";
}

function renderAll() {
  const sample = getSample();
  if (!sample) return;

  const speciesCount = uniqueSpecies(sample).length;
  const healthyCount = sample.trees.filter((tree) => tree.healthClass === "healthy").length;

  dom.plotTitle.textContent = `样方 ${sample.id} 空间分布`;
  dom.dialogTitle.textContent = `样方 ${sample.id} 空间分布`;
  dom.sampleIdInput.value = sample.id;
  dom.sampleStatus.textContent = `当前样方 ${sample.id}，共 ${sample.trees.length} 株单木，${speciesCount} 种树种。`;

  dom.metricGrid.innerHTML = [
    metricTemplate("trees", "trees", `单木 ${sample.trees.length}株`),
    metricTemplate("alert", "circle-check", `健康 ${healthyCount}株`),
    metricTemplate("species", "leaf", `树种 ${speciesCount}种`),
  ].join("");

  dom.insightList.innerHTML = sample.insights.map((item) => `<li>${escapeHtml(item)}</li>`).join("");

  renderLegends(sample);
  renderSpeciesFilter(sample);
  renderTreeList(sample);
  renderSelectedTreeSummary(sample);
  drawPlot(dom.plotCanvas, sample, getVisibleTrees(sample), selectedTreeId);
  drawPlot(dom.largePlotCanvas, sample, getVisibleTrees(sample), selectedTreeId);
  renderIcons();
}

function metricTemplate(type, icon, label) {
  return `<div class="metric ${type}"><i data-lucide="${icon}"></i><span>${label}</span></div>`;
}

function renderLegends(sample) {
  const html = uniqueSpecies(sample)
    .map((species) => {
      const item = speciesMeta(species);
      return `<span class="legend-item"><span class="legend-dot" style="--dot:${item.color}"></span>${escapeHtml(item.name)}</span>`;
    })
    .join("");

  dom.speciesLegend.innerHTML = html;
  dom.dialogLegend.innerHTML = html;
}

function renderSpeciesFilter(sample) {
  dom.speciesFilter.innerHTML = uniqueSpecies(sample)
    .map((species) => {
      const item = speciesMeta(species);
      const checked = enabledSpecies.has(species) ? "checked" : "";
      return `
        <label class="species-chip" style="--dot:${item.color}">
          <input type="checkbox" value="${escapeAttribute(species)}" ${checked} />
          <span class="legend-dot" style="--dot:${item.color}"></span>
          ${escapeHtml(item.name)}
        </label>
      `;
    })
    .join("");
}

function renderTreeList(sample = getSample()) {
  if (!sample) return;
  const trees = getVisibleTrees(sample);
  dom.treeListTitle.textContent = `样方 ${sample.id} 样木清单`;
  dom.treeCount.textContent = `${trees.length}/${sample.trees.length} 株`;

  if (!trees.length) {
    dom.treeList.innerHTML = `<div class="empty-state">当前树种筛选下没有样木。</div>`;
    return;
  }

  dom.treeList.innerHTML = trees
    .map((tree) => {
      const species = speciesMeta(tree.species);
      return `
        <button class="tree-item" type="button" data-tree-id="${escapeAttribute(tree.id)}">
          <span class="tree-dot" style="--dot:${species.color}"></span>
          <span>
            <span class="tree-id">${escapeHtml(tree.id)} · ${escapeHtml(species.name)}</span>
            <span class="tree-meta">胸径 ${formatValue(tree.dbh, "cm")} · 树高 ${formatValue(tree.height, "m")} · 坐标 ${formatValue(tree.localX, "m")}, ${formatValue(tree.localY, "m")}</span>
          </span>
          <span class="status-pill ${tree.healthClass}">${escapeHtml(tree.healthStatus)}</span>
        </button>
      `;
    })
    .join("");
}

function renderSelectedTreeSummary(sample = getSample()) {
  if (!sample || !dom.selectedTreeSummary) return;
  const tree = sample.trees.find((entry) => entry.id === selectedTreeId);

  if (!tree) {
    dom.selectedTreeSummary.innerHTML = `<span>点击图中树点查看编号和健康程度</span>`;
    return;
  }

  dom.selectedTreeSummary.innerHTML = `
    <span><strong>${escapeHtml(tree.id)}</strong> · ${escapeHtml(tree.species)} · 胸径 ${formatValue(tree.dbh, "cm")}</span>
    <span class="status-pill ${tree.healthClass}">${escapeHtml(tree.healthStatus)}</span>
  `;
}

function renderTreeDetail(tree) {
  selectedTreeId = tree.id;
  dom.treeDetailTitle.textContent = `${tree.id} 详情`;
  dom.treeDetailStatus.textContent = tree.healthStatus;
  dom.treeDetailStatus.className = tree.healthClass;

  const rows = [
    ["树种", tree.species],
    ["健康状态", tree.healthStatus],
    ["胸径", formatValue(tree.dbh, "cm")],
    ["树高", formatValue(tree.height, "m")],
    ["平均冠幅", formatValue(tree.crownWidth, "m")],
    ["枝下高", formatValue(tree.crownBaseHeight, "m")],
    ["样方内坐标", `${formatValue(tree.localX, "m")}, ${formatValue(tree.localY, "m")}`],
    ["原始坐标", `${formatValue(tree.x, "m")}, ${formatValue(tree.y, "m")}`],
    ["材积", formatValue(tree.databaseVolume, "m³")],
    ["材积状态", tree.volumeStatus || "无"],
    ["备注", tree.remarks || "无"],
  ];

  dom.treeDetailGrid.innerHTML = rows
    .map(([label, value], index) => {
      const wide = index >= 6 ? " wide" : "";
      return `
        <div class="detail-field${wide}">
          <span class="detail-label">${escapeHtml(label)}</span>
          <span class="detail-value">${escapeHtml(value)}</span>
        </div>
      `;
    })
    .join("");

  showTreeDetailPanel();
  renderIcons();
}

function plotLayout(canvas, sample) {
  const rect = canvas.getBoundingClientRect();
  const fallbackWidth = canvas.id === "largePlotCanvas" ? 760 : 360;
  const width = Math.max(rect.width || fallbackWidth, 320);
  const height = canvas.id === "largePlotCanvas" ? Math.min(width * 0.82, 620) : width * 0.78;
  const padding = { top: 20, right: 18, bottom: 42, left: 48 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;

  return {
    width,
    height,
    padding,
    plotWidth,
    plotHeight,
    xRange: sample.extent.xRange,
    yRange: sample.extent.yRange,
  };
}

function treePoint(tree, layout) {
  if (!Number.isFinite(tree.localX) || !Number.isFinite(tree.localY)) return null;
  return {
    tree,
    x: layout.padding.left + (tree.localX / layout.xRange) * layout.plotWidth,
    y: layout.padding.top + layout.plotHeight - (tree.localY / layout.yRange) * layout.plotHeight,
  };
}

function drawPlot(canvas, sample, trees, activeTreeId) {
  const ctx = canvas.getContext("2d");
  const layout = plotLayout(canvas, sample);
  const dpr = window.devicePixelRatio || 1;

  canvas.width = Math.floor(layout.width * dpr);
  canvas.height = Math.floor(layout.height * dpr);
  canvas.style.height = `${layout.height}px`;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, layout.width, layout.height);

  drawGrid(ctx, layout.padding, layout.plotWidth, layout.plotHeight, layout.xRange, layout.yRange);

  if (!trees.length) {
    drawEmptyPlot(ctx, layout.width, layout.height);
    return;
  }

  trees.forEach((tree) => {
    const point = treePoint(tree, layout);
    if (!point) return;
    ctx.beginPath();
    ctx.fillStyle = speciesMeta(tree.species).color;
    ctx.arc(point.x, point.y, tree.id === activeTreeId ? 6 : 4.5, 0, Math.PI * 2);
    ctx.fill();

    if (tree.healthClass !== "healthy") {
      ctx.lineWidth = 1.6;
      ctx.strokeStyle = tree.healthClass === "dead" ? "#7d8581" : "#db2c24";
      ctx.stroke();
    }
  });

  const activeTree = trees.find((tree) => tree.id === activeTreeId);
  if (activeTree) {
    drawActiveLabel(ctx, activeTree, layout);
  }
}

function drawGrid(ctx, padding, plotWidth, plotHeight, xRange, yRange) {
  ctx.save();
  ctx.strokeStyle = "#d7ddda";
  ctx.lineWidth = 1;
  ctx.setLineDash([4, 4]);

  for (let step = 0; step <= 4; step += 1) {
    const x = padding.left + (plotWidth / 4) * step;
    const y = padding.top + (plotHeight / 4) * step;
    ctx.beginPath();
    ctx.moveTo(x, padding.top);
    ctx.lineTo(x, padding.top + plotHeight);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(padding.left + plotWidth, y);
    ctx.stroke();
  }

  ctx.setLineDash([]);
  ctx.strokeStyle = "#154a35";
  ctx.lineWidth = 1.4;
  ctx.strokeRect(padding.left, padding.top, plotWidth, plotHeight);

  ctx.fillStyle = "#2b312f";
  ctx.font = "12px -apple-system, BlinkMacSystemFont, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  for (let step = 0; step <= 4; step += 1) {
    const xValue = round1((xRange / 4) * step);
    const yValue = round1((yRange / 4) * step);
    const x = padding.left + (plotWidth / 4) * step;
    const y = padding.top + plotHeight - (plotHeight / 4) * step;
    ctx.fillText(`${xValue}m`, x, padding.top + plotHeight + 10);
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    ctx.fillText(`${yValue}m`, padding.left - 8, y);
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
  }
  ctx.restore();
}

function drawEmptyPlot(ctx, width, height) {
  ctx.save();
  ctx.fillStyle = "#707a76";
  ctx.font = "14px -apple-system, BlinkMacSystemFont, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText("暂无可展示的样木坐标", width / 2, height / 2);
  ctx.restore();
}

function drawActiveLabel(ctx, tree, layout) {
  const point = treePoint(tree, layout);
  if (!point) return;
  const label = tree.id;
  const status = tree.healthStatus || "未知";
  ctx.save();
  ctx.strokeStyle = "#147d44";
  ctx.lineWidth = 2.5;
  ctx.beginPath();
  ctx.arc(point.x, point.y, 12, 0, Math.PI * 2);
  ctx.stroke();

  ctx.font = "13px -apple-system, BlinkMacSystemFont, sans-serif";
  const labelWidth = Math.max(ctx.measureText(label).width, ctx.measureText(status).width) + 18;
  const boxX = Math.min(point.x + 10, layout.padding.left + layout.plotWidth - labelWidth);
  const boxY = Math.max(point.y + 16, layout.padding.top + 6);
  ctx.fillStyle = "#0f6a45";
  roundRect(ctx, boxX, boxY, labelWidth, 42, 4);
  ctx.fill();
  ctx.fillStyle = "#fff";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(label, boxX + labelWidth / 2, boxY + 13);
  ctx.font = "12px -apple-system, BlinkMacSystemFont, sans-serif";
  ctx.fillText(status, boxX + labelWidth / 2, boxY + 29);
  ctx.restore();
}

function pickTreeFromCanvas(event, canvas) {
  const sample = getSample();
  if (!sample) return null;

  const rect = canvas.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  const layout = plotLayout(canvas, sample);
  const points = getVisibleTrees(sample)
    .map((tree) => treePoint(tree, layout))
    .filter(Boolean);

  let closest = null;
  let closestDistance = Infinity;
  points.forEach((point) => {
    const distance = Math.hypot(point.x - x, point.y - y);
    if (distance < closestDistance) {
      closest = point.tree;
      closestDistance = distance;
    }
  });

  return closestDistance <= 18 ? closest : null;
}

function selectTreeOnPlot(tree) {
  selectedTreeId = tree.id;
  renderSelectedTreeSummary();
  drawPlot(dom.plotCanvas, getSample(), getVisibleTrees(), selectedTreeId);
  if (dom.plotDialog.open) {
    drawPlot(dom.largePlotCanvas, getSample(), getVisibleTrees(), selectedTreeId);
  }
}

function roundRect(ctx, x, y, width, height, radius) {
  ctx.beginPath();
  ctx.moveTo(x + radius, y);
  ctx.arcTo(x + width, y, x + width, y + height, radius);
  ctx.arcTo(x + width, y + height, x, y + height, radius);
  ctx.arcTo(x, y + height, x, y, radius);
  ctx.arcTo(x, y, x + width, y, radius);
  ctx.closePath();
}

function switchView(view) {
  const isChat = view === "chat";
  const isMap = view === "map";
  const isSurvey = view === "survey";
  dom.chatView.classList.toggle("active", isChat);
  dom.mapView.classList.toggle("active", isMap);
  dom.surveyView.classList.toggle("active", isSurvey);

  [...dom.segments, ...dom.bottomTabs].forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
  });

  requestAnimationFrame(() => {
    if (isMap && getSample()) {
      drawPlot(dom.plotCanvas, getSample(), getVisibleTrees(), selectedTreeId);
    }
    if (isSurvey) {
      loadSurveyList();
    }
  });
}

async function selectSample(subplotId) {
  if (!subplotId) return false;
  const normalizedId = subplotId.trim().toUpperCase();
  setLoading(true, `正在加载样方 ${normalizedId}...`);

  try {
    const sample = await fetchSample(normalizedId);
    sampleStore.set(normalizedId, sample);
    activeSampleId = normalizedId;
    selectedTreeId = sample.trees[0]?.id || "";
    enabledSpecies = new Set(uniqueSpecies(sample));
    renderAll();
    showPlotPanel();
    setLoading(false, `已加载样方 ${normalizedId}，共 ${sample.trees.length} 株单木。`);
    return true;
  } catch (error) {
    setLoading(false, `加载样方 ${normalizedId} 失败：${error.message}`);
    return false;
  }
}

function setLoading(isLoading, message) {
  dom.sampleStatus.textContent = message;
  dom.sampleIdInput.disabled = isLoading;
  dom.sampleSearch.querySelector("button").disabled = isLoading;
}

function parseSampleId(text) {
  const match = text.trim().toUpperCase().match(/[A-Z0-9-]*\d[A-Z0-9-]*/);
  return match ? match[0] : "";
}

function renderIcons() {
  if (window.lucide) {
    window.lucide.createIcons();
  }
}

function showPlotPanel() {
  dom.plotPanel.classList.remove("hidden");
  dom.insightPanel.classList.remove("hidden");
  dom.treeListPanel.classList.add("hidden");
  dom.treeDetailPanel.classList.add("hidden");
  requestAnimationFrame(() => {
    if (getSample()) drawPlot(dom.plotCanvas, getSample(), getVisibleTrees(), selectedTreeId);
  });
}

function showTreeListPanel() {
  renderTreeList();
  dom.plotPanel.classList.add("hidden");
  dom.insightPanel.classList.add("hidden");
  dom.treeDetailPanel.classList.add("hidden");
  dom.treeListPanel.classList.remove("hidden");
}

function showTreeDetailPanel() {
  dom.plotPanel.classList.add("hidden");
  dom.insightPanel.classList.add("hidden");
  dom.treeListPanel.classList.add("hidden");
  dom.treeDetailPanel.classList.remove("hidden");
}

function safeStorageGet(key, fallback = "") {
  try {
    if (!window.localStorage) return fallback;
    const value = window.localStorage.getItem(key);
    return value === null ? fallback : value;
  } catch (error) {
    console.warn("localStorage 读取失败", error);
    return fallback;
  }
}

function safeStorageSet(key, value) {
  try {
    if (!window.localStorage) return false;
    window.localStorage.setItem(key, value);
    return true;
  } catch (error) {
    console.warn("localStorage 写入失败", error);
    return false;
  }
}

function getChatHistory() {
  try {
    const stored = JSON.parse(safeStorageGet(CHAT_HISTORY_KEY, "[]"));
    return Array.isArray(stored) ? stored : [];
  } catch {
    return [];
  }
}

function setChatHistory(history) {
  safeStorageSet(CHAT_HISTORY_KEY, JSON.stringify(history.slice(0, 30)));
}

function getSavedChatState() {
  try {
    const stored = JSON.parse(safeStorageGet(CHAT_STATE_KEY, "{}"));
    return stored && typeof stored === "object" ? stored : {};
  } catch {
    return {};
  }
}

function saveChatState() {
  safeStorageSet(
    CHAT_STATE_KEY,
    JSON.stringify({
      sessionId: currentSessionId,
      conversationId: currentConversationId,
      clientId: currentClientId,
    }),
  );
}

function seedDemoHistory() {
  const history = getChatHistory();
  if (history.length) return;
  setChatHistory(DEMO_CHAT_HISTORY);
}

function createMessage(role, content, extra = {}) {
  return {
    id: extra.id || `msg_${Date.now().toString(16)}_${Math.random().toString(16).slice(2, 8)}`,
    role,
    content: String(content || ""),
    time: extra.time || new Date().toISOString(),
    artifacts: Array.isArray(extra.artifacts) ? extra.artifacts : [],
    toolCalls: Array.isArray(extra.toolCalls) ? extra.toolCalls : [],
  };
}

function getFirstUserText() {
  const message = chatMessages.find((item) => item.role === "user");
  return message ? message.content || "" : "";
}

function getLastAssistantText() {
  const assistants = chatMessages.filter((item) => item.role === "assistant");
  return assistants.length ? assistants[assistants.length - 1].content || "" : "";
}

function currentChatSnapshot() {
  const userText = getFirstUserText();
  const assistantText = getLastAssistantText();
  return {
    id: currentConversationId,
    sessionId: currentSessionId,
    title: userText || "新对话",
    userText,
    assistantText,
    messages: chatMessages,
    time: new Date().toISOString(),
  };
}

function saveCurrentChat() {
  const snapshot = currentChatSnapshot();
  if (!snapshot.userText || snapshot.userText === DEFAULT_USER_TEXT || snapshot.userText === "新对话") return;

  const history = getChatHistory();
  const existingIndex = history.findIndex((item) => String(item.id) === String(snapshot.id));
  if (existingIndex >= 0) {
    history[existingIndex] = { ...history[existingIndex], ...snapshot, title: history[existingIndex].title || snapshot.title };
    setChatHistory([history[existingIndex], ...history.filter((_, index) => index !== existingIndex)]);
    return;
  }
  setChatHistory([snapshot, ...history]);
}

function ensureChatMessagesContainer() {
  if (!dom.chatMessages && dom.chatView) {
    dom.chatMessages = document.createElement("div");
    dom.chatMessages.className = "chat-messages";
    dom.chatMessages.id = "chatMessages";
    dom.chatView.appendChild(dom.chatMessages);
  }
  return dom.chatMessages;
}

function renderWelcomeChat() {
  chatMessages = [createMessage("assistant", DEFAULT_ASSISTANT_TEXT, { id: "welcome", time: new Date().toISOString() })];
  activeAssistantMessageId = "";
  renderChatMessages();
}

function startNewChat() {
  saveCurrentChat();
  currentSessionId = createSessionId();
  currentConversationId = Date.now();
  saveChatState();
  renderWelcomeChat();
  dom.queryInput.value = "";
  closeMoreMenu();
  closeHistoryPanel();
  switchView("chat");
}

async function renderHistoryList() {
  const history = await fetchRemoteSessionList();
  if (!history.length) {
    dom.historyList.innerHTML = `<div class="empty-state">\u6682\u65e0\u5386\u53f2\u8bb0\u5f55</div>`;
    return;
  }
  dom.historyList.innerHTML = history.map((item) => `
    <button class="history-item" type="button" data-session-id="${item.session_id}">
      <i data-lucide="message-square-text"></i>
      <span>
        <span class="history-title">${escapeHtml(item.title || "\u672a\u547d\u540d\u5bf9\u8bdd")}</span>
        <span class="history-meta">${escapeHtml(formatHistoryTime(item.updated_at || item.created_at))}</span>
      </span>
    </button>
  `).join("");
  renderIcons();
}

async function openHistoryPanel() {
  closeMoreMenu();
  await renderHistoryList();
  dom.historyPanel.classList.remove("hidden");
}

function closeHistoryPanel() {
  dom.historyPanel.classList.add("hidden");
}

function closeMoreMenu() {
  dom.moreMenu.classList.add("hidden");
}

function normalizeStoredMessages(item) {
  if (Array.isArray(item && item.messages) && item.messages.length) {
    return item.messages.map((message) => createMessage(
      message.role === "user" ? "user" : "assistant",
      message.content || "",
      { id: message.id, time: message.time, artifacts: message.artifacts, toolCalls: message.toolCalls || message.tool_calls },
    ));
  }
  const restored = [];
  if (item && item.userText) restored.push(createMessage("user", item.userText, { time: item.time }));
  if (item && item.assistantText) restored.push(createMessage("assistant", item.assistantText, { time: item.time }));
  return restored.length ? restored : [createMessage("assistant", DEFAULT_ASSISTANT_TEXT, { id: "welcome" })];
}

async function fetchRemoteSessionMessages(sessionId) {
  if (!sessionId) return [];
  const response = await fetch(`/api/agent/sessions/${encodeURIComponent(sessionId)}/messages?limit=200`);
  if (!response.ok) return [];
  const payload = await response.json();
  const messages = Array.isArray(payload.messages) ? payload.messages : [];
  return messages.map((message) => createMessage(
    message.role === "user" ? "user" : "assistant",
    message.content || "",
    { time: message.created_at || new Date().toISOString(), artifacts: message.artifacts, toolCalls: message.tool_calls },
  ));
}

async function fetchRemoteSessionList() {
  const params = new URLSearchParams({ limit: "50" });
  if (currentClientId) params.set("client_id", currentClientId);
  const response = await fetch(`/api/agent/sessions?${params.toString()}`);
  if (!response.ok) return [];
  const payload = await response.json();
  return Array.isArray(payload.sessions) ? payload.sessions : [];
}

async function restoreHistoryItem(sessionId) {
  if (!sessionId) return;
  const messages = await fetchRemoteSessionMessages(sessionId);
  currentSessionId = sessionId;
  currentConversationId = Date.now();
  saveChatState();
  chatMessages = messages.length ? messages : [createMessage("assistant", DEFAULT_ASSISTANT_TEXT, { id: "welcome" })];
  activeAssistantMessageId = "";
  renderChatMessages();
  closeHistoryPanel();
  switchView("chat");
}


async function restoreCurrentChatOnLoad() {
  const saved = getSavedChatState();
  if (saved.clientId && saved.clientId !== currentClientId) {
    currentClientId = saved.clientId;
  }

  if (saved.sessionId) {
    const rememberedMessages = await fetchRemoteSessionMessages(saved.sessionId);
    if (rememberedMessages.length) {
      currentSessionId = saved.sessionId;
      currentConversationId = saved.conversationId || Date.now();
      chatMessages = rememberedMessages;
      activeAssistantMessageId = "";
      renderChatMessages();
      saveChatState();
      return;
    }
  }

  const sessions = await fetchRemoteSessionList();
  const matched = sessions[0] || null;

  if (matched) {
    currentSessionId = matched.session_id || currentSessionId;
    currentConversationId = saved.conversationId || Date.now();
    const messages = await fetchRemoteSessionMessages(currentSessionId);
    chatMessages = messages.length ? messages : [createMessage("assistant", DEFAULT_ASSISTANT_TEXT, { id: "welcome" })];
    activeAssistantMessageId = "";
    renderChatMessages();
  } else {
    renderWelcomeChat();
  }

  saveChatState();
}

async function answerChat(text) {
  if (!text || isChatStreaming) return;
  saveChatState();
  if (chatMessages.length === 1 && chatMessages[0].id === "welcome") chatMessages = [];
  const userMessage = createMessage("user", text);
  const assistantMessage = createMessage("assistant", "正在思考...");
  chatMessages.push(userMessage, assistantMessage);
  activeAssistantMessageId = assistantMessage.id;
  renderChatMessages();
  saveCurrentChat();
  saveChatState();
  resetThinking();
  setChatStreaming(true);
  try {
    const answer = await streamAgentAnswer(text);
    updateAssistantMessage(activeAssistantMessageId, answer || "未收到回答");
    collapseThinking();
    saveCurrentChat();
    saveChatState();
  } catch (error) {
    updateAssistantMessage(activeAssistantMessageId, `问答接口调用失败：${error.message}`);
    collapseThinking();
    saveCurrentChat();
    saveChatState();
  } finally {
    setChatStreaming(false);
  }
}

async function streamAgentAnswer(question) {
  const response = await fetch("/api/agent/chat/stream", {
    method: "POST",
    headers: {
      accept: "application/x-ndjson",
      "content-type": "application/json; charset=utf-8",
    },
    body: JSON.stringify({
      session_id: currentSessionId,
      client_id: currentClientId,
      question,
      mode: "chat",
      context: {
        current_page: "chat",
        context_policy: "auto",
        active_subplot_id: activeSampleId,
        selected_tree_id: selectedTreeId,
      },
      max_rounds: 6,
      include_plan: false,
      include_candidates: false,
      include_debug: false,
    }),
  });
  console.debug("agent session_id", currentSessionId);

  if (!response.ok) {
    throw new Error(`接口返回 ${response.status}`);
  }

  if (!response.body) {
    return response.text();
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let answer = "";
  let hasFinalAnswer = false;

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split(/\r?\n/);
    buffer = lines.pop() || "";

    for (const line of lines) {
      const update = parseStreamLine(line);
      if (update.sessionId) {
        currentSessionId = update.sessionId;
        saveChatState();
      }
      if (update.thought) {
        appendThinking(update.thought, update.status);
      }
      if (update.artifacts && update.artifacts.length) {
        updateAssistantMessage(activeAssistantMessageId, answer, { artifacts: update.artifacts, toolCalls: update.toolCalls || [] });
      }
      if (!update.text) continue;
      if (hasFinalAnswer && update.mode === "append") continue;
      answer = update.mode === "append" ? answer + update.text : update.text;
      hasFinalAnswer = update.mode === "final" || hasFinalAnswer;
      updateAssistantMessage(activeAssistantMessageId, answer, { artifacts: update.artifacts || [], toolCalls: update.toolCalls || [] });
    }
  }

  if (buffer.trim()) {
    const update = parseStreamLine(buffer);
    if (update.sessionId) {
      currentSessionId = update.sessionId;
      saveChatState();
    }
    if (update.thought) {
      appendThinking(update.thought, update.status);
    }
    if (update.artifacts && update.artifacts.length) {
      updateAssistantMessage(activeAssistantMessageId, answer, { artifacts: update.artifacts, toolCalls: update.toolCalls || [] });
    }
    if (update.text) {
      if (!(hasFinalAnswer && update.mode === "append")) {
        answer = update.mode === "append" ? answer + update.text : update.text;
      }
    }
  }

  return answer.trim();
}

function parseStreamLine(line) {
  const normalized = line.trim().replace(/^data:\s*/, "");
  if (!normalized || normalized === "[DONE]") return emptyStreamUpdate();

  try {
    const payload = JSON.parse(normalized);
    return extractStreamText(payload);
  } catch {
    return { text: normalized, mode: "append" };
  }
}

function extractStreamText(payload) {
  if (!payload || typeof payload !== "object") return emptyStreamUpdate();
  if (payload.done || payload.event === "done" || payload.type === "done") return emptyStreamUpdate();

  if (payload.type === "stage") {
    const data = payload.data || {};
    return {
      ...emptyStreamUpdate(),
      thought: data.message ? `${data.stage || "阶段"}：${data.message}` : data.stage || "",
      status: data.status || "",
    };
  }

  if (payload.type === "model_round") {
    const data = payload.data || {};
    return {
      ...emptyStreamUpdate(),
      thought: data.message || `第${data.round || ""}轮推理`,
      status: "running",
    };
  }

  if (payload.type === "answer_chunk") {
    return { text: payload.data?.text || payload.text || "", mode: "append" };
  }

  if (payload.type === "final") {
    const data = payload.data || payload;
    return {
      text: data.answer || payload.answer || "",
      mode: "final",
      artifacts: Array.isArray(data.artifacts) ? data.artifacts : [],
      toolCalls: Array.isArray(data.used_tools) ? data.used_tools : [],
    };
  }

  if (payload.type === "session") {
    return { ...emptyStreamUpdate(), sessionId: payload.data?.session_id || payload.session_id || "" };
  }

  if (["start", "heartbeat"].includes(payload.type)) {
    return emptyStreamUpdate();
  }

  const choiceDelta = payload.choices?.[0]?.delta?.content;
  if (typeof choiceDelta === "string") return { text: choiceDelta, mode: "append" };

  const appendKeys = ["delta", "token", "chunk"];
  for (const key of appendKeys) {
    if (typeof payload[key] === "string") return { text: payload[key], mode: "append" };
  }

  if (payload.data && typeof payload.data === "object") {
    const nested = extractStreamText(payload.data);
    if (nested.text) return nested;
  }

  const replaceKeys = ["answer", "final_answer", "response", "result"];
  for (const key of replaceKeys) {
    if (typeof payload[key] === "string") return { text: payload[key], mode: "final" };
  }

  const appendTextKeys = ["content", "message", "text"];
  for (const key of appendTextKeys) {
    if (typeof payload[key] === "string") return { text: payload[key], mode: "append" };
  }

  return emptyStreamUpdate();
}

function emptyStreamUpdate() {
  return { text: "", mode: "append", thought: "", status: "", sessionId: "", artifacts: [], toolCalls: [] };
}

function resetThinking() {
  bindActiveThinkingRefs();
  if (!dom.thinkingList || !dom.thinkingToggle || !dom.thinkingPanel) return;
  dom.thinkingList.innerHTML = "";
  dom.thinkingToggle.textContent = "思考过程";
  dom.thinkingPanel.classList.remove("collapsed");
  dom.thinkingPanel.classList.remove("hidden");
}

function clearThinking() {
  bindActiveThinkingRefs();
  if (!dom.thinkingList || !dom.thinkingToggle || !dom.thinkingPanel) return;
  dom.thinkingList.innerHTML = "";
  dom.thinkingToggle.textContent = "思考过程";
  dom.thinkingPanel.classList.remove("collapsed");
  dom.thinkingPanel.classList.add("hidden");
}

function collapseThinking() {
  bindActiveThinkingRefs();
  if (!dom.thinkingList || !dom.thinkingPanel || !dom.thinkingToggle) return;
  if (!dom.thinkingList.children.length) {
    clearThinking();
    return;
  }
  dom.thinkingToggle.textContent = "思考过程";
  dom.thinkingPanel.classList.add("collapsed");
  dom.thinkingPanel.classList.remove("hidden");
}

function toggleThinking(event) {
  const panel = event && event.target ? event.target.closest(".thinking-panel") : dom.thinkingPanel;
  if (!panel || panel.classList.contains("hidden")) return;
  panel.classList.toggle("collapsed");
}

function appendThinking(message, status = "") {
  if (!message) return;
  bindActiveThinkingRefs();
  if (!dom.thinkingList || !dom.thinkingPanel) return;
  const last = dom.thinkingList.lastElementChild;
  if (last && last.textContent === message) return;
  const item = document.createElement("div");
  item.className = `thinking-item ${status === "done" ? "done" : "running"}`;
  item.textContent = message;
  dom.thinkingList.appendChild(item);
  dom.thinkingPanel.classList.remove("hidden");
}

function setChatStreaming(streaming) {
  isChatStreaming = streaming;
  dom.queryInput.disabled = streaming;
  dom.sendButton.disabled = streaming;
}

function getSpeechRecognitionConstructor() {
  return window.SpeechRecognition || window.webkitSpeechRecognition || null;
}

function isSpeechSupported() {
  return Boolean(getSpeechRecognitionConstructor());
}

function setVoiceListening(listening) {
  isVoiceListening = listening;
  if (!dom.voiceButton) return;
  dom.voiceButton.classList.toggle("recording", listening);
  dom.voiceButton.setAttribute("aria-pressed", listening ? "true" : "false");
  dom.voiceButton.setAttribute("title", listening ? "正在听，点击停止" : "语音输入");
}

function showVoiceHint(message) {
  if (!dom.queryInput) return;
  dom.queryInput.placeholder = message || "输入问题或样方ID，例如 0101";
}

function startVoiceInput() {
  const Recognition = getSpeechRecognitionConstructor();
  if (!Recognition) {
    showVoiceHint("当前浏览器不支持语音识别，请手动输入");
    window.setTimeout(() => showVoiceHint("输入问题或样方ID，例如 0101"), 2400);
    return;
  }

  if (isVoiceListening && speechRecognition) {
    speechRecognition.stop();
    return;
  }

  speechRecognition = new Recognition();
  speechRecognition.lang = "zh-CN";
  speechRecognition.continuous = false;
  speechRecognition.interimResults = true;
  speechRecognition.maxAlternatives = 1;

  let finalText = "";
  const originalValue = dom.queryInput.value.trim();

  speechRecognition.onstart = () => {
    setVoiceListening(true);
    showVoiceHint("正在听，请说话...");
  };

  speechRecognition.onresult = (event) => {
    let interimText = "";
    for (let index = event.resultIndex; index < event.results.length; index += 1) {
      const transcript = event.results[index][0]?.transcript || "";
      if (event.results[index].isFinal) finalText += transcript;
      else interimText += transcript;
    }
    const merged = `${originalValue}${originalValue && (finalText || interimText) ? " " : ""}${finalText || interimText}`.trim();
    dom.queryInput.value = merged;
  };

  speechRecognition.onerror = (event) => {
    const reason = event.error === "not-allowed" ? "没有麦克风权限" : `语音识别失败：${event.error || "未知错误"}`;
    showVoiceHint(reason);
    setVoiceListening(false);
    window.setTimeout(() => showVoiceHint("输入问题或样方ID，例如 0101"), 2600);
  };

  speechRecognition.onend = () => {
    setVoiceListening(false);
    showVoiceHint("输入问题或样方ID，例如 0101");
    dom.queryInput.focus();
  };

  try {
    speechRecognition.start();
  } catch (error) {
    console.warn("语音识别启动失败", error);
    setVoiceListening(false);
    showVoiceHint("语音识别启动失败，请重试");
  }
}

function renderAssistantAnswer(markdown) {
  updateAssistantMessage(activeAssistantMessageId, markdown);
}

function updateAssistantMessage(messageId, markdown, extra = {}) {
  const message = chatMessages.find((item) => item.id === messageId);
  if (!message) return;
  message.content = String(markdown || "");
  if (Array.isArray(extra.artifacts)) message.artifacts = extra.artifacts;
  if (Array.isArray(extra.toolCalls)) message.toolCalls = extra.toolCalls;
  const container = ensureChatMessagesContainer();
  const bubble = container ? container.querySelector(`[data-message-id="${cssEscape(messageId)}"] .assistant-bubble`) : null;
  if (bubble) bubble.innerHTML = renderMarkdown(message.content) + renderArtifacts(message.artifacts);
  scrollChatToBottom();
}

function renderChatMessages() {
  const container = ensureChatMessagesContainer();
  if (!container) return;
  container.innerHTML = chatMessages.map(renderChatMessage).join("");
  bindActiveThinkingRefs();
  renderIcons();
  scrollChatToBottom();
}

function renderChatMessage(message) {
  const time = formatMessageTime(message.time);
  if (message.role === "user") {
    return `
      <div class="message-row user-message" data-message-id="${escapeAttribute(message.id)}">
        <div>
          <div class="bubble user-bubble">${escapeHtml(message.content)}</div>
          <time class="message-time">${escapeHtml(time)}</time>
        </div>
      </div>
    `;
  }
  return `
    <div class="message-row assistant-message" data-message-id="${escapeAttribute(message.id)}">
      <div class="assistant-avatar" aria-hidden="true"><i data-lucide="bot"></i></div>
      <div>
        <div class="bubble assistant-bubble">${renderMarkdown(message.content)}${renderArtifacts(message.artifacts)}</div>
        <div class="thinking-panel hidden">
          <button class="thinking-title" type="button">思考过程</button>
          <div class="thinking-list"></div>
        </div>
        <time class="message-time">${escapeHtml(time)}</time>
      </div>
    </div>
  `;
}

function bindActiveThinkingRefs() {
  const container = ensureChatMessagesContainer();
  const active = activeAssistantMessageId && container
    ? container.querySelector(`[data-message-id="${cssEscape(activeAssistantMessageId)}"]`)
    : null;
  dom.thinkingPanel = active ? active.querySelector(".thinking-panel") : null;
  dom.thinkingToggle = active ? active.querySelector(".thinking-title") : null;
  dom.thinkingList = active ? active.querySelector(".thinking-list") : null;
}

function cssEscape(value) {
  if (window.CSS && typeof window.CSS.escape === "function") return window.CSS.escape(String(value));
  return String(value).replace(/[^a-zA-Z0-9_-]/g, "\\$&");
}

function scrollChatToBottom() {
  window.requestAnimationFrame(() => {
    if (dom.chatView) dom.chatView.scrollTop = dom.chatView.scrollHeight;
  });
}

function formatMessageTime(value) {
  const date = value ? new Date(value) : new Date();
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function renderMarkdown(markdown) {
  const lines = String(markdown || "").split(/\r?\n/);
  const html = [];
  let listOpen = false;

  const closeList = () => {
    if (listOpen) {
      html.push("</ul>");
      listOpen = false;
    }
  };

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const trimmed = line.trim();

    if (!trimmed) {
      closeList();
      html.push("<br>");
      continue;
    }

    if (isMarkdownTableStart(lines, index)) {
      closeList();
      const parsed = collectMarkdownTable(lines, index);
      html.push(renderMarkdownTable(parsed.rows));
      index = parsed.endIndex;
      continue;
    }

    const image = trimmed.match(/^!\[([^\]]*)\]\(([^)]+)\)$/);
    if (image) {
      closeList();
      html.push(renderImage(image[2], image[1]));
      continue;
    }

    const heading = trimmed.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      closeList();
      const level = heading[1].length;
      html.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }

    const listItem = trimmed.match(/^([-*]|\d+\.)\s+(.+)$/);
    if (listItem) {
      if (!listOpen) {
        html.push("<ul>");
        listOpen = true;
      }
      html.push(`<li>${renderInlineMarkdown(listItem[2])}</li>`);
      continue;
    }

    closeList();
    html.push(`<p>${renderInlineMarkdown(trimmed)}</p>`);
  }

  closeList();
  return html.join("");
}

function isMarkdownTableStart(lines, index) {
  const current = lines[index]?.trim() || "";
  const next = lines[index + 1]?.trim() || "";
  return current.includes("|") && /^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(next);
}

function collectMarkdownTable(lines, startIndex) {
  const rows = [];
  let index = startIndex;
  while (index < lines.length) {
    const line = lines[index].trim();
    if (!line || !line.includes("|")) break;
    rows.push(splitTableRow(line));
    index += 1;
  }
  return { rows, endIndex: index - 1 };
}

function splitTableRow(line) {
  return line
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function renderMarkdownTable(rows) {
  if (rows.length < 2) return "";
  const header = rows[0];
  const body = rows.slice(2);
  return `
    <div class="markdown-table-wrap">
      <table class="markdown-table">
        <thead><tr>${header.map((cell) => `<th>${renderInlineMarkdown(cell)}</th>`).join("")}</tr></thead>
        <tbody>${body.map((row) => `<tr>${header.map((_, idx) => `<td>${renderInlineMarkdown(row[idx] || "")}</td>`).join("")}</tr>`).join("")}</tbody>
      </table>
    </div>
  `;
}

function renderInlineMarkdown(text) {
  const imageTokens = [];
  let source = String(text || "").replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (_, alt, url) => {
    const token = `@@IMG_${imageTokens.length}@@`;
    imageTokens.push(renderImage(url, alt));
    return token;
  });
  let rendered = escapeHtml(source)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>");
  imageTokens.forEach((html, idx) => {
    rendered = rendered.replace(`@@IMG_${idx}@@`, html);
  });
  return rendered;
}

function renderImage(url, alt = "") {
  const safeUrl = normalizeArtifactUrl(String(url || "").trim());
  if (!safeUrl) return "";
  const caption = alt || "??";
  const escapedUrl = escapeAttribute(safeUrl);
  return `<figure class="chat-image" data-src="${escapedUrl}"><img src="${escapedUrl}" alt="${escapeAttribute(caption)}" loading="lazy" onerror="this.closest('figure').classList.add('image-load-failed'); this.style.display='none';" /><figcaption>${escapeHtml(caption)}</figcaption><p class="image-error">???????<a href="${escapedUrl}" target="_blank" rel="noopener">??????</a></p></figure>`;
}

function renderArtifacts(artifacts = []) {
  if (!Array.isArray(artifacts) || !artifacts.length) return "";
  const items = artifacts
    .map((artifact) => {
      const url = normalizeArtifactUrl(artifact.artifact_url || artifact.artifact_absolute_url || artifact.url || artifact.path || "");
      if (!url) return "";
      const title = artifact.title || artifact.name || artifact.filename || artifact.artifact_kind || "生成图表";
      const lower = url.toLowerCase();
      if (/\.(png|jpg|jpeg|webp|gif|svg)(\?|$)/.test(lower)) {
        return renderImage(url, title);
      }
      if (/\.html(\?|$)/.test(lower)) {
        return `<p class="artifact-link"><a href="${escapeAttribute(url)}" target="_blank" rel="noopener">打开交互图表：${escapeHtml(title)}</a></p>`;
      }
      return `<p class="artifact-link"><a href="${escapeAttribute(url)}" target="_blank" rel="noopener">打开文件：${escapeHtml(title)}</a></p>`;
    })
    .filter(Boolean);
  return items.length ? `<div class="artifact-list">${items.join("")}</div>` : "";
}

function normalizeArtifactUrl(url) {
  if (!url) return "";
  let value = String(url).trim().replace(/^<|>$/g, "").replace(/^['"]|['"]$/g, "");
  if (!value) return "";

  try {
    const parsed = new URL(value, window.location.origin);
    if (parsed.pathname.startsWith("/visualizations/")) {
      return parsed.pathname + parsed.search;
    }
    if (parsed.origin === window.location.origin) return parsed.pathname + parsed.search;
    if (/^https?:\/\//i.test(value)) return value;
  } catch {
    // Continue with path normalization.
  }

  value = value.replace(/\\/g, "/");
  const filename = value.split("/").filter(Boolean).pop() || "";

  if (value.startsWith("/visualizations/")) return value;
  if (value.startsWith("visualizations/")) return `/${value}`;
  if (/\.(png|jpg|jpeg|webp|gif|svg|html)(\?|$)/i.test(filename)) {
    return `/visualizations/${filename}`;
  }

  if (value.startsWith("/")) return value;
  return value;
}

function createSessionId() {
  const random = Math.random().toString(16).slice(2, 14);
  return `sess_${Date.now().toString(16)}${random}`;
}

function addClickListener(element, handler) {
  if (element) element.addEventListener("click", handler);
}

function bindEvents() {
  ensureChatMessagesContainer();
  [...dom.segments, ...dom.bottomTabs].forEach((button) => {
    button.addEventListener("click", () => {
      if (button.dataset.view) switchView(button.dataset.view);
    });
  });

  dom.composer.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = dom.queryInput.value.trim();
    await answerChat(text);
    dom.queryInput.value = "";
  });

  dom.queryInput.addEventListener("focus", () => document.body.classList.add("keyboard-open"));
  dom.queryInput.addEventListener("blur", () => {
    window.setTimeout(() => document.body.classList.remove("keyboard-open"), 120);
  });

  addClickListener(dom.moreButton, () => {
    if (dom.moreMenu) dom.moreMenu.classList.toggle("hidden");
  });
  addClickListener(dom.historyButton, openHistoryPanel);
  addClickListener(dom.openHistoryButton, openHistoryPanel);
  addClickListener(dom.closeHistoryButton, closeHistoryPanel);
  addClickListener(dom.newChatButton, startNewChat);
  addClickListener(dom.voiceButton, startVoiceInput);
  if (dom.voiceButton && !isSpeechSupported()) {
    dom.voiceButton.classList.add("unsupported");
    dom.voiceButton.setAttribute("title", "当前浏览器不支持语音识别");
  }

  if (dom.chatMessages) {
    dom.chatMessages.addEventListener("click", (event) => {
      if (event.target.closest(".thinking-title")) toggleThinking(event);
    });
  }

  dom.historyList.addEventListener("click", (event) => {
    const item = event.target.closest(".history-item");
    if (item) restoreHistoryItem(item.dataset.sessionId);
  });

  document.addEventListener("click", (event) => {
    if (dom.moreMenu && dom.moreButton && !dom.moreMenu.contains(event.target) && !dom.moreButton.contains(event.target)) {
      closeMoreMenu();
    }
  });

  dom.sampleSearch.addEventListener("submit", (event) => {
    event.preventDefault();
    const sampleId = parseSampleId(dom.sampleIdInput.value);
    if (!sampleId) {
      dom.sampleStatus.textContent = "请输入样方 ID，例如 0101";
      return;
    }
    selectSample(sampleId);
  });

  dom.speciesFilter.addEventListener("change", () => {
    const checked = [...dom.speciesFilter.querySelectorAll("input:checked")].map((input) => input.value);
    enabledSpecies = new Set(checked.length ? checked : uniqueSpecies());
    renderTreeList();
    drawPlot(dom.plotCanvas, getSample(), getVisibleTrees(), selectedTreeId);
    drawPlot(dom.largePlotCanvas, getSample(), getVisibleTrees(), selectedTreeId);
  });

  dom.treeList.addEventListener("click", (event) => {
    const item = event.target.closest(".tree-item");
    if (!item) return;
    const tree = getSample()?.trees.find((entry) => entry.id === item.dataset.treeId);
    if (tree) renderTreeDetail(tree);
  });

  addClickListener(dom.listButton, showTreeListPanel);
  addClickListener(dom.backPlotButton, showPlotPanel);
  addClickListener(dom.backListButton, showTreeListPanel);
  addClickListener(dom.locateTreeButton, showPlotPanel);

  dom.plotCanvas.addEventListener("click", (event) => {
    const tree = pickTreeFromCanvas(event, dom.plotCanvas);
    if (tree) selectTreeOnPlot(tree);
  });
  dom.largePlotCanvas.addEventListener("click", (event) => {
    const tree = pickTreeFromCanvas(event, dom.largePlotCanvas);
    if (tree) selectTreeOnPlot(tree);
  });
  addClickListener(dom.zoomButton, () => {
    dom.plotDialog.showModal();
    window.requestAnimationFrame(() => drawPlot(dom.largePlotCanvas, getSample(), getVisibleTrees(), selectedTreeId));
  });
  addClickListener(dom.closeDialog, () => dom.plotDialog.close());

  window.addEventListener("resize", () => {
    if (!getSample()) return;
    drawPlot(dom.plotCanvas, getSample(), getVisibleTrees(), selectedTreeId);
    if (dom.plotDialog.open) drawPlot(dom.largePlotCanvas, getSample(), getVisibleTrees(), selectedTreeId);
  });

  // ===== 调查模块初始化 =====
  initSurveyEventListeners();
}

// =========================================================================
// 野外调查模块 (Survey)
// =========================================================================

let currentPlanId = null;
let currentRecId = null;

function initSurveyEventListeners() {
  // 新建调查方案
  addClickListener(dom.newSurveyBtn, () => {
    dom.surveyNewDialog.showModal();
  });
  addClickListener(dom.surveyNewDialogClose, () => dom.surveyNewDialog.close());
  addClickListener(dom.surveyNewCancel, () => dom.surveyNewDialog.close());

  addClickListener(dom.surveyNewSubmit, async () => {
    const request = dom.surveyNewRequest.value.trim();
    if (!request) { alert("请输入调查需求"); return; }

    dom.surveyNewLoading.style.display = "block";
    dom.surveyNewSubmit.disabled = true;

    try {
      const resp = await fetch("/api/survey/plan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ request }),
      });
      const data = await resp.json();
      if (data.status === "success" && data.plan) {
        dom.surveyNewDialog.close();
        dom.surveyNewRequest.value = "";
        dom.surveyNewLoading.style.display = "none";
        dom.surveyNewSubmit.disabled = false;
        openSurveyPlan(data.plan.plan_id);
      } else {
        alert("生成方案失败: " + (data.message || "未知错误"));
      }
    } catch (err) {
      alert("网络错误: " + err.message);
    }
    dom.surveyNewLoading.style.display = "none";
    dom.surveyNewSubmit.disabled = false;
  });

  // 返回按钮
  addClickListener(dom.surveyBackBtn, () => {
    dom.surveyDetail.classList.add("hidden");
    dom.surveyHome.classList.remove("hidden");
    loadSurveyList();
  });

  // 生成报告
  addClickListener(dom.surveyReportBtn, async () => {
    if (!currentPlanId) return;
    try {
      const resp = await fetch(`/api/survey/plans/${currentPlanId}/report`, {
        method: "POST",
      });
      const data = await resp.json();
      if (data.status === "success") {
        showSurveyReport(data);
      } else {
        alert("生成报告失败: " + (data.message || ""));
      }
    } catch (err) {
      alert("网络错误: " + err.message);
    }
  });

  // 观察记录弹窗
  addClickListener(dom.surveyObsDialogClose, () => dom.surveyObsDialog.close());
  addClickListener(dom.surveyObsSave, saveSurveyObservation);
  addClickListener(dom.surveyObsSkip, skipSurveyObservation);
}

// ---- 加载方案列表 ----
async function loadSurveyList() {
  try {
    const resp = await fetch("/api/survey/plans?limit=20");
    const data = await resp.json();
    if (data.status !== "success") {
      dom.surveyList.innerHTML = `<div class="survey-empty">加载失败</div>`;
      return;
    }
    const plans = data.plans || [];
    if (!plans.length) {
      dom.surveyList.innerHTML = `<div class="survey-empty">暂无调查方案，点击上方按钮新建</div>`;
      return;
    }
    dom.surveyList.innerHTML = plans.map(p => {
      const statusClass = p.status || "draft";
      const statusLabel = {draft: "草稿", active: "进行中", completed: "已完成", cancelled: "已取消"}[statusClass] || statusClass;
      const desc = (p.summary || p.ai_analysis || "").slice(0, 80);
      return `
        <div class="survey-card" data-plan-id="${p.plan_id}">
          <div class="survey-card-head">
            <span class="survey-card-title">${escapeHtml(p.title || "未命名方案")}</span>
            <span class="survey-card-status ${statusClass}">${statusLabel}</span>
          </div>
          <div class="survey-card-meta">
            <span>🌳 ${p.tree_count || 0} 株</span>
            <span>📍 ${p.subplot_count || 0} 样地</span>
            <span>✅ ${p.completed_count || 0}/${p.tree_count || 0}</span>
            <span>📅 ${p.created_at || ""}</span>
          </div>
          ${desc ? `<div class="survey-card-desc">${escapeHtml(desc)}</div>` : ""}
        </div>
      `;
    }).join("");

    // 点击卡片打开方案
    dom.surveyList.querySelectorAll(".survey-card").forEach(card => {
      card.addEventListener("click", () => {
        const pid = parseInt(card.dataset.planId);
        openSurveyPlan(pid);
      });
    });
  } catch (err) {
    dom.surveyList.innerHTML = `<div class="survey-empty">加载失败: ${err.message}</div>`;
  }
}

// ---- 打开方案详情 ----
async function openSurveyPlan(planId) {
  currentPlanId = planId;
  try {
    const resp = await fetch(`/api/survey/plans/${planId}`);
    const data = await resp.json();
    if (data.status !== "success" || !data.plan) {
      alert("获取方案失败");
      return;
    }
    const plan = data.plan;
    const recs = plan.recommendations || [];

    dom.surveyHome.classList.add("hidden");
    dom.surveyDetail.classList.remove("hidden");

    dom.surveyDetailTitle.textContent = plan.title || "调查方案";
    const statusMap = {draft: "草稿", active: "进行中", completed: "已完成", cancelled: "已取消"};
    dom.surveyDetailStatus.textContent = statusMap[plan.status] || plan.status;
    dom.surveyDetailStatus.className = "survey-status-badge " + (plan.status || "draft");

    // 进度
    const total = recs.length;
    const completed = plan.completed_count || recs.filter(r => r.status === "completed").length;
    const pct = total > 0 ? Math.round(completed / total * 100) : 0;
    dom.surveyProgressFill.style.width = pct + "%";
    dom.surveyProgressText.textContent = `${completed}/${total}`;

    // AI 分析
    dom.surveyAiAnalysisText.textContent = plan.ai_analysis || "（无 AI 分析）";

    // 渲染建议列表
    renderSurveyRecs(recs);

    switchView("survey");
  } catch (err) {
    alert("加载方案失败: " + err.message);
  }
}

// ---- 渲染建议列表 ----
function renderSurveyRecs(recs) {
  if (!recs.length) {
    dom.surveyRecList.innerHTML = `<div class="survey-empty">该方案暂无调查建议</div>`;
    return;
  }

  dom.surveyRecList.innerHTML = recs.map(rec => {
    const priority = rec.priority || "medium";
    const status = rec.status || "pending";
    const treeInfo = rec.tree_id ? `🌲 ${rec.tree_id}` : `📍 样地 ${rec.subplot_id || "?"}`;
    const speciesInfo = rec.species ? `（${rec.species}）` : "";
    const categoryMap = {
      health_check: "健康检查", morphology: "形态关注", competition: "竞争压力",
      climate_stress: "气候胁迫", species_observation: "物种观察", control: "对照",
    };
    const categoryLabel = categoryMap[rec.category] || rec.category || "未分类";
    const priorityLabel = {high: "高", medium: "中", low: "低"}[priority] || priority;
    const isCompleted = status === "completed";
    const isSkipped = status === "skipped";

    return `
      <div class="survey-rec-card ${priority} ${status}" data-rec-id="${rec.rec_id}">
        <div class="survey-rec-head">
          <span class="survey-rec-tree">${treeInfo} ${speciesInfo}</span>
          <span class="survey-rec-tag ${priority}">${priorityLabel}</span>
        </div>
        <div class="survey-rec-meta">
          <span class="survey-rec-chip">${categoryLabel}</span>
          <span class="survey-rec-chip">样地 ${rec.subplot_id || "?"}</span>
        </div>
        <div class="survey-rec-reason">${escapeHtml(rec.reason || "")}</div>
        <div class="survey-rec-actions"><strong>建议行动：</strong>${escapeHtml(rec.suggested_actions || "")}</div>
        <div class="survey-rec-btns">
          <button class="survey-rec-btn record ${isCompleted ? 'completed' : ''}" data-rec-id="${rec.rec_id}">
            <i data-lucide="${isCompleted ? 'check-circle' : 'clipboard-list'}"></i>
            ${isCompleted ? '已记录' : '记录'}
          </button>
          <button class="survey-rec-btn skip ${isSkipped ? 'skipped' : ''}" data-rec-id="${rec.rec_id}">
            <i data-lucide="${isSkipped ? 'x-circle' : 'skip-forward'}"></i>
            ${isSkipped ? '已跳过' : '跳过'}
          </button>
        </div>
      </div>
    `;
  }).join("");

  // 更新图标
  if (window.lucide) lucide.createIcons();

  // 绑定事件
  dom.surveyRecList.querySelectorAll(".survey-rec-btn.record").forEach(btn => {
    btn.addEventListener("click", () => {
      const recId = parseInt(btn.dataset.recId);
      openObservationDialog(recId);
    });
  });
  dom.surveyRecList.querySelectorAll(".survey-rec-btn.skip").forEach(btn => {
    btn.addEventListener("click", async () => {
      const recId = parseInt(btn.dataset.recId);
      await skipRecommendation(recId);
    });
  });
}

// ---- 打开观察记录弹窗 ----
function openObservationDialog(recId) {
  currentRecId = recId;
  const card = dom.surveyRecList.querySelector(`.survey-rec-card[data-rec-id="${recId}"]`);
  if (card) {
    const treeText = card.querySelector(".survey-rec-tree")?.textContent || "";
    const reason = card.querySelector(".survey-rec-reason")?.textContent || "";
    const actions = card.querySelector(".survey-rec-actions")?.textContent || "";
    dom.surveyObsInfo.innerHTML = `<strong>${escapeHtml(treeText)}</strong><br/>
      <span style="color:var(--muted);font-size:12px;">原因：${escapeHtml(reason)}</span>`;
  }
  dom.surveyObsNotes.value = "";
  dom.surveyObsHealth.value = "";
  dom.surveyObsPest.value = "";
  dom.surveyObsPheno.value = "";
  dom.surveyObsDialog.showModal();
}

// ---- 保存观察记录 ----
async function saveSurveyObservation() {
  if (!currentPlanId || !currentRecId) return;

  const payload = {
    plan_id: currentPlanId,
    rec_id: currentRecId,
    notes: dom.surveyObsNotes.value.trim(),
    health_status: dom.surveyObsHealth.value || null,
    pest_signs: dom.surveyObsPest.value || null,
    phenophase: dom.surveyObsPheno.value || null,
  };

  try {
    const resp = await fetch("/api/survey/observations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (data.status === "success") {
      dom.surveyObsDialog.close();
      await updateRecStatus(currentRecId, "completed");
    } else {
      alert("保存失败: " + (data.message || ""));
    }
  } catch (err) {
    alert("网络错误: " + err.message);
  }
}

// ---- 跳过建议 ----
async function skipSurveyObservation() {
  if (!currentRecId) return;
  dom.surveyObsDialog.close();
  await updateRecStatus(currentRecId, "skipped");
}

async function skipRecommendation(recId) {
  await updateRecStatus(recId, "skipped");
}

async function updateRecStatus(recId, status) {
  try {
    const resp = await fetch(`/api/survey/recommendations/${recId}/status?status=${status}`, {
      method: "PUT",
    });
    const data = await resp.json();
    if (data.status === "success") {
      if (currentPlanId) openSurveyPlan(currentPlanId);
    }
  } catch (err) {
    console.error("更新状态失败", err);
  }
}

// ---- 显示报告 ----
function showSurveyReport(data) {
  const report = data.report || "";
  const stats = data.stats || {};

  const statsHtml = `
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:14px;">
      <div style="padding:10px;border-radius:8px;background:#edf6f1;text-align:center;">
        <div style="font-size:24px;font-weight:750;color:var(--green-800);">${stats.completed || 0}</div>
        <div style="font-size:12px;color:var(--muted);">已完成</div>
      </div>
      <div style="padding:10px;border-radius:8px;background:#fff2e7;text-align:center;">
        <div style="font-size:24px;font-weight:750;color:var(--warning);">${stats.total - stats.completed - stats.skipped || 0}</div>
        <div style="font-size:12px;color:var(--muted);">待完成</div>
      </div>
      <div style="padding:10px;border-radius:8px;background:#e8f4ee;text-align:center;">
        <div style="font-size:24px;font-weight:750;color:#3d8b63;">${stats.completion_rate || 0}%</div>
        <div style="font-size:12px;color:var(--muted);">完成率</div>
      </div>
    </div>
  `;

  // 在报告按钮上方插入报告内容
  const reportHtml = `
    <div class="survey-report-view">
      ${statsHtml}
      <pre style="white-space:pre-wrap;font-family:inherit;margin:0;">${escapeHtml(report)}</pre>
    </div>
  `;

  // 移除旧的报告视图
  const oldReport = document.querySelector(".survey-report-view");
  if (oldReport) oldReport.remove();

  dom.surveyReportBtn.insertAdjacentHTML("beforebegin", reportHtml);
  dom.surveyReportBtn.textContent = "📄 重新生成报告";
}

// ---- 工具函数 ----
function escapeHtml(str) {
  if (!str) return "";
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function formatValue(value, unit = "") {
  if (value === null || value === undefined || Number.isNaN(value)) return "无";
  if (typeof value === "number") {
    const rounded = Math.abs(value) >= 1000 ? value.toFixed(3) : String(round1(value));
    return `${rounded}${unit}`;
  }
  return `${value}${unit}`;
}

function formatHistoryTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function escapeAttribute(value) {
  return escapeHtml(value);
}

function initApp() {
  try {
    bindEvents();
  } catch (error) {
    console.error("事件绑定失败", error);
  }
  try {
    renderIcons();
    restoreCurrentChatOnLoad();
  } catch (error) {
    console.error("页面初始化失败", error);
    const container = ensureChatMessagesContainer();
    if (container) {
      container.innerHTML = `<div class="message-row assistant-message"><div class="assistant-avatar">AI</div><div><div class="bubble assistant-bubble">页面初始化失败：${escapeHtml(error.message || String(error))}</div></div></div>`;
    }
  }
  selectSample(DEFAULT_SUBPLOT_ID).catch((error) => console.warn("默认样方加载失败", error));
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initApp);
} else {
  initApp();
}

