// Transport-only planning. The caller freezes these parts before registering or
// sending them; the original logical reply remains unchanged in Alice.
const HARD = Object.freeze({ max_codepoints: 1000, max_utf8_bytes: 4000 });
const SOFT = Object.freeze({ short_join: 12, joined_short: 80, single_paragraph: 160,
  target: 120, candidate_min: 60, candidate_max: 180, minimum_tail: 24 });
const SUFFIX = '（后文未发送，完整回复请在本机网页查看。）';
const GRAPHEMES = new Intl.Segmenter('zh', { granularity: 'grapheme' });
const size = text => [...text].length;
const fits = text => size(text) <= HARD.max_codepoints && Buffer.byteLength(text) <= HARD.max_utf8_bytes;
const ACK = /^(?:嗯+|嗯哼|好(?:的|呀|啊|吧)?|行(?:啊|呀|吧)?|对(?:呀|啊|的)?|是(?:的|呀|啊)?|可以|没问题|知道了|明白了|收到)[。.!！…，,、\s]*$/u;

function mergeRanges(ranges) {
  const out = [];
  for (const range of ranges.sort((a, b) => a.start - b.start || b.end - a.end)) {
    const last = out.at(-1);
    if (last && range.start < last.end) last.end = Math.max(last.end, range.end);
    else out.push({ ...range });
  }
  return out;
}

function urlToken(value) {
  const closing = new Map([[')', '('], [']', '['], ['}', '{'], ['）', '（']]);
  const counts = new Map();
  for (const ch of value) counts.set(ch, (counts.get(ch) || 0) + 1);
  let end = value.length;
  while (end) {
    const last = value[end - 1], opening = closing.get(last);
    if (/[.,!?;:。！？；：，…]/u.test(last)) end--;
    else if (opening && counts.get(last) > (counts.get(opening) || 0)) { counts.set(last, counts.get(last) - 1); end--; }
    else break;
  }
  return value.slice(0, end);
}

function protectedRanges(text) {
  const ranges = [];
  const add = (start, end, type = 'protected') => ranges.push({ start, end, type });
  // Fences are whole blocks, including an unclosed block through end of reply.
  const fences = /^[ \t]{0,3}(`{3,}|~{3,})[^\n]*(?:\n|$)/gm;
  let match;
  while ((match = fences.exec(text))) {
    const delimiter = match[1];
    const closing = new RegExp(`^[ \\t]{0,3}${delimiter[0]}{${delimiter.length},}[ \\t]*(?:\\n|$)`, 'gm');
    closing.lastIndex = fences.lastIndex;
    const end = closing.exec(text)?.[0];
    const until = end === undefined ? text.length : closing.lastIndex;
    add(match.index, until, 'block'); fences.lastIndex = until;
  }
  // Chinese prose punctuation terminates a naked URL; percent-encoded URL
  // characters remain untouched. Parentheses inside a URL stay balanced.
  for (const pattern of [/(?:https?:\/\/|www\.)[^\s<>"“”‘’。！？；，]+/gu,
    /[\w.!#$%&'*+/=?^`{|}~-]+@[\w.-]+\.[A-Za-z]{2,}/g,
    /\b(?:v)?\d+(?:\.\d+)+(?:[-+][\w.-]+)?\b/g,
    /\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|vs|etc|e\.g|i\.e)\./g,
    /\.{2,}|…{2,}/gu]) {
    for (const m of text.matchAll(pattern)) {
      const token = /^(?:https?:\/\/|www\.)/u.test(m[0]) ? urlToken(m[0]) : m[0];
      add(m.index, m.index + token.length);
    }
  }
  let known = mergeRanges(ranges), cursor = 0;
  const ticks = /`+/g;
  while ((match = ticks.exec(text))) {
    while (known[cursor] && known[cursor].end <= match.index) cursor++;
    if (known[cursor]?.start <= match.index) { ticks.lastIndex = known[cursor].end; continue; }
    let close = -1, from = ticks.lastIndex;
    while (from < text.length) {
      const candidate = text.indexOf('`', from);
      if (candidate < 0) break;
      let end = candidate + 1;
      while (text[end] === '`') end++;
      if (end - candidate === match[0].length) { close = candidate; break; }
      from = end;
    }
    const until = close < 0 ? text.length : close + match[0].length;
    add(match.index, until); ticks.lastIndex = until;
  }
  known = mergeRanges(ranges); cursor = 0;
  const pairs = new Map([['“', '”'], ['‘', '’'], ['「', '」'], ['『', '』'], ['《', '》'],
    ['（', '）'], ['(', ')'], ['[', ']'], ['{', '}'], ['"', '"'], ["'", "'"]]);
  const stack = [];
  for (let i = 0; i < text.length; i++) {
    while (known[cursor] && known[cursor].end <= i) cursor++;
    if (known[cursor]?.start <= i) { i = known[cursor].end - 1; continue; }
    const ch = text[i];
    if (ch === "'" && /[\p{L}\p{N}]/u.test(text[i - 1] || '') && /[\p{L}\p{N}]/u.test(text[i + 1] || '')) continue;
    if (stack.at(-1)?.close === ch) {
      const opening = stack.pop();
      if (!stack.length) add(opening.start, i + 1, 'quote');
    } else if (pairs.has(ch)) {
      // Apostrophes within English words are not opening quotations.
      if (ch === "'" && /[\p{L}\p{N}]/u.test(text[i - 1] || '')) continue;
      stack.push({ start: i, close: pairs.get(ch) });
    }
  }
  if (stack.length) add(stack[0].start, text.length, 'quote');

  const lines = [...text.matchAll(/[^\n]*(?:\n|$)/g)].filter(m => m[0]);
  const list = /^[ \t]*(?:[-*+]\s+|\d+[.)、]\s*|[（(]\d+[）)]\s*|[一二三四五六七八九十]+、)/u;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^[ \t]*>/u.test(line[0])) {
      let end = i + 1;
      while (lines[end] && /^[ \t]*>/u.test(lines[end][0])) end++;
      add(line.index, lines[end - 1].index + lines[end - 1][0].length, 'block'); i = end - 1;
    } else if (list.test(line[0])) {
      const start = i > 0 && !list.test(lines[i - 1][0]) && /(?:[:：]\s*$|^\s*#{1,6}\s)/u.test(lines[i - 1][0])
        ? lines[i - 1].index : line.index;
      let end = i + 1;
      while (lines[end] && lines[end][0].trim() && !list.test(lines[end][0])) end++;
      add(start, lines[end - 1].index + lines[end - 1][0].length, 'list'); i = end - 1;
    }
  }
  // Keep items separately atomic so a hard-budget repack can reuse every item
  // boundary. Natural planning has no sentence cuts inside these short lists.
  return mergeRanges(ranges);
}

function document(text) {
  const ranges = protectedRanges(text), atoms = [];
  const graphemes = [...GRAPHEMES.segment(text)];
  let cursor = 0;
  for (let i = 0; i < graphemes.length; i++) {
    const { segment, index } = graphemes[i];
    while (ranges[cursor] && ranges[cursor].end <= index) cursor++;
    const range = ranges[cursor];
    let end = index + segment.length;
    const protectedAtom = !!range && range.start < end;
    if (protectedAtom) {
      // A regex boundary may touch a combining mark or variation selector.
      // Expand to whole graphemes, including overlapping protected ranges.
      let until = range.end;
      for (;;) {
        while (end < until && i + 1 < graphemes.length) { i++; end = graphemes[i].index + graphemes[i].segment.length; }
        if (!ranges[cursor + 1] || ranges[cursor + 1].start >= end) break;
        until = Math.max(until, ranges[++cursor].end);
      }
    }
    atoms.push({ start: index, end, value: text.slice(index, end), protected: protectedAtom, type: protectedAtom ? range.type : null });
  }
  const cp = [0], bytes = [0], cuts = new Map([[text.length, atoms.length]]);
  atoms.forEach((atom, index) => {
    cp.push(cp.at(-1) + size(atom.value)); bytes.push(bytes.at(-1) + Buffer.byteLength(atom.value));
    cuts.set(atom.end, index + 1);
  });
  const paragraphs = new Set();
  for (const m of text.matchAll(/\n[ \t]*\n(?:[ \t]*\n)*/g)) {
    const at = cuts.get(m.index + m[0].length);
    if (at !== undefined) paragraphs.add(at);
  }
  const sentence = index => {
    const atom = atoms[index - 1], next = atoms[index]?.value || '';
    if (!atom || (atom.protected && atom.type !== 'quote')) return false;
    if (atom.type === 'quote' && next && !/^\s/u.test(next)) return false;
    // Keep consecutive terminal punctuation and closing marks with the sentence.
    if (/^[。！？!?…”’」』）)\]}]/u.test(next)) return false;
    return /[。！？!?][”’」』）)\]}]*$/u.test(atom.value) || (/\.$/u.test(atom.value) && (!next || /^\s/u.test(next)));
  };
  return { atoms, cp, bytes, paragraphs, sentence,
    slice: (a, b) => text.slice(atoms[a]?.start ?? text.length, atoms[b - 1]?.end ?? text.length).trim(),
    fits: (a, b, reserve = '') => cp[b] - cp[a] + size(reserve) <= HARD.max_codepoints && bytes[b] - bytes[a] + Buffer.byteLength(reserve) <= HARD.max_utf8_bytes };
}

function furthest(doc, start, end, reserve = '') {
  let at = start;
  while (at < end && doc.fits(start, at + 1, reserve)) at++;
  return at;
}

function naturalUnits(doc) {
  const ends = [...doc.paragraphs, doc.atoms.length].sort((a, b) => a - b), units = [];
  let start = 0;
  for (const end of ends) {
    while (start < end) {
      let cut = end, hard = false;
      if (doc.cp[end] - doc.cp[start] > SOFT.single_paragraph || !doc.fits(start, end)) {
        const limit = furthest(doc, start, end), candidates = [];
        for (let i = start + 1; i <= limit; i++) {
          const head = doc.cp[i] - doc.cp[start], tail = doc.cp[end] - doc.cp[i];
          if (doc.sentence(i) && head >= SOFT.minimum_tail && tail >= SOFT.minimum_tail) candidates.push(i);
        }
        const nearby = candidates.filter(i => doc.cp[i] - doc.cp[start] >= SOFT.candidate_min && doc.cp[i] - doc.cp[start] <= SOFT.candidate_max);
        if (nearby.length) cut = nearby.sort((a, b) => Math.abs(doc.cp[a] - doc.cp[start] - SOFT.target) - Math.abs(doc.cp[b] - doc.cp[start] - SOFT.target) || a - b)[0];
        else if (candidates.length) cut = candidates[0];
        else if (limit < end) { cut = limit > start ? limit : start + 1; hard = !doc.atoms[cut - 1].protected; }
      }
      units.push({ start, end: cut, rank: cut === end ? 3 : 1, hard }); start = cut;
    }
  }
  for (let i = 0; i < units.length; i++) {
    const unit = units[i], value = doc.slice(unit.start, unit.end);
    if (size(value) > SOFT.short_join || !ACK.test(value)) continue;
    const next = units[i + 1] ? i + 1 : i - 1;
    if (next < 0) continue;
    const a = Math.min(i, next), b = Math.max(i, next);
    if (size(doc.slice(units[a].start, units[b].end)) <= SOFT.joined_short) {
      units.splice(a, 2, { start: units[a].start, end: units[b].end, rank: units[b].rank, hard: units[a].hard || units[b].hard }); i = a - 1;
    }
  }
  return units;
}

function compact(doc, units, max) {
  while (units.length > max) {
    let best = -1;
    for (let i = 0; i < units.length - 1; i++) {
      if (!doc.fits(units[i].start, units[i + 1].end)) continue;
      if (best < 0 || units[i].rank < units[best].rank ||
        (units[i].rank === units[best].rank && doc.cp[units[i + 1].end] - doc.cp[units[i].start] < doc.cp[units[best + 1].end] - doc.cp[units[best].start])) best = i;
    }
    if (best < 0) break;
    const a = units[best], b = units[best + 1];
    units.splice(best, 2, { start: a.start, end: b.end, rank: b.rank, hard: a.hard || b.hard });
  }
  return units;
}

function capacityParts(doc, max, truncated = false) {
  const parts = []; let start = 0, hard = false, blocked = null;
  while (start < doc.atoms.length && parts.length < max) {
    const reserve = truncated && parts.length === max - 1 ? `\n${SUFFIX}` : '';
    let end = furthest(doc, start, doc.atoms.length, reserve);
    if (end === start) { blocked = doc.atoms[start].protected ? 'protected_block' : 'oversize_grapheme'; break; }
    if (reserve && end < doc.atoms.length) {
      // The final excerpt ends at the nearest safe expression boundary when
      // available. An unpunctuated run may still require a grapheme cut.
      for (let at = end; at > start; at--) {
        if (doc.sentence(at) || doc.paragraphs.has(at) || doc.atoms[at - 1].protected) { end = at; break; }
      }
    }
    if (end < doc.atoms.length && !doc.atoms[end - 1].protected && !doc.sentence(end) && !doc.paragraphs.has(end) && !/^\s*$/u.test(doc.atoms[end - 1].value)) hard = true;
    const value = doc.slice(start, end);
    if (value) parts.push(value);
    start = end;
    if (!doc.fits(start, Math.min(start + 1, doc.atoms.length))) { blocked = doc.atoms[start].protected ? 'protected_block' : 'oversize_grapheme'; break; }
  }
  return { parts, hard, blocked, complete: start === doc.atoms.length };
}

/** Pure, deterministic QQ transport plan. Does not hash, persist, or send. */
export function buildReplyPlan(text, delivery = {}, kind = 'chat') {
  if (typeof text !== 'string' || !text.isWellFormed() || !['chat', 'notice'].includes(kind)) throw new TypeError('invalid_reply_plan_input');
  const mode = kind === 'notice' ? 'length' : delivery.reply_split_mode ?? 'natural';
  const max = kind === 'notice' ? 1 : delivery.max_reply_segments ?? 3;
  const spacing = delivery.send_spacing_ms ?? 1000;
  if (!['natural', 'length'].includes(mode) || !Number.isInteger(max) || max < 1 || max > 3 || !Number.isInteger(spacing) || spacing < 1000 || spacing > 60000) throw new RangeError('invalid_reply_plan_policy');
  const policy_snapshot = { mode, ...SOFT, ...HARD, max_reply_segments: max, send_spacing_ms: spacing };
  const input = text.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, '').replace(/\r\n?/g, '\n').trim() || '（沉默）';
  const doc = document(input), packed = capacityParts(doc, max);
  let parts = packed.parts, hard = packed.hard, truncated = !packed.complete;
  let reason = truncated ? (packed.blocked || 'capacity') : null;
  if (!truncated && mode === 'natural') {
    const units = compact(doc, naturalUnits(doc), max);
    if (units.length <= max && units.every(u => doc.fits(u.start, u.end))) {
      parts = units.map(u => doc.slice(u.start, u.end)).filter(Boolean); hard = units.some(u => u.hard);
    }
  }
  if (truncated) {
    const prefix = capacityParts(doc, max, true);
    parts = prefix.parts; hard = prefix.hard;
    // An oversized protected atom can stop before the reserved last slot. Add
    // the notice separately when possible; otherwise trim only at atom edges.
    if (!parts.length) parts = [SUFFIX];
    else if (fits(`${parts.at(-1)}\n${SUFFIX}`)) parts[parts.length - 1] += `\n${SUFFIX}`;
    else if (parts.length < max) parts.push(SUFFIX);
    else throw new Error('reply_plan_suffix_budget');
  }
  return { parts, planner_version: `${mode}-v1`, policy_snapshot, truncated, reason, hard_split: hard };
}
