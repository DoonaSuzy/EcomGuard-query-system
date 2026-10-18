(function() {
    'use strict';

    const searchInput = document.getElementById('search-input');
    const navMenu = document.getElementById('nav-menu');
    const testerModal = document.getElementById('api-tester-modal');
    const modalClose = document.getElementById('modal-close');
    const btnTestNav = document.getElementById('btn-test-nav');
    const btnSendRequest = document.getElementById('btn-send-request');
    const testerMethod = document.getElementById('tester-method');
    const testerPath = document.getElementById('tester-path');
    const testerBody = document.getElementById('tester-body');
    const bodyRow = document.getElementById('body-row');
    const testerResult = document.getElementById('tester-result');
    const resultStatus = document.getElementById('result-status');
    const resultTime = document.getElementById('result-time');
    const resultContent = document.getElementById('result-content');
    const testerTitle = document.getElementById('tester-title');

    document.querySelectorAll('.copy-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const targetId = btn.dataset.copy;
            const codeBlock = btn.closest('.code-section, .response-example').querySelector('code');
            if (codeBlock) {
                navigator.clipboard.writeText(codeBlock.textContent).then(() => {
                    const originalText = btn.innerHTML;
                    btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M22 11.08V12a10 10 0 11-5.93-9.14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><polyline points="22 4 12 14.01 9 11.01" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>已复制';
                    setTimeout(() => { btn.innerHTML = originalText; }, 2000);
                });
            }
        });
    });

    document.querySelectorAll('.btn-test').forEach(btn => {
        btn.addEventListener('click', () => {
            const method = btn.dataset.method;
            const path = btn.dataset.path;
            const name = btn.dataset.name;
            openTester(method, path, name);
        });
    });

    btnTestNav.addEventListener('click', () => {
        openTester('GET', '/api/trademarks', 'API 测试工具');
    });

    modalClose.addEventListener('click', closeTester);
    testerModal.querySelector('.modal-overlay').addEventListener('click', closeTester);

    function openTester(method, path, name) {
        testerMethod.value = method;
        testerPath.value = path;
        testerTitle.textContent = name || 'API 测试工具';
        testerResult.style.display = 'none';
        updateBodyVisibility();
        testerModal.classList.add('active');
    }

    function closeTester() {
        testerModal.classList.remove('active');
    }

    function updateBodyVisibility() {
        const method = testerMethod.value;
        if (['POST', 'PUT', 'PATCH'].includes(method)) {
            bodyRow.style.display = 'flex';
        } else {
            bodyRow.style.display = 'none';
        }
    }

    testerMethod.addEventListener('change', updateBodyVisibility);

    btnSendRequest.addEventListener('click', async () => {
        const method = testerMethod.value;
        const path = testerPath.value.trim();
        const body = testerBody.value.trim();

        if (!path) {
            alert('请输入 URL 路径');
            return;
        }

        btnSendRequest.disabled = true;
        const originalText = btnSendRequest.innerHTML;
        btnSendRequest.innerHTML = '<svg style="animation: spin 1s linear infinite;" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" opacity="0.3"/><path d="M12 2a10 10 0 0110 10" stroke="currentColor" stroke-width="3" stroke-linecap="round"/></svg>发送中...';

        try {
            const response = await fetch('/api/test', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    method: method,
                    path: path,
                    body: body
                })
            });

            const result = await response.json();
            displayResult(result);
        } catch (error) {
            displayResult({
                success: false,
                error: '请求失败: ' + error.message
            });
        } finally {
            btnSendRequest.disabled = false;
            btnSendRequest.innerHTML = originalText;
        }
    });

    function displayResult(result) {
        testerResult.style.display = 'block';

        if (result.success) {
            resultStatus.textContent = result.statusCode;
            resultStatus.className = 'status-badge ' + (result.statusCode < 300 ? 'success' : result.statusCode < 400 ? 'info' : 'error');
            resultTime.textContent = result.time ? result.time.toFixed(3) + 's' : '--';

            let displayData = result.data;
            if (typeof displayData === 'object') {
                displayData = JSON.stringify(displayData, null, 2);
            }
            resultContent.textContent = displayData;
        } else {
            resultStatus.textContent = 'Error';
            resultStatus.className = 'status-badge error';
            resultTime.textContent = '--';
            resultContent.textContent = JSON.stringify({ error: result.error }, null, 2);
        }

        if (window.Prism) {
            Prism.highlightElement(resultContent);
        }
    }

    searchInput.addEventListener('input', (e) => {
        const query = e.target.value.toLowerCase().trim();
        filterNavigation(query);
    });

    function filterNavigation(query) {
        const navGroups = navMenu.querySelectorAll('.nav-group');
        
        navGroups.forEach(group => {
            const navItems = group.querySelectorAll('.nav-item');
            let hasVisibleItems = false;

            navItems.forEach(item => {
                const searchText = (item.dataset.search || item.textContent).toLowerCase();
                if (!query || searchText.includes(query)) {
                    item.style.display = 'flex';
                    hasVisibleItems = true;
                    
                    if (query) {
                        highlightMatch(item, query);
                    }
                } else {
                    item.style.display = 'none';
                }
            });

            if (query) {
                group.classList.toggle('collapsed', !hasVisibleItems);
                group.classList.toggle('no-results', !hasVisibleItems);
            } else {
                group.classList.remove('collapsed', 'no-results');
                navItems.forEach(item => {
                    item.innerHTML = item.innerHTML.replace(/<mark>/g, '').replace(/<\/mark>/g, '');
                });
            }
        });

        filterEndpoints(query);
    }

    function highlightMatch(item, query) {
        const nameSpan = item.querySelector('.nav-item-name');
        if (nameSpan) {
            const text = nameSpan.textContent;
            const regex = new RegExp(`(${query})`, 'gi');
            nameSpan.innerHTML = text.replace(regex, '<mark>$1</mark>');
        }
    }

    function filterEndpoints(query) {
        const cards = document.querySelectorAll('.endpoint-card');
        cards.forEach(card => {
            const searchText = card.textContent.toLowerCase();
            const match = !query || searchText.includes(query);
            card.style.display = match ? '' : 'none';
        });

        const sections = document.querySelectorAll('.category-section');
        sections.forEach(section => {
            const visibleCards = section.querySelectorAll('.endpoint-card:not([style*="display: none"])');
            const sectionHeader = section.querySelector('.category-header');
            section.style.display = (!query || visibleCards.length > 0) ? '' : 'none';
        });
    }

    document.querySelectorAll('.nav-group-header').forEach(header => {
        header.addEventListener('click', () => {
            const group = header.parentElement;
            group.classList.toggle('collapsed');
        });
    });

    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const targetId = item.getAttribute('href').substring(1);
            const targetElement = document.getElementById(targetId);
            if (targetElement) {
                const navbarHeight = 0;
                const targetPosition = targetElement.getBoundingClientRect().top + window.pageYOffset - navbarHeight - 20;
                window.scrollTo({
                    top: targetPosition,
                    behavior: 'smooth'
                });

                document.querySelectorAll('.nav-item').forEach(i => i.classList.remove('highlight'));
                item.classList.add('highlight');
            }
        });
    });

    window.addEventListener('scroll', () => {
        updateActiveNavOnScroll();
    });

    function updateActiveNavOnScroll() {
        const sections = document.querySelectorAll('.endpoint-card');
        const navItems = document.querySelectorAll('.nav-item');
        
        let current = '';
        sections.forEach(section => {
            const rect = section.getBoundingClientRect();
            if (rect.top <= 100) {
                current = section.id;
            }
        });

        if (current) {
            navItems.forEach(item => {
                item.classList.remove('highlight');
                if (item.getAttribute('href') === '#' + current) {
                    item.classList.add('highlight');
                }
            });
        }
    }

    const style = document.createElement('style');
    style.textContent = '@keyframes spin { to { transform: rotate(360deg); } }';
    document.head.appendChild(style);

    console.log('API Documentation System initialized');
})();