/* 宽表拆分迁移演练台 —— 纯原生 JS，只与本机 :8000 门面通信 */
"use strict";

const $ = (sel) => document.querySelector(sel);

async function api(path, body) {
  const opt = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  const resp = await fetch(path, opt);
  const env = await resp.json();
  if (env.code !== 0) throw new Error(env.msg || ("code=" + env.code));
  return env.data;
}

const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const pretty = (o) => JSON.stringify(o, null, 2);
const shortHash = (h) => (h ? h.slice(0, 12) + "…" : "-");

/* ---------- 状态栏 ---------- */
async function refreshStatus() {
  const bar = $("#statusbar");
  try {
    const st = await api("/api/status");
    const pills = st.services.map((s) => {
      const fault = s.ok && s.detail && s.detail.fault;
      const cls = s.ok ? (fault ? "pill warn" : "pill ok") : "pill bad";
      const txt = s.ok ? (fault ? "故障注入中" : "正常") : "不可用";
      return `<span class="${cls}">${esc(s.name)}: ${txt}${s.ok ? " " + s.ms + "ms" : ""}</span>`;
    });
    const p = st.pending;
    const pendCls = p.count ? "pill warn" : "pill ok";
    const pendTxt = p.count === null ? "待重放: 未知" : `待重放: ${p.count} 批`;
    bar.innerHTML = pills.join("") + `<span class="${pendCls}">${pendTxt}</span>`;
    syncFaultButtons(st.services);
    renderStats(st.storage_stats);
  } catch (e) {
    bar.innerHTML = `<span class="pill bad">门面异常: ${esc(e.message)}</span>`;
  }
}

function syncFaultButtons(services) {
  for (const s of services) {
    if (s.name === "storage") setFaultBtn("#fault-storage", "新版存储", s.detail && s.detail.fault);
    if (s.name === "compat") setFaultBtn("#fault-compat", "兼容服务", s.detail && s.detail.fault);
  }
}

function setFaultBtn(sel, label, down) {
  const btn = $(sel);
  btn.textContent = `${label}故障：${down ? "开" : "关"}`;
  btn.classList.toggle("on", !!down);
}

/* ---------- 页签 ---------- */
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $("#tab-" + btn.dataset.tab).classList.add("active");
    if (btn.dataset.tab === "records") loadRecords();
    if (btn.dataset.tab === "compare") loadCompareSelectors();
    if (btn.dataset.tab === "ops") loadOps();
  });
});

/* ---------- ① 导入 ---------- */
const SAMPLE = [{
  "id": "rpt-20261118-mobile",
  "report_date": "2026-11-18",
  "channel": "mobile",
  "pv": 15230, "uv": 8421, "gmv": 93210.5,
  "attribution": { "model": "last_click", "window_days": 7 },
  "tags": ["promo", "app", "promo"],
  "hourly": [
    { "hour": 0, "pv": 320, "uv": 210 },
    { "hour": 1, "pv": 280, "uv": 190 },
    { "hour": 1, "pv": 280, "uv": 190 },
    { "hour": 2, "pv": 250, "uv": 170 }
  ],
  "top_pages": [],
  "segments": [
    { "name": "new", "uv": 3000, "dims": { "device": ["ios", "android"] } },
    { "name": "returning", "uv": 5421, "dims": { "device": ["ios"] } }
  ]
}];

$("#btn-sample").addEventListener("click", () => {
  $("#import-input").value = JSON.stringify(SAMPLE, null, 2);
});

function parseImport() {
  const text = $("#import-input").value.trim();
  if (!text) throw new Error("请先粘贴 JSON 或点「填入示例」");
  const parsed = JSON.parse(text);
  return Array.isArray(parsed) ? parsed : [parsed];
}

$("#btn-preview").addEventListener("click", async () => {
  const box = $("#preview");
  try {
    const records = parseImport();
    const pv = await api("/api/preview_split", { record: records[0] });
    box.classList.remove("muted");
    box.innerHTML =
      `<p>校验和 <code>${shortHash(pv.checksum)}</code> · 主记录字段 ${Object.keys(pv.master).length} 个 · ` +
      `子项分组 ${pv.groups.length} 个 · 子项行 ${pv.items_total} 行</p>` +
      `<h4>主记录（masters）</h4><pre class="jsonview">${esc(pretty(pv.master))}</pre>` +
      `<h4>子项分组（sub_item_groups，空数组 count=0 也保留）</h4><pre class="jsonview">${esc(pretty(pv.groups))}</pre>` +
      `<h4>子项行（sub_items，前 ${pv.items.length} 行，seq 保序、重复项各占一行）</h4>` +
      `<pre class="jsonview">${esc(pretty(pv.items))}</pre>`;
  } catch (e) {
    box.innerHTML = `<span class="err">预览失败：${esc(e.message)}</span>`;
  }
});

$("#btn-import").addEventListener("click", async () => {
  const box = $("#import-result");
  try {
    const records = parseImport();
    const r = await api("/api/import", { records });
    const rows = r.records.map((x) => `<tr><td>${esc(x.record_id)}</td><td>v${x.version}</td></tr>`).join("");
    const stCls = r.storage === "applied" ? "ok-text" : "warn-text";
    box.innerHTML =
      `<p>批次 <button class="link" onclick="showBatchSource('${r.batch_id}')">${r.batch_id}</button> 已接入 · ` +
      `新版存储: <span class="${stCls}">${r.storage === "applied" ? "已双写" : "待重放（故障或不可用）"}</span>` +
      (r.storage_error ? ` · <span class="err">${esc(r.storage_error)}</span>` : "") + `</p>` +
      `<table><thead><tr><th>记录</th><th>分配版本</th></tr></thead><tbody>${rows}</tbody></table>`;
    refreshStatus();
  } catch (e) {
    box.innerHTML = `<span class="err">导入失败：${esc(e.message)}</span>`;
  }
});

/* ---------- ② 记录与改版 ---------- */
async function loadRecords() {
  const box = $("#records-table");
  try {
    const records = await api("/api/records");
    if (!records.length) {
      box.innerHTML = '<p class="muted">暂无记录，请先在「导入」页写入。</p>';
      return;
    }
    const rows = records.map((r) =>
      `<tr><td><button class="link" onclick="showRecord('${esc(r.record_id)}')">${esc(r.record_id)}</button></td>` +
      `<td>v${r.latest_version}</td><td>${r.versions.length}</td>` +
      `<td><button class="link" onclick="showBatchSource('${r.versions[r.versions.length - 1].batch_id}')">${r.versions[r.versions.length - 1].batch_id}</button></td>` +
      `<td><code>${shortHash(r.versions[r.versions.length - 1].checksum)}</code></td></tr>`).join("");
    box.innerHTML = `<table><thead><tr><th>记录</th><th>最新版本</th><th>修订数</th><th>最近批次</th><th>校验和</th></tr></thead><tbody>${rows}</tbody></table>`;
  } catch (e) {
    box.innerHTML = `<span class="err">加载失败：${esc(e.message)}</span>`;
  }
}

window.showRecord = async (rid, version) => {
  const box = $("#record-detail");
  try {
    const [rec, rev] = await Promise.all([
      api("/api/record?record_id=" + encodeURIComponent(rid) + (version ? "&version=" + version : "")),
      api("/api/revisions?record_id=" + encodeURIComponent(rid)),
    ]);
    const revRows = rev.revisions.map((v) =>
      `<tr><td><button class="link" onclick="showRecord('${esc(rid)}', ${v.version})">v${v.version}</button></td>` +
      `<td><button class="link" onclick="showBatchSource('${v.batch_id}')">${v.batch_id}</button></td>` +
      `<td>${esc(v.ingested_at)}</td><td><code>${shortHash(v.checksum)}</code></td></tr>`).join("");
    box.innerHTML =
      `<p><b>${esc(rid)}</b> · 当前 v${rec.version} · 批次 ${rec.batch_id} · 校验和 <code>${shortHash(rec.checksum)}</code></p>` +
      `<h4>历史修订（revisions 表）</h4>` +
      `<table><thead><tr><th>版本</th><th>批次</th><th>时间</th><th>校验和</th></tr></thead><tbody>${revRows}</tbody></table>` +
      `<h4>重组内容（由三类表 assemble）</h4><pre class="jsonview">${esc(pretty(rec.payload))}</pre>` +
      `<h4>改版：编辑上方 JSON 的副本后提交，生成 v${rec.version + 1}</h4>` +
      `<textarea id="revise-input" spellcheck="false">${esc(pretty(rec.payload))}</textarea>` +
      `<div class="btnrow"><button class="primary" onclick="submitRevise('${esc(rid)}')">提交改版</button></div>` +
      `<div id="revise-result"></div>`;
  } catch (e) {
    box.innerHTML = `<span class="err">加载失败：${esc(e.message)}</span>`;
  }
};

window.submitRevise = async (rid) => {
  const box = $("#revise-result");
  try {
    const payload = JSON.parse($("#revise-input").value);
    const r = await api("/api/revise", { record_id: rid, payload });
    const stTxt = r.storage === "applied" ? "已双写" : "待重放";
    box.innerHTML = `<p class="ok-text">已生成 v${r.records[0].version}（批次 ${r.batch_id}，新版存储: ${stTxt}）</p>`;
    refreshStatus();
    showRecord(rid);
    loadRecords();
  } catch (e) {
    box.innerHTML = `<span class="err">改版失败：${esc(e.message)}</span>`;
  }
};

/* ---------- ③ 查询对比 ---------- */
async function loadCompareSelectors() {
  try {
    const records = await api("/api/records");
    const sel = $("#cmp-record");
    sel.innerHTML = records.map((r) => `<option>${esc(r.record_id)}</option>`).join("");
    sel.onchange = () => fillVersions(records);
    fillVersions(records);
  } catch (e) {
    $("#compare-verdict").innerHTML = `<span class="err">加载记录失败：${esc(e.message)}</span>`;
  }
}

function fillVersions(records) {
  const rid = $("#cmp-record").value;
  const rec = records.find((r) => r.record_id === rid);
  const vs = rec ? rec.versions.map((v) => v.version) : [];
  $("#cmp-version").innerHTML = vs.map((v) => `<option>${v}</option>`).join("");
}

$("#btn-compare").addEventListener("click", async () => {
  const rid = $("#cmp-record").value;
  const version = $("#cmp-version").value;
  const verdict = $("#compare-verdict");
  if (!rid) { verdict.innerHTML = '<span class="muted">暂无记录可对比。</span>'; return; }
  try {
    const r = await api(`/api/compare?record_id=${encodeURIComponent(rid)}&version=${version}`);
    $("#cmp-old").textContent = pretty(r.legacy_payload);
    $("#cmp-new").textContent = pretty(r.new_payload);
    if (r.equal) {
      verdict.innerHTML = `<p class="ok-text">✔ 等价：同一输入版本（${esc(rid)} v${version}）旧接口与兼容服务返回逐字节一致。` +
        `校验和 <code>${shortHash(r.legacy_checksum)}</code></p>`;
    } else {
      const diffs = (r.diffs || []).map((d) => `<li>${esc(d)}</li>`).join("");
      const errs = [r.legacy_error && `旧接口: ${r.legacy_error}`, r.new_error && `兼容服务: ${r.new_error}`]
        .filter(Boolean).map((s) => `<li>${esc(s)}</li>`).join("");
      verdict.innerHTML = `<p class="err">✘ 不一致（${esc(rid)} v${version}）</p><ul>${errs}${diffs}</ul>`;
    }
  } catch (e) {
    verdict.innerHTML = `<span class="err">对比失败：${esc(e.message)}</span>`;
  }
});

$("#btn-reconcile").addEventListener("click", () => runReconcile());

async function runReconcile() {
  const box = $("#reconcile-result");
  box.innerHTML = "对账中…";
  try {
    const r = await api("/api/reconcile");
    const cls = r.summary.bad === 0 ? "ok-text" : "err";
    const rows = r.rows.map((row) => {
      const stCls = row.status === "ok" ? "ok-text" : "err";
      const diffs = (row.diffs || []).slice(0, 5).map((d) => `<li>${esc(d)}</li>`).join("");
      const errs = [row.legacy_error, row.new_error].filter(Boolean).map((e) => `<li>${esc(e)}</li>`).join("");
      return `<tr><td>${esc(row.record_id)}</td><td>v${row.version}</td>` +
        `<td><button class="link" onclick="showBatchSource('${row.batch_id}')">${row.batch_id}</button></td>` +
        `<td class="${stCls}">${row.status}</td>` +
        `<td><code>${shortHash(row.stored_checksum)}</code></td>` +
        `<td><code>${shortHash(row.legacy_checksum)}</code></td>` +
        `<td><code>${shortHash(row.new_checksum)}</code></td>` +
        `<td>${diffs || errs ? `<ul class="tight">${errs}${diffs}</ul>` : "-"}</td></tr>`;
    }).join("");
    box.classList.remove("muted");
    box.innerHTML =
      `<p class="${cls}">共 ${r.summary.total} 个版本，一致 ${r.summary.ok}，异常 ${r.summary.bad}</p>` +
      `<table><thead><tr><th>记录</th><th>版本</th><th>批次</th><th>结论</th>` +
      `<th>接入校验和</th><th>旧接口</th><th>兼容服务</th><th>差异/错误</th></tr></thead><tbody>${rows}</tbody></table>`;
    renderOpsReconcile(r);
  } catch (e) {
    box.innerHTML = `<span class="err">对账失败：${esc(e.message)}</span>`;
  }
}

/* ---------- ④ 迁移演练 ---------- */
async function toggleFault(target, sel) {
  const on = $(sel).classList.contains("on");
  try {
    await api("/api/fault", { target, down: !on });
  } catch (e) {
    alert("切换失败：" + e.message);
  }
  refreshStatus();
}

$("#fault-storage").addEventListener("click", () => toggleFault("storage", "#fault-storage"));
$("#fault-compat").addEventListener("click", () => toggleFault("compat", "#fault-compat"));
$("#btn-refresh-ops").addEventListener("click", () => { loadOps(); refreshStatus(); });

$("#btn-replay").addEventListener("click", async () => {
  const box = $("#replay-result");
  try {
    const r = await api("/api/replay", {});
    box.classList.remove("muted");
    box.innerHTML = `<p class="ok-text">新应用 ${r.applied.length} 批：${r.applied.join(", ") || "（无）"}` +
      ` · 跳过已应用 ${r.skipped.length} 批 · 剩余待重放 ${r.pending_after.length} 批</p>`;
    loadOps();
    refreshStatus();
  } catch (e) {
    box.innerHTML = `<span class="err">重放失败：${esc(e.message)}</span>`;
  }
});

async function loadOps() {
  try {
    const st = await api("/api/status");
    const p = st.pending;
    $("#pending-list").innerHTML = p.count
      ? `<ul>${p.pending.map((b) => `<li><button class="link" onclick="showBatchSource('${b}')">${b}</button></li>`).join("")}</ul>`
      : '<p class="ok-text">无待重放批次，新版存储与日志一致。</p>';
    renderStats(st.storage_stats);
  } catch (e) {
    $("#pending-list").innerHTML = `<span class="err">加载失败：${esc(e.message)}</span>`;
  }
}

function renderStats(stats) {
  if (!stats) { $("#storage-stats").innerHTML = '<span class="muted">未知</span>'; return; }
  const t = stats.tables;
  $("#storage-stats").innerHTML =
    `<table><tbody>` +
    `<tr><td>masters 主记录</td><td>${t.masters}</td></tr>` +
    `<tr><td>sub_item_groups 子项分组</td><td>${t.sub_item_groups}</td></tr>` +
    `<tr><td>sub_items 子项行</td><td>${t.sub_items}</td></tr>` +
    `<tr><td>revisions 历史修订</td><td>${t.revisions}</td></tr>` +
    `<tr><td>batches 已应用批次</td><td>${t.batches}</td></tr>` +
    `</tbody></table>`;
}

function renderOpsReconcile(reconcile) {
  const box = $("#ops-reconcile");
  const rows = reconcile.batches.map((b) => {
    const cls = b.status === "ok" ? "ok-text" : "err";
    return `<tr><td><button class="link" onclick="showBatchSource('${b.batch_id}')">${b.batch_id}</button></td>` +
      `<td>${b.ok}/${b.total}</td><td class="${cls}">${b.status === "ok" ? "一致" : "有差异"}</td></tr>`;
  }).join("");
  box.classList.remove("muted");
  box.innerHTML = `<table><thead><tr><th>批次</th><th>一致/总数</th><th>结论</th></tr></thead><tbody>${rows}</tbody></table>`;
}

window.showBatchSource = async (batchId) => {
  document.querySelector('[data-tab="ops"]').click();
  const box = $("#batch-source");
  box.classList.remove("muted");
  try {
    const line = await api("/api/batch_source?batch_id=" + encodeURIComponent(batchId));
    box.textContent = pretty(line);
  } catch (e) {
    box.textContent = "读取失败：" + e.message;
  }
};

/* ---------- 启动 ---------- */
refreshStatus();
setInterval(refreshStatus, 3000);
