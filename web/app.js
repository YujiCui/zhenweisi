'use strict';
(() => {
  const TOKEN = document.querySelector('meta[name="zws-token"]').content;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const WD = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
  const WDCN = { mon: '一', tue: '二', wed: '三', thu: '四', fri: '五', sat: '六', sun: '日' };
  const STEPS = ['基本', '作息', '模块', '需求', '生成'];
  const PASSIVE = new Set(['free', 'flex', 'rest', 'commute', 'social']);
  const LS_FORM = 'zws.form.v1';
  const LS_ENGINE = 'zws.engine.v1';
  const ENGINES = [
    { key: 'claude', name: '自动', sub: '本机 Claude Code', desc: '一键生成。用你电脑上已登录的 Claude Code，不需要 API key。' },
    { key: 'manual', name: '手动', sub: '任意 AI 聊天', desc: '复制提示词贴给任意 AI（Claude、ChatGPT、DeepSeek、豆包、Kimi 都行），再把回答贴回来。' },
    { key: 'offline', name: '不用 AI', sub: '固定规则', desc: '只按表单字段排一份基础方案，立即出结果。「用自己的话说」那段不会用到。' },
  ];
  const MODEL_LABEL = { opus: 'Opus（默认）', sonnet: 'Sonnet（更快）', haiku: 'Haiku（最快）', fable: 'Fable（最强，较慢）' };

  const S = {
    boot: null, env: null, form: null, step: 0, base: null, result: null, stale: false,
    engine: 'claude', model: 'opus', allowCost: false, job: null, created: null, pending: null,
    pvDay: null, pvTab: 'schedule', manualPrompt: '', typeColor: {}, typeName: {},
  };

  // ---------- 小工具 ----------
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else if (k === 'style') Object.assign(el.style, v);
      else if (k === 'value' || k === 'checked' || k === 'disabled' || k === 'type' || k === 'readOnly' || k === 'selected') el[k] = v;
      else el.setAttribute(k, v === true ? '' : v);
    }
    for (const kid of kids.flat(Infinity)) {
      if (kid == null || kid === false) continue;
      el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }
  const clone = (o) => JSON.parse(JSON.stringify(o));
  const get = (o, k) => k.split('.').reduce((a, p) => (a == null ? a : a[p]), o);
  function set(o, k, v) {
    const ps = k.split('.');
    const last = ps.pop();
    let a = o;
    for (const p of ps) {
      if (typeof a[p] !== 'object' || a[p] === null) a[p] = {};
      a = a[p];
    }
    a[last] = v;
  }
  const wdText = (wds) => '周' + wds.map((d) => WDCN[d]).join('');

  async function api(path, body) {
    const opt = { method: body === undefined ? 'GET' : 'POST', headers: { 'X-ZWS-Token': TOKEN } };
    if (body !== undefined) {
      opt.headers['Content-Type'] = 'application/json';
      opt.body = JSON.stringify(body);
    }
    let res;
    try {
      res = await fetch(path, opt);
    } catch (e) {
      throw new Error('连不上本机服务。生成器的窗口是不是关了？');
    }
    let data = null;
    try { data = await res.json(); } catch (e) { /* 不是 JSON */ }
    if (!res.ok) throw new Error((data && data.error) || `请求失败（${res.status}）`);
    return data;
  }

  let toastTimer = null;
  function toast(msg, isErr) {
    const t = $('#toast');
    t.textContent = msg;
    t.className = 'toast' + (isErr ? ' err' : '');
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, isErr ? 6000 : 3200);
  }
  function setStatus(id, msg, kind, spin) {
    const el = $(id);
    el.className = 'status' + (kind ? ' ' + kind : '');
    el.replaceChildren(...(spin ? [h('span', { class: 'spin' })] : []), msg || '');
  }

  // ---------- 表单的存取 ----------
  function loadForm() {
    try {
      const raw = JSON.parse(localStorage.getItem(LS_FORM) || 'null');
      return raw && typeof raw === 'object' ? raw : null;
    } catch (e) { return null; }
  }
  function saveForm() {
    try { localStorage.setItem(LS_FORM, JSON.stringify(S.form)); } catch (e) { /* 存不了就算了 */ }
  }
  function withDefaults(saved) {
    const f = clone(S.boot.blank);
    if (!saved) return f;
    for (const k of Object.keys(f)) {
      if (!(k in saved)) continue;
      if (f[k] && typeof f[k] === 'object' && !Array.isArray(f[k])) Object.assign(f[k], saved[k] || {});
      else f[k] = saved[k];
    }
    return f;
  }
  function changed() {
    saveForm();
    if (S.result && !S.stale) {
      S.stale = true;
      $('#stale').hidden = false;
    }
    $('#about-count').textContent = String((S.form.about || '').length);
    schedulePreview();
  }

  // ---------- 步骤 ----------
  function renderSteps() {
    $('#steps').replaceChildren(...STEPS.map((name, i) => h('li', { class: (i === S.step ? 'on' : '') + (i < S.step ? ' done' : '') },
      h('button', { type: 'button', onclick: () => showStep(i), 'aria-current': i === S.step ? 'step' : null },
        h('span', { class: 'num', text: String(i + 1) }), h('span', { class: 'lbl', text: name })))));
  }
  function showStep(i) {
    S.step = Math.max(0, Math.min(STEPS.length - 1, i));
    for (const p of $$('.panel')) p.hidden = Number(p.dataset.step) !== S.step;
    $('#btn-prev').disabled = S.step === 0;
    $('#btn-next').hidden = S.step === STEPS.length - 1;
    renderSteps();
    if (S.step === 4) renderEngines();
  }

  // ---------- 基本输入、单选多选 ----------
  function syncInputs() {
    for (const el of $$('[data-k]')) {
      const v = get(S.form, el.dataset.k);
      el.value = v == null ? '' : v;
    }
    $('#about-count').textContent = String((S.form.about || '').length);
  }
  function bindInputs() {
    for (const el of $$('[data-k]')) {
      el.addEventListener('input', () => { set(S.form, el.dataset.k, el.value); changed(); });
    }
  }
  function renderChips() {
    const opts = { role: S.boot.roles, tone: S.boot.spec.tones, rest_days: WD.map((d) => [d, '周' + WDCN[d]]) };
    for (const box of $$('[data-chips]')) {
      const key = box.dataset.chips;
      const multi = box.dataset.multi === '1';
      box.replaceChildren(...opts[key].map(([val, label]) => {
        const on = multi ? S.form[key].includes(val) : S.form[key] === val;
        return h('button', {
          type: 'button', class: 'chip' + (on ? ' on' : ''), 'aria-pressed': String(on), text: label,
          onclick: () => {
            if (multi) S.form[key] = WD.filter((d) => (d === val ? !on : S.form[key].includes(d)));
            else S.form[key] = val;
            renderChips();
            changed();
          },
        });
      }));
    }
    $('#swatches').replaceChildren(...S.boot.spec.accents.map((c) => h('button', {
      type: 'button', class: 'sw' + (S.form.accent === c ? ' on' : ''), style: { backgroundColor: c },
      'aria-label': '主色 ' + c, 'aria-pressed': String(S.form.accent === c),
      onclick: () => { S.form.accent = c; renderChips(); changed(); },
    })));
  }

  // ---------- 列表行 ----------
  const num = (v, d) => { const n = parseInt(v, 10); return Number.isFinite(n) ? n : d; };
  const inp = (type, value, oninput, extra) => h('input', Object.assign({ type, value: value == null ? '' : value, oninput: (e) => { oninput(e.target.value); changed(); } }, extra || {}));
  const del = (key, i) => h('button', { type: 'button', class: 'del', 'aria-label': '删除这一行', text: '×', onclick: () => { S.form[key].splice(i, 1); renderRows(key); changed(); } });

  const LISTS = {
    fixed: {
      blank: () => ({ name: '', type: 'work', days: [], start: '09:00', end: '18:00' }),
      row: (it, i) => h('div', { class: 'row fixed' },
        inp('text', it.name, (v) => { it.name = v; }, { placeholder: '名称，例：上课', maxlength: 16 }),
        h('select', { 'aria-label': '类型', onchange: (e) => { it.type = e.target.value; changed(); } },
          S.boot.spec.block_types.map(([k, name]) => h('option', { value: k, selected: it.type === k, text: name }))),
        h('span', { class: 'wds' }, WD.map((d) => h('button', {
          type: 'button', class: 'chip mini' + (it.days.includes(d) ? ' on' : ''), text: WDCN[d], 'aria-label': '周' + WDCN[d],
          'aria-pressed': String(it.days.includes(d)),
          onclick: (e) => {
            const on = !it.days.includes(d);
            it.days = WD.filter((x) => (x === d ? on : it.days.includes(x)));
            e.target.classList.toggle('on', on);
            e.target.setAttribute('aria-pressed', String(on));
            changed();
          },
        }))),
        inp('time', it.start, (v) => { it.start = v; }, { 'aria-label': '开始时间' }),
        h('span', { class: 'dash', text: '到' }),
        inp('time', it.end, (v) => { it.end = v; }, { 'aria-label': '结束时间' }),
        del('fixed', i)),
    },
    goals: {
      blank: () => ({ name: '', date: '' }),
      row: (it, i) => h('div', { class: 'row' },
        inp('text', it.name, (v) => { it.name = v; }, { placeholder: '例：六级考到 550 分', maxlength: 40 }),
        inp('date', it.date, (v) => { it.date = v; }, { 'aria-label': '目标日期' }),
        del('goals', i)),
    },
    floors: {
      blank: () => '',
      row: (it, i) => h('div', { class: 'row' },
        inp('text', it, (v) => { S.form.floors[i] = v; }, { placeholder: '例：不熬过 00:30', maxlength: 40 }),
        del('floors', i)),
    },
    habits: {
      blank: () => '',
      row: (it, i) => h('div', { class: 'row' },
        inp('text', it, (v) => { S.form.habits[i] = v; }, { placeholder: '例：阅读 20 分', maxlength: 20 }),
        del('habits', i)),
    },
    countdowns: {
      blank: () => ({ name: '', date: '' }),
      row: (it, i) => h('div', { class: 'row' },
        inp('text', it.name, (v) => { it.name = v; }, { placeholder: '例：期末考试周', maxlength: 24 }),
        inp('date', it.date, (v) => { it.date = v; }, { 'aria-label': '日期' }),
        del('countdowns', i)),
    },
    maintenance: {
      blank: () => ({ name: '', every: 30 }),
      row: (it, i) => h('div', { class: 'row' },
        inp('text', it.name, (v) => { it.name = v; }, { placeholder: '例：换滤芯', maxlength: 24 }),
        h('span', { class: 'unit', text: '每' }),
        inp('number', it.every, (v) => { it.every = num(v, 0); }, { min: 1, max: 3650, 'aria-label': '间隔天数' }),
        h('span', { class: 'unit', text: '天' }),
        del('maintenance', i)),
    },
    people: {
      blank: () => ({ name: '', every: 14, topic: '' }),
      row: (it, i) => h('div', { class: 'row' },
        inp('text', it.name, (v) => { it.name = v; }, { placeholder: '人名', maxlength: 16 }),
        h('span', { class: 'unit', text: '每' }),
        inp('number', it.every, (v) => { it.every = num(v, 0); }, { min: 1, max: 365, 'aria-label': '间隔天数' }),
        h('span', { class: 'unit', text: '天' }),
        inp('text', it.topic, (v) => { it.topic = v; }, { placeholder: '要谈的事，可不填', maxlength: 40 }),
        del('people', i)),
    },
  };
  function renderRows(key) {
    const box = $('#rows-' + key);
    if (box) box.replaceChildren(...S.form[key].map((it, i) => LISTS[key].row(it, i)));
  }
  function addRow(key) {
    S.form[key].push(LISTS[key].blank());
    renderRows(key);
    const box = $('#rows-' + key);
    const first = box && box.lastElementChild && box.lastElementChild.querySelector('input');
    if (first) first.focus();
    changed();
  }
  function listBox(key, addLabel, hint) {
    return [hint ? h('p', { class: 'hint', text: hint }) : null, h('div', { class: 'rows', id: 'rows-' + key }),
      h('button', { type: 'button', class: 'btn small', text: addLabel, onclick: () => addRow(key) })];
  }

  // ---------- 模块 ----------
  function moduleBody(key) {
    const f = S.form;
    const n = (k, min, max, d) => inp('number', f[k] || '', (v) => { f[k] = num(v, d); }, { min, max });
    switch (key) {
      case 'habits': return listBox('habits', '加一个习惯', '可以不填：用 AI 生成时会按你的需求补，并给每个习惯定一个最晚完成时间。');
      case 'water': return h('div', { class: 'inline' }, '每天目标', n('water_goal_ml', 500, 6000, 2000), 'ml');
      case 'nutrition': return [h('div', { class: 'inline' }, '热量', n('kcal', 0, 6000, 0), 'kcal', '蛋白', n('protein', 0, 400, 0), 'g'),
        h('p', { class: 'hint', text: '不填就只记录、不设目标。用 AI 生成时，它只在你写了身高体重这类依据时才估目标，并把假设列出来。' })];
      case 'training': return h('div', { class: 'inline' }, '每周', n('train_per_week', 0, 7, 3), '次',
        inp('text', f.train_note, (v) => { f.train_note = v; }, { placeholder: '练什么，例：推拉腿 / 跑步 / 瑜伽', maxlength: 60 }));
      case 'focus': return h('div', { class: 'inline' }, '每次', n('focus_min', 5, 180, 25), '分钟');
      case 'countdowns': return listBox('countdowns', '加一个日期');
      case 'maintenance': return listBox('maintenance', '加一项');
      case 'people': return listBox('people', '加一个人');
      case 'qa': {
        const c = S.env && S.env.claude;
        const t = !c ? '正在检测本机的 Claude Code …'
          : (c.found && c.mode === 'subscription' ? `已检测到 Claude Code ${c.version}，${c.text}。浮窗里输入「问 …」时才会调用，一问一答。`
            : '这台电脑上没检测到可用的 Claude Code，开了也用不了；装好并登录后就能用。');
        return h('p', { class: 'hint', text: t });
      }
      default: return null;
    }
  }
  function renderModules() {
    const box = $('#modules');
    box.replaceChildren(...S.boot.spec.modules.map((m) => {
      const on = !!S.form.modules[m.key];
      const body = on ? moduleBody(m.key) : null;
      return h('div', { class: 'mod' + (on ? ' on' : '') + (body ? ' wide' : '') },
        h('div', { class: 'mod-head' },
          h('div', { class: 'mod-text' }, h('div', { class: 'mod-name', text: m.name }), h('div', { class: 'mod-desc', text: m.desc })),
          h('button', {
            type: 'button', class: 'switch', role: 'switch', 'aria-checked': String(on), 'aria-label': m.name,
            onclick: () => { S.form.modules[m.key] = !on; renderModules(); changed(); },
          })),
        body ? h('div', { class: 'mod-body' }, body) : null);
    }));
    for (const key of ['habits', 'countdowns', 'maintenance', 'people']) renderRows(key);
  }

  function renderExamples() {
    let armed = null;
    $('#example-btns').replaceChildren(...S.boot.examples.map((ex) => h('button', {
      type: 'button', class: 'btn small ghost', text: ex.label,
      onclick: (e) => {
        const dirty = JSON.stringify(S.form) !== JSON.stringify(S.boot.blank);
        if (dirty && armed !== ex.key) {
          armed = ex.key;
          e.target.textContent = '会覆盖已填内容，再点一次确认';
          setTimeout(() => { if (armed === ex.key) { armed = null; e.target.textContent = ex.label; } }, 4000);
          return;
        }
        armed = null;
        S.form = withDefaults(clone(ex.form));
        renderForm();
        changed();
        toast(`已载入示例：${ex.label}。这是虚构的人物，照着改成你自己的。`);
      },
    })));
  }

  function renderForm() {
    syncInputs();
    renderChips();
    for (const key of ['fixed', 'goals', 'floors']) renderRows(key);
    renderModules();
    renderExamples();
  }

  // ---------- 预览 ----------
  let pvTimer = null;
  let pvSeq = 0;
  function schedulePreview() {
    clearTimeout(pvTimer);
    pvTimer = setTimeout(refreshPreview, 250);
  }
  async function refreshPreview() {
    const seq = ++pvSeq;
    try {
      const r = await api('/api/preview', { form: S.form });
      if (seq !== pvSeq) return;
      S.base = r;
      renderPreview();
    } catch (e) {
      toast(e.message, true);
    }
  }
  const shown = () => (S.result && !S.stale ? S.result : S.base);
  const daysLeft = (iso) => Math.round((new Date(iso + 'T00:00:00') - new Date(new Date().toDateString())) / 86400000);

  function renderPreview() {
    const src = shown();
    if (!src) return;
    const plan = src.plan;
    const pv = src.preview;
    const m = plan.modules;
    const w = $('#widget');
    w.style.setProperty('--w-accent', plan.assistant.accent);
    $('#w-name').textContent = plan.assistant.name;
    if (!S.pvDay) S.pvDay = pv.today;
    $('#pv-days').replaceChildren(...WD.map((d) => h('button', {
      type: 'button', class: 'chip mini' + (d === S.pvDay ? ' on' : '') + (d === pv.today ? ' today' : ''), text: WDCN[d],
      'aria-label': '预览周' + WDCN[d], 'aria-pressed': String(d === S.pvDay), onclick: () => { S.pvDay = d; renderPreview(); },
    })));
    const day = pv.days[S.pvDay];
    $('#w-date').textContent = `周${WDCN[S.pvDay]} · ${day.label}`;

    const now = new Date();
    let nowm = now.getHours() * 60 + now.getMinutes();
    const [dh, dm] = plan.day_start.split(':').map(Number);
    if (nowm < dh * 60 + dm) nowm += 1440;
    const isToday = S.pvDay === pv.today;
    const blocks = day.blocks;
    const cur = isToday ? blocks.find((b) => b.s <= nowm && nowm < b.e) : null;
    const next = isToday ? blocks.find((b) => b.s > nowm) : blocks[0];
    const nowBox = $('#w-now');
    if (cur) {
      nowBox.replaceChildren(h('b', { text: cur.name }), h('span', { text: `还剩 ${cur.e - nowm} 分` }));
      $('#w-prog').style.width = Math.round(100 * (nowm - cur.s) / (cur.e - cur.s)) + '%';
    } else {
      nowBox.replaceChildren(h('b', { text: next ? next.name : (m.schedule ? '这一天的安排都过了' : '没有开日程模块') }),
        h('span', { text: next ? `${next.t} 开始` : '' }));
      $('#w-prog').style.width = '0';
    }

    const rules = pv.rules.slice().sort((a, b) => b.priority - a.priority);
    const after = cur ? blocks.find((b) => b.s > nowm) : null;
    const idle = plan.assistant.lines.day || (after ? `下一项 ${after.t} ${after.name}` : '现在没有要处理的事');
    $('#w-bar').textContent = rules.length ? rules[0].say : idle;
    $('#w-acts').hidden = !rules.length;

    const tabs = [];
    if (m.schedule) tabs.push(['schedule', '日程']);
    if (m.todo || m.countdowns || m.maintenance || m.people) tabs.push(['todo', '待办']);
    if (m.habits || m.water || m.nutrition || m.training) tabs.push(['log', '记录']);
    if (m.review) tabs.push(['review', '复盘']);
    if (!tabs.some((t) => t[0] === S.pvTab)) S.pvTab = tabs.length ? tabs[0][0] : '';
    $('#w-tabs').replaceChildren(...tabs.map(([k, name]) => h('button', {
      type: 'button', class: 'w-tab' + (k === S.pvTab ? ' on' : ''), text: name, onclick: () => { S.pvTab = k; renderPreview(); },
    })));

    const body = $('#w-body');
    const sec = (t) => h('div', { class: 'w-sec', text: t });
    const row = (left, name, meta, ck, color, isCur) => h('div', { class: 'w-row' + (isCur ? ' cur' : '') },
      left != null ? h('time', { text: left }) : null,
      color ? h('span', { class: 'bar', style: { backgroundColor: color } }) : null,
      ck != null ? h('span', { class: 'ck' + (ck ? '' : ' none') }) : null,
      h('span', { class: 'nm', text: name }), meta ? h('span', { class: 'meta', text: meta }) : null);
    const meter = (label, right, color) => h('div', { class: 'w-meter' },
      h('div', { class: 't' }, h('b', { text: label }), h('span', { text: right })), h('div', { class: 'm' }));
    const out = [];
    if (S.pvTab === 'schedule') {
      if (!blocks.length) out.push(h('div', { class: 'w-empty', text: '这一天没有时间块' }));
      for (const b of blocks) {
        out.push(row(b.t, b.name, b.fixed ? '固定' : (b.dnd ? '勿扰' : ''), !PASSIVE.has(b.type), S.typeColor[b.type], b === cur));
      }
    } else if (S.pvTab === 'todo') {
      if (m.todo) {
        out.push(sec('待办'));
        out.push(h('div', { class: 'w-empty', text: '还没有待办。在底部输入：待办 交报告 周五 17:00' }));
      }
      const dated = (m.countdowns ? plan.countdowns : []).concat(plan.goals.filter((g) => g.date));
      if (dated.length) {
        out.push(sec('倒计时'));
        for (const c of dated) out.push(row(null, c.name, `还有 ${daysLeft(c.date)} 天`));
      }
      if (m.maintenance && plan.maintenance.length) {
        out.push(sec('周期事务'));
        for (const c of plan.maintenance) out.push(row(null, c.name, `每 ${c.every} 天`));
      }
      if (m.people && plan.people.length) {
        out.push(sec('联络'));
        for (const c of plan.people) out.push(row(null, c.name, `每 ${c.every} 天`));
      }
    } else if (S.pvTab === 'log') {
      if (m.habits) {
        out.push(sec('习惯'));
        if (!plan.habits.length) out.push(h('div', { class: 'w-empty', text: '还没有习惯' }));
        for (const x of plan.habits) out.push(row(null, x.name, x.by ? `${x.by} 前` : '', true));
      }
      if (m.water) { out.push(sec('喝水')); out.push(meter('今天', `0 / ${plan.water.goal_ml} ml`)); }
      if (m.nutrition) {
        out.push(sec('饮食'));
        if (plan.nutrition.kcal) out.push(meter('热量', `0 / ${plan.nutrition.kcal} kcal`));
        if (plan.nutrition.protein) out.push(meter('蛋白', `0 / ${plan.nutrition.protein} g`));
        if (!plan.nutrition.kcal && !plan.nutrition.protein) out.push(h('div', { class: 'w-empty', text: '只记录，不设目标' }));
      }
      if (m.training && plan.training.queue.length) {
        const p = plan.training.programs[plan.training.queue[0]];
        out.push(sec('训练 · 下一节：' + p.name));
        for (const e of p.ex) out.push(row(null, e[0], e[1] ? `${e[1]} × ${e[2]}` : e[2]));
      }
    } else if (S.pvTab === 'review') {
      out.push(sec('今晚的复盘'));
      out.push(h('div', { class: 'w-pre', text: plan.review_template.trim() }));
      if (plan.goals.length) {
        out.push(sec('目标'));
        for (const g of plan.goals) out.push(row(null, g.name, g.date ? `还有 ${daysLeft(g.date)} 天` : ''));
      }
    }
    body.replaceChildren(...out);
    $('#pv-note').textContent = S.result && !S.stale
      ? '这是生成结果。点上面的星期看每天怎么排；助手条示意的是一条规则触发时的样子。'
      : '这是按固定规则排的基础方案，边填边更新。用 AI 生成后会换成贴合你的版本。';
  }

  // ---------- 生成 ----------
  async function loadEnv(fresh) {
    try {
      S.env = await api('/api/env' + (fresh ? '?fresh=1' : ''));
    } catch (e) {
      S.env = { claude: { found: false, mode: 'missing', version: '', text: e.message, cost_risk: '' } };
    }
    const c = S.env.claude;
    const usable = c.found && c.mode !== 'logged_out';
    if (!localStorage.getItem(LS_ENGINE)) S.engine = usable ? 'claude' : 'manual';
    if (S.step === 4) renderEngines();
    if (S.form.modules.qa) renderModules();
  }
  function renderEngines() {
    $('#engines').replaceChildren(...ENGINES.map((e) => h('button', {
      type: 'button', class: 'engine' + (S.engine === e.key ? ' on' : ''), 'aria-pressed': String(S.engine === e.key),
      onclick: () => {
        S.engine = e.key;
        try { localStorage.setItem(LS_ENGINE, e.key); } catch (err) { /* 忽略 */ }
        renderEngines();
      },
    }, h('span', { class: 'e-name', text: e.name }), h('span', { class: 'e-sub', text: e.sub }), h('span', { class: 'e-desc', text: e.desc }))));
    $('#btn-gen').textContent = S.engine === 'manual' ? (S.manualPrompt ? '重新生成提示词' : '生成提示词') : '生成助手';
    renderEngineOpts();
  }
  function renderEngineOpts() {
    const box = $('#engine-opts');
    if (S.engine === 'claude') {
      const c = S.env && S.env.claude;
      let dot = '';
      let text = '正在检测本机的 Claude Code …';
      if (c) {
        if (!c.found) { dot = 'bad'; text = '没检测到 Claude Code。装好并登录后点「重新检测」，或者改用手动方式。'; }
        else if (c.mode === 'logged_out') { dot = 'warn'; text = `已安装 ${c.version}，还没登录。在终端运行 claude 登录后点「重新检测」。`; }
        else if (c.cost_risk) { dot = 'warn'; text = `已检测到 ${c.version}。${c.cost_risk}。`; }
        else { dot = 'ok'; text = `已检测到 ${c.version}，${c.text}。`; }
      }
      box.replaceChildren(h('div', { class: 'box' },
        h('div', {}, h('span', { class: 'dot ' + dot }), text, ' ',
          h('button', { type: 'button', class: 'btn small ghost', text: '重新检测', onclick: () => { S.env = null; renderEngineOpts(); loadEnv(true); } })),
        h('div', { class: 'inline', style: { marginTop: '10px' } }, '模型',
          h('select', { style: { width: 'auto' }, 'aria-label': '模型', onchange: (e) => { S.model = e.target.value; } },
            S.boot.models.map((k) => h('option', { value: k, selected: S.model === k, text: MODEL_LABEL[k] || k }))),
          h('span', { class: 'hint', style: { margin: '0' }, text: '生成一次大约一分钟。' })),
        c && c.cost_risk ? h('label', { class: 'check' },
          h('input', { type: 'checkbox', checked: S.allowCost, onchange: (e) => { S.allowCost = e.target.checked; } }),
          '我知道这次可能另外计费，继续') : null));
    } else if (S.engine === 'manual') {
      if (!S.manualPrompt) {
        box.replaceChildren(h('div', { class: 'box hint', text: '点「生成提示词」，把它整段复制给任意 AI，再把它的回答贴回来。全程不需要 API key。' }));
        return;
      }
      const reply = h('textarea', { rows: 6, placeholder: '把 AI 的回答整段贴在这里', id: 'manual-reply', spellcheck: 'false' });
      const prompt = h('textarea', { rows: 6, readOnly: true, value: S.manualPrompt, 'aria-label': '提示词' });
      box.replaceChildren(h('div', { class: 'box' },
        h('div', { class: 'lab', text: '第一步：复制这段提示词，贴给任意 AI' }), prompt,
        h('button', { type: 'button', class: 'btn small', text: '复制提示词', onclick: () => copyText(S.manualPrompt, prompt) }),
        h('div', { class: 'lab', style: { marginTop: '14px' }, text: '第二步：把它的回答贴回来' }), reply,
        h('button', { type: 'button', class: 'btn small primary', text: '校验并导入', onclick: () => importReply(reply.value) })));
    } else {
      box.replaceChildren(h('div', { class: 'box hint', text: '不调用任何 AI。时间块按固定规则排，没有个性化的提醒规则；适合先看看效果，或者打算自己改 plan.json。' }));
    }
  }
  async function copyText(text, fallbackEl) {
    try {
      await navigator.clipboard.writeText(text);
      toast('已复制。');
    } catch (e) {
      fallbackEl.focus();
      fallbackEl.select();
      const ok = document.execCommand && document.execCommand('copy');
      toast(ok ? '已复制。' : '复制失败：已全选，按 Ctrl+C 复制。', !ok);
    }
  }
  function setBusy(on) {
    $('#btn-gen').disabled = on;
    $('#btn-cancel').hidden = !on;
    for (const b of $$('.engine')) b.disabled = on;
  }
  async function onGenerate() {
    try {
      if (S.engine === 'manual') {
        const r = await api('/api/prompt', { form: S.form });
        S.manualPrompt = r.prompt;
        renderEngines();
        setStatus('#gen-status', `提示词已生成，共 ${r.prompt.length} 字。`, 'ok');
        return;
      }
      const r = await api('/api/generate', { form: S.form, engine: S.engine, model: S.model, allow_cost: S.allowCost });
      S.job = r.job;
      setBusy(true);
      setStatus('#gen-status', '正在生成', '', true);
      poll();
    } catch (e) {
      setStatus('#gen-status', e.message, 'err');
    }
  }
  async function poll() {
    if (!S.job) return;
    let j;
    try {
      j = await api('/api/job?id=' + encodeURIComponent(S.job));
    } catch (e) {
      S.job = null;
      setBusy(false);
      setStatus('#gen-status', e.message, 'err');
      return;
    }
    if (j.state === 'running') {
      setStatus('#gen-status', `${j.log.length ? j.log[j.log.length - 1] : '正在生成'} · 已用 ${Math.round(j.elapsed)} 秒`, '', true);
      setTimeout(poll, 1000);
      return;
    }
    S.job = null;
    setBusy(false);
    if (j.state === 'done') {
      applyResult(j.result);
      setStatus('#gen-status', j.result.used_ai ? `生成完成，用时 ${Math.round(j.elapsed)} 秒。` : '已按固定规则生成。', 'ok');
    } else if (j.state === 'cancelled') {
      setStatus('#gen-status', '已取消。');
    } else {
      setStatus('#gen-status', j.error || '生成失败', 'err');
    }
  }
  async function importReply(text) {
    if (!text.trim()) { setStatus('#gen-status', '先把 AI 的回答贴进来。', 'err'); return; }
    try {
      applyResult(await api('/api/import', { form: S.form, reply: text }));
      setStatus('#gen-status', '已导入。', 'ok');
    } catch (e) {
      setStatus('#gen-status', e.message, 'err');
    }
  }
  function applyResult(res) {
    S.result = res;
    S.stale = false;
    S.created = null;
    S.pending = null;
    $('#exists-choice').hidden = true;
    setStatus('#launch-status', '');
    renderResult();
    renderPreview();
  }

  function renderResult() {
    const res = S.result;
    $('#result').hidden = !res;
    if (!res) return;
    $('#stale').hidden = !S.stale;
    const plan = res.plan;
    const m = plan.modules;
    $('#res-notes').replaceChildren(...(res.notes || []).map((t) => h('li', { text: t })));
    $('#res-warns').replaceChildren(...(res.warnings || []).map((t) => h('li', { text: t })));
    const secs = [];
    const sec = (title, wide, ...kids) => secs.push(h('div', { class: 'sec' + (wide ? ' wide' : '') }, h('h4', { text: title }), kids));

    if (m.schedule) {
      sec('日程', true, Object.keys(plan.day_types).map((k) => {
        const wds = WD.filter((d) => plan.week[d] === k);
        const d0 = res.preview.days[wds[0]] || {};
        return h('div', { class: 'tpl' },
          h('div', { class: 'tpl-head' }, h('b', { text: plan.day_types[k] }),
            h('span', { text: `${wdText(wds)} · ${d0.wake || ''} 起床` })),
          h('div', { class: 'blocks' }, plan.templates[k].map((b) => h('span', {
            class: 'blk', style: { borderLeftColor: S.typeColor[b.type] }, title: (S.typeName[b.type] || '') + (b.note ? ' · ' + b.note : ''),
          }, h('time', { text: b.t }), b.name,
          b.days ? h('span', { class: 'pin', text: wdText(b.days) }) : null,
          b.fixed ? h('span', { class: 'pin', text: '固定' }) : null))));
      }));
    }
    sec('它会在这些情况下提醒你', true, res.preview.rules.length
      ? res.preview.rules.map((r) => h('div', { class: 'rule' },
        h('div', { class: 'say', text: r.say }), h('div', { class: 'when', text: r.when }),
        r.why ? h('div', { class: 'why', text: '依据：' + r.why }) : null))
      : h('div', { class: 'muted', text: '没有个性化规则。内置提醒照常工作：时间块结束未勾、待办临期、睡前收尾、过了睡觉线、习惯临期、复盘未写。' }));
    if (m.habits && plan.habits.length) {
      sec('习惯', false, h('div', { class: 'tags' }, plan.habits.map((x) => h('span', { class: 'tagc', text: x.name + (x.by ? ` · ${x.by} 前` : '') }))));
    }
    const a = plan.assistant;
    const lines = [['早上', a.lines.morning], ['白天', a.lines.day], ['晚上', a.lines.night]].filter((x) => x[1]);
    if (lines.length || plan.owner.priorities.length) {
      sec('助手的话', false,
        lines.map(([k, v]) => h('div', { class: 'kv' }, `${k}：`, h('b', { text: v }))),
        plan.owner.priorities.length ? h('div', { class: 'kv', style: { marginTop: '6px' } }, '优先顺序：', h('b', { text: plan.owner.priorities.join(' > ') })) : null);
    }
    if (m.training && plan.training.queue.length) {
      sec('训练', false, plan.training.queue.map((k) => {
        const p = plan.training.programs[k];
        return h('div', { class: 'kv' }, h('b', { text: p.name }), p.ex.length ? '：' + p.ex.map((e) => e[0]).join('、') : '');
      }), h('div', { class: 'muted', style: { marginTop: '6px' }, text: '动作表是通用模板，按自己的情况改。' }));
    }
    if (m.nutrition) {
      sec('饮食', false, h('div', { class: 'kv' }, plan.nutrition.kcal ? `热量 ${plan.nutrition.kcal} kcal` : '热量不设目标', '，',
        plan.nutrition.protein ? `蛋白 ${plan.nutrition.protein} g` : '蛋白不设目标'),
      plan.nutrition.quick_food.length ? h('div', { class: 'tags', style: { marginTop: '6px' } },
        plan.nutrition.quick_food.map((f) => h('span', { class: 'tagc', text: `${f.name} ${f.kcal} kcal` }))) : null);
    }
    const misc = [];
    if (plan.goals.length) misc.push(...plan.goals.map((g) => h('div', { class: 'kv' }, '目标：', h('b', { text: g.name }), g.metric ? `（${g.metric}）` : '')));
    if (m.countdowns && plan.countdowns.length) misc.push(h('div', { class: 'kv' }, '倒计时：' + plan.countdowns.map((c) => `${c.name} ${c.date}`).join('；')));
    if (m.maintenance && plan.maintenance.length) misc.push(h('div', { class: 'kv' }, '周期事务：' + plan.maintenance.map((c) => `${c.name}（每 ${c.every} 天）`).join('；')));
    if (m.people && plan.people.length) misc.push(h('div', { class: 'kv' }, '联络：' + plan.people.map((c) => `${c.name}（每 ${c.every} 天）`).join('；')));
    if (plan.owner.floors.length) misc.push(h('div', { class: 'kv' }, '底线：' + plan.owner.floors.join('；')));
    if (misc.length) sec('目标与日期', false, misc);
    $('#res-sections').replaceChildren(...secs);
    $('#plan-json').value = JSON.stringify(plan, null, 1);
    setStatus('#json-status', '');
    $('#btn-startup').hidden = S.env ? S.env.platform !== 'win32' : false;
  }
  async function applyJson() {
    let obj;
    try {
      obj = JSON.parse($('#plan-json').value);
    } catch (e) {
      setStatus('#json-status', 'JSON 语法有错：' + e.message, 'err');
      return;
    }
    try {
      const r = await api('/api/preview', { plan: obj });
      S.result = Object.assign({}, S.result, { plan: r.plan, preview: r.preview, warnings: r.warnings });
      S.created = null;
      renderResult();
      renderPreview();
      setStatus('#json-status', r.warnings.length ? `已应用，有 ${r.warnings.length} 条提示，见上方。` : '已应用。', 'ok');
    } catch (e) {
      setStatus('#json-status', e.message, 'err');
    }
  }

  // ---------- 启动、快捷方式 ----------
  async function act(kind, mode) {
    if (!S.result) return;
    try {
      if (!S.created) {
        const r = await api('/api/create', { plan: S.result.plan, form: S.result.form || S.form, mode });
        if (r.exists) {
          S.pending = kind;
          $('#exists-text').textContent = `已经有一个叫「${r.name}」的助手。`;
          $('#exists-choice').hidden = false;
          return;
        }
        S.created = r.slug;
        refreshMine();
      }
      $('#exists-choice').hidden = true;
      S.pending = null;
      if (kind === 'launch') {
        const r = await api('/api/launch', { slug: S.created });
        setStatus('#launch-status', r.state === 'running' ? '浮窗已经开着，新配置几秒内自动生效。' : '已启动。浮窗在屏幕右上角，按住标题栏可以拖动。', 'ok');
      } else {
        const r = await api('/api/shortcut', { slug: S.created, where: kind });
        setStatus('#launch-status', (kind === 'desktop' ? '桌面快捷方式已建好：' : '已加入开机自启：') + r.path, 'ok');
      }
      setTimeout(refreshMine, 1500);
    } catch (e) {
      setStatus('#launch-status', e.message, 'err');
    }
  }

  // ---------- 我的助手 ----------
  async function refreshMine() {
    try {
      renderMine((await api('/api/assistants')).assistants);
    } catch (e) { /* 抽屉里再报 */ }
  }
  function renderMine(list) {
    $('#mine-count').textContent = list.length ? String(list.length) : '';
    $('#mine-dir').textContent = S.env ? `存放位置：${S.env.assistants_dir}` : '';
    const box = $('#mine-list');
    if (!list.length) {
      box.replaceChildren(h('p', { class: 'hint', text: '还没有生成过助手。' }));
      return;
    }
    box.replaceChildren(...list.map((a) => {
      let armed = false;
      const delBtn = h('button', {
        type: 'button', class: 'btn small danger', text: '删除',
        onclick: async () => {
          if (!armed) {
            armed = true;
            delBtn.textContent = '确认删除';
            setTimeout(() => { armed = false; delBtn.textContent = '删除'; }, 4000);
            return;
          }
          try {
            const r = await api('/api/delete', { slug: a.slug });
            if (S.created === a.slug) S.created = null;
            toast('已移到回收区：' + r.moved_to);
            refreshMine();
          } catch (e) { toast(e.message, true); }
        },
      });
      return h('div', { class: 'mine' },
        h('div', { class: 'mine-top' }, h('span', { class: 'w-dot', style: { backgroundColor: a.accent } }), h('b', { text: a.name }),
          a.owner ? h('span', { class: 'hint', style: { margin: '0' }, text: a.owner }) : null,
          a.running ? h('span', { class: 'run', text: '运行中' }) : null),
        h('div', { class: 'mine-meta', text: `${a.updated ? '更新于 ' + a.updated + ' · ' : ''}${a.path}` }),
        h('div', { class: 'mine-acts' },
          h('button', { type: 'button', class: 'btn small primary', text: a.running ? '已在运行' : '启动', disabled: a.running,
            onclick: async () => { try { await api('/api/launch', { slug: a.slug }); toast('已启动。'); setTimeout(refreshMine, 1500); } catch (e) { toast(e.message, true); } } }),
          h('button', { type: 'button', class: 'btn small', text: '载入修改', onclick: () => loadAssistant(a.slug) }),
          h('button', { type: 'button', class: 'btn small', text: '打开文件夹', onclick: async () => { try { await api('/api/open', { slug: a.slug }); } catch (e) { toast(e.message, true); } } }),
          delBtn));
    }));
  }
  async function loadAssistant(slug) {
    try {
      const r = await api('/api/load', { slug });
      if (r.form) {
        S.form = withDefaults(r.form);
        saveForm();
        renderForm();
      }
      S.result = { plan: r.plan, preview: r.preview, warnings: r.warnings, notes: [r.form ? '已载入这个助手现在的配置和当时填的表单。' : '已载入这个助手现在的配置。'], form: r.form || S.form, used_ai: false };
      S.stale = false;
      S.created = slug;
      S.pvDay = null;
      toggleDrawer(false);
      showStep(4);
      renderResult();
      refreshPreview();
    } catch (e) { toast(e.message, true); }
  }
  function toggleDrawer(open) {
    $('#drawer').hidden = !open;
    $('#scrim').hidden = !open;
    if (open) refreshMine();
  }

  // ---------- 启动 ----------
  async function init() {
    S.boot = await api('/api/boot');
    for (const [k, name, color] of S.boot.spec.block_types) { S.typeColor[k] = color; S.typeName[k] = name; }
    S.form = withDefaults(loadForm());
    try { S.engine = localStorage.getItem(LS_ENGINE) || 'claude'; } catch (e) { /* 忽略 */ }
    if (!ENGINES.some((e) => e.key === S.engine)) S.engine = 'claude';
    bindInputs();
    renderForm();
    showStep(0);
    renderMine(S.boot.assistants);
    $('#btn-prev').addEventListener('click', () => showStep(S.step - 1));
    $('#btn-next').addEventListener('click', () => showStep(S.step + 1));
    for (const b of $$('[data-add]')) b.addEventListener('click', () => addRow(b.dataset.add));
    $('#btn-gen').addEventListener('click', onGenerate);
    $('#btn-cancel').addEventListener('click', async () => { if (S.job) { try { await api('/api/cancel', { job: S.job }); } catch (e) { /* 轮询会报 */ } } });
    $('#btn-apply-json').addEventListener('click', applyJson);
    $('#btn-launch').addEventListener('click', () => act('launch'));
    $('#btn-desktop').addEventListener('click', () => act('desktop'));
    $('#btn-startup').addEventListener('click', () => act('startup'));
    for (const b of $$('#exists-choice [data-mode]')) b.addEventListener('click', () => act(S.pending || 'launch', b.dataset.mode));
    $('#btn-mine').addEventListener('click', () => toggleDrawer(true));
    $('#btn-close-drawer').addEventListener('click', () => toggleDrawer(false));
    $('#scrim').addEventListener('click', () => toggleDrawer(false));
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !$('#drawer').hidden) toggleDrawer(false); });
    await refreshPreview();
    loadEnv(false);
    await applyHash();
  }

  // 地址后面可以带参数直达某个状态：#example=student&step=4 载入示例并跳到第 4 步；#load=助手文件夹名 载入已有的助手
  async function applyHash() {
    const q = new URLSearchParams(location.hash.replace(/^#/, ''));
    const ex = S.boot.examples.find((e) => e.key === q.get('example'));
    if (ex) {
      S.form = withDefaults(clone(ex.form));
      renderForm();
      await refreshPreview();
    }
    if (q.get('load')) await loadAssistant(q.get('load'));
    const step = parseInt(q.get('step'), 10);
    if (step >= 1 && step <= STEPS.length) showStep(step - 1);
  }
  init().catch((e) => {
    document.body.append(h('div', { class: 'toast err', text: '页面初始化失败：' + e.message }));
  });
})();
