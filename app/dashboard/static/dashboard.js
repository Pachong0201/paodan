(function () {
  // Import form: give immediate feedback for large ZIP uploads.
  const importForm = document.querySelector('.import-form');
  if (importForm) {
    importForm.addEventListener('submit', function (e) {
      const input = importForm.querySelector('input[type="file"]');
      const status = document.getElementById('import-status');
      const button = importForm.querySelector('button[type="submit"]');
      if (input && (!input.files || input.files.length === 0)) {
        e.preventDefault();
        if (status) {
          status.textContent = '请先选择至少一个 .eml 或 .zip 文件。';
          status.style.color = '#b3261e';
        }
        return;
      }
      const count = input && input.files ? input.files.length : 0;
      let totalBytes = 0;
      if (input && input.files) {
        for (const f of input.files) totalBytes += f.size || 0;
      }
      const mb = (totalBytes / (1024 * 1024)).toFixed(1);
      if (status) {
        status.textContent = `正在上传并导入 ${count} 个文件（约 ${mb} MB），大 ZIP 需要一些时间，请勿关闭页面…`;
        status.style.color = '#14538c';
      }
      if (button) {
        button.disabled = true;
        button.textContent = '导入中，请稍候…';
      }
    });
  }

  // Dashboard review form
  const form = document.getElementById('review-form');
  if (form) {
    form.addEventListener('submit', async function (e) {
      e.preventDefault();
      const emailId = form.dataset.emailId;
      const fd = new FormData(form);
      const msg = document.getElementById('review-saved');
      try {
        const res = await fetch('/emails/' + encodeURIComponent(emailId) + '/review', { method: 'POST', body: fd });
        msg.textContent = res.ok ? '已保存' : '保存失败';
        msg.style.color = res.ok ? '#047857' : '#b3261e';
      } catch (err) {
        msg.textContent = '保存失败';
        msg.style.color = '#b3261e';
      }
    });
  }

  // Reader review form
  const rform = document.getElementById('reader-review-form');
  if (rform) {
    rform.addEventListener('submit', async function (e) {
      e.preventDefault();
      const emailId = rform.dataset.emailId;
      const fd = new FormData(rform);
      const msg = document.getElementById('reader-review-saved');
      try {
        const res = await fetch('/emails/' + encodeURIComponent(emailId) + '/review', { method: 'POST', body: fd });
        msg.textContent = res.ok ? '已保存' : '保存失败';
        msg.style.color = res.ok ? '#047857' : '#b3261e';
      } catch (err) {
        msg.textContent = '保存失败';
        msg.style.color = '#b3261e';
      }
    });
  }

  // Source reveal: fetch only on explicit click; never embedded in initial HTML
  const reveal = document.getElementById('reveal-source');
  if (reveal) {
    reveal.addEventListener('click', async function () {
      const emailId = reveal.dataset.emailId;
      const box = document.getElementById('source-box');
      try {
        const res = await fetch('/emails/' + encodeURIComponent(emailId) + '/reader/source');
        if (!res.ok) { box.textContent = '无来源信息'; return; }
        const data = await res.json();
        const pre = document.createElement('pre');
        pre.className = 'raw-body';
        pre.textContent = data.sender || '无来源信息';
        box.replaceChildren(pre);
      } catch (err) {
        box.textContent = '无来源信息';
      }
    });
  }

  // Job progress polling
  const progress = document.querySelector('.job-progress');
  if (progress && progress.dataset.jobId) {
    const jobId = progress.dataset.jobId;
    const timer = setInterval(async function () {
      try {
        const res = await fetch('/jobs/' + encodeURIComponent(jobId) + '/status');
        if (!res.ok) return;
        const data = await res.json();
        const set = (sel, val) => { const el = progress.querySelector(sel); if (el) el.textContent = val; };
        set('[data-field="processed"]', data.processed_count);
        set('[data-field="total"]', data.total_count);
        set('[data-field="s"]', data.s_count);
        set('[data-field="a"]', data.a_count);
        set('[data-field="b"]', data.b_count);
        set('[data-field="failed"]', data.failed_count);
        set('[data-field="filename"]', data.current_filename || '');
        const total = Number(data.total_count || 0), done = Number(data.processed_count || 0);
        const pct = total ? Math.round(done / total * 100) : 0;
        const bar = progress.querySelector('[data-field="bar"]');
        if (bar) bar.style.width = pct + '%';
        if (['COMPLETED','FAILED','CANCELLED','INTERRUPTED'].includes(data.status)) {
          clearInterval(timer);
          window.location.reload();
        }
      } catch (err) {}
    }, 1500);
  }
})();
