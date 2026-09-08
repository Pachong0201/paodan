(function () {
  const form = document.getElementById('review-form');
  if (!form) return;
  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    const emailId = form.dataset.emailId;
    const fd = new FormData(form);
    const msg = document.getElementById('review-saved');
    try {
      const res = await fetch('/emails/' + encodeURIComponent(emailId) + '/review', {
        method: 'POST',
        body: fd,
        headers: { 'X-Requested-With': 'fetch' }
      });
      if (!res.ok) {
        msg.textContent = '保存失敗';
        msg.style.color = '#b3261e';
        return;
      }
      msg.textContent = '已保存';
      msg.style.color = '#047857';
    } catch (err) {
      msg.textContent = '保存失敗';
      msg.style.color = '#b3261e';
    }
  });
})();
