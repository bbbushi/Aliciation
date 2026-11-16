"use strict";
// Presentation only. The canonical turn and request identity remain on the server.
(() => {
  const segmenter = typeof Intl.Segmenter === "function" ? new Intl.Segmenter("zh", {granularity:"sentence"}) : null;
  function sentences(text, final) {
    const pieces = segmenter ? [...segmenter.segment(text)].map((item) => item.segment)
      : text.match(/[\s\S]+?(?:[。！？!?]+[”’」』）》】"')\]]*|\n+|$)/gu) || [];
    const result = [];
    let pending = "";
    for (let i = 0; i < pieces.length; i++) {
      pending += pieces[i];
      const closedCode = (pending.match(/```/g) || []).length % 2 === 0;
      const boundary = i < pieces.length - 1 || /[。！？!?…][”’」』）》】"')\]]*\s*$/u.test(pending) || /\n\s*$/u.test(pending);
      if ((closedCode && boundary) || (final && i === pieces.length - 1)) {
        if (pending.trim()) result.push(pending);
        pending = "";
      }
    }
    return result;
  }
  class DesktopSpeech {
    constructor(change) {this.records = new Map(); this.change = change; this.active = false; this.timer = null; this.due = 0;}
    set(key, text, {final = false, immediate = false} = {}) {
      text = String(text || "");
      let record = this.records.get(key);
      if (record && record.text === text && record.final === final && !immediate) return;
      if (!record) {record = {text:"", final:false, parts:[], count:0}; this.records.set(key, record);}
      if (!text.startsWith(record.text) && !record.text.startsWith(text)) record.count = 0;
      record.text = text; record.final = final; record.parts = sentences(text, final);
      record.count = immediate ? record.parts.length : Math.min(record.count, record.parts.length);
      this.wake();
    }
    visible(key) {const record = this.records.get(key); return record ? record.parts.slice(0, record.count) : [];}
    pending(key) {const record = this.records.get(key); return !!record && (record.count < record.parts.length || (!record.final && record.parts.join("").length < record.text.length));}
    setActive(active) {
      if (this.active === active) return;
      this.active = active;
      if (!active) {clearTimeout(this.timer); this.timer = null;} else this.wake();
    }
    wake() {
      if (!this.active || this.timer !== null) return;
      if (![...this.records.values()].some((record) => record.count < record.parts.length)) return;
      this.timer = setTimeout(() => this.step(), Math.max(0, this.due - Date.now()));
    }
    step() {
      this.timer = null;
      if (!this.active) return;
      const entry = [...this.records.entries()].find(([, item]) => item.count < item.parts.length);
      if (!entry) return;
      const [key, record] = entry;
      const sentence = record.parts[record.count++];
      const length = [...sentence.replace(/\s/g, "")].length;
      this.due = Date.now() + Math.min(1600, Math.max(420, 240 + length * 38));
      this.change({key, index:record.count - 1}); this.wake();
    }
    retain(keys) {for (const key of this.records.keys()) if (!keys.has(key)) this.records.delete(key);}
    showAll() {for (const record of this.records.values()) record.count = record.parts.length; this.change();}
    clear() {clearTimeout(this.timer); this.timer = null; this.records.clear(); this.due = 0;}
  }
  window.AliceDesktopSpeech = DesktopSpeech;
  // Shared by the transcript and passive preview. Only a newly revealed sentence enters.
  class SentenceMotion {
    constructor() {
      this.animations = new Map();
      this.reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
      this.reduce.addEventListener("change", () => this.clear());
      document.addEventListener("visibilitychange", () => {if (document.hidden) this.clear();});
    }
    enter(node) {
      this.animations.get(node)?.cancel();
      if (!node?.isConnected || document.hidden || typeof node.animate !== "function") return;
      const reduced = this.reduce.matches;
      const animation = node.animate(reduced
        ? [{opacity:0}, {opacity:1}]
        : [{opacity:0, transform:"translateY(6px)"}, {opacity:1, transform:"translateY(0)"}],
      {duration:reduced ? 125 : 180, easing:"cubic-bezier(0.23, 1, 0.32, 1)"});
      this.animations.set(node, animation);
      animation.onfinish = animation.oncancel = () => {
        if (this.animations.get(node) === animation) this.animations.delete(node);
      };
    }
    clear() {for (const animation of this.animations.values()) animation.cancel();this.animations.clear();}
  }
  window.AliceDesktopSentenceMotion = SentenceMotion;
})();
