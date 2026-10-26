/* TAYO MV3 service worker.
 *
 * The extension never loads ML models itself. It forwards user-initiated
 * requests to the local FastAPI backend, which owns the heavy dependencies.
 */

const MENU_SELECTION = "tayo-factcheck-selection";
const MENU_TEXT = "tayo-analyze-selection";
const MENU_PAGE_TEXT = "tayo-analyze-page-text";
const MENU_LINK = "tayo-scan-link";
const MENU_IMAGE = "tayo-analyze-image";
const MENU_VIDEO = "tayo-analyze-video";
const MENU_VISIBLE_VIDEO = "tayo-inspect-visible-video";

function createMenus() {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: MENU_SELECTION,
      title: "Fact-check selected text",
      contexts: ["selection"],
    });
    chrome.contextMenus.create({
      id: MENU_TEXT,
      title: "Analyze selected text for AI generation",
      contexts: ["selection"],
    });
    chrome.contextMenus.create({
      id: MENU_PAGE_TEXT,
      title: "Analyze page text with TAYO",
      contexts: ["page"],
    });
    chrome.contextMenus.create({
      id: MENU_LINK,
      title: "Scan link for phishing risk",
      contexts: ["link"],
    });
    chrome.contextMenus.create({
      id: MENU_IMAGE,
      title: "Analyze image for AI generation",
      contexts: ["image"],
    });
    chrome.contextMenus.create({
      id: MENU_VIDEO,
      title: "Inspect video with TAYO",
      contexts: ["video"],
    });
    chrome.contextMenus.create({
      id: MENU_VISIBLE_VIDEO,
      title: "Inspect visible video with TAYO",
      contexts: ["page"],
    });
  });
}

chrome.runtime.onInstalled.addListener(() => {
  createMenus();
  chrome.sidePanel
    .setPanelBehavior({ openPanelOnActionClick: true })
    .catch((error) => console.warn("sidePanel behavior:", error));
});

// Also set this on browser startup, not only on install.
chrome.sidePanel
  .setPanelBehavior({ openPanelOnActionClick: true })
  .catch((error) => console.warn("sidePanel behavior:", error));

async function queueRequest(request, tab, openPanel = true) {
  const pending = { ...request, createdAt: Date.now() };
  // Session storage survives the panel being closed and reopened, and keeps
  // selections out of long-term storage.
  await chrome.storage.session.set({ tayoPending: pending });
  if (openPanel && tab?.id != null) {
    try {
      await chrome.sidePanel.open({ tabId: tab.id });
    } catch (error) {
      // Chrome may reject programmatic opening on some versions. The pending
      // request is still stored, so it appears when the user opens the panel.
      console.warn("Could not open side panel automatically:", error);
    }
  }
}

function openPanelFromGesture(tab) {
  if (tab?.id != null) {
    chrome.sidePanel.open({ tabId: tab.id }).catch((error) =>
      console.warn("Could not open side panel automatically:", error),
    );
  }
}

async function captureMediaFrame(info, tab, selector) {
  if (tab?.id == null || info.frameId > 0) {
    throw new Error("This media element cannot be captured from the page frame.");
  }
  const src = info.srcUrl || "";
  const [injection] = await chrome.scripting.executeScript({
    target: { tabId: tab.id, frameIds: [0] },
    args: [src, selector],
    func: (sourceUrl, mediaSelector) => {
      const media = [...document.querySelectorAll(mediaSelector)];
      const visible = media
        .map((element) => {
          const rect = element.getBoundingClientRect();
          const visibleWidth = Math.min(rect.right, window.innerWidth) - Math.max(rect.left, 0);
          const visibleHeight = Math.min(rect.bottom, window.innerHeight) - Math.max(rect.top, 0);
          return { element, rect, visibleArea: visibleWidth * visibleHeight,
            visibleWidth, visibleHeight };
        })
        .filter(({ visibleWidth, visibleHeight }) =>
          visibleWidth >= 32 && visibleHeight >= 32,
        );
      const match = visible.find(({ element }) =>
        sourceUrl && (element.currentSrc === sourceUrl || element.src === sourceUrl),
      );
      const selected = match || (mediaSelector === "video" && visible.sort(
        (a, b) => b.visibleArea - a.visibleArea,
      )[0]);
      if (!selected) return null;
      const { left, top, width, height } = selected.rect;
      return {
        left, top, width, height,
        viewportWidth: window.innerWidth,
        viewportHeight: window.innerHeight,
        sourceUrl: selected.element.currentSrc || selected.element.src || "",
      };
    },
  });
  const rect = injection?.result;
  if (!rect) throw new Error(`No matching visible ${selector} was found.`);
  const activeTab = await chrome.tabs.get(tab.id);
  if (!activeTab.active || activeTab.windowId !== tab.windowId) {
    throw new Error("The video tab is no longer active.");
  }
  const screenshot = await chrome.tabs.captureVisibleTab(tab.windowId, {
    format: "jpeg",
    quality: 72,
  });
  if (screenshot.length > 7_000_000) {
    throw new Error("The captured frame is too large to pass to the panel.");
  }
  return { screenshot, rect, sourceUrl: rect.sourceUrl };
}

async function queueVideoRequest(info, tab) {
  // sidePanel.open must start in the context-menu user gesture. Capturing the
  // page first can outlive that gesture and make Chrome reject the open call.
  openPanelFromGesture(tab);
  let frame = null;
  let captureError = null;
  try {
    frame = await captureMediaFrame(info, tab, "video");
  } catch (error) {
    captureError = error.message || String(error);
  }

  const src = info.srcUrl || frame?.sourceUrl || "";
  const direct = /^https?:\/\//i.test(src);

  if (direct) {
    // The frame is retained only as a fallback if the direct URL requires
    // browser cookies or a Referer that the local backend does not have.
    try {
      await queueRequest({ type: "video", value: src, frame }, tab, false);
    } catch (error) {
      // A screenshot can exceed session storage quota. The URL path still works.
      await queueRequest({ type: "video", value: src }, tab, false);
    }
  } else if (frame) {
    // blob: and Media Source players have no downloadable standalone URL.
    try {
      await queueRequest({ type: "video-frame", ...frame }, tab, false);
    } catch (error) {
      await queueRequest({
        type: "video-error",
        message: "The captured frame was too large to pass to the panel. Try the original file upload.",
      }, tab, false);
    }
  } else {
    await queueRequest({
      type: "video-error",
      message: `${captureError || "No video source was found."} Try the file upload for this player.`,
    }, tab, false);
  }
}

async function queueImageRequest(info, tab) {
  openPanelFromGesture(tab);
  let frame = null;
  let captureError = null;
  try {
    frame = await captureMediaFrame(info, tab, "img");
  } catch (error) {
    captureError = error.message || String(error);
  }
  const src = info.srcUrl || frame?.sourceUrl || "";
  if (/^https?:\/\//i.test(src)) {
    try {
      await queueRequest({ type: "image", value: src, frame }, tab, false);
    } catch {
      await queueRequest({ type: "image", value: src }, tab, false);
    }
  } else if (frame) {
    try {
      await queueRequest({ type: "image-frame", ...frame }, tab, false);
    } catch {
      await queueRequest({ type: "image-error", message: "The captured image is too large to pass to the panel." }, tab, false);
    }
  } else {
    await queueRequest({ type: "image-error", message: captureError || "No visible image was found." }, tab, false);
  }
}

async function queuePageText(tab) {
  openPanelFromGesture(tab);
  try {
    if (tab?.id == null) throw new Error("No page tab is available.");
    const [injection] = await chrome.scripting.executeScript({
      target: { tabId: tab.id, frameIds: [0] },
      func: () => {
        const article = [...document.querySelectorAll("article")]
          .sort((a, b) => (b.innerText || "").length - (a.innerText || "").length)[0];
        const content = article || document.querySelector("main") || document.body;
        return (content?.innerText || "").trim().slice(0, 100000);
      },
    });
    const value = injection?.result || "";
    if (value.length < 20) throw new Error("No readable page text was found. Select a paragraph instead.");
    await queueRequest({ type: "text", value }, tab, false);
  } catch (error) {
    await queueRequest({ type: "text-error", message: error.message || String(error) }, tab, false);
  }
}

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === MENU_SELECTION) {
    openPanelFromGesture(tab);
    queueRequest(
      {
        type: "factcheck",
        value: (info.selectionText || "").slice(0, 10000),
      },
      tab, false,
    );
  } else if (info.menuItemId === MENU_TEXT) {
    openPanelFromGesture(tab);
    queueRequest({ type: "text", value: (info.selectionText || "").slice(0, 100000) }, tab, false);
  } else if (info.menuItemId === MENU_PAGE_TEXT) {
    queuePageText(tab);
  } else if (info.menuItemId === MENU_LINK) {
    openPanelFromGesture(tab);
    queueRequest({ type: "url", value: info.linkUrl || "" }, tab, false);
  } else if (info.menuItemId === MENU_IMAGE) {
    queueImageRequest(info, tab);
  } else if (info.menuItemId === MENU_VIDEO) {
    queueVideoRequest(info, tab);
  } else if (info.menuItemId === MENU_VISIBLE_VIDEO) {
    queueVideoRequest(info, tab);
  }
});
