/* Published cards work as native disclosures; feedback adds an optional network action. */
(() => {
  const snapshot = document.querySelector('#publication-status time');
  if (snapshot && Date.now() - Date.parse(snapshot.dateTime) > 24 * 60 * 60 * 1000) {
    const status = snapshot.parentElement;
    status.className = 'monitor-callout';
    status.append(' · This edition is over a day old. Current service status is unknown.');
  }
  const announce = document.getElementById('rating-announcement');
  function closeCard(container) {
    if (container.matches('details')) {
      container.open = false;
      container.querySelector(':scope > summary').focus();
    } else {
      container.querySelector('.document-back').click();
    }
  }
  document.addEventListener('click', async event => {
    const close = event.target.closest('[data-close-card]');
    if (close) {
      closeCard(close.closest('details.curation-card, section.curation-document'));
      return;
    }
    const button = event.target.closest('[data-rating]');
    if (!button) return;
    const group = button.closest('.card-rating');
    if (group.dataset.saving === 'true') return;
    const status = group.querySelector('.rating-status');
    const details = group.closest('details.curation-card, section.curation-document');
    const buttons = details.querySelectorAll('[data-rating], [data-close-card]');
    group.dataset.saving = 'true';
    buttons.forEach(element => { element.disabled = true; });
    status.textContent = 'Saving feedback…';
    announce.textContent = '';
    try {
      const response = await fetch('/live/api/publish/ratings', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({kind: group.dataset.kind, id: group.dataset.id,
          version: group.dataset.version, rating: button.dataset.rating})
      });
      if (!response.ok) throw new Error('Feedback was not saved');
      const result = await response.json();
      if (!['helpful', 'needs_work'].includes(result.rating)) throw new Error('Invalid response');
      document.querySelectorAll('.card-rating').forEach(other => {
        if (other.dataset.kind !== group.dataset.kind || other.dataset.id !== group.dataset.id ||
            other.dataset.version !== group.dataset.version) return;
        other.querySelectorAll('[data-rating]').forEach(element => {
          element.setAttribute('aria-pressed', String(element.dataset.rating === result.rating));
        });
        other.querySelector('.rating-status').textContent = result.rating === 'helpful'
          ? 'Your rating: Helpful.' : 'Your rating: Needs work.';
        const card = other.closest('article');
        const receipt = card?.querySelector(':scope > .rating-receipt');
        if (receipt) {
          receipt.textContent = result.rating === 'helpful' ? 'Feedback saved: Helpful.' : 'Feedback saved: Needs work.';
          receipt.hidden = false;
        }
      });
      closeCard(details);
      announce.textContent = 'Thank you. Your feedback was saved.';
    } catch {
      status.textContent = 'Could not save feedback. Please try again.';
    } finally {
      group.dataset.saving = 'false';
      buttons.forEach(element => { element.disabled = false; });
    }
  });
})();
