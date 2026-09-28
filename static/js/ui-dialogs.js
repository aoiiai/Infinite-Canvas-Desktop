/* ui-dialogs.js — 全局自制弹窗（替代系统 alert / confirm / prompt）
 *
 * 用法：在页面脚本**之前**引入即可
 *     <script src="/static/js/ui-dialogs.js?v=..."></script>
 *
 * 暴露（都是 Promise）：
 *     window.uiAlert(msg, title)                     → Promise<void>
 *     window.uiConfirm({title,msg,okText,okStyle})   → Promise<boolean>
 *     window.uiPrompt({title,msg,value,placeholder}) → Promise<string|null>
 *
 * 副作用：覆盖 window.alert（调用点一行都不用改）。
 *         ⚠ confirm / prompt 是**同步**返回值，无法安全覆盖（`if(!confirm(..))` 会失效），
 *           必须把调用点改成 `await uiConfirm(...)` / `await uiPrompt(...)`。
 *
 * 样式：自带 CSS（uidlg- 前缀，不依赖 theme.css），跟随 studio-theme-dark 暗色。
 */
(function () {
    'use strict';
    if (window.__uiDialogsReady) return;
    window.__uiDialogsReady = true;

    var CSS = [
        '.uidlg-overlay{position:fixed;inset:0;z-index:2147483000;display:none;align-items:center;justify-content:center;background:rgba(17,24,39,.45)}',
        '.uidlg-overlay.uidlg-dark{background:rgba(0,0,0,.62)}',
        '.uidlg-panel{width:min(420px,86vw);max-height:86vh;overflow:auto;box-sizing:border-box;padding:22px 22px 18px;border-radius:18px;background:#fff;box-shadow:0 30px 80px rgba(0,0,0,.28);font-family:inherit;animation:uidlg-in .16s ease-out}',
        '.uidlg-overlay.uidlg-dark .uidlg-panel{background:#111827;box-shadow:0 30px 80px rgba(0,0,0,.65)}',
        '@keyframes uidlg-in{from{opacity:0;transform:translateY(8px) scale(.98)}to{opacity:1;transform:none}}',
        '.uidlg-title{font-size:15px;font-weight:800;color:#111827}',
        '.uidlg-overlay.uidlg-dark .uidlg-title{color:#f3f4f6}',
        '.uidlg-msg{margin-top:8px;font-size:13px;line-height:1.7;color:#6b7280;word-break:break-word;white-space:pre-wrap}',
        '.uidlg-overlay.uidlg-dark .uidlg-msg{color:#9ca3af}',
        /* 输入框：theme.css 有 `body input{...!important}`，这里也用 !important 反制 */
        '.uidlg-overlay .uidlg-input{display:block;width:100%;box-sizing:border-box;margin-top:12px;height:36px;padding:0 12px;border-radius:10px;background:#fff !important;border:1px solid rgba(0,0,0,.14) !important;color:#111827 !important;font:inherit;font-size:14px}',
        '.uidlg-overlay.uidlg-dark .uidlg-input{background:rgba(255,255,255,.06) !important;border-color:rgba(255,255,255,.16) !important;color:#e5e7eb !important}',
        '.uidlg-actions{display:flex;justify-content:flex-end;gap:10px;margin-top:18px}',
        '.uidlg-btn{height:36px;padding:0 20px;border-radius:10px;border:1px solid rgba(0,0,0,.14);background:#fff;color:#374151;font:inherit;font-size:14px;font-weight:700;cursor:pointer;transition:all .15s ease}',
        '.uidlg-btn:hover{border-color:rgba(0,0,0,.3);color:#111827}',
        '.uidlg-btn.danger{background:#dc2626;border-color:#dc2626;color:#fff}',
        '.uidlg-btn.danger:hover{background:#b91c1c;border-color:#b91c1c;color:#fff}',
        '.uidlg-btn.primary{background:#111827;border-color:#111827;color:#fff}',
        '.uidlg-btn.primary:hover{background:#1f2937;color:#fff}',
        '.uidlg-overlay.uidlg-dark .uidlg-btn{background:rgba(255,255,255,.06);border-color:rgba(255,255,255,.16);color:#d1d5db}',
        '.uidlg-overlay.uidlg-dark .uidlg-btn.danger{background:#dc2626;border-color:#dc2626;color:#fff}',
        '.uidlg-overlay.uidlg-dark .uidlg-btn.primary{background:#f3f4f6;border-color:#f3f4f6;color:#111827}'
    ].join('');

    function injectCSS() {
        if (document.getElementById('uidlg-style')) return;
        var s = document.createElement('style');
        s.id = 'uidlg-style';
        s.textContent = CSS;
        (document.head || document.documentElement).appendChild(s);
    }

    function isDark() {
        try {
            var de = document.documentElement;
            if (de.classList.contains('studio-theme-dark') || de.classList.contains('theme-dark')) return true;
            var t = localStorage.getItem('studio_theme') || localStorage.getItem('canvas_theme');
            return t === 'dark';
        } catch (e) { return false; }
    }

    var overlay = null, openNow = false;
    var queue = [];

    function ensure() {
        if (overlay && document.body && document.body.contains(overlay)) return overlay;
        overlay = document.createElement('div');
        overlay.className = 'uidlg-overlay';
        overlay.style.display = 'none';
        overlay.innerHTML =
            '<div class="uidlg-panel" role="dialog" aria-modal="true">' +
            '<div class="uidlg-title"></div>' +
            '<div class="uidlg-msg"></div>' +
            '<input class="uidlg-input" type="text" style="display:none">' +
            '<div class="uidlg-actions">' +
            '<button type="button" class="uidlg-btn uidlg-cancel">取消</button>' +
            '<button type="button" class="uidlg-btn uidlg-ok">确定</button>' +
            '</div>' +
            '</div>';
        document.body.appendChild(overlay);
        return overlay;
    }

    function open(opts) {
        opts = opts || {};
        return new Promise(function (resolve) {
            var task = { opts: opts, resolve: resolve };
            if (openNow) { queue.push(task); return; }
            show(task);
        });
    }

    function show(task) {
        var opts = task.opts, resolve = task.resolve;
        var mode = opts.mode || 'confirm';           // confirm | alert | prompt
        openNow = true;

        var r = ensure();
        r.classList.toggle('uidlg-dark', isDark());
        var titleEl = r.querySelector('.uidlg-title');
        var msgEl = r.querySelector('.uidlg-msg');
        var inputEl = r.querySelector('.uidlg-input');
        var okBtn = r.querySelector('.uidlg-ok');
        var cancelBtn = r.querySelector('.uidlg-cancel');

        titleEl.textContent = opts.title || (mode === 'alert' ? '提示' : '确认');
        msgEl.textContent = opts.msg == null ? '' : String(opts.msg);
        msgEl.style.display = opts.msg ? '' : 'none';
        okBtn.textContent = opts.okText || (mode === 'alert' ? '知道了' : '确定');
        okBtn.className = 'uidlg-btn uidlg-ok ' + (opts.okStyle === 'primary' ? 'primary' : 'danger');
        cancelBtn.style.display = (mode === 'alert') ? 'none' : '';

        if (mode === 'prompt') {
            inputEl.style.display = '';
            inputEl.value = opts.value == null ? '' : String(opts.value);
            inputEl.placeholder = opts.placeholder || '';
        } else {
            inputEl.style.display = 'none';
        }

        var settled = false;
        function done(v) {
            if (settled) return;
            settled = true;
            r.style.display = 'none';
            okBtn.onclick = cancelBtn.onclick = r.onclick = inputEl.onkeydown = null;
            document.removeEventListener('keydown', onKey, true);
            openNow = false;
            resolve(v);
            var next = queue.shift();
            if (next) show(next);
        }
        function cancelValue() { return mode === 'prompt' ? null : (mode === 'alert' ? true : false); }
        function onKey(e) {
            if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); done(cancelValue()); }
            else if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); done(mode === 'prompt' ? inputEl.value : true); }
        }

        okBtn.onclick = function () { done(mode === 'prompt' ? inputEl.value : true); };
        cancelBtn.onclick = function () { done(cancelValue()); };
        r.onclick = function (e) { if (e.target === r) done(cancelValue()); };
        document.addEventListener('keydown', onKey, true);

        r.style.display = 'flex';
        setTimeout(function () { try { (mode === 'prompt' ? inputEl : okBtn).focus(); } catch (e) { } }, 0);
    }

    window.uiAlert = function (msg, title) { return open({ mode: 'alert', msg: msg, title: title }); };
    window.uiConfirm = function (opts) {
        opts = opts || {};
        return open({ mode: 'confirm', title: opts.title, msg: opts.msg, okText: opts.okText, okStyle: opts.okStyle });
    };
    window.uiPrompt = function (opts) {
        opts = opts || {};
        return open({ mode: 'prompt', title: opts.title, msg: opts.msg, value: opts.value, placeholder: opts.placeholder, okText: opts.okText });
    };

    // 覆盖 window.alert —— 110 处调用点一行都不用改
    var nativeAlert = window.alert;
    window.alert = function (msg) {
        try { window.uiAlert(msg == null ? '' : String(msg)); }
        catch (e) { try { nativeAlert.call(window, msg); } catch (e2) { } }
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', injectCSS, { once: true });
    } else {
        injectCSS();
    }
})();
