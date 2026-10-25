/* TAYO MV3 service worker.
 *
 * The extension never loads ML models itself. It forwards user-initiated
 * requests to the local FastAPI backend, which owns the heavy dependencies.
 */

const MENU_SELECTION = "tayo-factcheck-selection";
const MENU_LINK = "tayo-scan-link";
const MENU_IMAGE = "tayo-analyze-image";

function createMenus() {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: MENU_SELECTION,
      title: "Fact-check selected text",
      contexts: ["selection"],
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

async function queueRequest(request, tab) {
  const pending = { ...request, createdAt: Date.now() };
  // Session storage survives the panel being closed and reopened, and keeps
  // selections out of long-term storage.
  await chrome.storage.session.set({ tayoPending: pending });
  if (tab?.id != null) {
    try {
      await chrome.sidePanel.open({ tabId: tab.id });
    } catch (error) {
      // Chrome may reject programmatic opening on some versions. The pending
      // request is still stored, so it appears when the user opens the panel.
      console.warn("Could not open side panel automatically:", error);
    }
  }
}

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === MENU_SELECTION) {
    queueRequest(
      {
        type: "factcheck",
        value: (info.selectionText || "").slice(0, 10000),
      },
      tab,
    );
  } else if (info.menuItemId === MENU_LINK) {
    queueRequest({ type: "url", value: info.linkUrl || "" }, tab);
  } else if (info.menuItemId === MENU_IMAGE) {
    queueRequest({ type: "image", value: info.srcUrl || "" }, tab);
  }
});
