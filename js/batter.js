'use strict';

(async () => {
  const params = new URLSearchParams(location.search);
  const batterName = params.get('name');
  if (!batterName) { location.href = 'index.html'; return; }

  // データ読み込み
  let allPitches = await STATSCAST.tryLoadFromFile();
  if (!allPitches || allPitches.length === 0) {
    allPitches = STATSCAST.loadStoredData();
  }
  if (allPitches.length === 0) {
    document.body.innerHTML =
      '<div style="padding:40px;text-align:center">データがありません。<a href="index.html">トップに戻る</a></div>';
    return;
  }

  const batterPitches = allPitches.filter(p => p.Batter === batterName);
  if (batterPitches.length === 0) {
    document.body.innerHTML =
      `<div style="padding:40px;text-align:center">${batterName} のデータが見つかりません。<a href="index.html">トップに戻る</a></div>`;
    return;
  }

  // --- 打者情報セット ---
  document.title = `${batterName} — 東京六大学野球 StatCast`;
  document.getElementById('batter-name').textContent = batterName;
  const first = batterPitches[0];
  document.getElementById('batter-team').textContent = first.BatterTeam || '';
  const sideMap = { Right: '右打', Left: '左打', Switch: '両打' };
  document.getElementById('batter-side').textContent = sideMap[first.BatterSide] || first.BatterSide || '';

  // --- 基本成績 ---
  const ab    = batterPitches.filter(p => p.PitchCall === 'InPlay' || p.KorBB === 'Strikeout').length;
  const hits  = batterPitches.filter(p => p.PitchCall === 'InPlay' &&
    ['Single','Double','Triple','HomeRun'].includes(p.PlayResult)).length;
  const k     = batterPitches.filter(p => p.KorBB === 'Strikeout').length;
  const bb    = batterPitches.filter(p => p.KorBB === 'Walk').length;
  const ba    = ab > 0 ? (hits / ab).toFixed(3).replace(/^0/, '') : '—';
  const totalP = batterPitches.length;
  const whiffs = batterPitches.filter(p => p.PitchCall === 'StrikeSwinging').length;
  const whiffPct = totalP > 0 ? (whiffs / totalP * 100).toFixed(1) + '%' : '—';
  const inPlay = batterPitches.filter(p => p.PitchCall === 'InPlay').length;
  const gbs   = batterPitches.filter(p => p.PitchCall === 'InPlay' && p.TaggedHitType === 'GroundBall').length;
  const gbPct = inPlay > 0 ? (gbs / inPlay * 100).toFixed(1) + '%' : '—';

  document.getElementById('stat-ab').textContent    = ab;
  document.getElementById('stat-hits').textContent  = hits;
  document.getElementById('stat-ba').textContent    = ba;
  document.getElementById('stat-k').textContent     = k;
  document.getElementById('stat-bb').textContent    = bb;
  document.getElementById('stat-whiff').textContent = whiffPct;
  document.getElementById('stat-gb').textContent    = gbPct;

  // --- 球種タブ構築 ---
  const pitchTypes = STATSCAST.getPitchTypes(batterPitches);
  const pitchTypeLabels = {
    Fastball:    'ストレート',
    Sinker:      'ツーシーム',
    Cutter:      'カット',
    Slider:      'スライダー',
    Curveball:   'カーブ',
    Changeup:    'チェンジアップ',
    Splitter:    'フォーク',
    Knuckleball: 'ナックル',
    Other:       'その他',
  };

  const typeContainer = document.getElementById('pitch-type-tabs');
  pitchTypes.forEach(type => {
    const btn = document.createElement('button');
    btn.className = 'tab-btn';
    btn.dataset.type = type;
    const n = batterPitches.filter(p => p.TaggedPitchType === type).length;
    btn.textContent = `${pitchTypeLabels[type] || type} (${n})`;
    typeContainer.appendChild(btn);
  });

  // --- 状態 ---
  let currentStat      = 'ba';
  let currentPitchType = 'all';
  let showCalled   = true;
  let showSwinging = true;
  let showHit      = true;

  const zoneCanvas  = document.getElementById('zone-canvas');
  const pitchCanvas = document.getElementById('pitch-canvas');
  const chartTitle  = document.getElementById('zone-chart-title');

  function update() {
    const pitches = STATSCAST.filterPitches(
      allPitches,
      batterName,
      currentPitchType === 'all' ? null : currentPitchType
    );

    // ゾーンチャート
    chartTitle.textContent = ZoneChart.STAT_LABELS[currentStat];
    const zoneStats = STATSCAST.getZoneStats(pitches);
    ZoneChart.draw(zoneCanvas, zoneStats, currentStat);

    // 投球散布図
    const plotPitches = STATSCAST.getBatterPitches(
      allPitches,
      batterName,
      currentPitchType === 'all' ? null : currentPitchType
    );
    PitchPlot.draw(pitchCanvas, plotPitches, { showCalled, showSwinging, showHit });
  }

  // --- 統計種別タブ ---
  document.querySelectorAll('[data-stat]').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('[data-stat]').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentStat = btn.dataset.stat;
      update();
    });
  });

  // --- 球種タブ ---
  typeContainer.addEventListener('click', e => {
    const btn = e.target.closest('[data-type]');
    if (!btn) return;
    document.querySelectorAll('[data-type]').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentPitchType = btn.dataset.type;
    update();
  });

  // --- 投球結果トグル ---
  document.getElementById('toggle-called').addEventListener('change', e => {
    showCalled = e.target.checked;
    updateLegend();
    update();
  });
  document.getElementById('toggle-swinging').addEventListener('change', e => {
    showSwinging = e.target.checked;
    updateLegend();
    update();
  });
  document.getElementById('toggle-hit').addEventListener('change', e => {
    showHit = e.target.checked;
    updateLegend();
    update();
  });

  function updateLegend() {
    document.querySelector('.legend-item.called').style.opacity   = showCalled   ? 1 : 0.35;
    document.querySelector('.legend-item.swinging').style.opacity = showSwinging ? 1 : 0.35;
    document.querySelector('.legend-item.hit').style.opacity      = showHit      ? 1 : 0.35;
  }

  // 初回描画
  update();
})();
