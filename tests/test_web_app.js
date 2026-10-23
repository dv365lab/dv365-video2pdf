// Exercise the actual drop and submit handlers without browser dependencies.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

class Element {
  constructor() {
    this.children = [];
    this.handlers = {};
    this.value = "0";
    this.classList = { add() {}, remove() {} };
  }
  append(...children) { this.children.push(...children); }
  addEventListener(name, handler) { this.handlers[name] = handler; }
  setAttribute(name, value) { this[name] = value; }
  removeAttribute(name) { delete this[name]; }
  replaceChildren(...children) { this.children = children; }
  remove() { this.removed = true; }
}

async function run() {
  const elements = new Map();
  const getElement = (id) => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  getElement("convert").disabled = true;
  getElement("sample-interval").value = "1";
  getElement("output-mode").value = "auto";
  for (const id of ["referer", "cookies-from-browser", "add-headers"]) getElement(id).value = "";
  const requests = [];
  const apiRequests = [];
  const jobs = new Map();
  const scheduledPolls = [];
  let nextJobState = null;
  let pollFailure = null;
  let failUpload = false;
  let expireToken = false;
  let restoredJobs = [];
  let legacyListing = false;
  let sourceId = 0;
  class Upload {
    constructor() { this.upload = new Element(); }
    open(method, url) { this.url = url; }
    setRequestHeader() {}
    send(file) {
      requests.push({ url: this.url, file });
      if (failUpload) { failUpload = false; this.onerror(); return; }
      this.status = 202;
      this.responseText = JSON.stringify({ id: String(requests.length) });
      this.onload();
    }
  }
  const browserWindow = new Element();
  browserWindow.location = {hash: "#jobs=" + "c".repeat(32) + ",invalid"};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8"), {
    document: { getElementById: getElement, createElement: () => new Element(), querySelector: () => ({ content: "token" }) },
    window: browserWindow,
    XMLHttpRequest: Upload,
    URLSearchParams,
    URL,
    AbortController,
    setTimeout: (callback, delay) => {
      if (delay === 15000) return {};
      scheduledPolls.push(callback);
      return callback;
    },
    clearTimeout: (callback) => {
      const index = scheduledPolls.indexOf(callback);
      if (index !== -1) scheduledPolls.splice(index, 1);
    },
    fetch: async (url, options) => {
      if (options?.method === "POST") {
        const body = JSON.parse(options.body);
        apiRequests.push({ url, body, headers: options.headers });
        assert.equal(options.headers["X-Upload-Token"], "token");
        if (expireToken) { expireToken = false; return { ok: false, status: 403, json: async () => ({error: "Expired token"}) }; }
        if (url === "/api/local-sources") return { ok: true, json: async () => ({ videos: body.source === "F:/Batch" ? ["F:/Batch/one.mp4", "F:/Batch/two.avi"] : [body.source] }) };
        if (url.endsWith("/retry")) {
          const parent = jobs.get(url.replace("/retry", ""));
          const id = "retry" + parent.id;
          const retry = { ...parent, id, status: "done", download_url: `/api/jobs/${id}/pdf` };
          jobs.set("/api/jobs/" + id, retry);
          parent.retry_job_id = id;
          return { ok: true, json: async () => retry };
        }
        if (url === "/api/video-folder") {
          if (body.source === "missing") return { ok: false, json: async () => ({ error: "视频文件夹不存在" }) };
          return { ok: true, json: async () => ({ videos: ["F:/课程/01.avi", "F:/课程/02.mp4"] }) };
        }
        if (url.endsWith("/open")) return { ok: true, json: async () => ({ path: "F:/Projects/video2pdf/output" }) };
        if (url.endsWith("/review")) {
          const job = jobs.get(url.replace("/review", ""));
          job.status = body.action === "generate" ? "done" : "screenshots";
          job.download_url = body.action === "generate" ? "result.pdf" : null;
          return { ok: true, json: async () => job };
        }
        const id = "source" + (++sourceId);
        const kind = url === "/api/downloader" ? "downloader" : body.kind;
        const job = { id, kind, filename: "下载器", pages: 2, log: "complete", screenshots_path: "output/test", status: body.options?.mode === "review" ? "review" : "done", download_url: kind === "downloader" ? null : "result.pdf", ...nextJobState };
        jobs.set("/api/jobs/" + id, job);
        return { ok: true, json: async () => ({ id, kind, filename: job.filename }) };
      }
      if (url === "/api/session") return { ok: true, json: async () => ({ upload_token: "token" }) };
      if (url === "/api/jobs") return legacyListing
        ? { ok: false, status: 404, json: async () => ({error:"旧服务"}) }
        : { ok: true, json: async () => ({ jobs: restoredJobs }) };
      if (pollFailure) { const error = pollFailure; pollFailure = null; throw error; }
      return { ok: true, json: async () => jobs.get(url) || ({ status: "done", pages: 2, log: "complete", download_url: url + "/pdf" }) };
    },
  });
  await new Promise((resolve) => setImmediate(resolve));
  const drop = getElement("drop-zone").handlers.drop;
  drop({ preventDefault() {}, dataTransfer: {files: [{name: "invalid.txt", size: 10}]} });
  assert.equal(getElement("drop-title").textContent, "文件未添加成功");
  assert.match(getElement("drop-feedback-help").textContent, /invalid.txt/);
  let prevented = false;
  drop({ preventDefault: () => { prevented = true; }, dataTransfer: { files: [
    { name: "lesson.1.mp4", size: 100 }, { name: "lesson.2.avi", size: 200 },
    { name: "notes.txt", size: 10 }, { name: "empty.mp4", size: 0 },
  ] } });
  assert.equal(prevented, true, "drop must prevent browser file navigation");
  assert.equal(getElement("queue-count").textContent, "2 个任务");
  assert.equal(getElement("convert").disabled, false);
  assert.match(getElement("notice").textContent, /notes.txt.*empty.mp4/);
  assert.equal(getElement("drop-title").textContent, "已添加 2 个视频");
  assert.equal(getElement("drop-feedback").hidden, false);
  assert.match(getElement("drop-feedback-files").textContent, /lesson.1.mp4.*lesson.2.avi/);
  assert.match(getElement("drop-feedback-help").textContent, /notes.txt.*empty.mp4/);
  const firstCard = getElement("jobs").children[0];
  firstCard.children[0].children[2].children[0].handlers.click();
  assert.equal(firstCard.removed, true);
  assert.equal(getElement("queue-count").textContent, "1 个任务");
  assert.equal(getElement("drop-title").textContent, "已添加 1 个视频");
  assert.doesNotMatch(getElement("drop-feedback-files").textContent, /lesson.1.mp4/);
  // A file name resembling markup must remain plain text.
  getElement("file-input").handlers.change({ target: { files: [{ name: "<img>.mp4", size: 100 }] } });
  const lastCard = getElement("jobs").children[2];
  assert.equal(lastCard.children[0].children[1].children[0].textContent, "<img>.mp4");
  getElement("crop-top").value = "4";
  getElement("crop-bottom").value = "6";
  getElement("remove-qr").checked = true;
  await getElement("convert-form").handlers.submit({ preventDefault() {} });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(requests.length, 2, "all remaining files must be uploaded");
  for (const request of requests) {
    const query = new URL("http://localhost" + request.url).searchParams;
    assert.equal(query.get("crop_top"), "4");
    assert.equal(query.get("crop_bottom"), "6");
    assert.equal(query.get("remove_qr"), "true");
    assert.equal(query.get("sample_interval"), "1");
    assert.equal(query.get("mode"), "auto");
  }
  assert.equal(lastCard.className, "job done");
  assert.equal(lastCard.children[0].children[2].children[0].textContent, "下载 PDF ↓");
  assert.equal(getElement("convert").disabled, true);
  assert.equal(lastCard.children[1].hidden, false);
  assert.equal(lastCard.children[1].children[0].children[1].textContent, "100%");
  assert.match(getElement("drop-feedback-help").textContent, /已提交/);
  assert.equal(scheduledPolls.length, 0, 'completed jobs must stop polling');

  await getElement("open-output").handlers.click();
  assert.equal(apiRequests.at(-1).url, "/api/output/open");
  assert.match(getElement("notice").textContent, /已打开文件夹/);
  getElement("tab-url").handlers.click();
  assert.equal(getElement("panel-url").hidden, false);
  assert.equal(getElement("panel-files").hidden, true);
  assert.equal(getElement("tab-url")["aria-selected"], "true");
  getElement("video-urls").value = "file:///C:/Windows";
  getElement("add-url").handlers.click();
  assert.match(getElement("notice").textContent, /HTTP/);
  getElement("video-urls").value = "https://example.com/video?a=1&b=2";
  getElement("add-url").handlers.click();
  const urlCard = getElement("jobs").children.at(-1);
  getElement("video-paths").value = "F:\\Videos\\course.avi";
  await getElement("add-local").handlers.click();
  const localCard = getElement("jobs").children.at(-1);
  getElement("screenshots-path").value = "F:\\Slides\\1.1";
  getElement("add-screenshots").handlers.click();
  getElement("output-mode").value = "review";
  getElement("sample-interval").value = "0.5";
  getElement("referer").value = "https://play.vidyard.com/";
  getElement("cookies-from-browser").value = "edge:Default";
  getElement("add-headers").value = "User-Agent: Example\nAuthorization: Bearer test";
  await getElement("convert-form").handlers.submit({ preventDefault() {} });
  await new Promise((resolve) => setImmediate(resolve));
  const sources = apiRequests.filter((request) => request.url === "/api/sources");
  assert.deepEqual(sources.map((request) => request.body.kind), ["url", "local", "screenshots"]);
  assert.equal(sources[0].body.source, "https://example.com/video?a=1&b=2");
  assert.equal(sources[0].body.options.sample_interval, "0.5");
  assert.equal(sources[0].body.options.cookies_from_browser, "edge:Default");
  assert.deepEqual(sources[0].body.options.add_headers, ["User-Agent: Example", "Authorization: Bearer test"]);
  assert.equal(sources[1].body.options.referer, undefined);
  assert.equal(urlCard.className, "job review");
  assert.match(urlCard.children[0].children[1].children[1].textContent, /待人工检查/);
  const urlActions = urlCard.children[0].children[2];
  await urlActions.children[2].handlers.click();
  assert.match(apiRequests.at(-1).url, /\/open$/);
  await urlActions.children[0].handlers.click();
  assert.equal(urlCard.className, "job done");
  assert.equal(urlActions.children[0].textContent, "下载 PDF ↓");
  await localCard.children[0].children[2].children[1].handlers.click();
  assert.equal(localCard.className, "job screenshots");
  await getElement("setup-downloader").handlers.click();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(apiRequests.at(-1).url, "/api/downloader");
  assert.equal(getElement("jobs").children.at(-1).className, "job done");

  nextJobState = { status: 'running', progress: {stage: 'extract', percent: 42} };
  getElement('video-urls').value = 'https://example.com/progress.mp4';
  getElement('add-url').handlers.click();
  getElement('output-mode').value = 'auto';
  await getElement('convert-form').handlers.submit({preventDefault() {}});
  await new Promise((resolve) => setImmediate(resolve));
  const progressCard = getElement('jobs').children.at(-1);
  const panel = progressCard.children[1];
  const progressJob = jobs.get('/api/jobs/source' + sourceId);
  assert.equal(panel.hidden, false);
  assert.equal(panel.children[0].children[1].textContent, '42%');
  assert.equal(panel.children[1].value, 42);
  pollFailure = new TypeError("Failed to fetch");
  await scheduledPolls.shift()();
  assert.notEqual(progressCard.className, "job error", "a disconnected poll is not a conversion failure");
  assert.match(progressCard.children[0].children[1].children[1].textContent, /连接|重连/);
  assert.equal(scheduledPolls.length, 1, "connection failure must continue polling");
  await scheduledPolls.shift()();
  assert.equal(progressCard.className, "job running");
  progressJob.progress = {stage:'clean', current:3, total:8, completed:2, percent:25};
  await scheduledPolls.shift()();
  assert.equal(panel.children[0].children[1].textContent, '第 3 / 8 页');
  assert.match(panel.children[2].textContent, /已完成 2 页.*25%/);
  progressJob.progress = {stage:'write', current:8, total:8, percent:null};
  await scheduledPolls.shift()();
  assert.equal(panel.children[0].children[1].textContent, '共 8 页');
  assert.equal(panel.children[1].value, undefined, 'writing must not show an invented percentage');
  progressJob.progress = {stage:'extract', total:null, percent:null};
  await scheduledPolls.shift()();
  assert.equal(panel.children[0].children[1].textContent, '处理中');
  progressJob.status = 'error';
  await scheduledPolls.shift()();
  assert.equal(panel.hidden, true, 'an error must not retain a success progress display');
  assert.equal(scheduledPolls.length, 0);
  nextJobState = null;
  getElement("tab-folder").handlers.click();
  assert.equal(getElement("panel-folder").hidden, false);
  assert.equal(getElement("panel-url").hidden, true);
  getElement("video-folder").value = "";
  await getElement("scan-folder").handlers.click();
  assert.match(getElement("folder-feedback").textContent, /请先填写/);
  getElement("video-folder").value = "F:/课程";
  const previousCardCount = getElement("jobs").children.length;
  await getElement("scan-folder").handlers.click();
  assert.equal(apiRequests.at(-1).body.source, "F:/课程");
  assert.equal(getElement("jobs").children.length, previousCardCount + 2);
  assert.match(getElement("folder-feedback").textContent, /找到 2 个视频，新增 2/);
  await getElement("scan-folder").handlers.click();
  assert.equal(getElement("jobs").children.length, previousCardCount + 2);
  assert.match(getElement("folder-feedback").textContent, /新增 0/);
  getElement("video-folder").value = "missing";
  await getElement("scan-folder").handlers.click();
  assert.match(getElement("folder-feedback").textContent, /视频文件夹不存在/);
  assert.equal(getElement("scan-folder").disabled, false);
  assert.equal(getElement("jobs").children.length, previousCardCount + 2);
  await getElement("convert-form").handlers.submit({preventDefault() {}});
  await new Promise((resolve) => setImmediate(resolve));
  const folderRequests = apiRequests.filter((request) => request.url === "/api/sources").slice(-2);
  assert.deepEqual(folderRequests.map((request) => request.body.source), ["F:/课程/01.avi", "F:/课程/02.mp4"]);
  assert.ok(folderRequests.every((request) => request.body.kind === "local" && request.body.options.mode === "auto"));
  for (const card of getElement("jobs").children.slice(-2)) {
    assert.equal(card.className, "job done");
    assert.equal(card.children[0].children[2].children[0].textContent, "下载 PDF ↓");
  }
  // A path can be a directory. Clicking Add resolves all videos without a file picker.
  getElement("video-paths").value = "F:/Batch";
  const beforeLocal = getElement("jobs").children.length;
  expireToken = true;
  await getElement("add-local").handlers.click();
  assert.equal(getElement("jobs").children.length, beforeLocal + 2);
  assert.match(getElement("notice").textContent, /添加 2 个视频/);
  assert.equal(getElement("jobs").children.at(-1).children[0].children[1].children[0].textContent, "F:/Batch/two.avi");
  nextJobState = {status: "error", retry_available: true};
  await getElement("convert-form").handlers.submit({preventDefault() {}});
  nextJobState = null;
  const beforeRetries = apiRequests.filter((request) => request.url.endsWith("/retry")).length;
  await getElement("retry-failed").handlers.click();
  assert.equal(apiRequests.filter((request) => request.url.endsWith("/retry")).length, beforeRetries + 3);
  assert.equal(progressCard.className, "job done", "bulk retry includes the previous failed conversion");
  assert.equal(getElement("retry-failed").disabled, true);
  for (const card of getElement("jobs").children.slice(-2)) assert.equal(card.className, "job done");

  // A failed upload keeps the selected File, and retry automatically requests a PDF.
  failUpload = true;
  const retryFile = {name: "upload-retry.avi", size: 120};
  getElement("file-input").handlers.change({target: {files: [retryFile]}});
  getElement("output-mode").value = "png";
  await getElement("convert-form").handlers.submit({preventDefault() {}});
  const uploadCard = getElement("jobs").children.at(-1);
  assert.equal(uploadCard.className, "job error");
  await uploadCard.children[0].children[2].children[0].handlers.click();
  assert.equal(requests.at(-1).file, retryFile);
  assert.equal(new URL("http://localhost" + requests.at(-1).url).searchParams.get("mode"), "auto");
  assert.equal(uploadCard.className, "job done");

  // Refresh recovers jobs without re-submitting successful videos or showing retry parents.
  const restored = {id: "restored", filename:"已完成.mp4", kind:"local", status:"done", pages:4, download_url:"result.pdf", log:""};
  restoredJobs = [{...restored, id: "parent", status:"error", retry_job_id:"restored"}, restored];
  const cardsBeforeRestore = getElement("jobs").children.length;
  const postsBeforeRestore = apiRequests.length;
  await getElement("restore-jobs").handlers.click();
  await getElement("restore-jobs").handlers.click();
  assert.equal(getElement("jobs").children.length, cardsBeforeRestore + 1);
  assert.equal(apiRequests.length, postsBeforeRestore);
  assert.equal(getElement("jobs").children.at(-1).className, "job done");
  legacyListing = true;
  jobs.set("/api/jobs/" + "c".repeat(32), {...restored, id:"c".repeat(32), filename:"旧批次.mp4"});
  await getElement("restore-jobs").handlers.click();
  assert.match(getElement("notice").textContent, /已恢复 1 个任务.*旧版服务/);
  assert.equal(getElement("jobs").children.at(-1).className, "job done");
  assert.equal(apiRequests.length, postsBeforeRestore, "recovery of an old batch must never restart conversion");
  console.log("Uploads, drop feedback, stage percentages, page counts, review and folder actions: passed");
}

run().catch((error) => { console.error(error); process.exitCode = 1; });
