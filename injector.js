(() => {
  'use strict';

  const dict = window.__AGY_ZH_DICT__;
  if (!dict || typeof dict !== 'object') return { ok: false, reason: 'missing_dict' };

  const uiPhrases = dict.uiPhrases || {};
  const chromeOnly = dict.chromeOnly || {};
  const attrMap = dict.attrs || {};
  const patterns = Array.isArray(dict.patterns)
    ? dict.patterns
        .map((p) => {
          try {
            return { re: new RegExp(p.regex), replace: p.replace };
          } catch {
            return null;
          }
        })
        .filter(Boolean)
    : [];

  if (window.__AGY_ZH_STATE__) {
    window.__AGY_ZH_STATE__.updateDict(uiPhrases, chromeOnly, attrMap, patterns);
    return window.__AGY_ZH_STATE__.stats();
  }

  const SKIP_ANCESTOR_SELECTOR = [
    'pre',
    'code',
    'kbd',
    'samp',
    'script',
    'style',
    'noscript',
    'textarea',
    'input',
    'select',
    'svg',
    '[contenteditable="true"]',
    '.monaco-editor',
    '.cm-editor',
    '.xterm',
    '[data-agy-no-zh]'
  ].join(',');

  const CHROME_ANCESTOR_SELECTOR = [
    'button',
    'a',
    'nav',
    'aside',
    'header',
    'footer',
    'label',
    'th',
    'summary',
    'legend',
    'h1',
    'h2',
    'h3',
    'h4',
    '[role="button"]',
    '[role="menuitem"]',
    '[role="menuitemradio"]',
    '[role="menuitemcheckbox"]',
    '[role="option"]',
    '[role="tab"]',
    '[role="switch"]',
    '[role="tooltip"]',
    '[role="tablist"]',
    '[role="menu"]',
    '[role="dialog"]',
    '[role="alertdialog"]',
    '[data-agy-header]',
    '[data-agy-layout]',
    '[data-agy-agents-pane]',
    '[data-agent-composer-root]',
    '[data-agy-filter-item]',
    '.select-none',
    '.cursor-pointer'
  ].join(',');

  const TARGET_ATTRS = ['placeholder', 'aria-label', 'title'];

  const state = {
    enabled: true,
    uiPhrases,
    chromeOnly,
    attrMap,
    patterns,
    translatedCount: 0,
    mutating: false,
    pendingRoots: new Set(),
    rafScheduled: false
  };

  function shouldSkipElement(el) {
    if (!el || el.nodeType !== 1) return true;
    return Boolean(el.closest(SKIP_ANCESTOR_SELECTOR));
  }

  function isSelectableChatText(el) {
    if (!el || el.nodeType !== 1) return false;
    if (el.closest(CHROME_ANCESTOR_SELECTOR)) return false;
    if (el.closest('.select-text, [class*="select-text"]')) return true;
    return false;
  }

  function isChromeContext(el) {
    if (!el || el.nodeType !== 1) return false;
    if (el.closest(CHROME_ANCESTOR_SELECTOR)) return true;
    if (el.closest('.select-text, [class*="select-text"]')) return false;
    try {
      const us = window.getComputedStyle(el).userSelect;
      if (us === 'none') return true;
    } catch {
      // ignore
    }
    return false;
  }

  function lookupTranslation(trimmed, el) {
    if (!trimmed || trimmed.length > 240) return null;
    if (!isSelectableChatText(el) && Object.prototype.hasOwnProperty.call(state.uiPhrases, trimmed)) {
      return state.uiPhrases[trimmed];
    }
    if (Object.prototype.hasOwnProperty.call(state.chromeOnly, trimmed) && isChromeContext(el)) {
      return state.chromeOnly[trimmed];
    }
    if (!isSelectableChatText(el)) {
      for (let i = 0; i < state.patterns.length; i++) {
        const p = state.patterns[i];
        if (p.re.test(trimmed)) {
          return trimmed.replace(p.re, p.replace);
        }
      }
    }
    return null;
  }

  function translateTextNode(node) {
    const parent = node.parentElement;
    if (!parent || shouldSkipElement(parent)) return;
    const raw = node.nodeValue;
    if (!raw) return;
    const trimmed = raw.trim();
    if (!trimmed) return;
    const next = lookupTranslation(trimmed, parent);
    if (!next || next === trimmed) return;
    const idx = raw.indexOf(trimmed);
    const lead = raw.slice(0, idx);
    const tail = raw.slice(idx + trimmed.length);
    const fullNext = lead + next + tail;
    if (node.nodeValue !== fullNext) {
      node.nodeValue = fullNext;
      state.translatedCount++;
    }
  }

  function translateAttributes(el) {
    if (!el || el.nodeType !== 1 || shouldSkipElement(el)) return;
    for (let i = 0; i < TARGET_ATTRS.length; i++) {
      const attr = TARGET_ATTRS[i];
      const val = el.getAttribute(attr);
      if (!val) continue;
      const trimmed = val.trim();
      if (!trimmed) continue;
      let next = null;
      if (Object.prototype.hasOwnProperty.call(state.attrMap, trimmed)) {
        next = state.attrMap[trimmed];
      } else if (Object.prototype.hasOwnProperty.call(state.uiPhrases, trimmed)) {
        next = state.uiPhrases[trimmed];
      } else if (Object.prototype.hasOwnProperty.call(state.chromeOnly, trimmed)) {
        next = state.chromeOnly[trimmed];
      }
      if (next && next !== val) {
        el.setAttribute(attr, next);
        state.translatedCount++;
      }
    }
  }

  function walkAndTranslate(root) {
    if (!state.enabled || !root) return;
    state.mutating = true;
    try {
      if (root.nodeType === 3) {
        translateTextNode(root);
        return;
      }
      if (root.nodeType !== 1 && root.nodeType !== 9) return;
      const startEl = root.nodeType === 9 ? root.body : root;
      if (!startEl) return;

      if (startEl.nodeType === 1) {
        translateAttributes(startEl);
      }

      const walker = document.createTreeWalker(
        startEl,
        NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT,
        {
          acceptNode(node) {
            if (node.nodeType === 1) {
              return shouldSkipElement(node)
                ? NodeFilter.FILTER_REJECT
                : NodeFilter.FILTER_ACCEPT;
            }
            return NodeFilter.FILTER_ACCEPT;
          }
        }
      );

      let current;
      while ((current = walker.nextNode())) {
        if (current.nodeType === 3) {
          translateTextNode(current);
        } else if (current.nodeType === 1) {
          translateAttributes(current);
        }
      }
    } finally {
      state.mutating = false;
    }
  }

  function flushPending() {
    state.rafScheduled = false;
    if (!state.enabled) {
      state.pendingRoots.clear();
      return;
    }
    const roots = Array.from(state.pendingRoots);
    state.pendingRoots.clear();
    if (roots.length > 50) {
      walkAndTranslate(document.body);
      return;
    }
    for (let i = 0; i < roots.length; i++) {
      walkAndTranslate(roots[i]);
    }
  }

  function scheduleRoot(node) {
    if (!node) return;
    state.pendingRoots.add(node);
    if (!state.rafScheduled) {
      state.rafScheduled = true;
      requestAnimationFrame(flushPending);
    }
  }

  const observer = new MutationObserver((mutations) => {
    if (!state.enabled || state.mutating) return;
    for (let i = 0; i < mutations.length; i++) {
      const m = mutations[i];
      if (m.type === 'characterData') {
        scheduleRoot(m.target);
      } else if (m.type === 'attributes') {
        scheduleRoot(m.target);
      } else if (m.type === 'childList') {
        for (let j = 0; j < m.addedNodes.length; j++) {
          scheduleRoot(m.addedNodes[j]);
        }
      }
    }
  });

  function start() {
    if (!document.body) {
      document.addEventListener('DOMContentLoaded', start, { once: true });
      return;
    }
    walkAndTranslate(document.body);
    setTimeout(() => walkAndTranslate(document.body), 400);
    setTimeout(() => walkAndTranslate(document.body), 1500);
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true,
      attributes: true,
      attributeFilter: TARGET_ATTRS
    });
  }

  window.__AGY_ZH_STATE__ = {
    updateDict(nextUi, nextChrome, nextAttrs, nextPatterns) {
      state.uiPhrases = nextUi;
      state.chromeOnly = nextChrome;
      state.attrMap = nextAttrs;
      state.patterns = nextPatterns;
      walkAndTranslate(document.body);
    },
    stats() {
      return {
        ok: true,
        enabled: state.enabled,
        translatedCount: state.translatedCount,
        uiPhraseCount: Object.keys(state.uiPhrases).length,
        chromeOnlyCount: Object.keys(state.chromeOnly).length,
        url: location.href
      };
    },
    setEnabled(flag) {
      state.enabled = Boolean(flag);
      if (state.enabled) walkAndTranslate(document.body);
    }
  };

  start();
  return window.__AGY_ZH_STATE__.stats();
})();
