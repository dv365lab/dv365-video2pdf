"use strict";

const $ = (id) => document.getElementById(id);
const extensions = new Set(["mp4", "avi", "mov", "mkv", "webm", "m4v", "wmv", "mpg", "mpeg", "ts", "mts", "m2ts", "flv"]);
const entries = [];
let uploading = false;
let rejectedFiles = [];

function updateCount() {
  const pending = entries.filter((entry) => entry.state === "pending").length;
  $("selection-count").textContent = pending ? `${pending} 个任务待处理` : "还未添加待处理任务";
  $("queue-count").textContent = `${entries.length} 个任务`;
  $("empty").hidden = entries.length > 0;
  $("convert").disabled = !pending || uploading;
  updateDropFeedback();
}

function updateDropFeedback() {
  const files = entries.filter((entry) => entry.kind === "file");
  const pending = files.filter((entry) => entry.state === "pending").length;
  $("drop-title").textContent = files.length ? `已添加 ${files.length} 个视频` : rejectedFiles.length ? "文件未添加成功" : "将视频拖到这里";
  $("drop-icon").textContent = files.length ? "✓" : rejectedFiles.length ? "!" : "↑";
  $("drop-description").textContent = files.length ? "可以继续拖入视频，或点击按钮追加文件" : "支持多个视频 · MP4 / AVI / MOV / MKV 等";
  $("drop-feedback").hidden = !files.length && !rejectedFiles.length;
  $("drop-feedback-files").textContent = files.slice(0, 3).map((entry) => entry.name).join("、") + (files.length > 3 ? ` 等 ${files.length} 个文件` : "");
  $("drop-feedback-help").textContent = rejectedFiles.length
    ? `未添加：${rejectedFiles.join("、")}。请使用非空视频文件。${pending ? "已添加的视频可点击下方“开始转换”。" : ""}`
    : pending ? `${pending} 个视频等待开始，请点击下方“开始转换”。` : "视频已提交，请在下方任务列表查看处理进度。";
  $("drop-zone").classList[files.length ? "add" : "remove"]("has-files");
  $("drop-zone").classList[rejectedFiles.length ? "add" : "remove"]("has-rejected");
}

function makeElement(tag, className, text) {
  const element = document.createElement(tag);
  element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function addEntry(values) {
  const entry = { state: "pending", id: null, ...values };
  entry.card = makeElement("article", "job");
  const row = makeElement("div", "job-main");
  const info = makeElement("div", "job-info");
  info.append(makeElement("div", "filename", entry.name));
  entry.status = makeElement("p", "status", entry.file ? `${(entry.file.size / 1024 / 1024).toFixed(1)} MB · 等待开始` : "已添加 · 等待开始");
  info.append(entry.status);
  entry.progress = document.createElement("progress");
  entry.progress.max = 100;
  entry.progress.value = 0;
  entry.progress.setAttribute("aria-label", `${entry.name} 处理进度`);
  entry.progressPanel = makeElement("div", "job-progress");
  entry.progressPanel.hidden = true;
  const progressHead = makeElement("div", "progress-head");
  entry.progressStage = makeElement("span", "progress-stage");
  entry.progressValue = makeElement("strong", "progress-value");
  entry.progressDetail = makeElement("p", "progress-detail");
  progressHead.append(entry.progressStage, entry.progressValue);
  entry.progressPanel.append(progressHead, entry.progress, entry.progressDetail);
  entry.actions = makeElement("div", "job-actions");
  const remove = makeElement("button", "remove", "移除");
  remove.type = "button";
  remove.addEventListener("click", () => {
    entries.splice(entries.indexOf(entry), 1);
    entry.card.remove();
    updateCount();
  });
  entry.actions.append(remove);
  row.append(makeElement("div", "file-icon", { url: "URL", screenshots: "PNG", downloader: "TOOL" }[entry.kind] || "VIDEO"), info, entry.actions);
  entry.details = document.createElement("details");
  entry.details.hidden = true;
  entry.log = document.createElement("pre");
  entry.details.append(makeElement("summary", "", "查看处理日志"), entry.log);
  entry.card.append(row, entry.progressPanel, entry.details);
  $("jobs").append(entry.card);
  entries.push(entry);
  updateCount();
  return entry;
}

function addFiles(files) {
  const invalid = [];
  for (const file of files) {
    if (!extensions.has(file.name.split(".").pop().toLowerCase()) || file.size === 0) {
      invalid.push(file.name);
      continue;
    }
    addEntry({ file, kind: "file", name: file.name });
  }
  $("notice").textContent = invalid.length ? `未添加空文件或不支持的格式：${invalid.join("、")}` : "";
  rejectedFiles = invalid;
  updateCount();
}

function setProgress(entry, stage, value, percent, detail = "") {
  entry.progressPanel.hidden = false;
  entry.progress.hidden = false;
  entry.progressStage.textContent = stage;
  entry.progressValue.textContent = value;
  entry.progressDetail.textContent = detail;
  entry.progressDetail.hidden = !detail;
  entry.progress.setAttribute("aria-label", `${entry.name} · ${stage}`);
  entry.progress.setAttribute("aria-valuetext", `${value} ${detail}`);
  if (Number.isFinite(percent)) entry.progress.value = percent;
  else entry.progress.removeAttribute("value");
}

function showJobProgress(entry, job) {
  const progress = job.progress || {};
  if (job.status === "error") { entry.progressPanel.hidden = true; return; }
  if (job.status === "queued") {
    setProgress(entry, "等待处理", "排队中", null);
    entry.progress.hidden = true;
  } else if (job.status === "done" || job.status === "screenshots") {
    setProgress(entry, "处理完成", "100%", 100, job.kind === "downloader" ? "下载器已就绪" : `共 ${job.pages} ${job.status === "screenshots" ? "张 PNG" : "页 PDF"}`);
  } else if (job.status === "review") {
    setProgress(entry, "截图已就绪", `共 ${job.pages} 页`, null, "等待检查截图并确认生成 PDF");
    entry.progress.hidden = true;
  } else if (progress.stage === "clean") {
    setProgress(entry, "清理 PDF 页面", `第 ${progress.current} / ${progress.total} 页`, progress.percent,
      `已完成 ${progress.completed} 页 · ${progress.percent}%`);
  } else if (progress.stage === "write") {
    setProgress(entry, "合成 PDF", `共 ${progress.total} 页`, null, "页面处理完毕，正在写入 PDF 文件…");
  } else {
    const label = {extract: "提取视频画面", download: "下载视频", copy: "准备截图"}[progress.stage] || "准备处理";
    const detail = progress.stage === "download" ? "当前下载文件的进度" : progress.stage === "copy" ? `${progress.current} / ${progress.total} 页` : "";
    setProgress(entry, label, Number.isFinite(progress.percent) ? `${progress.percent}%` : "处理中", progress.percent, detail);
  }
}

function upload(entry, options) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    const { crop_top, crop_bottom, remove_qr, mode, sample_interval } = options;
    request.open("POST", "/api/jobs?" + new URLSearchParams({ filename: entry.file.name, crop_top, crop_bottom, remove_qr, mode, sample_interval }));
    request.setRequestHeader("Content-Type", "application/octet-stream");
    request.setRequestHeader("X-Upload-Token", document.querySelector('meta[name="upload-token"]').content);
    request.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) {
        const percent = Math.round(event.loaded / event.total * 100);
        setProgress(entry, "上传到本机", `${percent}%`, percent);
        entry.status.textContent = percent === 100 ? "上传完成，正在加入队列…" : `正在上传 ${percent}%`;
      }
    });
    request.onload = () => {
      try {
        const result = JSON.parse(request.responseText);
        if (request.status !== 202) throw new Error(result.error || "上传失败");
        resolve(result);
      } catch (error) { reject(error); }
    };
    request.onerror = () => reject(new Error("无法连接本机服务，请确认启动窗口仍在运行。"));
    request.send(entry.file);
  });
}

async function post(path, payload = {}) {
  const response = await fetch(path, {
    method: "POST", headers: { "Content-Type": "application/json", "X-Upload-Token": document.querySelector('meta[name="upload-token"]').content },
    body: JSON.stringify(payload),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "操作失败，请查看启动窗口中的日志。");
  return result;
}

async function openFolder(path) {
  try {
    const result = await post(path);
    $("notice").textContent = `已打开文件夹：${result.path}`;
  } catch (error) { $("notice").textContent = error.message; }
}

function actionButton(entry, label, handler) {
  const button = makeElement("button", "secondary", label);
  button.type = "button";
  button.addEventListener("click", async () => {
    button.disabled = true;
    try { await handler(); }
    catch (error) { $("notice").textContent = error.message; }
    finally { button.disabled = false; }
  });
  entry.actions.append(button);
}

function showError(entry, error) {
  entry.state = "error";
  entry.card.className = "job error";
  entry.status.textContent = error.message;
  entry.progress.hidden = true;
  entry.progressPanel.hidden = true;
}

async function poll(entry) {
  try {
    const response = await fetch(`/api/jobs/${entry.id}`);
    if (!response.ok) throw new Error("无法读取任务。服务可能已重启，请重新添加视频。");
    const job = await response.json();
    entry.state = job.status;
    entry.log.textContent = job.log;
    entry.details.hidden = !job.log;
    entry.card.className = `job ${job.status}`;
    entry.status.textContent = { queued: "已加入队列，等待处理…", running: "正在处理，下方显示当前阶段进度。", done: job.kind === "downloader" ? "下载器安装 / 更新完成" : `转换完成 · ${job.pages} 页`, review: `待人工检查 · ${job.pages} 张 PNG。打开目录删除多余页面后，确认生成 PDF。`, screenshots: `已保留 ${job.pages} 张 PNG，未生成 PDF`, error: "处理失败，请展开日志查看原因。" }[job.status];
    showJobProgress(entry, job);
    entry.actions.replaceChildren();
    if (job.status === "done") {
      if (job.download_url) {
        const download = makeElement("a", "download", "下载 PDF ↓");
        download.href = job.download_url;
        entry.actions.append(download);
      }
    } else if (job.status === "review") {
      for (const [choice, label] of [["generate", "确认生成 PDF"], ["keep", "仅保留 PNG"]]) {
        actionButton(entry, label, async () => {
          await post(`/api/jobs/${entry.id}/review`, { action: choice });
          await poll(entry);
        });
      }
    } else if (job.status === "error") {
      entry.details.open = true;
    } else if (job.status !== "screenshots") {
      setTimeout(() => poll(entry), 1000);
    }
    if (job.kind !== "downloader") {
      actionButton(entry, job.screenshots_path ? "📂 打开截图 / 结果" : "📂 打开任务目录", () => openFolder(`/api/jobs/${entry.id}/open`));
    }
  } catch (error) { showError(entry, error); }
}

$("choose").addEventListener("click", () => $("file-input").click());
$("open-output").addEventListener("click", () => openFolder("/api/output/open"));
const sourceKinds = ["files", "url", "local", "screenshots"];
function selectSource(kind) {
  for (const name of sourceKinds) {
    $("panel-" + name).hidden = name !== kind;
    $("tab-" + name).setAttribute("aria-selected", String(name === kind));
  }
}
for (const kind of sourceKinds) {
  $("tab-" + kind).addEventListener("click", () => selectSource(kind));
  $("tab-" + kind).addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    const index = (sourceKinds.indexOf(kind) + (event.key === "ArrowRight" ? 1 : -1) + sourceKinds.length) % sourceKinds.length;
    selectSource(sourceKinds[index]);
    $("tab-" + sourceKinds[index]).focus();
  });
}
for (const [kind, field] of [["url", "video-urls"], ["local", "video-paths"], ["screenshots", "screenshots-path"]]) {
  $("add-" + kind).addEventListener("click", () => {
    const sources = $(field).value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
    if (!sources.length) { $("notice").textContent = "请先填写输入来源。"; return; }
    if (kind === "url" && sources.some((source) => {
      try { return !["http:", "https:"].includes(new URL(source).protocol); }
      catch { return true; }
    })) { $("notice").textContent = "请输入完整的 HTTP(S) 视频网址。"; return; }
    for (const source of sources) addEntry({ kind, source, name: source });
    $(field).value = "";
    $("notice").textContent = "";
  });
}
$("setup-downloader").addEventListener("click", async () => {
  $("setup-downloader").disabled = true;
  try {
    const job = await post("/api/downloader");
    const entry = addEntry({ name: job.filename, kind: "downloader", state: "queued", id: job.id });
    entry.actions.replaceChildren();
    poll(entry);
  } catch (error) { $("notice").textContent = error.message; }
  finally { $("setup-downloader").disabled = false; }
});
$("file-input").addEventListener("change", (event) => {
  addFiles(event.target.files);
  event.target.value = "";
});
// Prevent the browser from navigating to a file dropped outside the drop zone.
window.addEventListener("dragover", (event) => event.preventDefault());
window.addEventListener("drop", (event) => event.preventDefault());
let dragDepth = 0;
$("drop-zone").addEventListener("dragenter", (event) => {
  event.preventDefault();
  dragDepth += 1;
  $("drop-zone").classList.add("dragging");
});
$("drop-zone").addEventListener("dragleave", () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) $("drop-zone").classList.remove("dragging");
});
$("drop-zone").addEventListener("drop", (event) => {
  event.preventDefault();
  dragDepth = 0;
  $("drop-zone").classList.remove("dragging");
  addFiles(event.dataTransfer.files);
});

$("convert-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const options = { crop_top: $("crop-top").value, crop_bottom: $("crop-bottom").value, remove_qr: String($("remove-qr").checked), sample_interval: $("sample-interval").value, mode: $("output-mode").value };
  const downloadOptions = { referer: $("referer").value.trim(), cookies_from_browser: $("cookies-from-browser").value.trim(), add_headers: $("add-headers").value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean) };
  const pending = entries.filter((entry) => entry.state === "pending");
  if (uploading || !pending.length) return;
  uploading = true;
  // Reserve every entry now so it cannot be removed while awaiting an upload.
  for (const entry of pending) {
    entry.state = "uploading";
    entry.actions.replaceChildren();
    entry.status.textContent = "等待提交…";
  }
  updateCount();
  for (const entry of pending) {
    entry.status.textContent = entry.kind === "file" ? "正在上传…" : "正在提交…";
    setProgress(entry, entry.kind === "file" ? "上传到本机" : "提交任务", entry.kind === "file" ? "0%" : "请稍候", entry.kind === "file" ? 0 : null);
    try {
      const job = entry.kind === "file" ? await upload(entry, options) : await post("/api/sources", {
        kind: entry.kind, source: entry.source, options: { ...options, ...(entry.kind === "url" ? downloadOptions : {}) },
      });
      entry.id = job.id;
      poll(entry);
    } catch (error) { showError(entry, error); }
    entry.file = null;
  }
  uploading = false;
  updateCount();
});
