"use strict";

const $ = (id) => document.getElementById(id);
const extensions = new Set(["mp4", "avi", "mov", "mkv", "webm", "m4v", "wmv", "mpg", "mpeg", "ts", "mts", "m2ts", "flv"]);
const entries = [];
let uploading = false;

function updateCount() {
  const pending = entries.filter((entry) => entry.state === "pending").length;
  $("selection-count").textContent = pending ? `${pending} 个视频待转换` : "还未添加待转换视频";
  $("queue-count").textContent = `${entries.length} 个视频`;
  $("empty").hidden = entries.length > 0;
  $("convert").disabled = !pending || uploading;
}

function makeElement(tag, className, text) {
  const element = document.createElement(tag);
  element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function addFiles(files) {
  const invalid = [];
  for (const file of files) {
    if (!extensions.has(file.name.split(".").pop().toLowerCase()) || file.size === 0) {
      invalid.push(file.name);
      continue;
    }
    const entry = { file, state: "pending", id: null };
    entry.card = makeElement("article", "job");
    const row = makeElement("div", "job-main");
    const info = makeElement("div", "job-info");
    info.append(makeElement("div", "filename", file.name));
    entry.status = makeElement("p", "status", `${(file.size / 1024 / 1024).toFixed(1)} MB · 等待开始`);
    info.append(entry.status);
    entry.progress = document.createElement("progress");
    entry.progress.max = 100;
    entry.progress.value = 0;
    entry.progress.hidden = true;
    entry.progress.setAttribute("aria-label", `${file.name} 上传进度`);
    info.append(entry.progress);
    entry.actions = makeElement("div", "job-actions");
    const remove = makeElement("button", "remove", "移除");
    remove.type = "button";
    remove.addEventListener("click", () => {
      entries.splice(entries.indexOf(entry), 1);
      entry.card.remove();
      updateCount();
    });
    entry.actions.append(remove);
    row.append(makeElement("div", "file-icon", "VIDEO"), info, entry.actions);
    entry.details = document.createElement("details");
    entry.details.hidden = true;
    entry.log = document.createElement("pre");
    entry.details.append(makeElement("summary", "", "查看处理日志"), entry.log);
    entry.card.append(row, entry.details);
    $("jobs").append(entry.card);
    entries.push(entry);
  }
  $("notice").textContent = invalid.length ? `未添加空文件或不支持的格式：${invalid.join("、")}` : "";
  updateCount();
}

function upload(entry, options) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", "/api/jobs?" + new URLSearchParams({ filename: entry.file.name, ...options }));
    request.setRequestHeader("Content-Type", "application/octet-stream");
    request.setRequestHeader("X-Upload-Token", document.querySelector('meta[name="upload-token"]').content);
    request.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) {
        const percent = Math.round(event.loaded / event.total * 100);
        entry.progress.value = percent;
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

function showError(entry, error) {
  entry.state = "error";
  entry.card.className = "job error";
  entry.status.textContent = error.message;
  entry.progress.hidden = true;
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
    entry.status.textContent = { queued: "已加入队列，等待转换…", running: "正在提取画面并生成 PDF…", done: `转换完成 · ${job.pages} 页`, error: "转换失败，请展开日志查看原因。" }[job.status];
    if (job.status === "done") {
      const download = makeElement("a", "download", "下载 PDF ↓");
      download.href = job.download_url;
      entry.actions.append(download);
    } else if (job.status === "error") {
      entry.details.open = true;
    } else {
      setTimeout(() => poll(entry), 1000);
    }
  } catch (error) { showError(entry, error); }
}

$("choose").addEventListener("click", () => $("file-input").click());
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
  const options = { crop_top: $("crop-top").value, crop_bottom: $("crop-bottom").value, remove_qr: String($("remove-qr").checked) };
  const pending = entries.filter((entry) => entry.state === "pending");
  if (uploading || !pending.length) return;
  uploading = true;
  // Reserve every entry now so it cannot be removed while awaiting an upload.
  for (const entry of pending) {
    entry.state = "uploading";
    entry.actions.replaceChildren();
    entry.status.textContent = "等待上传…";
  }
  updateCount();
  for (const entry of pending) {
    entry.status.textContent = "正在上传…";
    entry.progress.hidden = false;
    try {
      const job = await upload(entry, options);
      entry.id = job.id;
      entry.progress.hidden = true;
      poll(entry);
    } catch (error) { showError(entry, error); }
    entry.file = null;
  }
  uploading = false;
  updateCount();
});
