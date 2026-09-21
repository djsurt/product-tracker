/* Small progressive enhancements for the server-rendered shopping flow. */
(function () {
  'use strict';
  var filter = 'all';
  var toastTimer;
  var pendingFocus;

  function notify(message, error) {
    var toast = document.getElementById('toast');
    if (!toast) return;
    clearTimeout(toastTimer);
    toast.textContent = message;
    toast.classList.toggle('is-error', Boolean(error));
    toast.hidden = false;
    toastTimer = setTimeout(function () { toast.hidden = true; }, error ? 8000 : 4500);
  }

  function applyFilter(announce) {
    var items = document.getElementById('items');
    if (!items) return;
    var cards = items.querySelectorAll('[data-item-id]');
    var intro = document.querySelector('.page-intro');
    if (intro) intro.classList.toggle('compact', cards.length > 0);
    var visible = 0;
    items.querySelectorAll('.filter-button').forEach(function (button) {
      button.setAttribute('aria-pressed', String(button.dataset.filter === filter));
    });
    cards.forEach(function (card) {
      var show = filter === 'all' || (filter === 'good' ? card.dataset.good === 'true' : card.dataset.state === filter);
      card.hidden = !show;
      if (show) visible++;
    });
    var empty = document.getElementById('filter-empty');
    if (empty) {
      empty.hidden = visible > 0;
      var copy = {
        good: ['The right price is worth the wait.', 'No good deals just yet. Your watched products will appear here when their price earns a thumbs-up.'],
        paused: ['Everything is on the lookout.', 'You haven’t paused any products. You can take a break from tracking in any product’s menu.'],
        watching: ['Taking a little breather.', 'Resume a paused product to start watching its price again.'],
        all: ['Nothing here just yet.', 'Add a product to start your wishlist.']
      };
      document.getElementById('filter-empty-title').textContent = copy[filter][0];
      document.getElementById('filter-empty-copy').textContent = copy[filter][1];
    }
    var status = document.getElementById('filter-status');
    if (status && announce) status.textContent = visible + (visible === 1 ? ' product shown.' : ' products shown.');
  }

  function openConnections() {
    var panel = document.getElementById('connections');
    if (panel && location.hash === '#connections') panel.open = true;
  }
  openConnections();
  window.addEventListener('hashchange', openConnections);

  document.addEventListener('click', function (event) {
    var button = event.target.closest('[data-filter]');
    if (button) {
      filter = button.dataset.filter;
      applyFilter(true);
      if (!button.classList.contains('filter-button')) {
        document.querySelector('.filter-button[data-filter="all"]').focus();
      }
    }
    if (event.target.closest('[data-focus-add]')) {
      var input = document.getElementById('add-input');
      if (input) input.focus();
    }
    var suggestion = event.target.closest('[data-search]');
    if (suggestion) {
      var query = document.getElementById('market-query');
      if (query && !query.form.classList.contains('htmx-request')) {
        query.value = suggestion.dataset.search;
        query.form.requestSubmit();
      }
    }
    document.querySelectorAll('.popover[open]').forEach(function (menu) {
      if (!menu.contains(event.target)) menu.open = false;
    });
  });
  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') return;
    document.querySelectorAll('.popover[open]').forEach(function (menu) {
      menu.open = false;
      menu.querySelector('summary').focus();
    });
  });

  var toggle = document.getElementById('more-add-toggle');
  if (toggle) toggle.addEventListener('click', function () {
    var panel = document.getElementById('more-add');
    panel.hidden = !panel.hidden;
    toggle.setAttribute('aria-expanded', String(!panel.hidden));
  });

  var addForm = document.getElementById('add-form');
  if (addForm) addForm.addEventListener('submit', function (event) {
    event.preventDefault();
    if (addForm.dataset.busy) return;
    if (!window.htmx) { notify('The connection didn’t finish loading. Refresh the page and try again.', true); return; }
    var input = document.getElementById('add-input');
    var target = document.getElementById('add-target');
    var value = input.value.trim();
    if (!value) { input.focus(); return; }
    var isUrl = /^https?:\/\//i.test(value) || /^[a-z0-9-]+(\.[a-z0-9-]+)+(\/|$)/i.test(value);
    var values = { target_price: target.value.trim() };
    if (isUrl) values.url = /^https?:\/\//i.test(value) ? value : 'https://' + value;
    else { values.title = value.slice(0, 255); values.query = value.toLowerCase(); }
    var submit = addForm.querySelector('button[type="submit"]');
    document.getElementById('add-result').textContent = '';
    addForm.dataset.busy = 'true';
    addForm.setAttribute('aria-busy', 'true');
    submit.disabled = true;
    window.htmx.ajax('POST', isUrl ? '/app/items/track-url' : '/app/items', {
      source: addForm, target: isUrl ? '#add-result' : '#items', swap: 'innerHTML', values: values
    }).catch(function () {
      notify('We couldn’t add that product. Please try again.', true);
    }).finally(function () {
      delete addForm.dataset.busy;
      addForm.removeAttribute('aria-busy');
      submit.disabled = false;
    });
  });

  document.querySelectorAll('.dropzone').forEach(function (zone) {
    var input = zone.querySelector('input[type="file"]');
    var name = zone.querySelector('.dz-name');
    var thumb = zone.querySelector('.dz-thumb');
    var previewUrl;
    function resetPreview() {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = null;
      zone.classList.remove('has-file');
      thumb.removeAttribute('src');
      name.textContent = 'Drop a product screenshot here, or browse';
    }
    ['dragenter', 'dragover'].forEach(function (type) {
      zone.addEventListener(type, function (event) { event.preventDefault(); zone.classList.add('drag'); });
    });
    ['dragleave', 'drop'].forEach(function (type) {
      zone.addEventListener(type, function (event) { event.preventDefault(); zone.classList.remove('drag'); });
    });
    zone.addEventListener('drop', function (event) {
      if (event.dataTransfer && event.dataTransfer.files.length) {
        input.files = event.dataTransfer.files;
        input.dispatchEvent(new Event('change', { bubbles: true }));
      }
    });
    input.addEventListener('change', function () {
      resetPreview();
      input.setCustomValidity('');
      var file = input.files && input.files[0];
      if (!file) return;
      if (!/^image\/(png|jpeg|webp|gif)$/.test(file.type) || file.size > 5 * 1024 * 1024) {
        input.setCustomValidity('Choose a PNG, JPG, WebP or GIF smaller than 5 MB.');
        input.reportValidity();
        return;
      }
      previewUrl = URL.createObjectURL(file);
      thumb.src = previewUrl;
      name.textContent = file.name;
      zone.classList.add('has-file');
    });
    input.form.addEventListener('reset', function () { resetPreview(); input.setCustomValidity(''); });
  });

  var rule = document.getElementById('rule');
  if (rule) {
    function updateAlertFields() {
      var percent = rule.value === 'pct_drop';
      var hasTarget = rule.dataset.hasTarget === 'true';
      var threshold = document.getElementById('threshold');
      document.getElementById('threshold-label').textContent = percent ? 'Price drop (%)' : 'Price threshold ($)';
      document.getElementById('threshold-hint').textContent = percent ? 'Email me when a price drops by this percentage.' : (hasTarget ? 'Leave blank to use your target price.' : 'Enter the price you’d like us to watch for.');
      threshold.placeholder = percent ? 'e.g. 10' : (hasTarget ? 'Use my target price' : 'e.g. 150');
      threshold.required = percent || !hasTarget;
      threshold.min = percent ? '0.01' : '0';
      if (percent) threshold.max = '100';
      else threshold.removeAttribute('max');
    }
    rule.addEventListener('change', updateAlertFields);
    rule.form.addEventListener('reset', function () { setTimeout(updateAlertFields, 0); });
    updateAlertFields();
  }

  document.body.addEventListener('htmx:beforeRequest', function (event) {
    var source = event.detail.elt;
    var card = source.closest('[data-item-id]');
    if (card) pendingFocus = card.dataset.itemId;
  });
  document.body.addEventListener('htmx:beforeSwap', function (event) {
    var target = event.detail.target || event.target;
    if (target.id === 'offers') {
      var price = target.querySelector('.v-price');
      target.dataset.prevPrice = price ? price.textContent : '';
    }
  });
  function afterItemsSwap() {
    applyFilter(false);
    if (pendingFocus) {
      var summary = document.querySelector('[data-item-id="' + CSS.escape(pendingFocus) + '"]:not([hidden]) summary');
      var fallback = document.querySelector('.filter-button[aria-pressed="true"]') || document.getElementById('add-input');
      if (summary || fallback) (summary || fallback).focus({ preventScroll: true });
      pendingFocus = null;
    }
  }
  document.body.addEventListener('htmx:afterSwap', function (event) {
    // The SSE extension dispatches this event on the element without a target
    // in its detail; ordinary HTMX requests include detail.target.
    var target = event.detail.target || event.target;
    if (target.id === 'items') afterItemsSwap();
    if (target.id === 'offers') {
      var price = target.querySelector('.v-price');
      if (price && target.dataset.prevPrice && price.textContent !== target.dataset.prevPrice) price.classList.add('tick');
    }
  });
  document.body.addEventListener('htmx:oobAfterSwap', afterItemsSwap);
  document.body.addEventListener('htmx:afterRequest', function (event) {
    if (!event.detail.successful) return;
    var source = event.detail.elt;
    var path = event.detail.requestConfig.path;
    if (source.id === 'add-form' && !document.querySelector('#add-result .error')) {
      source.reset();
      filter = 'all';
      applyFilter(false);
      notify('Added to your wishlist. We’re on the lookout.');
    } else if (source.dataset.successMessage) {
      notify(source.dataset.successMessage);
    }
    if (path === '/app/items' && source.id !== 'add-form') {
      var result = document.getElementById('identify-result');
      if (result) result.textContent = '';
      filter = 'all';
      applyFilter(false);
      notify('Added to your wishlist. We’re on the lookout.');
    }
  });
  document.body.addEventListener('htmx:responseError', function (event) {
    notify(event.detail.xhr.status === 422 ? 'Check the form fields and try again.' : 'That didn’t go through. Please try again.', true);
  });
  document.body.addEventListener('htmx:sendError', function () {
    notify('We couldn’t connect. Check your connection and try again.', true);
  });
})();
