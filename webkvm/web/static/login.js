(function () {
  const err = new URLSearchParams(location.search).get('err');
  const el = document.getElementById('err');
  if (err === 'blocked') el.textContent = I18N.t('login.err.blocked');
  else if (err) el.textContent = I18N.t('login.err.bad');
})();
