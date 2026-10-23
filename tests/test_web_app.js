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
  let sourceId = 0;
  class Upload {
    constructor() { this.upload = new Element(); }
    open(method, url) { this.url = url; }
    setRequestHeader() {}
    send(file) {
      requests.push({ url: this.url, file });
      this.status = 202;
      this.responseText = JSON.stringify({ id: String(requests.length) });
      this.onload();
    }
  }
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8"), {
    document: { getElementById: getElement, createElement: () => new Element(), querySelector: () => ({ content: "token" }) },
    window: new Element(),
    XMLHttpRequest: Upload,
    URLSearchParams,
    URL,
    setTimeout: (callback) => scheduledPolls.push(callback),
    fetch: async (url, options) => {
      if (options) {
        const body = JSON.parse(options.body);
        apiRequests.push({ url, body, headers: options.headers });
        assert.equal(options.headers["X-Upload-Token"], "token");
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
      return { ok: true, json: async () => jobs.get(url) || ({ status: "done", pages: 2, log: "complete", download_url: url + "/pdf" }) };
    },
  });
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
  getElement("add-local").handlers.click();
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
  console.log("Uploads, drop feedback, stage percentages, page counts, review and folder actions: passed");
}

run().catch((error) => { console.error(error); process.exitCode = 1; });
