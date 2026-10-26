const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

function loadBackground(sourceUrl = "blob:https://example.com/current", page = null) {
  const menus = [];
  const queued = [];
  const opened = [];
  let installed;
  let clicked;
  let captures = 0;
  const chrome = {
    contextMenus: {
      removeAll(callback) { callback(); },
      create(item) { menus.push(item); },
      onClicked: { addListener(callback) { clicked = callback; } },
    },
    runtime: { onInstalled: { addListener(callback) { installed = callback; } } },
    sidePanel: {
      setPanelBehavior() { return Promise.resolve(); },
      open(target) { opened.push(target); return Promise.resolve(); },
    },
    storage: { session: { async set(value) { queued.push(value.tayoPending); } } },
    scripting: {
      async executeScript(options) {
        if (page) return [{ result: options.func(...(options.args || [])) }];
        if (!options.args) {
          return [{ result: "This is a readable article paragraph with enough text to inspect." }];
        }
        return [{ result: {
          left: 10, top: 20, width: 300, height: 200,
          viewportWidth: 800, viewportHeight: 600,
          sourceUrl,
        } }];
      },
    },
    tabs: {
      async get() { return { active: true, windowId: 3 }; },
      async captureVisibleTab() {
        captures += 1;
        return "data:image/jpeg;base64,ZmFrZQ==";
      },
    },
  };
  const source = fs.readFileSync(path.join(__dirname, "../extension/background.js"), "utf8");
  vm.runInNewContext(source, { chrome, console, Date, document: page?.document,
    window: page?.window });
  installed();
  return { menus, queued, opened, clicked: (info) => clicked(info, { id: 8, windowId: 3 }),
    captures: () => captures };
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

test("video right-click is registered and queues a direct video URL", async () => {
  const app = loadBackground();
  assert.ok(app.menus.some((item) => item.id === "tayo-analyze-video" &&
    item.contexts.includes("video")));
  app.clicked({ menuItemId: "tayo-analyze-video", srcUrl: "https://example.com/clip.mp4" });
  await settle();
  assert.equal(app.queued[0].type, "video");
  assert.equal(app.queued[0].value, "https://example.com/clip.mp4");
  assert.equal(app.queued[0].frame.rect.width, 300);
  assert.equal(app.captures(), 1);
  assert.equal(app.opened[0].tabId, 8);
});

test("blob video right-click queues a visible-frame capture", async () => {
  const app = loadBackground();
  app.clicked({ menuItemId: "tayo-analyze-video", srcUrl: "blob:https://example.com/123", frameId: 0 });
  await settle();
  assert.equal(app.queued[0].type, "video-frame");
  assert.equal(app.queued[0].rect.width, 300);
  assert.equal(app.captures(), 1);
});

test("page right-click can inspect the largest visible video", async () => {
  const app = loadBackground("https://example.com/from-player.mp4");
  assert.ok(app.menus.some((item) => item.id === "tayo-inspect-visible-video" &&
    item.contexts.includes("page")));
  app.clicked({ menuItemId: "tayo-inspect-visible-video", frameId: 0 });
  await settle();
  assert.equal(app.queued[0].type, "video");
  assert.equal(app.queued[0].value, "https://example.com/from-player.mp4");
});

test("selected text can go directly to AI writing analysis", async () => {
  const app = loadBackground();
  assert.ok(app.menus.some((item) => item.id === "tayo-analyze-selection" &&
    item.contexts.includes("selection")));
  app.clicked({ menuItemId: "tayo-analyze-selection", selectionText: "A selected paragraph with enough text." });
  await settle();
  assert.equal(app.queued[0].type, "text");
  assert.equal(app.queued[0].value, "A selected paragraph with enough text.");
  assert.equal(app.opened.length, 1);
});

test("page text can be analyzed without selecting it", async () => {
  const app = loadBackground();
  assert.ok(app.menus.some((item) => item.id === "tayo-analyze-page-text" &&
    item.contexts.includes("page")));
  app.clicked({ menuItemId: "tayo-analyze-page-text" });
  await settle();
  assert.equal(app.queued[0].type, "text");
  assert.match(app.queued[0].value, /readable article/);
});

test("public image right-click keeps URL and screenshot fallback", async () => {
  const app = loadBackground("https://example.com/photo.jpg");
  app.clicked({ menuItemId: "tayo-analyze-image", srcUrl: "https://example.com/photo.jpg" });
  await settle();
  assert.equal(app.queued[0].type, "image");
  assert.equal(app.queued[0].value, "https://example.com/photo.jpg");
  assert.equal(app.queued[0].frame.rect.width, 300);
});

test("blob image right-click uses the displayed image", async () => {
  const app = loadBackground();
  app.clicked({ menuItemId: "tayo-analyze-image", srcUrl: "blob:https://example.com/current" });
  await settle();
  assert.equal(app.queued[0].type, "image-frame");
  assert.equal(app.captures(), 1);
});

test("image capture selects the clicked image when several are visible", async () => {
  const makeImage = (src, left, width) => ({
    currentSrc: src, src,
    getBoundingClientRect: () => ({ left, top: 20, right: left + width,
      bottom: 220, width, height: 200 }),
  });
  const images = [
    makeImage("https://example.com/large.jpg", 0, 600),
    makeImage("blob:https://example.com/target", 620, 150),
  ];
  const page = {
    document: { querySelectorAll: () => images },
    window: { innerWidth: 800, innerHeight: 600 },
  };
  const app = loadBackground("blob:https://example.com/target", page);
  app.clicked({ menuItemId: "tayo-analyze-image",
    srcUrl: "blob:https://example.com/target" });
  await settle();
  assert.equal(app.queued[0].type, "image-frame");
  assert.equal(app.queued[0].rect.left, 620);
  assert.equal(app.queued[0].rect.width, 150);
});

test("page text prefers article content", async () => {
  const page = {
    document: {
      querySelectorAll: () => [
        { innerText: "Short note" },
        { innerText: "This is the longer article content that should be analyzed." },
      ],
      querySelector: () => ({ innerText: "Main content" }),
      body: { innerText: "Entire page" },
    },
    window: {},
  };
  const app = loadBackground("", page);
  app.clicked({ menuItemId: "tayo-analyze-page-text" });
  await settle();
  assert.equal(app.queued[0].type, "text");
  assert.match(app.queued[0].value, /longer article content/);
});
