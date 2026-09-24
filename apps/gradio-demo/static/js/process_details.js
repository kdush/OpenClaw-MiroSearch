(() => {
        const closeFinishedProcessPanels = (root) => {
            const scope = root && root.querySelectorAll ? root : document;
            scope.querySelectorAll('details.process-details[data-collapsed="1"]').forEach((el) => {
                if (el.dataset.userToggled === '1') { return; }
                if (el.open) { el.open = false; }
                if (el.dataset.boundToggle === '1') { return; }
                el.dataset.boundToggle = '1';
                el.addEventListener('toggle', () => {
                    if (el.open) { el.dataset.userToggled = '1'; }
                });
            });
        };
        // 搜索行的图标按钮只显示图标（文字用 CSS font-size:0 收起），
        // 把按钮自身的文案回填成 title/aria-label，中英文随界面语言自动跟随。
        const bindActionButtonLabels = () => {
            document.querySelectorAll('#btn-row .ui-action-btn').forEach((el) => {
                if (el.dataset.labelBound === '1') { return; }
                const label = (el.textContent || '').trim();
                if (!label) { return; }
                el.dataset.labelBound = '1';
                el.setAttribute('title', label);
                if (!el.getAttribute('aria-label')) { el.setAttribute('aria-label', label); }
            });
        };
        const mo = new MutationObserver((mutations) => {
            for (const m of mutations) {
                if (m.type === 'childList' || m.type === 'attributes') {
                    closeFinishedProcessPanels(document);
                    bindActionButtonLabels();
                    break;
                }
            }
        });
        const start = () => {
            closeFinishedProcessPanels(document);
            bindActionButtonLabels();
            mo.observe(document.body, {
                childList: true,
                subtree: true,
                attributes: true,
                attributeFilter: ['open', 'data-collapsed', 'data-fp'],
            });
        };
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', start, { once: true });
        } else {
            start();
        }
    })();
