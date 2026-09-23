(() => {
        const SELECTORS = {
            skillsDownloadLink: '#skills-download-link',
        };

        const copyTextToClipboard = async (text) => {
            const normalizedText = String(text || '').trim();
            if (!normalizedText) { return false; }
            if (navigator.clipboard && window.isSecureContext) {
                try {
                    await navigator.clipboard.writeText(normalizedText);
                    return true;
                } catch (e) { void e; }
            }
            const el = document.createElement('textarea');
            el.value = normalizedText;
            el.setAttribute('readonly', '');
            el.style.cssText = 'position:fixed;opacity:0;pointer-events:none';
            document.body.appendChild(el);
            el.focus(); el.select();
            let copied = false;
            try { copied = document.execCommand('copy'); } catch (e) { void e; }
            document.body.removeChild(el);
            return copied;
        };

        const bindSkillsDownloadAction = () => {
            const linkEl = document.querySelector(SELECTORS.skillsDownloadLink);
            if (!linkEl || linkEl.dataset.boundCopyAction === '1') { return; }
            linkEl.dataset.boundCopyAction = '1';
            linkEl.addEventListener('click', () => {
                const rawUrl = linkEl.dataset.copyUrl || linkEl.getAttribute('href') || '';
                let absoluteUrl = '';
                try { absoluteUrl = new URL(rawUrl, window.location.origin).toString(); } catch (e) { return; }
                const copiedText = linkEl.dataset.copiedText || 'Link Copied';
                copyTextToClipboard(absoluteUrl).then((copied) => {
                    if (!copied) { return; }
                    const prevTitle = linkEl.getAttribute('title') || linkEl.dataset.title || '';
                    linkEl.setAttribute('title', copiedText);
                    linkEl.classList.add('is-copied');
                    window.setTimeout(() => {
                        linkEl.setAttribute('title', prevTitle || (linkEl.dataset.title || ''));
                        linkEl.classList.remove('is-copied');
                    }, 1200);
                });
            });
        };

        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', bindSkillsDownloadAction, { once: true });
        } else {
            bindSkillsDownloadAction();
        }
    })();
