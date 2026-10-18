(function() {
    'use strict';

    const API_BASE = '/api';

    const state = {
        trademarks: [],
        selectedIds: new Set(),
        editingId: null,
        currentPage: 'input'
    };

    const elements = {
        navItems: document.querySelectorAll('.nav-item'),
        pages: document.querySelectorAll('.page'),
        pageTitle: document.getElementById('page-title'),
        totalCount: document.getElementById('total-count'),
        brandNameInput: document.getElementById('brandName'),
        brandNameCount: document.getElementById('brandName-count'),
        brandNameError: document.getElementById('brandName-error'),
        applicantInput: document.getElementById('applicant'),
        registrationNumberInput: document.getElementById('registrationNumber'),
        niceClassInput: document.getElementById('niceClass'),
        notesInput: document.getElementById('notes'),
        trademarkForm: document.getElementById('trademark-form'),
        btnSubmit: document.getElementById('btn-submit'),
        btnText: document.getElementById('btn-text'),
        btnCancel: document.getElementById('btn-cancel'),
        importFile: document.getElementById('import-file'),
        btnImport: document.getElementById('btn-import'),
        importStatus: document.getElementById('import-status'),
        btnExportJson: document.getElementById('btn-export-json'),
        btnExportCsv: document.getElementById('btn-export-csv'),
        searchInput: document.getElementById('search-input'),
        selectAll: document.getElementById('select-all'),
        btnBatchDelete: document.getElementById('btn-batch-delete'),
        tableBody: document.getElementById('trademark-table-body'),
        emptyState: document.getElementById('empty-state'),
        btnStartCheck: document.getElementById('btn-start-check'),
        checkStatusDisplay: document.getElementById('check-status-display'),
        checkStatusText: document.getElementById('check-status-text'),
        checkResultCard: document.getElementById('check-result-card'),
        summaryCards: document.getElementById('summary-cards'),
        alertsList: document.getElementById('alerts-list'),
        reportsList: document.getElementById('reports-list'),
        reportDetailCard: document.getElementById('report-detail-card'),
        reportDetailTitle: document.getElementById('report-detail-title'),
        reportDetail: document.getElementById('report-detail'),
        btnCloseReport: document.getElementById('btn-close-report'),
        checkStatusCheckbox: document.getElementById('check-status'),
        checkSquattingCheckbox: document.getElementById('check-squatting'),
        toastContainer: document.getElementById('toast-container'),
        modalOverlay: document.getElementById('modal-overlay'),
        modalTitle: document.getElementById('modal-title'),
        modalMessage: document.getElementById('modal-message'),
        modalConfirm: document.getElementById('modal-confirm'),
        modalCancel: document.getElementById('modal-cancel')
    };

    let modalCallback = null;

    function init() {
        bindEvents();
        loadTrademarks();
        loadReports();
    }

    function bindEvents() {
        elements.navItems.forEach(item => {
            item.addEventListener('click', (e) => {
                e.preventDefault();
                switchPage(item.dataset.page);
            });
        });

        elements.brandNameInput.addEventListener('input', handleBrandNameInput);
        elements.trademarkForm.addEventListener('submit', handleFormSubmit);
        elements.btnCancel.addEventListener('click', resetForm);

        elements.btnImport.addEventListener('click', () => {
            elements.importFile.click();
        });
        elements.importFile.addEventListener('change', handleFileImport);

        elements.btnExportJson.addEventListener('click', () => exportData('json'));
        elements.btnExportCsv.addEventListener('click', () => exportData('csv'));

        elements.searchInput.addEventListener('input', renderTrademarks);
        elements.selectAll.addEventListener('change', handleSelectAll);
        elements.btnBatchDelete.addEventListener('click', handleBatchDelete);

        elements.btnStartCheck.addEventListener('click', startCheck);

        elements.btnCloseReport.addEventListener('click', () => {
            elements.reportDetailCard.style.display = 'none';
        });

        elements.modalCancel.addEventListener('click', hideModal);
        elements.modalOverlay.addEventListener('click', (e) => {
            if (e.target === elements.modalOverlay) hideModal();
        });
    }

    function switchPage(page) {
        state.currentPage = page;
        elements.navItems.forEach(item => {
            item.classList.toggle('active', item.dataset.page === page);
        });
        elements.pages.forEach(p => {
            p.classList.toggle('active', p.id === `page-${page}`);
        });

        const titles = {
            input: '商标录入',
            list: '商标清单',
            check: '状态检查',
            reports: '检查报告'
        };
        elements.pageTitle.textContent = titles[page] || page;

        if (page === 'list') renderTrademarks();
        if (page === 'reports') loadReports();
        if (page === 'check') loadCheckStatus();
    }

    async function loadTrademarks() {
        try {
            const response = await fetch(`${API_BASE}/trademarks`);
            const data = await response.json();
            if (data.success) {
                state.trademarks = data.data;
                state.selectedIds.clear();
                updateCount();
                renderTrademarks();
            }
        } catch (error) {
            showToast('加载失败', '无法加载商标数据', 'error');
        }
    }

    function updateCount() {
        elements.totalCount.textContent = state.trademarks.length;
    }

    function handleBrandNameInput(e) {
        const value = e.target.value;
        elements.brandNameCount.textContent = `${value.length}/200`;

        if (value.length > 0 && value.length < 2) {
            showFieldError('商标名称至少2个字符');
        } else if (value.length > 200) {
            showFieldError('商标名称长度不能超过200个字符');
        } else {
            clearFieldError();
        }
    }

    function showFieldError(message) {
        elements.brandNameInput.classList.add('error');
        elements.brandNameError.textContent = message;
    }

    function clearFieldError() {
        elements.brandNameInput.classList.remove('error');
        elements.brandNameError.textContent = '';
    }

    function validateForm(data) {
        if (!data.brandName || !data.brandName.trim()) {
            showFieldError('商标名称不能为空');
            return false;
        }
        if (data.brandName.trim().length < 2) {
            showFieldError('商标名称至少2个字符');
            return false;
        }
        if (data.brandName.trim().length > 200) {
            showFieldError('商标名称长度不能超过200个字符');
            return false;
        }
        return true;
    }

    async function handleFormSubmit(e) {
        e.preventDefault();

        const data = {
            brandName: elements.brandNameInput.value.trim(),
            applicant: elements.applicantInput.value.trim(),
            registrationNumber: elements.registrationNumberInput.value.trim(),
            niceClass: parseNiceClass(elements.niceClassInput.value),
            notes: elements.notesInput.value.trim()
        };

        if (!validateForm(data)) return;

        setSubmitting(true);

        try {
            let response;
            if (state.editingId) {
                response = await fetch(`${API_BASE}/trademarks/${state.editingId}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data)
                });
            } else {
                response = await fetch(`${API_BASE}/trademarks`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data)
                });
            }

            const result = await response.json();

            if (result.success) {
                showToast(
                    state.editingId ? '更新成功' : '添加成功',
                    `商标 "${data.brandName}" 已${state.editingId ? '更新' : '添加'}`,
                    'success'
                );
                resetForm();
                loadTrademarks();
            } else {
                showFieldError(result.error || '操作失败');
                showToast('操作失败', result.error || '未知错误', 'error');
            }
        } catch (error) {
            showToast('请求失败', '请检查网络连接', 'error');
        } finally {
            setSubmitting(false);
        }
    }

    function setSubmitting(submitting) {
        elements.btnSubmit.disabled = submitting;
        elements.btnText.textContent = submitting ? '提交中...' : (state.editingId ? '更新商标' : '添加商标');
        elements.btnSubmit.querySelector('svg').style.display = submitting ? 'none' : '';
    }

    function resetForm() {
        elements.trademarkForm.reset();
        elements.brandNameCount.textContent = '0/200';
        clearFieldError();
        state.editingId = null;
        elements.btnText.textContent = '添加商标';
    }

    function parseNiceClass(value) {
        if (!value) return [];
        return value.split(/[,，]/).map(s => s.trim()).filter(Boolean);
    }

    async function handleFileImport(e) {
        const file = e.target.files[0];
        if (!file) return;

        elements.importStatus.textContent = '上传中...';

        const formData = new FormData();
        formData.append('file', file);

        try {
            const response = await fetch(`${API_BASE}/import`, {
                method: 'POST',
                body: formData
            });
            const result = await response.json();

            if (result.success) {
                elements.importStatus.textContent = `成功导入 ${result.importedCount} 个商标`;
                showToast('导入成功', `已导入 ${result.importedCount} 个商标`, 'success');
                loadTrademarks();
            } else {
                elements.importStatus.textContent = result.error || '导入失败';
                showToast('导入失败', result.error || '未知错误', 'error');
            }
        } catch (error) {
            elements.importStatus.textContent = '导入失败';
            showToast('导入失败', '请检查文件格式', 'error');
        }

        e.target.value = '';
        setTimeout(() => {
            elements.importStatus.textContent = '';
        }, 3000);
    }

    function exportData(format) {
        window.location.href = `${API_BASE}/export?format=${format}`;
    }

    function renderTrademarks() {
        const searchTerm = elements.searchInput.value.toLowerCase().trim();
        const filtered = state.trademarks.filter(tm => {
            if (!searchTerm) return true;
            return (
                tm.brandName.toLowerCase().includes(searchTerm) ||
                (tm.applicant && tm.applicant.toLowerCase().includes(searchTerm))
            );
        });

        elements.tableBody.innerHTML = '';

        if (filtered.length === 0) {
            elements.emptyState.style.display = 'block';
            if (searchTerm) {
                elements.emptyState.querySelector('p').textContent = '未找到匹配的商标';
            } else {
                elements.emptyState.querySelector('p').textContent = '暂无商标数据';
            }
            return;
        }

        elements.emptyState.style.display = 'none';

        filtered.forEach(tm => {
            const tr = document.createElement('tr');
            if (state.selectedIds.has(tm.id)) tr.classList.add('selected');

            const checkbox = document.createElement('td');
            const checkboxInput = document.createElement('input');
            checkboxInput.type = 'checkbox';
            checkboxInput.checked = state.selectedIds.has(tm.id);
            checkboxInput.addEventListener('change', () => {
                if (checkboxInput.checked) state.selectedIds.add(tm.id);
                else state.selectedIds.delete(tm.id);
                updateBatchDeleteButton();
            });
            checkbox.appendChild(checkboxInput);
            tr.appendChild(checkbox);

            const brandName = document.createElement('td');
            brandName.className = 'brand-name';
            brandName.textContent = tm.brandName;
            tr.appendChild(brandName);

            const applicant = document.createElement('td');
            applicant.textContent = tm.applicant || '-';
            tr.appendChild(applicant);

            const regNumber = document.createElement('td');
            regNumber.textContent = tm.registrationNumber || '-';
            tr.appendChild(regNumber);

            const niceClass = document.createElement('td');
            niceClass.textContent = (tm.niceClass || []).join(', ') || '-';
            tr.appendChild(niceClass);

            const notes = document.createElement('td');
            notes.textContent = tm.notes || '-';
            tr.appendChild(notes);

            const createdAt = document.createElement('td');
            createdAt.textContent = formatDate(tm.createdAt);
            tr.appendChild(createdAt);

            const actions = document.createElement('td');
            actions.className = 'actions';

            const editBtn = document.createElement('button');
            editBtn.className = 'btn-icon';
            editBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
            editBtn.title = '编辑';
            editBtn.addEventListener('click', () => editTrademark(tm));

            const deleteBtn = document.createElement('button');
            deleteBtn.className = 'btn-icon danger';
            deleteBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
            deleteBtn.title = '删除';
            deleteBtn.addEventListener('click', () => deleteTrademark(tm));

            actions.appendChild(editBtn);
            actions.appendChild(deleteBtn);
            tr.appendChild(actions);

            elements.tableBody.appendChild(tr);
        });
    }

    function formatDate(dateStr) {
        if (!dateStr) return '-';
        try {
            const date = new Date(dateStr);
            return date.toLocaleDateString('zh-CN', {
                year: 'numeric',
                month: '2-digit',
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit'
            });
        } catch {
            return dateStr;
        }
    }

    function editTrademark(tm) {
        state.editingId = tm.id;
        elements.brandNameInput.value = tm.brandName;
        elements.brandNameCount.textContent = `${tm.brandName.length}/200`;
        elements.applicantInput.value = tm.applicant || '';
        elements.registrationNumberInput.value = tm.registrationNumber || '';
        elements.niceClassInput.value = (tm.niceClass || []).join(', ');
        elements.notesInput.value = tm.notes || '';
        clearFieldError();
        switchPage('input');
        elements.btnText.textContent = '更新商标';
    }

    async function deleteTrademark(tm) {
        showModal('确认删除', `确定要删除商标 "${tm.brandName}" 吗？此操作不可撤销。`, async () => {
            try {
                const response = await fetch(`${API_BASE}/trademarks/${tm.id}`, {
                    method: 'DELETE'
                });
                const result = await response.json();
                if (result.success) {
                    showToast('删除成功', `商标 "${tm.brandName}" 已删除`, 'success');
                    state.selectedIds.delete(tm.id);
                    loadTrademarks();
                } else {
                    showToast('删除失败', result.error || '未知错误', 'error');
                }
            } catch (error) {
                showToast('请求失败', '请检查网络连接', 'error');
            }
        });
    }

    function handleSelectAll(e) {
        if (e.target.checked) {
            state.trademarks.forEach(tm => state.selectedIds.add(tm.id));
        } else {
            state.selectedIds.clear();
        }
        renderTrademarks();
        updateBatchDeleteButton();
    }

    function updateBatchDeleteButton() {
        elements.btnBatchDelete.disabled = state.selectedIds.size === 0;
        elements.btnBatchDelete.textContent = `批量删除 (${state.selectedIds.size})`;
    }

    async function handleBatchDelete() {
        if (state.selectedIds.size === 0) return;

        showModal('确认删除', `确定要删除选中的 ${state.selectedIds.size} 个商标吗？此操作不可撤销。`, async () => {
            try {
                const response = await fetch(`${API_BASE}/trademarks/batch`, {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ ids: Array.from(state.selectedIds) })
                });
                const result = await response.json();
                if (result.success) {
                    showToast('删除成功', `已删除 ${result.deletedCount} 个商标`, 'success');
                    state.selectedIds.clear();
                    loadTrademarks();
                    updateBatchDeleteButton();
                } else {
                    showToast('删除失败', result.error || '未知错误', 'error');
                }
            } catch (error) {
                showToast('请求失败', '请检查网络连接', 'error');
            }
        });
    }

    async function startCheck() {
        if (state.trademarks.length === 0) {
            showToast('提示', '请先添加商标', 'warning');
            switchPage('input');
            return;
        }

        const checkStatus = elements.checkStatusCheckbox.checked;
        const checkSquatting = elements.checkSquattingCheckbox.checked;

        elements.checkStatusDisplay.style.display = 'flex';
        elements.checkStatusText.textContent = '检查进行中，请稍候...';
        elements.btnStartCheck.disabled = true;
        elements.btnStartCheck.querySelector('svg').style.display = 'none';
        elements.btnStartCheck.innerHTML = '<span>检查中...</span>';

        try {
            const response = await fetch(`${API_BASE}/check`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ checkStatus, checkSquatting })
            });
            const result = await response.json();

            if (result.success) {
                showToast('检查已启动', '结果将在稍后显示', 'info');
                pollCheckStatus();
            } else {
                showToast('启动失败', result.error || '未知错误', 'error');
                resetCheckButton();
            }
        } catch (error) {
            showToast('请求失败', '请检查网络连接', 'error');
            resetCheckButton();
        }
    }

    function resetCheckButton() {
        elements.checkStatusDisplay.style.display = 'none';
        elements.btnStartCheck.disabled = false;
        elements.btnStartCheck.innerHTML = '<svg viewBox="0 0 24 24" fill="none"><polygon points="5,3 19,12 5,21" fill="currentColor"/></svg><span>开始检查</span>';
    }

    function pollCheckStatus() {
        const pollInterval = setInterval(async () => {
            try {
                const response = await fetch(`${API_BASE}/check/status`);
                const data = await response.json();

                if (data.status === 'completed') {
                    clearInterval(pollInterval);
                    displayCheckResult(data.result);
                    resetCheckButton();
                } else if (data.status === 'error') {
                    clearInterval(pollInterval);
                    showToast('检查失败', data.error || '未知错误', 'error');
                    resetCheckButton();
                }
            } catch (error) {
                clearInterval(pollInterval);
                showToast('请求失败', '请检查网络连接', 'error');
                resetCheckButton();
            }
        }, 2000);
    }

    function loadCheckStatus() {
        pollCheckStatus();
    }

    // 兼容 errors 的两种形态：旧的数字形态与新的「错误详情列表」形态。
    // 直接写 `result.errors || 0` 时，空数组 [] 是真值，会被渲染成空白而不是 0。
    function errorCount(value) {
        if (Array.isArray(value)) {
            return value.length;
        }
        return value || 0;
    }

    function displayCheckResult(result) {
        elements.checkStatusDisplay.style.display = 'none';
        elements.checkResultCard.style.display = 'block';

        const summary = {
            total: result.total || 0,
            alerts: result.alerts ? result.alerts.length : 0,
            errors: errorCount(result.errors),
            issues: result.alerts ? result.alerts.filter(a => a.issues && a.issues.length > 0).length : 0
        };

        elements.summaryCards.innerHTML = `
            <div class="summary-card success">
                <div class="label">检查总数</div>
                <div class="value">${summary.total}</div>
            </div>
            <div class="summary-card warning">
                <div class="label">告警数</div>
                <div class="value">${summary.alerts}</div>
            </div>
            <div class="summary-card danger">
                <div class="label">异常数</div>
                <div class="value">${summary.issues}</div>
            </div>
            <div class="summary-card">
                <div class="label">错误数</div>
                <div class="value">${summary.errors}</div>
            </div>
        `;

        if (result.alerts && result.alerts.length > 0) {
            elements.alertsList.innerHTML = result.alerts.map(alert => renderAlert(alert)).join('');
        } else {
            elements.alertsList.innerHTML = `
                <div class="empty-state">
                    <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                        <path d="M22 11.08V12a10 10 0 11-5.93-9.14" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                        <polyline points="22 4 12 14.01 9 11.01" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                    <p>检查完成，无告警</p>
                </div>
            `;
        }
    }

    function renderAlert(alert) {
        const hasIssue = alert.hasIssue || alert.hasSquatting;
        const alertClass = alert.issues && alert.issues.includes('状态异常') ? 'danger' : 'warning';

        let statusSection = '';
        if (alert.abnormalStatuses && alert.abnormalStatuses.length > 0) {
            statusSection = `
                <div class="alert-section">
                    <div class="alert-section-title">异常状态</div>
                    <div class="alert-cases">
                        ${alert.abnormalStatuses.map(s => `
                            <div class="alert-case high">
                                <div class="alert-case-info">
                                    <div class="alert-case-brand">注册号 ${s.registrationNumber || 'N/A'}</div>
                                    <div class="alert-case-applicant">${s.status}${s.office ? ' (' + s.office + ')' : ''}</div>
                                </div>
                                <span class="risk-badge high">${s.status}</span>
                            </div>
                        `).join('')}
                    </div>
                </div>
            `;
        }

        let squattingSection = '';
        if (alert.squattingCases && alert.squattingCases.length > 0) {
            squattingSection = `
                <div class="alert-section">
                    <div class="alert-section-title">疑似抢注 (${alert.squattingCases.length})</div>
                    <div class="alert-cases">
                        ${alert.squattingCases.slice(0, 5).map(c => `
                            <div class="alert-case ${c.riskLevel}">
                                <div class="alert-case-info">
                                    <div class="alert-case-brand">${c.brandName}</div>
                                    <div class="alert-case-applicant">申请人: ${c.applicant || '未知'}</div>
                                </div>
                                <span class="risk-badge ${c.riskLevel}">${c.riskLevel}</span>
                            </div>
                        `).join('')}
                        ${alert.squattingCases.length > 5 ? `<div style="font-size: 12px; color: var(--text-tertiary); padding: 4px 12px;">还有 ${alert.squattingCases.length - 5} 条...</div>` : ''}
                    </div>
                </div>
            `;
        }

        let reviewSection = '';
        if (alert.needsReviewCases && alert.needsReviewCases.length > 0) {
            reviewSection = `
                <div class="alert-section">
                    <div class="alert-section-title">待审核 (${alert.needsReviewCases.length})</div>
                    <div class="alert-cases">
                        ${alert.needsReviewCases.slice(0, 5).map(c => `
                            <div class="alert-case ${c.riskLevel}">
                                <div class="alert-case-info">
                                    <div class="alert-case-brand">${c.brandName}</div>
                                    <div class="alert-case-applicant">申请人: ${c.applicant || '未知'}</div>
                                    <div class="alert-case-reason">${c.reason || ''}</div>
                                </div>
                                <span class="risk-badge ${c.riskLevel}">${c.riskLevel}</span>
                            </div>
                        `).join('')}
                        ${alert.needsReviewCases.length > 5 ? `<div style="font-size: 12px; color: var(--text-tertiary); padding: 4px 12px;">还有 ${alert.needsReviewCases.length - 5} 条...</div>` : ''}
                    </div>
                </div>
            `;
        }

        return `
            <div class="alert-item ${alertClass}">
                <div class="alert-header">
                    <span class="alert-brand">${alert.brandName}</span>
                    <div class="alert-badges">
                        ${alert.issues.map(i => `<span class="badge ${i === '状态异常' ? 'danger' : 'warning'}">${i}</span>`).join('')}
                    </div>
                </div>
                ${statusSection}
                ${squattingSection}
                ${reviewSection}
            </div>
        `;
    }

    async function loadReports() {
        try {
            const response = await fetch(`${API_BASE}/reports`);
            const data = await response.json();

            if (data.success) {
                elements.reportsList.innerHTML = '';

                if (data.data.length === 0) {
                    elements.reportsList.innerHTML = `
                        <div class="empty-state">
                            <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                                <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                                <path d="M14 2v6h6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                            </svg>
                            <p>暂无报告</p>
                            <span>执行一次检查后将生成报告</span>
                        </div>
                    `;
                    return;
                }

                data.data.forEach(report => {
                    const item = document.createElement('div');
                    item.className = 'report-item';
                    item.innerHTML = `
                        <div class="report-info">
                            <div class="report-icon">
                                <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                                    <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                                    <path d="M14 2v6h6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                                </svg>
                            </div>
                            <div class="report-details">
                                <div class="report-filename">${report.filename}</div>
                                <div class="report-meta">
                                    ${formatDate(report.timestamp)} | 
                                    共 ${report.total} 个商标 | 
                                    <span style="color: ${report.alertsCount > 0 ? 'var(--warning-color)' : 'var(--success-color)'}">
                                        ${report.alertsCount} 条告警
                                    </span>
                                </div>
                            </div>
                        </div>
                        <div class="report-actions">
                            <button class="btn btn-outline btn-sm">查看详情</button>
                        </div>
                    `;
                    item.addEventListener('click', () => viewReport(report.filename));
                    elements.reportsList.appendChild(item);
                });
            }
        } catch (error) {
            showToast('加载失败', '无法加载报告列表', 'error');
        }
    }

    async function viewReport(filename) {
        try {
            const response = await fetch(`${API_BASE}/reports/${encodeURIComponent(filename)}`);
            const data = await response.json();

            if (data.success) {
                elements.reportDetailTitle.textContent = filename;
                elements.reportDetail.innerHTML = renderReportDetail(data.data);
                elements.reportDetailCard.style.display = 'block';
                elements.reportDetailCard.scrollIntoView({ behavior: 'smooth' });
            }
        } catch (error) {
            showToast('加载失败', '无法加载报告详情', 'error');
        }
    }

    function renderReportDetail(data) {
        const alerts = data.alerts || [];
        const results = data.results || [];

        const summary = {
            total: data.total || results.length,
            alerts: alerts.length,
            errors: errorCount(data.errors)
        };

        return `
            <div class="report-summary">
                <div class="report-summary-item">
                    <div class="value">${summary.total}</div>
                    <div class="label">检查总数</div>
                </div>
                <div class="report-summary-item">
                    <div class="value">${summary.alerts}</div>
                    <div class="label">告警数</div>
                </div>
                <div class="report-summary-item">
                    <div class="value">${summary.errors}</div>
                    <div class="label">错误数</div>
                </div>
                <div class="report-summary-item">
                    <div class="value">${formatDate(data.timestamp)}</div>
                    <div class="label">检查时间</div>
                </div>
            </div>
            <h3 style="margin: 24px 0 16px; font-size: 16px;">告警详情</h3>
            <div class="alerts-list">
                ${alerts.length > 0 ? alerts.map(alert => renderAlert(alert)).join('') : '<div class="empty-state" style="padding: 20px;"><p>无告警</p></div>'}
            </div>
        `;
    }

    function showModal(title, message, callback) {
        elements.modalTitle.textContent = title;
        elements.modalMessage.textContent = message;
        modalCallback = callback;
        elements.modalOverlay.style.display = 'flex';
    }

    function hideModal() {
        elements.modalOverlay.style.display = 'none';
        modalCallback = null;
    }

    elements.modalConfirm.addEventListener('click', async () => {
        if (modalCallback) {
            await modalCallback();
        }
        hideModal();
    });

    function showToast(title, message, type = 'info') {
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;

        const icons = {
            success: '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" width="20" height="20"><path d="M22 11.08V12a10 10 0 11-5.93-9.14" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><polyline points="22 4 12 14.01 9 11.01" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
            error: '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" width="20" height="20"><circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2"/><line x1="15" y1="9" x2="9" y2="15" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><line x1="9" y1="9" x2="15" y2="15" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
            warning: '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" width="20" height="20"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><line x1="12" y1="9" x2="12" y2="13" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><line x1="12" y1="17" x2="12.01" y2="17" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
            info: '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" width="20" height="20"><circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2"/><line x1="12" y1="16" x2="12" y2="12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><line x1="12" y1="8" x2="12.01" y2="8" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>'
        };

        toast.innerHTML = `
            <div class="toast-icon">${icons[type] || icons.info}</div>
            <div>
                <div style="font-weight: 500; margin-bottom: 2px;">${title}</div>
                <div style="font-size: 13px;">${message}</div>
            </div>
            <button class="toast-close">×</button>
        `;

        elements.toastContainer.appendChild(toast);

        const closeBtn = toast.querySelector('.toast-close');
        closeBtn.addEventListener('click', () => {
            removeToast(toast);
        });

        setTimeout(() => {
            removeToast(toast);
        }, 4000);
    }

    function removeToast(toast) {
        toast.classList.add('hiding');
        setTimeout(() => {
            if (toast.parentNode) {
                toast.parentNode.removeChild(toast);
            }
        }, 300);
    }

    document.addEventListener('DOMContentLoaded', init);
})();