/* 工作流「默认配置」自动匹配（自建组件 · 视频生成页在用）
 *
 * 解决的问题：导入一份新的工作流 API JSON 之后，原来要进「工作流设置」页，
 * 在画布节点预览里一个输入一个输入地点，才知道这个工作流能配哪些参数。
 * 现在按「默认配置」往新工作流上套：能认出角色的节点自动匹配好，
 * 认不出的在弹层里「换节点」，比一个个挑简单。
 *
 * 默认配置分两类（用户要求：视频 / 图片分开，不要混在一起）：
 *   视频 → 直接读 MiniMax_H3 那份 .config.json（用户要求「以它为准」），读不到用内置兜底
 *   图片 → 内置一份（程序里目前没有图片工作流的现成配置可取）
 * 已有的自定义配置（用户自己配过的工作流）只「补齐」缺的角色，不覆盖已有字段。
 *
 * 这个文件只提供纯逻辑（匹配 / 生成字段 / 列候选），界面在页面里。
 * 加载它的页面用 window.WorkflowAutoFields 取用。
 */
(function () {
    'use strict';

    // ---------- 默认配置（模板）----------

    // 视频默认 = MiniMax_H3.config.json 的抄本（读不到接口时用这份）
    const VIDEO_FALLBACK = [
        { id: 'f_prompt', node: '', input: 'value', name: '提示词', type: 'textarea', default: '', min: null, max: null, step: null, options: [], bind_prompt: true },
        { id: 'f_duration', node: '', input: 'value', name: '时长（秒）', type: 'number', default: 8, min: 1, max: 15, step: 1, options: [] },
        { id: 'f_ratio', node: '', input: 'aspect_ratio', name: '画面比例', type: 'dropdown', default: '16:9 (Widescreen)', min: null, max: null, step: null, options: ['1:1 (Square)', '2:3 (Portrait Photo)', '3:2 (Photo)', '3:4 (Portrait Standard)', '4:3 (Standard)', '9:16 (Portrait Widescreen)', '16:9 (Widescreen)', '21:9 (Ultrawide)'] },
        { id: 'f_megapixels', node: '', input: 'megapixels', name: '百万像素', type: 'number', default: 0.4, min: 0.1, max: 2, step: 0.1, options: [] },
        { id: 'f_seed', node: '', input: 'noise_seed', name: '随机种子', type: 'number', default: 0, min: 0, max: 9007199254740991, step: 1, options: [], random_enabled: true }
    ];

    // 图片默认：提示词 / 随机种子 / 宽 / 高 / 步数（不含视频那套 时长/比例/百万像素）
    const IMAGE_TEMPLATE = [
        { id: 'f_prompt', node: '', input: 'text', name: '提示词', type: 'textarea', default: '', min: null, max: null, step: null, options: [], bind_prompt: true },
        { id: 'f_seed', node: '', input: 'noise_seed', name: '随机种子', type: 'number', default: 0, min: 0, max: 9007199254740991, step: 1, options: [], random_enabled: true },
        { id: 'f_width', node: '', input: 'width', name: '宽度', type: 'number', default: 1024, min: 64, max: 8192, step: 32, options: [] },
        { id: 'f_height', node: '', input: 'height', name: '高度', type: 'number', default: 1024, min: 64, max: 8192, step: 32, options: [] },
        { id: 'f_steps', node: '', input: 'steps', name: '步数', type: 'number', default: 20, min: 1, max: 200, step: 1, options: [] }
    ];

    // 视频工作流里最常见的"输出视频"节点；图片工作流是 SaveImage
    const VIDEO_CLASS_RE = /SaveVideo|CreateVideo|VideoCombine|SaveWEBM|VHS_|LTXDirector|MiniMaxH3|WanVideo|HunyuanVideo|AnimateDiff|SVD/i;
    const IMAGE_CLASS_RE = /SaveImage|PreviewImage/i;
    const TEXT_INPUT_RE = /prompt|text|value|string|caption|positive/i;
    // 名字里带这些的别当提示词：负向、文件名、时间轴数据、媒体、表达式…
    const SKIP_TEXT_INPUT_RE = /negative|filename|prefix|data|json|timeline|image|audio|video|mask|path|expression/i;
    const NEG_RE = /negative|负向|负面/i;

    // ---------- 基础工具 ----------

    // 一个节点上"能在界面上调"的输入：标量且不是连线（[节点id, 槽位] 是连线）
    function scalars(node) {
        const out = {};
        const ins = (node && node.inputs) || {};
        for (const k of Object.keys(ins)) {
            const v = ins[k];
            if (Array.isArray(v)) continue;              // 连线（也可能是 __value__ 数组，一并跳过）
            if (v === null || typeof v === 'object') continue;  // 对象型开关（如 speak_and_recognation）
            out[k] = v;
        }
        return out;
    }

    // 工作流里所有可当参数的候选（节点 × 输入）
    function candidates(workflow) {
        const out = [];
        for (const id of Object.keys(workflow || {})) {
            const node = workflow[id];
            if (!node || typeof node !== 'object') continue;
            const cls = String(node.class_type || '');
            const title = String((node._meta && node._meta.title) || '');
            const vals = scalars(node);
            for (const input of Object.keys(vals)) {
                out.push({ node: id, input: input, value: vals[input], cls: cls, title: title });
            }
        }
        return out;
    }

    // 候选的显示文字（弹层里的下拉用）
    function candLabel(c) {
        let v = c.value;
        if (typeof v === 'string' && v.length > 28) v = v.slice(0, 28) + '…';
        return '#' + c.node + ' ' + (c.title ? c.title + ' · ' : '') + c.cls + ' → ' + c.input + ' = ' + String(v);
    }

    // 判断工作流是视频还是图片（认不出来按图片算）
    function detectKind(workflow, name) {
        let hasVideo = false;
        for (const id of Object.keys(workflow || {})) {
            const node = workflow[id];
            if (!node || typeof node !== 'object') continue;
            const cls = String(node.class_type || '');
            if (VIDEO_CLASS_RE.test(cls)) hasVideo = true;
            if (IMAGE_CLASS_RE.test(cls)) hasVideo = false;
        }
        if (hasVideo) return 'video';                    // 有出视频的节点就算视频
        if (/视频|video/i.test(String(name || ''))) return 'video';
        return 'image';
    }

    // ---------- 角色识别 ----------
    // 角色 = 「这一项在这类工作流里是干嘛的」。模板字段和已有配置的字段用同一套规则，
    // 这样"已配置过的工作流"能按角色对上，不会被重复加一遍。
    function roleOf(field) {
        if (!field) return 'other';
        const inp = String(field.input || '').toLowerCase();
        const name = String(field.name || '');
        const key = inp + ' ' + name;
        if (field.bind_prompt) return 'prompt';
        if (inp === 'noise_seed' || inp === 'seed') return 'seed';
        if (inp === 'aspect_ratio' || inp === 'ratio') return 'ratio';
        if (inp === 'megapixels') return 'megapixels';
        if (/时长|duration|second/i.test(key) || /^.*(length|frames)$/i.test(inp)) return 'duration';
        if (inp === 'width' || inp === 'custom_width' || inp === '空') return 'width';
        if (inp === 'height' || inp === 'custom_height') return 'height';
        if (inp === 'steps') return 'steps';
        if (field.type === 'textarea' || /prompt|提示词|正向/.test(key)) return 'prompt';
        return 'input:' + inp;      // 认不出角色的，按"输入名"当角色
    }

    const ROLE_TEXT = {
        prompt: '提示词', duration: '时长', ratio: '画面比例', megapixels: '百万像素',
        seed: '随机种子', width: '宽度', height: '高度', steps: '步数'
    };
    function roleText(role) {
        if (ROLE_TEXT[role]) return ROLE_TEXT[role];
        if (role.startsWith('input:')) return '输入 ' + role.slice(6);
        return role;
    }

    // ---------- 各角色的匹配规则 ----------

    function pickPrompt(cands) {
        let best = null;
        for (const c of cands) {
            if (typeof c.value !== 'string') continue;   // 空串也是候选：新工作流的提示词常常是空的
            const inp = c.input.toLowerCase();
            if (!TEXT_INPUT_RE.test(inp)) continue;
            if (SKIP_TEXT_INPUT_RE.test(inp)) continue;
            if (NEG_RE.test(inp)) continue;
            let s = 0;
            if (/prompt|提示词|正向/i.test(c.title)) s += 4;
            if (/CLIPTextEncode|TextEncode|TextBox|PrimitiveString|String$/i.test(c.cls)) s += 3;
            if (/^(prompt|text|value|string)$/.test(inp)) s += 3;
            else s += 1;
            if (/local|segment|neg/i.test(inp)) s -= 2;   // global_prompt 优先于 local_prompts 这种
            if (c.value.length > 30) s += 2;
            else if (c.value.length > 0) s += 1;         // 空串只靠类名/标题/输入名得分
            if (NEG_RE.test(c.title)) s -= 8;
            if (!best || s > best.s || (s === best.s && String(c.value).length > String(best.c.value).length)) {
                best = { s: s, c: c };
            }
        }
        return best && best.s > 0 ? best.c : null;
    }

    function pickExact(cands, names) {
        for (const n of names) {
            const hit = cands.find(c => c.input.toLowerCase() === n);
            if (hit) return hit;
        }
        return null;
    }

    function pickSeed(cands) {
        return pickExact(cands, ['noise_seed', 'seed']);
    }

    function pickDuration(cands) {
        let best = null;
        for (const c of cands) {
            if (typeof c.value !== 'number') continue;
            const inp = c.input.toLowerCase();
            if (/denoise|shift|scale|multiplier/i.test(inp)) continue;
            let s = 0;
            if (/duration|时长|second/i.test(c.title)) s += 4;
            if (inp === 'duration_seconds') s += 4;
            else if (/duration|seconds/i.test(inp)) s += 3;
            else if (/length|frames|num_frames/i.test(inp)) s += 2;
            if (/^Primitive(Float|Int)|^(Float|Int|Integer|Number)$/.test(c.cls)) s += 1;
            if (s > 0 && (!best || s > best.s)) best = { s: s, c: c };
        }
        return best ? best.c : null;
    }

    function matchRole(role, cands) {
        if (role === 'prompt') return pickPrompt(cands);
        if (role === 'seed') return pickSeed(cands);
        if (role === 'ratio') return pickExact(cands, ['aspect_ratio', 'ratio']);
        if (role === 'megapixels') return pickExact(cands, ['megapixels']);
        if (role === 'duration') return pickDuration(cands);
        if (role === 'width') return pickExact(cands, ['width', 'custom_width']);
        if (role === 'height') return pickExact(cands, ['height', 'custom_height']);
        if (role === 'steps') return pickExact(cands, ['steps']);
        if (role.startsWith('input:')) return pickExact(cands, [role.slice(6)]);
        return null;
    }

    // ---------- 默认配置模板 ----------

    async function readTemplate(kind) {
        if (kind === 'video') {
            // 用户要求：视频默认 = MiniMax_H3 现在那份配置（它改了，默认跟着变）
            try {
                const r = await fetch('/api/workflows/MiniMax_H3.json', { cache: 'no-store' });
                if (r.ok) {
                    const d = await r.json();
                    const fs = (d.config && d.config.fields) || [];
                    if (fs.length) return fs.map(f => Object.assign({}, f));
                }
            } catch (e) { /* 读不到就用内置兜底 */ }
            return VIDEO_FALLBACK.map(f => Object.assign({}, f));
        }
        return IMAGE_TEMPLATE.map(f => Object.assign({}, f));
    }

    // ---------- 排布：模板每一项 → 目标工作流上的节点 ----------

    function plan(workflow, config, template) {
        const cands = candidates(workflow);
        const fields = (config && config.fields) || [];
        const usedRoles = {};
        const usedPair = {};
        for (const f of fields) {
            usedRoles[roleOf(f)] = true;
            usedPair[String(f.node) + '|' + String(f.input)] = true;
        }
        const rows = [];
        for (const t of template) {
            const role = roleOf(t);
            const covered = !!usedRoles[role];
            const sameRole = fields.find(f => roleOf(f) === role) || null;
            // 模板这一项的输入名命中了已有字段的同一个输入 → 也算已配置
            const pairHit = fields.find(f => String(f.input) === String(t.input)) || null;
            let cand = null;
            if (!covered) cand = matchRole(role, cands);
            if (cand && usedPair[String(cand.node) + '|' + String(cand.input)]) cand = null;
            const existing = covered ? sameRole : null;
            const row = {
                role: role,
                roleText: roleText(role),
                template: t,
                covered: covered,
                existing: existing ? { id: existing.id, node: existing.node, input: existing.input, name: existing.name } : null,
                existingField: existing || null,      // 真字段对象（导入时按模板就地刷新它）
                pairHit: (!covered && pairHit) ? { node: pairHit.node, input: pairHit.input, name: pairHit.name } : null,
                candidate: cand,
                checked: !covered && !!cand
            };
            // 时长这类：匹配到"帧数"的输入时，名字和步长要跟着改
            if (cand && role === 'duration' && /frame/i.test(cand.input + ' ' + cand.title)) {
                row.nameOverride = '时长（帧）';
                row.stepOverride = 1;
                row.minOverride = 1;
            }
            // 提示词角色：已有字段没绑提示词框 → 给一条"补绑定"
            if (role === 'prompt' && existing && !existing.bind_prompt) {
                row.bindFix = { id: existing.id, name: existing.name, node: existing.node, input: existing.input };
            }
            rows.push(row);
        }
        return { rows: rows, cands: cands, fields: fields };
    }

    // 把模板项 + 选中的节点 → 一条真字段（default 用节点当下的值，不用模板里的）
    function buildField(templateField, node, input, workflow) {
        const nodeData = (workflow || {})[node] || {};
        const raw = (nodeData.inputs || {})[input];
        const usable = (raw !== undefined && raw !== null && typeof raw !== 'object');
        const f = {
            id: 'f_' + Math.random().toString(36).slice(2, 9),
            node: String(node),
            input: String(input),
            name: templateField.name || input,
            type: templateField.type || 'text',
            default: usable ? raw : (templateField.default === undefined ? null : templateField.default),
            min: templateField.min === undefined ? null : templateField.min,
            max: templateField.max === undefined ? null : templateField.max,
            step: templateField.step === undefined ? null : templateField.step,
            options: Array.isArray(templateField.options) ? templateField.options.slice() : []
        };
        if (templateField.random_enabled) f.random_enabled = true;
        if (templateField.bind_prompt) f.bind_prompt = true;
        if (f.type === 'dropdown') {
            // 不同版本的 ResolutionSelector 选项词汇不一样，模板里的选项不一定对得上
            // → 至少把节点当前值补进选项，保证它是合法取值（要全套选项去「工作流设置」改）
            const cur = typeof raw === 'string' ? raw : null;
            if (cur && !f.options.includes(cur)) f.options.unshift(cur);
        }
        if (typeof f.default === 'number' && typeof f.min === 'number' && f.default < f.min) f.min = null;
        if (typeof f.default === 'number' && typeof f.max === 'number' && f.default > f.max) f.max = null;
        return f;
    }

    // ---------- 「工作流设置」页钩子 ----------
    // 用户要的流程：在那一页的「画布节点预览」卡里**先选「视频 / 图片」**，再导入对应默认配置
    // （自动匹配节点、直接变成可填的控件），没匹配到的**去右边的画布节点图上点输入补**。
    //
    // ⚠ 为什么是"把本文件再塞进 iframe"而不是从父页面直接改：
    //   那页的状态（currentConfig / previewValues / selectedName / currentWorkflow）都是 `let`，
    //   跨 iframe **读不到也写不了**（let 不挂 window）；只有 `function` 声明（selectWorkflow /
    //   renderEditor / renderPreview / loadList / setStatus）能从父页面调用。
    //   所以父页面只做一件事：往 iframe 的 document 里 append 一个 <script src=本文件>，
    //   让钩子跑在 iframe 自己的作用域里（那时上面那些变量就能直接用了）。
    const HELPER_BASE = 'http://127.0.0.1:8317';

    function pageKind() {
        if (document.getElementById('frame-comfyui-settings')) return 'parent';        // 应用外壳 index.html
        if (document.getElementById('previewCard') && document.getElementById('graphCard')) return 'settings';
        return 'other';                                                               // 视频生成页等
    }

    // 父页面：轮询那个 iframe，把本文件塞进去（幂等）
    function bootstrapSettingsFrame() {
        const timer = setInterval(() => {
            const f = document.getElementById('frame-comfyui-settings');
            let doc = null;
            try { doc = f && f.contentDocument; } catch (e) { doc = null; }
            if (!doc || !doc.getElementById('previewCard')) return;      // 还没加载就先等
            if (doc.getElementById('wfa-settings-script')) { clearInterval(timer); return; }
            const s = doc.createElement('script');
            s.id = 'wfa-settings-script';
            s.src = '/static/workflow-autofields.js';
            (doc.head || doc.documentElement).appendChild(s);
            clearInterval(timer);
        }, 700);
    }

    // 在「工作流设置」页里：插一块「默认配置」UI + 包一层 selectWorkflow
    function startSettingsHook() {
        if (window.__wfaSettingsHooked) return;
        let tries = 0;
        const timer = setInterval(() => {
            tries++;
            const card = document.getElementById('previewCard');
            if (typeof window.selectWorkflow !== 'function' || !card) {
                if (tries > 120) clearInterval(timer);     // 等一分钟还没好就放弃
                return;
            }
            clearInterval(timer);
            window.__wfaSettingsHooked = true;
            injectSettingsBar(card);
            const orig = window.selectWorkflow;
            window.selectWorkflow = async function () {
                const r = await orig.apply(this, arguments);
                try { refreshSettingsBar(); } catch (e) { }
                return r;
            };
            // 这页自己的「保存」按钮走后端 PUT —— 那条路会把 bind_prompt 这类未声明的键丢掉，
            // 所以也接一层：优先让助手直写文件（键全保留），校验不过/助手不在就退回原版保存。
            const origSave = window.onSave;
            if (typeof origSave === 'function') {
                window.onSave = async function () {
                    const fields = (currentConfig && currentConfig.fields) || [];
                    const bad = fields.some(f => !f.name || !String(f.name).trim());
                    if (bad || !selectedName || !currentConfig) return origSave.apply(this, arguments);
                    try {
                        const r = await fetch(HELPER_BASE + '/workflow-config', {
                            method: 'POST', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ name: selectedName, config: JSON.parse(JSON.stringify(currentConfig)) })
                        });
                        const res = await r.json();
                        if (res && res.ok) {
                            try { setStatus(typeof tr === 'function' ? tr('comfy.saved') : '已保存'); } catch (e) { }
                            try { await loadList(); } catch (e) { }
                            try { new BroadcastChannel('studio-api').postMessage({ type: 'workflows-changed' }); } catch (e) { }
                            return;
                        }
                    } catch (e) { }
                    return origSave.apply(this, arguments);
                };
            }
            refreshSettingsBar();
        }, 500);
    }

    function injectSettingsBar(card) {
        const style = document.createElement('style');
        // ⚠ 颜色一律走这页的主题变量（--panel/--soft/--text/--muted/--faint/--accent/--line[-strong]），
        //   别写死。这页暗色下 --accent 是**近白色**（#f5f6f8），写死 #fff 会变成白底白字（2026-09-25 用户报过）。
        //   文字一律 inline-flex + justify/align center，别再拿「隐形占位符」顶位置（会把字挤得看着不居中）。
        style.textContent = [
            '.wfa-bar{margin:10px 0 12px;padding:10px;border:1px solid var(--line,#e8ecf2);border-radius:12px;background:var(--soft,#f1f4f8)}',
            '.wfa-bar-head{font-size:11px;font-weight:800;color:var(--muted,#64748b);margin-bottom:7px}',
            '.wfa-bar-row{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:8px}',
            /* 「视频/图片」两个选项：照页面「运行测试」(.run-btn) 的调子做次级款 —— 未选=描边，选中=accent 填充 */
            '.wfa-kind{font:inherit;font-size:11.5px;font-weight:800;height:30px;padding:0 12px;border-radius:10px;',
            'display:inline-flex;align-items:center;justify-content:center;text-align:center;line-height:1;cursor:pointer;',
            'border:1px solid var(--line-strong,#dbe1ea);background:var(--panel,#fff);color:var(--muted,#64748b);transition:all .15s ease}',
            '.wfa-kind:hover{border-color:var(--text,#0f172a);color:var(--text,#0f172a)}',
            '.wfa-kind.on{background:var(--accent,#0f172a);color:var(--bg,#fff);border-color:var(--accent,#0f172a)}',
            /* 「导入默认配置」直接用这页 .run-btn 那套样式（用户要求参考下面的「运行测试」按钮） */
            '#wfaBar .run-btn{margin-top:2px}',
            '.wfa-detect{font-size:11px;color:var(--faint,#94a3b8);font-weight:700;margin-left:auto}',
            '.wfa-status{font-size:11.5px;color:var(--muted,#64748b);line-height:1.6;margin-top:8px}',
            '.wfa-status b{color:var(--text,#0f172a)}',
            '.wfa-status .warn{color:#b45309;font-weight:800}',
            'html.studio-theme-dark .wfa-status .warn,body.studio-theme-dark .wfa-status .warn{color:#fbbf24}'
        ].join('');
        (document.head || document.documentElement).appendChild(style);

        const box = document.createElement('div');
        box.className = 'wfa-bar';
        box.id = 'wfaBar';
        const head = document.createElement('div');
        head.className = 'wfa-bar-head';
        head.textContent = '默认配置';
        box.appendChild(head);

        const row = document.createElement('div');
        row.className = 'wfa-bar-row';
        const btnVideo = document.createElement('button');
        btnVideo.type = 'button';
        btnVideo.className = 'wfa-kind';
        btnVideo.dataset.kind = 'video';
        btnVideo.textContent = '视频工作流';
        const btnImage = document.createElement('button');
        btnImage.type = 'button';
        btnImage.className = 'wfa-kind';
        btnImage.dataset.kind = 'image';
        btnImage.textContent = '图片工作流';
        const detect = document.createElement('span');
        detect.className = 'wfa-detect';
        row.appendChild(btnVideo);
        row.appendChild(btnImage);
        row.appendChild(detect);
        box.appendChild(row);

        const goRow = document.createElement('div');
        goRow.className = 'wfa-bar-row';
        goRow.style.marginBottom = '0';
        const go = document.createElement('button');
        go.type = 'button';
        go.className = 'run-btn wfa-go';       // ← 这页「运行测试」用的就是 .run-btn
        go.innerHTML = '<i data-lucide="sparkles" class="w-4 h-4"></i><span>导入默认配置</span>';
        goRow.appendChild(go);
        box.appendChild(goRow);

        const status = document.createElement('div');
        status.className = 'wfa-status';
        status.id = 'wfaStatus';
        box.appendChild(status);

        const pick = (kind) => {
            box.dataset.kind = kind;
            btnVideo.classList.toggle('on', kind === 'video');
            btnImage.classList.toggle('on', kind === 'image');
        };
        btnVideo.onclick = () => pick('video');
        btnImage.onclick = () => pick('image');
        go.onclick = () => importDefaults(box.dataset.kind || 'video');

        const desc = card.querySelector('.preview-desc');
        if (desc && desc.nextSibling) card.insertBefore(box, desc.nextSibling);
        else card.appendChild(box);
        try { if (typeof refreshIcons === 'function') refreshIcons(); } catch (e) { }   // 把 lucide 图标画出来
        pick('video');
    }

    // 选到工作流之后刷新一下自动判断 + 提示（不导入，等用户点）
    function refreshSettingsBar() {
        const box = document.getElementById('wfaBar');
        if (!box) return;
        const detect = box.querySelector('.wfa-detect');
        let kind = 'video';
        try { kind = detectKind(currentWorkflow, selectedName); } catch (e) { }
        const n = (currentConfig && currentConfig.fields ? currentConfig.fields.length : 0);
        const chosen = box.dataset.kind;
        detect.textContent = '自动判断：' + (kind === 'video' ? '视频' : '图片');
        if (!box.classList.contains('wfa-picked')) {
            box.dataset.kind = kind;
            box.querySelectorAll('.wfa-kind').forEach(b => b.classList.toggle('on', b.dataset.kind === kind));
        }
        const st = document.getElementById('wfaStatus');
        st.innerHTML = (n
            ? '这个工作流已有 <b>' + n + '</b> 项字段：导入默认配置会补齐缺的，并按模板刷新已有的（范围/类型/选项，不动绑定和名字）'
            : '这个工作流还没有字段：先选「视频 / 图片」，再点「导入默认配置」')
            + '；没匹配到的去右边画布上点节点输入补。';
    }

    // 真正导入：算默认配置 → 写进 currentConfig.fields → 重画预览/画布 → 落盘
    async function importDefaults(kind) {
        const st = document.getElementById('wfaStatus');
        const box = document.getElementById('wfaBar');
        if (box) box.classList.add('wfa-picked');
        try {
            if (!currentWorkflow || !selectedName || !currentConfig) {
                st.textContent = '先在左边选一个工作流。';
                return;
            }
            st.textContent = '正在匹配节点…';
            const template = await readTemplate(kind);
            const planned = plan(currentWorkflow, currentConfig, template);   // ⚠ 别写成 const plan = plan(...)：同名遮蔽 + 自引用 = TDZ 炸
            const fields = (currentConfig.fields || []).slice();
            let added = 0, bound = 0, updated = 0;
            const unmatched = [];
            for (const row of planned.rows) {
                if (row.bindFix) {
                    const f = fields.find(x => x.id === row.bindFix.id);
                    if (f && !f.bind_prompt) { f.bind_prompt = true; bound++; }
                }
                // 已经配置过的：**也要按模板刷新**（范围/类型/选项/绑定）—— 用户 2026-09-25 要求
                // 「即使原来就配置好了，也要更新」：模板改了（比如时长改成 1~15）也点一下就同步。
                // 只动这些"定义"，**不动** node/input 绑定、字段名，也不动 default（那条以节点当前值为准）。
                if (row.covered) {
                    const f = row.existingField;
                    const t = row.template;
                    if (f && t) {
                        let touched = false;
                        for (const k of ['type', 'min', 'max', 'step']) {
                            const tv = t[k];
                            if (tv === undefined || tv === null) continue;      // 模板没约束就保持原样
                            if (JSON.stringify(f[k]) !== JSON.stringify(tv)) { f[k] = tv; touched = true; }
                        }
                        if (Array.isArray(t.options)) {
                            const arr = t.options.slice();
                            const cur = f.default;
                            if (cur !== undefined && cur !== null && cur !== ''
                                && !arr.some(o => String(o) === String(cur))) arr.unshift(cur);
                            if (JSON.stringify(arr) !== JSON.stringify(f.options || [])) { f.options = arr; touched = true; }
                        }
                        if (t.random_enabled && !f.random_enabled) { f.random_enabled = true; touched = true; }
                        if (t.bind_prompt && !f.bind_prompt) { f.bind_prompt = true; touched = true; }
                        // 模板的 min/max 别把字段当前值夹没了
                        if (typeof f.default === 'number') {
                            if (typeof f.min === 'number' && f.default < f.min) { f.min = null; touched = true; }
                            if (typeof f.max === 'number' && f.default > f.max) { f.max = null; touched = true; }
                        }
                        if (touched) updated++;
                    }
                    continue;
                }
                if (row.pairHit) continue;
                if (!row.candidate) { unmatched.push(row.roleText); continue; }
                if (fields.some(f => String(f.node) === String(row.candidate.node)
                        && String(f.input) === row.candidate.input)) continue;
                const t = Object.assign({}, row.template);
                if (row.nameOverride) t.name = row.nameOverride;
                if (row.stepOverride !== undefined) t.step = row.stepOverride;
                if (row.minOverride !== undefined) t.min = row.minOverride;
                const f = buildField(t, row.candidate.node, row.candidate.input, currentWorkflow);
                fields.push(f);
                try { if (f.default !== undefined && f.default !== null) previewValues[f.id] = f.default; } catch (e) { }
                added++;
            }
            currentConfig.fields = fields;
            try { renderEditor(); } catch (e) { }
            try { renderPreview(); } catch (e) { }
            // 落盘：走助手（它直接写 .config.json，保得住 bind_prompt；后端 PUT 会丢）
            let saved = false, msg = '';
            try {
                const r = await fetch(HELPER_BASE + '/workflow-config', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: selectedName, config: JSON.parse(JSON.stringify(currentConfig)) })
                });
                const res = await r.json();
                saved = !!(res && res.ok);
                if (!saved) msg = (res && res.message) || ('HTTP ' + r.status);
            } catch (e) { msg = e.message || String(e); }
            const parts = [];
            if (added || updated) {
                parts.push('已导入 ' + (added ? ('<b>' + added + '</b> 项') : '0 项')
                    + (updated ? ('、按模板更新了 <b>' + updated + '</b> 项') : ''));
            } else {
                parts.push('没有要补的项（默认项都已经有了）');
            }
            if (bound) parts.push('提示词已接到提示词框');
            if (unmatched.length) parts.push('<span class="warn">没匹配到：' + unmatched.join('、') + ' → 去右边画布上点节点输入补</span>');
            parts.push(saved ? '已保存' : ('<span class="warn">保存失败：' + msg + '</span>'));
            st.innerHTML = parts.join('；');
            try { if (typeof loadList === 'function') loadList(); } catch (e) { }   // 左边列表的「N 个字段」跟着更新
        } catch (e) {
            st.textContent = '导入失败：' + (e.message || e);
        }
    }

    window.WorkflowAutoFields = {
        VIDEO_FALLBACK: VIDEO_FALLBACK,
        IMAGE_TEMPLATE: IMAGE_TEMPLATE,
        readTemplate: readTemplate,
        detectKind: detectKind,
        roleOf: roleOf,
        roleText: roleText,
        candidates: candidates,
        candLabel: candLabel,
        plan: plan,
        buildField: buildField,
        matchRole: matchRole
    };

    // ---------- 自激活 ----------
    // 父页面（index.html）→ 只负责把本文件再塞进「工作流设置」那个 iframe；
    // 「工作流设置」页里 → 插「默认配置」块 + 包一层 selectWorkflow；
    // 视频生成页 → 什么都不做（它自己按需用 WorkflowAutoFields）。
    (function boot() {
        const start = () => {
            const kind = pageKind();
            if (kind === 'parent') bootstrapSettingsFrame();
            else if (kind === 'settings') startSettingsHook();
        };
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
        else start();
    })();
})();
