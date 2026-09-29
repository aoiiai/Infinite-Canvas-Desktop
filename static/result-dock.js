/* 生成结果栏 · 通用组件（2026-09-25 抽出）
 *
 * 为什么单独一个文件：这个「生成结果」列表要能**换页复用**（视频生成页现在用，
 * 以后文生图页也用它，不再各写一份）。
 *
 * 分工（重要）：
 *   · 组件管：列表怎么画、点开怎么播、空态、计数、媒体与提示词记录**按提示词配对去重**。
 *   · 页面管：数据从哪来（load）、「回填 / 删除」做什么（onUse / onDelete）、
 *             外壳（卡片壳由页面给，组件只写里面的内容）。
 *
 * 用法（classic script，先用 <script src="/static/result-dock.js"></script> 引入）：
 *   const dock = ResultDock.mount({
 *     host: '#resultDockHost',                 // 必填：挂载点（元素或选择器）
 *     title: '生成结果',                        // 可选，默认「生成结果」
 *     emptyText: '还没有生成结果',               // 可选
 *     onHide: () => toggleDockHidden('result'), // 可选：给了就在头部右侧画「隐藏」×
 *     load: async () => ({ media: [], records: [] }),  // 每次 refresh 都调
 *     recMeta: r => [r.at, r.workflow],         // 可选：记录行的元信息文字（数组）
 *     defaultKind: 'video',                     // 可选：认不出后缀时当什么（默认 video）
 *     onUse: (rec, item) => {},                 // 可选：点「回填」（rec 可能为 null）
 *     onDelete: rec => {},                      // 可选：给了才显示「删除」
 *     onOpen: url => {},                        // 可选：图片点开（默认新窗口）
 *     onRendered: () => {}                      // 可选：每次重画完（页面同步高度用）
 *   });
 *   dock.refresh();   // 重新取数 + 重画
 *   dock.render();    // 用现有数据重画
 *   dock.setData({ media, records });
 *   dock.setTitle('…');
 *   dock.destroy();
 *
 * media 条目：后端历史里的一条（对象），URL 从 videos / outputs / items / images 里找；
 * records 条目：提示词记录（对象，字段 prompt / at / workflow / status…）。
 */
window.ResultDock = (function () {
    'use strict';

    const STYLE_ID = 'result-dock-style';
    const CSS = `
.rd-host { display: block; }
.rd-head { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.rd-title { font-size: 11px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: #9ca3af; }
.rd-count { font-size: 12px; font-weight: 700; color: #d1d5db; margin-left: auto; }
.rd-x {
    width: 26px; height: 26px; border-radius: 8px; border: none; background: none;
    color: #9ca3af; cursor: pointer; font-size: 15px; line-height: 1;
    display: inline-flex; align-items: center; justify-content: center; flex-shrink: 0;
}
.rd-x:hover { background: rgba(0, 0, 0, 0.06); color: #111827; }
.rd-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 16px; }
.rd-empty {
    padding: 22px 0; text-align: center; font-size: 12px; font-weight: 700;
    color: #c7ccd4;
}
.rd-item {
    position: relative; border-radius: 18px; overflow: hidden; display: flex; flex-direction: column;
    background: #fff; border: 1px solid #f1f5f9; cursor: pointer;
    transition: transform 0.3s ease, box-shadow 0.3s ease;
}
.rd-item:hover { transform: translateY(-4px); box-shadow: 0 16px 32px rgba(0, 0, 0, 0.08); }
.rd-media { position: relative; width: 100%; aspect-ratio: 16 / 9; background: #000; overflow: hidden; }
.rd-media video, .rd-media img { width: 100%; height: 100%; object-fit: cover; display: block; }
/* 按钮行在视频**下面**（用户 2026-09-25：「现在卡在视频里，太小了」→ 挪出来 + 做成正常大小的按钮） */
.rd-foot { display: flex; align-items: center; justify-content: flex-end; gap: 8px; padding: 8px 10px; }
.rd-btn {
    font: inherit; font-size: 12.5px; font-weight: 800; padding: 6px 14px; border-radius: 999px;
    border: 1px solid rgba(0, 0, 0, 0.16); background: #fff; color: #374151; cursor: pointer;
    flex-shrink: 0; transition: border-color 0.15s ease, color 0.15s ease, background 0.15s ease;
}
.rd-btn:hover { border-color: rgba(37, 99, 235, 0.55); color: #2563eb; }
html.studio-theme-dark .rd-btn { background: #111827; border-color: rgba(255, 255, 255, 0.18); color: #e5e7eb; }
html.studio-theme-dark .rd-btn:hover { border-color: rgba(96, 165, 250, 0.6); color: #93c5fd; }
.rd-btn-danger { color: #dc2626; border-color: rgba(220, 38, 38, 0.3); }
.rd-rec {
    display: flex; align-items: flex-start; justify-content: space-between; gap: 12px;
    padding: 12px 14px; border: 1px solid rgba(0, 0, 0, 0.06); border-radius: 16px;
    cursor: pointer; transition: background 0.16s ease;
    /* 结果栏窄的时候（左栏只有 150~200px）按钮会挤掉标题 ⇒ 允许换行，
       否则标题被压成一字一行（2026-09-29 截图里实测到） */
    flex-wrap: wrap;
}
.rd-rec:hover { background: rgba(0, 0, 0, 0.02); }
.rd-rec-main { min-width: 0; flex: 1 1 140px; }
.rd-rec-title { font-size: 13px; font-weight: 700; color: #111827;
    overflow: hidden; text-overflow: ellipsis; }
.rd-rec-meta { display: flex; flex-wrap: wrap; gap: 4px 10px; margin-top: 4px;
    font-size: 11px; font-weight: 600; color: #9ca3af; }
.rd-rec-acts { display: flex; gap: 10px; flex-shrink: 0; }
.rd-rec .rd-btn { color: #2563eb; }
.rd-rec .rd-btn-danger { color: #dc2626; }
.rd-warn { color: #f59e0b; }
html.studio-theme-dark .rd-title { color: #6b7280; }
html.studio-theme-dark .rd-count { color: #4b5563; }
html.studio-theme-dark .rd-hint { color: #4b5563; }
.rd-hint { font-size: 11px; font-weight: 600; color: #6b7280; white-space: nowrap; }
html.studio-theme-dark .rd-x:hover { background: rgba(255, 255, 255, 0.1); color: #f3f4f6; }
html.studio-theme-dark .rd-empty { color: #6b7280; }
html.studio-theme-dark .rd-item { border-color: rgba(255, 255, 255, 0.08); background: #1f2937; }
html.studio-theme-dark .rd-rec { border-color: rgba(255, 255, 255, 0.1); }
html.studio-theme-dark .rd-rec:hover { background: rgba(255, 255, 255, 0.04); }
html.studio-theme-dark .rd-rec-title { color: #f3f4f6; }

/* 放大灯箱：尺寸全用百分比（不用 vw/vh —— 外层会给 iframe 做缩放，百分比跟着遮罩走最稳） */
.rd-lightbox { position: fixed; inset: 0; z-index: 100; background: rgba(0, 0, 0, .84);
    display: flex; align-items: center; justify-content: center; padding: 3%; }
.rd-lightbox img, .rd-lightbox video { max-width: 100%; max-height: 100%; border-radius: 10px;
    background: #000; display: block; }
.rd-lightbox-x { position: absolute; top: 12px; right: 14px; width: 42px; height: 42px;
    border-radius: 50%; border: 0; background: rgba(255, 255, 255, .16); color: #fff;
    font-size: 22px; line-height: 1; cursor: pointer; }
.rd-lightbox-x:hover { background: rgba(255, 255, 255, .28); }
/* 视频：浏览器那套控件 UI 整个隐藏（用户 2026-09-25 要求「那个 ui 整个隐藏」），
   播放/暂停就点画面本身；顺带把画中画按钮也去掉了 */
.rd-item video::-webkit-media-controls,
.rd-item video::-webkit-media-controls-enclosure,
.rd-item video::-webkit-media-controls-panel,
.rd-item video::-webkit-media-controls-overlay-play-button,
.rd-item video::-webkit-media-controls-start-playback-button,
.rd-lightbox video::-webkit-media-controls,
.rd-lightbox video::-webkit-media-controls-enclosure,
.rd-lightbox video::-webkit-media-controls-panel,
.rd-lightbox video::-webkit-media-controls-overlay-play-button,
.rd-lightbox video::-webkit-media-controls-start-playback-button { display: none !important; }
.rd-item video, .rd-lightbox video { cursor: pointer; }

/* 关掉浏览器的画中画按钮（鼠标悬到视频上会冒出来的那个图标，用户 2026-09-25 报） */
.rd-item video::-webkit-media-controls-picture-in-picture-button,
.rd-lightbox video::-webkit-media-controls-picture-in-picture-button { display: none !important; }
video.rd-nopip::-webkit-media-controls-picture-in-picture-button { display: none !important; }
`;

    function ensureCss() {
        if (document.getElementById(STYLE_ID)) return;
        const s = document.createElement('style');
        s.id = STYLE_ID;
        s.textContent = CSS;
        document.head.appendChild(s);
    }

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    // ---------- 放大灯箱（图片 / 视频都用）----------
    // 单点卡片还是"就地播放/暂停"（老行为不变），放大走这个：卡片上的「放大」按钮或双击卡片。
    let lightboxEl = null;
    function lightboxEsc(e) {
        if (e.key === 'Escape') { e.stopPropagation(); closeLightbox(); }
    }
    function closeLightbox() {
        if (!lightboxEl) return;
        const v = lightboxEl.querySelector('video');
        if (v) { try { v.pause(); } catch (e) { } v.removeAttribute('src'); }
        if (lightboxEl.parentNode) lightboxEl.parentNode.removeChild(lightboxEl);
        lightboxEl = null;
        document.removeEventListener('keydown', lightboxEsc, true);
    }
    function openLightbox(url, kind, prompt) {
        closeLightbox();
        // 列表里正在播的先停掉 —— 否则放大后两个一起响（用户 2026-09-25 报）
        try {
            if (dockHost) dockHost.querySelectorAll('video').forEach(function (v) { v.pause(); });
        } catch (e) { }
        const box = document.createElement('div');
        box.className = 'rd-lightbox';
        let el;
        if (kind === 'image') {
            el = document.createElement('img');
            el.src = url;
        } else {
            el = document.createElement('video');
            el.src = url;
            el.controls = false;                        // 浏览器控件整个不显示（点画面播放/暂停）
            el.autoplay = true;
            el.playsInline = true;
            el.disablePictureInPicture = true;
            el.setAttribute('disablepictureinpicture', '');
            el.addEventListener('click', function (e) {   // 点画面 = 播放/暂停（不开浏览器控件）
                e.stopPropagation();
                if (el.paused) {
                    const p = el.play();
                    if (p && p.catch) p.catch(function () { });
                } else {
                    el.pause();
                }
            });
        }
        box.appendChild(el);
        const x = document.createElement('button');
        x.type = 'button';
        x.className = 'rd-lightbox-x';
        x.textContent = '×';
        x.title = '关闭（Esc）';
        x.addEventListener('click', function (e) { e.stopPropagation(); closeLightbox(); });
        box.appendChild(x);
        box.addEventListener('click', function (e) { if (e.target === box) closeLightbox(); });
        document.addEventListener('keydown', lightboxEsc, true);
        // ⚠ 必须挂在 <html> 上，**不能挂 <body>**：页面外壳会给 body 加 `transform: scale(...)`
        //   （UI 缩放），而 transform 会成为 `position:fixed` 的**包含块** ⇒ 灯箱不再相对视口定位、
        //   变成跟着 body 走：页面一滚动灯箱就往上跑、底部露出页面（用户 2026-09-28 报
        //   「打开图片不会置顶」）。`<html>` 上没有 transform，挂这儿才是真的铺满视口。
        // ⚠ z-index 必须顶格：历史/素材库等弹窗（.editor-overlay）会被页面的动态层叠管理器抬到
        //   140+，而 CSS 里写死 z-index:100 ⇒ 从弹窗里开灯箱会被压在弹窗**下面**（用户
        //   2026-09-29 报「打开都不会出现在界面顶层」）。给顶格值，任何弹窗都压不住它。
        box.style.zIndex = '2147483000';
        document.documentElement.appendChild(box);
        lightboxEl = box;
        if (kind !== 'image') {
            try { el.play(); } catch (e) { }   // 点「放大」本身构成用户手势，带声音能播
        }
    }

    // 认 URL 是视频还是图片：先看后缀，再看条目自带的 kind
    // ⚠ 后缀后面可能是 & —— 后端的媒体地址长这样：
    //   /api/view?filename=MiniMax_H3_00236_.mp4&subfolder=video%5CMiniMax_H3&type=output
    //   原来只认 (?|$) → 这种"认不出"，结果栏直接跳过（2026-09-25 用户报：生成的视频没出现在生成结果里）
    function mediaKind(url, obj) {
        if (/\.(mp4|webm|mov|m4v)([?&#]|$)/i.test(url)) return 'video';
        if (/\.(png|jpe?g|webp|gif|avif|bmp)([?&#]|$)/i.test(url)) return 'image';
        if (obj && typeof obj === 'object') {
            if (obj.kind === 'video') return 'video';
            if (obj.kind === 'image') return 'image';
        }
        return '';
    }

    // 从一条历史里挑出可显示的媒体（后端不同工作流的字段名不一样，挨个找）
    function pickMedia(item, defaultKind) {
        if (!item) return null;
        const lists = [item.videos, item.outputs, item.items, item.images];
        for (const list of lists) {
            if (!Array.isArray(list)) continue;
            for (const it of list) {
                const url = typeof it === 'string' ? it : ((it && it.url) || '');
                if (!url) continue;
                const kind = mediaKind(url, it);
                if (kind) return { url: url, kind: kind, raw: item };
            }
        }
        const first = (item.outputs || [])[0];
        const url = typeof first === 'string' ? first : ((first && first.url) || '');
        return url ? { url: url, kind: defaultKind || 'video', raw: item } : null;
    }

    let dockHost = null;      // 当前挂载点（灯箱要拿它找列表里的视频，见 openLightbox）

    // 下载用的文件名：优先 ?filename=，其次路径末段，兜底 result（用户 2026-09-29 要「下载」按钮）
    function downloadName(url, kind) {
        const fallback = 'result' + (kind === 'video' ? '.mp4' : '.png');
        let u = String(url || '');
        if (!u || /^(data|blob):/i.test(u)) return fallback;      // data:/blob: 推不出名字
        let name = '';
        try {
            const m = /[?&]filename=([^&]+)/.exec(u);
            if (m) name = decodeURIComponent(m[1]);
        } catch (e) { }
        if (!name) {
            try { name = u.split('?')[0].split('#')[0].split('/').pop() || ''; } catch (e) { }
        }
        // 去掉文件名里不能用的字符，并限长（别让整段 URL 变成文件名）
        name = String(name).replace(/[\\/:*?"<>|\s]+/g, '_').slice(-80);
        if (!name || name.indexOf('.') < 0) name = (name || 'result') + (kind === 'video' ? '.mp4' : '.png');
        return name;
    }

    // 默认「下载」：同源用 <a download> 直接存盘；跨域时 download 会被忽略，退回新标签打开
    function defaultDownload(url, kind) {
        try {
            const a = document.createElement('a');
            a.href = url;
            a.download = downloadName(url, kind);
            a.rel = 'noopener';
            document.body.appendChild(a);
            a.click();
            a.remove();
        } catch (e) {
            try { window.open(url, '_blank'); } catch (e2) { }
        }
    }

    function mount(opts) {
        opts = opts || {};
        const host = typeof opts.host === 'string' ? document.querySelector(opts.host) : opts.host;
        if (!host) throw new Error('ResultDock.mount: 挂载点不存在');
        dockHost = host;
        ensureCss();

        host.classList.add('rd-host');
        host.innerHTML = '';

        const head = document.createElement('div');
        head.className = 'rd-head';
        const titleEl = document.createElement('span');
        titleEl.className = 'rd-title';
        titleEl.textContent = opts.title || '生成结果';
        const countEl = document.createElement('span');
        countEl.className = 'rd-count';
        head.appendChild(titleEl);
        head.appendChild(countEl);
        const hintEl = document.createElement('span');
        hintEl.className = 'rd-hint';
        hintEl.textContent = '（点画面播放/暂停）';
        hintEl.style.display = 'none';
        head.appendChild(hintEl);
        if (opts.headExtra) head.appendChild(opts.headExtra);
        if (typeof opts.onHide === 'function') {
            const x = document.createElement('button');
            x.type = 'button';
            x.className = 'rd-x';
            x.title = opts.hideTitle || '隐藏这一栏';
            x.textContent = '\u00d7';
            x.addEventListener('click', opts.onHide);
            head.appendChild(x);
        }

        const grid = document.createElement('div');
        grid.className = 'rd-grid';
        host.appendChild(head);
        host.appendChild(grid);

        let data = { media: [], records: [] };
        let seq = 0;                       // 防止两次 refresh 乱序覆盖

        function togglePlay(v) {
            if (v.paused) {
                grid.querySelectorAll('video').forEach(function (x) { if (x !== v) x.pause(); });
                v.muted = false;           // 点开的这条要能听声音（真实点击 = 用户手势，浏览器才放行）
                // ⚠ 这里**不要**再开 v.controls —— 用户要的就是把那套浏览器控件整个藏掉（2026-09-25）
                const p = v.play();
                if (p && p.catch) p.catch(function () { });
            } else {
                v.pause();
            }
        }

        function openUrl(url) {
            if (typeof opts.onOpen === 'function') opts.onOpen(url);
            else window.open(url, '_blank');
        }

        function render() {
            grid.innerHTML = '';
            const used = [];               // 已经被媒体卡带走的记录，别再单独画一行
            const pickRec = function (p) {
                if (!p) return null;
                const hit = data.records.find(function (r) {
                    return (r.prompt || '').trim() === p && used.indexOf(r) < 0;
                });
                if (hit) used.push(hit);
                return hit || null;
            };
            let shown = 0;
            let hasVideo = false;

            for (const item of data.media) {
                const m = pickMedia(item, opts.defaultKind);
                if (!m) continue;
                if (m.kind !== 'image') hasVideo = true;
                const p = (item.prompt || '').trim();
                // 页面给了 _rec（按媒体地址认出来的那条记录）就直接用 —— 同一个提示词跑多次时，
                // 按提示词猜会配错人（2026-09-26）
                let rec = null;
                if (item && item._rec && used.indexOf(item._rec) < 0) {
                    rec = item._rec;
                    used.push(rec);
                } else {
                    rec = pickRec(p);
                }
                const card = document.createElement('div');
                card.className = 'rd-item';
                let el;
                if (m.kind === 'image') {
                    el = document.createElement('img');
                    el.src = m.url;
                    el.alt = p;
                    el.loading = 'lazy';
                } else {
                    el = document.createElement('video');
                    el.src = m.url;
                    el.preload = 'metadata';
                    el.muted = true;
                    el.playsInline = true;
                    el.disablePictureInPicture = true;      // 悬停别冒画中画图标
                    el.setAttribute('disablepictureinpicture', '');
                }
                const media = document.createElement('div');
                media.className = 'rd-media';
                media.appendChild(el);
                card.appendChild(media);
                // 按钮行放在媒体**下面**；这里也**不显示提示词**
                // （用户 2026-09-25：「[subject_definitions]… 这是什么东西啊，不要显示」—— H3 那类
                //   工作流的提示词是整段模板文本，压在封面上又长又乱）
                const foot = document.createElement('div');
                foot.className = 'rd-foot';
                // 按钮顺序按用户 2026-09-29 说的：放大 / 回填 / 下载
                foot.innerHTML = '<button type="button" class="rd-btn" data-act="zoom">放大</button>'
                    + (opts.onUse ? '<button type="button" class="rd-btn" data-act="use">回填</button>' : '')
                    + (opts.onDownload === false
                        ? '' : '<button type="button" class="rd-btn" data-act="download">下载</button>');
                card.appendChild(foot);
                card.addEventListener('click', function (e) {
                    const act = e.target && e.target.getAttribute ? e.target.getAttribute('data-act') : null;
                    if (act === 'zoom') {          // 「放大」：灯箱里看大图/大视频
                        e.stopPropagation();
                        openLightbox(m.url, m.kind, p);
                        return;
                    }
                    if (act === 'use') {           // 「回填」别顺带把播放也触发了
                        e.stopPropagation();
                        if (opts.onUse) opts.onUse(rec, item);
                        return;
                    }
                    if (act === 'download') {      // 「下载」：页面没给 onDownload 就走默认存盘
                        e.stopPropagation();
                        if (typeof opts.onDownload === 'function') opts.onDownload(m.url, item, m.kind);
                        else defaultDownload(m.url, m.kind);
                        return;
                    }
                    if (m.kind === 'video') togglePlay(el);
                    else openUrl(m.url);
                });
                card.addEventListener('dblclick', function (e) {
                    if (e.target && e.target.closest && e.target.closest('[data-act]')) return;
                    openLightbox(m.url, m.kind, p);
                });
                grid.appendChild(card);
                shown++;
            }

            hintEl.style.display = hasVideo ? '' : 'none';   // 没有视频卡就不显示那行提示

            for (const r of data.records) {
                if (used.indexOf(r) >= 0) continue;
                const meta = (opts.recMeta ? opts.recMeta(r) : [r.at, r.workflow]) || [];
                const row = document.createElement('div');
                row.className = 'rd-rec';
                row.innerHTML = '<div class="rd-rec-main">'
                    + '<div class="rd-rec-title">' + esc(r.prompt || '（空提示词）') + '</div>'
                    + '<div class="rd-rec-meta">'
                    + meta.filter(Boolean).map(function (x) { return '<span>' + esc(x) + '</span>'; }).join('')
                    + (r.status === 'failed' ? '<span class="rd-warn">未成功</span>' : '')
                    + '</div></div>'
                    + '<div class="rd-rec-acts">'
                    + (opts.onUse ? '<button type="button" class="rd-btn" data-act="use">回填</button>' : '')
                    + (opts.onDelete ? '<button type="button" class="rd-btn rd-btn-danger" data-act="del">删除</button>' : '')
                    + '</div>';
                row.addEventListener('click', function (e) {
                    const act = e.target && e.target.getAttribute ? e.target.getAttribute('data-act') : null;
                    if (act === 'del') { e.stopPropagation(); if (opts.onDelete) opts.onDelete(r); return; }
                    if (opts.onUse) opts.onUse(r, null);
                });
                grid.appendChild(row);
                shown++;
            }

            if (!shown) {
                const e = document.createElement('div');
                e.className = 'rd-empty';
                e.textContent = opts.emptyText || '还没有生成结果';
                grid.appendChild(e);
            }
            countEl.textContent = shown ? shown + ' 条' : '';
            if (typeof opts.onRendered === 'function') {
                try { opts.onRendered(); } catch (err) { }
            }
        }

        const api = {
            version: '2026-09-25',
            el: host,
            grid: grid,
            render: render,
            setData: function (d) {
                data = {
                    media: (d && Array.isArray(d.media)) ? d.media : [],
                    records: (d && Array.isArray(d.records)) ? d.records : []
                };
                render();
            },
            setTitle: function (t) { titleEl.textContent = t; },
            refresh: async function () {
                const my = ++seq;
                let d = null;
                try {
                    d = await (typeof opts.load === 'function' ? opts.load() : { media: [], records: [] });
                } catch (e) {
                    d = { media: [], records: [] };
                }
                if (my !== seq) return;    // 有更新的那次 refresh 在跑，这次结果作废
                api.setData(d);
            },
            destroy: function () { host.innerHTML = ''; }
        };

        render();                          // 先画出来（空态），页面再 refresh 填数据
        return api;
    }

    // pickMedia 也对外给一份：页面别的地方要在「任务结果」里挑媒体地址时用得上
    // mount / pickMedia / mediaKind 之外，把灯箱也导出：别处（历史弹窗）点卡片放大直接复用，
    // 不再各写一套放大逻辑（2026-09-26）
    return { mount: mount, pickMedia: pickMedia, mediaKind: mediaKind, openLightbox: openLightbox };
})();
