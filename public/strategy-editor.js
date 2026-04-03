/**
 * Strategy Editor — reusable JS module for editing portfolio strategy rules.
 *
 * Usage:
 *   <script src="/strategy-editor.js"></script>
 *   renderStrategyEditor('container-id', strategyObj, portfolioId);
 */

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function _el(tag, attrs = {}, children = []) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'className') e.className = v;
    else if (k === 'textContent') e.textContent = v;
    else if (k === 'innerHTML') e.innerHTML = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2).toLowerCase(), v);
    else e.setAttribute(k, v);
  }
  for (const c of children) {
    if (typeof c === 'string') e.appendChild(document.createTextNode(c));
    else if (c) e.appendChild(c);
  }
  return e;
}

function _injectStyles() {
  if (document.getElementById('se-styles')) return;
  const style = document.createElement('style');
  style.id = 'se-styles';
  style.textContent = `
    .se-root {
      font-family: 'Inter', sans-serif;
      color: #e0e0e0;
      max-width: 820px;
    }
    .se-section {
      background: #12121a;
      border: 1px solid #1e1e2e;
      border-radius: 10px;
      padding: 20px 24px;
      margin-bottom: 16px;
    }
    .se-section h3 {
      margin: 0 0 16px 0;
      font-size: 14px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: #4fc3f7;
      font-family: 'JetBrains Mono', monospace;
    }
    .se-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px 24px;
    }
    .se-field {
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .se-field.full { grid-column: 1 / -1; }
    .se-field label {
      font-size: 12px;
      color: #888;
      font-family: 'JetBrains Mono', monospace;
    }
    .se-field input[type="number"],
    .se-field input[type="text"],
    .se-field textarea {
      background: #0a0a0f;
      border: 1px solid #1e1e2e;
      border-radius: 6px;
      padding: 8px 10px;
      color: #e0e0e0;
      font-family: 'JetBrains Mono', monospace;
      font-size: 13px;
      outline: none;
      transition: border-color 0.15s;
    }
    .se-field input:focus,
    .se-field textarea:focus {
      border-color: #4fc3f7;
    }
    .se-field textarea {
      resize: vertical;
      min-height: 48px;
    }
    .se-checks {
      display: flex;
      gap: 14px;
      flex-wrap: wrap;
      padding-top: 2px;
    }
    .se-checks label {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 13px;
      color: #e0e0e0;
      cursor: pointer;
      font-family: 'JetBrains Mono', monospace;
    }
    .se-checks input[type="checkbox"] {
      accent-color: #4fc3f7;
      width: 16px;
      height: 16px;
      cursor: pointer;
    }
    .se-actions {
      display: flex;
      align-items: center;
      gap: 16px;
      margin-top: 8px;
    }
    .se-save-btn {
      background: #4fc3f7;
      color: #0a0a0f;
      border: none;
      border-radius: 6px;
      padding: 10px 28px;
      font-weight: 700;
      font-size: 14px;
      font-family: 'Inter', sans-serif;
      cursor: pointer;
      transition: opacity 0.15s;
    }
    .se-save-btn:hover { opacity: 0.85; }
    .se-save-btn:disabled { opacity: 0.5; cursor: not-allowed; }
    .se-msg {
      font-size: 13px;
      font-family: 'JetBrains Mono', monospace;
    }
    .se-msg.ok  { color: #00e676; }
    .se-msg.err { color: #ff5252; }
  `;
  document.head.appendChild(style);
}

/* ------------------------------------------------------------------ */
/*  Build form fields                                                  */
/* ------------------------------------------------------------------ */

function _numberField(label, value, onChange) {
  const field = _el('div', { className: 'se-field' });
  field.appendChild(_el('label', { textContent: label }));
  const inp = _el('input', { type: 'number', value: value ?? '', step: 'any' });
  inp.addEventListener('input', () => onChange(inp.value === '' ? null : Number(inp.value)));
  field.appendChild(inp);
  return field;
}

function _textField(label, value, onChange, opts = {}) {
  const field = _el('div', { className: 'se-field' + (opts.full ? ' full' : '') });
  field.appendChild(_el('label', { textContent: label }));
  if (opts.multiline) {
    const ta = _el('textarea', { textContent: value ?? '' });
    ta.addEventListener('input', () => onChange(ta.value));
    field.appendChild(ta);
  } else {
    const inp = _el('input', { type: 'text', value: value ?? '' });
    inp.addEventListener('input', () => onChange(inp.value));
    field.appendChild(inp);
  }
  return field;
}

function _checkboxGroup(label, options, selected, onChange) {
  const field = _el('div', { className: 'se-field full' });
  field.appendChild(_el('label', { textContent: label }));
  const wrap = _el('div', { className: 'se-checks' });
  const current = new Set(selected || []);

  for (const opt of options) {
    const cb = _el('input', { type: 'checkbox' });
    cb.checked = current.has(opt);
    cb.addEventListener('change', () => {
      if (cb.checked) current.add(opt);
      else current.delete(opt);
      onChange([...current]);
    });
    wrap.appendChild(_el('label', {}, [cb, opt]));
  }
  field.appendChild(wrap);
  return field;
}

function _csvField(label, value, onChange) {
  const field = _el('div', { className: 'se-field full' });
  field.appendChild(_el('label', { textContent: label + '  (comma-separated)' }));
  const inp = _el('input', {
    type: 'text',
    value: (value || []).join(', '),
  });
  inp.addEventListener('input', () => {
    const list = inp.value
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);
    onChange(list);
  });
  field.appendChild(inp);
  return field;
}

/* ------------------------------------------------------------------ */
/*  Section builders                                                   */
/* ------------------------------------------------------------------ */

function _sectionTradeSelection(s, set) {
  const sec = _el('div', { className: 'se-section' });
  sec.appendChild(_el('h3', { textContent: 'Trade Selection' }));
  const grid = _el('div', { className: 'se-grid' });

  grid.appendChild(
    _checkboxGroup('Allowed bet types', ['sure', 'edge', 'safe_no'], s.allowed_bet_types, (v) =>
      set('allowed_bet_types', v)
    )
  );
  grid.appendChild(
    _checkboxGroup('Allowed sides', ['YES', 'NO'], s.allowed_sides, (v) =>
      set('allowed_sides', v)
    )
  );
  grid.appendChild(
    _checkboxGroup('Allowed band types', ['above', 'below', 'exact'], s.allowed_band_types, (v) =>
      set('allowed_band_types', v)
    )
  );
  grid.appendChild(
    _csvField('Blocked cities', s.blocked_cities, (v) => set('blocked_cities', v))
  );
  grid.appendChild(
    _csvField('Allowed cities', s.allowed_cities, (v) => set('allowed_cities', v))
  );

  sec.appendChild(grid);
  return sec;
}

function _sectionThresholds(title, key, fields, s, set) {
  const sec = _el('div', { className: 'se-section' });
  sec.appendChild(_el('h3', { textContent: title }));
  const grid = _el('div', { className: 'se-grid' });
  const obj = s[key] || {};

  for (const f of fields) {
    grid.appendChild(
      _numberField(f, obj[f], (v) => {
        if (!s[key]) s[key] = {};
        s[key][f] = v;
        set(key, { ...s[key] });
      })
    );
  }

  sec.appendChild(grid);
  return sec;
}

function _sectionRisk(s, set) {
  const sec = _el('div', { className: 'se-section' });
  sec.appendChild(_el('h3', { textContent: 'Risk Filters' }));
  const grid = _el('div', { className: 'se-grid' });
  const obj = s.risk || {};

  grid.appendChild(
    _numberField('max_model_disagreement', obj.max_model_disagreement, (v) => {
      if (!s.risk) s.risk = {};
      s.risk.max_model_disagreement = v;
      set('risk', { ...s.risk });
    })
  );
  grid.appendChild(
    _numberField('max_empirical_disagreement', obj.max_empirical_disagreement, (v) => {
      if (!s.risk) s.risk = {};
      s.risk.max_empirical_disagreement = v;
      set('risk', { ...s.risk });
    })
  );

  sec.appendChild(grid);
  return sec;
}

function _sectionPositionSizing(s, set) {
  const sec = _el('div', { className: 'se-section' });
  sec.appendChild(_el('h3', { textContent: 'Position Sizing' }));
  const grid = _el('div', { className: 'se-grid' });
  const ps = s.position_sizing || {};

  const psFields = [
    'bankroll_usd',
    'liquidity_safety_factor',
    'min_edge_after_slippage',
    'min_liquidity_usd',
    'kelly_fraction',
  ];

  for (const f of psFields) {
    grid.appendChild(
      _numberField(f, ps[f], (v) => {
        if (!s.position_sizing) s.position_sizing = {};
        s.position_sizing[f] = v;
        set('position_sizing', { ...s.position_sizing });
      })
    );
  }

  // Top-level sizing fields
  const topFields = [
    'max_trade_size_usd',
    'min_trade_size_usd',
    'preferred_entry_price_min',
    'preferred_entry_price_max',
    'max_portfolio_exposure_pct',
  ];
  for (const f of topFields) {
    grid.appendChild(_numberField(f, s[f], (v) => set(f, v)));
  }

  sec.appendChild(grid);
  return sec;
}

function _sectionCapitalManagement(s, set) {
  const sec = _el('div', { className: 'se-section' });
  sec.appendChild(_el('h3', { textContent: 'Capital Management' }));
  const grid = _el('div', { className: 'se-grid' });
  const cm = s.capital_management || {};

  const cmFields = [
    { key: 'max_portfolio_utilization_pct', label: 'max_portfolio_utilization_pct (0-100)' },
    { key: 'max_single_trade_pct', label: 'max_single_trade_pct (0-100)' },
    { key: 'max_single_trade_usd', label: 'max_single_trade_usd' },
    { key: 'max_correlated_exposure_pct', label: 'max_correlated_exposure_pct (0-100)' },
    { key: 'reserve_pct', label: 'reserve_pct (0-100)' },
  ];

  for (const f of cmFields) {
    grid.appendChild(
      _numberField(f.label, cm[f.key], (v) => {
        if (!s.capital_management) s.capital_management = {};
        s.capital_management[f.key] = v;
        set('capital_management', { ...s.capital_management });
      })
    );
  }

  sec.appendChild(grid);
  return sec;
}

function _selectField(label, options, value, onChange) {
  const field = _el('div', { className: 'se-field' });
  field.appendChild(_el('label', { textContent: label }));
  const sel = _el('select', {
    className: '',
  });
  sel.style.cssText = 'background:#0a0a0f;border:1px solid #1e1e2e;border-radius:6px;padding:8px 10px;color:#e0e0e0;font-family:"JetBrains Mono",monospace;font-size:13px;outline:none;';
  for (const opt of options) {
    const o = _el('option', { value: opt, textContent: opt });
    if (opt === value) o.selected = true;
    sel.appendChild(o);
  }
  sel.addEventListener('change', () => onChange(sel.value));
  field.appendChild(sel);
  return field;
}

function _sectionCapitalAllocation(s, set) {
  const sec = _el('div', { className: 'se-section' });
  sec.appendChild(_el('h3', { textContent: 'Capital Allocation' }));
  const grid = _el('div', { className: 'se-grid' });
  const ca = s.capital_allocation || {};

  // sort_field dropdown
  grid.appendChild(
    _selectField('sort_field', ['edge', 'confidence', 'ev_per_dollar', 'composite'], ca.sort_field || 'edge', (v) => {
      if (!s.capital_allocation) s.capital_allocation = {};
      s.capital_allocation.sort_field = v;
      set('capital_allocation', { ...s.capital_allocation });
      // Toggle sort_weights visibility
      weightsWrap.style.display = v === 'composite' ? '' : 'none';
    })
  );

  // sort_weights (3 number inputs, shown only when composite)
  const sw = ca.sort_weights || {};
  const weightsWrap = _el('div', { className: 'se-field full' });
  weightsWrap.style.display = (ca.sort_field === 'composite') ? '' : 'none';
  weightsWrap.appendChild(_el('label', { textContent: 'sort_weights (edge / confidence / ev)' }));
  const weightsGrid = _el('div', {});
  weightsGrid.style.cssText = 'display:flex;gap:12px;';

  const weightKeys = ['edge', 'confidence', 'ev'];
  for (const wk of weightKeys) {
    const inp = _el('input', {
      type: 'number',
      value: sw[wk] ?? '',
      step: 'any',
      placeholder: wk,
    });
    inp.style.cssText = 'background:#0a0a0f;border:1px solid #1e1e2e;border-radius:6px;padding:8px 10px;color:#e0e0e0;font-family:"JetBrains Mono",monospace;font-size:13px;outline:none;width:100%;';
    inp.addEventListener('input', () => {
      if (!s.capital_allocation) s.capital_allocation = {};
      if (!s.capital_allocation.sort_weights) s.capital_allocation.sort_weights = {};
      s.capital_allocation.sort_weights[wk] = inp.value === '' ? null : Number(inp.value);
      set('capital_allocation', { ...s.capital_allocation });
    });
    const wrapper = _el('div', {}, [_el('label', { textContent: wk, className: '' }), inp]);
    wrapper.style.cssText = 'flex:1;display:flex;flex-direction:column;gap:4px;';
    wrapper.querySelector('label').style.cssText = 'font-size:11px;color:#888;font-family:"JetBrains Mono",monospace;';
    weightsGrid.appendChild(wrapper);
  }
  weightsWrap.appendChild(weightsGrid);
  grid.appendChild(weightsWrap);

  sec.appendChild(grid);
  return sec;
}

function _sectionNotes(s, set) {
  const sec = _el('div', { className: 'se-section' });
  sec.appendChild(_el('h3', { textContent: 'Notes' }));
  sec.appendChild(
    _textField('notes', s.notes, (v) => set('notes', v), { full: true, multiline: true })
  );
  return sec;
}

/* ------------------------------------------------------------------ */
/*  Public API                                                         */
/* ------------------------------------------------------------------ */

/**
 * Renders an editable strategy form into a container element.
 * @param {string} containerId - DOM id of the container element
 * @param {object} strategy - The current strategy JSON object
 * @param {string} portfolioId - Portfolio UUID for saving
 */
function renderStrategyEditor(containerId, strategy, portfolioId) {
  _injectStyles();
  const container = document.getElementById(containerId);
  if (!container) {
    console.error('[strategy-editor] container not found:', containerId);
    return;
  }
  container.innerHTML = '';

  // Deep-clone so mutations don't affect caller's object until save
  const s = JSON.parse(JSON.stringify(strategy || {}));

  const root = _el('div', { className: 'se-root' });

  const set = (key, value) => {
    s[key] = value;
  };

  // Trade Selection
  root.appendChild(_sectionTradeSelection(s, set));

  // Sure Bet Thresholds
  root.appendChild(
    _sectionThresholds(
      'Sure Bet Thresholds',
      'sure_bet',
      ['min_prob', 'max_price', 'min_edge', 'min_confidence'],
      s,
      set
    )
  );

  // Edge Bet Thresholds
  root.appendChild(
    _sectionThresholds(
      'Edge Bet Thresholds',
      'edge_bet',
      ['min_prob', 'max_price', 'min_edge', 'min_confidence'],
      s,
      set
    )
  );

  // Safe NO Thresholds
  root.appendChild(
    _sectionThresholds(
      'Safe NO Thresholds',
      'safe_no',
      ['min_prob', 'max_no_price', 'min_no_price', 'min_return', 'min_confidence'],
      s,
      set
    )
  );

  // Risk Filters
  root.appendChild(_sectionRisk(s, set));

  // Position Sizing
  root.appendChild(_sectionPositionSizing(s, set));

  // Capital Management
  root.appendChild(_sectionCapitalManagement(s, set));

  // Capital Allocation
  root.appendChild(_sectionCapitalAllocation(s, set));

  // Notes
  root.appendChild(_sectionNotes(s, set));

  // Save button + message
  const actions = _el('div', { className: 'se-actions' });
  const btn = _el('button', { className: 'se-save-btn', textContent: 'Save Changes' });
  const msg = _el('span', { className: 'se-msg' });
  actions.appendChild(btn);
  actions.appendChild(msg);
  root.appendChild(actions);

  btn.addEventListener('click', async () => {
    btn.disabled = true;
    btn.textContent = 'Saving...';
    msg.textContent = '';
    msg.className = 'se-msg';
    try {
      await saveStrategy(portfolioId, s);
      msg.textContent = 'Saved successfully';
      msg.className = 'se-msg ok';
    } catch (err) {
      msg.textContent = 'Error: ' + err.message;
      msg.className = 'se-msg err';
    } finally {
      btn.disabled = false;
      btn.textContent = 'Save Changes';
    }
  });

  container.appendChild(root);
}

/**
 * Saves strategy changes via PATCH /api/portfolios?id=xxx
 * @param {string} portfolioId - Portfolio UUID
 * @param {object} strategy - The full strategy object to persist
 * @returns {Promise<object>} The updated portfolio record
 */
async function saveStrategy(portfolioId, strategy) {
  const resp = await fetch(`/api/portfolios?id=${encodeURIComponent(portfolioId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy }),
  });
  if (!resp.ok) {
    const body = await resp.text();
    throw new Error(`HTTP ${resp.status}: ${body}`);
  }
  return resp.json();
}
