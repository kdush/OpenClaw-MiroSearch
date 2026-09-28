(() => {
        // gradio_run / reconnect_or_init 通过隐藏 textbox#gr-task-id-bridge 写入当前 task_id；
        // 我们监听其变化，把 task_id 同步到 URL，避免刷新丢失。
        const observe = () => {
            const wrapper = document.querySelector('#gr-task-id-bridge');
            if (!wrapper) { return false; }
            const input = wrapper.querySelector('textarea, input');
            if (!input) { return false; }
            const initialUrlTaskId = new URL(window.location.href).searchParams.get('task_id') || '';
            if (!input.value && initialUrlTaskId) {
                input.value = initialUrlTaskId;
            }
            const sync = () => {
                const value = (input.value || '').trim();
                const url = new URL(window.location.href);
                const current = url.searchParams.get('task_id') || '';
                if (value && value !== current) {
                    url.searchParams.set('task_id', value);
                    window.history.replaceState(null, '', url.toString());
                } else if (!value && current) {
                    url.searchParams.delete('task_id');
                    window.history.replaceState(null, '', url.toString());
                }
            };
            input.addEventListener('input', sync);
            input.addEventListener('change', sync);
            // 兼容 Gradio 内部 set value 但不触发 input 事件的情况
            const observer = new MutationObserver(sync);
            observer.observe(input, { attributes: true, attributeFilter: ['value'] });
            // 初次轮询
            let last = input.value;
            window.setInterval(() => {
                if (input.value !== last) {
                    last = input.value;
                    sync();
                }
            }, 500);
            sync();
            return true;
        };
        const start = () => {
            if (observe()) { return; }
            const t = window.setInterval(() => {
                if (observe()) { window.clearInterval(t); }
            }, 300);
        };
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', start, { once: true });
        } else {
            start();
        }
    })();
