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
  setAttribute() {}
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
  const requests = [];
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
    setTimeout: () => { throw new Error("completed jobs must stop polling"); },
    fetch: async (url) => ({ ok: true, json: async () => ({ status: "done", pages: 2, log: "complete", download_url: url + "/pdf" }) }),
  });
  const drop = getElement("drop-zone").handlers.drop;
  let prevented = false;
  drop({ preventDefault: () => { prevented = true; }, dataTransfer: { files: [
    { name: "lesson.1.mp4", size: 100 }, { name: "lesson.2.avi", size: 200 },
    { name: "notes.txt", size: 10 }, { name: "empty.mp4", size: 0 },
  ] } });
  assert.equal(prevented, true, "drop must prevent browser file navigation");
  assert.equal(getElement("queue-count").textContent, "2 个视频");
  assert.equal(getElement("convert").disabled, false);
  assert.match(getElement("notice").textContent, /notes.txt.*empty.mp4/);
  const firstCard = getElement("jobs").children[0];
  firstCard.children[0].children[2].children[0].handlers.click();
  assert.equal(firstCard.removed, true);
  assert.equal(getElement("queue-count").textContent, "1 个视频");
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
  }
  assert.equal(lastCard.className, "job done");
  assert.equal(lastCard.children[0].children[2].children[0].textContent, "下载 PDF ↓");
  assert.equal(getElement("convert").disabled, true);
  console.log("Drop, validation, removal, safe names, bulk options and PDF links: passed");
}

run().catch((error) => { console.error(error); process.exitCode = 1; });
